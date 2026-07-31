"""주변기기 수집 — 쿠팡 파트너스.

    python scripts/collect_gear.py            # 전체 키워드
    python scripts/collect_gear.py --dry-run  # 저장하지 않고 결과만 출력
    python scripts/collect_gear.py --keyword "PS5 컨트롤러"   # 하나만

게임 수집기(BaseCollector)와 따로 둔 이유
  게임은 store_items·price_snapshots·버전 기록·전멸 방지 가드가 한 덩어리로 엮여
  있는데, 주변기기는 그 어느 것도 필요 없다. 억지로 얹으면 양쪽 다 복잡해진다.

'콘솔 관련만' 은 두 겹으로 지킨다
  1) 검색어에 항상 기기 이름을 넣는다 (아래 KEYWORDS)
  2) 돌아온 상품명을 parsers/coupang 이 다시 검사한다

필요한 환경변수
  COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY   — 파트너스 API 키
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  — 수집기와 동일
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import coupang                       # noqa: E402
from common.logging_util import get_logger       # noqa: E402
from db.client import get_client                 # noqa: E402
from parsers.coupang import MAX_PRICE, MIN_PRICE, GearRow, to_row  # noqa: E402

logger = get_logger(__name__)

# 검색어 — 전부 기기 이름을 포함한다. "게이밍 헤드셋" 처럼 기기 없는 말은 쓰지 않는다.
# (그렇게 찾으면 PC 주변기기가 대부분이라 콘솔 특화라는 색이 사라진다)
# 쿠팡이 한 번에 10건만 주므로(아래 SEARCH_LIMIT 주석) 물량은 키워드 수로 늘린다.
# 실제 수집 결과를 보고 고른 목록이다 — "게임 컨트롤러 충전기"는 6건을 받아
# 콘솔용이 0건이라 뺐고, 저장장치가 얇아서 기기별로 나눠 넣었다.
KEYWORDS: list[str] = [
    # PlayStation
    "PS5 컨트롤러", "듀얼센스 충전 거치대", "PS5 확장 SSD", "PS5 케이스 커버",
    "PS5 헤드셋", "PS5 거치대", "듀얼센스 그립",
    # Xbox
    "엑스박스 컨트롤러", "엑스박스 충전 배터리", "엑스박스 헤드셋", "엑스박스 거치대",
    "엑스박스 확장 스토리지",
    # Nintendo Switch
    "닌텐도 스위치 컨트롤러", "닌텐도 스위치 케이스", "닌텐도 스위치 메모리카드",
    "닌텐도 스위치 충전 독", "조이콘 그립", "닌텐도 스위치 보호필름",
    "닌텐도 스위치2 케이스", "닌텐도 스위치 파우치",
    # 공용
    "콘솔 게임 헤드셋", "게임패드 충전 거치대",
]

# 쿠팡 검색은 limit=20 을 거부하고 10 까지만 받는다(2026-07 확인).
# None 이면 통하는 값을 자동으로 찾는다 — common/coupang.py 의 SEARCH_LIMITS.
SEARCH_LIMIT = None
UPSERT_CHUNK = 100


def collect(keywords: list[str]) -> list[GearRow]:
    """키워드를 돌며 콘솔용 상품만 모은다. 같은 상품은 한 번만."""
    seen: dict[str, GearRow] = {}
    dropped = 0
    failed: list[str] = []

    for kw in keywords:
        try:
            products = coupang.search(kw, limit=SEARCH_LIMIT)
        except coupang.CoupangError as exc:
            logger.warning("[gear] '%s' 검색 실패: %s", kw, exc)
            failed.append(kw)
            continue

        kept = 0
        for p in products:
            row = to_row(p, via=kw)
            if row is None:
                dropped += 1
                continue
            # 같은 상품이 여러 키워드에 걸리면 먼저 것을 쓴다
            if row.shop_product_id not in seen:
                seen[row.shop_product_id] = row
                kept += 1
        logger.info("[gear] '%s' — 받음 %d, 콘솔용 %d", kw, len(products), kept)

    logger.info(
        "[gear] 수집 완료 — 상품 %d개 (콘솔 무관 %d개 제외, 검색 실패 %d개)",
        len(seen), dropped, len(failed),
    )
    if failed and len(failed) == len(keywords):
        # 전부 실패면 키나 서명 문제다. 조용히 0건으로 끝내면 원인을 못 찾는다.
        raise SystemExit("[gear] 모든 검색이 실패했습니다 — 키·서명을 확인하세요")
    return list(seen.values())


def to_durable_links(rows: list[GearRow]) -> int:
    """product_url 을 오래 사는 딥링크(coupa.ng/…)로 바꾼다.

    검색 API 가 주는 링크는 requestid·traceid·clickBeacon 이 박힌 1회성 주소다.
    우리는 링크를 DB 에 담아 두고 며칠씩 보여 주므로 그대로 쓰면 나중에 눌렀을 때
    쿠팡이 거부한다("사용권한이 없습니다"). deeplink API 가 주는 단축 링크는
    블로그 글에 박아 두는 그 링크라 시간이 지나도 살아 있다.

    변환에 실패한 것은 원래 링크를 그대로 둔다 — 없는 것보다는 낫다.
    """
    targets = [r for r in rows if r.canonical]
    if not targets:
        return 0

    # 같은 주소가 여러 번 나올 수 있다. 한 번만 변환한다.
    uniq = list(dict.fromkeys(r.canonical for r in targets))
    mapping = coupang.deeplink(uniq)  # type: ignore[arg-type]

    changed = 0
    for r in targets:
        short = mapping.get(r.canonical)
        if short:
            r.product_url = short
            changed += 1

    logger.info("[gear] 딥링크 변환 %d/%d건", changed, len(targets))
    if changed < len(targets):
        logger.warning(
            "[gear] %d건은 변환 실패 — 검색이 준 1회성 링크를 그대로 씁니다",
            len(targets) - changed,
        )
    return changed


def save(rows: list[GearRow]) -> int:
    """gear_items 에 upsert. last_seen_at 을 갱신해 '아직 파는 물건'을 표시한다."""
    if not rows:
        return 0
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()

    payload = [
        {
            "shop": r.shop,
            "shop_product_id": r.shop_product_id,
            "name": r.name,
            "category": r.category,
            "consoles": r.consoles,
            "price": r.price,
            "base_price": r.base_price,
            "discount": r.discount,
            "image_url": r.image_url,
            "product_url": r.product_url,
            "is_rocket": r.is_rocket,
            "is_free_ship": r.is_free_ship,
            "rating": r.rating,
            "review_count": r.review_count,
            "rank": r.rank,
            "last_seen_at": now,
        }
        for r in rows
    ]

    saved = 0
    for i in range(0, len(payload), UPSERT_CHUNK):
        chunk = payload[i : i + UPSERT_CHUNK]
        try:
            client.table("gear_items").upsert(
                chunk, on_conflict="shop,shop_product_id"
            ).execute()
            saved += len(chunk)
        except Exception:
            logger.exception("[gear] 저장 실패 (%d건)", len(chunk))
    return saved


def sweep_out_of_range() -> int:
    """값 범위를 벗어난 채 남아 있는 상품을 목록에서 내린다.

    파서의 가격 필터는 '새로 저장되는 것'만 막는다. 규칙을 조인 뒤에도 이미
    들어와 있던 행은 last_seen_at 이 만료될 때까지 화면에 남는다 — 실제로
    137만원짜리 500GB SSD 가 그렇게 사흘을 버텼다. 지울 게 아니라 내려 둔다
    (hidden). 쿠팡이 값을 고치면 다음 수집 때 되살릴 수 있게.
    """
    client = get_client()
    total = 0
    for flt, bound in (("gt", MAX_PRICE), ("lt", MIN_PRICE)):
        try:
            res = (
                client.table("gear_items")
                .update({"hidden": True})
                .filter("price", flt, bound)
                .eq("hidden", False)
                .execute()
            )
            total += len(res.data or [])
        except Exception:
            logger.exception("[gear] 범위 밖 상품 정리 실패 (%s %s)", flt, bound)
    if total:
        logger.info("[gear] 값 범위(%s~%s원) 밖 %d건을 목록에서 내렸습니다",
                    f"{MIN_PRICE:,}", f"{MAX_PRICE:,}", total)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Pakpick 주변기기 수집 (쿠팡 파트너스)")
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 본다")
    ap.add_argument("--keyword", help="이 검색어 하나만")
    args = ap.parse_args()

    if not coupang.configured():
        logger.error("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 없습니다")
        return 1

    rows = collect([args.keyword] if args.keyword else KEYWORDS)

    if args.dry_run:
        logger.info("[gear] (dry-run) 딥링크 변환은 건너뜁니다")
        by_cat: dict[str, int] = {}
        for r in rows:
            by_cat[r.category] = by_cat.get(r.category, 0) + 1
        for r in sorted(rows, key=lambda x: -x.discount)[:20]:
            logger.info(
                "  %-11s %-4s %6s원 %3d%% | %s",
                r.category, "/".join(r.consoles)[:10], f"{int(r.price):,}", r.discount, r.name[:52],
            )
        logger.info("[gear] 분류: %s", by_cat)
        return 0

    to_durable_links(rows)
    saved = save(rows)
    logger.info("[gear] 저장 %d건", saved)
    sweep_out_of_range()
    return 0 if saved or not rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
