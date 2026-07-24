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
REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "1.5"))

STORE_REGION = "KR"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
