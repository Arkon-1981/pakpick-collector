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


def _extract_meta(product: dict, market: dict, loc: dict) -> dict:
    """카탈로그 응답에 이미 들어 있는데 안 쓰고 있던 정보를 꺼낸다.

    전부 같은 응답 안에 있어 **추가 요청이 0회**다. 지금까지는 product 원본만
    통째로 보관해 두고 상위 키로 꺼내지 않아 웹에서 조회·정렬에 못 쓰고 있었다.
    """
    props = product.get("Properties") or {}
    out: dict = {}

    # 평점: 기간별로 여러 개가 오는데 표본이 가장 큰 AllTime 을 쓴다
    for usage in market.get("UsageData") or []:
        if usage.get("AggregateTimeSpan") == "AllTime" and usage.get("RatingCount"):
            out["review"] = {
                "count": usage.get("RatingCount"),
                "average": usage.get("AverageRating"),   # 5점 만점
            }
            break

    genres = props.get("Categories") or ([props["Category"]] if props.get("Category") else [])
    if genres:
        out["genres"] = list(dict.fromkeys(g for g in genres if g))

    # 연령등급: 한국 등급(GRB)이 있으면 그것을, 없으면 첫 등급을 쓴다
    ratings = market.get("ContentRatings") or []
    korean = next((r for r in ratings if r.get("RatingSystem") == "GRB"), None)
    chosen = korean or (ratings[0] if ratings else None)
    if chosen and chosen.get("RatingId"):
        out["content_rating"] = chosen["RatingId"]       # 예: "GRB:18"

    # 지원 세대: ConsoleGen9 = Series X|S, ConsoleGen8 = Xbox One
    gens = props.get("XboxConsoleGenCompatible") or []
    if gens:
        out["platforms"] = [
            {"ConsoleGen9": "Xbox Series X|S", "ConsoleGen8": "Xbox One"}.get(g, g)
            for g in gens
        ]
    if props.get("XboxLiveGoldRequired") is not None:
        out["gold_required"] = bool(props["XboxLiveGoldRequired"])
    if loc.get("Franchises"):
        out["franchises"] = loc["Franchises"]
    return out


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


def _includes_gamepass(obj) -> bool:
    """카탈로그 상품 JSON 어딘가의 Affirmations 에 Game Pass 멤버십 문구가 있는가.

    (실측: "with your Xbox Game Pass Premium membership." 같은 안내가
    Affirmations 배열로 온다. 중첩 위치가 SKU 구조에 따라 달라 재귀로 찾는다.)
    """
    if isinstance(obj, dict):
        for aff in obj.get("Affirmations") or []:
            if "game pass" in (aff.get("Description") or "").lower():
                return True
        return any(_includes_gamepass(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_includes_gamepass(v) for v in obj)
    return False


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

        # 상품 원본은 저장하지 않는다 — 행당 47KB(전 플랫폼 최대)로 DB 를 가장 많이
        # 먹었고, 가격이 JSON 안에 박혀 있어 바뀔 때마다 버전 기록까지 통째로 불었다.
        # 원본은 어차피 Storage 에 gz 로 남고, 필요한 값은 아래처럼 골라 뽑는다.
        extracted = {
            # 구독 포함 표시 — 웹이 중첩 JSON 을 못 뒤지므로 최상위에 뽑아 둔다
            **({"subscription": "gamepass"} if _includes_gamepass(product) else {}),
            "price_raw": price,
            "release_date": release_date,
            "developer": loc.get("DeveloperName"),
            "publisher": loc.get("PublisherName"),
            "short_description": loc.get("ShortDescription"),
            "gallery": gallery,
            **_extract_meta(product, market, loc),
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
