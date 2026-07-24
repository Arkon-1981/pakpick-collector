"use client";

/* 모바일 하단 탭 바 — 홈 / 검색 / 찜 / MY (시안 이식) */

import { HeartIcon, HomeIcon, SearchIcon, UserIcon } from "./icons";

export function MobileTabBar() {
  const item = (
    icon: React.ReactNode,
    label: string,
    active: boolean
  ) => (
    <div
      style={{
        flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
        gap: 3, color: active ? "var(--color-primary)" : "var(--color-fg-assistive)",
      }}
    >
      {icon}
      <span className="t-caption-2" style={{ fontWeight: active ? 700 : 500 }}>{label}</span>
    </div>
  );

  return (
    <nav
      style={{
        position: "fixed", bottom: 0, left: "50%", transform: "translateX(-50%)",
        width: "100%", maxWidth: 480, display: "flex",
        borderTop: "1px solid var(--color-line-neutral)",
        padding: "8px 8px 18px", background: "var(--color-bg-base)", zIndex: 20,
      }}
    >
      {item(<HomeIcon />, "홈", true)}
      {item(<SearchIcon size={21} />, "검색", false)}
      {item(<HeartIcon size={21} />, "찜", false)}
      {item(<UserIcon />, "MY", false)}
    </nav>
  );
}
