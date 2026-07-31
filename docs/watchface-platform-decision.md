# 워치페이스 개발 — 플랫폼 선택과 개발환경 결정 기록

> pakpick 수집기와는 별개인 워치페이스 프로젝트의 결정 기록. (2026-07 조사)
>
> **결론이 조사 중에 뒤바뀌었다.** 처음엔 "Garmin 개발환경을 어떻게 깔까"였는데,
> 수익화 자격을 확인해보니 **한국 거주자는 Garmin 공식 결제로 판매할 수 없다**는
> 사실이 나왔다. 그래서 플랫폼 자체를 다시 골라야 한다.

---

## 0. 최종 결론

| 목적 | 권장 경로 |
|---|---|
| **워치페이스를 팔아서 수익** | **Facer 등 서드파티 플랫폼** (Play Store 직판은 비권장) |
| 내 Garmin 시계용 + 무료 공개 | Garmin Connect IQ |
| Garmin에서 굳이 유료 | 스토어엔 무료 등록 + 외부 결제(언락키) |
| 갤럭시 Play Store 유료 직판 | 가능하지만 시장이 작고 줄고 있음 (§4) |

**⚠️ 이 문서의 초기 판단을 수정한다.** 처음엔 "한국에서 팔 수 있으니 갤럭시가 정답"으로
결론냈지만, 실제 시장 규모를 확인해보니 **갤럭시 스마트워치는 점유율 5%로 5위이고
전년 대비 28% 역성장 중**이다. "팔 수 있다"와 "팔린다"는 다른 문제다.

정리하면 이렇게 갈린다.

- **결제 접근성**: 갤럭시 ✅ / Garmin ❌(한국 판매자 불가)
- **시장 성장성**: Garmin ✅(FY2025 +15%, 첫 2천만 대) / 갤럭시 ❌(-28%)
- **워치페이스 유료 판매 실적**: 양쪽 다 나쁨. Play Store는 개발자들 사이에서도 회의적

즉 **"워치페이스를 팔아 수익을 낸다"는 목표 자체가 어느 플랫폼에서도 잘 안 되는
구조**다. 이걸 개발환경 설치 전에 아는 게 이 조사의 최대 소득이다.

---

## 1. 핵심 사실 — Garmin은 "팔 수는 없고 살 수만 있다"

Garmin 공식 문서에 **판매자 국가 목록과 구매자 국가 목록이 따로 있고, 서로 다르다.**
이걸 놓치면 개발을 다 해놓고 마지막에 막힌다.

### 판매자(개발자) 지원 국가 — 한국 없음 ❌

> "The Connect IQ™ monetization system is available to developers with legal entities in
> the United States, Canada, Australia, Singapore, and most of the European Union.
> **If you do not live or are not incorporated in any of the following countries,
> you cannot sign up for the monetization system**"

- 북미: 미국(푸에르토리코 포함), 캐나다
- 유럽: 오스트리아, 벨기에, 크로아티아, 키프로스, 체코, 덴마크, 에스토니아, 핀란드,
  프랑스, 독일, 지브롤터, 그리스, 건지, 헝가리, 아일랜드, 이탈리아, 라트비아,
  리히텐슈타인, 리투아니아, 룩셈부르크, 몰타, 모나코, 네덜란드, 노르웨이, 폴란드,
  포르투갈, 루마니아, 슬로바키아, 슬로베니아, 스페인, 스웨덴, 스위스, 영국
- APAC: **호주, 싱가포르** ← APAC은 이 둘뿐

→ **한국(대한민국)은 없다.** 한국 거주 개인/법인은 Garmin Merchant 등록 자체가 불가.

### 구매자(사용자) 지원 국가 — 한국 있음 ✅

APAC 구매 가능 국가 목록에 `Republic of Korea`가 **포함되어 있다.**

즉 **한국 사람은 Garmin 유료 앱을 살 수 있지만, 한국 사람은 팔 수 없다.**
이 비대칭이 이 조사의 핵심 발견이다.

