"""가격 알림 발송.

수집이 끝난 뒤 실행한다. 조건에 맞는 알림을 찾아 웹 푸시로 보낸다.

  1. pending_price_alerts 뷰에서 보낼 대상을 받는다
     (알림 켠 사용자 · 목표가 도달 · 아직 안 보냈거나 더 싸진 것)
  2. 사용자별 푸시 구독을 모아 발송
  3. 보낸 기록을 남겨 같은 할인으로 반복 발송하지 않는다

구독이 만료(404/410)된 기기는 즉시 지운다. 안 지우면 매번 실패가 쌓인다.

필요한 환경변수
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  — 수집기와 동일
  VAPID_PRIVATE_KEY                        — 발송 서명용 (웹의 공개키와 한 쌍)
  VAPID_SUBJECT                            — 연락처 (mailto:... 또는 https://...)
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywebpush import WebPushException, webpush  # noqa: E402

from common.logging_util import get_logger  # noqa: E402
from db.client import get_client  # noqa: E402

logger = get_logger(__name__)

SITE_URL = os.environ.get("SITE_URL", "https://pakpick-web.vercel.app")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@pakpick.app")

# 한 번에 너무 많이 보내지 않는다 (푸시 서비스 쪽 부담·차단 방지)
MAX_SEND = int(os.environ.get("NOTIFY_MAX_SEND", "500"))

PLATFORM_LABEL = {
    "playstation": "PlayStation",
    "xbox": "Xbox",
    "nintendo": "Nintendo Switch",
    "steam": "Steam",
}


def won(v) -> str:
    try:
        return f"{int(round(float(v))):,}원"
    except (TypeError, ValueError):
        return "-"


def build_message(row: dict) -> dict:
    """알림 본문. 제목에 게임 이름, 본문에 가격 — 알림창에서 잘려도 핵심이 남게."""
    title = row.get("title") or "찜한 게임"
    price = won(row.get("final_price"))
    regular = row.get("regular_price")
    disc = row.get("discount_percent")

    parts = [price]
    if disc:
        parts.append(f"{int(round(float(disc)))}% 할인")
    if regular and row.get("final_price") is not None and float(regular) > float(row["final_price"]):
        parts.append(f"정가 {won(regular)}")
    plat = PLATFORM_LABEL.get(row.get("platform", ""), row.get("platform", ""))

    return {
        "title": f"💸 {title}",
        "body": f"{' · '.join(parts)}  ({plat})",
        "url": f"{SITE_URL}/games/{row['store_item_id']}",
        "image": row.get("image_url") or None,
        # 같은 상품 알림은 최신 것으로 대체 (알림창이 쌓이지 않게)
        "tag": f"item-{row['store_item_id']}",
    }


def main() -> int:
    if not VAPID_PRIVATE_KEY:
        logger.error("VAPID_PRIVATE_KEY 가 없습니다 — 발송을 건너뜁니다")
        return 0  # 설정 전에는 수집 워크플로를 실패시키지 않는다

    client = get_client()

    # 1. 보낼 대상
    try:
        pending = (
            client.table("pending_price_alerts").select("*").limit(MAX_SEND).execute().data
        ) or []
    except Exception:
        logger.exception("발송 대상 조회 실패")
        return 1

    if not pending:
        logger.info("[notify] 보낼 알림 없음")
        return 0
    logger.info("[notify] 발송 대상 %d건", len(pending))

    # 2. 대상 사용자들의 구독을 한 번에 받아 사용자별로 묶는다
    user_ids = sorted({r["user_id"] for r in pending})
    subs_by_user: dict[str, list[dict]] = defaultdict(list)
    try:
        for i in range(0, len(user_ids), 100):
            batch = user_ids[i : i + 100]
            rows = (
                client.table("push_subscriptions")
                .select("endpoint,user_id,p256dh,auth")
                .in_("user_id", batch)
                .execute()
                .data
            ) or []
            for s in rows:
                subs_by_user[s["user_id"]].append(s)
    except Exception:
        logger.exception("구독 조회 실패")
        return 1

    sent = failed = dropped = 0

    for row in pending:
        subs = subs_by_user.get(row["user_id"])
        if not subs:
            continue  # 알림은 켰지만 아직 기기를 등록하지 않은 사용자

        payload = json.dumps(build_message(row), ensure_ascii=False)
        delivered = False

        for s in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": s["endpoint"],
                        "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                    },
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_SUBJECT},
                    ttl=86400,  # 하루 안에 못 받으면 버린다 (지난 할인 알림은 무의미)
                )
                delivered = True
                sent += 1
            except WebPushException as exc:
                code = getattr(exc.response, "status_code", None)
                if code in (404, 410):
                    # 구독이 만료됨 — 지우지 않으면 실행마다 같은 실패가 반복된다
                    try:
                        client.table("push_subscriptions").delete().eq(
                            "endpoint", s["endpoint"]
                        ).execute()
                        dropped += 1
                    except Exception:
                        logger.exception("만료 구독 삭제 실패")
                else:
                    failed += 1
                    logger.warning("[notify] 발송 실패 (%s): %s", code, str(exc)[:120])
            except Exception:
                failed += 1
                logger.exception("[notify] 발송 중 오류")

        # 3. 한 기기라도 성공하면 '알림 보냄'으로 기록한다.
        #    실패만 했다면 기록하지 않아 다음 실행에서 다시 시도된다.
        if delivered:
            try:
                client.table("alert_notifications").upsert(
                    {
                        "user_id": row["user_id"],
                        "store_item_id": row["store_item_id"],
                        "notified_price": row["final_price"],
                    },
                    on_conflict="user_id,store_item_id",
                ).execute()
            except Exception:
                logger.exception("발송 기록 저장 실패 (중복 발송 가능)")

    logger.info(
        "[notify] 완료 — 발송 %d건, 실패 %d건, 만료 구독 정리 %d건", sent, failed, dropped
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
