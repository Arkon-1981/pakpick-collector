"""수집기 공통 골격.

모든 플랫폼 수집기는 이 클래스를 상속받아 아래 순서로 동작한다:

  1. crawl_runs에 실행 기록 시작
  2. 할인 목록 수집 → 원본 저장(Storage + raw_documents)
  3. 목록에서 상품별 정보 추출
  4. store_items / store_item_versions / price_snapshots 저장
  5. 실행 기록 마무리 (성공/실패, 상품 수, 오류 수)

핵심 원칙: **파싱에 실패해도 원본은 반드시 저장한다.**
나중에 파서만 고쳐서 과거 원본을 다시 처리할 수 있다.
"""
from dataclasses import dataclass, field

from common import config
from common.hashing import sha256_bytes
from common.http_client import FetchResult
from common.logging_util import get_logger
from db import repository
from storage import raw_storage

logger = get_logger(__name__)


@dataclass
class ParsedItem:
    """파서가 목록/상세에서 뽑아낸 상품 1개의 정보.

    extracted_data에는 파싱으로 얻은 **모든** 정보를 담는다.
    (필드로 정의 안 된 정보도 전부 — 나중에 골라 쓰기 위함)
    """
    store_product_id: str
    title: str | None = None
    store_url: str | None = None
    image_url: str | None = None

    # 가격 정보 (원 단위 숫자, 없으면 None)
    regular_price: float | None = None
    sale_price: float | None = None
    final_price: float | None = None
    discount_percent: float | None = None
    sale_start_at: str | None = None   # ISO 형식 문자열
    sale_end_at: str | None = None
    is_on_sale: bool = False
    is_available: bool = True
    currency: str = "KRW"

    # 파싱으로 얻은 전체 데이터 (제한 없음)
    extracted_data: dict = field(default_factory=dict)


