/* 가짜(더미) 데이터 — 클로드 디자인 시안의 데이터를 그대로 이식.
   나중에 이 파일의 함수들을 Supabase 조회로 교체하면 실데이터로 전환된다. */

export type Platform = "PS5" | "Xbox" | "Switch";

export interface Deal {
  id: string;
  title: string;
  plat: Platform;
  krw: [number, number]; // [정가, 할인가]
  usd: [number, number];
  disc: number; // 할인율 %
  low: boolean; // 역대 최저 여부
  cov: string; // 커버 배경색
  ini: string; // 이미지 시드용 이니셜
  ends: string; // 종료까지 (D-3 등)
  sp: number; // 스파크라인 패턴 번호
}

export const DEALS: Deal[] = [
  { id: "hogwarts-legacy", title: "Hogwarts Legacy", plat: "Xbox", krw: [79900, 23970], usd: [59.99, 17.99], disc: 70, low: true, cov: "#E5F8EC", ini: "HL", ends: "D-3", sp: 0 },
  { id: "ea-fc-25", title: "EA SPORTS FC 25", plat: "PS5", krw: [84900, 25470], usd: [69.99, 20.99], disc: 70, low: false, cov: "#E5F8EC", ini: "FC", ends: "D-5", sp: 1 },
  { id: "cyberpunk-2077", title: "Cyberpunk 2077", plat: "Xbox", krw: [66000, 26400], usd: [59.99, 23.99], disc: 60, low: false, cov: "#E0F5F8", ini: "CP", ends: "D-7", sp: 2 },
  { id: "persona-5-royal", title: "페르소나 5 더 로열", plat: "Switch", krw: [62800, 25120], usd: [59.99, 23.99], disc: 60, low: true, cov: "#FFEAEA", ini: "P5", ends: "D-12", sp: 3 },
  { id: "elden-ring", title: "ELDEN RING", plat: "PS5", krw: [64800, 32400], usd: [59.99, 29.99], disc: 50, low: true, cov: "#D4E2FF", ini: "ER", ends: "D-2", sp: 0 },
  { id: "god-of-war-ragnarok", title: "God of War Ragnarök", plat: "PS5", krw: [79800, 39900], usd: [69.99, 34.99], disc: 50, low: false, cov: "#ECE0FF", ini: "GW", ends: "D-4", sp: 1 },
  { id: "diablo-4", title: "디아블로 IV", plat: "Xbox", krw: [84500, 42250], usd: [69.99, 34.99], disc: 50, low: false, cov: "#FFEAEA", ini: "D4", ends: "오늘", sp: 2 },
  { id: "hollow-knight", title: "Hollow Knight", plat: "Switch", krw: [16500, 8250], usd: [14.99, 7.49], disc: 50, low: true, cov: "#EAF2FE", ini: "HK", ends: "D-9", sp: 3 },
  { id: "sea-of-thieves", title: "Sea of Thieves", plat: "Xbox", krw: [43900, 26340], usd: [39.99, 23.99], disc: 40, low: false, cov: "#E0F5F8", ini: "ST", ends: "D-6", sp: 0 },
  { id: "zelda-totk", title: "젤다의 전설: 왕국의 눈물", plat: "Switch", krw: [79800, 55860], usd: [69.99, 48.99], disc: 30, low: true, cov: "#FFF7E0", ini: "ZL", ends: "D-8", sp: 1 },
  { id: "stellar-blade", title: "Stellar Blade", plat: "PS5", krw: [79800, 63840], usd: [69.99, 55.99], disc: 20, low: false, cov: "#F0ECFE", ini: "SB", ends: "D-10", sp: 2 },
  { id: "mario-wonder", title: "슈퍼 마리오브라더스 원더", plat: "Switch", krw: [64800, 51840], usd: [59.99, 47.99], disc: 20, low: false, cov: "#FFEAEA", ini: "MW", ends: "D-11", sp: 3 },
];

/* 90일 미니 그래프(스파크라인) 좌표 4종 */
export const SPARKS = [
  "0,6 20,6 20,12 44,12 44,9 68,9 68,16 92,16 92,13 104,13 104,23 120,23",
  "0,9 24,9 24,15 48,15 48,11 72,11 72,18 96,18 96,15 108,15 108,21 120,21",
  "0,5 18,5 18,13 40,13 40,8 64,8 64,17 88,17 88,12 102,12 102,22 120,22",
  "0,11 22,11 22,7 46,7 46,14 70,14 70,10 94,10 94,17 106,17 106,23 120,23",
];
export const SPARK_END = [23, 21, 22, 23];

/* 임시 커버 이미지 (실서비스에서는 스토어 커버 URL로 교체) */
export const img = (seed: string, w: number, h: number) =>
  `https://picsum.photos/seed/${seed}/${w}/${h}`;

