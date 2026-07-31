# Garmin 워치페이스 개발 — 최적 경로 정리

> 이 문서는 pakpick 수집기와는 별개인 Garmin 워치페이스 프로젝트의
> 개발환경 결정 기록입니다. (2026-07 기준 조사)

## 결론 먼저

1. **웹만으로 만드는 방법은 존재한다.** 단, 코드를 소유하지 못하고 확장이 막힌다.
2. **직접 만들 거라면 로컬 설치가 맞다.** 이유는 "설치가 필수"라서가 아니라
   **시뮬레이터**가 필요하기 때문이다.
3. **유료 판매 계획은 개발환경보다 먼저 확인해야 한다.** 여기서 막히면
   나머지 작업이 전부 의미가 달라진다.

---

## 1. GPT 대화 검증 결과

### 맞은 내용

| 내용 | 확인 |
|---|---|
| Visual Studio ≠ VS Code, 필요한 건 VS Code | 맞음 |
| Monkey C 확장은 Connect IQ SDK 4.0.6 이상 필요 | 맞음 (공식 명시) |
| 최신 SDK는 Connect IQ 9.2.0 | 맞음 |
| Developer Key는 RSA **4096비트**, 분실하면 업데이트 불가 | 맞음 (2048로 만들면 서명 실패) |
| VS Code 없이 `monkeyc` CLI만으로도 빌드 가능 | 맞음 |
| 스토어 제출용 `.iq`는 `Monkey C: Export Project`로 생성 | 맞음 |

### 틀린 내용

> "브라우저에서만 개발·빌드·테스트·출시하는 공식 웹 IDE는 없습니다."

**앞쪽은 맞지만 결론이 과했다.** Garmin **공식** 웹 IDE는 없는 게 사실이다.
그러나 워치페이스에 한해서는 브라우저에서 디자인하고 `.prg`(내 시계용) 및
`.iq`(스토어 제출용)까지 뽑아주는 서드파티 웹 빌더가 실제로 존재한다
(`garmin.watchfacebuilder.com`). 즉 "설치 없이는 불가능"이 아니라
**"설치 없이 가능하지만 대가가 있다"**가 정확한 서술이다.

### 빠진 내용 (중요도 순)

1. **유료 판매 자격 문제.** Connect IQ 유료 앱은
   - 연 **$100** 개발자 프로그램 비용
   - Garmin 승인 + 결제 대행사(Adyen) **Merchant 계정** 온보딩
   - Garmin 수수료 15%
   - 판매 가능 국가가 제한적 (US·UK·DE·EU·CA·MX·AU·NZ 중심)
   - Connect IQ System 7 이상 기기 대상

   **한국 거주 개인이 Merchant 온보딩을 통과할 수 있는지는 별도 확인이 필요하다.**
   GPT는 "Merchant 계정 승인이 필요하다"고만 하고 넘어갔지만,
   이건 승인 대기 시간이 긴 항목이라 **개발보다 먼저 신청해야 하고**,
   막히면 유료 판매 자체가 불가능하다. (무료 배포는 제약 없음)

2. **시뮬레이터가 왜 중요한지 설명하지 않았다.**
   GPT는 설치 항목을 나열했을 뿐, 시뮬레이터가 협업 구조에서
   어떤 역할인지 말하지 않았다. 워치페이스는 90%가 시각 작업이다.
   시뮬레이터가 없으면 "숫자 조금 더 크게" 한 번 확인하는 데
   빌드 → 다운로드 → USB 복사 → 시계 확인으로 5분이 걸린다.
   시뮬레이터가 있으면 10초다. 그리고 **시뮬레이터 스크린샷이
   사용자가 AI에게 결과를 보여줄 수 있는 유일한 수단**이다.

3. **협업 방식이 "프로젝트 ZIP 전달"로 되어 있다.**
   이건 실무에서 가장 빨리 망가지는 방식이다. 수정 한 번마다
   ZIP을 새로 받고 덮어써야 하고, 어느 버전이 최신인지 추적이 안 된다.
   git 저장소 + `git pull`이 정답이고, 더 나아가면 **AI를 사용자 PC에서
   직접 돌리는 것**이 맞다 (아래 4번).

---

## 2. 선택지 비교

### A안 — 웹 빌더 (설치 0)