### 참고 — Garmin 유료 판매 조건 (자격이 된다는 가정 하에)

- 연 **$100 USD** (환불 불가), Garmin 수수료 **15%**
- Adyen 온보딩: 사진 ID, 거주지 증명, 납세자번호 증명, 업종 증명 등 → 승인까지 수일
- 유료로 팔 수 있는 기기는 **API Level 3.4 이상**(fēnix 6 세대 이후)으로 제한
- 가격대 통화 목록에 **KRW 없음** (ARS/AUD/CAD/CHF/CZK/DKK/EUR/GBP/MXN/NOK/NZD/RON/SEK/THB/USD/VND)

---

## 2. 갤럭시(Wear OS)는 한국에 완전히 열려 있다

| 항목 | Garmin | 갤럭시 (Google Play) |
|---|---|---|
| 한국 개발자 등록 | 가능 | 가능 |
| **한국 판매자 등록** | **불가** ❌ | **가능** ✅ |
| 정산 통화 | KRW 없음 | **KRW** |
| 등록 비용 | 유료 판매 시 연 $100 | **1회 $25** |
| 수수료 | 15% | Google Play 표준 |
| 코딩 필요 | Monkey C 작성 필요 | **불필요** (아래) |

Google Play 지원 국가 표의 한국 행은 `South Korea ✔ ✔ KRW`
= 개발자 등록 ✔, 판매자 등록 ✔, 기본 정산 통화 KRW.

### 결정적인 부분: 코딩이 아예 필요 없다

Wear OS 워치페이스는 이제 **Watch Face Format(WFF)** 을 쓴다.

> "The Watch Face Format is a **declarative XML format, so there is no executable code
> involved** in creating a watch face, and there is no code embedded in the watch face APK."

그리고 삼성이 만든 **Watch Face Studio**는 코딩 없이 GUI로 워치페이스를 만드는
무료 데스크톱 도구다. Garmin에서 Monkey C를 배우거나 AI에게 코드를 맡기는 것과 달리,
여기서는 **디자인 도구에서 직접 만들고 바로 Play Store에 올린다.**

주의: WFF 마이그레이션 기한이 **2026-01-14**였다. 이미 지났으므로 지금 새로 만들면
자동으로 WFF다. 옛 자료(Wearable Support Library 기반 튜토리얼)는 따라가면 안 된다.

### 갤럭시 쪽 단점

- **시장이 작고 줄고 있다.** 점유율 5%(5위), 전년비 -28%. §4 참고.
  결제가 열려 있다는 것과 실제로 팔린다는 것은 별개다.
- **Play Store 워치페이스 유료 판매 실적이 매우 나쁘다.** 무료:유료 다운로드 비율
  약 100:1, 개발자 포럼에서 수익성 회의론이 반복된다. §4 참고.
- **갤럭시 워치 실기가 없으면 실제 착용 확인이 어렵다.**
  Watch Face Studio 내장 미리보기 + Android 에뮬레이터로 대체 가능하지만
  실기 배터리·상시표시(AOD) 체감은 확인이 안 된다.
- WFF는 선언형이라 **자유도가 낮다.** 복잡한 로직이 필요한 건 못 만든다.
  (반대로 워치페이스 대부분은 이걸로 충분하다)
- Galaxy Store에도 별도 등록 가능하지만, 워치 단독 콘텐츠의 수익 옵션은 제한적이라
  일반적으로 Google Play가 주력이다.

---

## 3. Garmin에서 굳이 유료로 팔고 싶다면

Garmin 결제 시스템만 막힌 것이고, **외부 결제는 금지되지 않았다.**

Garmin App Review Guidelines 4.d는 결제를 **공시**하도록만 요구한다:

> "You must identify whether or not your app requires payment. ...
> You must be honest when describing any payment requirements, whether optional or not."

