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
from db import repository
from parsers.nintendo import parse_detail_gallery, parse_detail_generation, parse_list_page

logger = get_logger(__name__)

LIST_URL = "https://store.nintendo.co.kr/digital/sale?p={page}"
MAX_PAGES = 200  # 무한 루프 방지용 안전장치

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
        enriched = 0  # 갤러리 보강한 상품 수 (상위 NINTENDO_GALLERY_MAX개만)
        sw2_ids: set[str] | None = None  # SW2 필터 상품 ID. None = 미확보

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

            # 1페이지 목록이 확보되면, SW2 필터로 'SW2 후보'를 식별한다.
            # (한 번만 실행. 실패해도 나머지 수집은 그대로 진행 → 세대 태깅만 생략)
            if sw2_ids is None:
                page1_ids = {i.store_product_id for i in items}
                sw2_ids = self._collect_filter_ids(SW2_LABEL, page1_ids)

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if not new_items:
                # 마지막 페이지를 넘어가면 같은 상품이 반복되는 경우가 있음
                logger.info("[nintendo] %d페이지는 전부 중복 — 수집 종료", page)
                break

            have_filter = bool(sw2_ids)
            for item in new_items:
                pid = item.store_product_id
                seen_ids.add(pid)
                in_sw2 = pid in sw2_ids
                # SW2 후보가 아니면 그냥 switch1 (필터가 동작한 경우만 태깅)
                if have_filter and not in_sw2:
                    item.extracted_data["platform_generation"] = "switch1"
                want_gallery = enriched < config.NINTENDO_GALLERY_MAX
                # SW2 후보(세대 확정 필요) 또는 갤러리 대상이면 상세를 본다
                if in_sw2 or want_gallery:
                    self._enrich_detail(item, in_sw2=in_sw2, want_gallery=want_gallery)
                if want_gallery:
                    enriched += 1
                self.save_item(item, raw_doc_id)

            logger.info("[nintendo] %d페이지: 상품 %d개 처리", page, len(new_items))

    # -----------------------------------------------------------------
    # 상세 스크린샷 갤러리 보강
    # -----------------------------------------------------------------

    def _enrich_detail(self, item, in_sw2: bool, want_gallery: bool) -> None:
        """상세 페이지 1회 로드로 세대(대상 본체)와 갤러리(스크린샷)를 함께 채운다.

        - in_sw2: SW2 후보 → 상세의 '대상 본체'로 both/switch2 확정 (매 실행 재확인)
        - want_gallery: 갤러리 보강 대상 → 이미 갤러리(2장+) 있으면 상세 재로드 없이 재사용
        - 상세가 필요 없으면(둘 다 아님) 요청하지 않는다. 실패해도 수집은 안 깨진다.
        """
        pid = item.store_product_id

        # 갤러리 재사용 가능 여부 확인 (있으면 상세 재로드 없이 씀)
        reusable_gallery = None
        if want_gallery:
            try:
                g = repository.get_item_gallery("nintendo", config.STORE_REGION, pid)
            except Exception:
                g = None
            if g and len(g) > 1:
                reusable_gallery = g

        # 세대 확정은 상세 HTML이 필요(SW2 후보). 갤러리는 재사용 불가할 때만 상세 필요.
        need_detail = in_sw2 or (want_gallery and reusable_gallery is None)
        html = None
        if need_detail and item.store_url:
            try:
                # 서버 응답 HTML(렌더 전)이어야 갤러리 스크립트·대상 본체가 온전히 들어 있다
                result = self._get_page(item.store_url, raw_response=True)
            except Exception:
                logger.exception("[nintendo] 상세 로드 실패: %s", item.store_url)
                result = None
            if result is not None and result.status_code == 200:
                self.save_raw(
                    result, document_type="detail",
                    filename=f"detail-{pid}.html",
                    store_product_id=pid,
                    content_type="text/html",
                )
                html = result.text

        # 세대: SW2 후보는 상세로 both/switch2 판별 (판별 실패 시 최소 switch2)
        if in_sw2:
            gen = parse_detail_generation(html) if html else None
            item.extracted_data["platform_generation"] = gen or "switch2"

        # 갤러리
        if want_gallery:
            if reusable_gallery is not None:
                item.extracted_data["gallery"] = reusable_gallery
            elif html:
                shots = parse_detail_gallery(html)
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

    def _fetch_with_browser(self, url: str, raw_response: bool = False) -> FetchResult | None:
        """Playwright로 실제 크롬을 띄워 페이지를 읽는다 (봇 차단 우회용).

        raw_response=True 면 렌더링된 DOM(page.content()) 대신 **서버 응답 본문**을
        돌려준다. 상세 갤러리 데이터(x-magento-init)는 서버 HTML에만 있고,
        렌더링 DOM에는 requireJS가 제거해 없기 때문이다.

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
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            # 상세 갤러리용: 서버 응답 본문(렌더링 전 HTML)을 그대로 사용
            if raw_response:
                status = response.status if response else 200
                try:
                    body = response.text() if response else self._page.content()
                except Exception:
                    body = self._page.content()
                return FetchResult(url, status, body.encode("utf-8"), {"x-fetched-via": "playwright-raw"})

            # 목록용: 상품 타일이 그려질 때까지 최대 20초 대기 후 렌더링 DOM 사용
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
