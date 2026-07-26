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


# 대표 이미지(카드/캐러셀 첫 장)로 쓸 아트 우선순위.
# 이 용도들은 '같은 게임 아트'의 다른 형태(박스/포스터/키아트)라 캐러셀엔 1장만 쓴다.
_XBOX_ART_PRIORITY = {"SuperHeroArt": 0, "TitledHeroArt": 1, "BrandedKeyArt": 2, "Poster": 3, "BoxArt": 4}


def _norm_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    return ("https:" + uri) if uri.startswith("//") else uri


def _images(images: list) -> tuple[str | None, list[str]]:
    """(대표 이미지, 갤러리)을 뽑는다.

    갤러리 = [대표 아트 1장] + [게임 스크린샷들].
    (예전엔 박스/포스터/키아트를 다 넣어 '같은 그림 다른 형태'가 반복됐음 → 스샷 위주로 교체)
    """
    if not images:
        return None, []
    arts: list[tuple[int, str]] = []   # 대표 후보
    shots: list[str] = []              # 게임 스크린샷
    for im in images:
        uri = _norm_uri(im.get("Uri"))
        if not uri:
            continue
        purpose = im.get("ImagePurpose", "")
        if purpose == "Screenshot":
            if uri not in shots:
                shots.append(uri)
        elif purpose in _XBOX_ART_PRIORITY:
            arts.append((_XBOX_ART_PRIORITY[purpose], uri))
        # Logo/Tile/FeaturePromotionalSquareArt 등은 제외

    arts.sort(key=lambda x: x[0])
    representative = arts[0][1] if arts else (shots[0] if shots else None)

    gallery: list[str] = []
    if representative:
        gallery.append(representative)
    for uri in shots:
        if uri not in gallery:
            gallery.append(uri)
    gallery = gallery[:6]  # 대표 1 + 스샷 최대 5
    return (representative, gallery)


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
        image_url, gallery = _images(loc.get("Images") or [])

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
            "gallery": gallery,
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