```
브라우저에서 디자인 → .prg 다운로드 → USB로 시계에 복사
                   → .iq 뽑아서 스토어 제출
```

- 장점: 설치 없음. 오늘 당장 내 시계에 올릴 수 있음. GUI 편집.
- 단점:
  - **코드를 소유하지 못한다.** 플랫폼이 지원하는 기능 밖으로는 못 나감.
  - 커스텀 로직(예: 특정 조건에서 다른 화면, 외부 데이터 연동) 불가.
  - 구독 비용, 플랫폼 종속. 나중에 코드 기반으로 이전 못 함.
  - 템플릿 기반 결과물이라 스토어에서 차별화가 어렵다. 유료 판매용으로는 약하다.
  - **AI가 도울 여지가 거의 없다.** 코드를 쓸 게 없으므로.
- 적합: "내 시계에 예쁜 거 하나 올리고 싶다"가 목적일 때.

### B안 — 로컬 VS Code + SDK (GPT 안) ✅ 권장

- 설치 비용: 실제로 **30~60분, 2~4GB**. 생각보다 무겁지 않다.
- 얻는 것: **시뮬레이터**. 기기 없이도 여러 화면 크기를 즉시 확인,
  스크린샷으로 피드백 전달, 디버깅.
- 단점: 한 번의 설치 수고.
- 적합: 직접 만들고, 코드를 소유하고, 팔 생각이 있을 때.

### C안 — 클라우드(GitHub Actions) 빌드 + USB 사이드로드 (설치 0, 코드 소유)

기술적으로 **가능하다**. 근거:
- Connect IQ SDK는 Linux 네이티브 버전이 제공되고 `monkeyc`는 CLI다 → CI에서 빌드 가능
- Developer Key는 `openssl`로 생성 가능하므로 VS Code가 필요 없다
  ```
  openssl genrsa -out developer_key.pem 4096
  openssl pkcs8 -topk8 -inform PEM -outform DER \
      -in developer_key.pem -out developer_key.der -nocrypt
  ```
- 시계에 올리는 건 USB 연결 후 `GARMIN/APPS` 폴더에 `.prg` 드래그 → 개발도구 불필요

하지만 **워치페이스에는 부적합하다.**
- 시뮬레이터가 없다 (GUI 필요) → 시각 반복 주기가 5분
- SDK 다운로드에 Garmin 로그인/라이선스 동의가 걸려 CI 세팅 자체가 일거리
- 내가 가진 기기 하나만 확인 가능

→ **초기 개발용으로는 쓰지 말고, 나중에 릴리스 자동화용으로만 검토.**

---

## 3. 권장 경로

**B안 + 한 가지 핵심 개선.**

GPT 안의 진짜 병목은 설치가 아니라 **사람이 중간에서 전달하는 왕복**이다.

```
[GPT 안]  AI가 코드 작성 → ZIP → 사용자가 열기 → 빌드 → 에러 발생
          → 사용자가 에러 복사 → AI에 붙여넣기 → 수정 → ZIP → 반복
```

사용자 PC에 SDK가 깔려 있는데 AI가 그 PC에 접근하지 못해서
사용자가 컴파일러 에러를 손으로 옮기는 구조다.

```
[개선안]  사용자 PC에 SDK + Claude Code 설치
          → AI가 직접 monkeyc 실행 → 에러를 스스로 읽고 수정 → 시뮬레이터 실행
          → 사용자는 화면만 보고 "숫자 더 크게"
```

즉 **사용자 PC에 개발도구를 깔되, 사용자가 그 도구를 쓰는 게 아니라
AI가 쓰게 한다.** 사용자가 하는 일은 화면 보고 말하는 것뿐이다.

이 구조에서는 VS Code조차 필수가 아니다 (`monkeyc`/`monkeydo`/`connectiq`는
전부 CLI). 다만 시뮬레이터를 띄우고 디버깅하기 편하니 같이 깔아두는 게 이득이다.

---

## 4. 설치 체크리스트 (Windows)

GPT 순서에서 **기기 선택**과 **키 보관 위치**만 수정했다.

### 1) Java (JDK 17 또는 21 LTS, 64비트)

```
java -version
```
버전이 11 이상이면 그대로 사용. 없으면 설치 후 명령 프롬프트를 새로 열고 재확인.

