"""Xbox displaycatalog JSON 파서.

displaycatalog API 응답 구조 (핵심 경로):

  Products[]
   ├─ ProductId                       ← 상품 ID (예: 9NKX70BBCDRN)
   ├─ LocalizedProperties[0]
   │   ├─ ProductTitle                ← 상품명
   │   ├─ DeveloperName / PublisherName
   │   ├─ ShortDescription / ProductDescription
   │   └─ Images[]                    ← 이미지 목록 (Uri, ImagePurpose)
   ├─ MarketProperties[0]
   │   └─ OriginalReleaseDate         ← 출시일
   ├─ Properties / ProductType 등
   └─ DisplaySkuAvailabilities[]
       └─ Availabilities[]
           ├─ OrderManagementData.Price
           │   ├─ MSRP                ← 정가
           │   ├─ ListPrice           ← 현재 판매가
           │   └─ CurrencyCode
           └─ Conditions.StartDate / EndDate  ← 판매(할인) 기간

상품 전체 JSON을 extracted_data에 그대로 보존하므로
Game Pass 여부, 지원 기기, 기능 정보 등도 모두 남는다.
"""
from collectors.base import ParsedItem
from common.logging_util import get_logger

logger = get_logger(__name__)


def _best_image(images: list) -> str | None:
    """대표 이미지 선택: BoxArt > Poster > 첫 번째"""
    if not images:
        return None
    priority = {"BoxArt": 0, "Poster": 1, "SuperHeroArt": 2, "BrandedKeyArt": 3}
    best = sorted(
        images,
        key=lambda im: priority.get(im.get("ImagePurpose", ""), 99),
    )[0]
    uri = best.get("Uri") or ""
    if uri.startswith("//"):
        uri = "https:" + uri
    return uri or None


def _extract_price(product: dict) -> dict:
    """SKU/Availability 목록에서 실제 판매 가격을 찾는다.

    ⚠️ 한 상품에는 '구매' 항목 외에도 라이선스/데모/Game Pass 소유 확인용
    Availability가 섞여 있고, 이들은 ListPrice=0 으로 온다.
    가장 낮은 가격을 그냥 고르면 0원 항목을 집어 '할인 아님'으로 오판한다.
    따라서 Actions 에 'Purchase' 가 있는 '실제 구매 가능' 항목만 본다.
    그중 ListPrice가 가장 낮은 것(기본 에디션)을 현재가로 삼는다.
    """
    best = {
        "msrp": None, "list_price": None, "currency": "KRW",
        "start": None, "end": None, "sku_title": None,
    }

    for sku_av in product.get("DisplaySkuAvailabilities") or []:
        sku = sku_av.get("Sku") or {}
        for av in sku_av.get("Availabilities") or []:
            # 구매 가능한 항목만 (라이선스/데모 등 0원 항목 제외)
            if "Purchase" not in (av.get("Actions") or []):
                continue
            price = ((av.get("OrderManagementData") or {}).get("Price")) or {}
            list_price = price.get("ListPrice")
            msrp = price.get("MSRP")
            if list_price is None:
                continue
            if best["list_price"] is None or list_price < best["list_price"]:
                conditions = av.get("Conditions") or {}
                best = {
                    "msrp": msrp,
                    "list_price": list_price,
                    "currency": price.get("CurrencyCode") or "KRW",
                    "start": conditions.get("StartDate"),
                    "end": conditions.get("EndDate"),
                    "sku_title": (
                        (sku.get("LocalizedProperties") or [{}])[0].get("SkuTitle")
                        if sku.get("LocalizedProperties")
                        else None
                    ),
                }
    return best


def parse_catalog_products(data: dict) -> list[ParsedItem]:
    items: list[ParsedItem] = []

    for product in data.get("Products") or []:
        product_id = product.get("ProductId")
        if not product_id:
            continue

        loc = (product.get("LocalizedProperties") or [{}])[0]
        title = loc.get("ProductTitle")
        image_url = _best_image(loc.get("Images") or [])

        market = (product.get("MarketProperties") or [{}])[0]
        release_date = market.get("OriginalReleaseDate")

        price = _extract_price(product)
        msrp = price["msrp"]
        list_price = price["list_price"]

        is_on_sale = (
            msrp is not None and list_price is not None and list_price < msrp
        )
        discount_percent = None
        if is_on_sale and msrp:
            discount_percent = round((1 - list_price / msrp) * 100, 2)

        store_url = f"https://www.xbox.com/ko-KR/games/store/p/{product_id}"

        # 상품 JSON 전체를 보존 — Game Pass, 지원 기기, 기능 등 모든 정보 포함
        extracted = {
            "product": product,
            "price_raw": price,
            "release_date": release_date,
            "developer": loc.get("DeveloperName"),
            "publisher": loc.get("PublisherName"),
            "short_description": loc.get("ShortDescription"),
        }

        items.append(
            ParsedItem(
                store_product_id=product_id,
                title=title,
                store_url=store_url,
                image_url=image_url,
                regular_price=msrp,
                sale_price=list_price if is_on_sale else None,
                final_price=list_price,
                discount_percent=discount_percent,
                sale_start_at=price["start"],
                sale_end_at=price["end"],
                is_on_sale=is_on_sale,
                currency=price["currency"] or "KRW",
                extracted_data=extracted,
            )
        )

    return items
