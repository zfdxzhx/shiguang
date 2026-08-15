import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "图纸 AI 工程助手",
  description: "AI 审核、工艺路线和参考报价三个彼此独立的图纸工具",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
