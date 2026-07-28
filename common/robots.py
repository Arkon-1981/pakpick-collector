"""robots.txt(사이트의 로봇 출입 규칙) 확인 모듈.

사이트들은 /robots.txt 파일로 "자동화 프로그램은 이 구역에 들어오지 마세요"
라는 규칙을 공개한다. 수집 전에 이 규칙을 읽고 준수한다.

- robots.txt가 없거나 읽을 수 없으면 → 허용으로 간주 (일반 관례)
- 규칙상 금지된 주소면 → 요청하지 않고 건너뜀
- 설정(RESPECT_ROBOTS=false)으로 끌 수 있지만 기본은 켜짐
"""
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from common import config
from common.logging_util import get_logger

logger = get_logger(__name__)

# 호스트별 파서 캐시 (실행당 1번만 robots.txt를 읽는다)
_parsers: dict[str, RobotFileParser | None] = {}

# ---------------------------------------------------------------------------
# 공식 공개 Web API 예외 목록
# ---------------------------------------------------------------------------
# robots.txt는 본래 '검색엔진이 웹페이지를 색인하지 말라'는 규약이다. 그런데 일부
# 업체는 API 전용 호스트에도 `Disallow: /` 를 걸어둔다(api.steampowered.com이 그렇다).
# 그 호스트에 있는 건 색인할 페이지가 아니라 키 없이 공개된 JSON API뿐이고,
# 스토어 화면 자체가 브라우저에서 같은 API를 호출한다.
#
# 그래서 '호스트 + 경로 접두사'를 정확히 적은 항목만 예외로 둔다. 와일드카드도 없고
# 호스트 전체를 여는 것도 아니다. 요청량은 배치(50개/회)로 눌러 두었다.
API_ALLOWLIST: tuple[tuple[str, str], ...] = (
    # 스팀 스토어 공개 API — 출시일·할인 종료일·스크린샷 배치 조회
    ("api.steampowered.com", "/IStoreBrowseService/"),
)


def _in_api_allowlist(netloc: str, path: str) -> bool:
    host = netloc.split("@")[-1].split(":")[0].lower()
    return any(host == h and path.startswith(p) for h, p in API_ALLOWLIST)


def _get_parser(base: str) -> RobotFileParser | None:
    if base in _parsers:
        return _parsers[base]

    parser: RobotFileParser | None = None
    try:
        # 순환 임포트 방지를 위해 함수 안에서 임포트
        from common.http_client import fetch

        result = fetch(f"{base}/robots.txt", check_robots=False, timeout=15)
        if result.status_code == 200:
            parser = RobotFileParser()
            parser.parse(result.text.splitlines())
            logger.info("robots.txt 확인 완료: %s", base)
        else:
            logger.info("robots.txt 없음(상태 %d): %s — 허용으로 간주", result.status_code, base)
    except Exception as exc:
        logger.warning("robots.txt 읽기 실패: %s — 허용으로 간주 (%s)", base, exc)

    _parsers[base] = parser
    return parser


def is_allowed(url: str) -> bool:
    """이 URL을 수집해도 되는지 robots.txt 기준으로 판단한다."""
    if not config.RESPECT_ROBOTS:
        return True

    parts = urlparse(url)
    if _in_api_allowlist(parts.netloc, parts.path):
        return True

    base = f"{parts.scheme}://{parts.netloc}"
    parser = _get_parser(base)
    if parser is None:
        return True  # robots.txt가 없으면 허용

    allowed = parser.can_fetch(config.USER_AGENT, url)
    if not allowed:
        logger.warning("robots.txt 규칙상 금지된 주소 — 건너뜀: %s", url)
    return allowed
