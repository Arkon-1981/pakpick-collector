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
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.monitoring import capture, init_sentry  # noqa: E402

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
    # 엑스박스 기종 나열 접두어: "Xbox One & Xbox Series X|S용 …"
    t = re.sub(r"^Xbox[^용]{0,40}용\s+", "", t)
    t = re.sub(r"\s*\([^)]*(한국어|영어|일본어|중국어|태국어)[^)]*\)", "", t)
    t = re.sub(r"\s*(for Nintendo Switch( 2)?|Nintendo Switch( 2)? Edition)\s*$", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def norm(s: str) -> str:
    """비교용 정규화: 소문자, 기호 제거, 판본 표기 제거, 로마 숫자 → 아라비아."""
    s = unicodedata.normalize("NFKC", (s or "").lower())
    s = re.sub(r"[®™©:：\-–—_'’!?.,·/|&]", " ", s)
    # 'PERSONA5' vs 'Persona 5' 처럼 숫자 붙임 표기 차이를 없앤다 (양쪽에 똑같이
    # 적용되는 등호 비교 전용이라, 얼마나 잘게 쪼개지든 판정은 대칭으로 안전하다)
    s = re.sub(r"(?<=[^\d\s])(?=\d)", " ", s)
    s = re.sub(r"(?<=\d)(?=[^\d\s])", " ", s)
    for w in EDITION_WORDS:
        s = s.replace(w, " ")
    words = [ROMAN.get(w, w) for w in s.split()]
    return " ".join(words)


def search_queries(title: str) -> list[str]:
    """한 제목에서 시도할 검색어들 (순서대로, 중복 제거).

    스토어 제목은 'Palworld / 팰월드'(병기), '커세어 코브 Corsair Cove'(붙임),
    '페르소나3 리로드(Persona 3 Reload)'(괄호) 처럼 한글·영문이 섞여 있는데
    IGDB 검색은 이런 혼합 문자열에 약하다. 조각을 나눠 차례로 찔러 본다.
    검색어가 달라도 매칭 판정은 항상 원제목 기준(호출부)이라 오매칭 위험은 없다.
    """
    qs = [title]
    # 괄호 안 영문 제목
    m = re.search(r"\(([A-Za-z0-9][^)]{3,60})\)", title)
    if m:
        qs.append(m.group(1).strip())
    # 슬래시·가운뎃점 병기 → 각 조각
    if re.search(r"\s[/·]\s?", title):
        qs += [p.strip() for p in re.split(r"\s[/·]\s?", title) if len(p.strip()) >= 2]
    # 한영 붙임 → 가장 긴 영문 구간 (4자 이상, 알파벳 포함)
    runs = [r.strip(" :") for r in re.findall(r"[A-Za-z0-9][A-Za-z0-9 :'!.&-]{3,}", title)]
    runs = [r for r in runs if re.search(r"[A-Za-z]{3}", r)]
    if runs:
        qs.append(max(runs, key=len))
    # ®™ 류는 검색 정확도만 떨어뜨린다 — 모든 검색어에서 제거
    out, seen = [], set()
    for q in qs:
        q = re.sub(r"[®™©]", "", q).strip()
        if len(q) >= 2 and q not in seen:
            seen.add(q)
            out.append(q)
    return out


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
    """IGDB 쿼리 1회. 429(한도)·5xx(일시 오류)·네트워크 예외는 백오프 후 재시도.

    예전엔 429만 재시도하고 500/502/연결오류는 즉시 raise 로 전파돼, 보강 도중
    IGDB 가 한 번만 흔들려도 실행 전체가 죽고 그때까지 매칭 결과가 통째로 유실됐다.
    """
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            r = requests.post(f"{IGDB}/{endpoint}", headers=igdb_headers(),
                              data=body.encode("utf-8"), timeout=30)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            last_exc = requests.exceptions.HTTPError(f"IGDB {r.status_code}")
            continue
        r.raise_for_status()
        return r.json()
    if last_exc:
        raise last_exc
    return []


def search_game(title: str) -> tuple[dict | None, str]:
    """제목으로 게임을 찾는다. (게임, 신뢰도) — 못 찾으면 (None, 'none').

    IGDB search 는 별칭(alternative_names — 한국어 제목 포함)도 뒤져 주므로
    한국어 스토어 제목도 상당수 잡힌다. 판정은 정규화 후 완전 일치만:
    원제목과 일치하면 exact, 병기 조각('Palworld / 팰월드'의 'Palworld',
    괄호 안 영문 등)과 일치하면 fuzzy. 조각은 같은 게임의 다른 표기라
    부분 문자열 매칭과 달리 엉뚱한 게임이 붙지 않는다.
    """
    want = norm(title)
    if not want:
        return None, "none"
    variants = search_queries(title)
    accept = {norm(v) for v in variants if norm(v)}

    for query in variants:
        esc = query.replace('"', '\\"')
        rows = igdb_query("games", (
            f'search "{esc}"; '
            "fields id,name,rating,rating_count,aggregated_rating,"
            "aggregated_rating_count,alternative_names.name,genres.name,category; "
            "limit 8;"
        ))
        time.sleep(REQUEST_GAP)

        for row in rows:
            names = [row.get("name") or ""]
            names += [a.get("name") or "" for a in row.get("alternative_names") or []]
            keys = {norm(n) for n in names}
            if want in keys:
                return row, "exact"
            if accept & keys:
                return row, "fuzzy"

        # 완전 일치가 없으면: 후보가 하나뿐이고 토큰이 포함 관계일 때만 (보수적).
        # 공유 토큰이 1개뿐이면 거른다 — '진・삼국무쌍: ORIGINS' 이 흔한 단어
        # 하나('Origins')로 엉뚱한 게임에 붙은 실측 오매칭이 있다.
        if len(rows) == 1:
            row = rows[0]
            names = [row.get("name") or ""] + [
                a.get("name") or "" for a in row.get("alternative_names") or []
            ]
            w = set(want.split())
            for n in names:
                c = set(norm(n).split())
                if w and c and (w <= c or c <= w) and min(len(w), len(c)) >= 2:
                    return row, "fuzzy"
    return None, "none"


GAME_FIELDS = (
    "id,name,rating,rating_count,aggregated_rating,"
    "aggregated_rating_count,genres.name"
)


def match_steam_by_appid(appids: list[str]) -> dict[str, dict]:
    """스팀 appid → IGDB 게임. 제목 매칭이 필요 없는 공식 매핑(external_games).

    한글 전용 제목(마블 스파이더맨 2 등)도 appid 로는 정확히 잡힌다.
    category 1 = Steam.
    """
    id_map: dict[str, int] = {}
    for i in range(0, len(appids), 100):
        chunk = appids[i : i + 100]
        uids = ",".join(f'"{a}"' for a in chunk)
        # category 는 IGDB 가 external_game_source 로 개명하며 폐기 중 — 필터 없이
        # 받아서 양쪽 필드 중 하나라도 Steam(1)이면 인정한다 (실측: 필터식은 0건).
        rows = igdb_query("external_games", (
            f"fields uid,game,category,external_game_source; "
            f"where uid = ({uids}); limit 500;"
        ))
        time.sleep(REQUEST_GAP)
        for r in rows:
            src = r.get("external_game_source") or r.get("category")
            if src == 1 and r.get("game") and r.get("uid"):
                id_map.setdefault(r["uid"], r["game"])

    games: dict[int, dict] = {}
    gids = sorted(set(id_map.values()))
    for i in range(0, len(gids), 100):
        chunk = gids[i : i + 100]
        rows = igdb_query("games", (
            f"fields {GAME_FIELDS}; "
            f"where id = ({','.join(map(str, chunk))}); limit 100;"
        ))
        time.sleep(REQUEST_GAP)
        for r in rows:
            games[r["id"]] = r
    return {uid: games[gid] for uid, gid in id_map.items() if gid in games}


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


# language_support_type: 1=음성(더빙), 2=자막, 3=인터페이스
_KO_TYPE = {1: "audio", 2: "subtitles", 3: "interface"}
_ko_lang_id: int | None = None


def korean_language_id() -> int | None:
    """IGDB languages 에서 한국어 id 를 찾는다 (실행당 1회).

    locale 표기가 'ko'인지 'ko-KR'인지 문서가 불분명 — where 필터로 찍지 말고
    목록(수십 건)을 통째로 받아 유연하게 찾는다. (실측: 'ko' 정확 일치 필터는
    0건이 나와 전체가 '지원 없음'으로 오염된 적 있다.)
    """
    global _ko_lang_id
    if _ko_lang_id is None:
        rows = igdb_query("languages", "fields id,name,native_name,locale; limit 200;")
        time.sleep(REQUEST_GAP)
        _ko_lang_id = 0
        for r in rows:
            loc = (r.get("locale") or "").lower()
            name = (r.get("name") or "").lower()
            if loc == "ko" or loc.startswith("ko-") or loc.startswith("ko_") or name == "korean":
                _ko_lang_id = r["id"]
                break
        if not _ko_lang_id:
            print(f"※ 한국어 언어 id 못 찾음 — languages {len(rows)}건 locale: "
                  + ", ".join(str(r.get("locale")) for r in rows[:40]))
    return _ko_lang_id or None


def fetch_ko_support(game_ids: list[int]) -> dict[int, list[str]]:
    """한국어 지원 형태. {igdb_id: ['audio','subtitles','interface'] 부분집합}"""
    ko = korean_language_id()
    if not ko or not game_ids:
        return {}
    out: dict[int, set[str]] = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i : i + 100]
        rows = igdb_query("language_supports", (
            "fields game,language,language_support_type; "
            f"where game = ({','.join(map(str, chunk))}) & language = {ko}; limit 500;"
        ))
        time.sleep(REQUEST_GAP)
        for r in rows:
            t = _KO_TYPE.get(r.get("language_support_type"))
            if t:
                out.setdefault(r["game"], set()).add(t)
    return {g: sorted(v) for g, v in out.items()}


