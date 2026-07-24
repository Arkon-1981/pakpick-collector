"""HTTP 요청 공통 모듈.

- 브라우저와 동일한 User-Agent 사용
- 요청 사이 대기 시간(딜레이)으로 스토어 서버 부담 최소화
- 429(요청 과다) / 5xx(서버 오류) 응답 시 지수 백오프로 재시도
"""
import time

import requests

from common import config
from common.logging_util import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 10


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


def fetch(url: str, *, extra_headers: dict | None = None, timeout: int = 30) -> FetchResult:
    """URL 하나를 가져온다. 재시도와 딜레이가 자동 적용된다."""
    global _last_request_at

    for attempt in range(1, MAX_RETRIES + 1):
        # 요청 간 최소 간격 유지
        elapsed = time.monotonic() - _last_request_at
        if elapsed < config.REQUEST_DELAY_SECONDS:
            time.sleep(config.REQUEST_DELAY_SECONDS - elapsed)

        headers = dict(extra_headers) if extra_headers else {}
        try:
            response = _session.get(url, headers=headers, timeout=timeout)
            _last_request_at = time.monotonic()
        except requests.RequestException as exc:
            logger.warning("요청 실패 (%d/%d회): %s — %s", attempt, MAX_RETRIES, url, exc)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(BACKOFF_BASE_SECONDS * attempt)
            continue

        # 요청 과다/서버 오류 → 잠시 쉬고 재시도
        if response.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "상태코드 %d (%d/%d회): %s", response.status_code, attempt, MAX_RETRIES, url
            )
            if attempt == MAX_RETRIES:
                return FetchResult(url, response.status_code, response.content, dict(response.headers))
            time.sleep(BACKOFF_BASE_SECONDS * attempt)
            continue

        return FetchResult(url, response.status_code, response.content, dict(response.headers))

    raise RuntimeError(f"요청 재시도 모두 실패: {url}")
