"""닌텐도 한국 스토어 수집기.

대상: https://store.nintendo.co.kr/digital/sale?p=N  (할인 상품 목록)

이 페이지는 서버에서 HTML에 상품명·정가·할인가를 그대로 내려주므로
HTML 파싱만으로 수집이 가능하다 (세 플랫폼 중 가장 단순).

동작:
  1. 목록 페이지를 1페이지부터 순서대로 요청
  2. 각 페이지 원본 HTML 저장
  3. 상품 타일에서 정보 추출 → DB 저장
  4. 상품이 더 안 나오면 종료
"""
import re

from bs4 import BeautifulSoup

from collectors.base import BaseCollector, ParsedItem
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.nintendo import parse_list_page

logger = get_logger(__name__)

LIST_URL = "https://store.nintendo.co.kr/digital/sale?p={page}"
MAX_PAGES = 200  # 무한 루프 방지용 안전장치


class NintendoCollector(BaseCollector):
    platform = "nintendo"

    def collect(self) -> None:
        seen_ids: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            url = LIST_URL.format(page=page)
            result = fetch(url)

            if result.status_code != 200:
                self.record_parse_error(url, f"목록 페이지 상태코드 {result.status_code}")
                break

            # 원본은 파싱 성공 여부와 관계없이 무조건 저장
            raw_doc_id = self.save_raw(
                result,
                document_type="list",
                filename=f"sale-p{page}.html",
                content_type="text/html",
            )
            self.pages_found += 1

            items = parse_list_page(result.text)
            if not items:
                logger.info("[nintendo] %d페이지에 상품 없음 — 수집 종료", page)
                break

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if not new_items:
                # 마지막 페이지를 넘어가면 같은 상품이 반복되는 경우가 있음
                logger.info("[nintendo] %d페이지는 전부 중복 — 수집 종료", page)
                break

            for item in new_items:
                seen_ids.add(item.store_product_id)
                self.save_item(item, raw_doc_id)

            logger.info("[nintendo] %d페이지: 상품 %d개 처리", page, len(new_items))