def ko_backfill() -> None:
    """이미 매칭된 game_meta 행 중 ko_support 가 비어 있는 것을 채운다 (일회성).

    015 SQL(ko_support 컬럼) 실행 후에 돌린다. 이후 신규 매칭분은
    본 실행이 저장 시점에 함께 채우므로 다시 돌릴 일이 없다.
    """
    if not korean_language_id():
        print("한국어 언어 id 조회 실패 — 오염 방지를 위해 중단")
        sys.exit(1)
    rows: list[dict] = []
    for offset in range(0, 100_000, 1000):
        # null(미조회)뿐 아니라 빈 배열도 다시 본다 — 언어 id 조회 실패로
        # '지원 없음'이 잘못 찍힌 행을 스스로 복구할 수 있게.
        page = _sb("game_meta?select=store_item_id,igdb_id&igdb_id=not.is.null"
                   "&or=(ko_support.is.null,ko_support.eq.%7B%7D)"
                   f"&limit=1000&offset={offset}") or []
        rows += page
        if len(page) < 1000:
            break
    gids = sorted({r["igdb_id"] for r in rows})
    print(f"백필 대상 {len(rows)}행 (게임 {len(gids)}개)")
    ko = fetch_ko_support(gids)
    # 지원 정보가 없는 게임은 빈 배열로 채워 '조회했지만 없음'과 '아직 안 봄'을 구분
    body = [{"store_item_id": r["store_item_id"],
             "ko_support": ko.get(r["igdb_id"], [])} for r in rows]
    saved = 0
    for i in range(0, len(body), 200):
        try:
            _sb("game_meta", method="POST", body=body[i : i + 200],
                extra_headers={"Prefer": "resolution=merge-duplicates"})
            saved += len(body[i : i + 200])
        except requests.exceptions.HTTPError as exc:
            print(f"백필 배치 실패({i}~): {exc.response.text[:200] if exc.response is not None else exc}")
    print(f"백필 완료: {saved}/{len(body)}행 (한국어 지원 확인 {len(ko)}게임)")


