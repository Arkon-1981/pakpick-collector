"""IGDB 메타 보강 — 플레이 타임 + 평점.

신선한 게임 중 아직 보강 안 된 것을 골라 IGDB(Twitch 공식 API)에서
게임을 찾아 평점(평론가/이용자)과 플레이 타임(클리어 시간)을
`game_meta` 테이블에 저장한다. 수집기가 current_data 를 통째로
덮어써도 별도 테이블이라 안전하다 (ai_reviews 와 같은 구조).

환경변수:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  (수집 워크플로와 동일)
  TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET   (dev.twitch.tv 앱 등록)

옵션:
  --limit N     한 번에 보강할 최대 개수 (기본 200)
  --dry-run     저장하지 않고 매칭 결과만 출력

매칭은 보수적으로 한다 — 제목 정규화 후 IGDB 이름/별칭과 정확히 일치할
때만 확정하고, 애매하면 '매칭 실패'로 기록해 틀린 게임의 평점을 붙이는
것을 막는다. 실패 기록도 남겨야 다음 실행이 같은 상품을 무한 재시도하지
않는다 (30일 뒤 자동 재시도).
"""
import argparse
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
TWITCH_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

IGDB = "https://api.igdb.com/v4"
RETRY_AFTER_DAYS = 30      # 매칭 실패분 재시도 주기
REQUEST_GAP = 0.3          # IGDB 는 초당 4회 한도 — 여유 있게 초당 ~3회


def _sb(path: str, method: str = "GET", body=None, extra_headers=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    r = requests.request(method, f"{SUPABASE_URL}/rest/v1/{path}",
                         headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json() if r.text else None


# ------------------------------------------------------------------
# 제목 정규화 (웹의 normTokens 와 같은 원리 — 판본 표기 제거 + 로마 숫자 통일)
# ------------------------------------------------------------------

EDITION_WORDS = (
    "standard edition", "deluxe edition", "ultimate edition", "gold edition",
    "complete edition", "definitive edition", "goty", "game of the year",
    "digital edition", "bundle", "스탠다드 에디션", "디럭스 에디션", "얼티밋 에디션",
    "골드 에디션", "합본", "번들", "디지털 에디션", "완전판", "통상판",
)
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
         "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12",
         "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16"}


def clean_title(raw: str) -> str:
    """스토어 제목의 스토어 특유 접두어·꼬리표를 걷어낸다."""
    t = raw or ""
    t = re.sub(r"^발매\s*\d{2}\.\d{1,2}\.\d{1,2}\s*", "", t)
    t = re.sub(r"^PS[45]®?용\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\([^)]*(한국어|영어|일본어|중국어|태국어)[^)]*\)", "", t)
    t = re.sub(r"\s*(for Nintendo Switch( 2)?|Nintendo Switch( 2)? Edition)\s*$", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def norm(s: str) -> str:
    """비교용 정규화: 소문자, 기호 제거, 판본 표기 제거, 로마 숫자 → 아라비아."""
    s = unicodedata.normalize("NFKC", (s or "").lower())
    s = re.sub(r"[®™©:：\-–—_'’!?.,·]", " ", s)
    for w in EDITION_WORDS:
        s = s.replace(w, " ")
    words = [ROMAN.get(w, w) for w in s.split()]
    return " ".join(words)


def english_part(title: str) -> str | None:
    """'페르소나3 리로드(Persona 3 Reload)' 처럼 괄호 안 영문 제목을 뽑는다."""
    m = re.search(r"\(([A-Za-z0-9][^)]{3,60})\)", title)
    return m.group(1).strip() if m else None


# ------------------------------------------------------------------
# IGDB
# ------------------------------------------------------------------

_token: str | None = None


def igdb_headers() -> dict:
    global _token
    if _token is None:
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={"client_id": TWITCH_ID, "client_secret": TWITCH_SECRET,
                    "grant_type": "client_credentials"},
            timeout=30,
        )
        r.raise_for_status()
        _token = r.json()["access_token"]
    return {"Client-ID": TWITCH_ID, "Authorization": f"Bearer {_token}"}


def igdb_query(endpoint: str, body: str) -> list[dict]:
    """IGDB 쿼리 1회. 429 는 잠깐 쉬고 재시도."""
    for attempt in range(4):
        r = requests.post(f"{IGDB}/{endpoint}", headers=igdb_headers(),
                          data=body.encode("utf-8"), timeout=30)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    return []


def search_game(title: str) -> tuple[dict | None, str]:
    """제목으로 게임을 찾는다. (게임, 신뢰도) — 못 찾으면 (None, 'none').

    IGDB search 는 별칭(alternative_names — 한국어 제목 포함)도 뒤져 주므로
    한국어 스토어 제목도 상당수 잡힌다. 후보 중에서는 정규화 제목이
    이름/별칭과 정확히 일치하는 것만 받아들인다.
    """
    esc = title.replace('"', '\\"')
    rows = igdb_query("games", (
        f'search "{esc}"; '
        "fields id,name,rating,rating_count,aggregated_rating,"
        "aggregated_rating_count,alternative_names.name,category; limit 8;"
    ))
    time.sleep(REQUEST_GAP)
    want = norm(title)
    if not want:
        return None, "none"

    for row in rows:
        names = [row.get("name") or ""]
        names += [a.get("name") or "" for a in row.get("alternative_names") or []]
        if any(norm(n) == want for n in names):
            return row, "exact"

    # 정확 일치가 없으면: 후보가 하나뿐이고 토큰이 포함 관계일 때만 (보수적)
    if len(rows) == 1:
        row = rows[0]
        names = [row.get("name") or ""] + [
            a.get("name") or "" for a in row.get("alternative_names") or []
        ]
        w = set(want.split())
        for n in names:
            c = set(norm(n).split())
            if w and c and (w <= c or c <= w):
                return row, "fuzzy"
    return None, "none"


def fetch_ttb(game_ids: list[int]) -> dict[int, dict]:
    """플레이 타임(초). {igdb_id: {hastily, normally, completely}}"""
    out: dict[int, dict] = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i : i + 100]
        rows = igdb_query("game_time_to_beats", (
            "fields game_id,hastily,normally,completely,count; "
            f"where game_id = ({','.join(map(str, chunk))}); limit 100;"
        ))
        time.sleep(REQUEST_GAP)
        for r in rows:
            out[r["game_id"]] = r
    return out


