"""닌텐도 스토어 인기 정렬 탐사 — 일회용.

    python scripts/probe_nintendo_popular.py

3차 실측으로 전용 베스트셀러 페이지를 찾았다:
  https://store.nintendo.co.kr/digital/best-sellers  (홈 상단 'BEST' 링크)
(목록 정렬 파라미터는 전부 무시, GraphQL 은 404 — 이 페이지가 유일한 소스)

4차: 이 페이지가 수집기 파서(parse_list_page)로 그대로 읽히는지,
순서가 의미 있는지(가나다순이 아닌지), 페이지네이션이 있는지 확인한다.
DB 는 안 건드린다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config                    # noqa: E402
from common.logging_util import get_logger   # noqa: E402
from parsers.nintendo import parse_list_page  # noqa: E402

logger = get_logger(__name__)

BEST = "https://store.nintendo.co.kr/digital/best-sellers"


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

        def load(url: str) -> str:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector("li.product-item, .product-item", timeout=20_000)
            except Exception:
                page.wait_for_timeout(4_000)
            return page.content()

        prev_ids: set[str] = set()
        for p in (1, 2, 3):
            items = parse_list_page(load(f"{BEST}?p={p}"))
            ids = {i.store_product_id for i in items}
            dup = "전부 중복!" if ids and ids <= prev_ids else ""
            logger.info("[probe] p=%d: %d개 %s", p, len(items), dup)
            for rank, it in enumerate(items[:10], start=1 + (p - 1) * len(prev_ids or ids)):
                logger.info("[probe]   #%2d %s | %s원 | 할인 %s%%",
                            rank, (it.title or "?")[:30], it.final_price, it.discount_percent)
            if not ids or ids <= prev_ids:
                break
            prev_ids |= ids

        browser.close()
    logger.info("[probe] 끝 — DB 에는 아무것도 쓰지 않았습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
