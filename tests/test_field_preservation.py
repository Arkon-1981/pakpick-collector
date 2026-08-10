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
def test_steam_enrich_fallback() -> None:
    """GetItems 배치가 실패하면 직전 보강값(갤러리·DLC판별 등)을 되살린다."""
    import collectors.steam as s

    col = s.SteamCollector.__new__(s.SteamCollector)
    col.platform = "steam"
    # 목록에서 온 상태: 갤러리는 header 1장뿐, DLC 판별 없음
    item = FakeItem("100", {"gallery": ["header.jpg"]})
    col._fetch_store_items = lambda ids: {}      # 배치 전면 실패 상황
    prev = {"100": {"content_type": "addon", "gallery": ["a.jpg", "b.jpg", "c.jpg"],
                    "release_date": "2024-01-01", "publishers": ["P"]}}
    with patch.object(s.repository, "fetch_item_meta", return_value=prev):
        col._enrich([item])
    d = item.extracted_data
    check("steam: 보강 실패 시 DLC 판별 복구", d.get("content_type") == "addon")
    check("steam: 보강 실패 시 갤러리(더 긴 쪽) 복구", len(d.get("gallery") or []) == 3)
    check("steam: 보강 실패 시 출시일 복구", d.get("release_date") == "2024-01-01")

    # 보강이 성공한 상품은 직전 값으로 되돌리지 않는다
    item2 = FakeItem("200", {"gallery": ["h.jpg"]})
    col._fetch_store_items = lambda ids: {"200": {"content_type": "game",
                                                  "screenshots": ["s1.jpg", "s2.jpg"]}}
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return {}
    with patch.object(s.repository, "fetch_item_meta", side_effect=_spy):
        col._enrich([item2])
    check("steam: 전부 보강 성공하면 복구 조회를 하지 않는다", called["n"] == 0)
    check("steam: 보강 성공분은 새 값 반영", item2.extracted_data.get("content_type") == "game")


# ---------------------------------------------------------------- playstation
def test_ps_keep_meta() -> None:
    """할인 경로 저장 직전, 신작/무료 때 보강해 둔 메타를 되살린다."""
    import collectors.playstation as p

    col = p.PlaystationCollector.__new__(p.PlaystationCollector)
    col._prev_meta = {"CUSA1": {"release_date": "2023-05-05", "publisher": "Sony",
                                "genres": ["액션"], "content_type": "game"}}
    item = FakeItem("CUSA1", {"price_raw": {"x": 1}})   # 할인 경로: 가격만 있다
    col._keep_meta(item)
    d = item.extracted_data
    check("PS: 할인 재저장에 출시일 보존", d.get("release_date") == "2023-05-05")
    check("PS: 할인 재저장에 DLC 판별 보존", d.get("content_type") == "game")
    check("PS: 할인 재저장에 장르 보존", d.get("genres") == ["액션"])
    check("PS: 가격은 그대로", d.get("price_raw") == {"x": 1})

    # 목록에서 이미 온 값은 직전 값으로 덮지 않는다
    item2 = FakeItem("CUSA1", {"release_date": "2024-12-31"})
    col._keep_meta(item2)
    check("PS: 새 값이 있으면 유지(덮어쓰지 않음)",
          item2.extracted_data.get("release_date") == "2024-12-31")

    # 직전 값이 없는 신규 상품은 그대로
    item3 = FakeItem("NEW1", {})
    col._keep_meta(item3)
    check("PS: 신규 상품은 예외 없이 통과", item3.extracted_data == {})


# ---------------------------------------------------------------- 병합 저장(A안)
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
    test_steam_enrich_fallback()
    test_ps_keep_meta()
    test_merge_current_data()
    print()
    if fails:
        print(f"실패 {len(fails)}건: " + ", ".join(fails))
        raise SystemExit(1)
    print("실패 0건 — 전부 통과")
    raise SystemExit(0)
