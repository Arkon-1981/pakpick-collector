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
from parsers.nintendo import parse_detail_gallery, parse_list_page

logger = get_logger(__name__)

LIST_URL = "https://store.nintendo.co.kr/digital/sale?p={page}"
MAX_PAGES = 200  # 무한 루프 방지용 안전장치

# 닌텐도 스토어 할인 목록의 플랫폼 필터(Amasty Shop By).
# label_platform 옵션값: 4679 = Nintendo Switch 2, 4678 = Nintendo Switch(1)
# 목록 타일 자체에는 세대 표시가 없어서, 각 세대 필터 목록을 따로 긁어
# 상품 ID 집합을 만든 뒤 각 상품에 세대를 태깅한다:
#   SW1 필터에만 있음 → switch1 / SW2 필터에만 있음 → switch2 / 둘 다 있음 → both
# URL 형식이 스토어 설정에 따라 다를 수 있어 후보를 순서대로 시도한다.
SW1_LABEL = "4678"
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
        # (SW1 ID집합, SW2 ID집합). None = 아직 미확보
        gen_sets: tuple[set[str], set[str]] | None = None

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

            # 1페이지 목록이 확보되면, 그걸 기준으로 세대 필터(SW1/SW2)를 식별한다.
            # (한 번만 실행. 실패해도 나머지 수집은 그대로 진행 → 세대 태깅만 생략)
            if gen_sets is None:
                page1_ids = {i.store_product_id for i in items}
                sw2 = self._collect_filter_ids(SW2_LABEL, page1_ids)
                sw1 = self._collect_filter_ids(SW1_LABEL, page1_ids)
                gen_sets = (sw1, sw2)

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if not new_items:
                # 마지막 페이지를 넘어가면 같은 상품이 반복되는 경우가 있음
                logger.info("[nintendo] %d페이지는 전부 중복 — 수집 종료", page)
                break

            sw1_ids, sw2_ids = gen_sets
            have_filter = bool(sw1_ids or sw2_ids)
            for item in new_items:
                seen_ids.add(item.store_product_id)
                # 세대 태깅: 필터가 하나라도 동작했을 때만.
                #   둘 다 있음 → both / SW2만 → switch2 / 그 외 → switch1(기본)
                if have_filter:
                    pid = item.store_product_id
                    in1 = pid in sw1_ids
                    in2 = pid in sw2_ids
                    item.extracted_data["platform_generation"] = (
                        "both" if (in1 and in2) else "switch2" if in2 else "switch1"
                    )
                # 상위 인기작에 상세 스크린샷 갤러리 보강 (이미 있으면 재사용)
                if enriched < config.NINTENDO_GALLERY_MAX:
                    self._ensure_gallery(item)
                    enriched += 1
                self.save_item(item, raw_doc_id)

            logger.info("[nintendo] %d페이지: 상품 %d개 처리", page, len(new_items))

    # -----------------------------------------------------------------
    # 상세 스크린샷 갤러리 보강
    # -----------------------------------------------------------------

    def _ensure_gallery(self, item) -> None:
        """상품 상세 페이지에서 스크린샷 갤러리를 채운다 (캐러셀용).

        이미 갤러리(2장 이상)가 저장된 상품은 상세 페이지를 다시 열지 않고
        기존 갤러리를 그대로 재사용한다 → 신규 상품에만 비용이 든다.
        실패해도 기존 썸네일 1장은 그대로 남아 수집이 깨지지 않는다.
        """
        try:
            existing = repository.get_item_gallery(
                "nintendo", config.STORE_REGION, item.store_product_id
            )
        except Exception:
            existing = None
        if existing and len(existing) > 1:
            item.extracted_data["gallery"] = existing
            return

        url = item.store_url
        if not url:
            return
        try:
            # 서버 응답 HTML을 받아야 갤러리 스크립트(x-magento-init)가 들어 있다
            result = self._get_page(url, raw_response=True)
        except Exception:
            logger.exception("[nintendo] 상세 갤러리 로드 실패: %s", url)
            return
        if result is None or result.status_code != 200:
            return

        self.save_raw(
            result, document_type="detail",
            filename=f"detail-{item.store_product_id}.html",
            store_product_id=item.store_product_id,
            content_type="text/html",
        )
        shots = parse_detail_gallery(result.text)
        if shots:
            item.extracted_data["gallery"] = shots  # [대표(isMain), 스크린샷...]

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
