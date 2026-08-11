"""필드 보존 회귀 테스트 — '재저장이 다른 경로의 값을 지우는' 버그 재발 방지.

이 버그는 플랫폼×경로 조합마다 반복해서 터졌다(갤러리·신작표시·인기순위·출시일).
경로가 늘 때마다 손으로 보존 코드를 넣는 방식이라, 빠뜨리면 조용히 데이터가
사라진다. 여기서 각 플랫폼의 보존 규칙을 고정해 둔다.

기존 테스트와 같은 스크립트 스타일(pytest 불필요): python tests/test_field_preservation.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fails: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        fails.append(name)


class FakeItem:
    """ParsedItem 대역 — 보존 로직이 쓰는 필드만."""
    def __init__(self, pid: str, data: dict | None = None, image_url=None, title=""):
        self.store_product_id = pid
        self.extracted_data = data if data is not None else {}
        self.image_url = image_url
        self.title = title
        self.is_on_sale = False
        self.sale_end_at = None


# ---------------------------------------------------------------- xbox
def test_xbox_kind_rank() -> None:
    import collectors.xbox as x

    col = x.XboxCollector.__new__(x.XboxCollector)
    col._prev_kinds = {
        "A": {"content_kind": "new", "popular_rank": 3},
        "B": {"content_kind": "free"},
    }
    col._failed_kinds = {"new"}          # new 페이지만 실패한 실행
    saved: list[tuple[str, dict]] = []
    col.save_item = lambda item, rid: saved.append((item.store_product_id, dict(item.extracted_data)))
    col.save_raw = lambda *a, **k: 1
    col.record_parse_error = lambda *a, **k: None

    prods = [FakeItem("A"), FakeItem("B"), FakeItem("C")]
    with patch.object(x, "parse_catalog_products", return_value=prods), \
         patch.object(x, "fetch", return_value=MagicMock(status_code=200, text="{}")), \
         patch.object(x, "json") as mj:
        mj.loads.return_value = {}
        col._fetch_catalog_batch(["A", "B", "C"], batch_index=0,
                                 kinds={"C": "new"}, popular_ranks={})
    d = dict(saved)
    check("xbox: 실패한 종류(new)는 직전 표시 유지", d["A"].get("content_kind") == "new")
    check("xbox: 인기 페이지 정상이면 이탈 상품 순위 삭제", d["A"].get("popular_rank") is None)
    check("xbox: 정상 페이지에서 빠진 상품(free)은 표시 삭제", d["B"].get("content_kind") is None)
    check("xbox: 이번에 잡힌 종류는 그대로 반영", d["C"].get("content_kind") == "new")


# ---------------------------------------------------------------- steam
def test_merge_covers_per_path_keys() -> None:
    """경로별 복구 코드를 지워도 안전한 이유를 고정한다.

    steam(_ENRICH_KEYS)·PS(META_KEYS)가 보강하는 값은 저장 계층의 병합
    (MERGE_FILL_KEYS)이 전부 되살린다. 그래서 중복된 경로별 fetch_item_meta
    복구를 제거했다 — 이 불변식이 깨지면(새 보강 키 추가 등) 그 값이 조용히
    사라지므로 여기서 막는다. 갤러리는 '더 긴 쪽' 규칙이 따로 처리한다.
    """
    from db.repository import MERGE_FILL_KEYS
    from collectors.steam import SteamCollector
    from collectors.playstation import META_KEYS

    missing_steam = [k for k in SteamCollector._ENRICH_KEYS if k not in MERGE_FILL_KEYS]
    check(f"steam 보강 키를 병합이 모두 커버 (누락 {missing_steam})", not missing_steam)
    missing_ps = [k for k in META_KEYS if k not in MERGE_FILL_KEYS]
    check(f"PS 보강 키를 병합이 모두 커버 (누락 {missing_ps})", not missing_ps)

    # 상태성 필드는 병합이 일부러 제외 → 경로별 로직이 반드시 남아 있어야 한다
    for k in ("content_kind", "popular_rank", "subscription"):
        check(f"{k} 는 병합 대상이 아니다(경로별 처리 유지)", k not in MERGE_FILL_KEYS)


def test_xbox_empty_page_is_failure() -> None:
    """종류 페이지가 200 이지만 상품 0건이면 '실패'로 봐야 한다.

    정상 이탈로 오판하면 그 종류 표시가 카탈로그 전체에서 지워진다
    (실측: free 페이지 200/0건 → 무료 표시 50개 전멸).
    """
    import collectors.xbox as x

    col = x.XboxCollector.__new__(x.XboxCollector)
    col.pages_found = 0
    col.save_raw = lambda *a, **k: 1
    errors: list[str] = []
    col.record_parse_error = lambda url, msg, details=None: errors.append(msg)

    # 첫 페이지는 빈 응답(200), 나머지는 상품 1개
    # 실제 스토어 마크업 형식: "productId":"<12자>"  (형식이 다르면 0건이 되어
    # 테스트가 '전부 실패'로 통과해 버리므로 실제 정규식과 맞춘다)
    def fake_fetch(url, **kw):
        empty = "coming-soon" in url          # upcoming 만 빈 페이지
        text = "" if empty else '"productId":"9nabcdefghij"'
        return MagicMock(status_code=200, text=text)

    with patch.object(x, "fetch", side_effect=fake_fetch):
        kinds, ranks, failed = col._fetch_release_kinds()
    check("xbox: 200/0건 페이지는 failed 로 표시", "upcoming" in failed)
    check("xbox: 0건 페이지는 오류로 기록", any("0건" in m for m in errors))
    check("xbox: 정상 페이지는 failed 아님", "new" not in failed)


def test_collectors_have_repository_import() -> None:
    """보존 코드가 repository 를 실제로 부를 수 있는지 (임포트 누락 회귀 방지).

    실측 사고: xbox.py 가 repository.fetch_item_meta 를 부르면서 임포트를 빠뜨려
    매 실행 NameError → except 가 삼켜 _prev_kinds={} → 보존이 통째로 무동작.
    단위 테스트가 _prev_kinds 를 손으로 주입했기 때문에 못 잡았다.
    """
    import importlib
    for mod_name in ("collectors.xbox", "collectors.steam",
                     "collectors.nintendo", "collectors.playstation"):
        mod = importlib.import_module(mod_name)
        src = Path(mod.__file__).read_text()
        uses = "repository." in src
        has_import = hasattr(mod, "repository")
        check(f"{mod_name.split('.')[-1]}: repository 사용 시 임포트 존재",
              (not uses) or has_import)


def test_merge_current_data() -> None:
    """current_data 병합 — 경로가 안 챙긴 보강 값을 자동 보존한다."""
    from db.repository import merge_current_data as merge

    # 신규 상품(직전 값 없음)은 그대로
    check("merge: 직전 값 없으면 새 값 그대로", merge(None, {"a": 1}) == {"a": 1})

    # 보강 값: new 가 비었으면 old 유지, 있으면 new 우선
    old = {"release_date": "2023-01-01", "publisher": "Sony", "genres": ["액션"],
           "content_type": "game"}
    out = merge(old, {"price_raw": {"f": 100}})       # 할인 경로: 가격만
    check("merge: 출시일 보존", out["release_date"] == "2023-01-01")
    check("merge: 퍼블리셔 보존", out["publisher"] == "Sony")
    check("merge: 장르 보존", out["genres"] == ["액션"])
    check("merge: DLC 판별 보존", out["content_type"] == "game")
    check("merge: 새 값(가격)은 그대로", out["price_raw"] == {"f": 100})

    out2 = merge(old, {"release_date": "2024-12-31"})
    check("merge: 새 값이 있으면 최신 우선", out2["release_date"] == "2024-12-31")

    # 갤러리: 목록 파서가 대표 1장만 넣어도 스샷 6장이 살아남아야 한다
    out3 = merge({"gallery": ["a", "b", "c", "d", "e", "f"]}, {"gallery": ["a"]})
    check("merge: 갤러리는 더 긴 쪽 유지", len(out3["gallery"]) == 6)
    out4 = merge({"gallery": ["a"]}, {"gallery": ["a", "b", "c"]})
    check("merge: 새 갤러리가 더 길면 새 것", len(out4["gallery"]) == 3)

    # 상태성 필드는 병합 대상이 아니다 — 목록에서 이탈하면 지워져야 한다
    out5 = merge({"content_kind": "new", "popular_rank": 3, "subscription": "gamepass"},
                 {"price_raw": {}})
    check("merge: content_kind 는 유지하지 않는다(정상 이탈 반영)",
          "content_kind" not in out5)
    check("merge: popular_rank 는 유지하지 않는다", "popular_rank" not in out5)
    check("merge: subscription 은 유지하지 않는다(게임패스 이탈 반영)",
          "subscription" not in out5)

    # 원본을 변형하지 않는다 (호출자가 재사용해도 안전)
    src_old, src_new = {"publisher": "P"}, {"x": 1}
    merge(src_old, src_new)
    check("merge: 입력 dict 를 변형하지 않는다",
          src_old == {"publisher": "P"} and src_new == {"x": 1})


if __name__ == "__main__":
    test_xbox_kind_rank()
    test_merge_covers_per_path_keys()
    test_xbox_empty_page_is_failure()
    test_collectors_have_repository_import()
    test_merge_current_data()
    print()
    if fails:
        print(f"실패 {len(fails)}건: " + ", ".join(fails))
        raise SystemExit(1)
    print("실패 0건 — 전부 통과")
    raise SystemExit(0)
