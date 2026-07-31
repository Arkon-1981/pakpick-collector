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
# 카테고리 판별 — 먼저 걸리는 것이 이긴다 (위쪽이 더 구체적)
# --------------------------------------------------------------------------
# 주의: 저장장치를 오디오보다 먼저 본다. "마이크로SD" 가 "마이크"에 걸려
# 헤드셋으로 분류되던 문제가 있었다. 부분 문자열로 맞추는 방식의 함정이다.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("controller", ("컨트롤러", "패드", "조이콘", "joy-con", "joycon", "프로콘", "듀얼센스",
                    "듀얼쇼크", "게임패드", "아케이드", "조이스틱", "핸들")),
    ("storage",    ("ssd", "microsd", "마이크로sd", "마이크로 sd", "메모리카드", "메모리 카드",
                    "저장", "외장하드", "tf카드", "sd카드", "sd 카드")),
    ("audio",      ("헤드셋", "헤드폰", "이어폰", "스피커", "사운드", "마이크")),
    ("charge",     ("충전", "충전기", "충전독", "거치대", "크래들", "도크", "독 ", "케이블",
                    "어댑터", "배터리", "보조배터리")),
    ("case",       ("케이스", "파우치", "가방", "커버", "보호", "스킨", "필름", "수납")),
]

# "마이크"로 오디오라고 판단하기 전에 걸러야 하는 말들
NOT_MIC = ("마이크로sd", "마이크로 sd", "마이크로파", "마이크로usb", "마이크로 usb")


def detect_category(name: str) -> str:
    n = _norm(name)
    for cat, words in CATEGORY_RULES:
        for w in words:
            if w not in n:
                continue
            if w == "마이크" and any(x in n for x in NOT_MIC):
                continue  # 마이크로SD 같은 말
            return cat
    return "etc"


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
    # 어떤 검색어로 찾았는지 (문제 추적용, 저장하지는 않는다)
    via: str = field(default="", compare=False)


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
    if price is None or price <= 0:
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
        via=via,
    )
