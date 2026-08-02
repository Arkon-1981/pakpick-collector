"""닌텐도 한국 스토어 수집기.

대상: https://store.nintendo.co.kr/digital/sale?p=N  (할인 상품 목록)

⚠️ 닌텐도 스토어는 봇 차단이 있어서 일반 요청이 상태코드 202
(내용 없는 응답)로 막힐 수 있다. 그래서 2단계로 시도한다:

  1차: 일반 HTTP 요청 (브라우저 헤더로 변장) — 빠름
  2차: 1차가 막히면 Playwright로 실제 크롬 브라우저를 띄워서
       사람처럼 페이지를 열어 읽음 — 느리지만 확실

동작:
  1. 목록 페이지를 1페이지부터 순서대로 요청
  2. 각 페이지 원본 HTML 저장
  3. 상품 타일에서 정보 추출 → DB 저장
  4. 상품이 더 안 나오면 종료
"""
import json
import re
import time
from datetime import datetime, timezone

from collectors.base import BaseCollector
from common import config
from common.http_client import FetchResult, fetch
from common.logging_util import get_logger
from db import repository
from parsers.nintendo import (
    parse_detail_gallery,
    parse_list_page,
    parse_price_api,
    parse_schedule_page,
)

logger = get_logger(__name__)

# 목록은 브라우저(Playwright)로만 받을 수 있다. 대체 경로를 다 확인해 봤고 전부 막혔다:
#   ec.nintendo.com/api/{KR,JP}/search/sales  404 (예전 라이브러리 경로는 폐기됨)
#   store.nintendo.co.kr/graphql  202 — Magento GraphQL 이 있지만 WAF 가 막는다.
#     브라우저 안에서(챌린지 통과 상태) fetch 해도 202 였다 → 쿠키 문제가 아니다.
#   store.nintendo.co.kr/robots.txt  202 — 사이트 전체가 차단 상태
#   nintendo.com/kr/software/switch/  200 이지만 큐레이션 페이지(NSUID 18개), 카탈로그 아님
# 결론: '브라우저로 발견 + 공식 가격 API(api.ec.nintendo.com)로 갱신'이 유일한 방법이다.
LIST_URL = "https://store.nintendo.co.kr/digital/sale?p={page}"
# 발매 일정(신작·발매예정) — 스토어와 달리 봇 차단이 없어 일반 HTTP로 받는다
SCHEDULE_URL = "https://www.nintendo.com/kr/schedule"
# 무료 게임 목록 (스토어라 봇 차단 가능 → 필요시 브라우저)
FREE_URL = "https://store.nintendo.co.kr/digital/diigital-free"
# 베스트셀러(인기) 목록 — 홈 상단 'BEST' 링크. 목록의 정렬 파라미터는 전부 무시되고
# GraphQL 도 404 라(탐사 실측), 이 전용 페이지가 스토어의 유일한 인기 소스다.
BEST_URL = "https://store.nintendo.co.kr/digital/best-sellers?p={page}"
BEST_PAGES = 2  # 순위는 상위권만 의미 있다 — 스팀(98)·엑스박스(47)와 보조를 맞춘다
# 공식 가격 API — NSUID 50개 배치, 봇 차단 없음, 세일 종료일까지 제공
PRICE_API_URL = "https://api.ec.nintendo.com/v1/price?country=KR&lang=en&ids={ids}"
PRICE_API_BATCH = 50  # API가 50개 초과 시 "Over ids limit number" 반환
MAX_PAGES = 200  # 무한 루프 방지용 안전장치


