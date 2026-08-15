"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Blueprint, Check, FilePdf, GearSix, MagnifyingGlass, Path, Receipt, UploadSimple, Warning } from "@phosphor-icons/react";

type Config = {
  configured: boolean;
  provider: string;
  visual_model?: string;
  secondary_model?: string;
  credential_available?: boolean;
  secondary_credential_available?: boolean;
};

type DocumentRecord = { id: string; filename: string; page_count: number; sha256: string; page_urls: string[] };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.detail || `请求失败（${response.status}）`);
  return body as T;
}

const FEATURES = [
  { id: "review", title: "AI 审核", icon: MagnifyingGlass, checkpoint: "CP2" },
  { id: "process", title: "工艺路线", icon: Path, checkpoint: "CP3" },
  { id: "quote", title: "报价", icon: Receipt, checkpoint: "CP3" },
] as const;

export function DrawingReviewApp() {
  const [config, setConfig] = useState<Config | null>(null);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [provider, setProvider] = useState("hybrid");
  const [model, setModel] = useState("gemini-3.6-flash");
  const [primaryKey, setPrimaryKey] = useState("");
  const [secondaryModel, setSecondaryModel] = useState("deepseek-v4-flash");
  const [secondaryKey, setSecondaryKey] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => { void api<Config>("/api/v1/ai/status").then(setConfig).catch(() => setMessage("无法读取 API 设置")); }, []);

  const changeProvider = (value: string) => {
    setProvider(value);
    setModel(value === "hybrid" ? "gemini-3.6-flash" : "k3");
    setPrimaryKey("");
  };

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault(); setBusy("settings"); setMessage("");
    try {
      const next = await api<Config>("/api/v1/ai/config", { method: "POST", body: JSON.stringify({
        provider, model, api_key: primaryKey || undefined,
        reuse_primary: !primaryKey && Boolean(config?.credential_available),
        secondary_model: secondaryModel, secondary_api_key: secondaryKey || undefined,
        reuse_secondary: !secondaryKey && Boolean(config?.secondary_credential_available), storage: "session",
      }) });
      setConfig(next); setPrimaryKey(""); setSecondaryKey(""); setMessage("API 设置已保存，密钥不会回显。下一阶段再接 AI 审核。 ");
    } catch (error) { setMessage(error instanceof Error ? error.message : "API 设置失败"); }
    finally { setBusy(""); }
  };

  const upload = async (file?: File) => {
    if (!file) return; setBusy("upload"); setMessage("");
    try { const body = new FormData(); body.append("file", file); setDocument(await api<DocumentRecord>("/api/v1/documents", { method: "POST", body })); setMessage("PDF 已在本机完成校验、哈希与分页。 "); }
    catch (error) { setMessage(error instanceof Error ? error.message : "PDF 上传失败"); }
    finally { setBusy(""); }
  };

  return (
    <div className="simple-product-shell">
      <header className="simple-header"><span className="simple-brand"><Blueprint size={25} /><span><strong>图纸 AI 工程助手</strong><small>Checkpoint 1 · PDF 与 API</small></span></span><nav><button className="active">接入基础</button></nav></header>
      {message && <div className="simple-notice info" role="status"><span>{message.includes("失败") || message.includes("无法") ? <Warning /> : <Check />}</span><div><strong>当前结果</strong><p>{message}</p></div></div>}
      <main><section className="home-view">
        <div className="home-title"><span className="home-kicker">阶段 B · 接入底座</span><h1>先安全接入图纸，再配置统一 API</h1><p>本检查点没有 AI 运行接口；审核、工艺和报价会在后续代码中真正加入。</p></div>
        <div className="feature-run-layout">
          <article className="run-card"><div className="run-step"><span>1</span><div><strong>选择 PDF 图纸</strong><small>文件只在本机校验和分页</small></div></div>
            <label className={`simple-dropzone ${document ? "has-file" : ""}`}><input type="file" accept="application/pdf,.pdf" onChange={(event) => void upload(event.target.files?.[0])} />{busy === "upload" ? <strong>正在处理…</strong> : document ? <><FilePdf size={35} /><strong>{document.filename}</strong><small>{document.page_count} 页 · SHA256 {document.sha256.slice(0, 12)}…</small></> : <><UploadSimple size={36} /><strong>选择 PDF 图纸</strong><small>完成文件头、页数、哈希与分页检查</small></>}</label>
          </article>
          <article className="run-card"><div className="run-step"><span>2</span><div><strong>统一 API 设置</strong><small>两个方案，三个功能共用</small></div></div>
            <form onSubmit={(event) => void saveSettings(event)} className="settings-modal">
              <label><span>AI 方案</span><select value={provider} onChange={(event) => changeProvider(event.target.value)}><option value="hybrid">Gemini + DeepSeek（推荐）</option><option value="kimi-hybrid">K3 + DeepSeek（国产备选）</option></select></label>
              <div className="settings-grid"><label><span>视觉模型</span><input value={model} onChange={(event) => setModel(event.target.value)} /></label><label><span>视觉模型 API Key</span><input type="password" value={primaryKey} onChange={(event) => setPrimaryKey(event.target.value)} placeholder={config?.credential_available ? "留空沿用现有 Key" : "仅进入后端内存"} /></label></div>
              <div className="settings-grid"><label><span>DeepSeek 模型</span><input value={secondaryModel} onChange={(event) => setSecondaryModel(event.target.value)} /></label><label><span>DeepSeek API Key</span><input type="password" value={secondaryKey} onChange={(event) => setSecondaryKey(event.target.value)} placeholder={config?.secondary_credential_available ? "留空沿用现有 Key" : "仅进入后端内存"} /></label></div>
              <button className="save-settings" disabled={busy === "settings"}>{busy === "settings" ? "正在保存…" : <><GearSix />保存 API 设置</>}</button>
            </form>
          </article>
        </div>
        <section className="task-launch-panel"><div className="home-section-heading"><div><h2>下一阶段</h2><p>基础能力已经完成，但业务功能代码尚未加入</p></div></div><div className="feature-card-grid">{FEATURES.map(({ id, title, icon: Icon, checkpoint }) => <article className={`feature-entry ${id}`} key={id}><div className="feature-entry-top"><span className="feature-icon"><Icon /></span><span className="feature-availability loading">{checkpoint}</span></div><h3>{title}</h3><p>当前没有该运行路由，按课堂任务在 {checkpoint} 实现。</p><button disabled>尚未实现</button></article>)}</div></section>
      </section></main>
    </div>
  );
}
