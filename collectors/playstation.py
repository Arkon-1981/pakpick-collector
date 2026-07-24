"""플레이스테이션 한국 스토어 수집기.

대상: https://store.playstation.com/ko-kr/pages/deals  (할인 프로모션 모음)

PS 스토어 웹은 Next.js 기반이라, 페이지 HTML 안의
<script id="__NEXT_DATA__"> 태그에 상품 데이터가 JSON으로 통째로 들어 있다.
HTML 셀렉터 파싱보다 이 JSON을 읽는 것이 훨씬 안정적이다.

동작:
  1. /pages/deals 페이지 요청 → 원본 저장 → JSON에서 상품 추출
  2. deals 페이지에 연결된 카테고리(프로모션) 목록 URL 수집
  3. 각 카테고리의 페이지들을 순서대로 요청 → 원본 저장 → 상품 추출
"""
import re

from collectors.base import BaseCollector
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.playstation import extract_next_data, parse_products_from_next_data

logger = get_logger(__name__)

BASE = "https://store.playstation.com"
DEALS_URL = f"{BASE}/ko-kr/pages/deals"

# deals 페이지 안의 카테고리(프로모션) 링크 형식
CATEGORY_URL_RE = re.compile(r"/ko-kr/category/([0-9a-f-]{36})")
MAX_CATEGORY_PAGES = 100  # 카테고리당 최대 페이지 수 (안전장치)
MAX_CATEGORIES = 30       # 한 번에 수집할 최대 프로모션 수


class PlaystationCollector(BaseCollector):
    platform = "playstation"

    def collect(self) -> None:
        seen_ids: set[str] = set()

        # 1. deals 허브 페이지
        result = fetch(DEALS_URL)
        if result.status_code != 200:
            self.record_parse_error(DEALS_URL, f"deals 페이지 상태코드 {result.status_code}")
            return

        raw_doc_id = self.save_raw(
            result, document_type="list", filename="deals-hub.html",
            content_type="text/html",
        )
        self.pages_found += 1

        next_data = extract_next_data(result.text)
        if next_data:
            for item in parse_products_from_next_data(next_data):
                if item.store_product_id not in seen_ids:
                    seen_ids.add(item.store_product_id)
                    self.save_item(item, raw_doc_id)
        else:
            self.record_parse_error(DEALS_URL, "__NEXT_DATA__를 찾지 못함")

        # 2. 카테고리(프로모션) 링크 수집
        category_ids = list(dict.fromkeys(CATEGORY_URL_RE.findall(result.text)))
        logger.info("[playstation] 프로모션 카테고리 %d개 발견", len(category_ids))

        # 3. 각 카테고리를 페이지 단위로 순회
        for category_id in category_ids[:MAX_CATEGORIES]:
            self._collect_category(category_id, seen_ids)

    def _collect_category(self, category_id: str, seen_ids: set[str]) -> None:
        for page in range(1, MAX_CATEGORY_PAGES + 1):
            url = f"{BASE}/ko-kr/category/{category_id}/{page}"
            result = fetch(url)

            if result.status_code == 404:
                break  # 페이지 끝
            if result.status_code != 200:
                self.record_parse_error(url, f"카테고리 페이지 상태코드 {result.status_code}")
                break

            raw_doc_id = self.save_raw(
                result, document_type="list",
                filename=f"category-{category_id}-p{page}.html",
                content_type="text/html",
            )
            self.pages_found += 1

            next_data = extract_next_data(result.text)
            if not next_data:
                self.record_parse_error(url, "__NEXT_DATA__를 찾지 못함")
                break

            items = parse_products_from_next_data(next_data)
            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if not new_items:
                break  # 이 카테고리에서 더 이상 새 상품 없음

            for item in new_items:
                seen_ids.add(item.store_product_id)
                self.save_item(item, raw_doc_id)

            logger.info(
                "[playstation] 카테고리 %s %d페이지: 상품 %d개",
                category_id[:8], page, len(new_items),
            )
