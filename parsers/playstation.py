"""플레이스테이션 스토어 파서.

PS 스토어 페이지의 <script id="__NEXT_DATA__"> 안 JSON에서 상품을 추출한다.

JSON 구조 (핵심 부분):
  props.apolloState  안에  "Product:UP0001-PPSA12345_00-XXXX" 형태의 키로
  상품 객체가 들어 있고, 각 객체에 name, price, media 등이 있다.

price 객체 예시:
  {
    "basePrice": "₩89,800",        ← 정가
    "discountedPrice": "₩67,350",  ← 할인가
    "discountText": "-25%",
    "endTime": "1753801140000",    ← 할인 종료 (밀리초 타임스탬프)
    "isFree": false, "isExclusive": false, ...
  }

⚠️ 구조가 바뀔 수 있으므로 apolloState에서 못 찾으면
JSON 전체를 재귀 탐색하는 예비 로직도 갖춰 두었다.
원본 HTML은 항상 저장되므로 파서 수정 후 재처리 가능.
"""
import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from collectors.base import ParsedItem
from common.logging_util import get_logger

logger = get_logger(__name__)

PRICE_NUM_RE = re.compile(r"[\d,]+")


def extract_next_data(html: str) -> dict | None:
    """HTML에서 __NEXT_DATA__ JSON을 꺼낸다."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        logger.warning("__NEXT_DATA__ JSON 파싱 실패")
        return None


def _parse_krw(text: str | None) -> float | None:
    """"₩89,800" → 89800.0 / "무료" → 0"""
    if not text:
        return None
    if "무료" in text or text.strip().lower() == "free":
        return 0.0
    m = PRICE_NUM_RE.search(text)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _parse_epoch_ms(value) -> str | None:
    """밀리초 타임스탬프 → ISO 문자열"""
    if not value:
        return None
    try:
        ts = int(value) / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


# 상세 페이지의 Price 노드에 있는 할인 종료시각. 목록(SkuPrice)엔 없고 상세에만 있다.
# 한 상세 페이지에 여러 에디션 가격이 있을 수 있어, 같은 price 객체 안의
# discountedValue(할인가 정수)로 대상 상품을 매칭한다.
_END_DISCOUNTED_RE = re.compile(
    r'"endTime":"(\d{13})"[^{}]*?"discountedValue":(\d+)'
)
_END_ANY_RE = re.compile(r'"endTime":"(\d{13})"')


def parse_detail_end_time(html: str, target_discounted: float | None = None) -> str | None:
    """상품 상세 HTML(__NEXT_DATA__)에서 할인 종료시각(ISO)을 뽑는다.

    price 객체가 중첩 JSON 문자열로 이스케이프돼 있을 수 있어(\\") 먼저 정규화한다.
    target_discounted(할인가)와 같은 discountedValue를 가진 endTime을 우선 매칭하고,
    못 찾으면 첫 endTime을 쓴다. 없으면 None.
    """
    if not html:
        return None
    norm = html.replace('\\"', '"')
    pairs = _END_DISCOUNTED_RE.findall(norm)
    if pairs:
        if target_discounted is not None:
            tgt = int(round(target_discounted))
            for et, dv in pairs:
                if int(dv) == tgt:
                    return _parse_epoch_ms(et)
        return _parse_epoch_ms(pairs[0][0])
    m = _END_ANY_RE.search(norm)
    return _parse_epoch_ms(m.group(1)) if m else None


# 대표 이미지(카드/캐러셀 첫 장)로 쓸 아트(키아트) 우선순위.
# 이 역할들은 사실상 '같은 키아트'의 다른 크기/버전이라 캐러셀엔 1장만 쓴다.
_PS_ART_PRIORITY = {
    "GAMEHUB_COVER_ART": 0, "MASTER": 1, "EDITION_KEY_ART": 2,
    "SIXTEEN_BY_NINE_BANNER": 3, "FOUR_BY_THREE_BANNER": 4,
}


# 스크린샷이 아예 없는 상품에서 갤러리를 채울 '가로 아트' 우선순위.
# 소니는 옛 게임·에디션·DLC 에 SCREENSHOT 을 안 주는 경우가 많다(실측: 신선한 PS
# 상품의 18%가 스샷 없음. 그중 대부분은 단품 GraphQL 로 다시 물어봐도 같은 응답이라
# '더 받아올' 스크린샷이 존재하지 않는다). 그럴 때 대표 1장만 남아 캐러셀이 빈다.
# 이미 받아 둔 다른 아트를 쓰면 추가 요청 0회로 몇 장이라도 채울 수 있다.
# ⚠️ LOGO(투명 로고)와 PORTRAIT_BANNER(세로)는 제외 — 16:9 카드에서 망가진다.
_PS_FALLBACK_ART = ("BACKGROUND", "SIXTEEN_BY_NINE_BANNER", "EDITION_KEY_ART",
                    "GAMEHUB_COVER_ART", "MASTER", "FOUR_BY_THREE_BANNER")


def _images_from_media(media, apollo: dict) -> tuple[str | None, list[str]]:
    """media 배열에서 (대표 이미지, 갤러리)을 뽑는다.

    갤러리 = [대표 키아트 1장] + [게임 스크린샷들].
    (예전엔 키아트 여러 버전을 다 넣어 '같은 그림 다른 크기'가 반복됐음 → 스샷 위주로 교체)
    스크린샷이 하나도 없으면 그때만 다른 가로 아트로 채운다 — 대표 1장짜리
    캐러셀보다는 낫고, 스샷이 있는 상품의 표시는 예전 그대로다.
    """
    if not isinstance(media, list):
        return None, []
    arts: list[tuple[int, str]] = []   # 대표 후보 (키아트)
    shots: list[str] = []              # 게임 스크린샷
    others: list[tuple[int, str]] = [] # 스샷이 없을 때 쓸 예비 아트
    for m in media:
        if isinstance(m, dict) and "__ref" in m:
            m = apollo.get(m["__ref"], {})
        if not isinstance(m, dict):
            continue
        url = m.get("url")
        if not url or (m.get("type") not in (None, "IMAGE")):
            continue  # 동영상(PREVIEW) 등 제외
        role = m.get("role", "")
        if role == "SCREENSHOT":
            if url not in shots:
                shots.append(url)
            continue
        if role in _PS_ART_PRIORITY:
            arts.append((_PS_ART_PRIORITY[role], url))
        if role in _PS_FALLBACK_ART:
            others.append((_PS_FALLBACK_ART.index(role), url))
        # LOGO(투명 로고)·PORTRAIT_BANNER(세로) 는 어느 쪽도 아님 → 제외

    arts.sort(key=lambda x: x[0])
    representative = arts[0][1] if arts else (shots[0] if shots else None)

    gallery: list[str] = []
    if representative:
        gallery.append(representative)
    for url in shots:
        if url not in gallery:
            gallery.append(url)
    if not shots:
        # 스샷이 없을 때만 — 대표로 이미 쓴 것은 빼고 나머지 아트를 순서대로
        others.sort(key=lambda x: x[0])
        for _, url in others:
            if url not in gallery:
                gallery.append(url)
    gallery = gallery[:6]  # 대표 1 + 스샷(또는 예비 아트) 최대 5
    return representative, gallery


def _product_from_node(key: str, node: dict, apollo: dict) -> ParsedItem | None:
    """apolloState의 Product 노드 1개를 ParsedItem으로 변환한다."""
    product_id = node.get("id") or key.split(":", 1)[-1]
    if not product_id:
        return None

    title = node.get("name")

    # price는 참조(__ref)로 연결돼 있을 수도, 바로 들어있을 수도 있다
    price_node = node.get("price")
    if isinstance(price_node, dict) and "__ref" in price_node:
        price_node = apollo.get(price_node["__ref"], {})
    if not isinstance(price_node, dict):
        price_node = {}

    base = _parse_krw(price_node.get("basePrice"))
    discounted = _parse_krw(price_node.get("discountedPrice"))
    is_on_sale = (
        base is not None and discounted is not None and discounted < base
    )

    # 할인율은 실제 세일 중일 때만 (비세일 상품에 잔여 discountText가 붙는 경우 방지)
    discount_percent = None
    discount_text = price_node.get("discountText")  # 예: "-25%"
    if is_on_sale and discount_text:
        m = re.search(r"(\d+(?:\.\d+)?)", discount_text)
        if m:
            discount_percent = float(m.group(1))

    # 이미지: media 배열에서 대표 이미지 + 갤러리(캐러셀용) 추출
    image_url, gallery = _images_from_media(node.get("media"), apollo)

    store_url = f"https://store.playstation.com/ko-kr/product/{product_id}"

    # 노드 전체를 보존 (플랫폼, 등급, 타입 등 모든 필드)
    extracted = {
        "apollo_key": key,
        "node": node,
        "price_raw": price_node,
        "gallery": gallery,
    }

    return ParsedItem(
        store_product_id=product_id,
        title=title,
        store_url=store_url,
        image_url=image_url,
        regular_price=base,
        sale_price=discounted if is_on_sale else None,
        final_price=discounted if discounted is not None else base,
        discount_percent=discount_percent,
        sale_end_at=_parse_epoch_ms(price_node.get("endTime")),
        is_on_sale=is_on_sale,
        extracted_data=extracted,
    )


def parse_concepts_from_next_data(next_data: dict) -> list[ParsedItem]:
    """apolloState의 Concept 노드에서 상품을 뽑는다.

    '신규 발매' 같은 일부 카테고리는 Product 노드가 껍데기({__typename,id})이고
    실제 이름·가격·이미지는 게임 단위 엔티티인 Concept 노드에 들어 있다.
    Concept.products[0].__ref 가 실제 Product ID를 가리키므로, 그 ID로 맞춰
    기존 상품과 같은 식별자 체계를 유지한다.
    """
    apollo = (next_data.get("props") or {}).get("apolloState") or {}
    items: list[ParsedItem] = []
    for key, node in apollo.items():
        if not isinstance(node, dict):
            continue
        if not (key.startswith("Concept:") or node.get("__typename") == "Concept"):
            continue
        try:
            refs = node.get("products") or []
            ref = refs[0].get("__ref") if refs and isinstance(refs[0], dict) else None
            if not ref:
                continue
            # "Product:JP0101-PPSA34474_00-PROBBSPIRITS2026:ko-kr" → 가운데 ID만
            parts = ref.split(":")
            product_id = parts[1] if len(parts) >= 2 else None
            if not product_id:
                continue
            item = _product_from_node(ref, {**node, "id": product_id}, apollo)
            if item and item.title:
                items.append(item)
        except Exception:
            logger.exception("PS Concept 노드 파싱 실패: %s", key)
    return items


def parse_products_from_next_data(next_data: dict) -> list[ParsedItem]:
    items: list[ParsedItem] = []

    apollo = (next_data.get("props") or {}).get("apolloState") or {}
    if apollo:
        for key, node in apollo.items():
            if not isinstance(node, dict):
                continue
            if key.startswith("Product:") or node.get("__typename") == "Product":
                # 껍데기 참조 노드({__typename,id}만 있는 것)는 건너뛴다.
                # 이런 카테고리는 실제 데이터가 Concept 노드에 있다.
                if node.get("name") is None and node.get("price") is None:
                    continue
                try:
                    item = _product_from_node(key, node, apollo)
                    if item:
                        items.append(item)
                except Exception:
                    logger.exception("PS 상품 노드 파싱 실패: %s", key)

    if items:
        return items

    # 예비: apolloState가 없거나 비었으면 JSON 전체를 재귀 탐색
    logger.warning("apolloState에서 상품을 못 찾음 — 전체 JSON 재귀 탐색 시도")
    found: list[ParsedItem] = []

    def walk(obj):
        if isinstance(obj, dict):
            if (
                obj.get("__typename") == "Product"
                and obj.get("id")
                # 껍데기 참조 노드는 제외 (실제 데이터는 Concept 노드에 있음)
                and not (obj.get("name") is None and obj.get("price") is None)
            ):
                try:
                    item = _product_from_node(f"Product:{obj['id']}", obj, {})
                    if item:
                        found.append(item)
                except Exception:
                    pass
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(next_data)
    return found


# ---------------------------------------------------------------------------
# GraphQL 카테고리 조회 (web.np.playstation.com/api/graphql)
# ---------------------------------------------------------------------------
# HTML 페이지는 한 번에 24개만 주지만 이 엔드포인트는 100개를 준다(실측: 한 카테고리
# totalCount 5,906 / 요청당 100개 / 할인가·할인율 포함). 요청 수가 1/4로 줄어 같은
# 시간예산에 훨씬 넓은 카탈로그를 덮는다.
# 상품 노드 구조가 apolloState 의 Product 와 같아서 기존 변환기를 그대로 재사용한다.
def parse_products_from_graphql(data: dict) -> list[ParsedItem]:
    grid = ((data.get("data") or {}).get("categoryGridRetrieve")) or {}
    items: list[ParsedItem] = []
    for p in grid.get("products") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        try:
            item = _product_from_node(f"Product:{p['id']}", p, {})
            if item and item.title:
                items.append(item)
        except Exception:
            logger.exception("PS GraphQL 상품 파싱 실패: %s", p.get("id"))
    return items


def graphql_total_count(data: dict) -> int | None:
    grid = ((data.get("data") or {}).get("categoryGridRetrieve")) or {}
    return (grid.get("pageInfo") or {}).get("totalCount")


# ---------------------------------------------------------------------------
# GraphQL 단품 조회 파서 (상세 HTML 대체)
# ---------------------------------------------------------------------------
# 상세 HTML은 1건에 400KB인데, 같은 정보를 주는 공개 GraphQL 오퍼레이션이 있다.
#   productRetrieveForCtasWithPrice  → 2.7KB  (할인 종료일)
#   metGetProductById                → 16KB   (출시일·퍼블리셔·장르·등급)
# 실측 기준 종료일 조회 기준 약 150배 가벼워, 같은 시간예산에서 훨씬 많이 훑을 수 있다.


def parse_cta_price(data: dict, target_discounted: float | None = None) -> dict | None:
    """productRetrieveForCtasWithPrice 응답에서 가격/할인 종료일을 뽑는다.

    webctas에는 일반 구매가와 PS Plus 전용가가 함께 오고, 종료일이 서로 다르다.
    target_discounted(목록에서 본 할인가)와 같은 값을 가진 CTA를 우선 고르고,
    없으면 '일반 구매 가능(APPLICABLE)' CTA를, 그것도 없으면 첫 CTA를 쓴다.
    """
    product = (data.get("data") or {}).get("productRetrieve") or {}
    ctas = [c for c in (product.get("webctas") or []) if isinstance(c, dict) and c.get("price")]
    if not ctas:
        return None

    chosen = None
    if target_discounted is not None:
        tgt = int(round(target_discounted))
        chosen = next(
            (c for c in ctas if c["price"].get("discountedValue") == tgt), None
        )
    if chosen is None:
        chosen = next(
            (c for c in ctas if c["price"].get("applicability") == "APPLICABLE"), ctas[0]
        )

    price = chosen["price"]
    return {
        "sale_end_at": _parse_epoch_ms(price.get("endTime")),
        "base_price": price.get("basePriceValue"),
        "discounted_price": price.get("discountedValue"),
        "discount_text": price.get("discountText"),
        # PS Plus 가입자만 받는 할인인지 (일반 이용자 체감가와 다르다)
        "plus_only": price.get("applicability") == "UPSELL"
        or bool(price.get("isTiedToSubscription")),
        "cta_type": chosen.get("type"),
    }


def parse_product_meta(data: dict) -> dict:
    """metGetProductById 응답에서 출시일·퍼블리셔·장르·등급 등을 뽑는다.

    값이 없는 항목은 넣지 않는다 → 호출부에서 기존 값을 덮어쓰지 않게.
    Product에 비어 있는 값은 게임 단위 엔티티인 concept 쪽을 한 번 더 본다.
    """
    product = (data.get("data") or {}).get("productRetrieve") or {}
    if not product:
        return {}
    concept = product.get("concept") or {}

    def pick(key):
        return product.get(key) or concept.get(key)

    out: dict = {}
    if pick("releaseDate"):
        out["release_date"] = pick("releaseDate")
    if pick("publisherName"):
        out["publisher"] = pick("publisherName")

    # 소니가 장르/서브장르를 합쳐서 주다 보니 같은 값이 두 번 오는 경우가 있다 → 순서 유지 중복 제거
    genres = list(dict.fromkeys(
        g["value"] for g in (pick("combinedLocalizedGenres") or [])
        if isinstance(g, dict) and g.get("value")
    ))
    if genres:
        out["genres"] = genres

    rating = product.get("contentRating") or {}
    if rating.get("description"):
        out["content_rating"] = rating["description"]  # 예: "GRAC 15+"

    for desc in (pick("descriptions") or []):
        if isinstance(desc, dict) and desc.get("type") == "SHORT" and desc.get("value"):
            out["short_description"] = desc["value"]
            break

    for notice in (pick("compatibilityNotices") or []):
        if isinstance(notice, dict) and notice.get("type") == "NO_OF_PLAYERS":
            out["players"] = notice.get("value")
            break

    if product.get("platforms"):
        out["platforms"] = product["platforms"]

    # 게임인지 DLC/아이템인지. 무료·신작 카테고리는 목록이 Concept 만 주기 때문에
    # (분류 정보가 아예 없다) 이 단품 조회가 유일한 판별 수단이다.
    # 이게 없으면 '무료 게임' 목록이 무료 DLC(예: 팩 티켓)로 채워진다.
    top = product.get("topCategory")
    if top:
        out["top_category"] = top                     # "GAME" | "ADDON" | ...
        out["content_type"] = "game" if top == "GAME" else "addon"
    klass = product.get("localizedStoreDisplayClassification")
    if klass:
        out["store_classification"] = klass           # "제품판" | "애드온" 등 표시용
    return out
