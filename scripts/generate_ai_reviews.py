"""AI 한 줄 평 생성기 (Gemini).

할인 중이면서 아직 리뷰가 없는 게임을 골라, Gemini로 한국어 '한 줄 평'을
생성해 Supabase `ai_reviews` 테이블에 저장한다.

환경변수:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  (수집 워크플로와 동일)
  GEMINI_API_KEY                           (Google AI Studio 키)

옵션:
  --limit N     한 번에 생성할 최대 개수 (기본 40)
  --dry-run     저장하지 않고 결과만 출력
  --model NAME  사용할 Gemini 모델 (기본 gemini-flash-latest)

무료 티어 보호를 위해 호출 사이에 잠깐 쉰다.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.monitoring import capture, init_sentry  # noqa: E402

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _sb(path: str, method: str = "GET", body=None, extra_headers=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    r = requests.request(method, url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json() if r.text else None


def clean_title(raw: str) -> str:
    """스토어 제목을 게임 식별에 좋게 정리한다."""
    if not raw:
        return ""
    t = raw
    t = re.sub(r"^발매\s*\d{2}\.\d{1,2}\.\d{1,2}\s*", "", t)   # 닌텐도 발매일 접두어
    t = re.sub(r"^PS[45]®?용\s+", "", t, flags=re.IGNORECASE)   # PS 접두어
    # 지원 언어 나열 괄호 제거
    t = re.sub(r"\s*\([^)]*(한국어|영어|일본어|중국어|태국어)[^)]*\)", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# 이 길이 미만의 기존 평은 '짧은 옛 형식'으로 보고 순차 재생성한다
# (프롬프트가 40~70자 → 90~140자 → 150~200자로 커졌다. 새 평 우선, 남는 쿼터로 교체)
SHORT_REVIEW_LEN = 140


def fetch_candidates(limit: int) -> list[dict]:
    """할인 중 게임 중 ① 리뷰 없음 ② 짧은 옛 평 순으로, 각각 정가 높은 순."""
    # 이미 리뷰가 있는 store_item_id → 평 길이 (PostgREST 1000행 상한 → 페이지네이션)
    have: dict[int, int] = {}
    for offset in range(0, 100_000, 1000):
        page = _sb(f"ai_reviews?select=store_item_id,summary&limit=1000&offset={offset}") or []
        for row in page:
            have[row["store_item_id"]] = len(row.get("summary") or "")
        if len(page) < 1000:
            break

    # 신선(48h) + 할인 중인 상품을 정가 높은 순으로
    from datetime import timedelta
    # timestamptz 의 '+00:00' 는 URL 에서 공백이 되어 깨지므로 'Z' 로
    iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")

    fresh_new: list[dict] = []      # 리뷰 없음
    fresh_short: list[dict] = []    # 짧은 옛 평 — 재생성 대상
    # ⚠️ 예전엔 여기서 두 가지를 잘못했다.
    #  ① offset 6,000 에서 끊겼다 — 신선한 상품이 13,889개로 늘어 절반 이상이
    #     아예 후보가 못 됐다(id 순이라 뒤쪽 id 는 평생 순서가 오지 않는다).
    #  ② 할인 여부를 판단하려고 상품마다 price_snapshots 를 임베드해 받았다.
    #     6,000행 × 스냅샷이라 조회가 무거웠다.
    # 이제 store_items.cur_is_on_sale / cur_regular_price 컬럼(트리거 갱신)이
    # 있으니 필터·정렬을 DB 에서 끝낸다. DLC 제외도 여기서 함께 한다 —
    # 예전엔 DLC 에도 한 줄 평을 생성했다.
    select = "id,title,platform,cur_regular_price"
    not_addon = "or=(current_data->>content_type.is.null,current_data->>content_type.neq.addon)"
    for offset in range(0, 200_000, 1000):
        rows = _sb(
            f"store_items?select={select}&last_seen_at=gte.{iso}"
            f"&cur_is_on_sale=is.true&{not_addon}"
            f"&order=cur_regular_price.desc.nullslast,id.asc&limit=1000&offset={offset}"
        ) or []
        for r in rows:
            length = have.get(r["id"])
            if length is not None and length >= SHORT_REVIEW_LEN:
                continue
            title = clean_title(r.get("title") or "")
            if not title:
                continue
            cand = {
                "id": r["id"], "title": title, "platform": r["platform"],
                "regular": r.get("cur_regular_price") or 0,
            }
            (fresh_new if length is None else fresh_short).append(cand)
        if len(rows) < 1000:
            break
        # DB 가 정가 내림차순으로 줬으므로 쿼터를 채웠으면 더 볼 필요가 없다
        if len(fresh_new) >= limit:
            break

    # 새 평이 먼저(대작 우선), 남는 쿼터로 짧은 옛 평을 교체한다
    fresh_new.sort(key=lambda x: x["regular"], reverse=True)
    fresh_short.sort(key=lambda x: x["regular"], reverse=True)
    picked = (fresh_new + fresh_short)[:limit]
    print(f"후보 구성: 새 평 {min(len(fresh_new), limit)}개"
          f" + 짧은 평 교체 {max(0, min(limit - len(fresh_new), len(fresh_short)))}개")
    return picked


def gen_review(title: str, model: str) -> str | None:
    """Gemini로 한 줄 평 생성. 게임을 모르면 빈 문자열."""
    prompt = (
        f"너는 콘솔 게임 딜 사이트의 에디터야. 게임 '{title}'에 대한 한국어 '에디터 평'을 써줘.\n"
        "규칙:\n"
        "- 150~200자, 문장 2~3개. 구매 결정에 도움되는 핵심만: 어떤 게임이고,\n"
        "  뭐가 좋고(핵심 재미·완성도), 어떤 사람에게 맞는지. 과장/스포일러 금지.\n"
        "- 평론가 평판(메타크리틱 점수 등)을 알면 자연스럽게 녹여줘.\n"
        "- 이 게임을 확실히 모르거나 정보가 부족하면 summary를 빈 문자열로.\n"
        'JSON만 출력: {"summary": "..."}'
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4,
        },
    }
    # 429(한도)면 백오프 후 재시도
    r = None
    for attempt in range(4):
        try:
            r = requests.post(
                GEMINI_ENDPOINT.format(model=model),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": GEMINI_KEY,
                    "User-Agent": "pakpick-collector/1.0",
                },
                json=body, timeout=60,
            )
        except requests.exceptions.RequestException as exc:
            # 연결 리셋/타임아웃 등 → 백오프 후 재시도 (지속 실패면 스킵)
            wait = 5 * (attempt + 1)
            print(f"  [연결오류 {exc.__class__.__name__}] {wait}s 후 재시도")
            time.sleep(wait)
            r = None
            continue
        if r.status_code == 429:
            wait = 20 * (attempt + 1)
            print(f"  [429 한도] {wait}s 대기 후 재시도")
            time.sleep(wait)
            continue
        break
    if r is None or r.status_code != 200:
        print(f"  [gemini {r.status_code if r is not None else '??'}] {(r.text[:120] if r is not None else '')}")
        return None
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        summary = (json.loads(text).get("summary") or "").strip()
    except (KeyError, IndexError, json.JSONDecodeError):
        return None
    # 너무 짧거나 긴 건 스킵 (목표 150~200자 — 여유를 두고 거른다)
    if not summary or len(summary) < 100 or len(summary) > 320:
        return None
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="gemini-flash-lite-latest")
    ap.add_argument("--sleep", type=float, default=6.0, help="호출 간 대기(초)")
    args = ap.parse_args()
    init_sentry("ai-reviews")

    if not (SUPABASE_URL and SUPABASE_KEY and GEMINI_KEY):
        print("환경변수(SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / GEMINI_API_KEY)가 필요합니다.")
        sys.exit(1)

    cands = fetch_candidates(args.limit)
    print(f"후보 {len(cands)}개 (리뷰 없는 할인 게임, 정가 높은 순)")

    made = 0
    for c in cands:
        summary = gen_review(c["title"], args.model)
        if not summary:
            print(f"  · skip: {c['title'][:40]}")
            continue
        print(f"  ✓ {c['title'][:34]:34} → {summary}")
        if not args.dry_run:
            try:
                _sb(
                    "ai_reviews",
                    method="POST",
                    body={
                        "store_item_id": c["id"],
                        "summary": summary,
                        "sources": [],
                        "model": args.model,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    extra_headers={"Prefer": "resolution=merge-duplicates"},
                )
                made += 1
            except Exception as exc:
                # 저장 1건 실패로 남은 후보의 Gemini 쿼터를 날리지 않는다 — 이 건만 건너뜀
                print(f"  ! 저장 실패 skip: {c['title'][:36]} ({exc})")
                capture(exc, store_item_id=c["id"])
        time.sleep(args.sleep)

    print(f"완료: {made}개 저장{' (dry-run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
