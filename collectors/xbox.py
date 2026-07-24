"""Xbox(마이크로소프트 스토어) 한국 수집기.

Xbox는 세 플랫폼 중 유일하게 2단계 방식이 필요하다:

  1단계 — 할인 상품 ID(BigId) 목록 얻기
    한 가지 경로가 막힐 수 있어서 여러 경로를 순서대로 시도한다:
      경로A: reco-public 추천 목록 API (구형, 막혔을 수 있음)
      경로B: storeedgefd 컬렉션 API (윈도우 스토어 앱이 쓰는 경로)
      경로C: xbox.com / microsoft.com 할인 페이지 HTML에서 상품 ID 추출

  2단계 — 상품 상세 조회 (인증 불필요 공개 JSON API)
      https://displaycatalog.mp.microsoft.com/v7.0/products
        ?bigIds=ID1,ID2,...&languages=ko-kr&market=KR
    → 상품명, 설명, 이미지, 가격, 할인 기간 등 모든 상세 정보

상품 ID(BigId)는 "9NKX70BBCDRN"처럼 9로 시작하는 12자리 영문+숫자다.
잘못된 ID가 섞여도 카탈로그 API가 무시하므로 안전하다.
"""
import json
import re

from collectors.base import BaseCollector
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.xbox import parse_catalog_products

logger = get_logger(__name__)

# ----- 1단계 경로들 -----
RECO_URL = (
    "https://reco-public.rec.mp.microsoft.com/channels/Reco/V8.0/Lists/Computed/Deal"
    "?Market=KR&Language=ko&ItemTypes=Game&deviceFamily=Windows.Xbox"
    "&count={count}&skipItems={skip}"
)
STOREEDGE_URL = (
    "https://storeedgefd.dsx.mp.microsoft.com/v9.0/recommendations/collections/Deal"
    "?Market=KR&Language=ko&ItemTypes=Game&deviceFamily=Windows.Xbox"
    "&count={count}&skipItems={skip}"
)
DEALS_PAGES = [
    "https://www.xbox.com/ko-KR/games/browse/game-deals",
    "https://www.microsoft.com/ko-kr/store/deals/games",
]

# ----- 2단계 -----
CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={ids}&languages=ko-kr&market=KR&MS-CV=pakpick"
)

PAGE_SIZE = 200        # 목록 API 한 번에 가져올 개수
CATALOG_BATCH = 20     # 상세 API 한 번에 조회할 상품 수
MAX_TOTAL = 5000       # 안전장치

# BigId 형식: 9로 시작하는 12자리 대문자 영문+숫자
BIG_ID_RE = re.compile(r"\b(9[A-Z0-9]{11})\b")


class XboxCollector(BaseCollector):
    platform = "xbox"

    def collect(self) -> None:
        product_ids = self._fetch_deal_ids()
        if not product_ids:
            raise RuntimeError(
                "할인 상품 ID를 어떤 경로로도 가져오지 못했습니다 "
                "(reco-public / storeedgefd / 할인 페이지 모두 실패)"
            )
        logger.info("[xbox] 할인 상품 ID %d개 확보", len(product_ids))

        # 20개씩 묶어서 상세 조회
        for i in range(0, len(product_ids), CATALOG_BATCH):
            batch = product_ids[i : i + CATALOG_BATCH]
            self._fetch_catalog_batch(batch, batch_index=i // CATALOG_BATCH)

    # =================================================================
    # 1단계: 할인 상품 ID 목록 — 여러 경로를 순서대로 시도
    # =================================================================

    def _fetch_deal_ids(self) -> list[str]:
        sources = [
            ("reco-public API", self._ids_from_list_api, RECO_URL),
            ("storeedgefd API", self._ids_from_list_api, STOREEDGE_URL),
            ("할인 페이지 HTML", self._ids_from_deals_pages, None),
        ]
        for name, func, arg in sources:
            try:
                ids = func(arg) if arg else func()
            except Exception as exc:
                logger.warning("[xbox] %s 경로 실패: %s — 다음 경로 시도", name, exc)
                self.record_parse_error(None, f"{name} 경로 실패: {exc}")
                continue
            if ids:
                logger.info("[xbox] '%s' 경로에서 상품 ID %d개 확보", name, len(ids))
                return ids
            logger.warning("[xbox] %s 경로에서 상품 ID 0개 — 다음 경로 시도", name)
        return []

    def _ids_from_list_api(self, url_template: str) -> list[str]:
        """reco-public / storeedgefd 형식의 목록 API에서 ID 수집."""
        ids: list[str] = []
        skip = 0

        while skip < MAX_TOTAL:
            url = url_template.format(count=PAGE_SIZE, skip=skip)
            result = fetch(url, extra_headers={"Accept": "application/json"})

            if result.status_code != 200:
                raise RuntimeError(f"상태코드 {result.status_code}")

            # 원본 저장
            self.save_raw(
                result, document_type="list",
                filename=f"deals-skip{skip}.json",
                content_type="application/json",
            )
            self.pages_found += 1

            data = json.loads(result.text)

            # 응답 구조가 조금씩 달라도 대응: Items 배열 또는 전체에서 ID 패턴 추출
            items = data.get("Items") or []
            batch_ids = [e.get("Id") for e in items if e.get("Id")]
            if not batch_ids:
                batch_ids = BIG_ID_RE.findall(result.text)

            if not batch_ids:
                break
            ids.extend(batch_ids)

            total = (data.get("PagingInfo") or {}).get("TotalItems")
            skip += max(len(items), len(batch_ids))
            if total is not None and skip >= int(total):
                break
            if not items:  # ID는 얻었지만 페이지 구조를 모르면 1페이지만
                break

        return list(dict.fromkeys(ids))

    def _ids_from_deals_pages(self) -> list[str]:
        """할인 페이지 HTML/내장 JSON에서 상품 ID 패턴을 직접 추출 (최후 수단)."""
        ids: list[str] = []
        for url in DEALS_PAGES:
            try:
                result = fetch(url)
            except Exception as exc:
                logger.warning("[xbox] 페이지 접속 실패: %s — %s", url, exc)
                continue
            if result.status_code != 200:
                continue

            self.save_raw(
                result, document_type="list",
                filename=f"deals-page-{len(ids)}.html",
                content_type="text/html",
            )
            self.pages_found += 1

            found = BIG_ID_RE.findall(result.text.upper())
            ids.extend(found)
            logger.info("[xbox] %s 에서 ID 후보 %d개", url, len(found))

        return list(dict.fromkeys(ids))

    # =================================================================
    # 2단계: 상품 상세 (displaycatalog)
    # =================================================================

    def _fetch_catalog_batch(self, big_ids: list[str], *, batch_index: int) -> None:
        url = CATALOG_URL.format(ids=",".join(big_ids))
        result = fetch(url, extra_headers={"Accept": "application/json"})

        if result.status_code != 200:
            self.record_parse_error(url, f"카탈로그 API 상태코드 {result.status_code}")
            return

        raw_doc_id = self.save_raw(
            result, document_type="detail",
            filename=f"catalog-batch{batch_index}.json",
            content_type="application/json",
        )

        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            self.record_parse_error(url, "카탈로그 JSON 파싱 실패")
            return

        for item in parse_catalog_products(data):
            self.save_item(item, raw_doc_id)