class BaseCollector:
    platform: str = "unknown"

    def __init__(self):
        self.run_id: int | None = None
        self.pages_found = 0
        self.products_found = 0
        self.errors_count = 0

    # ----- 하위 클래스가 구현하는 부분 -----

    def collect(self) -> None:
        """플랫폼별 수집 로직. save_raw()와 save_item()을 이용해 저장한다."""
        raise NotImplementedError

    # ----- 공통 유틸 -----

    def save_raw(
        self,
        result: FetchResult,
        *,
        document_type: str,
        filename: str,
        store_product_id: str | None = None,
        content_type: str = "text/html",
    ) -> int | None:
        """원본 응답을 Storage에 올리고 raw_documents에 기록한다."""
        content_hash = sha256_bytes(result.content)
        path = raw_storage.build_path(self.platform, document_type, filename)
        try:
            raw_storage.upload_raw(path, result.content, content_type="application/gzip")
        except Exception as exc:
            self.errors_count += 1
            logger.exception("원본 업로드 실패: %s", path)
            repository.record_error(
                self.run_id, self.platform,
                source_url=result.url, error_type="storage_upload",
                error_message=str(exc),
            )
            return None

        return repository.record_raw_document(
            crawl_run_id=self.run_id,
            platform=self.platform,
            document_type=document_type,
            store_region=config.STORE_REGION,
            store_product_id=store_product_id,
            source_url=result.url,
            storage_bucket=config.RAW_BUCKET,
            storage_path=path,
            content_type=content_type,
            http_status=result.status_code,
            content_hash=content_hash,
            file_size=len(result.content),
            response_headers=result.headers,
        )

    def save_item(self, item: ParsedItem, raw_document_id: int | None) -> None:
        """파싱된 상품 1개를 DB에 저장한다."""
        try:
            item_id = repository.upsert_store_item(
                platform=self.platform,
                store_region=config.STORE_REGION,
                store_product_id=item.store_product_id,
                title=item.title,
                store_url=item.store_url,
                image_url=item.image_url,
                extracted_data=item.extracted_data,
                raw_document_id=raw_document_id,
            )
            repository.insert_price_snapshot_if_changed(
                store_item_id=item_id,
                raw_document_id=raw_document_id,
                currency=item.currency,
                regular_price=item.regular_price,
                sale_price=item.sale_price,
                final_price=item.final_price,
                discount_percent=item.discount_percent,
                sale_start_at=item.sale_start_at,
                sale_end_at=item.sale_end_at,
                is_on_sale=item.is_on_sale,
                is_available=item.is_available,
                price_data=item.extracted_data.get("price_raw", {}),
            )
            self.products_found += 1
        except Exception as exc:
            self.errors_count += 1
            logger.exception("상품 저장 실패: %s", item.store_product_id)
            repository.record_error(
                self.run_id, self.platform,
                source_url=item.store_url, error_type="db_save",
                error_message=str(exc),
                error_details={"store_product_id": item.store_product_id},
            )

    def record_parse_error(self, url: str | None, message: str, details: dict | None = None) -> None:
        self.errors_count += 1
        repository.record_error(
            self.run_id, self.platform,
            source_url=url, error_type="parse",
            error_message=message, error_details=details or {},
        )

    # ----- 전멸 방지 가드 설정 -----
    # 직전 성공 수집이 이만큼은 돼야 급감 비교가 의미 있음 (신생 플랫폼 오탐 방지)
    GUARD_MIN_BASELINE = 30
    # 직전 대비 이 비율 미만이면 '크롤러 고장'으로 간주 (사이트 구조 변경/봇 차단 등)
    GUARD_ANOMALY_RATIO = 0.5
    # 고장 판단 시 기존 상품을 몇 시간 창 기준으로 보호(웹 신선도 창과 맞춤)
    GUARD_PROTECT_HOURS = 48

    # ----- 실행 진입점 -----

    def run(self) -> None:
        # 이상 감지 기준선: 직전에 실제로 수집된 실행의 상품 수 + 직전 실행 상태
        baseline = repository.last_good_run(self.platform)
        prev_status = repository.last_finished_status(self.platform)
        self.run_id = repository.start_crawl_run(self.platform)

        # 1) 수집 실행 (수집 자체가 예외로 실패하면 failed 처리 후 재던짐)
        try:
            self.collect()
        except Exception as exc:
            logger.exception("[%s] 수집 실패", self.platform)
            repository.finish_crawl_run(
                self.run_id,
                status="failed",
                pages_found=self.pages_found,
                products_found=self.products_found,
                errors_count=self.errors_count + 1,
                error_message=str(exc)[:2000],
            )
            raise

        # 2) 전멸 방지 가드: 이번 수집이 직전 대비 급감했으면 크롤러 고장으로 처리
        base_count = (baseline or {}).get("products_found") or 0
        if (
            base_count >= self.GUARD_MIN_BASELINE
            and self.products_found < base_count * self.GUARD_ANOMALY_RATIO
        ):
            protected = 0
            # 직전 실행이 '실패'가 아니었을 때만 1회 보호 (연속 고장이면 자연 소멸시켜 stale 방지)
            if prev_status != "failed":
                try:
                    protected = repository.protect_recent_items(
                        self.platform, self.GUARD_PROTECT_HOURS
                    )
                except Exception:
                    logger.exception("[%s] 데이터 보호 갱신 실패", self.platform)
            msg = (
                f"이상 감지: 이번 수집 {self.products_found}개 < 직전 {base_count}개의 "
                f"{int(self.GUARD_ANOMALY_RATIO * 100)}% — 크롤러 고장 의심. "
                f"기존 상품 {protected}건 보호(last_seen 갱신)."
            )
            logger.error("[%s] %s", self.platform, msg)
            repository.finish_crawl_run(
                self.run_id,
                status="failed",
                pages_found=self.pages_found,
                products_found=self.products_found,
                errors_count=self.errors_count + 1,
                error_message=msg[:2000],
            )
            raise RuntimeError(msg)

        # 3) 정상 종료
        status = "success" if self.errors_count == 0 else "partial"
        repository.finish_crawl_run(
            self.run_id,
            status=status,
            pages_found=self.pages_found,
            products_found=self.products_found,
            errors_count=self.errors_count,
        )
        logger.info(
            "[%s] 수집 완료 — 페이지 %d개, 상품 %d개, 오류 %d건",
            self.platform, self.pages_found, self.products_found, self.errors_count,
        )