# ------------------------------------------------------------------
# 후보 선정 + 저장
# ------------------------------------------------------------------

def fetch_candidates(limit: int) -> list[dict]:
    """신선(48h)하고 아직 보강 안 된 게임. 인기 순위 → 정가 순으로 우선한다."""
    done: set[int] = set()
    retry_before = (datetime.now(timezone.utc) - timedelta(days=RETRY_AFTER_DAYS)).isoformat()
    for offset in range(0, 100_000, 1000):
        page = _sb("game_meta?select=store_item_id,igdb_id,enriched_at"
                   f"&limit=1000&offset={offset}") or []
        for row in page:
            # 성공분은 계속 제외, 실패분은 30일 지나면 다시 후보가 된다
            if row["igdb_id"] is not None or row["enriched_at"] > retry_before:
                done.add(row["store_item_id"])
        if len(page) < 1000:
            break

    iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    picked: list[dict] = []
    # current_data 통째로는 받지 않는다 — PS 행 하나가 수십 KB (fetch_item_meta 와 같은 이유)
    for offset in range(0, 10_000, 1000):
        rows = _sb(
            "store_items?select=id,title,platform,content_type,"
            "rank:current_data->popular_rank"
            f"&last_seen_at=gte.{iso}&order=id.asc&limit=1000&offset={offset}"
        ) or []
        for r in rows:
            if r["id"] in done or (r.get("content_type") == "addon"):
                continue
            title = clean_title(r.get("title") or "")
            if not title:
                continue
            picked.append({
                "id": r["id"], "title": title, "platform": r["platform"],
                "rank": r.get("rank") if isinstance(r.get("rank"), (int, float)) else 10_000,
            })
        if len(rows) < 1000:
            break

    # 인기 상위 먼저 (평점·플레이 타임이 가장 많이 보이는 자리), 나머지는 id 순
    picked.sort(key=lambda x: (x["rank"], x["id"]))
    return picked[:limit]


def to_hours(seconds) -> float | None:
    if not seconds:
        return None
    return round(seconds / 3600, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not (SUPABASE_URL and SUPABASE_KEY and TWITCH_ID and TWITCH_SECRET):
        print("환경변수(SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / "
              "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET)가 필요합니다.")
        sys.exit(1)

    cands = fetch_candidates(args.limit)
    print(f"후보 {len(cands)}개 (인기 순위 → 정가 순)")

    matched: list[tuple[dict, dict, str]] = []   # (후보, igdb게임, 신뢰도)
    misses: list[dict] = []
    for c in cands:
        game, conf = search_game(c["title"])
        if game is None and (en := english_part(c["title"])):
            game, conf = search_game(en)   # 괄호 안 영문 제목으로 한 번 더
        if game is None:
            misses.append(c)
            print(f"  · miss: [{c['platform']}] {c['title'][:44]}")
        else:
            matched.append((c, game, conf))
            print(f"  ✓ {conf:5} [{c['platform']}] {c['title'][:34]:34} → {game['name'][:40]}")

    ttb = fetch_ttb(sorted({g["id"] for _, g, _ in matched}))

    if args.dry_run:
        print(f"완료(dry-run): 매칭 {len(matched)} / 실패 {len(misses)} / TTB {len(ttb)}")
        return

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for c, g, conf in matched:
        t = ttb.get(g["id"]) or {}
        rows.append({
            "store_item_id": c["id"],
            "igdb_id": g["id"],
            "igdb_name": g.get("name"),
            "match_confidence": conf,
            "critic_rating": round(g["aggregated_rating"], 1) if g.get("aggregated_rating") else None,
            "critic_rating_count": g.get("aggregated_rating_count"),
            "user_rating": round(g["rating"], 1) if g.get("rating") else None,
            "user_rating_count": g.get("rating_count"),
            "ttb_hastily_h": to_hours(t.get("hastily")),
            "ttb_normally_h": to_hours(t.get("normally")),
            "ttb_completely_h": to_hours(t.get("completely")),
            "enriched_at": now,
        })
    rows += [{"store_item_id": c["id"], "igdb_id": None, "igdb_name": None,
              "match_confidence": "none", "enriched_at": now} for c in misses]

    for i in range(0, len(rows), 200):
        _sb("game_meta", method="POST", body=rows[i : i + 200],
            extra_headers={"Prefer": "resolution=merge-duplicates"})
    print(f"완료: 매칭 {len(matched)}개 저장, 실패 기록 {len(misses)}개 "
          f"(플레이 타임 확보 {len(ttb)}개)")


if __name__ == "__main__":
    main()
