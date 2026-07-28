"""Xbox(마이크로소프트 스토어) 한국 수집기.

Xbox는 세 플랫폼 중 유일하게 2단계 방식이 필요하다:

  1단계 — 할인 상품 ID(BigId) 목록 얻기
    여러 경로의 결과를 '모두 합쳐서' 최대한 많이 확보한다:
      경로A: emerald(xbox.com 프론트도어) API — MS-CV 헤더 필요, 세일 채널 목록
      경로B: xbox.com / microsoft.com 세일 페이지 HTML에서 productId 추출
    (구형 reco-public / storeedgefd API는 각각 GH러너 IP 차단 / 빈 응답으로
     현재 사용 불가하여 제거했다.)

  2단계 — 상품 상세 조회 (인증 불필요 공개 JSON API)
      https://displaycatalog.mp.microsoft.com/v7.0/products
        ?bigIds=ID1,ID2,...&languages=ko-kr&market=KR
    → 상품명, 설명, 이미지, 가격, 할인 기간 등 모든 상세 정보

상품 ID(BigId)는 "9NKX70BBCDRN"처럼 9로 시작하는 12자리 영문+숫자다.
잘못된 ID가 섞여도 카탈로그 API가 무시하므로 안전하다.
"""
import json
import re

from collectors.base import BaseCollector
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.xbox import parse_catalog_products

logger = get_logger(__name__)

# ----- 1단계 경로들 -----
# emerald: xbox.com 웹 스토어가 실제로 쓰는 프론트도어 API.
# MS-CV 헤더가 필수(값은 형식만 맞으면 됨). 세일 채널별 상품 목록을 JSON으로 준다.
# PAGENUMBER 로 페이지네이션(페이지당 ~46개). 안 넘기면 첫 페이지 ~46개만 받는다.
MS_CV = "aaaaaaaaaaaaaaaa.0"
EMERALD_URL = (
    "https://emerald.xboxservices.com/xboxcomfd/browse"
    "?locale=ko-kr&channelKeyToBeUsedInResponse={channel}&PAGENUMBER={page}"
)
EMERALD_CHANNELS = ["game-deals", "ultimate-game-sale"]
EMERALD_MAX_PAGES = 60  # 무한 루프 방지 (페이지당 ~46개 → 최대 ~2,700개)

DEALS_PAGES = [
    "https://www.xbox.com/ko-KR/games/browse/game-deals",
    "https://www.xbox.com/ko-KR/games/browse/ultimate-game-sale",
    "https://www.microsoft.com/ko-kr/store/deals/games",
]

# 신작·출시예정 — microsoft.com 스토어 페이지는 상품 목록이 내장 JSON으로 서버 렌더링돼
# HTML만 받아도 productId를 뽑을 수 있다. (xbox.com/browse 계열은 클라이언트 렌더라 불가,
#  emerald API는 channelKey를 무시하고 항상 같은 목록을 줘서 신작 용도로 못 쓴다.)
RELEASE_PAGES = [
    ("upcoming", "https://www.microsoft.com/ko-kr/store/coming-soon/games/xbox"),
    ("new", "https://www.microsoft.com/ko-kr/store/new/games/xbox"),
    ("free", "https://www.microsoft.com/ko-kr/store/top-free/games/xbox"),
]

# ----- 2단계 -----
CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={ids}&languages=ko-kr&market=KR&MS-CV=pakpick"
)

CATALOG_BATCH = 20     # 상세 API 한 번에 조회할 상품 수

# BigId 형식: 12자리 대문자 영문+숫자 (9뿐 아니라 B/C 등으로도 시작함)
BIG_ID_RE = re.compile(r"\b(9[A-Z0-9]{11})\b")
# 페이지 내장 JSON의 productId 를 정확히 집는다 (접두어 무관)
PRODUCT_ID_RE = re.compile(r'"productId"\s*:\s*"([0-9A-Z]{12})"')
# microsoft.com 스토어는 productId 를 소문자로 내려준다 (displaycatalog 조회 시 대문자로 변환)
PRODUCT_ID_ANY_CASE_RE = re.compile(r'"productId"\s*:\s*"([0-9a-zA-Z]{12})"')


