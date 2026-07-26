"""닌텐도 한국 스토어 HTML 파서.

닌텐도 한국 스토어는 Magento(어도비 커머스) 기반이라
상품 목록이 아래와 비슷한 구조로 들어 있다:

  <li class="item product product-item">
    <a class="product-item-link" href="https://store.nintendo.co.kr/70010000012345">상품명</a>
    <span class="price-container" ...>
      <span data-price-type="finalPrice" data-price-amount="45480">...</span>
      <span data-price-type="oldPrice" data-price-amount="79800">...</span>
    </span>
    <img class="product-image-photo" src="...">
  </li>

⚠️ 실제 사이트 구조가 다를 수 있으므로, 첫 실행 후 저장된 원본 HTML을
확인해서 셀렉터를 조정해야 할 수 있다. 원본은 항상 저장되므로
파서를 고친 뒤 과거 데이터를 다시 처리할 수 있다.
"""
import re

from bs4 import BeautifulSoup

from collectors.base import ParsedItem
from common.logging_util import get_logger

logger = get_logger(__name__)

# 상품 상세 URL에서 상품 코드 추출 (예: /70010000122667)
PRODUCT_ID_RE = re.compile(r"/(\d{10,})")
# 원화 표기에서 숫자만 추출 (예: "₩45,480" → 45480)
PRICE_NUM_RE = re.compile(r"[\d,]+")


def _parse_price_text(text: str | None) -> float | None:
    if not text:
        return None
    m = PRICE_NUM_RE.search(text)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def parse_list_page(html: str) -> list[ParsedItem]:
    soup = BeautifulSoup(html, "lxml")
    items: list[ParsedItem] = []

    # Magento 표준 상품 타일. 구조가 다르면 원본 HTML을 보고 조정할 것.
    tiles = soup.select("li.product-item, .product-item")

    for tile in tiles:
        # 타일 안에는 앵커가 여러 개 있다 (이미지 링크가 먼저 나옴).
        # 이름이 들어있는 product-item-link를 우선으로 잡아야 title이 안 비게 된다.
        link = tile.select_one("a.product-item-link") or tile.select_one(
            "a[href*='store.nintendo.co.kr']"
        )
        if link is None:
            continue

        href = link.get("href", "")
        m = PRODUCT_ID_RE.search(href)
        if not m:
            continue
        product_id = m.group(1)
        name_el = tile.select_one(".product-item-name") or link
        title = name_el.get_text(strip=True) or None

        img = tile.select_one("img.product-image-photo, img")
        image_url = img.get("src") if img else None

        # 가격: data-price-amount 속성이 가장 정확
        final_el = tile.select_one("[data-price-type='finalPrice']")
        old_el = tile.select_one("[data-price-type='oldPrice']")

        final_price = None
        regular_price = None
        if final_el is not None and final_el.get("data-price-amount"):
            final_price = float(final_el["data-price-amount"])
        if old_el is not None and old_el.get("data-price-amount"):
            regular_price = float(old_el["data-price-amount"])

        # data 속성이 없으면 텍스트에서 추출 (예: "₩45,480")
        if final_price is None:
            price_texts = [t.get_text(strip=True) for t in tile.select(".price")]
            prices = [p for p in (_parse_price_text(t) for t in price_texts) if p]
            if prices:
                final_price = min(prices)
                if len(prices) > 1:
                    regular_price = max(prices)

        is_on_sale = (
            regular_price is not None
            and final_price is not None
            and final_price < regular_price
        )
        discount_percent = None
        if is_on_sale:
            discount_percent = round((1 - final_price / regular_price) * 100, 2)

        # 출시일 등 타일에 있는 텍스트 정보도 최대한 수집
        release_text = None
        for label in tile.select(".product-item-attribute, .release-date, .attribute"):
            text = label.get_text(" ", strip=True)
            if text and ("발매" in text or re.search(r"\d{4}\.\d{1,2}\.\d{1,2}", text)):
                release_text = text
                break

        # 타일 전체에서 얻을 수 있는 모든 정보를 extracted_data에 보존
        extracted = {
            "title": title,
            "store_url": href,
            "image_url": image_url,
            "gallery": [image_url] if image_url else [],
            "release_text": release_text,
            "tile_text": tile.get_text(" ", strip=True)[:1000],
            "price_raw": {
                "final_price": final_price,
                "regular_price": regular_price,
                "final_attr": final_el.get("data-price-amount") if final_el else None,
                "old_attr": old_el.get("data-price-amount") if old_el else None,
            },
            "data_attributes": {
                k: v for k, v in tile.attrs.items() if isinstance(v, str)
            },
        }

        items.append(
            ParsedItem(
                store_product_id=product_id,
                title=title,
                store_url=href,
                image_url=image_url,
                regular_price=regular_price,
                sale_price=final_price if is_on_sale else None,
                final_price=final_price,
                discount_percent=discount_percent,
                is_on_sale=is_on_sale,
                extracted_data=extracted,
            )
        )

    if not items:
        logger.warning("닌텐도 목록에서 상품을 하나도 찾지 못함 — HTML 구조 변경 가능성. 원본을 확인하세요.")
    return items
