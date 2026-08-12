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
import json
import re
from html import unescape

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

        # 할인 중이 아니면 정가 = 현재가. 스팀·PS·엑박 파서가 모두 이렇게 하는데
        # 닌텐도만 None 으로 뒀다가 실측 사고가 났다: 가격 API 경로는 정가를 채우고
        # 이 목록 경로는 비워서, 같은 상품에 두 값이 번갈아 저장됐다. 가격이 하나도
        # 안 변했는데 price_hash 가 매번 뒤집혀 **수집할 때마다** 스냅샷이 새로 쌓였다
        # (실측: 74,100원 그대로인 상품에 8건 — regular 가 null/74100/null/74100…).
        # is_on_sale 판정은 위에서 이미 끝났으므로 여기서 채워도 결과가 안 바뀐다.
        if regular_price is None and final_price is not None:
            regular_price = final_price

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


def _find_gallery_data(obj):
    """중첩된 x-magento-init JSON에서 'mage/gallery/gallery'의 data 배열을 찾는다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "mage/gallery/gallery" and isinstance(v, dict) and isinstance(v.get("data"), list):
                return v["data"]
            found = _find_gallery_data(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_gallery_data(v)
            if found:
                return found
    return None


def parse_detail_generation(html: str) -> str | None:
    """상세 페이지의 '대상 본체'(.label_platform) 항목으로 세대를 판별한다.

    구조:
      <div class="product-attribute label_platform ...">
        <div class="attribute-item-val">Nintendo Switch</div>
        <div class="attribute-item-val">Nintendo Switch 2</div>  ← 둘 다면 크로스젠
      </div>
    반환: "both" | "switch1" | "switch2" | None(판별 불가)
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    vals = [el.get_text(" ", strip=True) for el in soup.select(".label_platform .attribute-item-val")]
    vals = [v for v in vals if v]
    if not vals:
        return None
    has2 = any("Switch 2" in v for v in vals)
    has1 = any("Switch" in v and "Switch 2" not in v for v in vals)
    if has1 and has2:
        return "both"
    if has2:
        return "switch2"
    if has1:
        return "switch1"
    return None


def _gallery_urls(gallery_data: list, limit: int) -> list[str]:
    """갤러리 data 배열 → 이미지 URL 목록 (대표 isMain 먼저, 동영상 제외)."""
    mains: list[str] = []
    others: list[str] = []
    for entry in gallery_data:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in (None, "image"):  # 동영상 제외
            continue
        url = entry.get("full") or entry.get("img") or entry.get("thumb")
        if not url:
            continue
        if url.startswith("//"):  # 프로토콜 생략 URL 정규화
            url = "https:" + url
        (mains if entry.get("isMain") else others).append(url)
    ordered: list[str] = []
    for url in mains + others:
        if url not in ordered:
            ordered.append(url)
    return ordered[:limit]


