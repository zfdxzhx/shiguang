import type { Metadata } from "next";
import { DrawingReviewApp } from "./drawing-review-app";

export const metadata: Metadata = {
  title: "图纸 AI 工程助手",
  description: "AI 审核、工艺路线和参考报价三个彼此独立的图纸工具",
};

export default function Home() {
  return <DrawingReviewApp />;
}
