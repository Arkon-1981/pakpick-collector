"""가격 알림 발송 로직 검증.

    python tests/test_notify.py

pywebpush 와 Supabase 클라이언트를 가짜로 바꿔 끼우고 notify.main() 을 실제로
돌린다. 코드에 문자열이 들어갔는지 보는 게 아니라
 · 발송 호출이 몇 번 일어났는가
 · 어떤 payload 가 갔는가
 · alert_notifications 에 몇 번의 요청으로 몇 행이 기록됐는가
를 직접 센다. (예전에 필드 채움률 가드가 코드에는 있는데 실행되지 않아
로그에 한 줄도 안 찍힌 일이 있었다. 실행으로 확인해야 한다.)
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# ---- pywebpush 스텁 ----
# 진짜 pywebpush 를 쓰면 실제로 푸시가 나가고, 설치도 환경에 따라 실패한다
# (데비안에서 http-ece 빌드가 깨진다). 발송 호출만 가로채서 센다.
pw = types.ModuleType("pywebpush")


class WebPushException(Exception):
    def __init__(self, msg, response=None):
        super().__init__(msg)
        self.response = response


SENT = []          # 실제 발송 호출 기록
FORCE_STATUS = {}  # endpoint -> HTTP status (실패 시나리오)


def webpush(*, subscription_info, data, vapid_private_key, vapid_claims, ttl):
    ep = subscription_info["endpoint"]
    code = FORCE_STATUS.get(ep)
    if code:
        resp = types.SimpleNamespace(status_code=code)
        raise WebPushException(f"forced {code}", response=resp)
    SENT.append((ep, json.loads(data)))


pw.webpush = webpush
pw.WebPushException = WebPushException
sys.modules["pywebpush"] = pw

# ---- supabase 클라이언트 스텁 ----
UPSERTS = []   # (table, rows) — 요청 단위로 기록
DELETES = []


class Q:
    def __init__(self, table, store):
        self.t, self.store = table, store
        self._rows = None

    def select(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def in_(self, col, vals):
        self._rows = [r for r in self.store.get(self.t, []) if r[col] in vals]
        return self

    def eq(self, col, val):
        self._filter = (col, val)
        return self

    def upsert(self, rows, **k):
        UPSERTS.append((self.t, rows if isinstance(rows, list) else [rows]))
        self._rows = []
        return self

    def delete(self):
        self._del = True
        return self

    def execute(self):
        if getattr(self, "_del", False):
            DELETES.append((self.t, self._filter))
            return types.SimpleNamespace(data=[])
        rows = self._rows if self._rows is not None else self.store.get(self.t, [])
        return types.SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return Q(name, self.store)


import notify  # noqa: E402  (스텁을 먼저 심어야 import 가 성공한다)

UID = "9afe398b-b4a2-4b1a-b2ae-63a0adbab7e0"


def row(item_id, title, final, regular, disc):
    return {
        "user_id": UID,
        "store_item_id": item_id,
        "target_price": 999999,
        "final_price": final,
        "regular_price": regular,
        "discount_percent": disc,
        "title": title,
        "platform": "playstation",
        "image_url": f"https://img/{item_id}.jpg",
    }


def sub(endpoint):
    return {"endpoint": endpoint, "user_id": UID, "p256dh": "pk", "auth": "au"}


def run(pending, subs, *, max_per_user=3):
    SENT.clear(); UPSERTS.clear(); DELETES.clear(); FORCE_STATUS.clear()
    notify.MAX_PER_USER = max_per_user
    notify.VAPID_PRIVATE_KEY = "fake-private-key"
    store = {"pending_price_alerts": pending, "push_subscriptions": subs}
    notify.get_client = lambda: FakeClient(store)
    rc = notify.main()
    assert rc == 0, f"main() 이 {rc} 를 반환"
    return rc


fail = 0


def check(label, cond, detail=""):
    global fail
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        fail += 1


# =====================================================================
# 1) 상한 이내(3건) → 개별 알림 3건, 기록 요청은 1번에 3행
# =====================================================================
three = [row(1, "게임A", 10000, 50000, 80), row(2, "게임B", 20000, 40000, 50),
         row(3, "게임C", 30000, 35000, 14)]
run(three, [sub("https://push/dev1")])
check("3건 → 개별 발송 3건", len(SENT) == 3, f"실제 {len(SENT)}")
check("3건 → 태그가 상품별로 분리",
      sorted(m["tag"] for _, m in SENT) == ["item-1", "item-2", "item-3"],
      str(sorted(m["tag"] for _, m in SENT)))
alerts = [u for u in UPSERTS if u[0] == "alert_notifications"]
check("기록 요청 1번", len(alerts) == 1, f"실제 {len(alerts)}번")
check("기록 3행", len(alerts[0][1]) == 3 if alerts else False)
check("기록에 상품·가격이 정확", sorted((r["store_item_id"], r["notified_price"]) for r in alerts[0][1])
      == [(1, 10000), (2, 20000), (3, 30000)])

# =====================================================================
# 2) 상한 초과(8건) → 묶음 알림 1건, 기록은 8행 (다음 실행 중복 방지)
# =====================================================================
eight = [row(i, f"게임{i}", 10000 * i, 20000 * i, 50 + i) for i in range(1, 9)]
run(eight, [sub("https://push/dev1")])
check("8건 → 발송 1건으로 축소", len(SENT) == 1, f"실제 {len(SENT)}")
msg = SENT[0][1]
check("묶음 제목에 개수", msg["title"] == "💸 찜한 게임 8개가 할인 중", msg["title"])
check("묶음 태그 하나", msg["tag"] == "wishlist-digest", msg["tag"])
check("묶음 본문에 대표 상품 + 최대 할인율",
      "외 7개" in msg["body"] and "58% 할인" in msg["body"], msg["body"])
check("대표 상품은 할인액이 가장 큰 것", msg["body"].startswith("게임8"), msg["body"])
alerts = [u for u in UPSERTS if u[0] == "alert_notifications"]
check("묶음이어도 8행 전부 기록", len(alerts) == 1 and len(alerts[0][1]) == 8,
      f"요청 {len(alerts)}번 / {len(alerts[0][1]) if alerts else 0}행")

# =====================================================================
# 3) 만료 구독(410) → 자동 삭제, 기록은 남지 않음(재시도 가능해야 한다)
# =====================================================================
run(three, [sub("https://push/dead")])
FORCE_STATUS.clear()
SENT.clear(); UPSERTS.clear(); DELETES.clear()
notify.MAX_PER_USER = 3
FORCE_STATUS["https://push/dead"] = 410
store = {"pending_price_alerts": three, "push_subscriptions": [sub("https://push/dead")]}
notify.get_client = lambda: FakeClient(store)
notify.main()
check("410 → 발송 0건", len(SENT) == 0)
check("410 → 구독 삭제 요청 정확히 1번", 
      [t for t, _ in DELETES] == ["push_subscriptions"], str(DELETES))
check("410 → 발송 기록 남기지 않음",
      not [u for u in UPSERTS if u[0] == "alert_notifications"], str(UPSERTS))

# =====================================================================
# 4) 구독 없는 사용자 → 발송·기록 모두 없음 (조용히 넘긴다)
# =====================================================================
run(three, [])
check("구독 없음 → 발송 0건", len(SENT) == 0)
check("구독 없음 → 기록 0건", not [u for u in UPSERTS if u[0] == "alert_notifications"])

# =====================================================================
# 5) 기기 2대 → 알림 하나가 두 기기 모두에 (기록은 1행씩)
# =====================================================================
run([three[0]], [sub("https://push/dev1"), sub("https://push/dev2")])
check("기기 2대 → 발송 2건", len(SENT) == 2, f"실제 {len(SENT)}")
alerts = [u for u in UPSERTS if u[0] == "alert_notifications"]
check("기기 2대여도 기록 1행", len(alerts) == 1 and len(alerts[0][1]) == 1)

print()
print("실패 0건 — 전부 통과" if fail == 0 else f"실패 {fail}건")
sys.exit(1 if fail else 0)
