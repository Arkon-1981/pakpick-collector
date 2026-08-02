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

import requests

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


def fetch_candidates(limit: int) -> list[dict]:
    """할인 중 + 리뷰 없음 게임을 정가 높은 순(대작 우선)으로 고른다."""
    # 이미 리뷰가 있는 store_item_id 집합 (PostgREST 1000행 상한 → 페이지네이션)
    have: set = set()
    for offset in range(0, 100_000, 1000):
        page = _sb(f"ai_reviews?select=store_item_id&limit=1000&offset={offset}") or []
        have.update(row["store_item_id"] for row in page)
        if len(page) < 1000:
            break

    # 신선(48h) + 할인 중인 상품을 정가 순으로
    from datetime import timedelta
    # timestamptz 의 '+00:00' 는 URL 에서 공백이 되어 깨지므로 'Z' 로
    iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")

    picked: list[dict] = []
    select = (
        "id,title,platform,"
        "price_snapshots(is_on_sale,regular_price,collected_at)"
    )
    # 페이지네이션
    for offset in range(0, 6000, 1000):
        rows = _sb(
            f"store_items?select={select}&last_seen_at=gte.{iso}"
            f"&order=id.asc&limit=1000&offset={offset}"
        ) or []
        for r in rows:
            if r["id"] in have:
                continue
            snaps = sorted(
                r.get("price_snapshots") or [],
                key=lambda s: s["collected_at"], reverse=True,
            )
            if not snaps or not snaps[0].get("is_on_sale"):
                continue
            title = clean_title(r.get("title") or "")
            if not title:
                continue
            picked.append({
                "id": r["id"], "title": title, "platform": r["platform"],
                "regular": snaps[0].get("regular_price") or 0,
            })
        if len(rows) < 1000:
            break

    # 정가 높은 순(대작 우선) → 상위 limit개
    picked.sort(key=lambda x: x["regular"], reverse=True)
    return picked[:limit]


def gen_review(title: str, model: str) -> str | None:
    """Gemini로 한 줄 평 생성. 게임을 모르면 빈 문자열."""
    prompt = (
        f"너는 콘솔 게임 딜 사이트의 에디터야. 게임 '{title}'에 대한 한국어 '에디터 평'을 써줘.\n"
        "규칙:\n"
        "- 90~140자, 문장 두 개까지. 구매 결정에 도움되는 핵심만: 어떤 게임이고,\n"
        "  뭐가 좋고, 어떤 사람에게 맞는지. 과장/스포일러 금지.\n"
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
    # 너무 짧거나 긴 건 스킵 (목표 90~140자 — 여유를 두고 거른다)
    if not summary or len(summary) < 40 or len(summary) > 240:
        return None
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="gemini-flash-lite-latest")
    ap.add_argument("--sleep", type=float, default=6.0, help="호출 간 대기(초)")
    args = ap.parse_args()

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
        time.sleep(args.sleep)

    print(f"완료: {made}개 저장{' (dry-run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
