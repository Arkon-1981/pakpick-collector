"""HTTP 요청 공통 모듈 — 안전 수칙 내장.

이 모듈을 거치는 모든 요청에 다음 안전장치가 자동 적용된다:

  1. 사람 같은 요청 간격 — 기본 6~12초 사이 무작위 대기
     (사람이 페이지를 넘겨 보는 속도. 기계적인 일정 간격을 피함)
  2. robots.txt 준수 — 사이트가 금지한 구역은 요청하지 않음
  3. 요청 총량 상한 — 한 번 실행에서 MAX_REQUESTS_PER_RUN회 초과 금지
  4. 429(요청 과다) 응답의 Retry-After(대기 지시) 준수
  5. 연속 차단 감지 — 202/403/429가 연속 3회면 즉시 중단
     (차단당한 상태에서 계속 두드리면 더 강하게 차단되기 때문)
  6. 5xx 서버 오류는 지수 백오프로 최대 3회 재시도
"""
import random
import time

import requests

from common import config
from common.logging_util import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 15


class RobotsDisallowedError(Exception):
    """robots.txt 규칙상 금지된 주소."""


class BlockedError(Exception):
    """서버가 반복적으로 차단 응답을 보냄 — 이번 실행은 중단해야 함."""


class RequestBudgetExceededError(Exception):
    """실행당 요청 총량 상한 초과."""


class FetchResult:
    """요청 결과: 원본 바이트, 상태 코드, 응답 헤더를 함께 보관한다."""

    def __init__(self, url: str, status_code: int, content: bytes, headers: dict):
        self.url = url
        self.status_code = status_code
        self.content = content
        self.headers = headers

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _new_session() -> requests.Session:
    session = requests.Session()
    # 실제 크롬 브라우저가 보내는 헤더 구성 — 봇 차단을 줄이기 위함
    session.headers.update(
        {
            "User-Agent": config.USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="8"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
    )
    return session


_session = _new_session()
_last_request_at = 0.0
_request_count = 0
_consecutive_blocks = 0

BLOCK_STATUS_CODES = (202, 403, 429)


def polite_wait(api: bool = False) -> None:
    """요청 간 대기. HTML 스크래핑은 사람 속도(6~12초), 공식 API는 짧게(1.5~3초).

    api=True 는 스팀 검색 API·Xbox displaycatalog·닌텐도 가격 API처럼 프로그램
    호출용으로 공개된 엔드포인트에만 쓴다. 스토어 HTML 페이지에는 쓰지 않는다.
    """
    global _last_request_at
    base = config.API_REQUEST_DELAY_SECONDS if api else config.REQUEST_DELAY_SECONDS
    delay = base + random.uniform(0, base)  # base ~ 2*base 초
    elapsed = time.monotonic() - _last_request_at
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_at = time.monotonic()


def _check_budget() -> None:
    global _request_count
    _request_count += 1
    if _request_count > config.MAX_REQUESTS_PER_RUN:
        raise RequestBudgetExceededError(
            f"실행당 요청 상한({config.MAX_REQUESTS_PER_RUN}회) 초과 — 안전을 위해 중단"
        )


def _track_block(status_code: int) -> None:
    """차단성 응답이 연속되면 이번 실행을 포기한다."""
    global _consecutive_blocks
    if status_code in BLOCK_STATUS_CODES:
        _consecutive_blocks += 1
        if _consecutive_blocks >= config.CONSECUTIVE_BLOCK_LIMIT:
            raise BlockedError(
                f"차단성 응답(상태 {status_code})이 {_consecutive_blocks}회 연속 — "
                "서버 부담을 피하기 위해 이번 실행을 중단합니다"
            )
    else:
        _consecutive_blocks = 0


def fetch(
    url: str,
    *,
    extra_headers: dict | None = None,
    timeout: int = 30,
    check_robots: bool = True,
    api: bool = False,
) -> FetchResult:
    """URL 하나를 가져온다. 안전장치(간격·robots·상한·차단감지)가 자동 적용된다.

    api=True 면 공식 JSON API용 짧은 간격(1.5~3초)을 쓴다. HTML 스토어 페이지에는
    쓰지 말 것 — 그쪽은 사람 속도(6~12초)를 유지해야 한다.
    """
    if check_robots:
        from common import robots

        if not robots.is_allowed(url):
            raise RobotsDisallowedError(f"robots.txt 규칙상 금지된 주소: {url}")

    for attempt in range(1, MAX_RETRIES + 1):
        _check_budget()
        polite_wait(api)

        headers = dict(extra_headers) if extra_headers else {}
        try:
            response = _session.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            logger.warning("요청 실패 (%d/%d회): %s — %s", attempt, MAX_RETRIES, url, exc)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(BACKOFF_BASE_SECONDS * attempt)
            continue

        # 요청 과다: 서버의 대기 지시(Retry-After)를 그대로 따른다
        if response.status_code == 429:
            _track_block(429)
            retry_after = response.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 60
            logger.warning("429 요청 과다 — %d초 대기 후 재시도: %s", wait, url)
            if attempt == MAX_RETRIES:
                return FetchResult(url, 429, response.content, dict(response.headers))
            time.sleep(min(wait, 300))
            continue

        # 서버 오류: 잠시 쉬고 재시도
        if response.status_code in (500, 502, 503, 504):
            logger.warning(
                "상태코드 %d (%d/%d회): %s", response.status_code, attempt, MAX_RETRIES, url
            )
            if attempt == MAX_RETRIES:
                return FetchResult(url, response.status_code, response.content, dict(response.headers))
            time.sleep(BACKOFF_BASE_SECONDS * attempt)
            continue

        _track_block(response.status_code)
        return FetchResult(url, response.status_code, response.content, dict(response.headers))

    raise RuntimeError(f"요청 재시도 모두 실패: {url}")