Garmin 결제 시스템을 반드시 쓰라는 조항은 없다. 실제로 Gumroad 같은 외부 플랫폼에서
Garmin 워치페이스를 판매하는 개발자들이 있다.

가능한 경로 세 가지:

1. **스토어 무료 등록 + 외부 결제 언락키** — 워치페이스는 무료로 올리고,
   프리미엄 기능은 외부에서 구매한 키를 설정에 입력해 해제. 앱 설명에 결제 조건 공시 필수.
2. **스토어 밖에서 판매** — `.prg`를 직접 판매하고 구매자가 USB로 사이드로드.
   가장 단순하지만 스토어 노출을 못 받는다.
3. **지원 국가 법인 설립** — 싱가포르/미국 법인. 세무·유지비용 대비 워치페이스
   수익 규모를 생각하면 배보다 배꼽이 크다. 권하지 않는다.

⚠️ 1·2번은 지침 위반은 아니지만 Connect IQ Developer Agreement 본문까지 직접 확인하고
진행하는 게 안전하다. 심사 지침보다 계약서가 우선한다.

---

## 4. 시장 규모 — 실제 숫자

### 스마트워치 전체 (Counterpoint, 2026 Q1)

전체 시장은 전년 대비 **+4%** 성장했는데, 삼성만 역주행했다.

| 순위 | 브랜드 | 점유율 | 전년비 |
|---|---|---|---|
| 1 | Apple | 23% | +21% |
| 2 | Huawei | 17% | +12% |
| 3 | Xiaomi | 10% | +9% |
| 4 | Imoo | 7% | +2% |
| **5** | **Samsung** | **5%** | **-28%** |

삼성 점유율은 2025 Q1 7% → 2026 Q1 **5%**로 하락했고 출하량은 28% 줄었다.

**여기서 중요한 함정:** 1위 Apple(23%)은 서드파티 워치페이스를 **아예 허용하지 않는다.**
2·3위 Huawei·Xiaomi는 Wear OS가 아니고(자체 OS), 4위 Imoo는 어린이용 워치다.
→ **시장 1~4위가 전부 워치페이스를 팔 수 없는 곳**이고, 5위이자 역성장 중인 삼성이
사실상 유일한 유료 워치페이스 시장이다.

### Garmin (FY2025 실적)

- 연매출 **$7.25B**, 전년비 **+15%**
- 사상 첫 **2천만 대** 이상 출하
- 피트니스 부문 4분기 매출 **+42%**

**시장 성장성만 보면 Garmin이 갤럭시보다 낫다.** 문제는 한국 판매자가
그 시장에 공식 결제로 진입할 수 없다는 것뿐이다.

### 워치페이스 유료 판매의 실제 성적

삼성 개발자 포럼의 실제 개발자 보고들:

- **무료 대비 유료 다운로드 비율이 약 100:1**
- Galaxy Store에서 Play Store로 옮긴 뒤 수익성에 의문을 제기하는 개발자 다수,
  "실제로 이익 본 사람이 있느냐"는 글이 올라온다
- 한 개발자는 워치페이스를 **딱 1개** 판매했다고 보고
- "Watch Face Studio 팀이 한 훌륭한 작업이 Play Store에서 워치페이스를 파는 데는
  사실상 쓸모없다"는 평가
- Play Store의 워치 카테고리 자체가 노출·검색이 부실하다는 불만이 반복적으로 나온다
- Galaxy Watch 앱은 인앱결제·광고 옵션이 제한적이라 **유료 앱 외 수익 수단이 적다**

즉 문제는 제작 난이도가 아니라 **발견 가능성(discoverability)** 이다.
Play Store에 2백만 개 앱이 있고 워치페이스는 그 안의 아주 좁은 니치다.

### 그래서 수익이 목적이라면 — 서드파티 플랫폼

**Facer**가 현실적으로 가장 실적이 확인되는 채널이다.

