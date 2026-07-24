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

STORE_REGION = "KR"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
