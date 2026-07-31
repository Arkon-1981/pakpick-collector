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
from datetime import datetime, timezone

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


def parse_featured_items(items: list[dict], content_kind: str) -> list[ParsedItem]:
    """featuredcategories API의 항목(JSON)을 ParsedItem으로 변환한다.

    검색 HTML과 달리 신작(new_releases)·출시예정(coming_soon)을 JSON으로 준다.
    가격은 최소 단위(원×100)이며, 출시예정작은 original_price=null / final_price=0 처럼
    **가격이 아직 없는** 경우가 많아 무가격 저장을 허용한다(할인 목록엔 안 잡힘).

    content_kind: "new"(최근 출시) | "upcoming"(출시 예정)
    """
    out: list[ParsedItem] = []
    for it in items:
        try:
            appid = it.get("id")
            name = it.get("name")
            if not appid or not name:
                continue
            appid = str(appid)

            # 가격: 최소 단위(원×100) → 원. 출시예정은 미정(0/null)일 수 있다.
            def _won(v) -> float | None:
                return float(v) / 100.0 if isinstance(v, (int, float)) and v > 0 else None

            original = _won(it.get("original_price"))
            final = _won(it.get("final_price"))
            disc = it.get("discount_percent") or 0
            upcoming = content_kind == "upcoming"
            # 출시예정은 가격 미정이면 그대로 None (0원=무료로 오해되지 않게)
            if final is None and not upcoming and original is not None:
                final = original
            is_on_sale = bool(it.get("discounted")) and disc > 0 and final is not None
            if original is None and final is not None:
                original = final

            image = it.get("header_image") or it.get("large_capsule_image")
            image = image.split("?")[0] if image else _images(appid)[0]

            out.append(
                ParsedItem(
                    store_product_id=appid,
                    title=name,
                    store_url=f"https://store.steampowered.com/app/{appid}/?cc=kr",
                    image_url=image,
                    regular_price=original,
                    sale_price=final if is_on_sale else None,
                    final_price=final,
                    discount_percent=float(disc) if is_on_sale else None,
                    is_on_sale=is_on_sale,
                    extracted_data={
                        "appid": appid,
                        "gallery": [image],
                        "content_kind": content_kind,
                        "price_raw": {"original": original, "final": final, "discount": disc},
                    },
                )
            )
        except Exception:
            logger.exception("스팀 featured 항목 파싱 실패: %s", it.get("id"))
    return out


# ---------------------------------------------------------------------------
# IStoreBrowseService/GetItems — 배치 보강 (출시일·종료일·퍼블리셔·스크린샷·평점)
# ---------------------------------------------------------------------------
# 검색 API(results_html)에는 이름·가격밖에 없어서 스팀만 출시일·할인 종료일이 비어 있었다.
# 이 엔드포인트는 appid 50개를 한 번에 받아 그 모두를 준다(실측: 50개 요청 → 50건 응답,
# 출시일·종료일·스크린샷·리뷰 100% 커버, 285KB / 0.9초).
#
# 부수 효과가 더 크다: 기존 갤러리 보강은 appdetails를 **상품당 1회** 불렀는데
# (150개 = 요청 150회), 여기서 스크린샷까지 같이 오므로 50개 묶음 3회면 끝난다.
STORE_ASSET_BASE = "https://shared.cloudflare.steamstatic.com/store_item_assets/"
ELANG_KOREAN = 4          # Steam ELanguage enum (0=영어, 4=한국어)
SHOT_VARIANT = ".1920x1080.jpg"  # 원본(1MB)이 아닌 표준 리사이즈본(340KB)


