"""환경설정 로드.

.env 파일 또는 GitHub Actions의 Secrets(환경변수)에서 설정을 읽는다.
비밀키는 절대 코드에 직접 쓰지 않는다.
"""
import os

from dotenv import load_dotenv

load_dotenv()


# 비밀키 검증은 실제로 DB에 접속하는 시점(db/client.py)에 한다.
# 이렇게 해야 비밀키 없이도 파서 테스트 등을 할 수 있다.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

RAW_BUCKET = os.environ.get("RAW_BUCKET", "raw-store-data")

# ----- 안전 수칙 관련 설정 -----

# 요청 사이 기본 대기 시간(초). 실제 대기는 이 값~2배 사이 무작위로,
# 사람이 페이지를 넘겨 보는 속도(기본 6~12초)가 된다.
REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "6.0"))

# robots.txt(사이트의 로봇 출입 규칙) 준수 여부 — 기본 켜짐
RESPECT_ROBOTS = os.environ.get("RESPECT_ROBOTS", "true").lower() != "false"

# 한 번 실행에서 보낼 수 있는 최대 요청 수 (폭주 방지 상한선)
MAX_REQUESTS_PER_RUN = int(os.environ.get("MAX_REQUESTS_PER_RUN", "1500"))

# 차단성 응답(202/403/429)이 연속으로 이 횟수만큼 오면 즉시 중단
CONSECUTIVE_BLOCK_LIMIT = int(os.environ.get("CONSECUTIVE_BLOCK_LIMIT", "3"))

# Xbox 할인작 수집 상한. emerald(deals) API는 채널당 16,000+개를 페이지네이션으로
# 주므로(페이지당 ~46개), 상위 N개까지만 페이지를 넘기며 모은다. (예전엔 1페이지만 봐서 ~112개에 그침)
XBOX_MAX_ITEMS = int(os.environ.get("XBOX_MAX_ITEMS", "800"))

# 스팀 할인작 수집 상한 (전 세계 베스트셀러 순 상위 N개만).
# 스팀 할인은 수만 개라 전부 받으면 소규모 게임 노이즈 + DB 부담이 큼.
STEAM_MAX_ITEMS = int(os.environ.get("STEAM_MAX_ITEMS", "800"))

# 스팀 스크린샷 갤러리(캐러셀용)를 상위 N개까지 보강한다 (상세 API 추가 호출).
# 이미 갤러리가 채워진 상품은 재조회하지 않으므로 신규 상품에만 비용이 든다. 0이면 끔.
STEAM_GALLERY_MAX = int(os.environ.get("STEAM_GALLERY_MAX", "150"))

# 닌텐도 스크린샷 갤러리를 '할인율 상위' N개까지 보강한다 (상품 상세 페이지 추가 로드).
# 피드가 할인율 상위 150개(TOP_PER_PLATFORM)를 보여주므로 그만큼 덮어야 피드 카드가
# 스크린샷 롤링이 된다. 닌텐도는 봇 차단 때문에 상세도 실제 브라우저(Playwright)가 필요해
# 느리지만, 이미 갤러리가 채워진 상품은 재조회하지 않아 신규분에만 비용이 든다. 0이면 끔.
NINTENDO_GALLERY_MAX = int(os.environ.get("NINTENDO_GALLERY_MAX", "150"))

# PS 할인 종료일(endTime)은 목록 페이지엔 없고 상품 상세 페이지에만 있다.
# 할인율 상위 N개만 상세를 추가로 받아 종료일을 보강한다(봇 차단 없어 일반 HTTP, 빠름). 0이면 끔.
PS_DETAIL_END_MAX = int(os.environ.get("PS_DETAIL_END_MAX", "60"))

STORE_REGION = "KR"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
