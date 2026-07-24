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
from collectors.base import BaseCollector
from common import config
from common.http_client import FetchResult, fetch
from common.logging_util import get_logger
from parsers.nintendo import parse_list_page

logger = get_logger(__name__)

LIST_URL = "https://store.nintendo.co.kr/digital/sale?p={page}"
MAX_PAGES = 200  # 무한 루프 방지용 안전장치

# 닌텐도 스토어 할인 목록의 플랫폼 필터(Amasty Shop By).
# label_platform 옵션값: 4679 = Nintendo Switch 2, 4678 = Nintendo Switch(1)
# 목록 타일 자체에는 세대 표시가 없어서, Switch 2만 필터링한 목록을 따로 긁어
# 상품 ID 집합을 만든 뒤 각 상품에 세대를 태깅한다.
# URL 형식이 스토어 설정에 따라 다를 수 있어 후보를 순서대로 시도한다.
SW2_FILTER_URLS = [
    "https://store.nintendo.co.kr/digital/sale?label_platform=4679&p={page}",
    "https://store.nintendo.co.kr/digital/sale?amshopby%5Blabel_platform%5D%5B%5D=4679&p={page}",
]


class NintendoCollector(BaseCollector):
    platform = "nintendo"

    def __init__(self):
        super().__init__()
        self._use_browser = False   # 일반 요청이 막히면 True로 전환
        self._pw = None             # Playwright 인스턴스
        self._browser = None
        self._page = None

    def collect(self) -> None:
        try:
            self._collect_pages()
        finally:
            self._close_browser()

    def _collect_pages(self) -> None:
        seen_ids: set[str] = set()
        # Switch 2 상품 ID 집합. None = 아직 미확보, set() = 확보 실패(태깅 안 함)
        switch2_ids: set[str] | None = None

        # 워밍업: 사람처럼 첫 화면부터 방문 (쿠키 획득 → 차단 확률 감소)
        try:
            fetch("https://store.nintendo.co.kr/")
        except Exception:
            pass  # 워밍업 실패는 무시하고 진행

        for page in range(1, MAX_PAGES + 1):
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

            # 1페이지에서 상품이 하나도 안 나오면 → 봇 차단 가능성 → 브라우저로 재시도
            if not items and page == 1 and not self._use_browser:
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

            # 1페이지 상품 목록이 확보되면, 그걸 기준으로 Switch 2 세대를 식별한다.
            # (한 번만 실행. 실패해도 나머지 수집은 그대로 진행 → 세대 태깅만 생략)
            if switch2_ids is None:
                page1_ids = {i.store_product_id for i in items}
                switch2_ids = self._collect_switch2_ids(page1_ids)

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if not new_items:
                # 마지막 페이지를 넘어가면 같은 상품이 반복되는 경우가 있음
                logger.info("[nintendo] %d페이지는 전부 중복 — 수집 종료", page)
                break

            for item in new_items:
                seen_ids.add(item.store_product_id)
                # 세대 태깅: Switch 2 목록을 신뢰할 수 있을 때만 붙인다
                if switch2_ids:
                    gen = "switch2" if item.store_product_id in switch2_ids else "switch1"
                    item.extracted_data["platform_generation"] = gen
                self.save_item(item, raw_doc_id)

            logger.info("[nintendo] %d페이지: 상품 %d개 처리", page, len(new_items))

    # -----------------------------------------------------------------
    # Switch 1 / Switch 2 세대 구분
    # -----------------------------------------------------------------

    def _collect_switch2_ids(self, all_page1_ids: set[str]) -> set[str]:
        """Switch 2 전용 필터로 목록을 긁어 Switch 2 상품 ID 집합을 만든다.

        필터가 안 먹히면(=필터 목록 1페이지가 전체 목록 1페이지와 동일하거나 비어 있음)
        빈 집합을 돌려줘 세대 태깅을 건너뛴다. 어떤 예외가 나도 빈 집합을 돌려
        기존 수집이 절대 깨지지 않게 한다.
        """
        try:
            for template in SW2_FILTER_URLS:
                first = self._get_page(template.format(page=1))
                if first is None or first.status_code != 200:
                    continue
                first_ids = {i.store_product_id for i in parse_list_page(first.text)}
                # 필터가 무시되면 전체 목록과 같아진다 → 신뢰 불가, 다음 후보 시도
                if not first_ids or first_ids == all_page1_ids:
                    continue

                ids = set(first_ids)
                for page in range(2, MAX_PAGES + 1):
                    res = self._get_page(template.format(page=page))
                    if res is None or res.status_code != 200:
                        break
                    page_ids = {i.store_product_id for i in parse_list_page(res.text)}
                    if not page_ids or page_ids <= ids:
                        break  # 새 상품이 없으면 마지막 페이지
                    ids |= page_ids
                logger.info("[nintendo] Switch 2 상품 %d개 식별 (필터: %s)", len(ids), template)
                return ids

            logger.warning("[nintendo] Switch 2 필터가 동작하지 않음 — 세대 태깅 생략")
            return set()
        except Exception:
            logger.exception("[nintendo] Switch 2 목록 수집 실패 — 세대 태깅 생략")
            return set()

    # -----------------------------------------------------------------
    # 페이지 가져오기 — 일반 요청 또는 실제 브라우저
    # -----------------------------------------------------------------

    def _get_page(self, url: str) -> FetchResult | None:
        if not self._use_browser:
            try:
                result = fetch(url)
            except Exception as exc:
                logger.warning("[nintendo] 일반 요청 실패: %s — 브라우저로 전환", exc)
                self._use_browser = True
                return self._get_page(url)

            # 202/403 = 봇 차단 → 브라우저로 전환
            if result.status_code in (202, 403):
                logger.warning(
                    "[nintendo] 상태코드 %d (봇 차단 추정) — 실제 브라우저로 전환",
                    result.status_code,
                )
                self._use_browser = True
            else:
                return result

        return self._fetch_with_browser(url)

    def _fetch_with_browser(self, url: str) -> FetchResult | None:
        """Playwright로 실제 크롬을 띄워 페이지를 읽는다 (봇 차단 우회용).

        안전장치: robots.txt 준수 + 사람 같은 간격 유지 +
        이미지/동영상/폰트는 내려받지 않아 상대 서버 부담 최소화.
        """
        from common import robots
        from common.http_client import polite_wait

        if not robots.is_allowed(url):
            self.record_parse_error(url, "robots.txt 규칙상 금지된 주소")
            return None

        try:
            if self._page is None:
                from playwright.sync_api import sync_playwright

                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
                context = self._browser.new_context(
                    locale="ko-KR",
                    user_agent=config.USER_AGENT,
                    viewport={"width": 1280, "height": 900},
                )
                # 이미지·동영상·폰트 요청 차단 — 필요한 HTML만 받는다
                context.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ("image", "media", "font")
                    else route.continue_(),
                )
                self._page = context.new_page()

            polite_wait()  # 사람 같은 간격 유지 (6~12초)
            self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            # 상품 타일이 그려질 때까지 최대 20초 대기 (없어도 계속 진행)
            try:
                self._page.wait_for_selector("li.product-item, .product-item", timeout=20_000)
            except Exception:
                logger.warning("[nintendo] 브라우저에서도 상품 타일이 안 보임: %s", url)

            html = self._page.content()
            return FetchResult(
                url, 200, html.encode("utf-8"), {"x-fetched-via": "playwright"}
            )
        except Exception as exc:
            logger.exception("[nintendo] 브라우저 수집 실패: %s", url)
            self.record_parse_error(url, f"브라우저 수집 실패: {exc}")
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
