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

from collectors.base import BaseCollector, ParsedItem
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from db import repository
from parsers.steam import (
    count_rows,
    parse_featured_items,
    parse_screenshots,
    parse_search_results_html,
)

logger = get_logger(__name__)

# globaltopsellers: 판매 상위 + specials=1: 할인 중 + cc=kr: 원화 + koreana: 한국어
SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start={start}&count={count}"
    "&specials=1&filter=globaltopsellers"
    "&cc=kr&l=koreana&infinite=1"
)
# 신작·출시예정 묶음 API (new_releases / coming_soon 섹션을 JSON으로 준다)
FEATURED_URL = (
    "https://store.steampowered.com/api/featuredcategories/?cc=kr&l=korean"
)
# 100% 할인(무료 배포) — 기간 한정으로 '소장 가능한 무료'. F2P(상시 무료)와 구분된다.
FREE_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=50&specials=1&maxprice=free"
    "&cc=kr&l=koreana&infinite=1"
)
# 상시 무료(F2P) 인기작 — category1=998(게임) + maxprice=free. 장르 페이지는
# 상품 마크업이 없어(data-ds-appid 0개) 검색 API를 쓴다.
F2P_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=50&category1=998&maxprice=free"
    "&filter=globaltopsellers&cc=kr&l=koreana&infinite=1"
)
# 상세(스크린샷) API — 갤러리 보강용
APPDETAILS_URL = (
    "https://store.steampowered.com/api/appdetails"
    "?appids={appid}&cc=kr&l=koreana&filters=screenshots"
)
PAGE_SIZE = 100


class SteamCollector(BaseCollector):
    platform = "steam"

    def collect(self) -> None:
        max_items = config.STEAM_MAX_ITEMS
        gallery_max = config.STEAM_GALLERY_MAX
        start = 0
        page_idx = 0
        processed = 0  # 전체 순번 (상위 gallery_max개만 갤러리 보강)
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
                if processed < gallery_max:
                    self._ensure_gallery(item)
                self.save_item(item, raw_doc_id)
                processed += 1

            # 스팀이 돌려준 실제 행 수만큼 다음 페이지로 이동 (count가 무시돼도 정확히 진행)
            start += rows_in_page
            page_idx += 1
            if total_count and start >= total_count:
                break

        logger.info(
            "[steam] 할인 수집 — 페이지 %d개, 상품 %d개 (전체 할인 %s개)",
            self.pages_found, self.products_found, total_count,
        )

        # 할인 외 소스: 신작 / 출시예정 / 무료 배포 (실패해도 할인 수집분은 보존)
        self._collect_featured()
        self._collect_free()

    def _collect_featured(self) -> None:
        """신작(new_releases)·출시예정(coming_soon)을 featuredcategories에서 가져온다."""
        try:
            result = fetch(FEATURED_URL, extra_headers={"Accept": "application/json"})
            if result.status_code != 200:
                self.record_parse_error(FEATURED_URL, f"featured API 상태코드 {result.status_code}")
                return
            raw_doc_id = self.save_raw(
                result, document_type="list", filename="featured.json",
                content_type="application/json",
            )
            self.pages_found += 1
            data = json.loads(result.text)
        except Exception as exc:
            logger.warning("[steam] featured 수집 실패: %s", exc)
            return

        for section, kind in (("new_releases", "new"), ("coming_soon", "upcoming")):
            items = (data.get(section) or {}).get("items") or []
            parsed = parse_featured_items(items, kind)
            for item in parsed:
                self.save_item(item, raw_doc_id)
            logger.info("[steam] %s(%s) %d개 저장", section, kind, len(parsed))

    def _collect_free(self) -> None:
        """무료 게임 2종: 100% 할인(기간 한정 배포)과 상시 무료(F2P)."""
        for label, url, filename, f2p in (
            ("무료 배포(100% 할인)", FREE_URL, "free.json", False),
            ("상시 무료(F2P)", F2P_URL, "f2p.json", True),
        ):
            try:
                result = fetch(url, extra_headers={"Accept": "application/json"})
                if result.status_code != 200:
                    self.record_parse_error(url, f"무료 검색 상태코드 {result.status_code}")
                    continue
                raw_doc_id = self.save_raw(
                    result, document_type="list", filename=filename,
                    content_type="application/json",
                )
                self.pages_found += 1
                data = json.loads(result.text)
            except Exception as exc:
                logger.warning("[steam] %s 수집 실패: %s", label, exc)
                continue

            items = parse_search_results_html(data.get("results_html") or "")
            for item in items:
                item.extracted_data["content_kind"] = "free"
                item.extracted_data["is_f2p"] = f2p  # 상시 무료 / 기간 한정 구분
                self.save_item(item, raw_doc_id)
            logger.info("[steam] %s %d개 저장", label, len(items))

    def _ensure_gallery(self, item: ParsedItem) -> None:
        """상위 인기작에 스크린샷 갤러리를 채운다.

        이미 저장돼 있던 갤러리(스크린샷 포함, 2장 이상)가 있으면 그대로 재사용해
        상세 API를 다시 부르지 않는다 → 신규 상품에만 요청 비용이 든다.
        """
        appid = item.store_product_id
        try:
            existing = repository.get_item_gallery("steam", config.STORE_REGION, appid)
        except Exception:
            existing = None
        if existing and len(existing) > 1:
            item.extracted_data["gallery"] = existing
            return

        url = APPDETAILS_URL.format(appid=appid)
        try:
            result = fetch(url, extra_headers={"Accept": "application/json"})
        except Exception as exc:
            logger.warning("[steam] 갤러리 조회 실패 %s: %s", appid, exc)
            return
        if result.status_code != 200:
            return

        self.save_raw(
            result, document_type="detail",
            filename=f"appdetails-{appid}.json",
            store_product_id=appid,
            content_type="application/json",
        )
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            return

        shots = parse_screenshots(data, appid, limit=6)
        if shots:
            header = item.image_url
            gallery = ([header] if header else []) + [s for s in shots if s != header]
            item.extracted_data["gallery"] = gallery[:6]
