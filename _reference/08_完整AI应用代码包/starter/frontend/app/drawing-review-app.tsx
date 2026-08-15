"use client";

import { ArrowRight, Blueprint, MagnifyingGlass, Path, Receipt } from "@phosphor-icons/react";

const FEATURES = [
  { id: "review", title: "AI 审核", description: "上传图纸后识别问题，并直接生成审核报告。", result: "AI 审核报告", icon: MagnifyingGlass },
  { id: "process", title: "工艺路线", description: "根据图纸事实和公开参考数据生成加工路线。", result: "AI 工艺路线卡", icon: Path },
  { id: "quote", title: "报价", description: "补齐课堂成本参数，再由程序公式计算金额。", result: "AI 参考报价单", icon: Receipt },
] as const;

export function DrawingReviewApp() {
  return (
    <div className="simple-product-shell">
      <header className="simple-header">
        <span className="simple-brand"><Blueprint size={25} /><span><strong>图纸 AI 工程助手</strong><small>Starter · 产品骨架</small></span></span>
        <nav><button className="active">工作台</button></nav>
      </header>
      <main>
        <section className="home-view">
          <div className="home-toolbar">
            <div className="home-title"><span className="home-kicker">阶段 A · 先读懂产品</span><h1>三个功能，彼此独立</h1><p>Starter 只提供真实产品骨架。下一步由学员完成 PDF 接入与 API 设置。</p></div>
            <div className="service-status-card needs-setup"><span className="service-status-icon"><Blueprint size={22} /></span><div><small>当前检查点</small><strong>产品骨架已启动</strong><em>PDF 与 AI 功能尚未实现</em></div></div>
          </div>
          <section className="task-launch-panel" aria-labelledby="starter-features">
            <div className="home-section-heading"><div><h2 id="starter-features">产品终点</h2><p>先理解输入、动作和产物，不复制完成版代码</p></div><span>3 个独立功能</span></div>
            <div className="feature-card-grid">
              {FEATURES.map(({ id, title, description, result, icon: Icon }) => (
                <article className={`feature-entry ${id}`} key={id}>
                  <div className="feature-entry-top"><span className="feature-icon"><Icon size={28} /></span><span className="feature-availability loading">待开发</span></div>
                  <span className="feature-eyebrow">独立入口</span><h3>{title}</h3><p>{description}</p>
                  <div className="feature-deliverable"><Icon size={20} /><span><small>最终产物</small><strong>{result}</strong></span></div>
                  <button disabled>从后续检查点实现 <ArrowRight /></button>
                </article>
              ))}
            </div>
          </section>
        </section>
      </main>
    </div>
  );
}
