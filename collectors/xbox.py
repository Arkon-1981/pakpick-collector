"""Xbox(마이크로소프트 스토어) 한국 수집기.

Xbox는 세 플랫폼 중 유일하게 2단계 방식이 필요하다:

  1단계 — 할인 상품 ID 목록 얻기
    xbox.com의 할인 목록 페이지는 자바스크립트로 그려져서 HTML 파싱이 안 됨.
    대신 마이크로소프트의 공개 추천 목록 API를 사용한다:
      https://reco-public.rec.mp.microsoft.com/channels/Reco/V8.0/Lists/
        Computed/Deal?Market=KR&Language=ko&ItemTypes=Game&deviceFamily=Windows.Xbox
    → 할인 중인 게임의 상품 ID(BigId) 목록을 JSON으로 반환

  2단계 — 상품 상세 조회 (인증 불필요 공개 JSON API)
      https://displaycatalog.mp.microsoft.com/v7.0/products
        ?bigIds=ID1,ID2,...&languages=ko-kr&market=KR
    → 상품명, 설명, 이미지, 가격, 할인 기간 등 모든 상세 정보

두 API 모두 마이크로소프트 스토어 앱이 실제로 사용하는 공개 엔드포인트다.
"""
import json

from collectors.base import BaseCollector
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.xbox import parse_catalog_products

logger = get_logger(__name__)

DEALS_LIST_URL = (
    "https://reco-public.rec.mp.microsoft.com/channels/Reco/V8.0/Lists/Computed/Deal"
    "?Market=KR&Language=ko&ItemTypes=Game&deviceFamily=Windows.Xbox"
    "&count={count}&skipItems={skip}"
)
CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={ids}&languages=ko-kr&market=KR&MS-CV=pakpick"
)

PAGE_SIZE = 200        # 목록 API 한 번에 가져올 개수
CATALOG_BATCH = 20     # 상세 API 한 번에 조회할 상품 수
MAX_TOTAL = 5000       # 안전장치


class XboxCollector(BaseCollector):
    platform = "xbox"

    def collect(self) -> None:
        product_ids = self._fetch_deal_ids()
        logger.info("[xbox] 할인 상품 ID %d개 확보", len(product_ids))

        # 20개씩 묶어서 상세 조회
        for i in range(0, len(product_ids), CATALOG_BATCH):
            batch = product_ids[i : i + CATALOG_BATCH]
            self._fetch_catalog_batch(batch, batch_index=i // CATALOG_BATCH)

    # ----- 1단계: 할인 상품 ID 목록 -----

    def _fetch_deal_ids(self) -> list[str]:
        ids: list[str] = []
        skip = 0

        while skip < MAX_TOTAL:
            url = DEALS_LIST_URL.format(count=PAGE_SIZE, skip=skip)
            result = fetch(url, extra_headers={"Accept": "application/json"})

            if result.status_code != 200:
                self.record_parse_error(url, f"할인 목록 API 상태코드 {result.status_code}")
                break

            # 원본 저장
            self.save_raw(
                result, document_type="list",
                filename=f"deals-skip{skip}.json",
                content_type="application/json",
            )
            self.pages_found += 1

            try:
                data = json.loads(result.text)
            except json.JSONDecodeError:
                self.record_parse_error(url, "할인 목록 JSON 파싱 실패")
                break

            items = data.get("Items") or []
            if not items:
                break

            for entry in items:
                big_id = entry.get("Id")
                if big_id:
                    ids.append(big_id)

            total = (data.get("PagingInfo") or {}).get("TotalItems")
            skip += len(items)
            if total is not None and skip >= int(total):
                break

        return list(dict.fromkeys(ids))  # 중복 제거, 순서 유지

    # ----- 2단계: 상품 상세 (displaycatalog) -----

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