class XboxCollector(BaseCollector):
    platform = "xbox"

    def collect(self) -> None:
        product_ids = self._fetch_deal_ids()
        if not product_ids:
            raise RuntimeError(
                "할인 상품 ID를 어떤 경로로도 가져오지 못했습니다 "
                "(emerald API / 세일 페이지 HTML 모두 실패)"
            )
        logger.info("[xbox] 할인 상품 ID %d개 확보", len(product_ids))

        # 신작/출시예정/무료 표시를 상세 조회 **전에** 확보한다.
        # 예전에는 상세를 다 받은 뒤 이 목록을 훑으면서 '이미 받은 건 건너뛰기'를 했는데,
        # 엑스박스 목록 API가 할인만 주는 게 아니어서 신작·무료가 거의 다 이미 받은
        # 상품이었다 → 전부 건너뛰어져 content_kind 가 하나도 안 붙었다
        # (실측: new 50/50, free 50/50 이 중복 처리되어 신작·무료 탭이 비었다).
        kinds = self._fetch_release_kinds()

        # 목록에 없던 신작·무료만 뒤에 붙여, 상품 하나당 상세 조회는 한 번만 한다
        known = set(product_ids)
        all_ids = product_ids + [i for i in kinds if i not in known]
        if len(all_ids) > len(product_ids):
            logger.info("[xbox] 신작·무료 목록에서 %d개 추가", len(all_ids) - len(product_ids))

        for i in range(0, len(all_ids), CATALOG_BATCH):
            batch = all_ids[i : i + CATALOG_BATCH]
            self._fetch_catalog_batch(batch, batch_index=i // CATALOG_BATCH, kinds=kinds)

    def _fetch_release_kinds(self) -> dict[str, str]:
        """microsoft.com 스토어의 신작·출시예정·무료 목록에서 {상품ID: 종류}를 만든다.

        상세는 받지 않는다 — 여기서는 '무엇이 신작인가'만 알아내고, 실제 상세 조회는
        할인 목록과 합쳐 한 번에 처리한다(같은 상품을 두 번 받지 않기 위해).
        먼저 나온 종류를 유지한다(출시예정 → 신작 → 무료 순).
        """
        kinds: dict[str, str] = {}
        for kind, url in RELEASE_PAGES:
            try:
                result = fetch(url)
            except Exception as exc:
                logger.warning("[xbox] %s 페이지 접속 실패: %s", kind, exc)
                continue
            if result.status_code != 200:
                self.record_parse_error(url, f"{kind} 페이지 상태코드 {result.status_code}")
                continue

            self.save_raw(
                result, document_type="list",
                filename=f"{kind}-games.html", content_type="text/html",
            )
            self.pages_found += 1

            # displaycatalog는 대문자 BigId를 쓰는데 스토어 페이지는 소문자로 준다
            found = [i.upper() for i in dict.fromkeys(PRODUCT_ID_ANY_CASE_RE.findall(result.text))]
            added = 0
            for pid in found:
                if pid not in kinds:
                    kinds[pid] = kind
                    added += 1
            logger.info("[xbox] %s 상품 ID %d개 (신규 %d)", kind, len(found), added)
        return kinds

    # =================================================================
    # 1단계: 할인 상품 ID 목록 — 여러 경로를 순서대로 시도
    # =================================================================

    def _fetch_deal_ids(self) -> list[str]:
        """여러 경로의 상품 ID를 '모두 합쳐서' 최대한 많이 확보한다.

        과거엔 첫 성공 경로만 쓰고 멈췄지만, reco/storeedge API가 막히면서
        HTML 폴백 한 곳(≈54개)에만 의존해 커버리지가 급감했다.
        이제 emerald API + 여러 세일 페이지를 병합해 중복 제거 후 반환한다.
        """
        ids: list[str] = []

        # 1. emerald 웹 스토어 API (xbox.com이 실제로 쓰는 경로, 데이터 정확)
        try:
            ids.extend(self._ids_from_emerald())
        except Exception as exc:
            logger.warning("[xbox] emerald 경로 실패: %s", exc)
            self.record_parse_error(None, f"emerald 경로 실패: {exc}")

        # 이미 목표 수량을 채웠으면 HTML 폴백은 생략 (dedup 후 최종 상한 적용)
        unique_so_far = list(dict.fromkeys(ids))
        if len(unique_so_far) >= config.XBOX_MAX_ITEMS:
            return unique_so_far[: config.XBOX_MAX_ITEMS]

        # 2. 세일 페이지 HTML(여러 채널)에서 상품 ID 추출 (보강)
        try:
            ids.extend(self._ids_from_deals_pages())
        except Exception as exc:
            logger.warning("[xbox] 할인 페이지 경로 실패: %s", exc)
            self.record_parse_error(None, f"할인 페이지 경로 실패: {exc}")

        unique = list(dict.fromkeys(ids))[: config.XBOX_MAX_ITEMS]
        logger.info("[xbox] 확보한 고유 상품 ID %d개", len(unique))
        return unique

    def _ids_from_emerald(self) -> list[str]:
        """emerald(xbox.com 프론트도어) API에서 세일 채널 상품 ID를 페이지네이션으로 수집.

        PAGENUMBER 를 1부터 넘기며 목표 수량(XBOX_MAX_ITEMS)까지 모은다.
        (채널들이 사실상 같은 목록을 주므로, 한 채널로 목표를 채우면 다음 채널은 건너뛴다.)
        """
        seen: set[str] = set()
        ids: list[str] = []
        for channel in EMERALD_CHANNELS:
            if len(seen) >= config.XBOX_MAX_ITEMS:
                break
            for page in range(1, EMERALD_MAX_PAGES + 1):
                if len(seen) >= config.XBOX_MAX_ITEMS:
                    break
                url = EMERALD_URL.format(channel=channel, page=page)
                result = fetch(url, extra_headers={"Accept": "application/json", "MS-CV": MS_CV})
                if result.status_code != 200:
                    logger.warning("[xbox] emerald(%s p%d) 상태코드 %s", channel, page, result.status_code)
                    break

                self.save_raw(
                    result, document_type="list",
                    filename=f"emerald-{channel}-p{page}.json",
                    content_type="application/json",
                )
                self.pages_found += 1

                try:
                    data = json.loads(result.text)
                except json.JSONDecodeError:
                    self.record_parse_error(url, "emerald JSON 파싱 실패")
                    break

                page_ids: list[str] = []
                for summary in data.get("productSummaries") or []:
                    pid = summary.get("productId")
                    if pid:
                        page_ids.append(pid)
                for channel_obj in (data.get("channels") or {}).values():
                    for product in channel_obj.get("products") or []:
                        pid = product.get("productId")
                        if pid:
                            page_ids.append(pid)

                # 새 ID가 없으면 마지막 페이지로 간주하고 종료
                fresh = [p for p in page_ids if p not in seen]
                if not fresh:
                    break
                for pid in fresh:
                    seen.add(pid)
                    ids.append(pid)

            logger.info("[xbox] emerald(%s) 누적 상품 ID %d개", channel, len(ids))
        return ids

    def _ids_from_deals_pages(self) -> list[str]:
        """할인 페이지 HTML/내장 JSON에서 상품 ID 패턴을 직접 추출 (최후 수단)."""
        ids: list[str] = []
        for page_idx, url in enumerate(DEALS_PAGES):
            try:
                result = fetch(url)
            except Exception as exc:
                logger.warning("[xbox] 페이지 접속 실패: %s — %s", url, exc)
                continue
            if result.status_code != 200:
                continue

            # 페이지 인덱스로 파일명 구성 (누적 id 개수를 쓰면 0끼리 덮어써짐)
            self.save_raw(
                result, document_type="list",
                filename=f"deals-page-{page_idx}.html",
                content_type="text/html",
            )
            self.pages_found += 1

            # 내장 JSON의 productId 를 우선 추출(접두어 무관), 없으면 BigId 패턴 폴백
            found = PRODUCT_ID_RE.findall(result.text)
            if not found:
                found = BIG_ID_RE.findall(result.text.upper())
            ids.extend(found)
            logger.info("[xbox] %s 에서 ID 후보 %d개", url, len(found))

        return list(dict.fromkeys(ids))

    # =================================================================
    # 2단계: 상품 상세 (displaycatalog)
    # =================================================================

    def _fetch_catalog_batch(
        self, big_ids: list[str], *, batch_index: int, kinds: dict[str, str] | None = None
    ) -> None:
        """상품 ID 묶음의 상세를 받아 저장한다.

        kinds: {상품ID: "new"|"upcoming"|"free"}. 배치 안에 종류가 섞일 수 있어
        배치 단위가 아니라 상품 단위로 붙인다.
        """
        url = CATALOG_URL.format(ids=",".join(big_ids))
        result = fetch(url, extra_headers={"Accept": "application/json"}, api=True)

        if result.status_code != 200:
            self.record_parse_error(url, f"카탈로그 API 상태코드 {result.status_code}")
            return

        raw_doc_id = self.save_raw(
            result, document_type="detail",
            filename=f"catalog-batch{batch_index}.json",
            content_type="application/json",
        )

        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            self.record_parse_error(url, "카탈로그 JSON 파싱 실패")
            return

        for item in parse_catalog_products(data):
            kind = (kinds or {}).get(item.store_product_id)
            if kind:
                item.extracted_data["content_kind"] = kind
            self.save_item(item, raw_doc_id)