- 사용자 100만 명 돌파, 디자이너 누적 지급액 **$100만** 돌파를 공개
- 월 수천 달러를 벌어 이걸 주 수입으로 삼는 디자이너들이 존재한다고 공개
- Wear OS와 Apple Watch 양쪽에 대응 (Apple 워치페이스를 우회해서 다룸)
- Premium Designer / Creator Partner 프로그램은 **초대·심사제**라 아무나 바로
  수익화되지 않는다. 수익 배분율은 비공개.

핵심 차이: Play Store는 **내가 노출을 스스로 만들어야** 하고,
Facer는 **이미 워치페이스를 찾으러 온 사용자**가 모여 있는 곳이다.
디자인 실력으로 경쟁하는 구조라, 개발보다 디자인이 강점이면 여기가 맞다.

(비슷한 대안: WatchMaker)

### 위험을 최소화하는 순서

```
① 무료로 하나 출시 → ② 실제 다운로드·반응 확인 → ③ 그때 유료화·플랫폼 결정
```

$100/년(Garmin)이나 $25(Play)를 먼저 쓰기 전에 무료 출시로 반응을 보는 게 낫다.
Garmin은 무료 배포에 국가 제약이 전혀 없다.

⚠️ 위 시장 수치는 Counterpoint 요약 보도와 Garmin 실적 발표 기준이다.
집계 기관마다 "스마트워치" 정의(피트니스 밴드 포함 여부)가 달라 절대 수치는 출처별로
크게 차이 난다. 순위와 증감 추세는 일관되므로 그 부분을 근거로 쓰는 게 안전하다.

---

## 5. GPT 대화 검증 결과

### 맞은 내용

| 내용 | 확인 |
|---|---|
| Visual Studio ≠ VS Code, 필요한 건 VS Code | 맞음 |
| Monkey C 확장은 Connect IQ SDK 4.0.6 이상 필요 | 맞음 (공식 명시) |
| 최신 SDK는 Connect IQ 9.2.0 | 맞음 |
| Developer Key는 RSA **4096비트**, 분실하면 업데이트 불가 | 맞음 (2048은 서명 실패) |
| VS Code 없이 `monkeyc` CLI만으로도 빌드 가능 | 맞음 |
| 스토어 제출용 `.iq`는 `Monkey C: Export Project`로 생성 | 맞음 |

### 틀린 내용

> "브라우저에서만 개발·빌드·테스트·출시하는 공식 웹 IDE는 없습니다."

Garmin **공식** 웹 IDE가 없는 건 맞지만 결론이 과했다. 워치페이스에 한해서는
브라우저에서 디자인하고 `.prg`·`.iq`까지 뽑아주는 서드파티 웹 빌더가 존재한다
(`garmin.watchfacebuilder.com`). "설치 없이는 불가능"이 아니라
**"가능하지만 대가가 있다"**가 정확하다. 그리고 갤럭시 쪽 Watch Face Studio는
애초에 코딩이 필요 없다.

### 빠진 내용 (중요도 순)

1. **한국 판매 자격 문제를 전혀 확인하지 않았다.** GPT는
   "Merchant 계정 승인이 필요합니다"라고만 하고 유료 판매 계획을 그대로 진행했다.
   실제로는 **한국 거주자에게 그 문턱이 닫혀 있다.** 개발환경을 다 깔고
   워치페이스를 다 만든 다음에야 알게 되는 종류의 문제였다.
2. **대안 플랫폼(갤럭시/Wear OS)을 검토하지 않았다.** 사용자가 원한
   "코딩 최소화 + 판매"에 가장 잘 맞는 답이 사실 여기 있었다.
3. **시뮬레이터가 왜 중요한지 설명하지 않았다.** 워치페이스는 90%가 시각 작업이다.
   시뮬레이터 없으면 "숫자 조금 더 크게" 확인에 빌드→다운로드→USB→시계로 5분,
   있으면 10초다. 그리고 **시뮬레이터 스크린샷이 AI에게 결과를 보여줄 유일한 수단**이다.
