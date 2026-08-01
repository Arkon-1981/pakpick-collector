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
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import coupang                       # noqa: E402
from common.logging_util import get_logger       # noqa: E402
from db.client import get_client                 # noqa: E402
from parsers.coupang import (  # noqa: E402
    MAX_PRICE, MIN_PRICE, PRICE_RANGES, GearRow, to_row,
)

logger = get_logger(__name__)

# 검색어 — 전부 기기 이름을 포함한다. "게이밍 헤드셋" 처럼 기기 없는 말은 쓰지 않는다.
# (그렇게 찾으면 PC 주변기기가 대부분이라 콘솔 특화라는 색이 사라진다)
# 쿠팡이 한 번에 10건만 주므로(아래 SEARCH_LIMIT 주석) 물량은 키워드 수로 늘린다.
# 실제 수집 결과를 보고 고른 목록이다 — "게임 컨트롤러 충전기"는 6건을 받아
# 콘솔용이 0건이라 뺐고, 저장장치가 얇아서 기기별로 나눠 넣었다.
# (검색어, 카테고리 힌트). 힌트는 상품명 규칙이 아무것도 못 찾을 때만 쓴다.
# 게임 타이틀은 상품명에 범주 단어가 없는 경우가 많아 힌트가 필수다.
KEYWORDS: list[tuple[str, str | None]] = [
    # 본체 — 세대·모델별로 찾는다. "게임기 본체"처럼 넓히면 레트로 에뮬기가 쏟아진다.
    ("PS5 본체", "console"), ("PS5 프로 본체", "console"),
    ("엑스박스 시리즈X 본체", "console"), ("엑스박스 시리즈S 본체", "console"),
    ("닌텐도 스위치 본체", "console"), ("닌텐도 스위치2 본체", "console"),
    ("닌텐도 스위치 올레드 본체", "console"),
    # 게임 타이틀(패키지판)
    ("PS5 게임 타이틀", "title"), ("PS5 게임 패키지", "title"),
    ("닌텐도 스위치 게임 타이틀", "title"), ("닌텐도 스위치2 게임 타이틀", "title"),
    ("닌텐도 스위치 게임칩", "title"), ("엑스박스 게임 타이틀", "title"),
    # 프랜차이즈별 — 종류가 많은 타이틀은 일반 검색어로는 인기작 10개만 반복된다.
    # bestcategories 로 카테고리 전체를 받으려 했지만 최상위 ID만 통하고 그 베스트
    # 100 에 콘솔 물건이 0건이라(2026-08 실측) 검색어를 늘리는 수밖에 없다.
    ("닌텐도 스위치 젤다", "title"), ("닌텐도 스위치 마리오", "title"),
    ("닌텐도 스위치 포켓몬", "title"), ("닌텐도 스위치 커비", "title"),
    ("닌텐도 스위치 동물의숲", "title"), ("닌텐도 스위치 스플래툰", "title"),
    ("닌텐도 스위치2 마리오카트", "title"), ("닌텐도 스위치 피크민", "title"),
    ("PS5 파이널판타지", "title"), ("PS5 몬스터헌터", "title"),
    ("PS5 엘든링", "title"), ("PS5 갓오브워", "title"),
    ("PS5 스파이더맨", "title"), ("PS5 그란투리스모", "title"),
    ("PS5 바이오하자드", "title"), ("PS5 철권", "title"),
    ("엑스박스 포르자", "title"), ("엑스박스 헤일로", "title"),
    # PlayStation 주변기기
    ("PS5 컨트롤러", None), ("듀얼센스 충전 거치대", None), ("PS5 확장 SSD", None),
    ("PS5 케이스 커버", None), ("PS5 헤드셋", None), ("PS5 거치대", None),
    ("듀얼센스 그립", None),
    # Xbox
    ("엑스박스 컨트롤러", None), ("엑스박스 충전 배터리", None), ("엑스박스 헤드셋", None),
    ("엑스박스 거치대", None), ("엑스박스 확장 스토리지", None),
    # Nintendo Switch
    ("닌텐도 스위치 컨트롤러", None), ("닌텐도 스위치 케이스", None),
    ("닌텐도 스위치 메모리카드", None), ("닌텐도 스위치 충전 독", None),
    ("조이콘 그립", None), ("닌텐도 스위치 보호필름", None),
    ("닌텐도 스위치2 케이스", None), ("닌텐도 스위치 파우치", None),
    # 공용
    ("콘솔 게임 헤드셋", None), ("게임패드 충전 거치대", None),
]

# 쿠팡 검색은 limit=20 을 거부하고 10 까지만 받는다(2026-07 확인).
# None 이면 통하는 값을 자동으로 찾는다 — common/coupang.py 의 SEARCH_LIMITS.
SEARCH_LIMIT = None
UPSERT_CHUNK = 100


