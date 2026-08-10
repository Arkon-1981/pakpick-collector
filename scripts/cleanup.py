"""오래된 원본 파일 정리.

원본(HTML/JSON)을 모두 보관하는 이유는 파서를 고친 뒤 과거 데이터를 다시 처리할 수
있어서다. 좋은 설계지만 보존 기한이 없으면 무한히 쌓인다.

실측 규모: 하루 2회 × 4플랫폼 × (스팀 배치 하나만 285KB) → 월 수 GB.
Supabase Storage 무료 한도는 1GB다. 한도를 넘기면 업로드가 실패하고,
그러면 수집 자체가 망가진다. 즉 이건 '나중에 할 일'이 아니라 시한폭탄이다.

정리 대상은 두 곳이다 — Storage 파일과 raw_documents 레코드. 둘을 같이 지워야
'DB에는 있는데 파일은 없는' 유령 레코드가 생기지 않는다.

  RAW_RETENTION_DAYS  보존 일수 (기본 45일)
  CLEANUP_DRY_RUN     "1" 이면 지우지 않고 대상만 보고한다
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import config  # noqa: E402
from common.logging_util import get_logger  # noqa: E402
from common.monitoring import init_sentry  # noqa: E402
from db.client import get_client  # noqa: E402
from storage import raw_storage  # noqa: E402

logger = get_logger(__name__)

RETENTION_DAYS = int(os.environ.get("RAW_RETENTION_DAYS", "45"))
DRY_RUN = os.environ.get("CLEANUP_DRY_RUN") == "1"
# 한 번에 처리할 레코드 수. 너무 크게 잡으면 요청이 타임아웃된다.
PAGE = 500
# Storage 삭제는 경로 목록을 한 번에 넘길 수 있다 (요청 수 절감)
DELETE_CHUNK = 100


def main() -> int:
    init_sentry("cleanup")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    logger.info(
        "[cleanup] %d일 이전(%s) 원본 정리 시작%s",
        RETENTION_DAYS, cutoff[:10], " (시험 실행)" if DRY_RUN else "",
    )

    client = get_client()
    total_files = total_rows = total_bytes = 0

    while True:
        try:
            res = (
                client.table("raw_documents")
                .select("id,storage_path,file_size")
                # 같은 내용이 다시 수집되면 last_collected_at 만 갱신된다(행을 새로 만들지
                # 않는다). 그래서 "마지막으로 본 시각"이 보존 기준이다.
                .lt("last_collected_at", cutoff)
                .order("id")
                .limit(PAGE)
                .execute()
            )
        except Exception:
            logger.exception("[cleanup] 대상 조회 실패")
            return 1

        rows = res.data or []
        if not rows:
            break

        paths = [r["storage_path"] for r in rows if r.get("storage_path")]
        ids = [r["id"] for r in rows]
        total_bytes += sum(int(r.get("file_size") or 0) for r in rows)

        if DRY_RUN:
            total_files += len(paths)
            total_rows += len(ids)
            logger.info("[cleanup] (시험) %d건 대상 — 예: %s", len(rows), paths[:2])
            break  # 시험 실행은 한 페이지만 보고 끝낸다

        # 1) Storage 파일 삭제. 실패해도 레코드는 지운다 —
        #    파일이 남는 것보다 '레코드는 있는데 파일이 없는' 상태가 더 헷갈린다.
        for i in range(0, len(paths), DELETE_CHUNK):
            chunk = paths[i : i + DELETE_CHUNK]
            try:
                raw_storage.delete_many(chunk)
                total_files += len(chunk)
            except Exception:
                logger.exception("[cleanup] 파일 삭제 실패 (%d건 건너뜀)", len(chunk))

        # 2) DB 레코드 삭제
        try:
            client.table("raw_documents").delete().in_("id", ids).execute()
            total_rows += len(ids)
        except Exception:
            logger.exception("[cleanup] 레코드 삭제 실패")
            return 1

        logger.info("[cleanup] %d건 정리 (누적 %d건)", len(ids), total_rows)

    logger.info(
        "[cleanup] 완료 — 파일 %d개, 레코드 %d건, 약 %.1fMB 확보%s",
        total_files, total_rows, total_bytes / 1_048_576,
        " (시험 실행이라 실제로는 지우지 않음)" if DRY_RUN else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
