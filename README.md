# Pakpick 수집기 (pakpick-collector)

콘솔 게임(닌텐도 / 플레이스테이션 / Xbox) 한국 스토어의 할인 정보를
하루 2번 자동으로 수집해서 Supabase에 저장하는 프로그램입니다.

## 동작 원리 (쉬운 설명)

```
스토어 사이트 (닌텐도 / PS / Xbox)
        │
        ▼
① 페이지 원본을 통째로 내려받음
        │
        ▼
② 원본을 압축해서 창고(Supabase Storage)에 보관   ← 나중에 필요한 정보를 다시 꺼낼 수 있음
        │
        ▼
③ 원본에서 게임명·가격·할인율 등을 뽑아냄
        │
        ▼
④ 표(Supabase DB 테이블)에 정리해서 저장
   - 가격이 지난번과 같으면 저장 안 함 (변동만 기록)
   - 상품 정보가 바뀌면 버전 기록을 남김
```

**핵심 원칙: 원본을 무조건 저장한다.**
파싱(정보 뽑기)이 실패하거나 나중에 새 정보가 필요해져도,
원본이 창고에 있으므로 파서만 고쳐서 과거 데이터를 다시 처리할 수 있습니다.

## 폴더 구조

```
collectors/   각 스토어에 접속해서 데이터를 가져오는 코드
parsers/      가져온 원본에서 정보를 뽑아내는 코드
db/           Supabase DB 저장 코드
storage/      원본 파일 창고(Storage) 업로드 코드
common/       공통 도구 (설정, HTTP 요청, 해시, 로그)
scripts/      실행 진입점
.github/workflows/  하루 2번 자동 실행 설정 (GitHub Actions)
```

## 플랫폼별 수집 방식

| 플랫폼 | 방식 | 비고 |
|---|---|---|
| 닌텐도 | `store.nintendo.co.kr/digital/sale` HTML 파싱 | 가장 단순 |
| PS | `store.playstation.com/ko-kr/pages/deals` 페이지 안의 JSON(`__NEXT_DATA__`) 추출 | 정보 가장 풍부 |
| Xbox | ① 공개 추천 API로 할인 상품 ID 목록 → ② `displaycatalog` 공개 JSON API로 상세 조회 | 2단계 방식 |

## 처음 설정하는 방법

### 1. GitHub Secrets 등록 (자동 실행용)

GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| 이름 | 값 |
|---|---|
| `SUPABASE_URL` | Supabase Project URL (`https://xxxx.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase secret 키 (`sb_secret_...`) |

### 2. 자동 실행 확인

등록만 하면 **한국 시간 오전 9시 / 오후 9시**에 자동으로 돌아갑니다.

지금 바로 테스트하려면:
GitHub 저장소 → **Actions** 탭 → **collect** → **Run workflow** 버튼

### 3. 결과 확인

- Supabase → **Table Editor** → `crawl_runs` : 실행 성공/실패 기록
- `store_items` : 수집된 상품 목록
- `price_snapshots` : 가격 기록
- Supabase → **Storage** → `raw-store-data` : 원본 파일

## 내 컴퓨터에서 직접 실행 (선택)

```bash
# 1. 라이브러리 설치
pip install -r requirements.txt

# 2. .env 파일 만들기 (.env.example 복사해서 실제 값 입력)
cp .env.example .env

# 3. 실행
python scripts/collect.py --platform nintendo     # 닌텐도만
python scripts/collect.py --platform all          # 전부
```

## 문제가 생기면

1. `crawl_runs` 테이블에서 `status`가 `failed`인지 확인
2. `crawl_errors` 테이블에서 오류 메시지 확인
3. 스토어 사이트 구조가 바뀐 경우: `parsers/` 폴더의 해당 파일만 수정하면 됨
   (원본은 저장돼 있으므로 데이터 유실 없음)

## 주의사항 (안전 수칙)

- 요청 사이 1.5초 대기 — 스토어 서버에 부담을 주지 않기 위함
- 하루 2번만 실행 — 과도한 수집 금지
- 로그인/우회 없음 — 누구나 볼 수 있는 공개 페이지만 수집
- 비밀키는 절대 코드에 넣지 않음 — GitHub Secrets / .env 사용
