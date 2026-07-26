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
) -> int:
    """상품 기본 정보를 저장/갱신하고, 내용이 바뀌었으면 버전 기록도 남긴다."""
    client = get_client()

    existing = (
        client.table("store_items")
        .select("id")
        .eq("platform", platform)
        .eq("store_region", store_region)
        .eq("store_product_id", store_product_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        item_id = existing.data[0]["id"]
        client.table("store_items").update(
            {
                "title": title,
                "store_url": store_url,
                "image_url": image_url,
                "current_data": extracted_data,
                "last_seen_at": _now(),
                "updated_at": _now(),
            }
        ).eq("id", item_id).execute()
    else:
        res = client.table("store_items").insert(
            {
                "platform": platform,
                "store_region": store_region,
                "store_product_id": store_product_id,
                "title": title,
                "store_url": store_url,
                "image_url": image_url,
                "current_data": extracted_data,
            }
        ).execute()
        item_id = res.data[0]["id"]

    # 상품 정보 버전 기록 — 내용 해시가 같으면 last_seen_at만 갱신
    data_hash = sha256_json(extracted_data)
    existing_version = (
        client.table("store_item_versions")
        .select("id")
        .eq("store_item_id", item_id)
        .eq("data_hash", data_hash)
        .limit(1)
        .execute()
    )
    if existing_version.data:
        client.table("store_item_versions").update(
            {"last_seen_at": _now()}
        ).eq("id", existing_version.data[0]["id"]).execute()
    else:
        client.table("store_item_versions").insert(
            {
                "store_item_id": item_id,
                "raw_document_id": raw_document_id,
                "data_hash": data_hash,
                "extracted_data": extracted_data,
            }
        ).execute()

    return item_id


# ---------------------------------------------------------------
# 가격 (price_snapshots)
# ---------------------------------------------------------------

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
