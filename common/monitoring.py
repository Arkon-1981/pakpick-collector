"""에러 모니터링(Sentry) — 선택적.

SENTRY_DSN 환경변수가 있을 때만 켜지고, 없으면 전부 no-op 이라 로컬·키 없는
실행에서도 그대로 돈다 (TWITCH 키 패턴과 동일). 수집기가 조용히 실패하던 것을
Sentry 에 자동 수집해, 프로덕션에서야 눈치채던 문제를 알림으로 바꾼다.

트레이싱(성능 추적)은 끈다 — 무료 한도를 아끼고 '에러만' 본다.
"""
import os

from common.logging_util import get_logger

logger = get_logger(__name__)

_enabled = False


def init_sentry(component: str) -> None:
    """진입점 시작 시 1회 호출. DSN 없으면 조용히 통과."""
    global _enabled
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN 은 있으나 sentry-sdk 미설치 — 모니터링 생략")
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENV", "production"),
        traces_sample_rate=0.0,     # 에러만, 성능 추적 끔(한도 절약)
        send_default_pii=False,
    )
    sentry_sdk.set_tag("component", component)   # collect / igdb / ai-reviews
    _enabled = True
    logger.info("Sentry 모니터링 활성화 (%s)", component)


def capture(exc: BaseException, **tags) -> None:
    """예외를 Sentry 로 보낸다 (꺼져 있으면 no-op). 로그와 별개로 호출한다."""
    if not _enabled:
        return
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass  # 모니터링이 본 작업을 깨뜨리지 않는다
