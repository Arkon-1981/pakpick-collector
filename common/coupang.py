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


def call(path_suffix: str, params: dict | None = None) -> dict:
    """GET 호출. path_suffix 는 PREFIX 뒤에 붙는 부분이다.

    실패하면 CoupangError 를 던진다 — 호출부가 그 키워드만 건너뛰고 계속 갈 수 있게.
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
            "Authorization": _auth_header("GET", path, query),
            "Content-Type": "application/json;charset=UTF-8",
        },
        api=True,
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


def search(keyword: str, limit: int = 30) -> list[dict]:
    """키워드 검색. 반환값은 상품 dict 목록 (제휴 링크 포함)."""
    body = call("/products/search", {"keyword": keyword, "limit": limit})
    data = body.get("data") or {}
    return data.get("productData") or []


def goldbox() -> list[dict]:
    """골드박스(오늘의 특가). 콘솔 물건이 섞여 있을 때만 쓸모가 있다."""
    body = call("/products/goldbox")
    return body.get("data") or []
