"use client";

/* 전역 상태: 테마(라이트/다크) + 지역(KR/US) + 찜 목록
   시안의 Tweaks(테마·지역 전환)와 찜 하트 동작을 그대로 재현한다.
   찜 목록은 localStorage에 저장 → 새로고침해도 유지 (로그인 없는 1단계 방식) */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

type Region = "KR" | "US";

interface AppState {
  theme: "light" | "dark";
  toggleTheme: () => void;
  region: Region;
  toggleRegion: () => void;
  wish: Record<string, boolean>;
  toggleWish: (id: string) => void;
}

const AppContext = createContext<AppState | null>(null);

export function Providers({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [region, setRegion] = useState<Region>("KR");
  const [wish, setWish] = useState<Record<string, boolean>>({});

  // 저장된 설정 복원
  useEffect(() => {
    try {
      const t = localStorage.getItem("pakpick-theme");
      if (t === "dark" || t === "light") setTheme(t);
      const w = localStorage.getItem("pakpick-wish");
      if (w) setWish(JSON.parse(w));
      const r = localStorage.getItem("pakpick-region");
      if (r === "KR" || r === "US") setRegion(r);
    } catch {}
  }, []);

  // 테마를 <html data-theme>에 반영
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("pakpick-theme", theme);
    } catch {}
  }, [theme]);

  const toggleTheme = useCallback(
    () => setTheme((t) => (t === "light" ? "dark" : "light")),
    []
  );
  const toggleRegion = useCallback(() => {
    setRegion((r) => {
      const next = r === "KR" ? "US" : "KR";
      try {
        localStorage.setItem("pakpick-region", next);
      } catch {}
      return next;
    });
  }, []);
  const toggleWish = useCallback((id: string) => {
    setWish((w) => {
      const next = { ...w, [id]: !w[id] };
      try {
        localStorage.setItem("pakpick-wish", JSON.stringify(next));
      } catch {}
      return next;
    });
  }, []);

  return (
    <AppContext.Provider
      value={{ theme, toggleTheme, region, toggleRegion, wish, toggleWish }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp은 Providers 안에서만 사용");
  return ctx;
}

/* 지역에 맞는 가격 표기: KR → ₩55,860 / US → $48.99 */
export function formatPrice(region: Region, krw: number, usd: number): string {
  return region === "KR"
    ? "₩" + krw.toLocaleString("ko-KR")
    : "$" + usd.toFixed(2);
}
