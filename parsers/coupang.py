"""쿠팡 검색 결과 → 주변기기 항목.

여기서 제일 중요한 일은 **콘솔과 상관없는 물건을 걸러내는 것**이다.
쿠팡 검색은 키워드가 하나만 걸려도 잡아 오기 때문에, "닌텐도 스위치 케이스"로
찾아도 안경 케이스나 전등 스위치가 섞여 들어온다.

그래서 두 겹으로 거른다.
  1) 검색어 자체를 콘솔 특화로 만든다 (collect_gear.py 의 KEYWORDS)
  2) 돌아온 상품명에 **확실한 콘솔 단어**가 있는지 다시 확인한다

"스위치"는 전등·네트워크 장비에도 쓰이는 말이라 그것만으로는 통과시키지 않는다.
게임 맥락 단어가 함께 있어야 인정한다.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 콘솔 판별
# --------------------------------------------------------------------------

# 이 단어가 있으면 그 기기용으로 확정한다
STRONG: dict[str, tuple[str, ...]] = {
    "PS5": ("ps5", "ps4", "플레이스테이션", "플스", "듀얼센스", "dualsense", "듀얼쇼크", "dualshock"),
    "Xbox": ("xbox", "엑스박스", "엑박"),
    "Switch": ("닌텐도", "nintendo", "조이콘", "joy-con", "joycon", "프로콘", "스위치2", "switch2",
               "스위치 2", "switch 2", "oled 스위치", "스위치 oled", "스위치 라이트"),
    "PC": ("스팀덱", "스팀 덱", "steam deck", "steamdeck"),
}

# 이것만으로는 부족한 단어 — 아래 게임 맥락 단어가 함께 있어야 인정한다
WEAK_SWITCH = ("스위치", "switch")
GAME_CONTEXT = (
    "게임", "게이밍", "콘솔", "독", "그립", "조이", "카트리지", "게임칩", "게임카드",
    "휴대용", "거치", "충전", "컨트롤러", "패드", "본체", "커버", "파우치", "케이스",
)

# 이 단어가 있으면 콘솔 물건이 아니다 (주로 '스위치' 오검출)
DENY = (
    "전등", "조명", "콘센트", "벽스위치", "벽 스위치", "누름", "토글스위치", "로커스위치",
    "네트워크", "스위치허브", "허브 스위치", "랜스위치", "poe", "리미트", "압력스위치",
    "자동차", "차량", "오토바이", "보일러", "정수기", "냉장고", "세탁기",
    "아이폰", "갤럭시", "에어팟", "버즈", "태블릿거치", "자전거",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def detect_consoles(name: str) -> list[str]:
    """상품명에서 어느 기기용인지 찾는다. 하나도 없으면 빈 목록(=제외 대상)."""
    n = _norm(name)
    if any(d in n for d in DENY):
        return []

    found: list[str] = []
    for console, words in STRONG.items():
        if any(w in n for w in words):
            found.append(console)

    # '스위치'만 있는 경우 — 게임 맥락이 함께 있어야 닌텐도로 본다
    if "Switch" not in found and any(w in n for w in WEAK_SWITCH):
        if any(c in n for c in GAME_CONTEXT):
            found.append("Switch")

    return found


# --------------------------------------------------------------------------
# 카테고리 판별
# --------------------------------------------------------------------------
# 쿠팡 상품명은 검색에 걸리려고 호환 기기·부속품 이름을 뒤에 잔뜩 붙인다.
#   "PS5용 휴대용 케이스 … 여행 가방 콘솔 컨트롤러 헤드셋 및 …"
# 그래서 "먼저 걸리는 단어"로 정하면 케이스가 죄다 컨트롤러가 된다
# (실제로 119건 중 53건이 컨트롤러로 몰렸다).
#
# 대신 앞 HEAD_WINDOW 글자 안에서 **가장 뒤**에 나온 단어를 고른다.
# 한국어 상품명은 "수식어 + 핵심명사" 순서라 핵심이 뒤에 오고, 검색용 나열은
# 그보다 더 뒤에 붙기 때문이다.
#     "PS5 듀얼센스 컨트롤러 보관 케이스"   → 케이스
#     "호후 PS5 컨트롤러 듀얼 스탠드 충전기" → 충전기
#
# 쉼표 앞만 보는 규칙도 써 봤지만 뺐다. 옵션 구분자인 줄 알았던 쉼표가
# 상품명 안에도 들어간다 — "닌텐도 스위치 1, 스위치 2 프로콘 호환 …" 은
# 쉼표에서 자르면 "닌텐도 스위치 1" 만 남아 분류할 단어가 사라진다.
# 창을 48로 두면 "…, 블랙, 1개, 단일상품" 같은 옵션 꼬리는 대개 창 밖이다.
#
# 실제 수집 159건으로 맞춘 값이다(창 40→48 로 미분류 14건→9건).
# 바꾸기 전에 tests/test_coupang_parser.py 를 먼저 볼 것.
HEAD_WINDOW = 48

# 주의: 저장장치를 오디오보다 먼저 둔다. "마이크로SD" 가 "마이크"에 걸려
# 헤드셋으로 분류되던 문제가 있었다. 부분 문자열로 맞추는 방식의 함정이다.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("controller", ("컨트롤러", "패드", "조이콘", "joy-con", "joycon", "프로콘", "듀얼센스",
                    "듀얼쇼크", "게임패드", "아케이드", "조이스틱", "핸들", "레이싱휠",
                    "쉬프터", "기어박스")),
    ("storage",    ("ssd", "microsd", "마이크로sd", "마이크로 sd", "메모리카드", "메모리 카드",
                    "저장", "외장하드", "tf카드", "sd카드", "sd 카드",
                    # Xbox 확장 카드·외장 드라이브류가 통째로 '기타'로 빠지고 있었다
                    "스토리지", "확장 카드", "확장카드", "드라이브", "hdd", "nvme",
                    "cfexpress", "m.2", "m2 ")),
    ("audio",      ("헤드셋", "헤드폰", "이어폰", "스피커", "사운드", "마이크")),
    ("charge",     ("충전", "충전기", "충전독", "차저", "거치대", "크래들", "도크", "독 ",
                    "tv독", "미니독", "케이블", "어댑터", "배터리", "보조배터리")),
    ("case",       ("케이스", "파우치", "가방", "백팩", "커버", "보호", "스킨", "필름",
                    "수납", "보관")),
]

# "마이크"로 오디오라고 판단하기 전에 걸러야 하는 말들
NOT_MIC = ("마이크로sd", "마이크로 sd", "마이크로파", "마이크로usb", "마이크로 usb")


def detect_category(name: str) -> str:
    # '+' 뒤는 끼워 주는 물건이다. "메모리카드 + SD카드 케이스" 는 메모리카드지
    # 케이스가 아니다 — 안 자르면 뒤에 붙은 사은품이 분류를 가져간다.
    head = _norm(name).split("+")[0][:HEAD_WINDOW]
    best, best_pos = "etc", -1
    for cat, words in CATEGORY_RULES:
        for w in words:
            pos = head.rfind(w)
            if pos < 0:
                continue
            if w == "마이크" and any(x in head for x in NOT_MIC):
                continue  # 마이크로SD 같은 말
            if pos > best_pos:
                best, best_pos = cat, pos
    return best


# --------------------------------------------------------------------------
# 상품 변환
# --------------------------------------------------------------------------

@dataclass
class GearRow:
    shop: str
    shop_product_id: str
    name: str
    category: str
    consoles: list[str]
    price: float
    base_price: float | None
    discount: int
    image_url: str | None
    product_url: str
    is_rocket: bool = False
    is_free_ship: bool = False
    rating: float | None = None
    review_count: int | None = None
    # 쿠팡 검색 순위 (1이 제일 위). 정가를 안 주므로 기본 정렬은 이걸로 한다.
    rank: int | None = None
    # deeplink 변환에 쓸 '깨끗한' 상품 주소. 저장하지는 않는다.
    canonical: str | None = field(default=None, compare=False)
    # 오늘 골드박스(쿠팡 특가)에 올라온 상품인가
    is_goldbox: bool = field(default=False, compare=False)
    # 어떤 검색어로 찾았는지 (문제 추적용, 저장하지는 않는다)
    via: str = field(default="", compare=False)


# 값이 말이 되는 범위. 해외 배송 리스팅 중에 500GB SSD 를 137만원에 걸어 둔 것
# 같은 게 섞여 들어온다 — 할인 정보 사이트에 그런 값이 뜨면 신뢰를 잃는다.
# 반대로 몇백원짜리는 액정필름 1장 같은 미끼라 목록만 지저분해진다.
MIN_PRICE = 2_000
MAX_PRICE = 500_000


def canonical_url(p: dict) -> str | None:
    """검색 응답 → 평범한 쿠팡 상품 주소.

    검색이 주는 productUrl 은 link.coupang.com/re/AFFSDP?…&requestid=… 형태인데
    requestid·traceid 는 그 응답 한 번에 딸린 값이라 며칠 뒤엔 안 통한다.
    deeplink API 에 넣어 오래 가는 링크로 바꾸려면 '깨끗한' 주소가 필요하다.

    옵션(색상·용량)까지 맞추려면 itemId·vendorItemId 가 있어야 하는데 검색 응답
    본문에는 없고 productUrl 쿼리에만 들어 있다 — 거기서 꺼낸다.
    """
    pid = p.get("productId")
    if not pid:
        return None
    url = f"https://www.coupang.com/vp/products/{pid}"
    q = urllib.parse.parse_qs(urllib.parse.urlparse(p.get("productUrl") or "").query)
    extra = {k: q[k][0] for k in ("itemId", "vendorItemId") if q.get(k)}
    return url + ("?" + urllib.parse.urlencode(extra) if extra else "")


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_row(p: dict, *, via: str = "") -> GearRow | None:
    """쿠팡 상품 dict → GearRow. 콘솔용이 아니면 None."""
    name = p.get("productName") or ""
    pid = p.get("productId")
    url = p.get("productUrl")
    if not name or not pid or not url:
        return None

    consoles = detect_consoles(name)
    if not consoles:
        return None  # 콘솔과 무관 → 버린다

    price = _num(p.get("productPrice"))
    if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
        return None

    # 쿠팡 검색 응답에는 정가가 없는 경우가 많다. 없으면 할인율 0으로 둔다 —
    # 모르는 값을 지어내면 "할인율순"이 통째로 거짓말이 된다.
    base = _num(p.get("productBasePrice")) or _num(p.get("basePrice"))
    if base is not None and base <= price:
        base = None
    discount = int(round((1 - price / base) * 100)) if base else 0

    return GearRow(
        shop="coupang",
        shop_product_id=str(pid),
        name=name.strip(),
        category=detect_category(name),
        consoles=consoles,
        price=price,
        base_price=base,
        discount=discount,
        image_url=p.get("productImage"),
        product_url=url,
        is_rocket=bool(p.get("isRocket")),
        is_free_ship=bool(p.get("isFreeShipping")),
        rating=_num(p.get("rating")),
        review_count=int(p["reviewCount"]) if str(p.get("reviewCount", "")).isdigit() else None,
        rank=int(p["rank"]) if str(p.get("rank", "")).isdigit() else None,
        canonical=canonical_url(p),
        via=via,
    )
