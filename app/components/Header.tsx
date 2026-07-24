"use client";

/* 데스크톱 상단 헤더 — 로고 / 내비 / 검색 / 테마 / 지역 / 로그인 (시안 이식) */

import Link from "next/link";
import { useApp } from "../providers";
import { MoonIcon, SearchIcon } from "./icons";

export function DesktopHeader({ showSearch = true }: { showSearch?: boolean }) {
  const { toggleTheme, region, toggleRegion } = useApp();

  return (
    <header
      style={{
        display: "flex", alignItems: "center", gap: 22, padding: "0 40px",
        height: 64, borderBottom: "1px solid var(--color-line-neutral)",
        position: "sticky", top: 0, background: "var(--color-bg-base)", zIndex: 10,
      }}
    >
      <Link href="/" style={{ display: "flex", alignItems: "center", gap: 3 }}>
        <span style={{ font: "800 22px/1 var(--font-family-display)", letterSpacing: "-.03em", color: "var(--color-fg-strong)" }}>
          Pakpick
        </span>
        <span style={{ width: 6, height: 6, borderRadius: 999, background: "var(--color-primary)" }} />
      </Link>
      <nav style={{ display: "flex", gap: 22, flex: "none" }}>
        <Link href="/" className="t-label-1" style={{ color: "var(--color-fg-strong)", whiteSpace: "nowrap" }}>홈</Link>
        <span className="t-label-1 hover-fg" style={{ color: "var(--color-fg-neutral)", cursor: "pointer", whiteSpace: "nowrap" }}>플랫폼별</span>
        <span className="t-label-1 hover-fg" style={{ color: "var(--color-fg-neutral)", cursor: "pointer", whiteSpace: "nowrap" }}>역대 최저</span>
        <span className="t-label-1 hover-fg" style={{ color: "var(--color-fg-neutral)", cursor: "pointer", whiteSpace: "nowrap" }}>마감 임박</span>
      </nav>
      <span style={{ flex: 1 }} />
      {showSearch && (
        <div
          style={{
            display: "flex", alignItems: "center", gap: 8, width: 260, height: 38,
            padding: "0 12px", background: "var(--color-bg-alternate)",
            border: "1px solid var(--color-line-neutral)", borderRadius: 8,
            color: "var(--color-fg-assistive)",
          }}
        >
          <SearchIcon />
          <span className="t-body-2-r">게임 타이틀 검색</span>
        </div>
      )}
      <button
        onClick={toggleTheme}
        aria-label="테마 전환"
        className="hover-bg"
        style={{
          width: 36, height: 36, borderRadius: 999, border: "1px solid var(--color-line-normal)",
          background: "transparent", display: "inline-flex", alignItems: "center",
          justifyContent: "center", color: "var(--color-fg-normal)", cursor: "pointer",
        }}
      >
        <MoonIcon />
      </button>
      <button
        onClick={toggleRegion}
        className="t-label-2"
        style={{
          display: "inline-flex", alignItems: "center", gap: 5, padding: "7px 12px",
          background: "transparent", border: "1px solid var(--color-line-normal)",
          borderRadius: 999, color: "var(--color-fg-normal)", cursor: "pointer", whiteSpace: "nowrap",
        }}
      >
        {region === "KR" ? "KR ₩" : "US $"}
      </button>
      <button
        className="t-label-1"
        style={{
          height: 36, padding: "0 16px", background: "var(--color-bg-inverse)",
          color: "var(--color-fg-on-color)", border: "none", borderRadius: 8,
          cursor: "pointer", whiteSpace: "nowrap",
        }}
      >
        로그인
      </button>
    </header>
  );
}
