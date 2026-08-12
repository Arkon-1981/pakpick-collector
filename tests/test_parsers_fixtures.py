"""파서 픽스처 회귀 테스트 — 스토어 마크업/스키마 변경을 '배포 전에' 잡는다.

왜 필요한가:
  파서는 스토어가 주는 HTML/JSON 모양에 통째로 의존한다. 스토어가 구조를 바꾸면
  파싱이 0건이 되거나 필드가 빈 채로 저장되는데, 지금까지는 **실제 수집을 돌려야만**
  알 수 있었다(실측 사고: 무료 표시 50개 전멸, SW2 세대 오기록, 갤러리 유실).
  여기서는 실제 스토어 응답을 고정해 두고 파서를 돌려, 같은 입력에 같은 결과가
  나오는지 검사한다. 마크업이 바뀌어 파서를 고칠 때 이 테스트가 먼저 깨진다.

구조:
  플랫폼별로 `_xxx_failures(items) -> 실패한 검사 이름 목록` 을 두고
  1) 정상 픽스처로 돌려 실패가 0건인지 확인하고(회귀 테스트),
  2) 픽스처의 선택자/키를 일부러 망가뜨려 돌려 **실패가 잡히는지** 확인한다
     (변이 테스트 — "이 테스트가 실제로 뭔가를 지키고 있는가"를 지킨다).
  2)가 없으면 통과만 하고 아무것도 못 잡는 테스트가 되기 쉽다. 실제로 처음
  작성했을 때 그런 상태였고, 이 자기검사로 확인했다.

픽스처 갱신(스토어가 실제로 바뀌어 파서를 고쳤을 때):
  실제 응답을 다시 받아 tests/fixtures/ 에 덮어쓰고, 아래 기대값을 새로 맞춘다.
  픽스처는 '그때의 진짜 응답'이라 값이 오늘 스토어와 달라도 정상이다.

실행: python tests/test_parsers_fixtures.py   (pytest 불필요 — 기존 테스트와 같은 스타일)
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).resolve().parent / "fixtures"

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def _report(failures: list[str]) -> None:
    """_xxx_failures() 결과를 개별 PASS/FAIL 로 출력한다."""
    for name, ok, detail in failures:
        check(name, ok, detail)


def _prices_sane(items, *, allow_zero: bool) -> bool:
    """가격이 '숫자이고 음수가 아니며 현재가 <= 정가' 인지. 0 허용 여부는 플랫폼별."""
    for i in items:
        for v in (i.regular_price, i.final_price):
            if v is None:
                continue
            if not isinstance(v, (int, float)) or v < 0:
                return False
            if not allow_zero and v == 0:
                return False
        if i.regular_price and i.final_price and i.final_price > i.regular_price:
            return False
    return True


def _priced_ratio(items) -> float:
    """final_price 가 채워진 비율. 가격 키가 사라지면 여기서 0 이 된다."""
    if not items:
        return 0.0
    return sum(1 for i in items if i.final_price is not None) / len(items)


# ------------------------------------------------------------------ steam
def _steam_failures(html: str) -> list[tuple[str, bool, str]]:
    """검색 API 의 results_html 에 대한 검사 목록 — 목록 수집의 핵심 경로."""
    from parsers.steam import count_rows, parse_search_results_html

    items = parse_search_results_html(html)
    first = items[0] if items else None
    return [
        ("steam: 상품이 파싱된다", len(items) >= 20, f"{len(items)}개"),
        ("steam: count_rows 가 파싱 개수와 일치", count_rows(html) == len(items),
         f"{count_rows(html)} vs {len(items)}"),
        ("steam: 모든 상품에 appid", bool(items) and all(i.store_product_id.isdigit() for i in items), ""),
        ("steam: 모든 상품에 제목", bool(items) and all(i.title for i in items), ""),
        ("steam: 모든 상품에 이미지", bool(items) and all(i.image_url for i in items), ""),
        ("steam: 가격이 정상 범위", _prices_sane(items, allow_zero=True), ""),
        # 이 픽스처(할인 검색)는 전부 할인 중이어야 하고 할인율이 계산돼 있어야 한다
        ("steam: 할인 상품으로 인식", bool(items) and all(i.is_on_sale for i in items), ""),
        ("steam: 할인율이 채워짐", bool(items) and all(i.discount_percent for i in items), ""),
        # 고정 회귀값 — 파서가 이 픽스처에서 뽑던 값 (마크업 변경 시 여기서 깨진다)
        ("steam: 첫 상품 appid 고정값", bool(first) and first.store_product_id == "1478500",
         first.store_product_id if first else "없음"),
        ("steam: 첫 상품 제목 고정값", bool(first) and first.title == "Big Walk",
         str(first.title) if first else "없음"),
        ("steam: 첫 상품 가격 고정값",
         bool(first) and (first.regular_price, first.final_price, first.discount_percent)
         == (21800.0, 16350.0, 25.0),
         f"{first.regular_price}/{first.final_price}/{first.discount_percent}" if first else "없음"),
    ]


def test_steam_search() -> None:
    _report(_steam_failures((FIX / "steam_search.html").read_text()))


def test_steam_price_fallback() -> None:
    """할인가 '요소'가 사라져도 data-price-final 폴백이 같은 값을 내는지.

    파서에 의도적으로 넣어 둔 폴백(parsers/steam.py 의 data-price-final 분기)이라
    이 변이만은 결과가 바뀌지 않는 게 정상이다. 변이 테스트에서 '못 잡는다'가
    아니라 '안 잡혀야 맞다'인 유일한 경우여서 여기서 따로 고정한다.
    """
    from parsers.steam import parse_search_results_html

    html = (FIX / "steam_search.html").read_text()
    normal = parse_search_results_html(html)
    fallback = parse_search_results_html(html.replace("discount_final_price", "zzz_gone"))
    check("steam: 할인가 요소가 없어도 data-price-final 로 같은 가격",
          [(i.store_product_id, i.final_price) for i in normal]
          == [(i.store_product_id, i.final_price) for i in fallback])


# ------------------------------------------------------------------ xbox
def _xbox_failures(raw: str) -> list[tuple[str, bool, str]]:
    """displaycatalog JSON — 엑박은 이 응답 하나에서 가격·이미지·메타를 다 뽑는다."""
    from parsers.xbox import parse_catalog_products

    items = parse_catalog_products(json.loads(raw))
    by_id = {i.store_product_id: i for i in items}
    gta = by_id.get("9NQ0MVSLV0G7")
    return [
        ("xbox: 상품이 파싱된다", len(items) >= 5, f"{len(items)}개"),
        ("xbox: 모든 상품에 ProductId", bool(items) and all(i.store_product_id for i in items), ""),
        ("xbox: 모든 상품에 제목", bool(items) and all(i.title for i in items), ""),
        ("xbox: 모든 상품에 대표 이미지", bool(items) and all(i.image_url for i in items), ""),
        ("xbox: 가격이 정상 범위", _prices_sane(items, allow_zero=True), ""),
        # 가격 키가 통째로 바뀌면 '전부 무가격'이 되는데, 개별 상품 검사만으론 안 잡힌다
        ("xbox: 대부분 가격이 있다", _priced_ratio(items) >= 0.5, f"{_priced_ratio(items):.0%}"),
        # 갤러리는 '대표 1 + 스샷' 최대 6장 — 이게 0이 되면 상세 화면 캐러셀이 빈다
        ("xbox: 갤러리가 채워짐",
         bool(items) and all(len(i.extracted_data.get("gallery") or []) >= 1 for i in items), ""),
        # 다이어트 확인: product 원본을 저장하지 않는다(행당 47KB 로 DB 를 먹던 값)
        ("xbox: product 원본을 싣지 않는다",
         bool(items) and all("product" not in i.extracted_data for i in items), ""),
        ("xbox: 알려진 상품이 있다(GTA V)", gta is not None, ""),
        ("xbox: GTA V 정가 고정값", bool(gta) and gta.regular_price == 62000.0,
         str(gta.regular_price) if gta else "없음"),
        ("xbox: GTA V 갤러리 6장",
         bool(gta) and len(gta.extracted_data.get("gallery") or []) == 6,
         str(len(gta.extracted_data.get("gallery") or [])) if gta else "없음"),
    ]


def test_xbox_catalog() -> None:
    _report(_xbox_failures((FIX / "xbox_catalog.json").read_text()))


# ------------------------------------------------------------------ playstation
def _ps_failures(raw: str) -> list[tuple[str, bool, str]]:
    """__NEXT_DATA__ JSON — PS 는 페이지 HTML 안의 이 JSON 에서 상품을 읽는다."""
    from parsers.playstation import parse_products_from_next_data

    items = parse_products_from_next_data(json.loads(raw))
    spider = next((i for i in items if "MARVELSPIDERMAN2" in i.store_product_id), None)
    return [
        ("PS: 상품이 파싱된다", len(items) >= 5, f"{len(items)}개"),
        ("PS: 모든 상품에 product id", bool(items) and all(i.store_product_id for i in items), ""),
        ("PS: 모든 상품에 제목", bool(items) and all(i.title for i in items), ""),
        ("PS: 가격이 정상 범위", _prices_sane(items, allow_zero=True), ""),
        ("PS: 대부분 가격이 있다", _priced_ratio(items) >= 0.5, f"{_priced_ratio(items):.0%}"),
        # PS 는 원본 노드를 통째로 보관한다 — 웹의 플랫폼 표시·구독 배지가 여기서 나온다
        ("PS: 원본 노드를 싣는다", bool(items) and all(i.extracted_data.get("node") for i in items), ""),
        ("PS: 알려진 상품이 있다(스파이더맨 2)", spider is not None, ""),
        ("PS: 스파이더맨 2 가격 고정값",
         bool(spider) and (spider.final_price, spider.discount_percent) == (33516.0, 57.0),
         f"{spider.final_price}/{spider.discount_percent}" if spider else "없음"),
    ]


def test_ps_next_data() -> None:
    _report(_ps_failures((FIX / "ps_next_data.json").read_text()))


# ------------------------------------------------------------------ 변이 테스트
# "픽스처의 이 부분이 바뀌면 위 검사가 반드시 깨져야 한다" 를 고정한다.
# (name, 원본 조각 → 바꿀 조각 목록)
_MUTATIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "steam_search.html": ("steam", [
        ("행 선택자", [("search_result_row", "zzz_row")]),
        ("appid 속성", [("data-ds-appid", "data-ds-zzz")]),
        ("제목 클래스", [('class="title"', 'class="zzz"')]),
        ("가격 블록", [("discount_block", "zzz_block")]),
        ("정가 클래스", [("discount_original_price", "zzz_orig")]),
        # 할인가는 폴백이 있어 둘 다 없어져야 깨진다 (test_steam_price_fallback 참고)
        ("할인가(요소+속성 모두)", [("discount_final_price", "zzz_fin"),
                                    ("data-price-final", "data-zzz")]),
    ]),
    "xbox_catalog.json": ("xbox", [
        ("Products 배열", [('"Products"', '"zzzProducts"')]),
        ("ProductId 키", [('"ProductId"', '"zzzId"')]),
        ("ProductTitle 키", [('"ProductTitle"', '"zzzTitle"')]),
        ("ListPrice 키", [('"ListPrice"', '"zzzPrice"')]),
        ("MSRP 키", [('"MSRP"', '"zzzMSRP"')]),
        ("ImagePurpose 키", [('"ImagePurpose"', '"zzzPurpose"')]),
        ("Purchase 판정(Actions)", [('"Actions"', '"zzzActions"')]),
    ]),
    "ps_next_data.json": ("ps", [
        ("price 노드", [('"price"', '"zzzprice"')]),
        ("basePrice 키", [('"basePrice"', '"zzzBase"')]),
        ("discountedPrice 키", [('"discountedPrice"', '"zzzDisc"')]),
        ("name 키", [('"name"', '"zzzname"')]),
    ]),
}

_FAILURE_FN = {"steam": _steam_failures, "xbox": _xbox_failures, "ps": _ps_failures}


def test_mutations_are_detected() -> None:
    """픽스처를 망가뜨리면 위 검사들이 실제로 실패하는가 (테스트의 테스트).

    통과만 하고 아무것도 못 잡는 테스트를 막는다. 여기서 '변이인데 통과'가
    나오면 그 선택자/키는 사실상 검사되고 있지 않다는 뜻이다.
    """
    for fixture, (platform, mutations) in _MUTATIONS.items():
        raw = (FIX / fixture).read_text()
        fn = _FAILURE_FN[platform]
        for label, subs in mutations:
            broken = raw
            for old, new in subs:
                assert old in broken, f"{fixture}: 변이 대상 '{old}' 가 픽스처에 없다"
                broken = broken.replace(old, new)
            try:
                caught = [n for n, ok, _ in fn(broken) if not ok]
            except Exception as exc:            # 파서가 아예 터져도 '잡았다'로 본다
                caught = [f"예외: {exc.__class__.__name__}"]
            check(f"변이 감지({platform}): {label}", bool(caught),
                  "변이했는데 전부 통과 — 이 부분은 검사되지 않는다")


# ------------------------------------------------------------------ 픽스처 자체
FIXTURE_FILES = ("steam_search.html", "xbox_catalog.json", "ps_next_data.json")


def test_fixtures_present() -> None:
    """픽스처가 저장소에 있고 비어 있지 않은지 (실수로 빈 파일이 커밋되는 것 방지).

    ⚠️ '파일이 있다' 로는 부족해서 git 추적 여부까지 본다.
    실측 사고: .gitignore 의 `*.html` 규칙이 steam_search.html 을 조용히 삼켜
    커밋이 안 됐다. 로컬에는 파일이 있어 전부 통과했지만 CI 는 FileNotFoundError
    로 계속 실패했다 — 로컬에서 절대 재현되지 않는 실패다.
    """
    tracked: set[str] = set()
    try:
        out = subprocess.run(["git", "ls-files", "tests/fixtures"], cwd=ROOT,
                             capture_output=True, text=True, timeout=15)
        tracked = {Path(l).name for l in out.stdout.split() if l}
    except Exception:
        tracked = set()          # git 이 없는 환경이면 이 검사는 건너뛴다

    for name in FIXTURE_FILES:
        f = FIX / name
        size = f.stat().st_size if f.exists() else 0
        check(f"픽스처 존재: {name}", f.exists() and size > 1000, f"{size} bytes")
        if tracked:
            check(f"픽스처가 git 에 추적됨: {name}", name in tracked,
                  ".gitignore 에 걸려 커밋이 안 되는 상태 — CI 에서만 깨진다")


def _missing_fixtures() -> list[str]:
    return [n for n in FIXTURE_FILES if not (FIX / n).exists()]


if __name__ == "__main__":
    test_fixtures_present()
    # 픽스처가 없으면 뒤 테스트는 예외로 죽어 원인이 안 보인다 — 여기서 멈춘다
    if _missing_fixtures():
        print()
        print(f"실패: 픽스처 없음 — {', '.join(_missing_fixtures())}")
        print("  (tests/fixtures/ 에 실제 스토어 응답이 있어야 한다. "
              ".gitignore 의 *.html 규칙에 걸리지 않았는지 확인)")
        raise SystemExit(1)
    test_steam_search()
    test_steam_price_fallback()
    test_xbox_catalog()
    test_ps_next_data()
    test_mutations_are_detected()
    print()
    if fails:
        print(f"실패 {len(fails)}건: " + ", ".join(fails))
        raise SystemExit(1)
    print("실패 0건 — 전부 통과")
    raise SystemExit(0)
