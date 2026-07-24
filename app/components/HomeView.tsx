"use client";

/* 홈 화면 — 클로드 디자인 확정 시안(2a: 에디토리얼 히어로 + 할인 시세표) 구현
   데스크톱 / 모바일 두 레이아웃을 모두 담고 CSS로 전환한다. */

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  DEALS, PICKS, PLAT_COLOR, SPARKS, SPARK_END, img, type Deal,
} from "@/lib/data";
import { formatPrice, useApp } from "../providers";
import { DesktopHeader } from "./Header";
import { MobileTabBar } from "./MobileTabBar";
import { BellIcon, HeartIcon, MoonIcon, PlatformIcon, SearchIcon } from "./icons";

type Tab = "전체" | "PlayStation" | "Xbox" | "Nintendo";
type Sort = "discount" | "price";

/* 정렬/필터 칩 스타일 (시안의 chip 함수 이식) */
function chipStyle(active: boolean): React.CSSProperties {
  return {
    background: active ? "var(--color-bg-inverse)" : "transparent",
    color: active ? "var(--color-fg-on-color)" : "var(--color-fg-neutral)",
    border: `1px solid ${active ? "var(--color-bg-inverse)" : "var(--color-line-normal)"}`,
    borderRadius: 999, fontWeight: 600, cursor: "pointer",
  };
}

