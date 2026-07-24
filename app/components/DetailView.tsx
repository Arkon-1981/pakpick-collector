"use client";

/* 게임 상세 화면 — 클로드 디자인 확정 시안 구현
   가격 변동 그래프(30일/90일/1년) · 에디션 비교 · 목표가 알림 · 찜 · 평점 ·
   유저 한 줄 평 · 게임 정보 · 시리즈 연관작. 데스크톱/모바일 모두 포함. */

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  EDITIONS, PLAT_COLOR, PLAT_LABEL, PRICE_SERIES, PRICE_X_LABELS, REVIEWS,
  SIMILAR, STORE_LABEL, img, type Deal,
} from "@/lib/data";
import { formatPrice, useApp } from "../providers";
import { DesktopHeader } from "./Header";
import {
  BackIcon, BellIcon, CalendarIcon, CardIcon, ClockIcon, DownloadIcon,
  FolderIcon, GamepadIcon, HeartIcon, InfoIcon, LanguageIcon, MoonIcon,
  PlatformIcon, ThumbsUpIcon, UserIcon,
} from "./icons";

type Period = "30일" | "90일" | "1년";

function chipStyle(active: boolean): React.CSSProperties {
  return {
    background: active ? "var(--color-bg-inverse)" : "transparent",
    color: active ? "var(--color-fg-on-color)" : "var(--color-fg-neutral)",
    border: `1px solid ${active ? "var(--color-bg-inverse)" : "var(--color-line-normal)"}`,
    borderRadius: 999, fontWeight: 600, cursor: "pointer",
  };
}

