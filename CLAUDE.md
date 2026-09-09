# pakpick-collector 작업 규칙

콘솔 게임(닌텐도·PS·Xbox)과 스팀, 쿠팡 주변기기의 한국 스토어 할인 정보를
GitHub Actions로 하루 2번 수집해 Supabase에 넣는다. 웹은 별도 저장소(`pakpick-web`,
private) → https://pakpick-web.vercel.app

구조 설명은 `README.md`. 이 문서는 **모르면 반드시 다시 밟게 되는 함정**만 적는다.

---

## 테스트

pytest 를 쓰지 않는다. 평범한 스크립트다.

```bash
python tests/test_parsers_fixtures.py      # 스토어 응답 파싱 회귀
python tests/test_field_preservation.py    # 필드 보존·수집기 규칙
```

`check(name, cond)` 는 **위치 인자 2개만** 받는다. 상세는 name 의 f-string 에 넣는다.

### 변이 테스트 규율 (이 저장소의 핵심 관행)

버그를 고치면 테스트를 추가하고, **그 다음 `/tmp` 복사본에서 고친 코드를 되돌려
테스트가 실제로 깨지는지 확인한다.** 안 하면 "통과만 하고 아무것도 못 잡는 테스트"가
쌓인다 — 실제로 그런 상태였고 이 자기검사로 발견했다.

`tests/test_parsers_fixtures.py` 의 `_MUTATIONS` 는 이 규율을 자동화한 것이다.
픽스처의 선택자·키를 일부러 망가뜨려 검사가 잡는지 본다.

⚠️ 변이 확인을 `git checkout <file>` 로 되돌리지 말 것. 아직 스테이지 안 된 작업이
인덱스의 옛 버전으로 덮여 사라진다(실측 사고). **매번 새로 복사**해서 변이시킨다.

### 픽스처

`tests/fixtures/` 는 실제 스토어 응답 스냅샷이다. 값이 오늘 스토어와 달라도 정상이다.

⚠️ `.gitignore` 의 `*.html` 이 `tests/fixtures/steam_search.html` 을 조용히 삼켜
CI가 계속 `FileNotFoundError` 로 실패한 적이 있다(로컬엔 파일이 있어 알아채기 어려웠다).
`!tests/fixtures/*.html` 예외가 그래서 있다. 픽스처를 추가하면 **`git ls-files` 로
추적 여부를 확인**한다 — `test_fixtures_present()` 가 이걸 검사한다.

---

## 플레이스테이션 — 가장 자주 깨지는 곳

2026-08-11 소니가 스토어 목록을 클라이언트 렌더링으로 바꿨다. 페이지 HTML 의
`__NEXT_DATA__` 에서 상품이 통째로 사라졌고(`apolloState` 에 내비게이션 5개만 남았다),
`/pages/deals` 허브의 프로모션 링크도 0개가 되어 **수집이 8회 연속 실패**했다.

그래서 지금은 **HTML 을 읽지 않는다.** `web.np.playstation.com` 의 공개 GraphQL
`categoryGridRetrieve` 로 UUID 가 고정된 GMA 카테고리를 훑는다
(`collectors/playstation.py` 의 `CATALOG_CATEGORIES` / `CONCEPT_CATEGORIES`).

- 회전하는 프로모션을 쫓지 않는 이유: 프로모션 상품은 정의상 할인 중이고
  `cat.gma.AllDeals` 가 그 상위집합이다. 범위는 같고 요청 수는 훨씬 적다.
- `NewGames`·`FreeToPlay` 는 `products` 가 아니라 **`concepts` 로만 오고 가격이 없다.**
  단품 CTA 오퍼레이션으로 상품당 1요청 채운다. 안 채우면 무료 탭이 가격 없는 카드로 찬다.
- PS Plus 전용가(`plus_only`)는 일반 이용자 체감가가 아니므로 **할인으로 표시하지 않는다.**
- 게임/DLC 판별은 목록 응답의 `localizedStoreDisplayClassification` 으로만 한다(추가 요청 0회).
  이게 죽으면 게임 피드의 61%가 의상·캐릭터·레벨 같은 DLC 로 찬다(실측).
  처음 보는 분류는 어느 쪽에도 안 넣고 미판별로 남긴다 — 게임을 잘못 숨기는 쪽이 더 나쁘다.
- persistedQuery 해시는 소니가 스키마를 바꾸면 무효가 된다. 죽으면 `Query not whitelisted`
  가 온다. 새 해시는 스토어 JS 번들에서 캐야 한다(임의 쿼리 전송은 화이트리스트에 막힌다).

**HTML 경로를 폴백으로 되살리지 말 것.** 목록도 상세도 마찬가지다. 0건을 돌려주는
경로로 되돌아가면 시간예산만 태우고 고장을 '조용한 부분 수집'으로 감춘다.
실측(run 250): 종료일 HTML 폴백이 300건을 상품당 10초씩 33분 태워 종료일 10건(1%)을
얻었고, 그 33분 때문에 AllPS4·PreOrders 가 통째로 시간예산 밖으로 밀려났다.
`test_ps_html_list_path_is_gone()` 이 부활을 막는다.

### 시간 계산 — 여기서 두 번 틀렸다

**grid 1페이지(100개)에 약 84초가 걸린다.** 병목은 요청 간격이 아니라 상품당 DB 쓰기다
(`REQUEST_DELAY_SECONDS=6.0` 은 스토어 HTML 용이고, GraphQL 은 `api=True` 라 1.5초다).
카탈로그가 11,000건이면 3시간이 필요한데 잡 타임아웃은 2시간이다 — **한 실행에 전부
훑을 수 없다는 것을 전제로 설계해야 한다.**