/* 플랫폼별 배지 색상 */
export const PLAT_COLOR: Record<Platform, { fg: string; bg: string }> = {
  PS5: { fg: "var(--color-blue-90)", bg: "var(--color-blue-10)" },
  Xbox: { fg: "var(--color-green-100)", bg: "var(--color-green-10)" },
  Switch: { fg: "var(--color-red-100)", bg: "var(--color-red-10)" },
};

export const PLAT_LABEL: Record<Platform, string> = {
  PS5: "PlayStation 5",
  Xbox: "Xbox Series X|S",
  Switch: "Nintendo Switch",
};

export const STORE_LABEL: Record<Platform, string> = {
  PS5: "PlayStation Store",
  Xbox: "Microsoft Store",
  Switch: "닌텐도 eShop",
};

/* 에디터 픽 (홈) */
export interface Pick {
  deal: Deal;
  blurb: string;
}
export const PICKS: Pick[] = [
  { deal: DEALS.find((d) => d.id === "elden-ring")!, blurb: "DLC 출시 전 본편 정리 기회. 반값 할인은 1년에 두 번뿐이었어요." },
  { deal: DEALS.find((d) => d.id === "zelda-totk")!, blurb: "닌텐도 퍼스트 파티 30% 할인은 드뭅니다. 역대 최저가와 동일." },
  { deal: DEALS.find((d) => d.id === "cyberpunk-2077")!, blurb: "2.0 업데이트 이후 평가 반전. 확장팩 포함 에디션도 -55%." },
];

/* 상세 페이지: 가격 변동 그래프 (기간별 계단형 시리즈, 0=최고가 1=최저가) */
export const PRICE_SERIES: Record<string, number[]> = {
  "30일": [0, 0, 0.3, 0.3, 0, 0, 0.55, 0.55, 1, 1],
  "90일": [0, 0, 0.2, 0.2, 0, 0, 0.35, 0.35, 0.15, 0.15, 0.6, 0.6, 0.3, 0.3, 1, 1],
  "1년": [0.1, 0.1, 0, 0, 0.45, 0.45, 0.2, 0.2, 0.6, 0.6, 0.3, 0.3, 0.75, 0.75, 0.4, 0.4, 1, 1],
};
export const PRICE_X_LABELS: Record<string, [string, string]> = {
  "30일": ["6월 24일", "7월 9일"],
  "90일": ["4월 25일", "6월 9일"],
  "1년": ["2025년 7월", "2026년 1월"],
};

/* 상세 페이지: 유저 한 줄 평 */
export const REVIEWS = [
  { ini: "하", name: "하이랄방랑자", meta: "Switch · 플레이 120시간", text: "야숨보다 더 좋습니다. 울트라핸드 하나로 게임이 두 배가 됐어요.", likes: "214", av: "var(--color-blue-10)", avFg: "var(--color-blue-90)" },
  { ini: "게", name: "게임은세일때", meta: "Switch · 플레이 45시간", text: "정가 주고 사도 아깝지 않은데 -30%면 그냥 사세요.", likes: "156", av: "var(--color-green-10)", avFg: "var(--color-green-100)" },
  { ini: "주", name: "주말게이머", meta: "Switch · 플레이 30시간", text: "전작 안 했어도 충분히 재밌어요. 다만 초반 3시간은 튜토리얼.", likes: "89", av: "var(--color-purple-10)", avFg: "var(--color-purple-90)" },
];

/* 상세 페이지: 에디션·판매처 비교 (KR/US 금액 튜플) */
export const EDITIONS = [
  { name: "스탠다드 에디션", note: "본편", store: "닌텐도 eShop", disc: "-30%", krw: 55860, usd: 48.99 },
  { name: "본편 + 확장 패스", note: "DLC 포함", store: "닌텐도 eShop", disc: "-25%", krw: 82350, usd: 71.99 },
  { name: "패키지 (실물)", note: "배송 상품", store: "닌텐도 스토어", disc: "-10%", krw: 71820, usd: 62.99 },
];

/* 상세 페이지: 시리즈·연관작 */
export const SIMILAR = [
  { t: "젤다의 전설: 야생의 숨결", cov: "#FFF7E0", seed: "loot-BW", disc: "-30%", krw: 47460, usd: 41.99 },
  { t: "스카이워드 소드 HD", cov: "#EAF2FE", seed: "loot-SS", disc: "-40%", krw: 38880, usd: 35.99 },
  { t: "꿈꾸는 섬", cov: "#E5F8EC", seed: "loot-LA", disc: "정가", krw: 64800, usd: 59.99 },
  { t: "젤다 무쌍: 재앙의 시대", cov: "#FFEAEA", seed: "loot-HW", disc: "-45%", krw: 35640, usd: 32.99 },
];
