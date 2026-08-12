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
import urllib.parse

from collectors.base import BaseCollector, ParsedItem
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from db import repository
from parsers.steam import (
    count_rows,
    parse_featured_items,
    parse_search_results_html,
    parse_store_items,
)

logger = get_logger(__name__)

# globaltopsellers: 판매 상위 + specials=1: 할인 중 + cc=kr: 원화 + koreana: 한국어
SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start={start}&count={count}"
    "&specials=1&filter=globaltopsellers&category1=998"
    "&cc=kr&l=koreana&infinite=1"
)
# 신작·출시예정 묶음 API (new_releases / coming_soon 섹션을 JSON으로 준다)
FEATURED_URL = (
    "https://store.steampowered.com/api/featuredcategories/?cc=kr&l=korean"
)
# 100% 할인(무료 배포) — 기간 한정으로 '소장 가능한 무료'. F2P(상시 무료)와 구분된다.
FREE_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=50&specials=1&maxprice=free&category1=998"
    "&cc=kr&l=koreana&infinite=1"
)
# 상시 무료(F2P) 인기작 — category1=998(게임) + maxprice=free. 장르 페이지는
# 상품 마크업이 없어(data-ds-appid 0개) 검색 API를 쓴다.
F2P_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=50&category1=998&maxprice=free"
    "&filter=globaltopsellers&cc=kr&l=koreana&infinite=1"
)
# 인기(판매 상위) — 할인 검색과 같은 페이지에서 specials=1(할인만) 조건만 뺀 것.
# 파서도 같은 것을 쓴다. 상위 100위면 '인기 탭'으로 충분하고 요청은 1회다.
POPULAR_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count=100"
    "&filter=globaltopsellers&category1=998"
    "&cc=kr&l=koreana&infinite=1"
)

# 배치 보강 API — appid 50개를 한 번에 받아 출시일·할인 종료일·퍼블리셔·스크린샷·
# 리뷰 요약·한국어 지원 여부를 모두 준다. 예전에는 상품당 appdetails를 1회씩 불러
# 스크린샷만 받았는데(150개 = 요청 150회), 이제 3회면 같은 일을 하고 정보는 더 많다.
GETITEMS_URL = (
    "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json={payload}"
)
GETITEMS_BATCH = 50
GETITEMS_REQUEST = {
    "include_basic_info": True,      # 퍼블리셔·개발사·짧은 소개
    "include_release": True,         # 출시일
    "include_screenshots": True,     # 갤러리 (appdetails 상품별 호출 대체)
    "include_reviews": True,         # 리뷰 요약 (평점 정렬/필터용)
    "include_supported_languages": True,  # 한국어 지원 여부
    "include_assets": True,          # 정확한 대표 이미지 주소 (조립하면 404가 난다)
}
PAGE_SIZE = 100


def _is_composed_header(url: str | None) -> bool:
    """appid 로 조립한 legacy 헤더 주소인가 (스팀이 준 해시 주소가 아닌).

    조립 주소는 `.../steam/apps/<appid>/header.jpg` 형태로, 그 상품에 파일이
    없으면 404 다. 스팀이 준 주소는 `store_item_assets/.../<해시>/header.jpg`.
    """
    return bool(url) and "/store_item_assets/" not in url and url.endswith("header.jpg")


