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
import time

from collectors.base import BaseCollector
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from db import repository
from parsers.playstation import (
    extract_next_data,
    parse_concepts_from_next_data,
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

# 신작·출시예정 카테고리 (할인과 같은 __NEXT_DATA__ 구조라 기존 파서를 그대로 쓴다).
# 규모가 작아(수십 개) 할인 크롤보다 **먼저** 수집한다 — 할인 크롤이 시간예산을
# 다 쓰면 뒤에 둔 단계는 영영 실행되지 않기 때문.
RELEASE_CATEGORIES = [
    ("new", "e1699f77-77e1-43ca-a296-26d08abacb0f"),       # 신규 발매
    ("upcoming", "3bf499d7-7acf-4931-97dd-2667494ee2c9"),  # 출시 예정
    ("free", "4dfd67ab-4ed7-40b0-a937-a549aece13d0"),      # 무료 게임
]
RELEASE_MAX_PAGES = 3  # 카테고리당 최대 페이지 (페이지당 ~24개)


class PlaystationCollector(BaseCollector):
    platform = "playstation"

    def collect(self) -> None:
        seen_ids: set[str] = set()
        # 이미 '저장된' 상품들(종료일 보강 후보). 저장은 페이지 단위로 즉시 하고,
        # 종료일 보강은 목록 크롤이 끝난 뒤 할인율 상위 N개만 상세로 덧입힌다.
        saved: list = []

        # 목록 크롤 시간예산. 이 시각을 넘기면 남은 카테고리/페이지를 건너뛰고
        # 종료일 보강 단계로 넘어간다(잡 타임아웃 전에 보강이 반드시 실행되도록).
        crawl_deadline = time.monotonic() + config.PS_CRAWL_BUDGET_SECONDS

        # 워밍업: 사람처럼 첫 화면부터 방문 (쿠키 획득 → 차단 확률 감소)
        try:
            fetch(f"{BASE}/ko-kr")
        except Exception:
            pass  # 워밍업 실패는 무시하고 진행

        # 0. 신작·출시예정 (소량) — 할인 크롤이 시간예산을 다 써도 반드시 수집되도록 먼저
        self._collect_releases(seen_ids)

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
            if time.monotonic() >= crawl_deadline:
                logger.info(
                    "[playstation] 크롤 시간예산(%d분) 소진 — 남은 카테고리 건너뛰고 종료일 보강으로",
                    config.PS_CRAWL_BUDGET_SECONDS // 60,
                )
                break
            self._collect_category(category_id, seen_ids, saved, crawl_deadline)

        # 4. 할인 종료일 보강 — 이미 저장된 상품의 최신 스냅샷에 in-place 갱신.
        #    크롤이 시간예산으로 조기 종료되어도 이 단계는 반드시 실행된다.
        self._enrich_end_dates(saved)

    def _collect_releases(self, seen_ids: set[str]) -> None:
        """신작·출시예정 카테고리를 수집한다 (할인과 동일한 __NEXT_DATA__ 구조).

        출시예정작은 아직 가격이 없거나 정가만 있어 is_on_sale=False 로 저장되므로
        할인 목록에는 섞이지 않는다. content_kind 로 종류를 표시한다.
        실패해도 이후 할인 수집은 계속되도록 예외를 흡수한다.
        """
        for kind, category_id in RELEASE_CATEGORIES:
            count = 0
            for page in range(1, RELEASE_MAX_PAGES + 1):
                url = f"{BASE}/ko-kr/category/{category_id}/{page}"
                try:
                    result = fetch(url)
                    if result.status_code != 200:
                        break
                    raw_doc_id = self.save_raw(
                        result, document_type="list",
                        filename=f"{kind}-{category_id}-p{page}.html",
                        content_type="text/html",
                    )
                    self.pages_found += 1

                    next_data = extract_next_data(result.text)
                    if not next_data:
                        break
                    items = parse_products_from_next_data(next_data)
                    if not items:
                        # '신규 발매'처럼 Product가 껍데기인 카테고리는 Concept에 실제 데이터가 있다
                        items = parse_concepts_from_next_data(next_data)
                    if not items:
                        break
                    new_items = [i for i in items if i.store_product_id not in seen_ids]
                    for item in new_items:
                        seen_ids.add(item.store_product_id)
                        item.extracted_data["content_kind"] = kind
                        self.save_item(item, raw_doc_id)
                    count += len(new_items)
                    if not new_items:
                        break  # 더 볼 게 없음
                except Exception:
                    logger.exception("[playstation] %s 카테고리 수집 실패 (page %d)", kind, page)
                    break
            logger.info("[playstation] %s %d개 저장", kind, count)

    def _collect_category(
        self, category_id: str, seen_ids: set[str], saved: list, deadline: float
    ) -> None:
        no_new_streak = 0  # 신규 상품 0건인 페이지가 연속으로 나온 횟수
        for page in range(1, MAX_CATEGORY_PAGES + 1):
            if time.monotonic() >= deadline:
                break  # 시간예산 소진 — 이 카테고리도 중단
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
            if not items:
                break  # 상품이 아예 없으면 카테고리 끝

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            for item in new_items:
                seen_ids.add(item.store_product_id)
                self.save_item(item, raw_doc_id)  # 즉시 저장
                saved.append(item)

            logger.info(
                "[playstation] 카테고리 %s %d페이지: 상품 %d개 (신규 %d)",
                category_id[:8], page, len(items), len(new_items),
            )

            # 카테고리끼리 상품이 크게 겹쳐, 신규가 0건인 페이지가 연속 2번이면
            # 이 카테고리는 사실상 소진된 것으로 보고 조기 종료한다(크롤 시간 대폭 단축).
            if not new_items:
                no_new_streak += 1
                if no_new_streak >= 2:
                    logger.info("[playstation] 카테고리 %s 연속 %d페이지 신규 없음 — 조기 종료",
                                category_id[:8], no_new_streak)
                    break
            else:
                no_new_streak = 0

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
