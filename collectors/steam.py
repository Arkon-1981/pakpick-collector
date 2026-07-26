"""스팀(Steam) 스토어 한국 할인 수집기.

스팀은 할인(specials) 상품이 매우 많으므로(수만 개, 대부분 소규모 게임),
검색 API를 '전 세계 베스트셀러(globaltopsellers)' 필터로 정렬해
상위 STEAM_MAX_ITEMS개(기본 800)만 수집한다 → 알 만한 인기 할인작 위주.

검색 API 한 번 호출(`/search/results/?...&infinite=1`)이 JSON으로
`results_html`(상품 목록 HTML)과 `total_count`를 돌려주고, 그 HTML 한 줄에
상품명·정가·할인가·할인율이 모두 들어 있어 **상품별 추가 요청 없이** 끝난다.
가격은 원화(cc=kr) 기준.
"""
import json

from collectors.base import BaseCollector
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.steam import count_rows, parse_search_results_html

logger = get_logger(__name__)

# globaltopsellers: 판매 상위 + specials=1: 할인 중 + cc=kr: 원화 + koreana: 한국어
SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start={start}&count={count}"
    "&specials=1&filter=globaltopsellers"
    "&cc=kr&l=koreana&infinite=1"
)
PAGE_SIZE = 100


class SteamCollector(BaseCollector):
    platform = "steam"

    def collect(self) -> None:
        max_items = config.STEAM_MAX_ITEMS
        start = 0
        page_idx = 0
        total_count = None

        while start < max_items:
            url = SEARCH_URL.format(start=start, count=PAGE_SIZE)
            result = fetch(url, extra_headers={"Accept": "application/json"})
            if result.status_code != 200:
                self.record_parse_error(url, f"검색 API 상태코드 {result.status_code}")
                break

            raw_doc_id = self.save_raw(
                result, document_type="list",
                filename=f"specials-{page_idx}.json",
                content_type="application/json",
            )
            self.pages_found += 1

            try:
                data = json.loads(result.text)
            except json.JSONDecodeError:
                self.record_parse_error(url, "검색 API JSON 파싱 실패")
                break

            html = data.get("results_html") or ""
            total_count = data.get("total_count") or total_count

            rows_in_page = count_rows(html)
            if rows_in_page == 0:
                break

            for item in parse_search_results_html(html):
                self.save_item(item, raw_doc_id)

            # 스팀이 돌려준 실제 행 수만큼 다음 페이지로 이동 (count가 무시돼도 정확히 진행)
            start += rows_in_page
            page_idx += 1
            if total_count and start >= total_count:
                break

        logger.info(
            "[steam] 수집 완료 — 페이지 %d개, 상품 %d개 (전체 할인 %s개)",
            self.pages_found, self.products_found, total_count,
        )
