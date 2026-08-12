"""IGDB 보강 로직 테스트 — API 키 없이 검증되는 부분만.

왜 필요한가:
  이 스크립트는 Twitch 키가 GitHub Secrets 에만 있어 로컬에서 실제 호출을 못 한다.
  그래서 '평점 폴백' 같은 판단 로직이 맞는지 확인할 방법이 없었다. IGDB 응답을
  흉내 낸 dict 를 넣어 순수 로직만 고정한다.

실행: python tests/test_igdb_enrich.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def test_has_rating() -> None:
    from scripts.enrich_igdb import has_rating

    check("평점 있음: 평론가만", has_rating({"aggregated_rating": 82.5}))
    check("평점 있음: 이용자만", has_rating({"rating": 77.0}))
    check("평점 없음: 빈 dict", not has_rating({}))
    # IGDB 가 0 을 주는 경우가 있는데 '평점 0점'은 사실상 무의미 → 없음으로 본다
    check("평점 없음: 0 은 없는 것으로", not has_rating({"rating": 0, "aggregated_rating": 0}))


def test_parent_fallback_targets() -> None:
    """평점 없는 것만, 부모가 있을 때만 조회 대상이 된다."""
    import scripts.enrich_igdb as e

    games = [
        {"id": 1, "aggregated_rating": 90},                    # 평점 있음 → 제외
        {"id": 2, "version_parent": 200},                      # 판본 원본 → 대상
        {"id": 3, "parent_game": 300},                         # 본편 → 대상
        {"id": 4},                                             # 부모 없음 → 제외
        {"id": 5, "version_parent": 5},                        # 자기 자신 → 제외
        {"id": 6, "version_parent": {"id": 600}},              # 확장 응답 형태 → 대상
        {"id": 7, "version_parent": 700},                      # 부모도 평점 없음 → 결과 제외
    ]
    parent_rows = [
        {"id": 200, "aggregated_rating": 88.0, "aggregated_rating_count": 30},
        {"id": 300, "rating": 75.5, "rating_count": 120},
        {"id": 600, "aggregated_rating": 70.0},
        {"id": 700},                                           # 평점 없는 부모
    ]
    calls: list[str] = []

    def fake_query(endpoint, body):
        calls.append(body)
        return parent_rows

    with patch.object(e, "igdb_query", side_effect=fake_query), \
         patch.object(e.time, "sleep", lambda *_: None):
        out = e.fetch_parent_ratings(games)

    check("폴백 대상만 조회", sorted(out) == [2, 3, 6], str(sorted(out)))
    check("평점 있는 게임은 부모를 안 본다", 1 not in out)
    check("부모 없으면 제외", 4 not in out)
    check("자기 자신을 부모로 가리키면 제외", 5 not in out)
    check("부모에도 평점이 없으면 제외", 7 not in out)
    check("판본 원본 평점을 가져온다", out.get(2, {}).get("aggregated_rating") == 88.0)
    check("본편 평점을 가져온다", out.get(3, {}).get("rating") == 75.5)
    check("확장 응답(dict) 부모도 처리", out.get(6, {}).get("aggregated_rating") == 70.0)
    # 요청은 한 번에 묶어 보낸다 (상품마다 부르면 IGDB 한도를 태운다)
    check("부모 조회는 배치 1회", len(calls) == 1, f"{len(calls)}회")


def test_no_parent_no_query() -> None:
    """폴백 대상이 없으면 IGDB 를 아예 부르지 않는다."""
    import scripts.enrich_igdb as e

    calls: list[str] = []
    with patch.object(e, "igdb_query", side_effect=lambda ep, b: calls.append(b) or []):
        out = e.fetch_parent_ratings([{"id": 1, "rating": 80}])
    check("전부 평점 있으면 조회 0회", out == {} and not calls)


def test_candidate_scan_not_capped() -> None:
    """후보 조회가 짧은 페이지에서 멈추고, 상한에 먼저 걸리지 않는다.

    실측 사고: offset 10,000 상한 때문에 신선한 상품 13,889개 중 뒤쪽 3,900개가
    영구히 후보가 못 됐다 — 앞쪽이 다 보강돼도 순서가 오지 않는다.
    """
    import re
    src = Path("scripts/enrich_igdb.py").read_text()
    caps = [int(m.replace("_", "")) for m in re.findall(r"for offset in range\(0, ([\d_]+), 1000\)", src)]
    check(f"페이지 상한이 충분히 크다 {caps}", bool(caps) and all(c >= 100_000 for c in caps))


if __name__ == "__main__":
    test_has_rating()
    test_parent_fallback_targets()
    test_no_parent_no_query()
    test_candidate_scan_not_capped()
    print()
    if fails:
        print(f"실패 {len(fails)}건: " + ", ".join(fails))
        raise SystemExit(1)
    print("실패 0건 — 전부 통과")
    raise SystemExit(0)
