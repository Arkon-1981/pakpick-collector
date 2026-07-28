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


def _median(values: list[int]) -> int:
    """중앙값 (빈 리스트면 0). 전멸 방지 가드 기준선 계산용."""
    if not values:
        return 0
    s = sorted(values)
    return s[len(s) // 2]


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
        # 이번 실행에서 '확인한' 상품들의 내부 id.
        # 전멸 방지 가드는 이 개수를 본다 — 목록에서 새로 저장한 건수(products_found)만
        # 세면, 목록 크롤을 줄이고 공식 API로 시세를 갱신하는 방식에서 실제로는 수천 개를
        # 확인했는데도 '급감'으로 오인한다(닌텐도에서 실제로 발생).
        self.items_seen: set[int] = set()
        # 필드별 채움 건수. '상품 수'만 보는 가드로는 잡히지 않는 조용한 고장을 잡는다.
        # 예: 스팀 상세 API 스키마가 바뀌면 상품 수는 그대로인데 할인 종료일만 0건이 된다.
        self.field_hits: dict[str, int] = {}
        self.field_total: dict[str, int] = {}
        # 상품ID → 내부 id. 실행 시작 때 한 번 받아 두고 '기존 상품 찾기' 조회를 없앤다.
        # 실측(PS 1회): 그 조회만 2,137회였다. None = 아직 안 받음.
        self._id_cache: dict[str, int] | None = None
        # 버전 행 last_seen_at 갱신 대상. 상품마다 PATCH 하면 실측 1,674회가 더 붙는다.
        self._version_touches: list[int] = []

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

    def _ensure_id_cache(self) -> dict[str, int]:
        """상품ID→내부 id 매핑을 한 번만 받아 둔다 (실패하면 빈 캐시로 진행)."""
        if self._id_cache is None:
            try:
                self._id_cache = repository.item_id_map(self.platform, config.STORE_REGION)
                logger.info("[%s] 기존 상품 %d개 색인", self.platform, len(self._id_cache))
            except Exception:
                logger.exception("[%s] 상품 색인 실패 — 개별 조회로 진행", self.platform)
                self._id_cache = {}
        return self._id_cache

    def flush_deferred(self) -> None:
        """모아 둔 버전 last_seen_at 갱신을 한 번에 처리한다."""
        if not self._version_touches:
            return
        ids = list(dict.fromkeys(self._version_touches))
        self._version_touches.clear()
        try:
            repository.touch_versions_many(ids)
            logger.info("[%s] 버전 %d건 신선도 일괄 갱신", self.platform, len(ids))
        except Exception:
            logger.exception("[%s] 버전 신선도 갱신 실패", self.platform)

    def save_item(self, item: ParsedItem, raw_document_id: int | None) -> None:
        """파싱된 상품 1개를 DB에 저장한다."""
        try:
            item_id = repository.upsert_store_item(
                id_cache=self._ensure_id_cache(),
                touch_queue=self._version_touches,
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
            # 저장에 성공한 뒤에만 집계한다 (실패한 상품이 '확인됨'으로 세지면 안 된다)
            self.items_seen.add(item_id)
            self._count_fields(item)
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

    def _note(self, name: str, present: bool) -> None:
        self.field_total[name] = self.field_total.get(name, 0) + 1
        if present:
            self.field_hits[name] = self.field_hits.get(name, 0) + 1

    def _count_fields(self, item) -> None:
        """저장한 상품에서 '있어야 하는 값'이 실제로 채워졌는지 센다."""
        self._note("title", bool(item.title))
        self._note("image_url", bool(item.image_url))
        self._note("final_price", item.final_price is not None)
        # 할인 중인 상품만 분모로 삼는다 (정가 상품에 종료일이 없는 건 정상)
        if item.is_on_sale:
            self._note("sale_end_at", bool(item.sale_end_at))

    def field_rates(self) -> dict[str, float]:
        """필드별 채움률 (분모가 0이면 제외)"""
        return {
            k: self.field_hits.get(k, 0) / v
            for k, v in self.field_total.items()
            if v > 0
        }

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
    # 0.35로 완화: PS는 시간예산 안에서 카테고리를 회전하며 훑기 때문에 실행마다
    # 수집량이 정상적으로 출렁인다. 0.5면 이 정상 변동을 고장으로 오인해 실행이 실패한다.
    # (진짜 고장 = 0건~소수 는 0.35에서도 그대로 걸린다)
    GUARD_ANOMALY_RATIO = 0.35
    # 고장 판단 시 기존 상품을 몇 시간 창 기준으로 보호(웹 신선도 창과 맞춤)
    GUARD_PROTECT_HOURS = 48
    # 필드 채움률 최저선. 이 밑으로 떨어지면 '조용한 고장'으로 본다.
    # 상품 수 가드는 스키마 변경을 못 잡는다 — 상품은 그대로 들어오는데 특정 필드만
    # 비기 때문. 폴백이 없는 경로(스팀 상세 API, PS 단품 오퍼레이션)가 특히 위험하다.
    # 값은 실측 기준으로 넉넉히 잡았다(정상 변동으로 실패하지 않게).
    FIELD_FLOORS: dict[str, float] = {
        "title": 0.95,
        "image_url": 0.80,
        "final_price": 0.90,
    }
    # 채움률 판정에 필요한 최소 표본 (소량 수집에서 오탐 방지)
    FIELD_MIN_SAMPLE = 50

    # ----- 실행 진입점 -----

    def run(self) -> None:
        # 이상 감지 기준선: 최근 성공 수집 몇 건의 '중앙값'(단발성 등락에 덜 민감) + 직전 실행 상태
        recent_counts = repository.recent_good_counts(self.platform, 3)
        base_count = _median(recent_counts)
        prev_status = repository.last_finished_status(self.platform)
        self.run_id = repository.start_crawl_run(self.platform)

        # 1) 수집 실행 (수집 자체가 예외로 실패하면 failed 처리 후 재던짐)
        try:
            self.collect()
        except Exception as exc:
            logger.exception("[%s] 수집 실패", self.platform)
            self.flush_deferred()   # 여기까지 모아 둔 갱신은 살린다
            repository.finish_crawl_run(
                self.run_id,
                status="failed",
                pages_found=self.pages_found,
                products_found=self.products_found,
                errors_count=self.errors_count + 1,
                error_message=str(exc)[:2000],
            )
            raise

        # 모아 둔 갱신을 여기서 한 번에 내보낸다 (수집 중에는 요청을 아꼈다)
        self.flush_deferred()

        # 이번 실행에서 확인한 상품 수 (목록 저장 + API 갱신 등 모든 경로 합산, 중복 제외)
        confirmed = len(self.items_seen) or self.products_found

        # 2) 전멸 방지 가드: 이번 수집이 최근 기준선(중앙값) 대비 급감했으면 크롤러 고장으로 처리
        if (
            base_count >= self.GUARD_MIN_BASELINE
            and confirmed < base_count * self.GUARD_ANOMALY_RATIO
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
                f"이상 감지: 이번 수집 {confirmed}개 < 최근 기준선(중앙값) {base_count}개의 "
                f"{int(self.GUARD_ANOMALY_RATIO * 100)}% — 크롤러 고장 의심. "
                f"기존 상품 {protected}건 보호(last_seen 갱신)."
            )
            logger.error("[%s] %s", self.platform, msg)
            repository.finish_crawl_run(
                self.run_id,
                status="failed",
                pages_found=self.pages_found,
                products_found=confirmed,
                errors_count=self.errors_count + 1,
                error_message=msg[:2000],
            )
            raise RuntimeError(msg)

        # 3) 필드 채움률 검사 — 상품 수는 정상인데 특정 필드만 비는 '조용한 고장' 감지
        rates = self.field_rates()
        if rates:
            logger.info(
                "[%s] 필드 채움률 — %s",
                self.platform,
                " · ".join(
                    f"{k} {v:.0%}({self.field_hits.get(k,0)}/{self.field_total[k]})"
                    for k, v in sorted(rates.items())
                ),
            )
        low = [
            (k, rates[k], floor)
            for k, floor in self.FIELD_FLOORS.items()
            if k in rates
            and self.field_total.get(k, 0) >= self.FIELD_MIN_SAMPLE
            and rates[k] < floor
        ]
        if low:
            detail = ", ".join(f"{k} {r:.0%}(최저 {f:.0%})" for k, r, f in low)
            msg = (
                f"필드 채움률 이상: {detail} — 파서/스키마 변경 의심. "
                f"상품 수({confirmed}개)는 정상이라 수량 가드로는 잡히지 않는 유형."
            )
            logger.error("[%s] %s", self.platform, msg)
            repository.finish_crawl_run(
                self.run_id,
                status="failed",
                pages_found=self.pages_found,
                products_found=confirmed,
                errors_count=self.errors_count + 1,
                error_message=msg[:2000],
            )
            raise RuntimeError(msg)

        # 4) 정상 종료
        status = "success" if self.errors_count == 0 else "partial"
        repository.finish_crawl_run(
            self.run_id,
            status=status,
            pages_found=self.pages_found,
            products_found=confirmed,
            errors_count=self.errors_count,
        )
        logger.info(
            "[%s] 수집 완료 — 페이지 %d개, 확인 상품 %d개(신규 저장 %d), 오류 %d건",
            self.platform, self.pages_found, confirmed, self.products_found, self.errors_count,
        )
