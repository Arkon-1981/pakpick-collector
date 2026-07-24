import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Pakpick — 콘솔 게임 할인 추적",
  description:
    "닌텐도 스위치 · PlayStation · Xbox 할인 가격을 한곳에서. 역대 최저가, 가격 변동 그래프, 목표가 알림까지 — 팩픽.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

/* 페이지가 뜨기 전에 저장된 테마를 적용해서 화면 깜빡임을 막는다 */
const themeInitScript = `
try {
  var t = localStorage.getItem("pakpick-theme");
  if (t === "dark" || t === "light") document.documentElement.dataset.theme = t;
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" data-theme="light" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700;800;900&display=swap"
          rel="stylesheet"
        />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
        />
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