def parse_detail_gallery(html: str, limit: int = 6) -> list[str]:
    """상품 상세 페이지(Magento)에서 갤러리 이미지 URL을 뽑는다.

    Magento 2는 미디어 갤러리를 아래 둘 중 하나로 심는다:
      - <script type="text/x-magento-init"> 안의 JSON
      - 요소의 data-mage-init="..." 속성 (HTML 이스케이프됨)
    둘 다에서 "mage/gallery/gallery": {"data": [{"img","full","thumb","isMain","type"}...]}
    를 찾아 대표(isMain) 먼저, 그다음 스크린샷 순으로 URL을 뽑는다.

    ⚠️ 반드시 '서버 응답 HTML'을 넣어야 한다. 렌더링된 DOM(page.content())은
    requireJS가 이 스크립트를 이미 제거한 뒤라 갤러리 데이터가 없다.
    구조를 못 찾으면 빈 리스트 → 호출측이 기존 썸네일을 유지한다.
    """
    blobs: list[str] = []
    # (a) x-magento-init 스크립트 본문
    for m in re.finditer(
        r'<script[^>]*type=["\']text/x-magento-init["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        blobs.append(m.group(1))
    # (b) data-mage-init 속성값 (HTML 이스케이프될 수 있어 언이스케이프)
    for m in re.finditer(r'data-mage-init=(["\'])(.*?)\1', html, re.S):
        blobs.append(unescape(m.group(2)))

    for blob in blobs:
        if "mage/gallery/gallery" not in blob:
            continue
        try:
            data = json.loads(blob.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        gallery_data = _find_gallery_data(data)
        if gallery_data:
            urls = _gallery_urls(gallery_data, limit)
            if urls:
                return urls
    return []


# ---------------------------------------------------------------------------
# 발매 일정(nintendo.com/kr/schedule) — 신작·발매예정
# ---------------------------------------------------------------------------
# 스토어(store.nintendo.co.kr)는 봇 차단(202)이라 브라우저가 필요하지만, 발매 일정
# 페이지는 일반 HTTP로 받을 수 있고 본문에 상품 JSON이 그대로 박혀 있다.
# 레코드 예:
#   {"releaseDate":"2026-07-02T00:00:00.000Z","title":"Rain World",
#    "nsuid":"70010000075084","hardware":"switch","publisher":"Akupara Games",
#    "link":"https://store.nintendo.co.kr/70010000075084","imageHero":{"url":"..."}}
# nsuid 는 스토어 URL 경로와 같은 값이라 기존 상품 ID 체계와 그대로 맞는다.
_SCHEDULE_ITEM_RE = re.compile(
    r'"releaseDate":"(?P<date>[^"]+)"'
    r'.{0,400}?"title":"(?P<title>[^"]+)"'
    r',"nsuid":"(?P<nsuid>\d+)"'
    r'.{0,1200}?"hardware":"(?P<hardware>[^"]*)"',
    re.S,
)
_IMAGE_ORG_RE = re.compile(r'"imageHeroOrg":\{"url":"([^"]+)"')


def parse_schedule_page(html: str) -> list[ParsedItem]:
    """발매 일정 페이지에서 신작·발매예정 상품을 뽑는다.

    가격 정보는 없는 페이지라 가격은 비워 두고(is_on_sale=False) 출시일·세대만 채운다.
    → 할인 목록에는 섞이지 않고, 웹에서 출시일 기준으로 신작/발매예정을 가른다.
    """
    text = html.replace('\\"', '"')  # RSC 스트림이 따옴표를 이스케이프해 둔다
    items: list[ParsedItem] = []
    seen: set[str] = set()

    for m in _SCHEDULE_ITEM_RE.finditer(text):
        nsuid = m.group("nsuid")
        if nsuid in seen:
            continue
        seen.add(nsuid)

        title = m.group("title").strip()
        if not title:
            continue

        hardware = (m.group("hardware") or "").strip().lower()
        gen = "switch2" if "switch2" in hardware or "2" in hardware else "switch1"

        # 레코드 구간 안에서 대표 이미지 찾기 (없으면 생략)
        seg = text[m.start() : m.end() + 600]
        img = _IMAGE_ORG_RE.search(seg)
        image_url = img.group(1).split("?")[0] if img else None

        extracted = {
            "nsuid": nsuid,
            "release_date": m.group("date"),
            "platform_generation": gen,
            "hardware": hardware,
            "gallery": [image_url] if image_url else [],
        }
        extracted.update(_schedule_extras(seg))

        items.append(
            ParsedItem(
                store_product_id=nsuid,
                title=title,
                store_url=f"https://store.nintendo.co.kr/{nsuid}",
                image_url=image_url,
                is_on_sale=False,
                extracted_data=extracted,
            )
        )
    return items


# 같은 레코드 안에 이미 들어 있는데 그동안 버리고 있던 값들 (추가 요청 0회).
# 닌텐도는 상품 상세를 브라우저로만 열 수 있어(봇 차단) 이 정보를 따로 받으려면
# 상품당 Playwright 로드가 필요하다 — 목록에서 같이 주워 두면 그 비용이 통째로 사라진다.
_SCHED_PUBLISHER_RE = re.compile(r'"publisher":"([^"]+)"')
_SCHED_RATING_RE = re.compile(r'"rating":(?:\{[^{}]*"name":"([^"]+)"|"([^"]+)")')
_SCHED_CATEGORY_RE = re.compile(r'"category":\[([^\]]*)\]')


def _schedule_extras(segment: str) -> dict:
    """일정 레코드 구간에서 퍼블리셔·등급·판매 형태를 추가로 뽑는다."""
    out: dict = {}
    m = _SCHED_PUBLISHER_RE.search(segment)
    if m:
        out["publisher"] = m.group(1)
    # rating 은 현재 모든 항목이 null 이다(실측 115/115). 닌텐도가 채우기 시작하면
    # 그대로 잡히도록 남겨 둔다 — 없는 동안엔 키 자체를 넣지 않는다.
    m = _SCHED_RATING_RE.search(segment)
    if m:
        out["content_rating"] = m.group(1) or m.group(2)
    m = _SCHED_CATEGORY_RE.search(segment)
    if m:
        cats = re.findall(r'"([^"]+)"', m.group(1))
        if cats:
            out["categories"] = cats     # 예: ["다운로드 버전"], ["패키지 버전"]
    return out


# ---------------------------------------------------------------------------
# 공식 가격 API (api.ec.nintendo.com/v1/price) — 시세 갱신용
# ---------------------------------------------------------------------------
# 스토어 HTML 크롤은 봇 차단 때문에 브라우저가 필요해 느리다. 반면 이 엔드포인트는
# 닌텐도가 공개한 가격 조회 API로, NSUID 50개를 한 번에 받고 차단도 없다.
# 무엇보다 **세일 종료일(end_datetime)** 을 주는데, 이건 HTML 목록엔 없는 정보다.
#
# 응답 예:
#   {"country":"KR","prices":[
#     {"title_id":70010000119900,"sales_status":"onsale",
#      "regular_price":{"raw_value":"22000","currency":"KRW"},
#      "discount_price":{"raw_value":"7500","start_datetime":"...","end_datetime":"..."}}]}
def parse_price_api(data: dict) -> dict[str, dict]:
    """가격 API 응답을 {nsuid: {정가/할인가/종료일...}} 로 정리한다."""
    out: dict[str, dict] = {}
    for p in data.get("prices") or []:
        tid = p.get("title_id")
        if tid is None:
            continue
        reg = (p.get("regular_price") or {}).get("raw_value")
        dis = p.get("discount_price") or {}
        dis_raw = dis.get("raw_value")

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        regular = _num(reg)
        final = _num(dis_raw) if dis_raw is not None else regular
        on_sale = (
            regular is not None and final is not None and final < regular
        )
        out[str(tid)] = {
            "regular_price": regular,
            "final_price": final,
            "is_on_sale": on_sale,
            "discount_percent": (
                round((1 - final / regular) * 100, 2)
                if on_sale and regular
                else None
            ),
            "sale_start_at": dis.get("start_datetime"),
            "sale_end_at": dis.get("end_datetime"),
            "sales_status": p.get("sales_status"),
        }
    return out