def collect(keywords: list[tuple[str, str | None]]) -> list[GearRow]:
    """키워드를 돌며 콘솔용 상품만 모은다. 같은 상품은 한 번만."""
    seen: dict[str, GearRow] = {}
    dropped = 0
    failed: list[str] = []

    for kw, hint in keywords:
        try:
            products = coupang.search(kw, limit=SEARCH_LIMIT)
        except coupang.CoupangError as exc:
            logger.warning("[gear] '%s' 검색 실패: %s", kw, exc)
            failed.append(kw)
            continue

        kept = 0
        for p in products:
            row = to_row(p, via=kw, hint=hint)
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


def collect_goldbox() -> list[GearRow]:
    """골드박스(오늘의 특가) 중 콘솔 물건만.

    검색 API 는 정가를 안 줘서 "할인 중"을 알 방법이 없다. 그래서 목록에 상품은
    많은데 진짜 할인은 거의 없다(첫 192건 중 정가 있는 것 0건). 골드박스는 그
    자체가 '지금 특가'라는 뜻이라 이 문제를 정면으로 푸는 유일한 소스다.

    콘솔 물건이 없는 날도 있다. 0건은 실패가 아니라 정상이다 —
    검색 쪽과 달리 여기서 예외를 던지면 안 된다.
    """
    try:
        items = coupang.goldbox()
    except coupang.CoupangError as exc:
        logger.warning("[gear] 골드박스 조회 실패: %s", exc)
        return []

    rows: list[GearRow] = []
    for p in items:
        row = to_row(p, via="goldbox")
        if row is not None:
            row.is_goldbox = True
            rows.append(row)
    logger.info("[gear] 골드박스 — 받음 %d, 콘솔용 %d", len(items), len(rows))
    if rows:
        deals = sum(1 for r in rows if r.discount > 0)
        logger.info("[gear] 골드박스 콘솔용 중 정가까지 온 것 %d건", deals)
    return rows


def _existing_deeplinks() -> dict[str, str]:
    """이미 저장돼 있는 딥링크 {shop_product_id: url}.

    딥링크는 link.coupang.com/a/XXXX 형태다. 검색이 준 1회성 링크
    (/re/AFFSDP?…&requestid=…)는 여기 포함하지 않는다 — 그건 다시 만들어야 한다.
    """
    try:
        res = (
            get_client()
            .table("gear_items")
            .select("shop_product_id,product_url")
            .eq("shop", "coupang")
            .like("product_url", "%link.coupang.com/a/%")
            .execute()
        )
    except Exception:
        logger.exception("[gear] 기존 딥링크 조회 실패 — 전부 새로 만듭니다")
        return {}
    return {r["shop_product_id"]: r["product_url"] for r in (res.data or []) if r.get("product_url")}


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

    # 이미 만들어 둔 딥링크는 다시 만들지 않는다. 딥링크는 한 번 받으면 계속
    # 살아 있으므로 매번 새로 뽑을 이유가 없다 — 10분마다 도는데 그대로 두면
    # 하루 1,000번 넘게 같은 변환을 반복하게 된다.
    known = _existing_deeplinks()
    reused = 0
    todo: list[GearRow] = []
    for r in targets:
        got = known.get(r.shop_product_id)
        if got:
            r.product_url = got
            reused += 1
        else:
            todo.append(r)

    changed = 0
    if todo:
        # 같은 주소가 여러 번 나올 수 있다. 한 번만 변환한다.
        uniq = list(dict.fromkeys(r.canonical for r in todo))
        mapping = coupang.deeplink(uniq)  # type: ignore[arg-type]
        for r in todo:
            short = mapping.get(r.canonical)
            if short:
                r.product_url = short
                changed += 1

    logger.info(
        "[gear] 딥링크 — 새로 %d건, 재사용 %d건 (전체 %d건)", changed, reused, len(targets)
    )
    failed = len(todo) - changed
    if failed:
        logger.warning("[gear] %d건은 변환 실패 — 검색이 준 1회성 링크를 그대로 씁니다", failed)
    return changed + reused


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


def backfill_deeplinks() -> int:
    """저장돼 있는 1회성 링크를 딥링크로 바꾼다.

    이번 수집에 안 잡힌 상품은 to_durable_links 가 손대지 못한다. 검색 결과에서
    빠지면 재수집이 안 되므로, 옛날 링크를 단 채로 last_seen_at 이 만료될 때까지
    (사흘) 목록에 남는다. 실제로 그 카드를 누른 이용자가 "사용권한이 없습니다"를
    봤다 — 링크의 clickBeacon 생성 시각이 첫 딥링크보다 37분 앞섰다.

    itemId·vendorItemId 는 옛 링크의 쿼리에 들어 있으니 거기서 되살린다.
    """
    try:
        res = (
            get_client()
            .table("gear_items")
            .select("id,product_url")
            .eq("shop", "coupang")
            .eq("hidden", False)
            .not_.like("product_url", "%link.coupang.com/a/%")
            .execute()
        )
    except Exception:
        logger.exception("[gear] 옛 링크 조회 실패")
        return 0

    stale = res.data or []
    if not stale:
        return 0

    # 옛 링크(/re/AFFSDP?pageKey=…&itemId=…)에서 평범한 상품 주소를 되살린다
    wanted: dict[int, str] = {}
    for r in stale:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(r["product_url"]).query)
        pid = (q.get("pageKey") or [None])[0]
        if not pid:
            continue
        extra = {k: q[k][0] for k in ("itemId", "vendorItemId") if q.get(k)}
        wanted[r["id"]] = (
            f"https://www.coupang.com/vp/products/{pid}"
            + ("?" + urllib.parse.urlencode(extra) if extra else "")
        )

    if not wanted:
        logger.warning("[gear] 옛 링크 %d건에서 상품 주소를 못 읽었습니다", len(stale))
        return 0

    mapping = coupang.deeplink(list(dict.fromkeys(wanted.values())))
    client = get_client()
    fixed = 0
    for row_id, canon in wanted.items():
        short = mapping.get(canon)
        if not short:
            continue
        try:
            client.table("gear_items").update({"product_url": short}).eq("id", row_id).execute()
            fixed += 1
        except Exception:
            logger.exception("[gear] 링크 교체 실패 (id=%s)", row_id)

    logger.info("[gear] 옛 링크 정리 — %d/%d건 딥링크로 교체", fixed, len(wanted))
    return fixed


