"""DB 저장 로직.

Supabase에 이미 만들어 둔 6개 테이블에 맞춰 저장한다:
  crawl_runs           수집 실행 기록
  raw_documents        원본 파일 위치 기록
  store_items          스토어 상품 기본 정보 (현재 상태)
  store_item_versions  상품 정보가 바뀔 때마다 전체 스냅샷
  price_snapshots      가격이 바뀔 때마다 기록
  crawl_errors         수집 중 오류
"""
from datetime import datetime, timedelta, timezone

from common.hashing import sha256_json
from common.logging_util import get_logger
from db.client import get_client

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------
# 전멸 방지 가드 보조
# ---------------------------------------------------------------
def recent_good_counts(platform: str, n: int = 3) -> list[int]:
    """최근 '실제로 수집된' 실행(success/partial) n건의 상품 수 목록.

    이상 감지(급감)의 기준선을 '직전 1건'이 아니라 최근 몇 건의 중앙값으로
    잡기 위한 데이터. 세일 주기로 인한 단발성 등락에 덜 민감해진다.
    """
    res = (
        get_client()
        .table("crawl_runs")
        .select("products_found")
        .eq("platform", platform)
        .in_("status", ["success", "partial"])
        .order("started_at", desc=True)
        .limit(n)
        .execute()
    )
    return [(r.get("products_found") or 0) for r in (res.data or [])]


def last_finished_status(platform: str) -> str | None:
    """직전 실행의 상태(성공/부분/실패)를 반환.

    연속 고장 판단용: 직전이 이미 failed면 보호를 반복하지 않아 stale 영구화를 막는다.
    이 함수는 현재 실행 생성(start_crawl_run) 전에 호출되므로,
    가장 최근 실행 1건이 곧 '직전 실행'이다.
    """
    res = (
        get_client()
        .table("crawl_runs")
        .select("status")
        .eq("platform", platform)
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0]["status"] if res.data else None