4. **협업 방식이 "프로젝트 ZIP 전달"이다.** 수정마다 ZIP을 받아 덮어쓰는 구조는
   금방 망가진다. git이 맞고, 더 나아가면 AI를 사용자 PC에서 직접 돌리는 게 맞다.

---

## 6. Garmin으로 갈 경우 — 설치 체크리스트 (Windows)

무료 배포 / 내 시계용 / 포트폴리오 목적이면 여전히 유효하다.
GPT 순서에서 **기기 선택**과 **키 보관 위치**를 수정했다.

### 1) Java (JDK 17 또는 21 LTS, 64비트)

```
java -version
```
11 이상이면 그대로 사용. 없으면 설치 후 명령 프롬프트를 새로 열고 재확인.

### 2) Visual Studio Code
설치 시 "PATH에 추가" 체크.

### 3) Monkey C 확장 (게시자: Garmin)
`Ctrl+Shift+X` → `Monkey C` 검색 → 설치.

### 4) Connect IQ SDK Manager
https://developer.garmin.com/connect-iq/sdk/ → `Accept & Download for Windows`
→ 압축 해제 → 실행 → Garmin 계정 로그인 → **SDK 9.2.0** → **Active SDK** 지정.

**기기 데이터는 처음부터 다 받지 말 것.** 3개로 시작하면 충분하다.
- 본인이 실제로 쓰는 Garmin 모델 ← 최우선
- AMOLED 대표 1종 (Venu 3 또는 Forerunner 970)
- MIP 대표 1종 (fēnix 8 Solar)

AMOLED와 MIP은 색 표현과 상시표시(AOD) 동작이 달라 둘 다 봐야 한다.
나머지는 나중에 클릭 한 번으로 추가된다.

### 5) Developer Key

`Ctrl+Shift+P` → `Monkey C: Generate a Developer Key`

CLI로도 만들 수 있다 (VS Code 불필요):
```
openssl genrsa -out developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
    -in developer_key.pem -out developer_key.der -nocrypt
```

**보관 규칙 (사고가 가장 많이 나는 지점):**
- 저장 위치를 **프로젝트 폴더 밖으로**. 예: `C:\GarminDeveloper\Keys\`
  → 안에 두면 결국 git에 커밋된다. `.gitignore`를 믿지 말고 물리적으로 분리.
- 스토어 앱 **업데이트에 같은 키가 필요하다.** 분실 = 그 앱은 영구히 업데이트 불가.
- USB + 클라우드 등 최소 2곳 백업. 공개 저장소에 절대 올리지 않는다.

### 6) 설치 확인

`Ctrl+Shift+P` → `Monkey C` → 아래가 보이면 정상.
```
Monkey C: Verify Installation      ← 설치 직후 이걸 가장 먼저 실행
Monkey C: New Project
Monkey C: Build Current Project
Monkey C: Export Project
```
`Verify Installation`이 GPT 안내에 없었는데, 설치 진단을 해주는 명령이다.

### 참고 — 설치 없이 Garmin 빌드하는 경로 (비권장)

기술적으로 가능하다: Connect IQ SDK는 Linux 네이티브를 제공하고 `monkeyc`는 CLI이므로
GitHub Actions에서 빌드할 수 있고, 시계에 올리는 건 USB 연결 후 `GARMIN/APPS` 폴더에
`.prg` 드래그면 끝이라 개발도구가 필요 없다. Developer Key도 위 `openssl`로 만든다.

**그런데 시뮬레이터가 없어서(GUI 필요) 시각 반복 주기가 5분대로 늘어난다.**
SDK 다운로드에 Garmin 로그인/라이선스 동의가 걸려 CI 세팅 자체도 일거리다.
→ 초기 개발용으로 쓰지 말고, 나중에 릴리스 자동화용으로만 검토.

---

## 7. 협업 구조 — 실제 병목

플랫폼과 별개로, GPT 안의 진짜 병목은 설치가 아니라 **사람이 중간에서 하는 왕복**이다.

```
[GPT 안]  AI가 코드 작성 → ZIP → 사용자가 열기 → 빌드 → 에러
          → 사용자가 에러 복사 → AI에 붙여넣기 → 수정 → ZIP → 반복