### 2) Visual Studio Code

설치 시 "PATH에 추가" 체크.

### 3) Monkey C 확장 (게시자: Garmin)

`Ctrl+Shift+X` → `Monkey C` 검색 → 설치.

### 4) Connect IQ SDK Manager

https://developer.garmin.com/connect-iq/sdk/ → `Accept & Download for Windows`
→ 압축 해제 → 실행 → Garmin 계정 로그인 → **SDK 9.2.0** 다운로드
→ 해당 SDK를 **Active SDK**로 지정.

**기기 데이터는 처음부터 다 받지 말 것.** 아래 3개로 시작하면 충분하다.

- 본인이 실제로 쓰는 Garmin 모델 ← 최우선
- AMOLED 대표 1종 (예: Venu 3 또는 Forerunner 970)
- MIP 대표 1종 (예: fēnix 8 Solar)

AMOLED와 MIP은 색 표현과 상시표시(AOD) 동작이 달라서 둘 다 봐야 하지만,
그 외 기기는 나중에 SDK Manager에서 클릭 한 번으로 추가된다.

### 5) Developer Key 생성

`Ctrl+Shift+P` → `Monkey C: Generate a Developer Key`

**보관 규칙 (여기서 사고가 가장 많이 난다):**

- 저장 위치를 **프로젝트 폴더 밖으로** 한다. 예: `C:\GarminDeveloper\Keys\`
  → 프로젝트 안에 두면 실수로 git에 커밋된다. `.gitignore`를 믿지 말고 물리적으로 분리.
- 스토어에 올린 앱을 **업데이트할 때 같은 키가 필요하다.** 분실 = 그 앱은 영구히 업데이트 불가.
- USB + 클라우드 등 최소 2곳에 백업.
- 공개 저장소에 절대 올리지 않는다.

### 6) 설치 확인

`Ctrl+Shift+P` → `Monkey C` 입력 → 아래 명령들이 보이면 정상.

```
Monkey C: New Project
Monkey C: Build Current Project
Monkey C: Verify Installation      ← 이걸 먼저 실행하면 설치 진단을 해준다
Monkey C: Export Project
```

`Verify Installation`이 GPT 안내에 없었는데, 설치 직후 가장 먼저 돌려야 하는 명령이다.

---

## 5. 개발과 병행해서 지금 시작할 것

유료 판매가 목표라면 **승인 대기가 가장 긴 항목이므로 개발과 동시에 신청**한다.

- [ ] Garmin 개발자 계정 생성
- [ ] 유료 판매 조건 확인 — **한국에서 Merchant 온보딩이 가능한지 먼저 확인**
      (연 $100, Adyen 온보딩, 수수료 15%, 판매 대상 국가 제한)
- [ ] 불가하거나 지연되면 → **무료 배포로 먼저 출시**하고 반응을 본 뒤 결정

무료 배포에는 위 제약이 없다. 그래서 순서는
"무료로 내보고 → 반응 확인 → 유료 전환 검토"가 위험이 가장 적다.

---

## 6. 다음 단계에 필요한 정보

프로젝트를 만들려면 아래 두 개가 있어야 한다.

1. **사용 중인 Garmin 워치 모델명** — `manifest.xml`의 대상 기기와 해상도가 여기서 정해진다.
2. **워치페이스에 무엇을 보여줄지** — 시간 표시 형식, 함께 보여줄 데이터
   (심박수 / 걸음 수 / 배터리 / 날짜 / 날씨 등), 원하는 분위기.

---

## 참고 자료

- Get the SDK — https://developer.garmin.com/connect-iq/sdk/
- Monkey C VS Code 확장 — https://marketplace.visualstudio.com/items?itemName=garmin.monkey-c
- Manifest 및 권한 — https://developer.garmin.com/connect-iq/core-topics/manifest-and-permissions/
- 유료 앱 도입 발표 — https://www.garmin.com/en-US/newsroom/press-release/wearables-health/garmin-enables-premium-app-purchases-in-the-connect-iq-store-and-unveils-fun-new-watch-faces-and-apps/
- Merchant 자격 관련 포럼 논의 — https://forums.garmin.com/developer/connect-iq/f/connect-iq-web-store/428218/questions-about-becoming-a-connect-iq-merchant-as-an-individual-no-registered-business