# ------------------------------------------------------------------
# 후보 선정 + 저장
# ------------------------------------------------------------------

def fetch_candidates(limit: int) -> list[dict]:
    """신선(48h)하고 아직 보강 안 된 게임. 인기 순위 → 정가 순으로 우선한다."""
    done: set[int] = set()
    retry_before = (datetime.now(timezone.utc) - timedelta(days=RETRY_AFTER_DAYS)).isoformat()
    for offset in range(0, 100_000, 1000):
        try:
            page = _sb("game_meta?select=store_item_id,igdb_id,enriched_at"
                       f"&limit=1000&offset={offset}") or []
        except requests.exceptions.HTTPError as exc:
            # 테이블이 아직 없으면(011 SQL 미실행) 전부 후보로 본다.
            # dry-run 매칭 확인은 되지만 실제 저장은 SQL 실행 후에만 가능하다.
            if exc.response is not None and exc.response.status_code == 404:
                print("※ game_meta 테이블 없음 — 011 SQL 실행 전에는 저장이 실패합니다")
                break
            raise
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
            "store_items?select=id,title,platform,store_product_id,"
            "ctype:current_data->>content_type,rank:current_data->popular_rank"
            f"&last_seen_at=gte.{iso}&order=id.asc&limit=1000&offset={offset}"
        ) or []
        for r in rows:
            # DLC(addon)는 게임 본편이 아니라 IGDB 매칭 대상이 아니다
            if r["id"] in done or (r.get("ctype") == "addon"):
                continue
            title = clean_title(r.get("title") or "")
            if not title:
                continue
            picked.append({
                "id": r["id"], "title": title, "platform": r["platform"],
                "pid": r.get("store_product_id") or "",
                "rank": r.get("rank") if isinstance(r.get("rank"), (int, float)) else 10_000,
            })
        if len(rows) < 1000:
            break

    # 동시에 수집이 돌면 페이지가 밀려 같은 상품이 두 페이지에 걸쳐 잡힐 수 있다.
    # 한 배치에 같은 PK 가 두 번 가면 Postgres 가 400 을 던지므로 여기서 걷어낸다.
    picked = list({c["id"]: c for c in picked}.values())
    # 인기 상위 먼저 (평점·플레이 타임이 가장 많이 보이는 자리), 나머지는 id 순
    picked.sort(key=lambda x: (x["rank"], x["id"]))
    return picked[:limit]