def mark_goldbox(rows: list[GearRow]) -> int:
    """오늘 골드박스에 오른 상품에 goldbox_at 을 찍는다.

    upsert 본문에 섞지 않고 따로 하는 이유
      PostgREST 는 보낸 dict 들의 키로 컬럼 목록을 만든다. 행마다 키가 다르면
      목록이 어긋난다. 그렇다고 전부 goldbox_at 키를 넣으면 골드박스가 아닌
      행이 null 로 덮여 '어제 특가였다'는 기록이 지워진다.

    지우지 않고 시각만 갱신한다 — '오늘 특가인가'는 뷰에서 시간으로 판단한다.
    last_seen_at 과 같은 방식이라 따로 초기화할 필요가 없다.
    """
    ids = [r.shop_product_id for r in rows if r.is_goldbox]
    if not ids:
        return 0
    try:
        res = (
            get_client()
            .table("gear_items")
            .update({"goldbox_at": datetime.now(timezone.utc).isoformat()})
            .eq("shop", "coupang")
            .in_("shop_product_id", ids)
            .execute()
        )
    except Exception:
        logger.exception("[gear] 골드박스 표시 실패 (%d건)", len(ids))
        return 0
    n = len(res.data or [])
    logger.info("[gear] 골드박스 표시 %d건", n)
    return n


def sweep_out_of_range() -> int:
    """값 범위를 벗어난 채 남아 있는 상품을 목록에서 내린다.

    파서의 가격 필터는 '새로 저장되는 것'만 막는다. 규칙을 조인 뒤에도 이미
    들어와 있던 행은 last_seen_at 이 만료될 때까지 화면에 남는다 — 실제로
    137만원짜리 500GB SSD 가 그렇게 사흘을 버텼다. 지울 게 아니라 내려 둔다
    (hidden). 쿠팡이 값을 고치면 다음 수집 때 되살릴 수 있게.
    """
    client = get_client()
    total = 0
    # 범위가 카테고리마다 다르다 — 본체 100만원은 정상, 주변기기 100만원은 이상.
    special = list(PRICE_RANGES.keys())
    checks = [(cat, *PRICE_RANGES[cat]) for cat in special] + [(None, MIN_PRICE, MAX_PRICE)]
    for cat, lo, hi in checks:
        for flt, bound in (("gt", hi), ("lt", lo)):
            try:
                q = (
                    client.table("gear_items")
                    .update({"hidden": True})
                    .filter("price", flt, bound)
                    .eq("hidden", False)
                )
                q = q.eq("category", cat) if cat else q.not_.in_("category", special)
                res = q.execute()
                total += len(res.data or [])
            except Exception:
                logger.exception("[gear] 범위 밖 상품 정리 실패 (%s %s %s)", cat, flt, bound)
    if total:
        logger.info("[gear] 값 범위 밖 %d건을 목록에서 내렸습니다", total)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Pakpick 주변기기 수집 (쿠팡 파트너스)")
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 본다")
    ap.add_argument("--keyword", help="이 검색어 하나만")
    args = ap.parse_args()

    if not coupang.configured():
        logger.error("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 없습니다")
        return 1

    rows = collect([(args.keyword, None)] if args.keyword else KEYWORDS)

    # 골드박스는 키워드 하나만 볼 때는 건너뛴다 (그건 검색 디버깅용이다)
    if not args.keyword:
        by_id = {r.shop_product_id: r for r in rows}
        for g in collect_goldbox():
            existing = by_id.get(g.shop_product_id)
            if existing is None:
                by_id[g.shop_product_id] = g
            else:
                # 이미 검색으로 잡힌 상품이면 특가 표시만 옮긴다. 골드박스 응답이
                # 정가를 준다면 그쪽 값이 더 낫다 — 검색은 정가를 아예 안 준다.
                existing.is_goldbox = True
                if g.base_price and not existing.base_price:
                    existing.base_price = g.base_price
                    existing.discount = g.discount
                    existing.price = g.price
        rows = list(by_id.values())

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
    mark_goldbox(rows)
    backfill_deeplinks()
    sweep_out_of_range()
    return 0 if saved or not rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