class SteamCollector(BaseCollector):
    platform = "steam"

    # 스팀 할인은 스토어가 종료일을 항상 준다(실측 100%). 이게 무너지면
    # IStoreBrowseService 스키마 변경이라는 뜻이고, 폴백 경로가 없어 조용히 빈다.
    FIELD_FLOORS = {
        **BaseCollector.FIELD_FLOORS,
        "sale_end_at": 0.70,
    }

    def collect(self) -> None:
        max_items = config.STEAM_MAX_ITEMS
        start = 0
        page_idx = 0
        total_count = None

        while start < max_items:
            url = SEARCH_URL.format(start=start, count=PAGE_SIZE)
            result = fetch(url, extra_headers={"Accept": "application/json"}, api=True)
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

            page_items = parse_search_results_html(html)
            self._enrich(page_items)  # 페이지(100개) = 배치 2회
            for item in page_items:
                self.save_item(item, raw_doc_id)

            # 스팀이 돌려준 실제 행 수만큼 다음 페이지로 이동 (count가 무시돼도 정확히 진행)
            start += rows_in_page
            page_idx += 1
            if total_count and start >= total_count:
                break

        logger.info(
            "[steam] 할인 수집 — 페이지 %d개, 상품 %d개 (전체 할인 %s개)",
            self.pages_found, self.products_found, total_count,
        )

        # 할인 외 소스: 신작 / 출시예정 / 무료 배포 / 인기 (실패해도 할인 수집분은 보존)
        self._collect_featured()
        self._collect_free()
        # 인기는 맨 마지막 — upsert 가 current_data 를 통째로 덮어쓰므로,
        # 같은 실행의 앞 단계가 인기 표시를 지우지 않게 순서로 보장한다.
        self._collect_popular()

    def _collect_featured(self) -> None:
        """신작(new_releases)·출시예정(coming_soon)을 featuredcategories에서 가져온다."""
        try:
            result = fetch(FEATURED_URL, extra_headers={"Accept": "application/json"}, api=True)
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
            # 200 인데 섹션이 비면 '이 종류가 없어진 것'이 아니라 응답/마크업 문제다.
            # 조용히 넘기면 앞서 할인 목록이 지운 content_kind 를 아무도 되살리지
            # 못해 신작·출시예정 탭이 빈다 → 오류로 남겨 보이게 한다.
            if not parsed:
                logger.warning("[steam] featured %s(%s) 0건 — 실패로 기록", section, kind)
                self.record_parse_error(FEATURED_URL, f"featured {section} 0건 (응답/마크업 변경 의심)")
                continue
            self._enrich(parsed)
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
                result = fetch(url, extra_headers={"Accept": "application/json"}, api=True)
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
            if not items:
                # 무료 배포는 할인 목록과 겹쳐(100% 할인) 앞선 저장이 이미 free 표시를
                # 지웠다. 여기서 0건을 조용히 넘기면 무료 탭이 빈다.
                logger.warning("[steam] %s 0건 — 실패로 기록", label)
                self.record_parse_error(url, f"{label} 0건 (마크업/차단 의심)")
                continue
            self._enrich(items)
            for item in items:
                item.extracted_data["content_kind"] = "free"
                item.extracted_data["is_f2p"] = f2p  # 상시 무료 / 기간 한정 구분
                self.save_item(item, raw_doc_id)
            logger.info("[steam] %s %d개 저장", label, len(items))

    def _collect_popular(self) -> None:
        """판매 상위(인기). 순위가 곧 정보라 popular_rank 를 함께 저장한다.

        신작·무료 표시가 있는 상품이 인기에도 오르면 표시가 지워지는 문제가 있다
        (upsert 가 current_data 를 통째로 덮어씀) — 기존 content_kind 를 읽어 와
        다시 실어 준다. 순위에서 빠진 상품은 다른 목록이 다시 저장하면서 자연히
        popular_rank 가 지워지고, 어디에도 안 잡히면 신선도 창에서 밀려난다.
        """
        try:
            result = fetch(POPULAR_URL, extra_headers={"Accept": "application/json"}, api=True)
            if result.status_code != 200:
                self.record_parse_error(POPULAR_URL, f"인기 검색 상태코드 {result.status_code}")
                return
            raw_doc_id = self.save_raw(
                result, document_type="list", filename="popular.json",
                content_type="application/json",
            )
            self.pages_found += 1
            data = json.loads(result.text)
        except Exception as exc:
            logger.warning("[steam] 인기 수집 실패: %s", exc)
            return

        items = parse_search_results_html(data.get("results_html") or "")
        if not items:
            # 인기 순위는 매 실행 다시 쓰는 값이라, 0건을 정상으로 보면 앞선 할인
            # 저장이 지운 popular_rank 를 아무도 복구하지 못해 인기 탭이 빈다.
            logger.warning("[steam] 인기 0건 — 실패로 기록")
            self.record_parse_error(POPULAR_URL, "인기 0건 (마크업/차단 의심)")
            return
        self._enrich(items)
        keep = repository.fetch_item_meta(
            self.platform, config.STORE_REGION, ["content_kind", "is_f2p"]
        )
        for rank, item in enumerate(items, start=1):
            prev = keep.get(item.store_product_id) or {}
            if prev.get("content_kind"):
                item.extracted_data["content_kind"] = prev["content_kind"]
                if prev.get("is_f2p") is not None:
                    item.extracted_data["is_f2p"] = prev["is_f2p"]
            item.extracted_data["popular_rank"] = rank
            self.save_item(item, raw_doc_id)
        logger.info("[steam] 인기(판매 상위) %d개 저장", len(items))

    # ------------------------------------------------------------------
    # 배치 보강
    # ------------------------------------------------------------------

    # 보강으로만 채워지는 필드 — 배치가 실패하면 목록 수준 데이터로 덮여
    # 사라진다. 실패 상품은 DB 의 직전 값을 되살려 유실을 막는다.
    # (gallery 는 목록 파서가 [header] 1장을 채우므로 아래서 따로 처리)
    _ENRICH_KEYS = (
        "content_type", "release_date", "publishers",
        "developers", "short_description", "review", "korean", "is_f2p",
    )

    def _enrich(self, items: list[ParsedItem]) -> None:
        """GetItems로 items를 제자리 보강한다 (50개씩 묶어 요청).

        보강이 실패해도 목록에서 얻은 이름·가격은 그대로 저장된다 → 수집 자체는 계속.
        단, 배치가 실패한(=보강 못 한) 상품은 저장 시 current_data 가 통째로
        덮여 기존 갤러리·DLC표시·출시일이 지워지므로, DB 의 직전 값을 되살린다.
        """
        if not items:
            return
        by_id = {i.store_product_id: i for i in items if i.store_product_id.isdigit()}
        appids = list(by_id)
        enriched: set[str] = set()

        for offset in range(0, len(appids), GETITEMS_BATCH):
            chunk = appids[offset : offset + GETITEMS_BATCH]
            info_map = self._fetch_store_items(chunk)
            for appid, info in info_map.items():
                item = by_id.get(appid)
                if item is not None and self._apply_info(item, info):
                    enriched.add(appid)

        # 보강 실패분(missed)은 저장 계층의 current_data 병합이 직전 값을 되살린다
        # (_ENRICH_KEYS 가 전부 MERGE_FILL_KEYS 에 포함, 갤러리는 '더 긴 쪽' 규칙).
        # 예전엔 여기서 fetch_item_meta 를 또 불렀지만 이제 불필요하다.
        missed = [a for a in appids if a not in enriched]
        if missed:
            logger.info("[steam] 상세 보강 실패 %d개 — 저장 시 직전 값으로 병합됨", len(missed))

        if enriched:
            logger.info("[steam] 상세 보강 %d/%d건 (요청 %d회)",
                        len(enriched), len(appids),
                        (len(appids) + GETITEMS_BATCH - 1) // GETITEMS_BATCH)

    def _fetch_store_items(self, appids: list[str]) -> dict[str, dict]:
        payload = {
            "ids": [{"appid": int(a)} for a in appids],
            "context": {
                "language": "koreana",
                "country_code": config.STORE_REGION.upper(),
                "steam_realm": 1,
            },
            "data_request": GETITEMS_REQUEST,
        }
        url = GETITEMS_URL.format(payload=urllib.parse.quote(json.dumps(payload)))
        try:
            result = fetch(url, extra_headers={"Accept": "application/json"}, api=True)
        except Exception as exc:
            logger.warning("[steam] GetItems 요청 실패 (%d개): %s", len(appids), exc)
            return {}
        if result.status_code != 200:
            logger.warning("[steam] GetItems 상태코드 %s (%d개)", result.status_code, len(appids))
            return {}

        # 원본 보존: 파서를 고쳐도 과거 응답을 재처리할 수 있게 (배치라 문서 수가 적다)
        self.save_raw(
            result, document_type="detail",
            filename=f"getitems-{appids[0]}-{len(appids)}.json",
            content_type="application/json",
        )
        try:
            return parse_store_items(json.loads(result.text))
        except (json.JSONDecodeError, ValueError):
            logger.warning("[steam] GetItems 응답 파싱 실패 (%d개)", len(appids))
            return {}

    @staticmethod
    def _apply_info(item: ParsedItem, info: dict) -> bool:
        """보강 결과를 ParsedItem에 반영한다. 값이 있는 필드만 덮어쓴다."""
        if not info:
            return False
        data = item.extracted_data

        # 할인 종료일은 '할인 중'일 때만 의미가 있다 (지난 할인의 잔여 값 방지)
        if info.get("sale_end_at") and item.is_on_sale:
            item.sale_end_at = info["sale_end_at"]
        if not item.title and info.get("title"):
            item.title = info["title"]

        # DLC 표시 — 웹이 이 값으로 게임 목록에서 걸러낸다
        if info.get("content_type"):
            data["content_type"] = info["content_type"]
        # 목록에서 조립한 주소보다 스팀이 준 주소가 항상 옳다
        if info.get("image_url"):
            item.image_url = info["image_url"]

        for key in ("release_date", "publishers", "developers",
                    "short_description", "review", "korean"):
            if key in info:
                data[key] = info[key]
        if info.get("is_f2p"):
            data["is_f2p"] = True

        shots = info.get("screenshots")
        if shots:
            # 스팀이 assets.header 를 아직 안 준 신규 상품은 image_url 이 목록에서
            # appid 로 조립한 주소(.../apps/<appid>/header.jpg)로 남는데, 그 파일이
            # 없어서 404 가 난다(실측: appid 4630450 — header/capsule 둘 다 404).
            # 스크린샷은 있으므로 그걸 대표로 쓴다. 없는 주소를 대표로 두는 것보다
            # 실제 있는 그림이 낫다. 다음 수집에서 스팀이 header 를 주면 교체된다.
            if info.get("image_url") is None and _is_composed_header(item.image_url):
                item.image_url = shots[0]

            header = item.image_url
            gallery = ([header] if header else []) + [s for s in shots if s != header]
            data["gallery"] = gallery[:6]
        return True