/* 스탯 카드 (역대 최저 / 90일 평균 / 정가 / 최근 1년 할인) */
function Stat({ label, value, positive = false }: { label: string; value: string; positive?: boolean }) {
  return (
    <div style={{ padding: "12px 14px", background: "var(--color-bg-alternate)", borderRadius: 10, display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>{label}</span>
      <span className="t-label-1" style={{ color: positive ? "var(--color-positive)" : "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{value}</span>
    </div>
  );
}

/* 게임 정보 카드 */
function InfoCard({ icon, label, value, dim = false }: { icon: React.ReactNode; label: string; value: React.ReactNode; dim?: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 18px", border: "1px solid var(--color-line-normal)", borderRadius: 12, background: "var(--color-bg-elevated)" }}>
      <span style={{ width: 34, height: 34, borderRadius: 8, background: "var(--color-bg-alternate)", color: "var(--color-fg-neutral)", display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "none" }}>{icon}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
        <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>{label}</span>
        <span className="t-label-2" style={{ color: dim ? "var(--color-fg-assistive)" : "var(--color-fg-strong)" }}>{value}</span>
      </div>
    </div>
  );
}

export function DetailView({ deal }: { deal: Deal }) {
  const { region, toggleTheme, toggleRegion, wish, toggleWish } = useApp();
  const [period, setPeriod] = useState<Period>("90일");
  const [alertOn, setAlertOn] = useState(false);
  const [target, setTarget] = useState(deal.krw[1]);

  const KR = region === "KR";
  const fmt = (krw: number, usd: number) => formatPrice(region, krw, usd);
  const orig = fmt(deal.krw[0], deal.usd[0]);
  const sale = fmt(deal.krw[1], deal.usd[1]);
  const avg = fmt(Math.round((deal.krw[0] * 0.93) / 100) * 100, Math.round(deal.usd[0] * 93) / 100);
  const mid = fmt(Math.round((deal.krw[0] + deal.krw[1]) / 2 / 100) * 100, Math.round(((deal.usd[0] + deal.usd[1]) / 2) * 100) / 100);
  const wished = !!wish[deal.id];
  const store = STORE_LABEL[deal.plat];
  const targetLabel = KR
    ? "₩" + target.toLocaleString("ko-KR")
    : "$" + (Math.round((target / deal.krw[0]) * deal.usd[0] * 100) / 100).toFixed(2);

  /* 가격 변동 그래프 좌표 (시안 로직 이식: y 8=최고가 ~ 200=최저가) */
  const chart = useMemo(() => {
    const pts = PRICE_SERIES[period];
    return pts
      .map((f, i) => `${Math.round((i / (pts.length - 1)) * 1000)},${Math.round(8 + f * 192)}`)
      .join(" ");
  }, [period]);
  const [xStart, xMid] = PRICE_X_LABELS[period];
  const periods: Period[] = ["30일", "90일", "1년"];

  const alertBtnStyle: React.CSSProperties = {
    background: alertOn ? "var(--color-primary-bg)" : "transparent",
    color: alertOn ? "var(--color-blue-90)" : "var(--color-fg-normal)",
    border: `1px solid ${alertOn ? "var(--color-blue-30)" : "var(--color-line-normal)"}`,
  };

  /* 평점 카드 (Metacritic / OpenCritic) */
  const ratingCards = (compact: boolean) => (
    <>
      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: compact ? 8 : 10, padding: compact ? "9px 10px" : "12px 14px", border: "1px solid var(--color-line-normal)", borderRadius: 10, background: "var(--color-bg-elevated)" }}>
        <span style={{ width: compact ? 28 : 36, height: compact ? 28 : 36, borderRadius: compact ? 5 : 6, background: "#00CE7A", color: "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center", font: `700 ${compact ? 13 : 16}px/1 var(--font-family-mono)`, flex: "none" }}>96</span>
        {compact ? (
          <span className="t-caption-2" style={{ color: "var(--color-fg-neutral)", fontWeight: 600 }}>Metacritic</span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
            <span className="t-label-2" style={{ color: "var(--color-fg-strong)" }}>Metacritic</span>
            <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>평론가 145건</span>
          </div>
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: compact ? 8 : 10, padding: compact ? "9px 10px" : "12px 14px", border: "1px solid var(--color-line-normal)", borderRadius: 10, background: "var(--color-bg-elevated)" }}>
        <span style={{ width: compact ? 28 : 36, height: compact ? 28 : 36, borderRadius: 999, background: "#FC430A", color: "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center", font: `700 ${compact ? 13 : 16}px/1 var(--font-family-mono)`, flex: "none" }}>96</span>
        {compact ? (
          <span className="t-caption-2" style={{ color: "var(--color-fg-neutral)", fontWeight: 600 }}>OpenCritic</span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
            <span className="t-label-2" style={{ color: "var(--color-fg-strong)" }}>OpenCritic</span>
            <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>상위 1% · Mighty</span>
          </div>
        )}
      </div>
    </>
  );

  /* 게이머들의 선택 (구매 비율 막대) */
  const buyerBar = (h: number) => (
    <>
      <div style={{ display: "flex", height: h, borderRadius: 8, overflow: "hidden" }}>
        <div style={{ width: "24%", background: "var(--color-blue-10)", display: "flex", alignItems: "center", padding: "0 14px" }}>
          <span className="t-headline-2" style={{ color: "var(--color-blue-80)", fontWeight: 700 }}>24%</span>
        </div>
        <div style={{ width: "76%", background: "var(--color-red-10)", display: "flex", alignItems: "center", justifyContent: "flex-end", padding: "0 14px" }}>
          <span className="t-headline-2" style={{ color: "var(--color-red-80)", fontWeight: 700 }}>76%</span>
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10 }}>
        <span className="t-label-2" style={{ color: "var(--color-blue-80)" }}>안 샀어요</span>
        <span className="t-label-2" style={{ color: "var(--color-red-80)" }}>샀어요</span>
      </div>
    </>
  );

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg-base)" }}>
      {/* ================= 데스크톱 ================= */}
      <div className="only-desktop">
        <DesktopHeader showSearch={false} />
        <main style={{ maxWidth: 1200, margin: "0 auto", padding: "28px 40px 64px" }}>
          <Link href="/" className="t-label-2" style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--color-fg-neutral)", marginBottom: 20 }}>
            <BackIcon />할인 목록으로
          </Link>

          <div style={{ display: "grid", gridTemplateColumns: "440px minmax(0,1fr)", gap: 44, alignItems: "stretch", marginBottom: 44 }}>
            {/* 왼쪽: 커버 + 스크린샷 + 평점 */}
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ flex: 1, minHeight: 250, borderRadius: 16, background: `${deal.cov} url(${img("loot-" + deal.ini + "-detail", 880, 540)}) center/cover no-repeat` }} />
              <div style={{ display: "flex", gap: 10 }}>
                {[1, 2, 3].map((n) => (
                  <div key={n} style={{ flex: 1, minWidth: 0, height: 64, borderRadius: 8, background: `url(${img("loot-" + deal.ini + "-s" + n, 260, 128)}) center/cover no-repeat`, cursor: "pointer" }} />
                ))}
              </div>
              <div style={{ display: "flex", gap: 10 }}>{ratingCards(false)}</div>
            </div>

            {/* 오른쪽: 정보 */}
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 22, height: 22, borderRadius: 5, background: PLAT_COLOR[deal.plat].bg, color: PLAT_COLOR[deal.plat].fg, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                  <PlatformIcon plat={deal.plat} size={13} />
                </span>
                <span className="t-label-2" style={{ color: "var(--color-fg-neutral)" }}>{PLAT_LABEL[deal.plat]} · {store}</span>
                {deal.low && <span className="t-caption-2" style={{ padding: "3px 9px", borderRadius: 4, background: "var(--color-positive-bg)", color: "var(--color-green-100)", fontWeight: 700 }}>역대 최저</span>}
              </div>
              <h1 style={{ font: "800 36px/1.2 var(--font-family-display)", letterSpacing: "-.02em", color: "var(--color-fg-strong)" }}>{deal.title}</h1>
              <div className="t-body-2-r" style={{ color: "var(--color-fg-neutral)" }}>The Legend of Zelda: Tears of the Kingdom · Nintendo</div>

              {/* 출시일 / 장르 / 이용 등급 */}
              <div style={{ display: "flex", gap: 20 }}>
                {[
                  { icon: <CalendarIcon />, label: "출시일", value: "2023-05-12", mono: true },
                  { icon: <GamepadIcon />, label: "장르", value: "액션 어드벤처", mono: false },
                  { icon: <InfoIcon />, label: "이용 등급", value: "전체 이용가", mono: false },
                ].map((m) => (
                  <div key={m.label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 30, height: 30, borderRadius: 8, background: "var(--color-bg-alternate)", color: "var(--color-fg-neutral)", display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "none" }}>{m.icon}</span>
                    <div style={{ display: "flex", flexDirection: "column" }}>
                      <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>{m.label}</span>
                      <span className="t-label-2" style={{ color: "var(--color-fg-normal)", fontFamily: m.mono ? "var(--font-family-mono)" : undefined }}>{m.value}</span>
                    </div>
                  </div>
                ))}
              </div>

              {/* 가격 */}
              <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginTop: 4 }}>
                <span style={{ font: "800 40px/1 var(--font-family-display)", color: "var(--color-negative)" }}>-{deal.disc}%</span>
                <span style={{ font: "800 40px/1 var(--font-family-mono)", color: "var(--color-fg-strong)" }}>{sale}</span>
                <span className="t-body-1-r" style={{ color: "var(--color-fg-assistive)", textDecoration: "line-through", fontFamily: "var(--font-family-mono)" }}>{orig}</span>
              </div>
              <div className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>
                할인 종료까지 <span style={{ color: "var(--color-negative)", fontWeight: 700 }}>8일 4시간</span> · 2026-08-01 23:59 KST 종료
              </div>

              {/* 구매 / 찜 / 알림 */}
              <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
                <button className="t-label-1 btn-primary" style={{ flex: 1, height: 46, padding: "0 24px", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", whiteSpace: "nowrap", fontSize: 16 }}>
                  {store}에서 구매
                </button>
                <button onClick={() => toggleWish(deal.id)} className="t-label-1 hover-bg" style={{ height: 46, padding: "0 18px", background: "transparent", color: wished ? "var(--color-negative)" : "var(--color-fg-normal)", border: "1px solid var(--color-line-normal)", borderRadius: 8, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 7, whiteSpace: "nowrap" }}>
                  <HeartIcon size={16} filled={wished} />
                  {wished ? "찜 완료" : "찜하기"}
                </button>
                <button onClick={() => setAlertOn((v) => !v)} className="t-label-1" style={{ height: 46, padding: "0 18px", borderRadius: 8, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 7, whiteSpace: "nowrap", ...alertBtnStyle }}>
                  <BellIcon size={16} />
                  {alertOn ? "알림 설정됨" : "가격 알림"}
                </button>
              </div>

              {/* 목표가 알림 패널 */}
              {alertOn && (
                <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 16px", background: "var(--color-primary-bg)", borderRadius: 12 }}>
                  <span className="t-label-2" style={{ color: "var(--color-blue-90)", whiteSpace: "nowrap" }}>목표가 알림</span>
                  <span className="t-caption-1" style={{ color: "var(--color-fg-neutral)" }}>
                    가격이 <span style={{ fontFamily: "var(--font-family-mono)", fontWeight: 700, color: "var(--color-blue-90)" }}>{targetLabel}</span> 이하로 내려가면 푸시 알림을 보내드려요
                  </span>
                  <span style={{ flex: 1 }} />
                  <button onClick={() => setTarget((t) => Math.max(9800, t - 5000))} style={{ width: 28, height: 28, borderRadius: 8, background: "var(--color-bg-base)", border: "1px solid var(--color-line-normal)", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "var(--color-fg-normal)", fontWeight: 700 }}>−</button>
                  <button onClick={() => setTarget((t) => Math.min(deal.krw[0], t + 5000))} style={{ width: 28, height: 28, borderRadius: 8, background: "var(--color-bg-base)", border: "1px solid var(--color-line-normal)", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "var(--color-fg-normal)", fontWeight: 700 }}>+</button>
                </div>
              )}

              {/* 가격 스탯 4종 */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginTop: 4 }}>
                <Stat label="역대 최저" value={sale} positive />
                <Stat label="90일 평균" value={avg} />
                <Stat label="정가" value={orig} />
                <Stat label="최근 1년 할인" value="4회" />
              </div>

              {/* 게이머들의 선택 */}
              <div style={{ border: "1px solid var(--color-line-normal)", borderRadius: 12, padding: "16px 18px", background: "var(--color-bg-elevated)", marginTop: 4 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 12 }}>
                  <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>게이머들의 선택</span>
                  <span style={{ flex: 1 }} />
                  <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>이번 할인 기간 · 1,842명</span>
                </div>
                {buyerBar(52)}
              </div>
            </div>
          </div>

          {/* 유저 한 줄 평 */}
          <section style={{ marginBottom: 36 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
              <h3 style={{ color: "var(--color-fg-strong)" }}>유저 한 줄 평</h3>
              <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>유저 평점 8.3 · 24,304명 참여</span>
              <span style={{ flex: 1 }} />
              <span className="t-label-2" style={{ color: "var(--color-primary)", cursor: "pointer" }}>전체 보기</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16 }}>
              {REVIEWS.map((r) => (
                <div key={r.name} style={{ display: "flex", flexDirection: "column", gap: 10, padding: "16px 18px", border: "1px solid var(--color-line-normal)", borderRadius: 12, background: "var(--color-bg-elevated)" }}>
                  <p className="t-body-2-r" style={{ color: "var(--color-fg-normal)" }}>“{r.text}”</p>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: "auto" }}>
                    <span style={{ width: 26, height: 26, borderRadius: 999, background: r.av, color: r.avFg, display: "inline-flex", alignItems: "center", justifyContent: "center", font: "700 12px/1 var(--font-family-base)", flex: "none" }}>{r.ini}</span>
                    <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
                      <span className="t-caption-1" style={{ color: "var(--color-fg-strong)", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                      <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", whiteSpace: "nowrap" }}>{r.meta}</span>
                    </div>
                    <span style={{ flex: 1 }} />
                    <span className="t-caption-2 hover-primary-fg" style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--color-fg-neutral)", whiteSpace: "nowrap", cursor: "pointer" }}>
                      <ThumbsUpIcon />{r.likes}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* 가격 변동 그래프 */}
          <section style={{ border: "1px solid var(--color-line-normal)", borderRadius: 16, padding: "24px 28px", marginBottom: 36, background: "var(--color-bg-elevated)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
              <h3 style={{ color: "var(--color-fg-strong)" }}>가격 변동</h3>
              <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>{store} 한국 기준 · 매일 09:00 수집</span>
              <span style={{ flex: 1 }} />
              {periods.map((p) => (
                <span key={p} onClick={() => setPeriod(p)} className="t-caption-1" style={{ padding: "6px 12px", ...chipStyle(period === p) }}>{p}</span>
              ))}
            </div>
            <div style={{ display: "flex", gap: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: 200, padding: "4px 0" }}>
                {[orig, mid, sale].map((v, i) => (
                  <span key={i} className="t-caption-2" style={{ color: "var(--color-fg-assistive)", fontFamily: "var(--font-family-mono)" }}>{v}</span>
                ))}
              </div>
              <svg width="100%" height="208" viewBox="0 0 1000 208" preserveAspectRatio="none" style={{ display: "block", flex: 1 }}>
                <line x1="0" y1="8" x2="1000" y2="8" stroke="var(--color-line-neutral)" strokeWidth="1" />
                <line x1="0" y1="104" x2="1000" y2="104" stroke="var(--color-line-neutral)" strokeWidth="1" />
                <line x1="0" y1="200" x2="1000" y2="200" stroke="var(--color-line-neutral)" strokeWidth="1" />
                <line x1="0" y1="200" x2="1000" y2="200" stroke="var(--color-green-80)" strokeWidth="1" strokeDasharray="4 4" opacity="0.6" />
                <polyline points={chart} fill="none" stroke="var(--color-primary)" strokeWidth="2.5" strokeLinejoin="round" />
                <circle cx="996" cy="200" r="4" fill="var(--color-primary)" />
              </svg>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, paddingLeft: 60 }}>
              <span className="t-caption-2" style={{ color: "var(--color-fg-assistive)" }}>{xStart}</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-assistive)" }}>{xMid}</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-assistive)" }}>오늘</span>
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 12, paddingLeft: 60 }}>
              <span className="t-caption-1" style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--color-fg-neutral)" }}>
                <span style={{ width: 14, height: 2.5, background: "var(--color-primary)", borderRadius: 2 }} />판매가
              </span>
              <span className="t-caption-1" style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--color-fg-neutral)" }}>
                <span style={{ width: 14, height: 0, borderTop: "2px dashed var(--color-green-80)" }} />역대 최저 {sale}
              </span>
            </div>
          </section>

          {/* 에디션 · 판매처 비교 */}
          <section style={{ marginBottom: 36 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
              <h3 style={{ color: "var(--color-fg-strong)" }}>에디션 · 판매처 비교</h3>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 130px 110px 130px 130px", gap: 16, alignItems: "center", padding: "8px 16px", borderBottom: "1px solid var(--color-line-normal)" }}>
              <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", fontWeight: 700 }}>에디션</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", fontWeight: 700 }}>판매처</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", fontWeight: 700, textAlign: "right" }}>할인</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)", fontWeight: 700, textAlign: "right" }}>현재가</span>
              <span />
            </div>
            {EDITIONS.map((e) => (
              <div key={e.name} className="hover-row" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 130px 110px 130px 130px", gap: 16, alignItems: "center", padding: "12px 16px", borderBottom: "1px solid var(--color-line-neutral)" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                  <span className="t-label-1" style={{ color: "var(--color-fg-strong)" }}>{e.name}</span>
                  <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>{e.note}</span>
                </div>
                <span className="t-label-2" style={{ color: "var(--color-fg-neutral)" }}>{e.store}</span>
                <span className="t-label-1" style={{ textAlign: "right", color: "var(--color-negative)", fontWeight: 700, fontFamily: "var(--font-family-mono)" }}>{e.disc}</span>
                <span className="t-headline-2" style={{ textAlign: "right", color: "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{fmt(e.krw, e.usd)}</span>
                <button className="t-label-2 hover-bg" style={{ height: 32, padding: "0 14px", background: "transparent", border: "1px solid var(--color-line-normal)", borderRadius: 8, color: "var(--color-fg-normal)", cursor: "pointer", justifySelf: "end" }}>스토어 이동</button>
              </div>
            ))}
          </section>

          {/* 게임 정보 */}
          <section style={{ marginBottom: 36 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
              <h3 style={{ color: "var(--color-fg-strong)" }}>게임 정보</h3>
              <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>IGDB · eShop 기준</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16 }}>
              <InfoCard icon={<ClockIcon />} label="플레이 타임" value="메인 32시간 · 완전 클리어 120시간" />
              <InfoCard icon={<FolderIcon />} label="개발 · 퍼블리셔" value="Nintendo EPD · Nintendo" />
              <InfoCard icon={<LanguageIcon />} label="지원 언어" value={<>한국어 자막 <span style={{ color: "var(--color-positive)" }}>○</span> · 음성 <span style={{ color: "var(--color-fg-assistive)" }}>—</span></>} />
              <InfoCard icon={<UserIcon size={16} />} label="플레이 인원" value="1인 · 온라인/로컬 멀티 미지원" />
              <InfoCard icon={<DownloadIcon />} label="파일 용량" value={<span style={{ fontFamily: "var(--font-family-mono)" }}>18.2 GB</span>} />
              <InfoCard icon={<CardIcon />} label="구독 카탈로그" value="Game Pass · PS Plus 미포함" dim />
            </div>
          </section>

          {/* 시리즈 · 연관작 */}
          <section>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
              <h3 style={{ color: "var(--color-fg-strong)" }}>시리즈 · 연관작</h3>
              <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>젤다의 전설 시리즈</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 20 }}>
              {SIMILAR.map((s) => (
                <div key={s.t} className="hover-card" style={{ border: "1px solid var(--color-line-normal)", borderRadius: 12, overflow: "hidden", cursor: "pointer", background: "var(--color-bg-elevated)" }}>
                  <div style={{ height: 110, backgroundColor: s.cov, backgroundImage: `url(${img(s.seed, 300, 160)})`, backgroundSize: "cover", backgroundPosition: "center" }} />
                  <div style={{ padding: "12px 14px 14px", display: "flex", flexDirection: "column", gap: 6 }}>
                    <span className="t-label-2" style={{ color: "var(--color-fg-strong)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.t}</span>
                    <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                      <span className="t-label-2" style={{ color: "var(--color-negative)", fontWeight: 700 }}>{s.disc}</span>
                      <span className="t-label-1" style={{ color: "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{fmt(s.krw, s.usd)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </main>
      </div>

      {/* ================= 모바일 ================= */}
      <div className="only-mobile">
        <div style={{ maxWidth: 480, margin: "0 auto", minHeight: "100vh", background: "var(--color-bg-base)", paddingBottom: 96 }}>
          <header style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px", position: "sticky", top: 0, background: "var(--color-bg-base)", zIndex: 10 }}>
            <Link href="/" style={{ color: "var(--color-fg-normal)", display: "inline-flex" }}><BackIcon size={22} /></Link>
            <span style={{ flex: 1 }} />
            <span onClick={toggleTheme} style={{ color: "var(--color-fg-normal)", cursor: "pointer", display: "inline-flex" }}><MoonIcon size={18} /></span>
            <span onClick={toggleRegion} className="t-caption-1" style={{ padding: "5px 10px", border: "1px solid var(--color-line-normal)", borderRadius: 999, color: "var(--color-fg-normal)", fontWeight: 600, cursor: "pointer" }}>{region === "KR" ? "KR ₩" : "US $"}</span>
            <span onClick={() => toggleWish(deal.id)} style={{ color: wished ? "var(--color-negative)" : "var(--color-fg-normal)", cursor: "pointer", display: "inline-flex" }}>
              <HeartIcon size={20} filled={wished} />
            </span>
          </header>

          <div style={{ height: 210, margin: "0 16px", borderRadius: 16, background: `${deal.cov} url(${img("loot-" + deal.ini + "-detail", 880, 540)}) center/cover no-repeat` }} />
          <div style={{ display: "flex", gap: 8, margin: "10px 16px 0" }}>{ratingCards(true)}</div>

          <div style={{ padding: "16px 16px 0", display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ width: 18, height: 18, borderRadius: 4, background: PLAT_COLOR[deal.plat].bg, color: PLAT_COLOR[deal.plat].fg, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                <PlatformIcon plat={deal.plat} size={11} />
              </span>
              <span className="t-caption-1" style={{ color: "var(--color-fg-neutral)" }}>{PLAT_LABEL[deal.plat]} · {store}</span>
              {deal.low && <span className="t-caption-2" style={{ padding: "2px 8px", borderRadius: 4, background: "var(--color-positive-bg)", color: "var(--color-green-100)", fontWeight: 700 }}>역대 최저</span>}
            </div>
            <h2 style={{ font: "800 24px/1.3 var(--font-family-display)", letterSpacing: "-.02em", color: "var(--color-fg-strong)" }}>{deal.title}</h2>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span style={{ font: "800 28px/1 var(--font-family-display)", color: "var(--color-negative)" }}>-{deal.disc}%</span>
              <span style={{ font: "800 28px/1 var(--font-family-mono)", color: "var(--color-fg-strong)" }}>{sale}</span>
              <span className="t-caption-1" style={{ color: "var(--color-fg-assistive)", textDecoration: "line-through", fontFamily: "var(--font-family-mono)" }}>{orig}</span>
            </div>
            <div className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>
              종료까지 <span style={{ color: "var(--color-negative)", fontWeight: 700 }}>8일 4시간</span> · 08-01 23:59
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginTop: 2 }}>
              <Stat label="역대 최저" value={sale} positive />
              <Stat label="90일 평균" value={avg} />
              <Stat label="정가" value={orig} />
            </div>

            {/* 게이머들의 선택 */}
            <div style={{ border: "1px solid var(--color-line-normal)", borderRadius: 12, padding: 14, background: "var(--color-bg-elevated)" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
                <span className="t-label-1" style={{ color: "var(--color-fg-strong)" }}>게이머들의 선택</span>
                <span style={{ flex: 1 }} />
                <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>1,842명</span>
              </div>
              {buyerBar(42)}
            </div>

            {alertOn && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", background: "var(--color-primary-bg)", borderRadius: 12 }}>
                <span className="t-caption-1" style={{ color: "var(--color-blue-90)", flex: 1 }}>
                  목표가 <span style={{ fontFamily: "var(--font-family-mono)", fontWeight: 700 }}>{targetLabel}</span> 이하일 때 푸시 알림
                </span>
                <button onClick={() => setTarget((t) => Math.max(9800, t - 5000))} style={{ width: 30, height: 30, borderRadius: 8, background: "var(--color-bg-base)", border: "1px solid var(--color-line-normal)", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "var(--color-fg-normal)", fontWeight: 700 }}>−</button>
                <button onClick={() => setTarget((t) => Math.min(deal.krw[0], t + 5000))} style={{ width: 30, height: 30, borderRadius: 8, background: "var(--color-bg-base)", border: "1px solid var(--color-line-normal)", display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "var(--color-fg-normal)", fontWeight: 700 }}>+</button>
              </div>
            )}
          </div>

          {/* 유저 한 줄 평 — 가로 스크롤 */}
          <div style={{ padding: "20px 16px 8px", display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>유저 한 줄 평</span>
            <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>8.3 · 24,304명</span>
          </div>
          <div className="scroll-x" style={{ display: "flex", gap: 10, padding: "4px 16px 4px" }}>
            {REVIEWS.map((r) => (
              <div key={r.name} style={{ flex: "none", width: 240, display: "flex", flexDirection: "column", gap: 8, padding: "12px 14px", border: "1px solid var(--color-line-normal)", borderRadius: 12, background: "var(--color-bg-elevated)" }}>
                <p className="t-caption-1" style={{ color: "var(--color-fg-normal)", lineHeight: 1.5 }}>“{r.text}”</p>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: "auto" }}>
                  <span style={{ width: 20, height: 20, borderRadius: 999, background: r.av, color: r.avFg, display: "inline-flex", alignItems: "center", justifyContent: "center", font: "700 10px/1 var(--font-family-base)", flex: "none" }}>{r.ini}</span>
                  <span className="t-caption-2" style={{ color: "var(--color-fg-strong)", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                  <span style={{ flex: 1 }} />
                  <span className="t-caption-2" style={{ display: "inline-flex", alignItems: "center", gap: 3, color: "var(--color-fg-alternate)", whiteSpace: "nowrap" }}>
                    <ThumbsUpIcon size={11} />{r.likes}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* 가격 변동 */}
          <div style={{ margin: "16px 16px 0", border: "1px solid var(--color-line-normal)", borderRadius: 16, padding: 16, background: "var(--color-bg-elevated)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>가격 변동</span>
              <span style={{ flex: 1 }} />
              {periods.map((p) => (
                <span key={p} onClick={() => setPeriod(p)} className="t-caption-2" style={{ padding: "5px 10px", ...chipStyle(period === p) }}>{p}</span>
              ))}
            </div>
            <svg width="100%" height="140" viewBox="0 0 1000 208" preserveAspectRatio="none" style={{ display: "block" }}>
              <line x1="0" y1="200" x2="1000" y2="200" stroke="var(--color-green-80)" strokeWidth="1.5" strokeDasharray="5 5" opacity="0.6" />
              <polyline points={chart} fill="none" stroke="var(--color-primary)" strokeWidth="3" strokeLinejoin="round" />
              <circle cx="996" cy="200" r="5" fill="var(--color-primary)" />
            </svg>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
              <span className="t-caption-2" style={{ color: "var(--color-fg-assistive)" }}>{xStart}</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-assistive)" }}>{xMid}</span>
              <span className="t-caption-2" style={{ color: "var(--color-fg-assistive)" }}>오늘</span>
            </div>
          </div>

          {/* 에디션 · 판매처 */}
          <div style={{ padding: "20px 16px 8px" }}><span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>에디션 · 판매처</span></div>
          <div>
            {EDITIONS.map((e) => (
              <div key={e.name} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderBottom: "1px solid var(--color-line-neutral)" }}>
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
                  <span className="t-label-2" style={{ color: "var(--color-fg-strong)" }}>{e.name}</span>
                  <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>{e.store} · {e.note}</span>
                </div>
                <span className="t-caption-1" style={{ color: "var(--color-negative)", fontWeight: 700 }}>{e.disc}</span>
                <span className="t-label-1" style={{ color: "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{fmt(e.krw, e.usd)}</span>
              </div>
            ))}
          </div>

          {/* 게임 정보 */}
          <div style={{ padding: "20px 16px 8px" }}><span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>게임 정보</span></div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, padding: "4px 16px 4px" }}>
            {[
              { label: "플레이 타임", value: "메인 32h · 클리어 120h" },
              { label: "지원 언어", value: "한국어 자막 ○ · 음성 —" },
              { label: "플레이 인원", value: "1인 · 멀티 미지원" },
              { label: "파일 용량", value: "18.2 GB", mono: true },
              { label: "개발 · 퍼블리셔", value: "Nintendo EPD" },
              { label: "구독 카탈로그", value: "미포함", dim: true },
            ].map((m) => (
              <div key={m.label} style={{ padding: "10px 12px", background: "var(--color-bg-alternate)", borderRadius: 10, display: "flex", flexDirection: "column", gap: 1 }}>
                <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>{m.label}</span>
                <span className="t-caption-1" style={{ color: m.dim ? "var(--color-fg-assistive)" : "var(--color-fg-strong)", fontWeight: 600, fontFamily: m.mono ? "var(--font-family-mono)" : undefined }}>{m.value}</span>
              </div>
            ))}
          </div>

          {/* 시리즈 · 연관작 — 가로 스크롤 */}
          <div style={{ padding: "20px 16px 8px", display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>시리즈 · 연관작</span>
            <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>젤다의 전설 시리즈</span>
          </div>
          <div className="scroll-x" style={{ display: "flex", gap: 10, padding: "4px 16px 12px" }}>
            {SIMILAR.map((s) => (
              <div key={s.t} style={{ flex: "none", width: 150, border: "1px solid var(--color-line-normal)", borderRadius: 12, overflow: "hidden", background: "var(--color-bg-elevated)" }}>
                <div style={{ height: 76, backgroundColor: s.cov, backgroundImage: `url(${img(s.seed, 300, 160)})`, backgroundSize: "cover", backgroundPosition: "center" }} />
                <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
                  <span className="t-caption-1" style={{ color: "var(--color-fg-strong)", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "block" }}>{s.t}</span>
                  <div style={{ display: "flex", gap: 5, alignItems: "baseline" }}>
                    <span className="t-caption-1" style={{ color: "var(--color-negative)", fontWeight: 700 }}>{s.disc}</span>
                    <span className="t-caption-1" style={{ color: "var(--color-fg-strong)", fontWeight: 600, fontFamily: "var(--font-family-mono)" }}>{fmt(s.krw, s.usd)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* 하단 고정 구매 바 */}
          <div style={{ position: "fixed", bottom: 0, left: "50%", transform: "translateX(-50%)", width: "100%", maxWidth: 480, display: "flex", gap: 8, padding: "12px 16px 22px", background: "var(--color-bg-base)", borderTop: "1px solid var(--color-line-neutral)", zIndex: 20 }}>
            <button onClick={() => setAlertOn((v) => !v)} style={{ width: 46, height: 46, borderRadius: 8, display: "inline-flex", alignItems: "center", justifyContent: "center", cursor: "pointer", ...alertBtnStyle }}>
              <BellIcon size={18} />
            </button>
            <button className="t-label-1 btn-primary" style={{ flex: 1, height: 46, color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", whiteSpace: "nowrap" }}>
              {store}에서 구매 · {sale}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
