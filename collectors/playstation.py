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
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from db import repository
from parsers.playstation import (
    extract_next_data,
    parse_detail_end_time,
    parse_products_from_next_data,
)

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
        # 이미 '저장된' 상품들(종료일 보강 후보). 저장은 페이지 단위로 즉시 하고,
        # 종료일 보강은 목록 크롤이 끝난 뒤 할인율 상위 N개만 상세로 덧입힌다.
        saved: list = []

        # 워밍업: 사람처럼 첫 화면부터 방문 (쿠키 획득 → 차단 확률 감소)
        try:
            fetch(f"{BASE}/ko-kr")
        except Exception:
            pass  # 워밍업 실패는 무시하고 진행

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
                    self.save_item(item, raw_doc_id)  # 즉시 저장(부분 실패해도 데이터 보존)
                    saved.append(item)
        else:
            self.record_parse_error(DEALS_URL, "__NEXT_DATA__를 찾지 못함")

        # 2. 카테고리(프로모션) 링크 수집
        category_ids = list(dict.fromkeys(CATEGORY_URL_RE.findall(result.text)))
        logger.info("[playstation] 프로모션 카테고리 %d개 발견", len(category_ids))

        # 3. 각 카테고리를 페이지 단위로 순회 (페이지마다 즉시 저장)
        for category_id in category_ids[:MAX_CATEGORIES]:
            self._collect_category(category_id, seen_ids, saved)

        # 4. 할인 종료일 보강 — 이미 저장된 상품의 최신 스냅샷에 in-place 갱신
        self._enrich_end_dates(saved)

    def _collect_category(self, category_id: str, seen_ids: set[str], saved: list) -> None:
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
            # 페이지에 상품이 아예 없어야 카테고리 끝 (여기서만 break).
            # 카테고리끼리 상품이 겹쳐 new_items가 비어도, 다음 페이지엔
            # 새 상품이 있을 수 있으므로 break 하지 않고 계속 넘긴다.
            if not items:
                break

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            for item in new_items:
                seen_ids.add(item.store_product_id)
                self.save_item(item, raw_doc_id)  # 즉시 저장
                saved.append(item)

            logger.info(
                "[playstation] 카테고리 %s %d페이지: 상품 %d개 (신규 %d)",
                category_id[:8], page, len(items), len(new_items),
            )

    def _enrich_end_dates(self, saved: list) -> None:
        """할인율 상위 N개 상품의 상세 페이지에서 할인 종료일을 받아 최신 스냅샷에 덧입힌다.

        목록 페이지엔 endTime이 없고 상세 페이지(Price 노드)에만 있다. 상품 자체는 이미
        저장돼 있으므로, 여기서는 최신 price_snapshot의 sale_end_at만 in-place 갱신한다.
        상세 fetch/갱신 실패나 요청상한 도달은 흡수한다(이미 저장된 데이터는 안전).
        """
        limit = config.PS_DETAIL_END_MAX
        if limit <= 0:
            return
        targets = [
            it for it in saved
            if it.is_on_sale and it.sale_end_at is None and it.store_url
        ]
        targets.sort(key=lambda it: it.discount_percent or 0, reverse=True)

        done = 0
        for item in targets[:limit]:
            try:
                res = fetch(item.store_url)
                if res.status_code != 200:
                    continue
                end_at = parse_detail_end_time(res.text, item.final_price)
                if end_at and repository.update_latest_sale_end(
                    self.platform, config.STORE_REGION, item.store_product_id, end_at
                ):
                    done += 1
            except Exception:
                # 요청상한/네트워크/파싱 실패는 무시 — 상품은 이미 저장됨
                logger.exception("[playstation] 종료일 보강 실패: %s", item.store_product_id)
                continue
        logger.info("[playstation] 할인 종료일 보강 %d건 (대상 상위 %d)", done, min(limit, len(targets)))
