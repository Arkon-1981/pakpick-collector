"""쿠팡 파트너스 Open API 클라이언트.

인증은 HMAC-SHA256 서명이다. 시크릿 키는 절대 브라우저로 나가면 안 되므로
수집기(GitHub Actions)에서만 부른다 — 웹은 우리 DB만 읽는다.

서명 규칙
    message   = signed_date + METHOD + path + query
    signature = HMAC-SHA256(secret, message) 를 16진수로
    헤더      = Authorization: CEA algorithm=HmacSHA256, access-key=...,
                signed-date=..., signature=...
    signed_date 는 GMT 기준 "yymmddTHHMMSSZ"

주의
  · path 와 query 를 나눠서 이어 붙인다. '?' 는 넣지 않는다.
  · query 는 실제로 보낸 문자열과 한 글자도 달라선 안 된다(순서·인코딩 포함).
    그래서 여기서 만든 query 문자열을 그대로 요청에도 쓴다.
  · 호출 한도가 있다. 화면을 열 때마다 부르지 않고 하루 한 번 모아서 받는다.

필요한 환경변수
    COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone

from common.http_client import fetch
from common.logging_util import get_logger

logger = get_logger(__name__)

BASE = "https://api-gateway.coupang.com"
PREFIX = "/v2/providers/affiliate_open_api/apis/openapi"

ACCESS_KEY = os.environ.get("COUPANG_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("COUPANG_SECRET_KEY", "")

# 호출 사이 최소 간격(초). 한도를 넘기면 한동안 전부 막히므로 넉넉히 둔다.
CALL_GAP = 1.2
_last_call = 0.0


class CoupangError(RuntimeError):
    pass


def configured() -> bool:
    return bool(ACCESS_KEY and SECRET_KEY)


def _signature(method: str, path: str, query: str) -> tuple[str, str]:
    """(signed_date, signature) 를 만든다."""
    signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = f"{signed_date}{method}{path}{query}"
    sig = hmac.new(SECRET_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return signed_date, sig


def _auth_header(method: str, path: str, query: str) -> str:
    signed_date, sig = _signature(method, path, query)
    return (
        f"CEA algorithm=HmacSHA256, access-key={ACCESS_KEY}, "
        f"signed-date={signed_date}, signature={sig}"
    )


def _throttle() -> None:
    global _last_call
    wait = CALL_GAP - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def call(
    path_suffix: str,
    params: dict | None = None,
    *,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    """API 호출. path_suffix 는 PREFIX 뒤에 붙는 부분이다.

    실패하면 CoupangError 를 던진다 — 호출부가 그 키워드만 건너뛰고 계속 갈 수 있게.

    POST 여도 서명 규칙은 같다. 공식 예제(HmacGenerator)를 보면
    message = datetime + method + path + query 라 **본문은 서명에 들어가지 않는다.**
    """
    if not configured():
        raise CoupangError("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 없습니다")

    path = f"{PREFIX}{path_suffix}"
    # 서명에 쓴 것과 요청에 쓴 것이 같아야 한다 → 문자열을 한 번만 만든다
    query = urllib.parse.urlencode(params or {}, quote_via=urllib.parse.quote)
    url = f"{BASE}{path}" + (f"?{query}" if query else "")

    _throttle()
    result = fetch(
        url,
        extra_headers={
            "Authorization": _auth_header(method, path, query),
            "Content-Type": "application/json;charset=UTF-8",
        },
        api=True,
        method=method,
        json_body=body,
    )

    if result.status_code == 401:
        raise CoupangError("인증 실패(401) — 액세스/시크릿 키 또는 서명 형식을 확인하세요")
    if result.status_code == 429:
        raise CoupangError("호출 한도 초과(429)")
    if result.status_code != 200:
        raise CoupangError(f"상태코드 {result.status_code}: {result.text[:200]}")

    try:
        body = json.loads(result.text)
    except ValueError as exc:
        raise CoupangError(f"JSON 아님: {result.text[:200]}") from exc

    # 쿠팡은 200 안에 rCode 로 실패를 담아 보내기도 한다
    code = str(body.get("rCode", "0"))
    if code not in ("0", "200"):
        raise CoupangError(f"rCode={code} {body.get('rMessage', '')}")
    return body


# 검색 limit 의 허용 범위가 문서마다 다르고 바뀌기도 한다(30 은 "out of range" 였다).
# 큰 값부터 시도해 통하는 값을 찾고, 이후 호출은 그 값을 재사용한다.
SEARCH_LIMITS = (20, 10, 5)
_ok_limit: int | None = None


def _is_limit_error(exc: Exception) -> bool:
    m = str(exc).lower()
    return "limit" in m and "range" in m


def search(keyword: str, limit: int | None = None) -> list[dict]:
    """키워드 검색. 반환값은 상품 dict 목록 (제휴 링크 포함)."""
    global _ok_limit

    if limit is not None:
        candidates = [limit]
    elif _ok_limit is not None:
        candidates = [_ok_limit]
    else:
        candidates = list(SEARCH_LIMITS)

    last: Exception | None = None
    for lim in candidates:
        try:
            body = call("/products/search", {"keyword": keyword, "limit": lim})
        except CoupangError as exc:
            if _is_limit_error(exc) and lim != candidates[-1]:
                logger.info("[coupang] limit=%d 거부됨 — 더 작은 값으로 재시도", lim)
                last = exc
                continue
            raise
        if _ok_limit != lim:
            logger.info("[coupang] 검색 limit=%d 사용", lim)
            _ok_limit = lim
        data = body.get("data") or {}
        return data.get("productData") or []

    raise last or CoupangError("검색 실패")


# --------------------------------------------------------------------------
# 딥링크 — 오래 살아 있는 링크로 바꾼다
# --------------------------------------------------------------------------
# 검색 API 가 주는 productUrl 은 link.coupang.com/re/AFFSDP?…&requestid=…&traceid=
# …&clickBeacon=… 형태다. requestid 는 그 응답 한 번에 딸린 값이라 오래 못 간다.
# 우리는 링크를 DB 에 넣어 두고 며칠씩 보여 주므로 그 형태로는 안 된다.
#
# deeplink API 는 coupa.ng/xxxxx 같은 단축 링크를 준다 — 블로그 글에 박아 두는
# 바로 그 링크라 시간이 지나도 살아 있다.
#   POST /v1/deeplink   {"coupangUrls": [...]}
#   → {"rCode":"0","data":[{"originalUrl":…,"shortenUrl":"https://coupa.ng/…"}]}
#
# 한 번에 20개까지다. 문서에는 없고 API 가 직접 알려 줬다:
#   rCode=400 URL count should be less than or equal 20
DEEPLINK_CHUNKS = (20, 10, 5)
_ok_chunk: int | None = None


def _is_size_error(exc: Exception) -> bool:
    """'묶음이 너무 크다'는 응답인가.

    쿠팡은 한도를 문구로만 알려 주고 형태도 제각각이다.
      검색   : "limit is out of range"
      딥링크 : "URL count should be less than or equal 20"
    그래서 넉넉히 잡는다. 못 알아들으면 그 묶음을 통째로 버리게 되는데,
    실제로 그렇게 135건을 날린 적이 있다.
    """
    m = str(exc).lower()
    return any(
        w in m
        for w in ("out of range", "too many", "exceed", "less than or equal", "count should")
    )


def deeplink(urls: list[str]) -> dict[str, str]:
    """쿠팡 URL 목록 → {원본: 단축링크}. 변환 못 한 것은 빠진 채로 돌아온다."""
    global _ok_chunk
    if not urls:
        return {}

    out: dict[str, str] = {}
    sizes = [_ok_chunk] if _ok_chunk else list(DEEPLINK_CHUNKS)

    i = 0
    while i < len(urls):
        size = sizes[0]
        chunk = urls[i : i + size]
        try:
            body = call("/v1/deeplink", method="POST", body={"coupangUrls": chunk})
        except CoupangError as exc:
            if _is_size_error(exc) and len(sizes) > 1:
                sizes.pop(0)
                logger.info("[coupang] deeplink %d개 거부됨 — %d개로 재시도", size, sizes[0])
                continue           # i 를 안 올린다 → 같은 구간을 더 작게 다시
            logger.warning("[coupang] deeplink 실패 (%d건 건너뜀): %s", len(chunk), exc)
            i += size
            continue

        if _ok_chunk != size:
            logger.info("[coupang] deeplink 묶음 %d개 사용", size)
            _ok_chunk = size
        for row in body.get("data") or []:
            orig, short = row.get("originalUrl"), row.get("shortenUrl")
            if orig and short:
                out[orig] = short
        i += size

    return out


def goldbox() -> list[dict]:
    """골드박스(오늘의 특가).

    검색 API 는 정가를 안 줘서 "할인 중"인지 알 방법이 없다. 골드박스는 그 자체가
    '지금 특가'라는 뜻이라, 할인 정보 사이트에서는 이게 유일하게 의미 있는 소스다.
    쿠팡이 매일 오전 7:30 에 갱신하므로 자주 부를 이유가 없다.

    콘솔 물건이 매일 있으리란 보장은 없다 — 없는 날은 빈 목록이 정상이다.
    """
    body = call("/v1/products/goldbox")
    data = body.get("data") or []

    # 이 응답에 정가/할인율이 들어오는지는 문서에 없다. 실제로 뭐가 오는지
    # 한 번 찍어 두면 다음에 추측하지 않아도 된다 — 검색 응답에 정가가 없다는
    # 것도 이렇게 확인했다.
    if data:
        logger.info("[coupang] 골드박스 %d건, 응답 필드: %s",
                    len(data), sorted(data[0].keys()))
    return data