def _epoch_iso(ts) -> str | None:
    """유닉스 초 → ISO8601(UTC). 0/None/이상값은 None."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _clan_names(v) -> list[str]:
    """[{"name": "...", "creator_clan_account_id": ...}, ...] → ["..."]"""
    if not isinstance(v, list):
        return []
    return [x["name"] for x in v if isinstance(x, dict) and x.get("name")]


def _shot_urls(item: dict, limit: int) -> list[str]:
    """screenshots.all_ages_screenshots → 절대 URL 목록.

    filename은 "steam/apps/<appid>/ss_<hash>.jpg?t=<캐시버스터>" 형태다.
    ?t= 는 떼어 URL을 안정화하고(값이 바뀔 때마다 버전 diff가 생김),
    확장자 앞에 .1920x1080 을 넣어 원본(1MB) 대신 리사이즈본(340KB)을 쓴다.
    """
    shots = (item.get("screenshots") or {}).get("all_ages_screenshots") or []
    urls: list[str] = []
    for s in sorted(shots, key=lambda x: x.get("ordinal") or 0):
        fn = (s.get("filename") or "").split("?")[0]
        if not fn.endswith(".jpg"):
            continue
        urls.append(STORE_ASSET_BASE + fn[: -len(".jpg")] + SHOT_VARIANT)
        if len(urls) >= limit:
            break
    return urls


# GetItems 의 store_items[].type — 4 는 DLC (0 은 게임)
STORE_TYPE_DLC = 4
# 스팀 상품 이미지 CDN. assets.asset_url_format 이 이 뒤에 붙는 상대 경로를 준다.
ASSET_BASE = "https://shared.akamai.steamstatic.com/store_item_assets/"


def _asset_url(it: dict, key: str) -> str | None:
    """GetItems assets 에서 이미지 절대 주소를 만든다.

    asset_url_format = "steam/apps/1091500/${FILENAME}?t=1784714077"
    assets[key]      = "26b96.../header_koreana.jpg"
    """
    assets = it.get("assets") or {}
    fmt = assets.get("asset_url_format")
    name = assets.get(key)
    if not fmt or not name:
        return None
    return ASSET_BASE + fmt.replace("${FILENAME}", name)


def parse_store_items(data: dict, *, gallery_limit: int = 6) -> dict[str, dict]:
    """GetItems 응답을 {appid: {보강 필드...}} 로 정리한다.

    값이 없는 필드는 아예 넣지 않는다 → 호출부에서 기존 값을 덮어쓰지 않게.
    """
    out: dict[str, dict] = {}
    for it in (data.get("response") or {}).get("store_items") or []:
        appid = it.get("appid")
        if not appid or not it.get("success"):
            continue

        info: dict = {}

        release = _epoch_iso((it.get("release") or {}).get("steam_release_date"))
        if release:
            info["release_date"] = release

        # 할인 종료일: best_purchase_option.active_discounts[0].discount_end_date
        bpo = it.get("best_purchase_option") or {}
        discounts = bpo.get("active_discounts") or []
        end = _epoch_iso(discounts[0].get("discount_end_date")) if discounts else None
        if end:
            info["sale_end_at"] = end

        basic = it.get("basic_info") or {}
        pubs = _clan_names(basic.get("publishers"))
        devs = _clan_names(basic.get("developers"))
        if pubs:
            info["publishers"] = pubs
        if devs:
            info["developers"] = devs
        if basic.get("short_description"):
            info["short_description"] = basic["short_description"]

        shots = _shot_urls(it, gallery_limit)
        if shots:
            info["screenshots"] = shots

        # 리뷰 요약: 평점 필터/정렬에 쓸 수 있는 유일한 무료 신호
        summary = (it.get("reviews") or {}).get("summary_filtered") or {}
        if summary.get("review_count"):
            info["review"] = {
                "count": summary.get("review_count"),
                "percent_positive": summary.get("percent_positive"),
                "score": summary.get("review_score"),
                "label": summary.get("review_score_label"),
            }

        langs = it.get("supported_languages")
        if isinstance(langs, list) and langs:
            info["korean"] = any(
                l.get("elanguage") == ELANG_KOREAN and l.get("supported") for l in langs
            )

        if it.get("is_free"):
            info["is_f2p"] = True
        if it.get("name"):
            info["title"] = it["name"]

        # 상품 종류: 4 = DLC. 게임 목록에 DLC가 섞이면 "무료 게임"에 팩 티켓 같은 게 뜬다.
        if it.get("type") == STORE_TYPE_DLC:
            info["content_type"] = "addon"

        # 대표 이미지: 스팀이 알려주는 정확한 경로를 쓴다.
        # appid로 header.jpg 주소를 조립하면 헤더가 없는 상품(주로 DLC·신규 등록)에서
        # 404가 나 이미지가 통째로 깨진다. asset_url_format 은 해시와 ?t= 버전까지
        # 들어 있어 항상 유효하고, 이미지가 바뀌면 주소도 바뀐다.
        img = _asset_url(it, "header")
        if img:
            info["image_url"] = img

        out[str(appid)] = info
    return out
