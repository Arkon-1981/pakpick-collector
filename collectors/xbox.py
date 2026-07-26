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
from common.http_client import fetch
from common.logging_util import get_logger
from parsers.xbox import parse_catalog_products

logger = get_logger(__name__)

# ----- 1단계 경로들 -----
# emerald: xbox.com 웹 스토어가 실제로 쓰는 프론트도어 API.
# MS-CV 헤더가 필수(값은 형식만 맞으면 됨). 세일 채널별 상품 목록을 JSON으로 준다.
MS_CV = "aaaaaaaaaaaaaaaa.0"
EMERALD_URL = (
    "https://emerald.xboxservices.com/xboxcomfd/browse"
    "?locale=ko-kr&channelKeyToBeUsedInResponse={channel}"
)
EMERALD_CHANNELS = ["game-deals", "ultimate-game-sale"]

DEALS_PAGES = [
    "https://www.xbox.com/ko-KR/games/browse/game-deals",
    "https://www.xbox.com/ko-KR/games/browse/ultimate-game-sale",
    "https://www.microsoft.com/ko-kr/store/deals/games",
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

        # 20개씩 묶어서 상세 조회
        for i in range(0, len(product_ids), CATALOG_BATCH):
            batch = product_ids[i : i + CATALOG_BATCH]
            self._fetch_catalog_batch(batch, batch_index=i // CATALOG_BATCH)

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

        # 2. 세일 페이지 HTML(여러 채널)에서 상품 ID 추출 (보강)
        try:
            ids.extend(self._ids_from_deals_pages())
        except Exception as exc:
            logger.warning("[xbox] 할인 페이지 경로 실패: %s", exc)
            self.record_parse_error(None, f"할인 페이지 경로 실패: {exc}")

        unique = list(dict.fromkeys(ids))
        logger.info("[xbox] 확보한 고유 상품 ID %d개", len(unique))
        return unique

    def _ids_from_emerald(self) -> list[str]:
        """emerald(xbox.com 프론트도어) API에서 세일 채널 상품 ID를 수집."""
        ids: list[str] = []
        for channel in EMERALD_CHANNELS:
            url = EMERALD_URL.format(channel=channel)
            result = fetch(url, extra_headers={"Accept": "application/json", "MS-CV": MS_CV})
            if result.status_code != 200:
                logger.warning("[xbox] emerald(%s) 상태코드 %s", channel, result.status_code)
                continue

            self.save_raw(
                result, document_type="list",
                filename=f"emerald-{channel}.json",
                content_type="application/json",
            )
            self.pages_found += 1

            try:
                data = json.loads(result.text)
            except json.JSONDecodeError:
                self.record_parse_error(url, "emerald JSON 파싱 실패")
                continue

            for summary in data.get("productSummaries") or []:
                pid = summary.get("productId")
                if pid:
                    ids.append(pid)
            for channel_obj in (data.get("channels") or {}).values():
                for product in channel_obj.get("products") or []:
                    pid = product.get("productId")
                    if pid:
                        ids.append(pid)

            logger.info("[xbox] emerald(%s)에서 상품 ID 수집 (누적 %d)", channel, len(ids))
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

    def _fetch_catalog_batch(self, big_ids: list[str], *, batch_index: int) -> None:
        url = CATALOG_URL.format(ids=",".join(big_ids))
        result = fetch(url, extra_headers={"Accept": "application/json"})

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
            self.save_item(item, raw_doc_id)