export function HomeView() {
  const { region, toggleTheme, toggleRegion, wish, toggleWish } = useApp();
  const [tab, setTab] = useState<Tab>("전체");
  const [sort, setSort] = useState<Sort>("discount");
  const [lowOnly, setLowOnly] = useState(false);

  const fmt = (d: Deal, i: 0 | 1) => formatPrice(region, d.krw[i], d.usd[i]);

  const deals = useMemo(() => {
    let items = [...DEALS];
    if (tab !== "전체")
      items = items.filter((d) =>
        tab === "PlayStation" ? d.plat === "PS5" : tab === "Xbox" ? d.plat === "Xbox" : d.plat === "Switch"
      );
    if (lowOnly) items = items.filter((d) => d.low);
    items.sort(
      sort === "price"
        ? (a, b) => (region === "KR" ? a.krw[1] - b.krw[1] : a.usd[1] - b.usd[1])
        : (a, b) => b.disc - a.disc
    );
    return items;
  }, [tab, sort, lowOnly, region]);

  /* 역대 최저가 표기: 최저가 아닌 상품은 현재가보다 살짝 낮은 값 (시안 로직 이식) */
  const lowPrice = (d: Deal) =>
    d.low
      ? fmt(d, 1)
      : region === "KR"
        ? "₩" + (Math.round((d.krw[1] * 0.92) / 10) * 10).toLocaleString("ko-KR")
        : "$" + (Math.round(d.usd[1] * 92) / 100).toFixed(2);

  const tabs: Tab[] = ["전체", "PlayStation", "Xbox", "Nintendo"];
  const sortChips: { label: string; active: boolean; onClick: () => void }[] = [
    { label: "할인율 ↓", active: sort === "discount", onClick: () => setSort("discount") },
    { label: "낮은 가격순", active: sort === "price", onClick: () => setSort("price") },
    { label: "역대 최저만", active: lowOnly, onClick: () => setLowOnly((v) => !v) },
  ];

  const hero = DEALS[0]; // Hogwarts Legacy — 오늘의 드랍

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg-base)" }}>
      {/* ================= 데스크톱 ================= */}
      <div className="only-desktop">
        <DesktopHeader />

        {/* 히어로: 오늘의 드랍 (양 테마 모두 다크 유지 — 브랜드 앵커) */}
        <section style={{ background: "var(--color-cool-neutral-99)", color: "#fff", padding: "48px 40px 44px" }}>
          <div style={{ maxWidth: 1200, margin: "0 auto", display: "grid", gridTemplateColumns: "1fr 460px", gap: 48, alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 18, alignItems: "flex-start" }}>
              <span className="t-caption-1" style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 12px", borderRadius: 999, background: "rgba(255,255,255,.1)", color: "var(--color-blue-40)", fontWeight: 700 }}>
                오늘의 드랍 · Deal of the day
              </span>
              <h1 style={{ font: "800 52px/1.15 var(--font-family-display)", letterSpacing: "-.03em", color: "#fff" }}>
                {hero.title}
                <br />
                <span style={{ color: "var(--color-blue-50)" }}>역대 최저가 도착</span>
              </h1>
              <p className="t-body-1-r" style={{ color: "var(--color-cool-neutral-40)", maxWidth: 420 }}>
                출시 후 가장 낮은 가격입니다. 지난 90일 평균 대비 ₩31,960 저렴해요. 할인은 7월 27일에 종료됩니다.
              </p>
              <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
                <span style={{ font: "800 44px/1 var(--font-family-display)", color: "var(--color-red-70)" }}>-{hero.disc}%</span>
                <span style={{ font: "800 44px/1 var(--font-family-mono)", color: "#fff" }}>{fmt(hero, 1)}</span>
                <span className="t-body-1-r" style={{ color: "var(--color-cool-neutral-50)", textDecoration: "line-through", fontFamily: "var(--font-family-mono)" }}>{fmt(hero, 0)}</span>
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <Link href={`/games/${hero.id}`}>
                  <button className="t-label-1 btn-primary" style={{ height: 44, padding: "0 22px", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", whiteSpace: "nowrap" }}>
                    스토어에서 보기
                  </button>
                </Link>
                <button className="t-label-1 hover-white" style={{ height: 44, padding: "0 18px", background: "transparent", color: "#fff", border: "1px solid rgba(255,255,255,.25)", borderRadius: 8, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 7, whiteSpace: "nowrap" }}>
                  <BellIcon size={15} />
                  가격 알림 받기
                </button>
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ height: 260, borderRadius: 16, background: "#25272A", position: "relative", overflow: "hidden" }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={img("loot-HL-hero", 920, 520)} alt={hero.title} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
                <span className="t-caption-2" style={{ position: "absolute", top: 12, left: 12, padding: "3px 9px", borderRadius: 999, background: "var(--color-green-10)", color: "var(--color-green-100)", fontWeight: 700 }}>
                  Xbox · Series X|S
                </span>
              </div>
              <div style={{ background: "rgba(255,255,255,.06)", borderRadius: 12, padding: "14px 16px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span className="t-caption-1" style={{ color: "var(--color-cool-neutral-40)" }}>90일 가격 추이</span>
                  <span className="t-caption-1" style={{ color: "var(--color-positive)", fontWeight: 700 }}>지금이 역대 최저</span>
                </div>
                <svg width="100%" height="48" viewBox="0 0 428 48" preserveAspectRatio="none" style={{ display: "block" }}>
                  <polyline points="0,10 60,10 60,22 140,22 140,14 220,14 220,30 300,30 300,24 360,24 360,42 428,42" fill="none" stroke="var(--color-blue-50)" strokeWidth="2" strokeLinejoin="round" />
                  <circle cx="424" cy="42" r="3" fill="var(--color-blue-50)" />
                </svg>
              </div>
            </div>
          </div>
        </section>

        <main style={{ maxWidth: 1200, margin: "0 auto", padding: "36px 40px 48px" }}>
          {/* 에디터 픽 */}
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 16 }}>
            <h3 style={{ color: "var(--color-fg-strong)" }}>에디터 픽</h3>
            <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>이번 주 놓치면 아까운 3건</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 20, marginBottom: 40 }}>
            {PICKS.map((p) => (
              <Link key={p.deal.id} href={`/games/${p.deal.id}`}>
                <div className="hover-card" style={{ border: "1px solid var(--color-line-normal)", borderRadius: 16, overflow: "hidden", cursor: "pointer", background: "var(--color-bg-elevated)" }}>
                  <div style={{ height: 150, backgroundColor: p.deal.cov, backgroundImage: `url(${img("loot-" + p.deal.ini + "-w", 480, 300)})`, backgroundSize: "cover", backgroundPosition: "center", position: "relative", overflow: "hidden" }}>
                    <span className="t-caption-2" style={{ position: "absolute", top: 10, left: 10, padding: "3px 8px", borderRadius: 999, background: PLAT_COLOR[p.deal.plat].bg, color: PLAT_COLOR[p.deal.plat].fg, fontWeight: 700 }}>
                      {p.deal.plat === "PS5" ? "PS5" : p.deal.plat}
                    </span>
                  </div>
                  <div style={{ padding: "16px 18px 18px", display: "flex", flexDirection: "column", gap: 8 }}>
                    <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>{p.deal.title}</span>
                    <p className="t-body-2-r" style={{ color: "var(--color-fg-neutral)" }}>{p.blurb}</p>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 4 }}>
                      <span className="t-heading-2" style={{ color: "var(--color-negative)", fontWeight: 700 }}>-{p.deal.disc}%</span>
                      <span className="t-heading-2" style={{ color: "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{fmt(p.deal, 1)}</span>
                      <span className="t-caption-1" style={{ color: "var(--color-fg-assistive)", textDecoration: "line-through", fontFamily: "var(--font-family-mono)" }}>{fmt(p.deal, 0)}</span>
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>

          {/* 할인 시세표 */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
            <h3 style={{ color: "var(--color-fg-strong)" }}>할인 시세표</h3>
            <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)" }}>{deals.length}건 진행 중 · 매일 09:00 갱신</span>
            <span style={{ flex: 1 }} />
            {sortChips.map((c) => (
              <span key={c.label} onClick={c.onClick} className="t-caption-1" style={{ display: "inline-flex", alignItems: "center", padding: "6px 12px", ...chipStyle(c.active) }}>
                {c.label}
              </span>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            {tabs.map((t) => (
              <span key={t} onClick={() => setTab(t)} className="t-label-2" style={{ padding: "7px 14px", ...chipStyle(tab === t) }}>
                {t}
              </span>
            ))}
          </div>

          {/* 표 머리글 */}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 96px 140px 130px 90px 130px 92px", gap: 16, alignItems: "center", padding: "8px 16px", borderBottom: "1px solid var(--color-line-normal)" }}>
            {["타이틀", "플랫폼", "90일 가격 추이"].map((h) => (
              <span key={h} className="t-caption-2" style={{ color: "var(--color-fg-alternate)", fontWeight: 700 }}>{h}</span>
            ))}
            {["역대 최저", "할인", "현재가"].map((h) => (
              <span key={h} className="t-caption-2" style={{ color: "var(--color-fg-alternate)", fontWeight: 700, textAlign: "right" }}>{h}</span>
            ))}
            <span />
          </div>

          {/* 표 행 */}
          {deals.map((d) => (
            <Link key={d.id} href={`/games/${d.id}`} style={{ display: "block" }}>
              <div className="hover-row" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 96px 140px 130px 90px 130px 92px", gap: 16, alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--color-line-neutral)", cursor: "pointer" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
                  <div style={{ width: 44, height: 44, borderRadius: 6, backgroundColor: d.cov, backgroundImage: `url(${img("loot-" + d.ini, 120, 120)})`, backgroundSize: "cover", backgroundPosition: "center", flex: "none" }} />
                  <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
                    <span className="t-label-1" style={{ color: "var(--color-fg-strong)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.title}</span>
                    <span className="t-caption-1" style={{ color: "var(--color-fg-alternate)", whiteSpace: "nowrap" }}>{d.ends} 종료</span>
                  </div>
                </div>
                <span title={d.plat} style={{ justifySelf: "start", width: 30, height: 30, borderRadius: 999, background: PLAT_COLOR[d.plat].bg, color: PLAT_COLOR[d.plat].fg, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                  <PlatformIcon plat={d.plat} />
                </span>
                <svg width="120" height="28" viewBox="0 0 120 28" style={{ display: "block" }}>
                  <polyline points={SPARKS[d.sp]} fill="none" stroke="var(--color-blue-50)" strokeWidth="1.5" strokeLinejoin="round" />
                  <circle cx="116" cy={SPARK_END[d.sp]} r="2.5" fill="var(--color-blue-50)" />
                </svg>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                  <span className="t-label-2" style={{ color: "var(--color-fg-normal)", fontFamily: "var(--font-family-mono)" }}>{lowPrice(d)}</span>
                  {d.low && <span className="t-caption-2" style={{ color: "var(--color-positive)", fontWeight: 700, whiteSpace: "nowrap" }}>지금이 역대 최저</span>}
                </div>
                <span className="t-label-1" style={{ textAlign: "right", color: "var(--color-negative)", fontWeight: 700, fontFamily: "var(--font-family-mono)" }}>-{d.disc}%</span>
                <span className="t-headline-2" style={{ textAlign: "right", color: "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{fmt(d, 1)}</span>
                <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <span
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleWish(d.id); }}
                    style={{ width: 30, height: 30, borderRadius: 8, border: "1px solid var(--color-line-normal)", display: "flex", alignItems: "center", justifyContent: "center", color: wish[d.id] ? "var(--color-negative)" : "var(--color-fg-alternate)", transition: "color 150ms" }}
                  >
                    <HeartIcon filled={!!wish[d.id]} />
                  </span>
                  <span className="hover-primary-fg" style={{ width: 30, height: 30, borderRadius: 8, border: "1px solid var(--color-line-normal)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-fg-alternate)" }}>
                    <BellIcon />
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </main>
      </div>

      {/* ================= 모바일 ================= */}
      <div className="only-mobile">
        <div style={{ maxWidth: 480, margin: "0 auto", minHeight: "100vh", background: "var(--color-bg-base)", paddingBottom: 76 }}>
          <header style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 16px" }}>
            <span style={{ font: "800 19px/1 var(--font-family-display)", letterSpacing: "-.03em", color: "var(--color-fg-strong)" }}>Pakpick</span>
            <span style={{ flex: 1 }} />
            <span style={{ color: "var(--color-fg-normal)" }}><SearchIcon size={20} /></span>
            <span onClick={toggleTheme} style={{ color: "var(--color-fg-normal)", cursor: "pointer", display: "inline-flex" }}><MoonIcon size={18} /></span>
            <span onClick={toggleRegion} className="t-caption-1" style={{ padding: "5px 10px", border: "1px solid var(--color-line-normal)", borderRadius: 999, color: "var(--color-fg-normal)", fontWeight: 600, whiteSpace: "nowrap", cursor: "pointer" }}>
              {region === "KR" ? "KR ₩" : "US $"}
            </span>
          </header>

          {/* 모바일 히어로 */}
          <section style={{ margin: "0 12px", borderRadius: 16, background: "var(--color-cool-neutral-99)", color: "#fff", padding: "22px 20px" }}>
            <span className="t-caption-2" style={{ display: "inline-block", padding: "4px 10px", borderRadius: 999, background: "rgba(255,255,255,.1)", color: "var(--color-blue-40)", fontWeight: 700, marginBottom: 12 }}>오늘의 드랍</span>
            <div style={{ font: "800 26px/1.25 var(--font-family-display)", letterSpacing: "-.02em", marginBottom: 6, color: "#fff" }}>{hero.title}</div>
            <div className="t-caption-1" style={{ color: "var(--color-cool-neutral-40)", marginBottom: 14 }}>Xbox · 역대 최저가 · 7월 27일 종료</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 16 }}>
              <span style={{ font: "800 30px/1 var(--font-family-display)", color: "var(--color-red-70)" }}>-{hero.disc}%</span>
              <span style={{ font: "800 30px/1 var(--font-family-mono)", color: "#fff" }}>{fmt(hero, 1)}</span>
              <span className="t-caption-1" style={{ color: "var(--color-cool-neutral-50)", textDecoration: "line-through", fontFamily: "var(--font-family-mono)" }}>{fmt(hero, 0)}</span>
            </div>
            <svg width="100%" height="36" viewBox="0 0 300 36" preserveAspectRatio="none" style={{ display: "block", marginBottom: 16 }}>
              <polyline points="0,8 42,8 42,16 98,16 98,10 154,10 154,22 210,22 210,18 252,18 252,31 300,31" fill="none" stroke="var(--color-blue-50)" strokeWidth="1.5" strokeLinejoin="round" />
            </svg>
            <div style={{ display: "flex", gap: 8 }}>
              <Link href={`/games/${hero.id}`} style={{ flex: 1, display: "flex" }}>
                <button className="t-label-2 btn-primary" style={{ flex: 1, height: 40, color: "#fff", border: "none", borderRadius: 8, fontWeight: 600, whiteSpace: "nowrap", cursor: "pointer" }}>스토어에서 보기</button>
              </Link>
              <button className="t-label-2 hover-white" style={{ height: 40, padding: "0 14px", background: "transparent", color: "#fff", border: "1px solid rgba(255,255,255,.25)", borderRadius: 8, display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", cursor: "pointer" }}>
                <BellIcon />알림
              </button>
            </div>
          </section>

          {/* 에디터 픽 — 가로 스크롤 */}
          <div style={{ padding: "20px 16px 8px", display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>에디터 픽</span>
            <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>이번 주 3건</span>
          </div>
          <div className="scroll-x" style={{ display: "flex", gap: 10, padding: "4px 16px 12px" }}>
            {PICKS.map((p) => (
              <Link key={p.deal.id} href={`/games/${p.deal.id}`} style={{ flex: "none" }}>
                <div style={{ width: 150, border: "1px solid var(--color-line-normal)", borderRadius: 12, overflow: "hidden", background: "var(--color-bg-elevated)" }}>
                  <div style={{ height: 76, backgroundColor: p.deal.cov, backgroundImage: `url(${img("loot-" + p.deal.ini + "-w", 480, 300)})`, backgroundSize: "cover", backgroundPosition: "center" }} />
                  <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
                    <span className="t-caption-1" style={{ color: "var(--color-fg-strong)", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "block" }}>{p.deal.title}</span>
                    <div style={{ display: "flex", gap: 5, alignItems: "baseline" }}>
                      <span className="t-caption-1" style={{ color: "var(--color-negative)", fontWeight: 700 }}>-{p.deal.disc}%</span>
                      <span className="t-caption-1" style={{ color: "var(--color-fg-strong)", fontWeight: 600, fontFamily: "var(--font-family-mono)" }}>{fmt(p.deal, 1)}</span>
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>

          {/* 시세표 — 필터 칩 + 리스트 */}
          <div style={{ padding: "8px 16px 10px", display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="t-headline-2" style={{ color: "var(--color-fg-strong)" }}>할인 시세표</span>
            <span className="t-caption-2" style={{ color: "var(--color-fg-alternate)" }}>{deals.length}건</span>
          </div>
          <div className="scroll-x" style={{ display: "flex", gap: 6, padding: "0 16px 12px" }}>
            {tabs.map((t) => (
              <span key={t} onClick={() => setTab(t)} className="t-caption-1" style={{ flex: "none", padding: "6px 12px", ...chipStyle(tab === t) }}>{t}</span>
            ))}
            <span style={{ flex: "none", width: 1, background: "var(--color-line-normal)", margin: "2px 2px" }} />
            {sortChips.map((c) => (
              <span key={c.label} onClick={c.onClick} className="t-caption-1" style={{ flex: "none", padding: "6px 12px", whiteSpace: "nowrap", ...chipStyle(c.active) }}>{c.label}</span>
            ))}
          </div>
          <div>
            {deals.map((d) => (
              <Link key={d.id} href={`/games/${d.id}`} style={{ display: "block" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px", borderBottom: "1px solid var(--color-line-neutral)", cursor: "pointer" }}>
                  <div style={{ width: 44, height: 44, borderRadius: 6, backgroundColor: d.cov, backgroundImage: `url(${img("loot-" + d.ini, 120, 120)})`, backgroundSize: "cover", backgroundPosition: "center", flex: "none" }} />
                  <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                    <span className="t-label-2" style={{ color: "var(--color-fg-strong)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.title}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span title={d.plat} style={{ width: 22, height: 22, borderRadius: 999, background: PLAT_COLOR[d.plat].bg, color: PLAT_COLOR[d.plat].fg, display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
                        <PlatformIcon plat={d.plat} size={12} />
                      </span>
                      {d.low && <span className="t-caption-2" style={{ color: "var(--color-positive)", fontWeight: 700, whiteSpace: "nowrap" }}>역대 최저</span>}
                    </div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                    <span className="t-caption-1" style={{ color: "var(--color-negative)", fontWeight: 700 }}>-{d.disc}%</span>
                    <span className="t-label-1" style={{ color: "var(--color-fg-strong)", fontFamily: "var(--font-family-mono)" }}>{fmt(d, 1)}</span>
                  </div>
                  <span
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleWish(d.id); }}
                    style={{ width: 34, height: 34, borderRadius: 8, border: "1px solid var(--color-line-normal)", display: "flex", alignItems: "center", justifyContent: "center", color: wish[d.id] ? "var(--color-negative)" : "var(--color-fg-alternate)" }}
                  >
                    <HeartIcon size={15} filled={!!wish[d.id]} />
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </div>
        <MobileTabBar />
      </div>
    </div>
  );
}
