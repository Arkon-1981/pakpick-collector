"""닌텐도 스토어 인기 정렬 탐사 — 일회용.

    python scripts/probe_nintendo_popular.py

'인기' 탭에 닌텐도를 합류시키려면 스토어 목록의 인기 정렬 파라미터가 필요한데
(Magento 라 product_list_order=... 형태일 것), 값이 문서에 없다.
스토어는 WAF 봇 차단이라 러너의 실제 브라우저로만 볼 수 있다 — 여기서
목록 페이지의 정렬 <select> 옵션을 그대로 읽어 로그로 남긴다. DB 는 안 건드린다.

확인하려는 것
  1. 정렬 드롭다운에 어떤 값이 있는가 (인기순이 존재하는가)
  2. 그 값으로 정렬한 목록의 상위 상품이 기본 정렬과 실제로 다른가
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config                    # noqa: E402
from common.logging_util import get_logger   # noqa: E402

logger = get_logger(__name__)

# 정렬 옵션을 읽을 목록 페이지 (할인 목록 = 수집기가 이미 쓰는 곳)
BASE_LIST = "https://store.nintendo.co.kr/digital/sale"
# 전체 디지털 카탈로그 — 인기 정렬은 여기가 본진일 수 있다
CATALOG = "https://store.nintendo.co.kr/digital"

SORTER_RE = re.compile(
    r'<option[^>]*value="([^"]*?)"[^>]*>([^<]{1,30})</option>', re.IGNORECASE
)
TITLE_RE = re.compile(
    r'class="product-item-link"[^>]*>\s*([^<]{2,80})', re.IGNORECASE
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

        def load(url: str) -> str:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector("li.product-item, .product-item", timeout=20_000)
            except Exception:
                page.wait_for_timeout(4_000)
            return page.content()

        # 2차: 드롭다운이 <option> 이 아니었다(1차 실측 0개). 정렬 마크업을 통째로
        # 덤프하고, Magento 표준 인기 정렬 값을 직접 찔러 순서 변화를 비교한다.
        html = load(CATALOG)
        base_titles = [t.strip()[:22] for t in TITLE_RE.findall(html)][:5]
        logger.info("[probe] 기본 정렬 상위 5: %s", base_titles)

        # 정렬 관련 마크업 덤프 — 파라미터 이름과 값이 이 근처에 있다
        low = html.lower()
        for needle in ("product_list_order", "sorter", "정렬"):
            pos, shown = 0, 0
            while shown < 3:
                i = low.find(needle, pos)
                if i < 0:
                    break
                snippet = re.sub(r"\s+", " ", html[max(0, i-120):i+240])
                logger.info("[probe] '%s' 주변: %s", needle, snippet[:330])
                pos = i + len(needle)
                shown += 1

        # Magento 에서 흔한 인기 정렬 값들을 직접 시도
        for value in ("bestsellers", "popularity", "most_ordered", "most_viewed",
                      "top_rated", "sales", "ranking", "position"):
            try:
                h2 = load(f"{CATALOG}?product_list_order={value}")
                t2 = [t.strip()[:22] for t in TITLE_RE.findall(h2)][:5]
                changed = "다름!" if (t2 and t2 != base_titles) else "동일/비어있음"
                logger.info("[probe] order=%s → %s  상위 5: %s", value, changed, t2)
            except Exception as exc:
                logger.warning("[probe] order=%s 실패: %s", value, exc)

        browser.close()
    logger.info("[probe] 끝 — DB 에는 아무것도 쓰지 않았습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
