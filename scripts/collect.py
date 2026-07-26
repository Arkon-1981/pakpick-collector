"""수집 실행 진입점.

사용법:
  python scripts/collect.py --platform nintendo      # 닌텐도만
  python scripts/collect.py --platform playstation   # 플레이스테이션만
  python scripts/collect.py --platform xbox          # Xbox만
  python scripts/collect.py --platform all           # 3개 전부
"""
import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.nintendo import NintendoCollector          # noqa: E402
from collectors.playstation import PlaystationCollector    # noqa: E402
from collectors.steam import SteamCollector                # noqa: E402
from collectors.xbox import XboxCollector                  # noqa: E402
from common.logging_util import get_logger                 # noqa: E402

logger = get_logger(__name__)

COLLECTORS = {
    "nintendo": NintendoCollector,
    "playstation": PlaystationCollector,
    "xbox": XboxCollector,
    "steam": SteamCollector,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pakpick 콘솔 게임 할인 수집기")
    parser.add_argument(
        "--platform",
        choices=[*COLLECTORS.keys(), "all"],
        required=True,
        help="수집할 플랫폼",
    )
    args = parser.parse_args()

    targets = list(COLLECTORS.keys()) if args.platform == "all" else [args.platform]

    failed = []
    for name in targets:
        try:
            COLLECTORS[name]().run()
        except Exception:
            logger.exception("[%s] 수집 실패 — 다음 플랫폼 계속 진행", name)
            failed.append(name)

    if failed:
        logger.error("실패한 플랫폼: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
