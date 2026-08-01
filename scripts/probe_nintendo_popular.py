"""닌텐도 스토어 인기 정렬 탐사 — 일회용.

    python scripts/probe_nintendo_popular.py

'인기' 탭에 닌텐도를 합류시키려면 스토어 목록의 인기 정렬이 필요한데,
1·2차 실측 결과 목록 페이지에는 정렬 UI 자체가 없고
product_list_order 파라미터도 전부 무시됐다(8개 값 모두 기본 순서와 동일).

3차: 방향을 바꾼다. 스토어는 WAF 봇 차단이라 러너의 실제 브라우저로만
접근 가능 — 브라우저 컨텍스트 안에서 두 가지를 확인한다. DB 는 안 건드린다.

  1. 홈 화면: '인기/베스트/랭킹' 류의 링크·섹션이 있는가
     (전용 랭킹 페이지나 인기 캐러셀이 있으면 그걸 소스로 쓴다)
  2. Magento GraphQL(/graphql): 정렬 입력 타입을 인트로스펙션으로 직접 조회
     (bestseller 류 정렬 필드가 노출되어 있으면 목록 UI 없이도 쓸 수 있다)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config                    # noqa: E402
from common.logging_util import get_logger   # noqa: E402

logger = get_logger(__name__)

HOME = "https://store.nintendo.co.kr/"

LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.{0,120}?)</a>', re.IGNORECASE | re.DOTALL)
HEADING_RE = re.compile(r"<h[23][^>]*>\s*([^<]{2,60})", re.IGNORECASE)
KEYWORDS = ("best", "rank", "popular", "top", "chart", "인기", "베스트", "랭킹", "순위")

# Magento 2 GraphQL — 정렬 입력 타입과 상품 정렬 시도를 한 번에
GQL_SORT_FIELDS = '{ __type(name: "ProductAttributeSortInput") { inputFields { name } } }'
GQL_BESTSELLER = (
    '{ products(search: "", pageSize: 10, sort: {%s: DESC})'
    ' { items { name } } }'
)


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ko-KR", user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
        page = context.new_page()
        page.goto(HOME, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(6_000)   # 위젯(캐러셀)은 JS 렌더 — 잠깐 기다린다
        html = page.content()

        # 1) 인기 냄새가 나는 링크
        seen: set[str] = set()
        for href, label in LINK_RE.findall(html):
            text = re.sub(r"<[^>]+>|\s+", " ", label).strip()
            hay = (href + " " + text).lower()
            if any(k in hay for k in KEYWORDS) and href not in seen:
                seen.add(href)
                logger.info("[probe] 링크: %s  (%s)", href, text[:40])
        if not seen:
            logger.info("[probe] 인기/베스트 류 링크 없음")

        # 2) 홈 섹션 제목 — '인기 상품' 캐러셀이 있는지
        headings = [h.strip() for h in HEADING_RE.findall(html)]
        logger.info("[probe] 홈 섹션 제목: %s", headings[:20])

        # 3) GraphQL 정렬 필드 인트로스펙션 (브라우저 안에서 fetch → WAF 통과)
        def gql(query: str) -> dict:
            return page.evaluate(
                """async (q) => {
                    const r = await fetch('/graphql', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({query: q}),
                    });
                    const text = await r.text();
                    try { return {status: r.status, body: JSON.parse(text)}; }
                    catch { return {status: r.status, body: text.slice(0, 400)}; }
                }""",
                query,
            )

        res = gql(GQL_SORT_FIELDS)
        logger.info("[probe] GraphQL 정렬 필드: %s", json.dumps(res, ensure_ascii=False)[:800])

        fields: list[str] = []
        try:
            fields = [f["name"] for f in res["body"]["data"]["__type"]["inputFields"]]
        except (KeyError, TypeError):
            pass

        # bestseller 류 필드가 있으면 실제 정렬 결과까지 바로 확인
        for cand in fields:
            if any(k in cand.lower() for k in ("best", "sold", "popular", "rank")):
                r2 = gql(GQL_BESTSELLER % cand)
                logger.info("[probe] sort=%s 상위: %s",
                            cand, json.dumps(r2, ensure_ascii=False)[:600])

        browser.close()
    logger.info("[probe] 끝 — DB 에는 아무것도 쓰지 않았습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