def to_hours(seconds) -> float | None:
    """초 → 시간. 크라우드 데이터라 말이 안 되는 값이 온다.

    (실측: 'Content Warning' 컴플리트 216,228시간 ≈ 25년 — 이 한 행이
    numeric(6,1) 오버플로(22003)를 내며 배치 전체를 400 으로 죽였다.)
    HLTB 기준 최장급(방치형/MMO)도 수천 시간 선이라 5,000h 초과는 버린다.
    """
    if not seconds:
        return None
    hours = round(seconds / 3600, 1)
    return hours if hours <= 5000 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ko-backfill", action="store_true",
                    help="기존 매칭분의 ko_support 채우기 (015 SQL 이후 일회성)")
    args = ap.parse_args()
    init_sentry("igdb")

    if not (SUPABASE_URL and SUPABASE_KEY and TWITCH_ID and TWITCH_SECRET):
        print("환경변수(SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / "
              "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET)가 필요합니다.")
        sys.exit(1)

    if args.ko_backfill:
        ko_backfill()
        return

    cands = fetch_candidates(args.limit)
    print(f"후보 {len(cands)}개 (인기 순위 → 정가 순)")

    # 스팀은 appid 공식 매핑으로 먼저 — 제목 검색이 전혀 필요 없다
    steam_map: dict[str, dict] = {}
    steam_appids = [c["pid"] for c in cands
                    if c["platform"] == "steam" and c["pid"].isdigit()]
    if steam_appids:
        try:
            steam_map = match_steam_by_appid(steam_appids)
            print(f"스팀 appid 매핑: {len(steam_map)}/{len(steam_appids)}")
        except Exception as exc:
            print(f"스팀 appid 매핑 실패({exc}) — 제목 검색으로 폴백")

    matched: list[tuple[dict, dict, str]] = []   # (후보, igdb게임, 신뢰도)
    misses: list[dict] = []
    for c in cands:
        game, conf = None, "exact"
        if c["platform"] == "steam":   # nsuid 등 타 플랫폼 숫자 ID 와 충돌 방지
            game = steam_map.get(c["pid"])
        try:
            if game is None:
                game, conf = search_game(c["title"])
        except Exception as exc:
            # 한 상품의 검색 실패가 실행 전체를 죽이지 않게 — 이 상품만 건너뛴다
            # (miss 로도 남기지 않는다: 일시 오류라 다음 실행에서 다시 시도해야 함)
            print(f"  ! 검색오류 skip: [{c['platform']}] {c['title'][:40]} ({exc})")
            capture(exc, platform=c["platform"], title=c["title"][:60])
            continue
        if game is None:
            misses.append(c)
            print(f"  · miss: [{c['platform']}] {c['title'][:44]}")
        else:
            matched.append((c, game, conf))
            print(f"  ✓ {conf:5} [{c['platform']}] {c['title'][:34]:34} → {game['name'][:40]}")

    matched_ids = sorted({g["id"] for _, g, _ in matched})
    # 보조 조회가 최종 실패해도 매칭 결과 자체는 저장한다 (전부 잃는 것보다 낫다)
    try:
        ttb = fetch_ttb(matched_ids)
    except Exception as exc:
        print(f"  ! TTB 조회 실패 — 플레이타임 없이 저장 ({exc})")
        ttb = {}
    # 언어 id 조회가 실패하면 null 로 남긴다 — '지원 없음([])'으로 오염 금지
    try:
        ko_ok = korean_language_id() is not None
        ko = fetch_ko_support(matched_ids) if ko_ok else {}
    except Exception as exc:
        print(f"  ! 한국어 지원 조회 실패 — null 로 저장 ({exc})")
        ko_ok, ko = False, {}

    if args.dry_run:
        print(f"완료(dry-run): 매칭 {len(matched)} / 실패 {len(misses)} / TTB {len(ttb)} / 한국어 {len(ko)}")
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
            "genres": [x["name"] for x in g.get("genres") or []] or None,
            # 빈 배열 = '조회했지만 지원 없음' (null 은 '아직 안 봄' — 백필 대상)
            "ko_support": ko.get(g["id"], []) if ko_ok else None,
            "enriched_at": now,
        })
    # PostgREST 일괄 insert 는 모든 행의 키가 같아야 한다 — 실패 행도 전체 키로
    rows += [{
        "store_item_id": c["id"], "igdb_id": None, "igdb_name": None,
        "match_confidence": "none",
        "critic_rating": None, "critic_rating_count": None,
        "user_rating": None, "user_rating_count": None,
        "ttb_hastily_h": None, "ttb_normally_h": None, "ttb_completely_h": None,
        "genres": None,
        "ko_support": None,
        "enriched_at": now,
    } for c in misses]

    saved = 0
    for i in range(0, len(rows), 200):
        saved += save_rows(rows[i : i + 200])
    print(f"완료: {saved}/{len(rows)}행 저장 (매칭 {len(matched)} / 실패 기록 {len(misses)} "
          f"/ 플레이 타임 {len(ttb)} / 한국어 지원 {len(ko)})")


def save_rows(rows: list[dict]) -> int:
    """일괄 upsert. 400 이면 반으로 쪼개 원인 행만 고립한다 — 나머지는 전부 저장.

    (실측: 한 행의 값 문제로 배치 전체가 400 을 받으면 원인 행이 로그에 안 남아
    두 번이나 원인을 못 찾았다. 행 단위까지 내려가 행 JSON 과 응답 본문을 찍는다.)
    """
    if not rows:
        return 0
    try:
        _sb("game_meta", method="POST", body=rows,
            extra_headers={"Prefer": "resolution=merge-duplicates"})
        return len(rows)
    except requests.exceptions.HTTPError as exc:
        body = (exc.response.text[:300] if exc.response is not None else str(exc))
        if len(rows) == 1:
            print(f"  ✗ 저장 불가: {json.dumps(rows[0], ensure_ascii=False)[:280]}")
            print(f"    사유: {body}")
            return 0
        mid = len(rows) // 2
        return save_rows(rows[:mid]) + save_rows(rows[mid:])


if __name__ == "__main__":
    main()
