"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Blueprint, Check, FilePdf, GearSix, MagnifyingGlass, Path, Receipt, Sparkle, UploadSimple, Warning } from "@phosphor-icons/react";

type Config = { configured: boolean; provider: string; visual_model?: string; credential_available?: boolean; secondary_credential_available?: boolean };
type DocumentRecord = { id: string; filename: string; page_count: number; sha256: string; page_urls: string[] };
type Run = { id: string; status: string; error?: string; output?: { summary: string; report_url: string; findings?: Array<{ id: string; conclusion: string; impact?: string; recommendation?: string }> } };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.detail || `请求失败（${response.status}）`);
  return body as T;
}

export function DrawingReviewApp() {
  const [config, setConfig] = useState<Config | null>(null);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [provider, setProvider] = useState("hybrid");
  const [model, setModel] = useState("gemini-3.6-flash");
  const [primaryKey, setPrimaryKey] = useState("");
  const [secondaryKey, setSecondaryKey] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => { void api<Config>("/api/v1/ai/status").then(setConfig).catch(() => setMessage("无法读取 API 设置")); }, []);
  useEffect(() => {
    if (!run || !["queued", "validating", "rendering", "calling_ai", "analyzing", "processing"].includes(run.status)) return;
    const timer = window.setTimeout(() => { void api<Run>(`/api/v1/features/runs/${run.id}`).then(setRun).catch((error) => setMessage(error.message)); }, 1000);
    return () => window.clearTimeout(timer);
  }, [run]);

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault(); setBusy("settings");
    try { const next = await api<Config>("/api/v1/ai/config", { method: "POST", body: JSON.stringify({ provider, model, api_key: primaryKey || undefined, reuse_primary: !primaryKey && Boolean(config?.credential_available), secondary_model: "deepseek-v4-flash", secondary_api_key: secondaryKey || undefined, reuse_secondary: !secondaryKey && Boolean(config?.secondary_credential_available), storage: "session" }) }); setConfig(next); setShowSettings(false); setMessage("API 设置已保存。 "); }
    catch (error) { setMessage(error instanceof Error ? error.message : "API 设置失败"); }
    finally { setBusy(""); }
  };

  const upload = async (file?: File) => {
    if (!file) return; setBusy("upload"); setRun(null);
    try { const body = new FormData(); body.append("file", file); setDocument(await api<DocumentRecord>("/api/v1/documents", { method: "POST", body })); setMessage("图纸已在本机完成校验与分页。 "); }
    catch (error) { setMessage(error instanceof Error ? error.message : "PDF 上传失败"); }
    finally { setBusy(""); }
  };

  const startReview = async () => {
    if (!document || !config?.configured) return; setBusy("run");
    try { setRun(await api<Run>("/api/v1/features/review/runs", { method: "POST", body: JSON.stringify({ document_id: document.id, external_processing_consent: true }) })); setMessage("AI 正在生成审核报告。 "); }
    catch (error) { setMessage(error instanceof Error ? error.message : "审核没有启动"); }
    finally { setBusy(""); }
  };

  const changeProvider = (value: string) => { setProvider(value); setModel(value === "hybrid" ? "gemini-3.6-flash" : "k3"); setPrimaryKey(""); };
  const completed = run?.status === "completed" && run.output;

  return (
    <div className="simple-product-shell">
      <header className="simple-header"><span className="simple-brand"><Blueprint size={25} /><span><strong>图纸 AI 工程助手</strong><small>Checkpoint 2 · 独立 AI 审核</small></span></span><nav><button className="active"><MagnifyingGlass />AI 审核</button><button onClick={() => setShowSettings(true)}><GearSix />API 设置</button></nav></header>
      {message && <div className="simple-notice info" role="status"><span>{message.includes("失败") || message.includes("无法") ? <Warning /> : <Check />}</span><div><strong>当前结果</strong><p>{message}</p></div></div>}
      <main><section className="feature-workspace">
        <div className="feature-page-heading"><div><span className="feature-icon review"><MagnifyingGlass /></span><div><span>Checkpoint 2</span><h1>AI 审核</h1><p>从一份新 PDF 独立开始，完成后直接生成报告。</p></div></div></div>
        {!completed && <div className="feature-run-layout"><article className="run-card">
          <label className={`simple-dropzone ${document ? "has-file" : ""}`}><input type="file" accept="application/pdf,.pdf" onChange={(event) => void upload(event.target.files?.[0])} />{busy === "upload" ? <strong>正在处理…</strong> : document ? <><FilePdf size={35} /><strong>{document.filename}</strong><small>{document.page_count} 页 · 已完成本地校验</small></> : <><UploadSimple size={36} /><strong>选择 PDF 图纸</strong><small>支持 PDF</small></>}</label>
          {!config?.configured && <button className="configure-inline" onClick={() => setShowSettings(true)}><GearSix />先设置 API 接入</button>}
          <button className="primary-run-button" disabled={!document || !config?.configured || busy === "run"} onClick={() => void startReview()}><Sparkle />{busy === "run" || (run && run.status !== "completed" && run.status !== "failed") ? "正在生成审核报告…" : "开始 AI 审核"}</button>
          <small className="run-boundary">上传后直接生成图纸 AI 审核报告。</small>
        </article><article className={`preview-card ${document ? "has-document" : "empty"}`}>{document?.page_urls?.[0] ? <DocumentPreview src={document.page_urls[0]} alt={`${document.filename} 第 1 页`} /> : <div className="empty-preview"><FilePdf size={58} /><strong>图纸预览</strong><small>选择文件后显示第一页</small></div>}</article></div>}
        {run?.status === "failed" && <div className="failed-result"><Warning /><div><strong>本次运行安全停止</strong><p>{run.error}</p></div></div>}
        {completed && <section className="feature-result"><div className="result-actions"><a className="download-report" href={run.output?.report_url} target="_blank" rel="noreferrer"><FilePdf />下载 PDF 报告</a></div><article className="result-card findings-result"><div className="result-card-heading"><div><span>审核发现</span><h2>问题、影响与建议</h2></div></div><div className="simple-finding-list">{run.output?.findings?.map((item, index) => <article key={item.id}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.conclusion}</strong>{item.impact && <p><b>可能影响</b>{item.impact}</p>}{item.recommendation && <p><b>建议措施</b>{item.recommendation}</p>}</div></article>)}</div></article></section>}
        <section className="task-launch-panel"><div className="home-section-heading"><div><h2>下一检查点</h2><p>工艺路线和报价尚未注册产品路由</p></div></div><div className="feature-card-grid"><article className="feature-entry process"><span className="feature-icon"><Path /></span><h3>工艺路线</h3><p>CP3 实现参考资料匹配和工艺路线卡。</p><button disabled>尚未实现</button></article><article className="feature-entry quote"><span className="feature-icon"><Receipt /></span><h3>报价</h3><p>CP3 实现参考成本参数和确定性公式。</p><button disabled>尚未实现</button></article></div></section>
      </section></main>
      {showSettings && <div className="settings-backdrop"><section className="settings-modal"><div className="settings-heading"><div><span>统一设置</span><h2>API 接入</h2><p>三个功能最终共用；本阶段只运行审核。</p></div></div><form onSubmit={(event) => void saveSettings(event)}><label><span>AI 方案</span><select value={provider} onChange={(event) => changeProvider(event.target.value)}><option value="hybrid">Gemini + DeepSeek（推荐）</option><option value="kimi-hybrid">K3 + DeepSeek（国产备选）</option></select></label><div className="settings-grid"><label><span>视觉模型</span><input value={model} onChange={(event) => setModel(event.target.value)} /></label><label><span>视觉模型 API Key</span><input type="password" value={primaryKey} onChange={(event) => setPrimaryKey(event.target.value)} /></label></div><label><span>DeepSeek API Key</span><input type="password" value={secondaryKey} onChange={(event) => setSecondaryKey(event.target.value)} /></label><button className="save-settings" disabled={busy === "settings"}>{busy === "settings" ? "正在保存…" : "保存 API 设置"}</button><button type="button" className="quiet-button" onClick={() => setShowSettings(false)}>取消</button></form></section></div>}
    </div>
  );
}

function DocumentPreview({ src, alt }: { src: string; alt: string }) {
  // The image is a local runtime URL, not a build-time asset.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} />;
}
