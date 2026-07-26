"""스팀 스토어 파서.

스팀 검색 API(`/search/results/`)가 돌려주는 `results_html`(할인 상품 목록 HTML)에서
상품을 추출한다. 목록 HTML 한 줄(search_result_row)에 상품명·정가·할인가·할인율이
모두 들어 있어 상품별 상세 요청 없이 목록만으로 수집이 끝난다.

한 행(row) 구조 (핵심 부분):
  <a class="search_result_row" data-ds-appid="2059670" ...>
    <span class="title">Moss II VR</span>
    <div class="discount_block" data-discount="50" data-price-final="1075000" ...>
      <div class="discount_pct">-50%</div>
      <div class="discount_original_price">₩ 21,500</div>
      <div class="discount_final_price">₩ 10,750</div>
    </div>
  </a>

⚠️ 번들/패키지 행(data-ds-appid 없음)은 건너뛴다.
   구조가 바뀌어도 원본 JSON은 항상 저장되므로 파서 수정 후 재처리 가능.
"""
import re

from bs4 import BeautifulSoup

from collectors.base import ParsedItem
from common.logging_util import get_logger

logger = get_logger(__name__)

PRICE_NUM_RE = re.compile(r"[\d,]+")

# 상품 대표 이미지(헤더 460x215)는 appid로 규칙이 정해져 있어 상세 요청 없이 구성 가능
CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"


def _parse_krw(text: str | None) -> float | None:
    """"₩ 21,500" → 21500.0 / "무료" → 0"""
    if not text:
        return None
    if "무료" in text or "free" in text.lower():
        return 0.0
    m = PRICE_NUM_RE.search(text)
    return float(m.group(0).replace(",", "")) if m else None


def _images(appid: str) -> tuple[str, list[str]]:
    """appid로 대표 이미지(헤더)를 구성한다. (스크린샷 갤러리는 상세 API 필요 → 추후)"""
    header = f"{CDN}/{appid}/header.jpg"
    return header, [header]


def _item_from_row(a) -> ParsedItem | None:
    """검색 결과 행(<a>) 1개를 ParsedItem으로 변환한다."""
    appid = a.get("data-ds-appid")
    # 번들/패키지 등 단일 appid가 아닌 행은 제외
    if not appid or "," in appid:
        return None

    title_el = a.select_one(".title")
    title = title_el.get_text(strip=True) if title_el else None

    block = a.select_one(".discount_block")
    original = final = None
    discount_percent = None
    if block is not None:
        d = block.get("data-discount")
        if d and d.isdigit():
            discount_percent = float(d)
        op = block.select_one(".discount_original_price")
        fp = block.select_one(".discount_final_price")
        original = _parse_krw(op.get_text()) if op else None
        final = _parse_krw(fp.get_text()) if fp else None
        if final is None:
            # 할인 표기가 없을 때: data-price-final(최소 단위, 원×100) 폴백
            dpf = block.get("data-price-final")
            if dpf and dpf.isdigit():
                final = float(dpf) / 100.0

    if final is None:
        return None  # 가격을 구하지 못하면 저장하지 않음 (specials면 보통 존재)
    if original is None:
        original = final

    is_on_sale = original > final
    if discount_percent is None and is_on_sale:
        discount_percent = round((1 - final / original) * 100)

    image_url, gallery = _images(appid)
    store_url = f"https://store.steampowered.com/app/{appid}/?cc=kr"

    return ParsedItem(
        store_product_id=str(appid),
        title=title,
        store_url=store_url,
        image_url=image_url,
        regular_price=original,
        sale_price=final if is_on_sale else None,
        final_price=final,
        discount_percent=discount_percent if is_on_sale else None,
        is_on_sale=is_on_sale,
        extracted_data={
            "appid": appid,
            "gallery": gallery,
            "price_raw": {"original": original, "final": final, "discount": discount_percent},
        },
    )


def parse_search_results_html(html: str) -> list[ParsedItem]:
    """검색 결과 HTML에서 할인 상품 목록을 뽑는다."""
    soup = BeautifulSoup(html, "lxml")
    items: list[ParsedItem] = []
    for a in soup.select("a.search_result_row"):
        try:
            item = _item_from_row(a)
            if item:
                items.append(item)
        except Exception:
            logger.exception("스팀 상품 행 파싱 실패: %s", a.get("data-ds-appid"))
    return items


def count_rows(html: str) -> int:
    """페이지의 행 개수 (번들 포함). 페이지네이션 start 증가에 사용."""
    return html.count('class="search_result_row')


def parse_screenshots(data: dict, appid: str, limit: int = 5) -> list[str]:
    """appdetails(filters=screenshots) 응답에서 스크린샷 원본 URL을 뽑는다.

    응답 구조: { "<appid>": { "success": true,
                 "data": { "screenshots": [ {"path_full": "...ss_....1920x1080.jpg?t=..."} ] } } }
    """
    app = data.get(str(appid)) or {}
    if not app.get("success"):
        return []
    shots = (app.get("data") or {}).get("screenshots") or []
    urls: list[str] = []
    for s in shots:
        u = s.get("path_full")
        if u:
            urls.append(u.split("?")[0])  # 캐시버스터(?t=) 제거해 URL 안정화
        if len(urls) >= limit:
            break
    return urls