```

사용자 PC에 SDK가 있는데 AI가 접근을 못 해서 사용자가 컴파일러 에러를 손으로 옮긴다.

```
[개선]    사용자 PC에 SDK + Claude Code 설치
          → AI가 직접 monkeyc 실행, 에러를 스스로 읽고 수정, 시뮬레이터 실행
          → 사용자는 화면만 보고 "숫자 더 크게"
```

**개발도구를 깔되, 사용자가 쓰는 게 아니라 AI가 쓴다.**
이 구조면 VS Code조차 필수가 아니다 (`monkeyc`/`monkeydo`/`connectiq` 전부 CLI).

단, 갤럭시(Watch Face Studio)로 가면 이 구조가 통하지 않는다.
GUI 디자인 도구라 사용자가 직접 조작해야 하고, AI는 옆에서 안내하는 역할이 된다.
**"AI가 다 만들어준다"에 가까운 건 Garmin(코드 기반) 쪽이고,
"내가 직접 쉽게 만든다"에 가까운 건 갤럭시(GUI) 쪽이다.** 이 트레이드오프가 핵심이다.

---

## 8. 다음 단계에 필요한 정보

1. **목적** — 수익화인가, 내 시계용/포트폴리오인가?
2. **보유 기기** — Garmin 모델명 / 갤럭시 워치 보유 여부
3. **워치페이스 내용** — 시간 표시 형식, 함께 보여줄 데이터
   (심박수 / 걸음 수 / 배터리 / 날짜 / 날씨), 원하는 분위기

---

## 참고 자료

**Garmin**
- Get the SDK — https://developer.garmin.com/connect-iq/sdk/
- Merchant Onboarding (판매자 국가 목록) — https://developer.garmin.com/connect-iq/monetization/merchant-onboarding/
- App Sales (구매자 국가·지원 기기·수수료) — https://developer.garmin.com/connect-iq/monetization/app-sales/
- Price Points — https://developer.garmin.com/connect-iq/monetization/price-points/
- App Review Guidelines — https://developer.garmin.com/connect-iq/app-review-guidelines/
- Monkey C VS Code 확장 — https://marketplace.visualstudio.com/items?itemName=garmin.monkey-c

**갤럭시 / Wear OS**
- Watch Face Format — https://developer.android.com/training/wearables/wff
- Watch Face Studio 다운로드 — https://developer.samsung.com/watch-face-studio/download.html
- Play Console 지원 국가 (개발자/판매자) — https://support.google.com/googleplay/android-developer/answer/9306917
- Play에 워치페이스 배포 — https://support.google.com/googleplay/android-developer/answer/13560201
- Wear OS 워치페이스 변경 공지 (WFF 마이그레이션) — https://android-developers.googleblog.com/2025/06/upcoming-changes-to-wear-os-watch-faces.html
- Galaxy Store Seller Portal — http://seller.samsungapps.com/

**시장 데이터**
- Counterpoint 2026 Q1 스마트워치 시장 — https://counterpointresearch.com/en/insights/global-smartwatch-market-q1-2026
- Garmin FY2025 실적 발표 — https://www8.garmin.com/aboutGarmin/invRelations/reports/2025_Q4_Earnings_Press_Release.pdf
- Play Store 워치페이스 판매 실태 (삼성 개발자 포럼) — https://forum.developer.samsung.com/t/wear-os-watch-faces-on-samsung-galaxy-store/15017
- Facer 디자이너 지급액 $100만 돌파 — https://news.facer.io/celebrating-1-million-in-designer-payouts-and-launching-our-first-facer-creator-partner-program-85bd2e11baff
- Facer Premium Designer 프로그램 — https://community.facer.io/t/premium-designer-program-admission-rankings-and-revenue-sharing-terms/25147
