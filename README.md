# Pakpick 웹 (pakpick-web)

콘솔 게임(닌텐도/PS/Xbox) 할인 추적 서비스 **팩픽**의 웹사이트.
클로드 디자인에서 확정한 시안(홈 2a + 게임 상세)을 Next.js로 구현했습니다.

## 현재 상태

- ✅ **홈** — 오늘의 드랍 히어로 + 에디터 픽 + 할인 시세표 (플랫폼 탭 · 정렬 · 역대최저 필터 · 찜)
- ✅ **게임 상세** — 가격 변동 그래프(30일/90일/1년) · 에디션 비교 · 목표가 알림 · 찜 · 평점 · 유저 한 줄 평
- ✅ 라이트/다크 테마 전환 (설정 저장됨)
- ✅ KR ₩ / US $ 지역 전환
- ✅ 데스크톱 + 모바일 반응형 (768px 기준)
- ⏳ 데이터는 아직 **더미(가짜)** — `lib/data.ts` 파일 하나만 Supabase 조회로 바꾸면 실데이터 전환
- ⏳ PWA(홈 화면 설치) · 로그인 · 검색은 다음 단계

## 기술 구성

- Next.js 15 (App Router) + TypeScript + React 19
- 스타일: 클로드 디자인의 Wanted 디자인 시스템 토큰을 CSS 변수로 이식 (`app/globals.css`)
- 폰트: 본문 Roboto · 가격 숫자 Pretendard (tabular-nums)

## 폴더 구조

```
app/
├── page.tsx              홈 (/)
├── games/[id]/page.tsx   게임 상세 (/games/zelda-totk 등)
├── layout.tsx            공통 레이아웃 (폰트, 테마 초기화)
├── providers.tsx         전역 상태 (테마·지역·찜)
├── globals.css           디자인 토큰 (색상·타이포·다크테마)
└── components/
    ├── HomeView.tsx      홈 화면 전체
    ├── DetailView.tsx    상세 화면 전체
    ├── Header.tsx        데스크톱 헤더
    ├── MobileTabBar.tsx  모바일 하단 탭
    └── icons.tsx         SVG 아이콘 모음
lib/
└── data.ts               더미 데이터 (→ 나중에 Supabase로 교체)
```

## 실행 방법

```bash
npm install     # 처음 한 번
npm run dev     # 개발 서버 → http://localhost:3000
npm run build   # 배포용 빌드
```

## 배포 (Vercel)

1. vercel.com 가입 (GitHub 계정으로)
2. Add New → Project → 이 저장소(pakpick-web) 선택
3. 설정 변경 없이 Deploy — 끝. 자동으로 주소가 생깁니다
