import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "HyPRA · 苏澄",
  description: "打通提示词架构与混合记忆的情感陪伴 3D 交互系统",
};

/** 移动端地址栏 / 状态栏配色与页面底色一致（避免与浅色主题割裂） */
export const viewport: Viewport = {
  themeColor: "#FAF7F2",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        {/* 魔珐星云 SDK / 网关域名预热：减少数字人首次出场时的 DNS + TLS 往返 */}
        <link rel="preconnect" href="https://media.xingyun3d.com" crossOrigin="anonymous" />
        <link
          rel="preconnect"
          href="https://nebula-agent.xingyun3d.com"
          crossOrigin="anonymous"
        />
      </head>
      <body className="min-h-screen bg-surface-base font-sans text-ink antialiased">
        {children}
      </body>
    </html>
  );
}
