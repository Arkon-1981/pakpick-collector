"""parsers/coupang 회귀 테스트.

    python tests/test_coupang_parser.py

여기서 지키려는 것
  쿠팡 상품명은 검색에 걸리려고 호환 기기·부속품 이름을 뒤에 잔뜩 붙인다.
  분류 규칙을 "먼저 걸리는 단어"로 되돌리면 케이스가 죄다 컨트롤러가 된다
  (실제로 첫 수집 119건 중 53건이 컨트롤러로 몰렸다). 아래 이름들은 전부
  그때 실제로 수집된 것이고, 규칙을 건드리면 여기서 먼저 깨져야 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parsers.coupang import (  # noqa: E402
    MAX_PRICE, MIN_PRICE, detect_category, detect_consoles, to_row,
)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  — 기대 {want!r}, 실제 {got!r}"))


# --------------------------------------------------------------------------
# 카테고리 — 전부 실제 수집된 상품명이다
# --------------------------------------------------------------------------
CATEGORY_CASES = [
    # 핵심 명사가 뒤에 온다 (한국어 어순)
    ("호후 탠드 PS5 컨트롤러 듀얼 스탠드 충전기, 혼합색상, 1개, 단일상품", "charge"),
    ("칸탐 PS5 듀얼센스 컨트롤러 보관 케이스, 투명블랙, 1개, 단일상품", "case"),
    ("이이네  PS5 듀얼센스/듀얼엣지 컨트롤러 충전 거치대 미니 충전 베이스", "charge"),
    ("조이트론 무선 컨트롤러 충전 듀얼 2100mAh XSX XBOX 배터리 팩 키트", "charge"),
    # 검색용 나열이 뒤에 붙은 긴 이름 — 앞쪽 창만 보고 판단해야 한다
    ("PSCHUNYD 하드 쉘 휴대용 케이스 PS5 슬림 여행용 가방 플레이스테이션 5 콘솔용 "
     "정리함 보호 듀얼 컨트롤러 헤드셋 및 게임 카드", "case"),
    ("PS5용 휴대용 케이스 플레이스테이션 5 콘솔과 호환되는 하드 쉘 여행용 어깨끈이 "
     "있는 두꺼운 보호 여행 가방 콘솔 컨트롤러 헤드셋", "case"),
    # 있는 그대로 컨트롤러인 것들
    ("엑스박스 무선 컨트롤러", "controller"),
    ("엑스박스 마이크로소프트 4세대 무선 컨트롤러", "controller"),
    ("플레이스테이션 원신 리미티드 에디션 PS5 듀얼센스 무선 컨트롤러", "controller"),
    # 나머지 갈래
    ("터틀비치 콘솔 게이밍 채팅 헤드셋 (Xbox)", "audio"),
    ("대한 닌텐도 스위치 파우치 동물의숲 에디션 하드 케이스 26cm", "case"),
    ("호후 닌텐도 스위치 강화유리 액정보호필름", "case"),
    ("PS5용 판샹 500GB NVMe SSD PCIe 4세대 게이밍 PS5 스토리지 확장", "storage"),
    # "마이크로SD"가 "마이크"에 걸려 오디오가 되던 문제
    ("닌텐도 스위치 마이크로SD 256GB 메모리카드", "storage"),
]

print("— 카테고리")
for name, want in CATEGORY_CASES:
    check(name[:46], detect_category(name), want)

# --------------------------------------------------------------------------
# 기기 판별 — '스위치'는 전등·네트워크에도 쓰는 말이라 게임 맥락이 필요하다
# --------------------------------------------------------------------------
print("\n— 기기 판별")
check("듀얼센스 → PS5", detect_consoles("PS5 듀얼센스 무선 컨트롤러"), ["PS5"])
check("엑박 → Xbox", detect_consoles("엑스박스 시리즈 X 컨트롤러"), ["Xbox"])
check("닌텐도 → Switch", detect_consoles("닌텐도 스위치 케이스"), ["Switch"])
check("스위치+게임맥락 → Switch", detect_consoles("스위치 무선 컨트롤러"), ["Switch"])
check("전등 스위치 → 없음", detect_consoles("벽스위치 3구 조명 스위치"), [])
check("네트워크 스위치 → 없음", detect_consoles("기가비트 네트워크 스위치 허브 8포트"), [])
check("공용 헤드셋 → 여러 개", detect_consoles("게이밍 헤드셋 PS5 엑스박스 겸용"), ["PS5", "Xbox"])

# --------------------------------------------------------------------------
# to_row — 가격 범위와 없는 값 처리
# --------------------------------------------------------------------------
print("\n— to_row")


def product(name: str, price: float, **kw) -> dict:
    base = {"productId": 1, "productName": name, "productPrice": price,
            "productUrl": "https://link.coupang.com/x", "productImage": "https://img/x.jpg"}
    base.update(kw)
    return base


row = to_row(product("PS5 듀얼센스 무선 컨트롤러", 62900, rank=3, isRocket=True))
check("정상 상품이 들어온다", row is not None, True)
assert row is not None
check("rank 를 담는다", row.rank, 3)
check("로켓배송", row.is_rocket, True)
# 쿠팡 검색 응답에는 정가가 없다 → 지어내지 말고 0%
check("정가 없으면 base_price=None", row.base_price, None)
check("정가 없으면 discount=0", row.discount, 0)

check("콘솔 무관은 버린다", to_row(product("벽스위치 3구 조명", 9900)), None)
check(f"{MAX_PRICE:,}원 초과는 버린다",
      to_row(product("PS5용 판샹 500GB NVMe SSD 확장", MAX_PRICE + 1)), None)
check(f"{MIN_PRICE:,}원 미만은 버린다",
      to_row(product("닌텐도 스위치 강화유리 액정보호필름", MIN_PRICE - 1)), None)
check("경계값은 남긴다", to_row(product("닌텐도 스위치 케이스", MAX_PRICE)) is not None, True)

# 정가를 주는 날이 오면 할인율을 제대로 계산해야 한다
row2 = to_row(product("엑스박스 무선 컨트롤러", 54900, productBasePrice=79800))
assert row2 is not None
check("정가가 있으면 할인율 계산", row2.discount, 31)
# 정가가 판매가보다 싸면 잘못된 값이다 → 무시
row3 = to_row(product("엑스박스 무선 컨트롤러", 54900, productBasePrice=40000))
assert row3 is not None
check("정가 < 판매가면 무시", (row3.base_price, row3.discount), (None, 0))

print(f"\n실패 {fails}건" + (" — 전부 통과" if not fails else ""))
raise SystemExit(1 if fails else 0)
