"""원본(HTML/JSON) 파일을 gzip 압축해서 Supabase Storage에 업로드한다.

저장 경로 규칙:
  {플랫폼}/{연도}/{월}/{일}/{시분}/{문서종류}/{파일이름}.gz
예:
  nintendo/2026/07/25/0900/list/sale-p1.html.gz
  xbox/2026/07/25/2100/detail/9NKX70BBCDRN.json.gz
"""
import gzip
from datetime import datetime, timezone, timedelta

from common import config
from common.logging_util import get_logger
from db.client import get_client

logger = get_logger(__name__)

KST = timezone(timedelta(hours=9))

_bucket_checked = False


def _ensure_bucket() -> None:
    """버킷이 없으면 비공개(private)로 자동 생성한다."""
    global _bucket_checked
    if _bucket_checked:
        return
    client = get_client()
    try:
        buckets = client.storage.list_buckets()
        names = {b.name if hasattr(b, "name") else b["name"] for b in buckets}
        if config.RAW_BUCKET not in names:
            client.storage.create_bucket(config.RAW_BUCKET, options={"public": False})
            logger.info("Storage 버킷 생성: %s", config.RAW_BUCKET)
    except Exception:
        # 이미 존재하거나 권한 문제 — 업로드 시점에 다시 오류가 나면 그때 잡는다
        logger.warning("버킷 확인/생성 중 경고 (계속 진행)", exc_info=True)
    _bucket_checked = True


def build_path(platform: str, document_type: str, filename: str, collected_at: datetime | None = None) -> str:
    at = (collected_at or datetime.now(KST)).astimezone(KST)
    return (
        f"{platform}/{at:%Y/%m/%d}/{at:%H%M}/{document_type}/{filename}.gz"
    )


def upload_raw(path: str, content: bytes, *, content_type: str = "application/gzip") -> str:
    """원본 바이트를 gzip으로 압축해 업로드하고 저장 경로를 반환한다."""
    _ensure_bucket()
    compressed = gzip.compress(content)
    client = get_client()
    client.storage.from_(config.RAW_BUCKET).upload(
        path,
        compressed,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    return path


def delete_many(paths: list[str]) -> int:
    """Storage 파일 여러 개를 한 번에 지운다 (보존 기한 정리용).

    경로 목록을 한 요청으로 넘길 수 있어, 파일마다 부르는 것보다 훨씬 가볍다.
    지울 게 없으면 아무것도 하지 않는다.
    """
    if not paths:
        return 0
    get_client().storage.from_(config.RAW_BUCKET).remove(paths)
    logger.info("원본 %d개 삭제", len(paths))
    return len(paths)