- `CATALOG_CATEGORIES` 의 **순서가 곧 우선순위**다. 작고 유일한 공급원(PreOrders =
  출시예정 탭)을 앞에, 폭(breadth)용(AllPS4)을 뒤에 둔다.
- 늘 잘리는 큰 카테고리는 `rotate=True` 로 실행마다 시작 offset 을 옮긴다. 안 그러면
  매번 앞부분만 반복해 뒤쪽 상품의 `last_seen_at` 이 영영 갱신되지 않고 웹에서 사라진다
  (실측: AllDeals 5,820건 중 앞 1,700건만 반복).
- 로컬 드라이런의 소요 시간으로 운영 시간을 추정하지 말 것. 컨테이너는 DB 왕복도
  요청 간격도 다르다 — 실제 실행 로그의 페이지당 초를 봐야 한다.

### 카테고리 규모는 계절을 탄다

AllDeals 는 세일 시즌에 크게 불어난다: 2,259건(08-13) → 2,329건(08-25) → **5,820건(09-09)**.
"몇 건짜리 카테고리"라는 가정을 코드나 예산에 고정하지 말 것.

### 단품 오퍼레이션은 한 번 실패했다고 죽은 게 아니다

`CTA_FAIL_LIMIT`(연속 3회) 만큼 쌓여야 무효화로 본다. 예전엔 1회 실패에 이 실행 내내
보강을 접었다(run 250에서 실제로 그렇게 됐다).

---

## 수집 안전장치 — 건드리기 전에 이해할 것

`collectors/base.py` 의 이상 감지: 이번 수집량이 최근 중앙값의 35% 미만이면
**저장하지 않고 예외를 던진다.** 기존 상품은 `last_seen_at` 만 갱신해 살려 둔다.

위 PS 사고 14일 동안 매 실행 이게 동작해서 카탈로그를 지켜 냈다. 수집이 실패한다고
이 가드를 느슨하게 하지 말 것 — 가드는 증상이 아니라 경보다.

`FIELD_FLOORS` 도 같은 취지다(제목·이미지·가격 커버리지 하한).

카테고리가 시간예산이나 페이지 상한으로 잘리면 **반드시 로그로 남긴다.** 조용히 끊기면
뒤쪽 상품은 `last_seen_at` 이 안 갱신돼 웹에서 사라지는데 로그만 보면 정상이다.

---

## Supabase 함정 (웹 저장소와 공유)

- **응답 1,000행 상한.** `limit=8000` 은 조용히 무시된다. 페이지네이션하거나
  플랫폼별로 나눠 질의한다(실측: 후보 92개가 8개로 잘렸다).
- `create or replace view` 로 **컬럼 타입을 못 바꾼다**(42P16). `drop view` + `create view`
  후 `grant` 를 다시 줘야 한다.
- `anon` 롤에 statement timeout 이 있다(57014). 뷰의 `lateral` 조인은 필터·정렬 컬럼에
  인덱스를 못 써서 잘 터진다 → 실제 컬럼으로 물질화 + 트리거.
- `crawl_runs` 등이 anon 키로 빈 배열(HTTP 200)로 오면 **데이터 유실이 아니라 RLS** 다.
  service role 없이 "데이터가 없다"고 단정하지 말 것.

---

## 비밀키

`.env` 는 절대 커밋하지 않는다. 운영 값은 전부 GitHub Actions Secrets 에만 둔다:
`SUPABASE_URL` `SUPABASE_SERVICE_ROLE_KEY` `COUPANG_ACCESS_KEY` `COUPANG_SECRET_KEY`
`TWITCH_CLIENT_ID` `TWITCH_CLIENT_SECRET` `GEMINI_API_KEY` `VAPID_PRIVATE_KEY`
`VAPID_SUBJECT` `SENTRY_DSN`

⚠️ VAPID 개인키·쿠팡 시크릿·Twitch 시크릿은 **절대** 브라우저나 `NEXT_PUBLIC_*` 로
내보내지 않는다. 사용자에게 토큰·키를 채팅에 붙여 달라고 요청하지 않는다.

---

## GitHub Actions 운영

- `collect.yml` 은 동시 dispatch 하면 서로 취소된다. **순차로** 실행할 것.
- `actions_list` MCP 출력이 커서 컨텍스트를 넘긴다 — 저장된 JSON 파일을 python 으로 파싱한다.
- 실패 로그는 `get_job_logs(failed_only=true)` 가 tail 만 준다. 앞부분이 필요하면
  `get_workflow_run_logs_url` 로 zip 을 받아 푼다.

---

## 지금 열려 있는 것 (2026-09-09 기준)

- PS 수집은 2026-08-11~09-09 동안 전부 실패하다 PR #49 병합 후 run 250 에서 복구됐다.
  다만 그 실행은 시간예산 때문에 1,974건만 훑었다 — 위 '시간 계산' 항목의 수정이
  그에 대한 대응이다. 다음 실행 로그에서 카테고리별 저장 건수와 절단 경고를 확인할 것.
- 닌텐도 파서 픽스처 없음 (스팀·엑스박스·PS 는 있음).
- 웹 쪽 오픈 블로커: `lib/legal.ts` 회사명이 placeholder, 사이트명·도메인 미정.
