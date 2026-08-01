"""쿠팡 bestcategories 엔드포인트 탐사 — 일회용.

    python scripts/probe_coupang_categories.py

/products/bestcategories/{categoryId} 로 카테고리별 베스트 상품을 받을 수 있다는
것까지는 문서에 있는데, categoryId 체계가 없다. 추측으로 수집기를 짜는 대신
여기서 실제로 찔러 보고 결과를 로그로 남긴다. DB 에는 아무것도 쓰지 않는다.

확인하려는 것
  1. 어떤 categoryId 가 통하는가 (문서의 최상위 ID? 사이트의 심층 ID?)
  2. limit 은 몇까지 받는가 (검색은 10이 상한이었다)
  3. 응답에 정가·할인율이 있는가 (검색·골드박스에는 없었다)
  4. 콘솔 게임 관련 상품이 얼마나 섞여 있는가
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import coupang                     # noqa: E402
from common.logging_util import get_logger     # noqa: E402
from parsers.coupang import detect_consoles    # noqa: E402

logger = get_logger(__name__)

# 파트너스 문서에 나열된 최상위 카테고리 중 게임이 있을 법한 곳 + 심층 ID 실험용.
# 317777 은 쿠팡 사이트 URL(/np/categories/…)에서 쓰는 형식의 ID 가 통하는지
# 보려는 실험이지, 값 자체에 의미가 있는 건 아니다.
CANDIDATES = [
    ("1016", "가전디지털"),
    ("1020", "완구/취미"),
    ("1019", "도서/음반/DVD"),
    ("1017", "스포츠/레저"),
    ("1021", "문구/오피스"),
    ("317777", "심층 ID 형식 실험"),
]

LIMITS = (100, 50, 20, 10)


def probe(cid: str, label: str) -> None:
    for lim in LIMITS:
        try:
            body = coupang.call(f"/v1/products/bestcategories/{cid}", {"limit": lim})
        except coupang.CoupangError as exc:
            msg = str(exc)
            if coupang._is_limit_error(exc) and lim != LIMITS[-1]:
                logger.info("[probe] %s(%s) limit=%d 거부 — 줄여서 재시도", label, cid, lim)
                continue
            logger.warning("[probe] %s(%s) 실패: %s", label, cid, msg[:160])
            return

        items = (body.get("data") or {})
        # 응답 모양이 검색과 같은지(dict 안 productData) 다른지(리스트)도 기록한다
        if isinstance(items, dict):
            items = items.get("productData") or []
        logger.info("[probe] %s(%s) limit=%d → %d건", label, cid, lim, len(items))
        if not items:
            return

        first = items[0]
        logger.info("[probe]   응답 필드: %s", sorted(first.keys()))
        console_hits = [p for p in items if detect_consoles(p.get("productName") or "")]
        logger.info("[probe]   콘솔 관련: %d/%d건", len(console_hits), len(items))
        for p in console_hits[:8]:
            logger.info("[probe]     %8s원 | %s",
                        f"{int(float(p.get('productPrice', 0))):,}",
                        (p.get("productName") or "")[:60])
        return


def main() -> int:
    if not coupang.configured():
        logger.error("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 없습니다")
        return 1
    for cid, label in CANDIDATES:
        probe(cid, label)
    logger.info("[probe] 끝 — DB 에는 아무것도 쓰지 않았습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