def _pages_this_run() -> list[int]:
    """이번 실행에서 훑을 목록 페이지 번호들.

    목록 전체(50페이지 넘음)를 매번 훑으면 브라우저 크롤이라 80분이 걸린다.
    시세·할인 종료일은 이제 공식 가격 API가 이미 아는 상품 전부를 갱신하므로
    (last_seen_at 도 그때 찍힌다) 목록 크롤은 '새 상품 발견'만 하면 된다.

    그래서 한 실행에 NINTENDO_LIST_PAGES 페이지짜리 구간 하나만 보고,
    12시간 단위로 구간을 옮긴다 → 며칠에 걸쳐 전체를 덮는다.
    앞쪽만 계속 보면 뒤쪽에 새로 들어온 상품을 영영 발견하지 못한다.
    """
    count = max(1, config.NINTENDO_LIST_PAGES)
    span = max(count, config.NINTENDO_LIST_SPAN)
    blocks = max(1, span // count)
    block = int(time.time() // (12 * 3600)) % blocks
    start = block * count + 1
    pages = list(range(start, min(start + count, MAX_PAGES + 1)))
    logger.info("[nintendo] 목록 %d~%d페이지 (전체 %d페이지를 %d구간으로 회전)",
                pages[0], pages[-1], span, blocks)
    return pages

# 닌텐도 스토어 할인 목록의 Switch 2 플랫폼 필터(Amasty Shop By). label_platform=4679.
# 목록 타일엔 세대 표시가 없어서, SW2 필터 목록으로 'SW2에 올라온 게임'을 추린 뒤
# 그 게임들만 상세 페이지의 '대상 본체'(.label_platform)로 세대를 확정한다:
#   SW2 목록에 없음 → switch1 / SW2 목록에 있고 상세가 1·2 모두 → both / 2만 → switch2
# (SW1 필터 전체 크롤은 무거워서 쓰지 않는다 — SW2 후보만 상세 확인)
SW2_LABEL = "4679"
FILTER_URL_TEMPLATES = [
    "https://store.nintendo.co.kr/digital/sale?label_platform={opt}&p={page}",
    "https://store.nintendo.co.kr/digital/sale?amshopby%5Blabel_platform%5D%5B%5D={opt}&p={page}",
]


def _looks_nso_only(html: str) -> bool:
    """상세 HTML 이 'NSO 가입자 전용 무료'를 뜻하는가.

    실측 기반 문구 목록 — 스토어가 표현을 바꾸면 _collect_free 가 남기는
    'NSO 문구(비전용 판정)' 로그를 보고 여기만 갱신하면 된다.
    "온라인 플레이에는 가입이 필요" 같은 일반 멀티플레이 안내(거의 모든
    게임에 있음)와 헷갈리지 않게, 전용·한정 표현만 인정한다.
    """
    if "Nintendo Switch Online" not in html:
        return False
    needles = (
        "가입자 한정",             # TETRIS 99 류
        "가입자 전용",
        "멤버십 전용",
        "Nintendo Switch Online 전용",
        "+ 확장팩 전용",           # Nintendo Classics (GameCube 등)
        "확장팩 이용권",
    )
    return any(n in html for n in needles)


class NintendoCollector(BaseCollector):
    platform = "nintendo"

    # 목록 타일엔 이미지가 없는 경우가 있고, 발매 일정 항목은 가격이 아직 없다.
    # 그래서 기본값보다 느슨하게 둔다 — 정상 변동으로 실패하면 가드가 무의미해진다.
    FIELD_FLOORS = {"title": 0.95, "image_url": 0.60, "final_price": 0.70}

    def __init__(self):
        super().__init__()
        self._use_browser = False   # 일반 요청이 막히면 True로 전환
        self._pw = None             # Playwright 인스턴스
        self._browser = None
        self._page = None

    def collect(self) -> None:
        try:
            # 발매 일정(신작·발매예정)은 일반 HTTP로 받을 수 있어 가볍고 빠르다.
            # 브라우저가 필요한 할인 목록보다 먼저 수집해 실패 위험을 줄인다.
            self._collect_schedule()
            self._collect_pages()
            self._collect_free()
            # 인기는 다른 목록 뒤에 — upsert 가 current_data 를 통째로 덮어쓰므로,
            # 같은 실행의 앞 단계가 popular_rank 를 지우지 않게 순서로 보장한다.
            self._collect_popular()
            self._refresh_prices_via_api()
        finally:
            self._close_browser()

    def _collect_schedule(self) -> None:
        """발매 일정 페이지에서 신작·발매예정을 수집한다 (가격 없음, 출시일·세대만).

        스토어와 달리 봇 차단이 없어 일반 HTTP로 받는다. nsuid가 스토어 상품 ID와
        같은 값이라 기존 상품과 자연스럽게 합쳐진다.
        """
        try:
            result = fetch(SCHEDULE_URL)
            if result.status_code != 200:
                self.record_parse_error(SCHEDULE_URL, f"발매 일정 상태코드 {result.status_code}")
                return
            raw_doc_id = self.save_raw(
                result, document_type="list", filename="schedule.html",
                content_type="text/html",
            )
            self.pages_found += 1

            items = parse_schedule_page(result.text)
            now = datetime.now(timezone.utc)
            new_cnt = up_cnt = 0
            for item in items:
                raw = item.extracted_data.get("release_date")
                kind = "new"
                try:
                    if raw and datetime.fromisoformat(raw.replace("Z", "+00:00")) > now:
                        kind = "upcoming"
                except ValueError:
                    pass
                item.extracted_data["content_kind"] = kind
                new_cnt += kind == "new"
                up_cnt += kind == "upcoming"
                self.save_item(item, raw_doc_id)
            logger.info("[nintendo] 발매 일정 %d개 저장 (신작 %d / 발매예정 %d)",
                        len(items), new_cnt, up_cnt)
        except Exception:
            logger.exception("[nintendo] 발매 일정 수집 실패")

    def _refresh_prices_via_api(self) -> None:
        """공식 가격 API로 이미 아는 상품들의 시세를 갱신한다.

        스토어 HTML 크롤은 봇 차단 때문에 브라우저가 필요해 느리지만, 이 API는
        NSUID 50개를 한 번에 주고 차단도 없다. 무엇보다 **세일 종료일**을 주는데
        HTML 목록엔 없는 정보라, 이 단계에서만 닌텐도 종료일을 채울 수 있다.

        상품 자체(제목·이미지)는 이미 저장돼 있으므로 여기서는 가격 스냅샷만 남긴다.
        """
        # 상품ID → 내부 id 를 한 번에 받아 둔다.
        # 예전엔 상품마다 find_item_id + touch_last_seen 을 불러서 2,000개 갱신에
        # 요청이 4,000번 넘게 나갔다(이 단계만 34분). 목록으로 받으면 20여 회면 된다.
        try:
            id_map = repository.item_id_map(self.platform, config.STORE_REGION)
        except Exception:
            logger.exception("[nintendo] 기존 상품 ID 조회 실패")
            return
        ids = [i for i in id_map if i.isdigit()]
        if not ids:
            return

        logger.info("[nintendo] 가격 API로 %d개 시세 갱신 시작", len(ids))
        updated = ends = 0
        seen_item_ids: list[int] = []   # last_seen_at 은 마지막에 한 번에 갱신
        for i in range(0, len(ids), PRICE_API_BATCH):
            batch = ids[i : i + PRICE_API_BATCH]
            try:
                result = fetch(PRICE_API_URL.format(ids=",".join(batch)),
                               extra_headers={"Accept": "application/json"}, api=True)
                if result.status_code != 200:
                    logger.warning("[nintendo] 가격 API 상태코드 %s", result.status_code)
                    continue
                prices = parse_price_api(json.loads(result.text))
            except Exception:
                logger.exception("[nintendo] 가격 API 배치 실패 (%d~)", i)
                continue

            for nsuid, p in prices.items():
                try:
                    # 상품 정보(제목·이미지·content_kind)는 건드리지 않고 id만 쓴다.
                    # upsert_store_item 을 쓰면 넘긴 값으로 통째로 덮어써 기존 정보가 지워진다.
                    item_id = id_map.get(nsuid)
                    if item_id is None:
                        continue  # 아직 목록에서 못 본 상품 — 시세만 따로 만들진 않는다
                    seen_item_ids.append(item_id)
                    repository.insert_price_snapshot_if_changed(
                        store_item_id=item_id,
                        raw_document_id=None,
                        regular_price=p["regular_price"],
                        sale_price=p["final_price"] if p["is_on_sale"] else None,
                        final_price=p["final_price"],
                        discount_percent=p["discount_percent"],
                        sale_start_at=p["sale_start_at"],
                        sale_end_at=p["sale_end_at"],
                        is_on_sale=p["is_on_sale"],
                    )
                    updated += 1
                    if p["sale_end_at"]:
                        ends += 1
                except Exception:
                    logger.exception("[nintendo] 시세 저장 실패: %s", nsuid)

        # 가격 API가 응답한 상품은 스토어에 살아 있다는 뜻 → 신선도 갱신.
        # 목록에서 안 봤어도 '이번 실행에서 확인한 상품'이므로 가드 집계에도 넣는다.
        self.items_seen.update(seen_item_ids)
        try:
            repository.touch_last_seen_many(seen_item_ids)
        except Exception:
            logger.exception("[nintendo] last_seen 일괄 갱신 실패")
        logger.info("[nintendo] 가격 API 갱신 %d건 (종료일 %d건)", updated, ends)

    def _collect_free(self) -> None:
        """무료 게임 목록. 스토어라 봇 차단이 있어 필요시 브라우저 경로를 탄다."""
        try:
            result = self._get_page(FREE_URL)
            if result is None or result.status_code != 200:
                # 일반 요청이 막히면 브라우저로 한 번 더
                if not self._use_browser:
                    self._use_browser = True
                    result = self._get_page(FREE_URL)
            if result is None or result.status_code != 200:
                self.record_parse_error(FREE_URL, "무료 목록 접근 실패")
                return

            raw_doc_id = self.save_raw(
                result, document_type="list", filename="free.html",
                content_type="text/html",
            )
            self.pages_found += 1

            items = parse_list_page(result.text)

            # NSO 전용 판별 — 목록 타일엔 표기가 없어 상세를 확인한다.
            # 매 실행 24개 안팎 × 상세 1회 ≈ 4분. 캐시를 두면 문구 규칙을 고쳐도
            # 옛 판정이 남아서 못 고치므로, 그냥 매번 확인한다 (자가 치유).
            nso = 0
            for item in items:
                item.extracted_data["content_kind"] = "free"
                if item.store_url:
                    html = self._fetch_detail_server(item.store_url) or ""
                    if _looks_nso_only(html):
                        item.extracted_data["subscription"] = "nso"
                        nso += 1
                    else:
                        item.extracted_data["subscription"] = ""
                        # 문구 실측용: NSO 를 언급하는데 전용 판정이 아니면 주변 문구를
                        # 남긴다 — 표기 규칙이 바뀌었을 때 이 로그로 바로 잡는다.
                        i = html.find("Nintendo Switch Online")
                        if i >= 0:
                            snippet = re.sub(r"\s+", " ", html[max(0, i - 160):i + 200])
                            logger.info("[nintendo] NSO 문구(비전용 판정) %s: %s",
                                        item.title, snippet[:300])
                self.save_item(item, raw_doc_id)
            logger.info("[nintendo] 무료 게임 %d개 저장 (NSO 전용 %d개)", len(items), nso)
        except Exception:
            logger.exception("[nintendo] 무료 게임 수집 실패")

    def _collect_popular(self) -> None:
        """베스트셀러 목록에서 인기 순위를 수집한다. 순위가 곧 정보라 popular_rank 도 저장.

        upsert 가 current_data 를 통째로 덮어쓰므로, 다른 목록에서 채워 둔 값
        (content_kind·세대·갤러리·출시일)은 저장 직전에 읽어 와 다시 실어 준다.
        순위에서 빠진 상품은 다른 목록이 재저장하면서 자연히 popular_rank 가 지워지고,
        어디에도 안 잡히면 신선도 창에서 밀려난다.
        """
        try:
            keep = repository.fetch_item_meta(
                self.platform, config.STORE_REGION,
                ["content_kind", "platform_generation", "gallery", "release_date",
                 "subscription"],
            )
        except Exception:
            logger.exception("[nintendo] 기존 메타 조회 실패 — 인기 수집 생략")
            return

        rank = 0
        seen: set[str] = set()
        for page_no in range(1, BEST_PAGES + 1):
            url = BEST_URL.format(page=page_no)
            result = self._get_page(url)
            if result is None or result.status_code != 200:
                code = result.status_code if result else "요청 실패"
                self.record_parse_error(url, f"인기 목록 상태코드 {code}")
                break

            raw_doc_id = self.save_raw(
                result, document_type="list",
                filename=f"best-p{page_no}.html", content_type="text/html",
            )
            self.pages_found += 1

            items = parse_list_page(result.text)
            new_items = [i for i in items if i.store_product_id not in seen]
            if not new_items:
                break  # 마지막 페이지를 넘어가면 같은 상품이 반복된다

            for item in new_items:
                seen.add(item.store_product_id)
                rank += 1
                prev = keep.get(item.store_product_id) or {}
                for key in ("content_kind", "platform_generation", "release_date",
                            "subscription"):
                    if prev.get(key) is not None:
                        item.extracted_data[key] = prev[key]
                # 상세 보강으로 채운 갤러리(2장+)를 타일 1장짜리로 덮지 않는다
                prev_gallery = prev.get("gallery")
                if isinstance(prev_gallery, list) and len(prev_gallery) > 1:
                    item.extracted_data["gallery"] = prev_gallery
                item.extracted_data["popular_rank"] = rank
                self.save_item(item, raw_doc_id)
            logger.info("[nintendo] 인기 %d페이지: %d개", page_no, len(new_items))

        logger.info("[nintendo] 인기(베스트셀러) 총 %d개 저장", rank)

    def _collect_pages(self) -> None:
        seen_ids: set[str] = set()
        sw2_ids: set[str] | None = None  # SW2 필터 상품 ID. None = 미확보
        # (item, raw_doc_id) 를 모아뒀다가, 갤러리 보강은 목록을 다 모은 뒤
        # '할인율 상위'부터 처리한다. 피드가 할인율 순으로 보여주므로,
        # 목록 등장 순서가 아니라 할인율 순으로 보강해야 피드에 스크린샷이 채워진다.
        collected: list[tuple] = []

        # 워밍업: 사람처럼 첫 화면부터 방문 (쿠키 획득 → 차단 확률 감소)
        try:
            fetch("https://store.nintendo.co.kr/")
        except Exception:
            pass  # 워밍업 실패는 무시하고 진행

        for idx, page in enumerate(_pages_this_run()):
            url = LIST_URL.format(page=page)
            result = self._get_page(url)

            if result is None or result.status_code != 200:
                code = result.status_code if result else "요청 실패"
                self.record_parse_error(url, f"목록 페이지 상태코드 {code}")
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

            # 첫 페이지에서 상품이 하나도 안 나오면 → 봇 차단 가능성 → 브라우저로 재시도
            if not items and idx == 0 and not self._use_browser:
                logger.warning("[nintendo] 일반 요청으로는 상품이 안 보임 — 실제 브라우저로 전환")
                self._use_browser = True
                result = self._get_page(url)
                if result is not None and result.status_code == 200:
                    raw_doc_id = self.save_raw(
                        result, document_type="list",
                        filename=f"sale-p{page}-browser.html",
                        content_type="text/html",
                    )
                    items = parse_list_page(result.text)

            if not items:
                logger.info("[nintendo] %d페이지에 상품 없음 — 수집 종료", page)
                break

            # 첫 페이지 목록이 확보되면, SW2 필터로 'SW2 후보'를 식별한다.
            # (한 번만 실행. 실패해도 나머지 수집은 그대로 진행 → 세대 태깅만 생략)
            if sw2_ids is None:
                first_page_ids = {i.store_product_id for i in items}
                sw2_ids = self._collect_filter_ids(SW2_LABEL, first_page_ids)

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if not new_items:
                # 마지막 페이지를 넘어가면 같은 상품이 반복되는 경우가 있음
                logger.info("[nintendo] %d페이지는 전부 중복 — 수집 종료", page)
                break

            for item in new_items:
                seen_ids.add(item.store_product_id)
                collected.append((item, raw_doc_id))

            logger.info("[nintendo] %d페이지: 상품 %d개 수집", page, len(new_items))

        if not collected:
            return

        have_filter = bool(sw2_ids)
        # 갤러리 보강 대상: 할인율 상위 NINTENDO_GALLERY_MAX개 (피드에 뜨는 것과 일치)
        by_disc = sorted(
            collected, key=lambda t: t[0].discount_percent or 0, reverse=True
        )
        gallery_ids = {
            item.store_product_id
            for item, _ in by_disc[: config.NINTENDO_GALLERY_MAX]
        }

        for item, raw_doc_id in collected:
            pid = item.store_product_id
            # 세대: SW2 필터 소속이면 switch2, 아니면 switch1 (필터가 동작한 경우만).
            # ※ 닌텐도 KR 스토어는 크로스젠을 SW1/SW2 '별도 상품'으로 올려 상품별 '대상 본체'엔
            #    항상 한 기종만 표기된다(진단 확인: SW2 상품 81/81 = 'Nintendo Switch 2' 단일).
            #    따라서 상세 파싱 없이 필터 소속만으로 세대를 확정한다.
            if have_filter:
                item.extracted_data["platform_generation"] = "switch2" if pid in sw2_ids else "switch1"
            if pid in gallery_ids:
                self._enrich_detail(item)  # 갤러리(스크린샷)만 보강
            self.save_item(item, raw_doc_id)

        logger.info(
            "[nintendo] 총 %d개 저장 (갤러리 대상 상위 %d개)",
            len(collected), len(gallery_ids),
        )

    # -----------------------------------------------------------------
    # 상세 스크린샷 갤러리 보강
    # -----------------------------------------------------------------

    def _enrich_detail(self, item) -> None:
        """갤러리(스크린샷) 보강 — 상세 페이지 서버 HTML의 x-magento-init에서 추출.

        이미 갤러리(2장+)가 있으면 상세를 다시 받지 않고 재사용한다(신규분에만 비용).
        실패해도 수집은 안 깨진다.
        """
        pid = item.store_product_id
        if not item.store_url:
            return

        # 이미 채워진 갤러리 재사용
        try:
            g = repository.get_item_gallery("nintendo", config.STORE_REGION, pid)
        except Exception:
            g = None
        if g and len(g) > 1:
            item.extracted_data["gallery"] = g
            return

        server_html = self._fetch_detail_server(item.store_url)
        if not server_html:
            return
        # 원본(서버 HTML) 저장 — 갤러리 재처리용
        self.save_raw(
            FetchResult(item.store_url, 200, server_html.encode("utf-8"), {"x-fetched-via": "playwright-raw"}),
            document_type="detail",
            filename=f"detail-{pid}.html",
            store_product_id=pid,
            content_type="text/html",
        )
        shots = parse_detail_gallery(server_html)
        if shots:
            item.extracted_data["gallery"] = shots  # [대표, 스크린샷...]

    # -----------------------------------------------------------------
    # Switch 1 / Switch 2 세대 구분
    # -----------------------------------------------------------------

    def _collect_filter_ids(self, opt: str, all_page1_ids: set[str]) -> set[str]:
        """특정 세대 필터(label_platform=opt)로 목록을 긁어 상품 ID 집합을 만든다.

        필터가 안 먹히면(=필터 1페이지가 전체 목록 1페이지와 동일하거나 비어 있음)
        빈 집합을 돌려준다. 어떤 예외가 나도 빈 집합을 돌려 기존 수집이 깨지지 않게 한다.
        """
        try:
            for template in FILTER_URL_TEMPLATES:
                first = self._get_page(template.format(opt=opt, page=1))
                if first is None or first.status_code != 200:
                    continue
                first_ids = {i.store_product_id for i in parse_list_page(first.text)}
                # 필터가 무시되면 전체 목록과 같아진다 → 신뢰 불가, 다음 후보 시도
                if not first_ids or first_ids == all_page1_ids:
                    continue

                ids = set(first_ids)
                for page in range(2, MAX_PAGES + 1):
                    res = self._get_page(template.format(opt=opt, page=page))
                    if res is None or res.status_code != 200:
                        break
                    page_ids = {i.store_product_id for i in parse_list_page(res.text)}
                    if not page_ids or page_ids <= ids:
                        break  # 새 상품이 없으면 마지막 페이지
                    ids |= page_ids
                logger.info("[nintendo] 필터 %s 상품 %d개 식별", opt, len(ids))
                return ids

            logger.warning("[nintendo] 필터 %s 동작하지 않음", opt)
            return set()
        except Exception:
            logger.exception("[nintendo] 필터 %s 수집 실패", opt)
            return set()

    # -----------------------------------------------------------------
    # 페이지 가져오기 — 일반 요청 또는 실제 브라우저
    # -----------------------------------------------------------------

    def _get_page(self, url: str, raw_response: bool = False) -> FetchResult | None:
        if not self._use_browser:
            try:
                result = fetch(url)
            except Exception as exc:
                logger.warning("[nintendo] 일반 요청 실패: %s — 브라우저로 전환", exc)
                self._use_browser = True
                return self._get_page(url, raw_response)

            # 202/403 = 봇 차단 → 브라우저로 전환
            if result.status_code in (202, 403):
                logger.warning(
                    "[nintendo] 상태코드 %d (봇 차단 추정) — 실제 브라우저로 전환",
                    result.status_code,
                )
                self._use_browser = True
            else:
                return result  # 일반 요청 성공분은 이미 서버 HTML

        return self._fetch_with_browser(url, raw_response=raw_response)

    def _ensure_page(self):
        """Playwright 페이지를 준비한다 (이미지/동영상/폰트 차단, 1회 초기화)."""
        if self._page is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            context = self._browser.new_context(
                locale="ko-KR",
                user_agent=config.USER_AGENT,
                viewport={"width": 1280, "height": 900},
            )
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
            self._page = context.new_page()
        return self._page

    def _fetch_with_browser(self, url: str, raw_response: bool = False) -> FetchResult | None:
        """Playwright로 실제 크롬을 띄워 페이지를 읽는다 (봇 차단 우회용).

        raw_response=True 면 렌더링된 DOM 대신 서버 응답 본문을 돌려준다.
        안전장치: robots.txt 준수 + 사람 같은 간격 유지 + 이미지/동영상/폰트 미다운로드.
        """
        from common import robots
        from common.http_client import polite_wait

        if not robots.is_allowed(url):
            self.record_parse_error(url, "robots.txt 규칙상 금지된 주소")
            return None

        try:
            page = self._ensure_page()
            polite_wait()  # 사람 같은 간격 유지 (6~12초)
            response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            if raw_response:
                status = response.status if response else 200
                try:
                    body = response.text() if response else page.content()
                except Exception:
                    body = page.content()
                return FetchResult(url, status, body.encode("utf-8"), {"x-fetched-via": "playwright-raw"})

            # 목록용: 상품 타일이 그려질 때까지 최대 20초 대기 후 렌더링 DOM 사용.
            # 안 보이면 한 번 더 시도한다 — 봇 차단은 일시적인 경우가 많고, 여기서
            # 포기하면 그 페이지의 상품은 이번 실행에서 통째로 유실된다(실측: 매 실행
            # 2~3페이지가 이렇게 빠졌다). 재시도 전에 잠깐 쉬어 부담을 낮춘다.
            for attempt in (1, 2):
                try:
                    page.wait_for_selector("li.product-item, .product-item", timeout=20_000)
                    break
                except Exception:
                    if attempt == 1:
                        logger.info("[nintendo] 타일이 안 보임 — 잠시 후 재시도: %s", url)
                        page.wait_for_timeout(4_000)
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=45_000)
                        except Exception:
                            pass
                    else:
                        logger.warning(
                            "[nintendo] 재시도 후에도 상품 타일이 안 보임: %s", url
                        )

            html = page.content()
            return FetchResult(url, 200, html.encode("utf-8"), {"x-fetched-via": "playwright"})
        except Exception as exc:
            logger.exception("[nintendo] 브라우저 수집 실패: %s", url)
            self.record_parse_error(url, f"브라우저 수집 실패: {exc}")
            return None

    def _fetch_detail_server(self, url: str) -> str | None:
        """상세 페이지의 '서버 응답 HTML'을 얻는다 (갤러리 x-magento-init이 여기에만 있음).

        갤러리만 필요하므로 렌더링 DOM 대기 없이 서버 본문만 받는다(빠름).
        """
        from common import robots
        from common.http_client import polite_wait

        if not robots.is_allowed(url):
            self.record_parse_error(url, "robots.txt 규칙상 금지된 주소")
            return None
        try:
            page = self._ensure_page()
            polite_wait()
            response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                return response.text() if response else page.content()
            except Exception:
                return page.content()
        except Exception:
            logger.exception("[nintendo] 상세 로드 실패: %s", url)
            return None

    def _close_browser(self) -> None:
        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._browser = self._pw = self._page = None
