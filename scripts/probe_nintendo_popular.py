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

        for label, url in (("할인 목록", BASE_LIST), ("전체 카탈로그", CATALOG)):
            try:
                html = load(url)
            except Exception as exc:
                logger.warning("[probe] %s 접근 실패: %s", label, exc)
                continue
            # 정렬 select 는 'sorter' 클래스/아이디를 쓴다 — 옵션을 통째로 본다
            opts = SORTER_RE.findall(html)
            logger.info("[probe] %s 정렬 옵션 %d개: %s", label, len(opts),
                        [(v, t.strip()) for v, t in opts][:12])
            titles = TITLE_RE.findall(html)
            logger.info("[probe] %s 기본 정렬 상위 5: %s", label,
                        [t.strip()[:24] for t in titles[:5]])

            # 인기로 보이는 옵션이 있으면 그 정렬로 다시 열어 순서가 바뀌는지 확인
            for value, text in opts:
                if any(w in text for w in ("인기", "베스트", "판매")):
                    sorted_url = f"{url}?product_list_order={value}"
                    try:
                        html2 = load(sorted_url)
                        t2 = TITLE_RE.findall(html2)
                        logger.info("[probe] %s '%s'(%s) 상위 5: %s",
                                    label, text.strip(), value,
                                    [t.strip()[:24] for t in t2[:5]])
                    except Exception as exc:
                        logger.warning("[probe] 정렬 %s 실패: %s", value, exc)

        browser.close()
    logger.info("[probe] 끝 — DB 에는 아무것도 쓰지 않았습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
