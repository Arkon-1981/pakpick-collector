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


def _images_from_media(media, apollo: dict) -> tuple[str | None, list[str]]:
    """media 배열에서 (대표 이미지, 갤러리)을 뽑는다.

    갤러리 = [대표 키아트 1장] + [게임 스크린샷들].
    (예전엔 키아트 여러 버전을 다 넣어 '같은 그림 다른 크기'가 반복됐음 → 스샷 위주로 교체)
    """
    if not isinstance(media, list):
        return None, []
    arts: list[tuple[int, str]] = []   # 대표 후보 (키아트)
    shots: list[str] = []              # 게임 스크린샷
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
        elif role in _PS_ART_PRIORITY:
            arts.append((_PS_ART_PRIORITY[role], url))
        # LOGO(투명 로고)·BACKGROUND·PORTRAIT_BANNER 등은 대표/스샷 어느 쪽도 아님 → 제외

    arts.sort(key=lambda x: x[0])
    representative = arts[0][1] if arts else (shots[0] if shots else None)

    gallery: list[str] = []
    if representative:
        gallery.append(representative)
    for url in shots:
        if url not in gallery:
            gallery.append(url)
    gallery = gallery[:6]  # 대표 1 + 스샷 최대 5
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
