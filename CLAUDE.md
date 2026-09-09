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

**HTML 목록 경로를 폴백으로 되살리지 말 것.** 0건을 돌려주는 경로로 되돌아가면
시간예산만 태우고 고장을 '조용한 부분 수집'으로 감춘다 — 위 사고가 8회나 이어진 이유가
정확히 그것이다. `test_ps_html_list_path_is_gone()` 이 부활을 막는다.

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

- **PR #49 미병합.** PS 수집 GraphQL 전환 + PS DLC 판별. 병합 전까지 PS 수집은 계속
  실패한다(2026-08-11 이후 성공 0회). CI 통과, 충돌 없음.
- 닌텐도 파서 픽스처 없음 (스팀·엑스박스·PS 는 있음).
- 웹 쪽 오픈 블로커: `lib/legal.ts` 회사명이 placeholder, 사이트명·도메인 미정.