def protect_recent_items(platform: str, hours: int) -> int:
    """최근 `hours` 안에 보였던 상품들의 last_seen_at을 now로 갱신.

    크롤러 고장(급감)으로 판단됐을 때, 웹의 신선도 필터에서 기존 상품이
    한 사이클 더 살아남게 해 '사이트 전멸'을 막는다. 반환: 보호된 행 수.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        get_client()
        .table("store_items")
        .update({"last_seen_at": _now()})
        .eq("platform", platform)
        .gte("last_seen_at", cutoff)
        .execute()
    )
    return len(res.data or [])


# ---------------------------------------------------------------
# 수집 실행 (crawl_runs)
# ---------------------------------------------------------------

def start_crawl_run(platform: str) -> int:
    res = (
        get_client()
        .table("crawl_runs")
        .insert({"platform": platform, "status": "running"})
        .execute()
    )
    run_id = res.data[0]["id"]
    logger.info("[%s] 수집 시작 (run_id=%d)", platform, run_id)
    return run_id


def finish_crawl_run(
    run_id: int,
    *,
    status: str,
    pages_found: int = 0,
    products_found: int = 0,
    errors_count: int = 0,
    error_message: str | None = None,
) -> None:
    get_client().table("crawl_runs").update(
        {
            "finished_at": _now(),
            "status": status,
            "pages_found": pages_found,
            "products_found": products_found,
            "errors_count": errors_count,
            "error_message": error_message,
        }
    ).eq("id", run_id).execute()


def record_error(
    run_id: int | None,
    platform: str,
    *,
    source_url: str | None = None,
    error_type: str = "unknown",
    error_message: str = "",
    error_details: dict | None = None,
) -> None:
    try:
        get_client().table("crawl_errors").insert(
            {
                "crawl_run_id": run_id,
                "platform": platform,
                "source_url": source_url,
                "error_type": error_type,
                "error_message": error_message[:2000],
                "error_details": error_details or {},
            }
        ).execute()
    except Exception:  # 오류 기록 자체가 실패해도 수집은 계속
        logger.exception("crawl_errors 기록 실패")


# ---------------------------------------------------------------
# 원본 문서 (raw_documents)
# ---------------------------------------------------------------

def record_raw_document(
    *,
    crawl_run_id: int,
    platform: str,
    document_type: str,
    store_region: str,
    store_product_id: str | None,
    source_url: str,
    storage_bucket: str,
    storage_path: str,
    content_type: str,
    http_status: int,
    content_hash: str,
    file_size: int,
    response_headers: dict,
) -> int | None:
    """원본 파일 정보를 기록한다.

    같은 URL + 같은 내용(해시)이 이미 있으면 last_collected_at만 갱신된다
    (unique 제약: platform, store_region, source_url, content_hash).
    """
    client = get_client()

    # 이미 같은 내용이 저장돼 있는지 확인
    existing = (
        client.table("raw_documents")
        .select("id")
        .eq("platform", platform)
        .eq("store_region", store_region)
        .eq("source_url", source_url)
        .eq("content_hash", content_hash)
        .limit(1)
        .execute()
    )
    if existing.data:
        doc_id = existing.data[0]["id"]
        client.table("raw_documents").update(
            {"last_collected_at": _now()}
        ).eq("id", doc_id).execute()
        return doc_id

    res = client.table("raw_documents").insert(
        {
            "crawl_run_id": crawl_run_id,
            "platform": platform,
            "document_type": document_type,
            "store_region": store_region,
            "store_product_id": store_product_id,
            "source_url": source_url,
            "storage_bucket": storage_bucket,
            "storage_path": storage_path,
            "content_type": content_type,
            "http_status": http_status,
            "content_hash": content_hash,
            "file_size": file_size,
            "response_headers": {
                k: v
                for k, v in response_headers.items()
                if k.lower() in ("content-type", "date", "last-modified", "etag", "cache-control")
            },
        }
    ).execute()
    return res.data[0]["id"]


# ---------------------------------------------------------------
# 상품 (store_items / store_item_versions)
# ---------------------------------------------------------------

def get_item_gallery(platform: str, store_region: str, store_product_id: str) -> list | None:
    """이미 저장된 상품의 갤러리(current_data->gallery)를 돌려준다. 없으면 None.

    스팀 갤러리 보강 시 재사용용: 이미 스크린샷을 채운 상품은 상세 API를
    다시 부르지 않고 기존 갤러리를 그대로 유지한다.
    """
    res = (
        get_client()
        .table("store_items")
        .select("current_data")
        .eq("platform", platform)
        .eq("store_region", store_region)
        .eq("store_product_id", store_product_id)
        .limit(1)
        .execute()
    )
    if res.data:
        gallery = (res.data[0].get("current_data") or {}).get("gallery")
        if isinstance(gallery, list):
            return gallery
    return None


def known_product_ids(platform: str, store_region: str, limit: int = 5000) -> list[str]:
    """이미 저장된 상품 ID 목록. 공식 가격 API로 시세만 새로 받을 때 대상 명단으로 쓴다.

    (닌텐도는 목록 크롤이 느려서, 한 번 알게 된 상품은 이후 API로만 갱신한다)
    """
    out: list[str] = []
    page = 1000
    for offset in range(0, limit, page):
        res = (
            get_client()
            .table("store_items")
            .select("store_product_id")
            .eq("platform", platform)
            .eq("store_region", store_region)
            .order("id")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        out.extend(r["store_product_id"] for r in rows if r.get("store_product_id"))
        if len(rows) < page:
            break
    return out


def item_id_map(platform: str, store_region: str, limit: int = 20000) -> dict[str, int]:
    """{상품ID: 내부 id} 전체를 한 번에 받아 온다.

    find_item_id 를 상품마다 부르면 요청이 수천 번 난다(닌텐도 가격 갱신 2,000개
    기준 2,000회). 목록으로 받으면 20여 회로 끝난다.
    """
    out: dict[str, int] = {}
    page = 1000
    for offset in range(0, limit, page):
        res = (
            get_client()
            .table("store_items")
            .select("id,store_product_id")
            .eq("platform", platform)
            .eq("store_region", store_region)
            .order("id")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        for row in rows:
            pid = row.get("store_product_id")
            if pid:
                out[pid] = row["id"]
        if len(rows) < page:
            break
    return out


def touch_versions_many(version_ids: list[int], chunk: int = 500) -> None:
    """버전 행들의 last_seen_at 을 한 번에 갱신한다 (상품마다 PATCH 하지 않기 위해)."""
    for i in range(0, len(version_ids), chunk):
        batch = version_ids[i : i + chunk]
        if not batch:
            continue
        get_client().table("store_item_versions").update(
            {"last_seen_at": _now()}
        ).in_("id", batch).execute()


def touch_last_seen_many(item_ids: list[int], chunk: int = 500) -> None:
    """여러 상품의 last_seen_at 을 한 번에 갱신한다 (상품 정보는 건드리지 않음)."""
    for i in range(0, len(item_ids), chunk):
        batch = item_ids[i : i + chunk]
        if not batch:
            continue
        get_client().table("store_items").update(
            {"last_seen_at": _now()}
        ).in_("id", batch).execute()


def find_item_id(platform: str, store_region: str, store_product_id: str) -> int | None:
    """상품의 내부 id만 찾는다. 아무것도 수정하지 않는다.

    가격 API처럼 '시세만' 갱신할 때 쓴다. upsert_store_item 은 넘긴 값으로 제목·이미지·
    current_data 를 통째로 덮어쓰므로, 가격만 있는 호출에 쓰면 기존 상품 정보가 지워진다.
    """
    res = (
        get_client()
        .table("store_items")
        .select("id")
        .eq("platform", platform)
        .eq("store_region", store_region)
        .eq("store_product_id", store_product_id)
        .limit(1)
        .execute()
    )
    return res.data[0]["id"] if res.data else None


def fetch_item_meta(platform: str, store_region: str, keys: list[str]) -> dict[str, dict]:
    """저장된 상품들의 current_data 중 지정한 키만 뽑아 {상품ID: {키: 값}} 으로 돌려준다.

    upsert_store_item 은 current_data 를 통째로 덮어쓴다. 그래서 한 번 보강해 둔
    값(출시일·퍼블리셔 등)을 다음 실행에서 유지하려면, 저장 직전에 기존 값을 다시
    실어 줘야 한다. 상품마다 조회하면 요청이 수백 번 나가므로 한 번에 받아 온다.

    current_data 통째로가 아니라 필요한 키만 골라 받는다(PS는 노드 원본을 통째로
    보관해서 행 하나가 수십 KB다 → 키만 뽑으면 수백 배 가볍다).
    """
    if not keys:
        return {}
    # `->>` 가 아니라 `->` 를 쓴다: `->>` 는 무조건 문자열로 바꿔 버려서
    # 배열(genres, platforms)이 '["액션"]' 같은 문자열로 되돌아온다.
    select = "store_product_id," + ",".join(f"{k}:current_data->{k}" for k in keys)
    out: dict[str, dict] = {}
    page = 1000
    for offset in range(0, 20000, page):
        res = (
            get_client()
            .table("store_items")
            .select(select)
            .eq("platform", platform)
            .eq("store_region", store_region)
            .order("id")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        for row in rows:
            pid = row.get("store_product_id")
            values = {k: row[k] for k in keys if row.get(k) is not None}
            if pid and values:
                out[pid] = values
        if len(rows) < page:
            break
    return out


def touch_last_seen(item_id: int) -> None:
    """last_seen_at 만 갱신한다 (상품 정보는 건드리지 않음)."""
    get_client().table("store_items").update(
        {"last_seen_at": _now()}
    ).eq("id", item_id).execute()


def fetch_items_by_product_ids(
    platform: str, store_region: str, product_ids: list[str]
) -> dict[str, dict]:
    """상품ID 목록의 행을 {상품ID: {"id", "current_data"}} 로 돌려준다.

    current_data 를 통째로 받는 무거운 조회라 소수 상품 전용이다
    (PS 인기 Top 10 처럼). 카탈로그 전체에는 fetch_item_meta 를 쓴다.
    """
    if not product_ids:
        return {}
    res = (
        get_client()
        .table("store_items")
        .select("id,store_product_id,current_data")
        .eq("platform", platform)
        .eq("store_region", store_region)
        .in_("store_product_id", product_ids)
        .execute()
    )
    return {
        row["store_product_id"]: {"id": row["id"], "current_data": row.get("current_data") or {}}
        for row in (res.data or [])
    }


def update_current_data(item_id: int, current_data: dict, *, touch: bool = False) -> None:
    """current_data 를 통째로 교체한다 (호출자가 기존 값에 병합해서 넘길 것).

    upsert_store_item 과 달리 제목·이미지·버전 기록은 건드리지 않는다 —
    순위 표시처럼 current_data 안의 키 하나만 고치고 싶을 때 쓴다.
    """
    payload: dict = {"current_data": current_data}
    if touch:
        payload["last_seen_at"] = _now()
    get_client().table("store_items").update(payload).eq("id", item_id).execute()


def upsert_store_item(
    *,
    platform: str,
    store_region: str,
    store_product_id: str,
    title: str | None,
    store_url: str | None,
    image_url: str | None,
    extracted_data: dict,
    raw_document_id: int | None,
    id_cache: dict[str, int] | None = None,
    touch_queue: list[int] | None = None,
) -> int:
    """상품 기본 정보를 저장/갱신하고, 내용이 바뀌었으면 버전 기록도 남긴다.

    id_cache: {상품ID: 내부 id}. 주면 '기존 상품 찾기' 조회를 건너뛴다.
      실측(PS 1회 실행): 이 조회만 2,137회였다. 목록으로 한 번 받아 쓰면 1회로 줄고,
      새로 넣은 상품은 캐시에 바로 반영해 같은 실행 안에서도 일관된다.
    touch_queue: 버전 행의 last_seen_at 갱신을 여기 모아 두면 호출자가 한 번에 처리한다.
      내용이 안 바뀐 상품마다 PATCH 를 보내면 실측 1,674회가 더 붙는다.
    """
    client = get_client()

    # (platform, store_region, store_product_id) 유니크 제약을 이용한 원자적 upsert.
    # 예전엔 'SELECT 로 id 찾기 → UPDATE 또는 INSERT' 분기였는데, 같은 플랫폼
    # 워크플로가 겹쳐 돌면 둘 다 '없음'으로 보고 INSERT 해 중복 행/오류가 났다.
    # upsert 는 DB 가 충돌을 처리하므로 레이스가 없고, 캐시 미스 시의 SELECT 도 사라진다.
    res = client.table("store_items").upsert(
        {
            "platform": platform,
            "store_region": store_region,
            "store_product_id": store_product_id,
            "title": title,
            "store_url": store_url,
            "image_url": image_url,
            "current_data": extracted_data,
            "last_seen_at": _now(),
            "updated_at": _now(),
        },
        on_conflict="platform,store_region,store_product_id",
    ).execute()
    item_id = res.data[0]["id"]
    if id_cache is not None:
        id_cache[store_product_id] = item_id

    # 상품 정보 버전 기록 — (store_item_id, data_hash) 유니크 제약을 이용해
    # upsert 한 방으로 처리한다. 내용이 같으면 그 행의 last_seen_at 만 갱신되고,
    # 다르면 새 행이 들어간다. 예전의 'SELECT 후 분기'를 없애 상품당 조회 1회가
    # 사라지고(N+1 완화), 동시 실행이 겹쳐도 DB 가 중복을 막아 레이스가 없다.
    # (touch_queue 는 하위호환용으로 남겨 두지만 이 경로에선 쓰지 않는다.)
    data_hash = sha256_json(extracted_data)
    client.table("store_item_versions").upsert(
        {
            "store_item_id": item_id,
            "raw_document_id": raw_document_id,
            "data_hash": data_hash,
            "extracted_data": extracted_data,
            "last_seen_at": _now(),
        },
        on_conflict="store_item_id,data_hash",
    ).execute()

    return item_id


# ---------------------------------------------------------------
# 가격 (price_snapshots)
# ---------------------------------------------------------------

def update_latest_sale_end(
    platform: str, store_region: str, store_product_id: str, sale_end_at: str
) -> bool:
    """이미 저장된 상품의 '가장 최근 price_snapshot' 할인 종료시각만 제자리 갱신한다.

    PS 종료일 보강용: 목록 수집 때 이미 저장된 스냅샷에 상세에서 얻은 sale_end_at을
    덧입힌다. price_hash는 건드리지 않는다(다음 수집의 변동 감지가 종료일 때문에 흔들려
    불필요한 스냅샷이 쌓이는 것을 막기 위함). 대상 상품/스냅샷이 없으면 False.
    """
    client = get_client()
    item = (
        client.table("store_items")
        .select("id")
        .eq("platform", platform)
        .eq("store_region", store_region)
        .eq("store_product_id", store_product_id)
        .limit(1)
        .execute()
    )
    if not item.data:
        return False
    item_id = item.data[0]["id"]
    latest = (
        client.table("price_snapshots")
        .select("id")
        .eq("store_item_id", item_id)
        .order("collected_at", desc=True)
        .limit(1)
        .execute()
    )
    if not latest.data:
        return False
    client.table("price_snapshots").update({"sale_end_at": sale_end_at}).eq(
        "id", latest.data[0]["id"]
    ).execute()
    return True


def insert_price_snapshot_if_changed(
    *,
    store_item_id: int,
    raw_document_id: int | None,
    currency: str = "KRW",
    regular_price: float | None,
    sale_price: float | None,
    final_price: float | None,
    discount_percent: float | None,
    sale_start_at: str | None,
    sale_end_at: str | None,
    is_on_sale: bool,
    is_available: bool = True,
    price_data: dict | None = None,
) -> bool:
    """가격이 직전 기록과 다를 때만 새 스냅샷을 저장한다.

    반환값: 새로 저장했으면 True, 변동 없어서 건너뛰었으면 False
    """
    client = get_client()

    price_hash = sha256_json(
        {
            "regular": regular_price,
            "sale": sale_price,
            "final": final_price,
            "discount": discount_percent,
            "sale_start": sale_start_at,
            "sale_end": sale_end_at,
            "on_sale": is_on_sale,
            "available": is_available,
        }
    )

    latest = (
        client.table("price_snapshots")
        .select("price_hash")
        .eq("store_item_id", store_item_id)
        .order("collected_at", desc=True)
        .limit(1)
        .execute()
    )
    if latest.data and latest.data[0]["price_hash"] == price_hash:
        return False  # 가격 변동 없음

    client.table("price_snapshots").insert(
        {
            "store_item_id": store_item_id,
            "raw_document_id": raw_document_id,
            "currency": currency,
            "regular_price": regular_price,
            "sale_price": sale_price,
            "final_price": final_price,
            "discount_percent": discount_percent,
            "sale_start_at": sale_start_at,
            "sale_end_at": sale_end_at,
            "is_on_sale": is_on_sale,
            "is_available": is_available,
            "price_data": price_data or {},
            "price_hash": price_hash,
        }
    ).execute()
    return True
