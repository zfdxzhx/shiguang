"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Blueprint,
  Check,
  ClockCounterClockwise,
  FilePdf,
  GearSix,
  MagnifyingGlass,
  Path,
  Receipt,
  ShieldCheck,
  Sparkle,
  UploadSimple,
  Warning,
  X,
} from "@phosphor-icons/react";

type Feature = "review" | "process" | "quote";
type View = "home" | "history" | Feature;
type ProviderId = "hybrid" | "kimi-hybrid" | "kimi" | "gemini" | "openai";

type DocumentRecord = {
  id: string;
  filename: string;
  sha256: string;
  page_count: number;
  page_urls: string[];
};

type ProviderOption = {
  id: ProviderId;
  label: string;
  default_model: string;
  secondary_default_model?: string;
  requires_secondary?: boolean;
};

type AppConfig = {
  milestone?: number;
  configured: boolean;
  provider: ProviderId;
  model?: string | null;
  visual_model?: string | null;
  secondary_model?: string | null;
  credential_available?: boolean;
  secondary_credential_available?: boolean;
  provider_options?: ProviderOption[];
  persistent_credentials_supported?: boolean;
  credential_storage?: string;
  verification?: {
    status?: string;
    primary_status?: string;
    secondary_status?: string;
    checked_at?: string | null;
  };
  max_file_size_mb?: number;
};

type Source = {
  id: string;
  title: string;
  publisher: string;
  url: string;
  accessed_at: string;
  role: string;
};

type FeatureOutput = {
  kind: "review_report" | "process_plan" | "quote_estimate";
  summary: string;
  report_url?: string | null;
  result_status?: "ready" | "assumptions_only" | "insufficient_input";
  warnings?: string[];
  boundary: string;
  facts: Array<{ name: string; label?: string; value: unknown; confidence?: number }>;
  findings?: Array<{
    id: string;
    code: string;
    field?: string;
    category?: string;
    conclusion: string;
    impact?: string;
    recommendation?: string;
    confidence?: number;
  }>;
  review?: {
    recommended_disposition: "blocked" | "conditional" | "ready_for_human_release";
    conclusion: string;
    blocker_count: number;
    review_count: number;
    issues: Array<{
      id: string;
      code: string;
      field?: string;
      category?: string;
      severity: "blocked" | "needs_review";
      problem: string;
      impact: string;
      recommendation: string;
    }>;
  };
  process_plan?: {
    manufacturing_family: string;
    quantity: number;
    route_summary: string;
    steps: Array<{
      sequence: number;
      operation: string;
      purpose: string;
      equipment_capability?: string;
      resource?: string;
      key_characteristics?: string[];
      control_points?: string[];
    }>;
    warnings: string[];
  };
  prequote?: {
    quantity: number;
    unit_prequote: number;
    total_cost: number;
    target_revenue: number;
    formula_version: string;
    cost_items: Array<{ code: string; label: string; amount: number; basis: string }>;
    inputs: Record<string, number | string>;
    warnings: string[];
  };
  sources?: Source[];
  assumptions?: string[];
};

type FeatureRun = {
  id: string;
  feature: Feature;
  status: string;
  business_status?: string | null;
  human_status?: string | null;
  provider: string;
  model?: string;
  provider_execution?: {
    visual_provider?: string | null;
    visual_model?: string | null;
    visual_status?: string | null;
    secondary_provider?: string | null;
    secondary_model?: string | null;
    secondary_status?: string | null;
    routing?: string | null;
    total_token_count?: number | null;
    localization_target_count?: number | null;
    localization_accepted_count?: number | null;
  };
  document: DocumentRecord;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  output?: FeatureOutput | null;
};

type Notice = { tone: "info" | "success" | "warning"; title: string; body: string };
type LoadState = "loading" | "ready" | "error";

const FEATURE_COPY: Record<Feature, { title: string; eyebrow: string; description: string; action: string; result: string }> = {
  review: {
    title: "AI 审核",
    eyebrow: "发现问题",
    description: "识别图纸要求、问题、影响和建议，完成后直接生成审核报告。",
    action: "开始 AI 审核",
    result: "AI 审核报告",
  },
  process: {
    title: "工艺路线",
    eyebrow: "规划制造",
    description: "AI 读取图纸并结合公开参考资料，自动生成可讨论的加工路线卡。",
    action: "生成工艺路线",
    result: "AI 工艺路线卡",
  },
  quote: {
    title: "报价",
    eyebrow: "估算成本",
    description: "AI 和公开资料补齐参考条件，再由确定性公式生成课堂参考报价。",
    action: "生成参考报价",
    result: "AI 参考报价单",
  },
};

const RUNNING = new Set(["queued", "validating", "rendering", "calling_ai", "analyzing", "validating_output", "applying_rules", "processing", "running"]);
const FEATURE_MILESTONE: Record<Feature, number> = { review: 2, process: 3, quote: 3 };
const FIELD_LABELS: Record<string, string> = {
  part_name: "零件名称",
  revision: "图纸版本",
  material: "材料",
  dimensions: "主要尺寸",
  tolerances: "公差要求",
  title_block: "标题栏信息",
  surface_treatment: "表面处理",
  inspection: "检验要求",
};
const CATEGORY_LABELS: Record<string, string> = {
  source_integrity: "来源与版本",
  requirement_consistency: "要求一致性",
  manufacturability: "可制造性",
  inspectability: "可检验性",
  assembly: "装配",
  compliance: "标准符合性",
  other: "其他",
};

function apiUrl(path: string) {
  if (typeof window === "undefined") return path;
  return new URL(path, window.location.origin).toString();
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.detail || `请求失败（${response.status}）`);
  return body as T;
}

function money(value?: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(value || 0);
}

function dateTime(value?: string) {
  if (!value) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === "") return "未识别";
  return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value);
}

function fieldLabel(name?: string) {
  return name ? FIELD_LABELS[name] || "其他字段" : "综合问题";
}

function categoryLabel(name?: string) {
  return name ? CATEGORY_LABELS[name] || "工程问题" : "工程问题";
}

function compactSummary(value: string, limit = 96) {
  const text = value.trim();
  return text.length > limit ? `${text.slice(0, limit).trim()}…` : text;
}

function visualProviderLabel(provider?: string | null) {
  if (provider === "gemini") return "Gemini";
  if (provider === "kimi") return "K3";
  if (provider === "openai") return "OpenAI";
  return provider || "视觉模型";
}

function providerExecutionText(run: FeatureRun) {
  const execution = run.provider_execution;
  if (!execution?.visual_status) return providerPlanName(run.provider as ProviderId);
  const visual = `${visualProviderLabel(execution.visual_provider)} 已完成`;
  if (execution.secondary_status === "completed") return `${visual} · DeepSeek 已复核`;
  if (execution.secondary_status === "skipped") return `${visual} · DeepSeek 未触发`;
  if (execution.secondary_status === "failed_safely") return `${visual} · DeepSeek 未完成，已安全降级`;
  return visual;
}

function primaryProviderFamily(provider?: ProviderId) {
  if (provider === "hybrid" || provider === "gemini") return "gemini";
  if (provider === "kimi-hybrid" || provider === "kimi") return "kimi";
  return provider || "";
}

function providerPlanName(provider?: ProviderId) {
  if (provider === "hybrid") return "Gemini + DeepSeek";
  if (provider === "kimi-hybrid") return "K3 + DeepSeek";
  if (provider === "gemini") return "Gemini";
  if (provider === "kimi") return "K3";
  if (provider === "openai") return "OpenAI";
  return "尚未选择";
}

function visualProviderName(provider?: ProviderId) {
  const family = primaryProviderFamily(provider);
  if (family === "gemini") return "Gemini";
  if (family === "kimi") return "K3";
  if (family === "openai") return "OpenAI";
  return "当前视觉模型";
}

function FeatureIcon({ feature, size = 28 }: { feature: Feature; size?: number }) {
  if (feature === "review") return <MagnifyingGlass size={size} />;
  if (feature === "process") return <Path size={size} />;
  return <Receipt size={size} />;
}

export function DrawingReviewApp() {
  const [view, setView] = useState<View>("home");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configState, setConfigState] = useState<LoadState>("loading");
  const [configError, setConfigError] = useState("");
  const [history, setHistory] = useState<FeatureRun[]>([]);
  const [historyState, setHistoryState] = useState<LoadState>("loading");
  const [historyError, setHistoryError] = useState("");
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [run, setRun] = useState<FeatureRun | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const closeSettings = useCallback(() => setShowSettings(false), []);

  const currentFeature = view === "review" || view === "process" || view === "quote" ? view : null;
  const running = Boolean(run && RUNNING.has(run.status));
  const activeRunId = run?.id;
  const activeRunStatus = run?.status;

  const loadConfig = useCallback(async (silent = false) => {
    if (!silent) setConfigState("loading");
    setConfigError("");
    try {
      setConfig(await api<AppConfig>("/api/v1/ai/status"));
      setConfigState("ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "请检查本地服务。";
      setConfigError(message);
      setConfigState("error");
      if (!silent) setNotice({ tone: "warning", title: "无法读取 API 设置", body: message });
    }
  }, []);

  const loadHistory = useCallback(async (silent = false) => {
    if (!silent) setHistoryState("loading");
    setHistoryError("");
    try {
      const payload = await api<{ runs: FeatureRun[] }>("/api/v1/features/history");
      setHistory(payload.runs.filter((item) => item.provider !== "mock"));
      setHistoryState("ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "请检查本地服务后重试。";
      setHistoryError(message);
      setHistoryState("error");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadConfig(); void loadHistory(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadConfig, loadHistory]);

  useEffect(() => {
    if (notice?.tone !== "success") return;
    const timer = window.setTimeout(() => setNotice(null), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!activeRunId || !activeRunStatus || !RUNNING.has(activeRunStatus)) return;
    let cancelled = false;
    let timer: number | undefined;
    let failures = 0;
    const runId = activeRunId;

    const poll = async () => {
      try {
        const next = await api<FeatureRun>(`/api/v1/features/runs/${encodeURIComponent(runId)}`);
        if (cancelled) return;
        const recovered = failures > 0;
        failures = 0;
        setRun(next);
        if (next.status === "completed") {
          const downloadable = Boolean(next.output?.report_url);
          const blockedReview = next.feature === "review" && next.output?.review?.recommended_disposition === "blocked";
          setNotice({
            tone: blockedReview ? "warning" : downloadable ? "success" : "info",
            title: blockedReview ? "报告已生成，发现必须解决的问题" : downloadable ? `${FEATURE_COPY[next.feature].result}已生成` : "AI 识别已完成",
            body: blockedReview
              ? `图纸不能直接放行：${next.output?.review?.blocker_count || 0} 项必须解决，${next.output?.review?.review_count || 0} 项待确认。`
              : downloadable ? "结果可直接查看并下载 PDF。" : (next.output?.summary || "当前信息不足，未生成不可靠的报告。"),
          });
          void loadHistory(true);
          void loadConfig(true);
          return;
        }
        if (next.status === "failed") {
          setNotice({ tone: "warning", title: "本次运行已停止", body: next.error || "请检查 API、网络、模型名称或额度后重试。" });
          void loadHistory(true);
          void loadConfig(true);
          return;
        }
        if (recovered) setNotice({ tone: "info", title: "连接已恢复", body: "任务仍在运行，状态已继续更新。" });
        timer = window.setTimeout(() => { void poll(); }, 1200);
      } catch (error) {
        if (cancelled) return;
        failures += 1;
        const delay = Math.min(1200 * 2 ** Math.min(failures, 3), 8000);
        const reason = error instanceof Error ? error.message : "暂时无法读取任务状态。";
        setNotice({ tone: "warning", title: "状态连接中断，正在重试", body: `${reason} 将在 ${Math.ceil(delay / 1000)} 秒后重试。` });
        timer = window.setTimeout(() => { void poll(); }, delay);
      }
    };

    timer = window.setTimeout(() => { void poll(); }, 1200);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeRunId, activeRunStatus, loadConfig, loadHistory]);

  const openFeature = (feature: Feature) => {
    if (busy === "upload" || busy === "run") {
      setNotice({ tone: "info", title: "当前操作尚未完成", body: "请等待图纸上传或任务启动完成。" });
      return;
    }
    if (running && run) {
      if (run.feature === feature) {
        setView(feature);
        window.scrollTo({ top: 0 });
      } else {
        setNotice({ tone: "info", title: "当前任务仍在运行", body: `请先等待${FEATURE_COPY[run.feature].result}生成，再新建其他任务。` });
      }
      return;
    }
    setView(feature);
    setDocument(null);
    setRun(null);
    setNotice(null);
    window.scrollTo({ top: 0 });
  };

  const chooseFile = async (next?: File) => {
    if (!next || busy === "upload" || busy === "run" || running) return;
    if (!(next.type === "application/pdf" || next.name.toLowerCase().endsWith(".pdf"))) {
      setNotice({ tone: "warning", title: "请选择 PDF 图纸", body: "当前文件未上传。" });
      return;
    }
    setDocument(null);
    setRun(null);
    setBusy("upload");
    try {
      const body = new FormData();
      body.append("file", next);
      const uploaded = await api<DocumentRecord>("/api/v1/documents", { method: "POST", body });
      setDocument(uploaded);
      setNotice({ tone: "success", title: "图纸已就绪", body: `已完成 ${uploaded.page_count} 页校验，可以直接运行。` });
    } catch (error) {
      setNotice({ tone: "warning", title: "图纸没有上传", body: error instanceof Error ? error.message : "请换一份 PDF 重试。" });
    } finally {
      setBusy("");
    }
  };

  const startFeature = async () => {
    if (!currentFeature || !document || !config?.configured || busy || running) return;
    setBusy("run");
    setNotice({ tone: "info", title: `${FEATURE_COPY[currentFeature].title}正在运行`, body: "AI 正在读取分页图并生成本功能结果。" });
    try {
      const created = await api<FeatureRun>(`/api/v1/features/${currentFeature}/runs`, {
        method: "POST",
        body: JSON.stringify({ document_id: document.id, external_processing_consent: true }),
      });
      setRun(created);
    } catch (error) {
      setNotice({ tone: "warning", title: "任务没有启动", body: error instanceof Error ? error.message : "请检查 API 设置。" });
    } finally {
      setBusy("");
    }
  };

  const openHistoryRun = async (item: FeatureRun) => {
    if (busy === "upload" || busy === "run") {
      setNotice({ tone: "info", title: "当前操作尚未完成", body: "请等待图纸上传或任务启动完成。" });
      return;
    }
    if (running && item.id !== run?.id) {
      setNotice({ tone: "info", title: "当前任务仍在运行", body: "请先返回当前任务查看进度。" });
      return;
    }
    setBusy(`open-${item.id}`);
    try {
      const detail = await api<FeatureRun>(`/api/v1/features/runs/${encodeURIComponent(item.id)}`);
      setView(detail.feature);
      setDocument(detail.document);
      setRun(detail);
      window.scrollTo({ top: 0 });
    } catch (error) {
      setNotice({ tone: "warning", title: "无法打开记录", body: error instanceof Error ? error.message : "请稍后重试。" });
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="simple-product-shell">
      <header className="simple-header">
        <button className="simple-brand" aria-label="返回工作台" onClick={() => { setView("home"); setNotice(null); }}><Blueprint size={25} /><span><strong>图纸 AI 工程助手</strong><small>审核 · 工艺 · 报价</small></span></button>
        <nav aria-label="产品导航">
          <button aria-label="工作台" aria-current={view === "home" ? "page" : undefined} className={view === "home" ? "active" : ""} onClick={() => { setView("home"); setNotice(null); }}><Blueprint size={18} /><span>工作台</span></button>
          <button aria-label="历史记录" aria-current={view === "history" ? "page" : undefined} className={view === "history" ? "active" : ""} onClick={() => { setView("history"); setNotice(null); void loadHistory(); }}><ClockCounterClockwise size={18} /><span>历史记录</span></button>
          <button aria-label="API 设置" aria-haspopup="dialog" onClick={() => setShowSettings(true)}><GearSix size={18} /><span>API 设置</span></button>
        </nav>
      </header>

      {notice && <div className={`simple-notice ${notice.tone}`} role={notice.tone === "warning" ? "alert" : "status"}><span>{notice.tone === "success" ? <Check /> : notice.tone === "warning" ? <Warning /> : <Sparkle />}</span><div><strong>{notice.title}</strong><p>{notice.body}</p></div><button aria-label="关闭提示" onClick={() => setNotice(null)}><X /></button></div>}

      <main>
        {view === "home" && <HomeView config={config} configState={configState} configError={configError} history={history} historyState={historyState} historyError={historyError} activeRun={running ? run : null} onFeature={openFeature} onSettings={() => setShowSettings(true)} onRetryConfig={() => void loadConfig()} onRetryHistory={() => void loadHistory()} onHistory={() => { setView("history"); setNotice(null); void loadHistory(); }} onOpen={(item) => void openHistoryRun(item)} />}
        {currentFeature && (
          <FeatureWorkspace
            feature={currentFeature}
            available={(config?.milestone ?? 3) >= FEATURE_MILESTONE[currentFeature]}
            config={config}
            document={document}
            run={run}
            busy={busy}
            onBack={() => { setView("home"); setNotice(null); }}
            onFile={(next) => void chooseFile(next)}
            onRun={() => void startFeature()}
            onSettings={() => setShowSettings(true)}
            onReset={() => openFeature(currentFeature)}
          />
        )}
        {view === "history" && <HistoryView history={history} state={historyState} error={historyError} busy={busy} onOpen={(item) => void openHistoryRun(item)} onHome={() => { setView("home"); setNotice(null); }} onRetry={() => void loadHistory()} />}
      </main>

      {showSettings && <SettingsModal config={config} onClose={closeSettings} onSaved={(next) => { setConfig(next); setConfigState("ready"); setConfigError(""); setShowSettings(false); setNotice({ tone: "success", title: "API 设置已保存", body: "现在可以返回任一功能运行真实图纸。" }); }} />}
    </div>
  );
}

function HomeView({ config, configState, configError, history, historyState, historyError, activeRun, onFeature, onSettings, onRetryConfig, onRetryHistory, onHistory, onOpen }: {
  config: AppConfig | null;
  configState: LoadState;
  configError: string;
  history: FeatureRun[];
  historyState: LoadState;
  historyError: string;
  activeRun: FeatureRun | null;
  onFeature: (feature: Feature) => void;
  onSettings: () => void;
  onRetryConfig: () => void;
  onRetryHistory: () => void;
  onHistory: () => void;
  onOpen: (item: FeatureRun) => void;
}) {
  const completed = history.filter((item) => item.status === "completed").length;
  const verified = config?.verification?.status === "verified";
  const primaryVerified = verified || config?.verification?.status === "primary_verified" || config?.verification?.primary_status === "verified";
  const secondaryFailed = config?.verification?.secondary_status === "failed";
  const providerName = providerPlanName(config?.provider);
  const availability = configState === "loading" ? "loading" : configState === "error" ? "error" : !config?.configured ? "needs-setup" : primaryVerified ? "verified" : "unverified";
  const availabilityLabel = availability === "loading" ? "检查中" : availability === "error" ? "状态未知" : availability === "needs-setup" ? "需设置 API" : availability === "verified" ? "可运行" : "已配置";
  return (
    <section className="home-view">
      <div className="home-toolbar">
        <div className="home-title"><span className="home-kicker">图纸任务工作台</span><h1>开始新任务</h1><p>选择要生成的结果，进入后上传一份 PDF。三个功能，彼此独立。</p></div>
        <div className={`service-status-card ${configState === "loading" ? "loading" : configState === "error" ? "error" : !config?.configured ? "needs-setup" : primaryVerified ? "verified" : "configured"}`}>
          <span className="service-status-icon">{configState === "error" ? <Warning size={22} /> : config?.configured ? <ShieldCheck size={22} /> : <GearSix size={22} />}</span>
          <div><small>AI 服务状态</small><strong>{configState === "loading" ? "正在读取配置" : configState === "error" ? "配置读取失败" : !config?.configured ? "需要配置 API" : verified ? "全部模型已验证" : primaryVerified ? "主模型已验证" : "配置已保存"}</strong><em>{configState === "loading" ? "请稍候" : configState === "error" ? configError : config?.configured ? verified ? `${providerName} · 可运行` : primaryVerified ? `${visualProviderName(config.provider)} 可运行 · ${secondaryFailed ? "DeepSeek 复核需检查" : "DeepSeek 首次触发时验证"}` : `${providerName} · 首次运行时验证` : "配置主方案或国产备选"}</em></div>
          <button onClick={configState === "error" ? onRetryConfig : onSettings}>{configState === "error" ? <ClockCounterClockwise size={17} /> : <GearSix size={17} />}{configState === "error" ? "重试" : "管理"}</button>
        </div>
      </div>

      <section className="task-launch-panel" aria-labelledby="task-launch-title">
        <div className="home-section-heading"><div><h2 id="task-launch-title">选择工作类型</h2><p>每次任务都从一份新的 PDF 图纸开始</p></div><span>3 个独立功能</span></div>
        <div className="feature-card-grid">
          {(["review", "process", "quote"] as Feature[]).map((feature) => {
            const copy = FEATURE_COPY[feature];
            const isActive = activeRun?.feature === feature;
            const locked = Boolean(activeRun && !isActive);
            const statusLabel = activeRun ? isActive ? "运行中" : "请等待" : availabilityLabel;
            return <article className={`feature-entry ${feature} ${locked ? "locked" : ""}`} key={feature}><div className="feature-entry-top"><span className="feature-icon"><FeatureIcon feature={feature} /></span><span className={`feature-availability ${activeRun ? "loading" : availability}`}>{isActive ? <Sparkle size={14} /> : availability === "verified" ? <Check size={14} /> : availability === "needs-setup" ? <GearSix size={14} /> : availability === "error" ? <Warning size={14} /> : <ShieldCheck size={14} />}{statusLabel}</span></div><span className="feature-eyebrow">{copy.eyebrow}</span><h3>{copy.title}</h3><p>{copy.description}</p><div className="feature-deliverable"><FilePdf size={20} /><span><small>输出文件</small><strong>{copy.result}</strong></span></div><button disabled={locked} onClick={() => onFeature(feature)}>{isActive ? "查看运行进度" : locked ? "当前有任务运行" : copy.action}{!locked && <ArrowRight />}</button></article>;
          })}
        </div>
      </section>

      <section className="recent-panel" aria-labelledby="recent-title">
        <div className="home-section-heading"><div><h2 id="recent-title">最近任务</h2><p>审核、工艺和报价结果统一保存在本地</p></div><button onClick={onHistory}>查看全部<ArrowRight size={16} /></button></div>
        {historyState === "loading" && !history.length ? <InlineLoadState title="正在读取历史记录" /> : historyState === "error" ? <InlineLoadState tone="error" title="历史记录读取失败" body={historyError} onRetry={onRetryHistory} /> : history.length ? <div className="home-recent-list">{history.slice(0, 3).map((item) => <button key={item.id} onClick={() => onOpen(item)}><span className={`history-feature ${item.feature}`}><FeatureIcon feature={item.feature} size={19} /></span><span><strong>{item.document?.filename || "未命名图纸"}</strong><small>{FEATURE_COPY[item.feature].title} · {dateTime(item.updated_at)}</small></span><em className={item.status}>{item.status === "completed" ? "已生成" : item.status === "failed" ? "运行失败" : "运行中"}</em><ArrowRight size={17} /></button>)}</div> : <div className="home-empty-recent"><ClockCounterClockwise size={26} /><div><strong>还没有任务记录</strong><p>从上方选择一个工作类型，完成后的结果会显示在这里。</p></div><span>{completed} 个结果</span></div>}
      </section>
    </section>
  );
}

function InlineLoadState({ title, body, tone = "loading", onRetry }: { title: string; body?: string; tone?: "loading" | "error"; onRetry?: () => void }) {
  return <div className={`inline-load-state ${tone}`} role={tone === "error" ? "alert" : "status"}>{tone === "loading" ? <span className="simple-spinner" /> : <Warning size={24} />}<div><strong>{title}</strong>{body && <p>{body}</p>}</div>{onRetry && <button onClick={onRetry}><ClockCounterClockwise size={16} />重新读取</button>}</div>;
}

function FeatureWorkspace({ feature, available, config, document, run, busy, onBack, onFile, onRun, onSettings, onReset }: {
  feature: Feature;
  available: boolean;
  config: AppConfig | null;
  document: DocumentRecord | null;
  run: FeatureRun | null;
  busy: string;
  onBack: () => void;
  onFile: (file?: File) => void;
  onRun: () => void;
  onSettings: () => void;
  onReset: () => void;
}) {
  const copy = FEATURE_COPY[feature];
  const running = Boolean(run && RUNNING.has(run.status));
  const completed = run?.status === "completed" && Boolean(run.output);
  const locked = running || busy === "upload" || busy === "run";
  const canReset = Boolean(document || run) && !locked && run?.status !== "failed";
  const resultTitleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!completed) return;
    const frame = window.requestAnimationFrame(() => resultTitleRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [completed, run?.id]);

  return (
    <section className="feature-workspace">
      <div className="feature-page-heading"><button onClick={onBack}><ArrowLeft />返回首页</button><div><span className={`feature-icon ${feature}`}><FeatureIcon feature={feature} /></span><div><span>{copy.eyebrow}</span><h1>{copy.title}</h1><p>{copy.description}</p></div></div>{canReset ? <button className="quiet-button" onClick={onReset}>新建任务</button> : <span className="heading-action-spacer" aria-hidden="true" />}</div>

      {!completed && <div className="feature-run-layout">
        <article className="run-card" aria-busy={locked}>
          <label className={`simple-dropzone ${document ? "has-file" : ""} ${locked ? "is-locked" : ""}`}>
            <input aria-label="选择 PDF 图纸" type="file" accept="application/pdf,.pdf" disabled={locked} onChange={(event) => onFile(event.target.files?.[0])} />
            {busy === "upload" ? <><span className="simple-spinner" /><strong>正在校验图纸…</strong></> : document ? <><FilePdf size={35} /><strong>{document.filename}</strong><small>{document.page_count} 页 · 已完成本地校验</small><em>{running ? "任务运行中，暂不可更换" : "点击更换"}</em></> : <><UploadSimple size={36} /><strong>选择 PDF 图纸</strong><small>点击选择 PDF 文件{config?.max_file_size_mb ? ` · 最大 ${config.max_file_size_mb} MB` : ""}</small></>}
          </label>

          {!config?.configured && <button className="configure-inline" onClick={onSettings}><GearSix />先设置 API 接入</button>}
          <button className="primary-run-button" disabled={!available || !document || !config?.configured || locked} onClick={onRun}>{running || busy === "run" ? <><span className="simple-spinner light" />正在生成{copy.result}…</> : !available ? <>当前功能暂不可用</> : <><Sparkle />{copy.action}</>}</button>
          <small className="run-boundary">{!available ? "当前服务尚未开放此功能。" : feature === "review" ? "上传后直接生成图纸 AI 审核报告。" : feature === "process" ? "参考路线不是 NC 程序或投产工艺。" : "AI 负责补齐参考条件，最终金额由程序公式计算。"}</small>
        </article>

        <article className={`preview-card ${document ? "has-document" : "empty"}`}>
          <div><span>图纸预览</span><small>{document ? `第 1 / ${document.page_count} 页` : "等待选择文件"}</small></div>
          {document?.page_urls?.[0] ? <PreviewImage src={apiUrl(document.page_urls[0])} alt={`${document.filename} 第 1 页预览`} /> : <div className="empty-preview"><Blueprint size={58} /><strong>选择图纸后在这里预览</strong><small>支持 PDF</small></div>}
        </article>
      </div>}

      {run?.status === "failed" && <div className="failed-result" role="alert"><Warning size={32} /><div><strong>本次运行安全停止</strong><p>{run.error || "请检查 API、网络、模型名称或额度后重试。"}</p></div><button onClick={onReset}>重新开始</button></div>}
      {completed && run && <FeatureResult run={run} onReset={onReset} titleRef={resultTitleRef} />}
    </section>
  );
}

function PreviewImage({ src, alt }: { src: string; alt: string }) {
  // The production app serves these generated page previews from its own local API.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} width={1400} height={1000} />;
}

function FeatureResult({ run, onReset, titleRef }: { run: FeatureRun; onReset: () => void; titleRef: RefObject<HTMLHeadingElement | null> }) {
  const output = run.output as FeatureOutput;
  const copy = FEATURE_COPY[run.feature];
  const nestedWarnings = run.feature === "process" ? output.process_plan?.warnings || [] : run.feature === "quote" ? output.prequote?.warnings || [] : [];
  const warnings = Array.from(new Set([...(output.warnings || []), ...nestedWarnings]));
  const summary = output.summary || `${copy.result}已生成`;
  const summaryPreview = compactSummary(summary);
  const metadata = [run.document?.filename, run.updated_at ? dateTime(run.updated_at) : "", providerExecutionText(run)].filter(Boolean);
  return (
    <section className="feature-result">
      <article className="result-summary-card"><span className={`feature-icon ${run.feature}`}><FeatureIcon feature={run.feature} /></span><div><span>{copy.result}</span><h2 ref={titleRef} tabIndex={-1}>{summaryPreview}</h2>{summaryPreview !== summary && <details className="result-summary-details"><summary>查看完整摘要</summary><p>{summary}</p></details>}{metadata.length > 0 && <p>{metadata.join(" · ")}</p>}</div></article>
      {run.feature === "review" && output.review ? <ReviewDecisionNotice review={output.review} /> : <ResultTrustNotice status={output.result_status} />}
      <div className="result-actions" aria-label={`${copy.result}操作`}>{output.report_url && <a className="download-report" href={apiUrl(output.report_url)} target="_blank" rel="noreferrer"><FilePdf />下载 PDF 报告</a>}<button className="quiet-button" onClick={onReset}>再运行一次</button></div>
      {warnings.length > 0 && <section className="result-warning-list" aria-labelledby="result-warning-title"><div><Warning size={22} /><h2 id="result-warning-title">关键提醒</h2></div><ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}

      {run.feature === "review" && <ReviewResult output={output} />}
      {run.feature === "process" && <ProcessResult output={output} />}
      {run.feature === "quote" && <QuoteResult output={output} />}
      <SourceDetails output={output} />
      <RunTechnicalDetails run={run} output={output} />
      <div className="result-boundary"><ShieldCheck /><p>{output.boundary}</p></div>
    </section>
  );
}

function ReviewDecisionNotice({ review }: { review: NonNullable<FeatureOutput["review"]> }) {
  const blocked = review.recommended_disposition === "blocked";
  const conditional = review.recommended_disposition === "conditional";
  const title = blocked ? "报告已生成，但图纸不能直接放行" : conditional ? "报告已生成，确认问题后再继续" : "报告已生成，仍需工程确认";
  return <div className={`review-decision-notice ${blocked ? "blocked" : conditional ? "conditional" : "ready"}`} role={blocked ? "alert" : "status"}><span>{blocked ? <Warning size={24} /> : <ShieldCheck size={24} />}</span><div><strong>{title}</strong><p>{review.conclusion}</p></div><dl><div><dt>必须解决</dt><dd>{review.blocker_count}</dd></div><div><dt>待确认</dt><dd>{review.review_count}</dd></div></dl></div>;
}

function ResultTrustNotice({ status }: { status?: FeatureOutput["result_status"] }) {
  if (!status) return null;
  const copy = status === "ready"
    ? { tone: "ready", title: "信息足以生成本次结果", body: "请按报告中的边界与来源使用。" }
    : status === "assumptions_only"
      ? { tone: "assumptions", title: "本次结果包含参考假设", body: "部分制造或成本条件采用课堂参考值，请重点查看关键提醒和估算依据。" }
      : { tone: "insufficient", title: "输入信息不足", body: "当前仅显示可核实内容；请补充图纸后重新运行。" };
  return <div className={`result-trust-notice ${copy.tone}`} role={status === "ready" ? "status" : "alert"}>{status === "ready" ? <ShieldCheck size={22} /> : <Warning size={22} />}<div><strong>{copy.title}</strong><p>{copy.body}</p></div></div>;
}

function ReviewResult({ output }: { output: FeatureOutput }) {
  const issues = output.review?.issues || (output.findings || []).map((finding) => ({
    id: finding.id,
    code: finding.code,
    field: finding.field,
    category: finding.category,
    severity: "needs_review" as const,
    problem: finding.conclusion,
    impact: finding.impact || "需要工程人员结合原图确认影响。",
    recommendation: finding.recommendation || "请对照原图核对并记录处理结果。",
  }));
  return <div className="result-section-grid"><article className="result-card facts-result"><div className="result-card-heading"><div><span>图纸事实</span><h2>AI 识别结果</h2></div><em>{output.facts.length} 项</em></div><div className="fact-grid">{output.facts.map((fact) => <div key={fact.name}><span>{fact.label || fieldLabel(fact.name)}</span><strong>{valueText(fact.value)}</strong><small>{confidenceText(fact.confidence)}</small></div>)}</div></article><article className="result-card findings-result"><div className="result-card-heading"><div><span>综合检查</span><h2>问题、影响与建议</h2></div><em>{issues.length} 项</em></div><div className="simple-finding-list">{issues.map((issue, index) => <article key={issue.id} className={issue.severity}><span>{String(index + 1).padStart(2, "0")}</span><div><div className="finding-labels"><em>{issue.severity === "blocked" ? "必须解决" : "待确认"}</em><small>{categoryLabel(issue.category)} · {fieldLabel(issue.field)}</small></div><strong>{issue.problem}</strong>{issue.impact && <p><b>可能影响</b>{issue.impact}</p>}{issue.recommendation && <p><b>建议措施</b>{issue.recommendation}</p>}</div></article>)}{!issues.length && <div className="empty-result">本次综合检查没有列出问题；使用前仍请核对图纸和报告边界。</div>}</div></article></div>;
}

function RunTechnicalDetails({ run, output }: { run: FeatureRun; output: FeatureOutput }) {
  const execution = run.provider_execution;
  const rawCodes = Array.from(new Set((output.findings || []).map((item) => item.code))).filter(Boolean);
  if (!execution && !rawCodes.length && !run.business_status) return null;
  const localization = execution?.localization_target_count === 0
    ? "无需二次定位"
    : typeof execution?.localization_target_count === "number"
      ? `${execution.localization_accepted_count || 0}/${execution.localization_target_count}`
      : "本次无定位任务";
  return <details className="run-technical-details"><summary>查看模型运行详情</summary><div className="technical-detail-grid"><div><span>模型执行</span><strong>{providerExecutionText(run)}</strong><small>{execution?.visual_model || run.model || "模型名称未记录"}</small></div><div><span>坐标定位</span><strong>{localization}</strong><small>只接受通过坐标与证据校验的位置</small></div><div><span>Token</span><strong>{execution?.total_token_count ? execution.total_token_count.toLocaleString("zh-CN") : "未记录"}</strong><small>主分析、定位与按需复核合计</small></div><div><span>业务状态</span><strong>{run.business_status === "blocked" ? "不能直接放行" : run.business_status === "needs_review" ? "需要确认" : run.business_status === "pass" ? "规则未发现阻断" : "未记录"}</strong><small>{rawCodes.length ? `模型问题代码：${rawCodes.join("、")}` : "本次没有模型问题代码"}</small></div></div></details>;
}

function confidenceText(value?: number) {
  return typeof value === "number" ? `置信度 ${Math.round(value * 100)}%` : "置信度未提供";
}

function ProcessResult({ output }: { output: FeatureOutput }) {
  const plan = output.process_plan;
  if (!plan) return null;
  return <article className="result-card process-result"><div className="result-card-heading"><div><span>AI 工艺路线</span><h2>{plan.route_summary}</h2></div><em>{plan.steps.length} 道工序</em></div><div className="route-strip" tabIndex={0} aria-label="工艺路线，可横向滚动">{plan.steps.map((step) => {
    const controls = Array.from(new Set([...(step.key_characteristics || []), ...(step.control_points || [])])).slice(0, 3);
    return <article key={step.sequence}><span>{String(step.sequence).padStart(2, "0")}</span><div><strong>{step.operation}</strong><p>{step.purpose}</p><small>{step.equipment_capability || step.resource || "设备能力待现场确认"}</small>{controls.map((item) => <em key={item}>{item}</em>)}</div></article>;
  })}</div></article>;
}

function QuoteResult({ output }: { output: FeatureOutput }) {
  const quote = output.prequote;
  if (!quote) return null;
  return <div className="quote-result-layout"><article className="quote-hero-card"><span>单件参考报价</span><strong>{money(quote.unit_prequote)}</strong><p>{quote.quantity} 件 · 未含税课堂估算</p><div><span>本批总成本<strong>{money(quote.total_cost)}</strong></span><span>目标收入<strong>{money(quote.target_revenue)}</strong></span></div></article><article className="result-card"><div className="result-card-heading"><div><span>确定性公式</span><h2>成本明细</h2></div><em>{quote.formula_version}</em></div><div className="simple-cost-list">{quote.cost_items.map((item) => <div key={item.code}><span><strong>{item.label}</strong><small>{item.basis}</small></span><em>{money(item.amount)}</em></div>)}</div></article></div>;
}

function SourceDetails({ output }: { output: FeatureOutput }) {
  if (!output.sources?.length && !output.assumptions?.length) return null;
  return <details className="source-details"><summary>查看 AI 与公开资料的估算依据</summary><div>{output.assumptions?.map((item) => <p key={item}>• {item}</p>)}</div>{output.sources?.length ? <div className="source-link-grid">{output.sources.map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer"><span>{source.publisher} · {source.accessed_at}</span><strong>{source.title}</strong><small>{source.role}</small></a>)}</div> : null}</details>;
}

function HistoryView({ history, state, error, busy, onOpen, onHome, onRetry }: { history: FeatureRun[]; state: LoadState; error: string; busy: string; onOpen: (item: FeatureRun) => void; onHome: () => void; onRetry: () => void }) {
  return <section className="history-view-simple"><div className="history-heading"><div><span>本地历史</span><h1>三个功能的结果都在这里</h1><p>历史来自本地后端，不保存 API Key，也不依赖浏览器缓存。</p></div><button onClick={onHome}>＋ 新建任务</button></div>{state === "error" && <InlineLoadState tone="error" title="历史记录读取失败" body={error} onRetry={onRetry} />}{state === "loading" && !history.length ? <InlineLoadState title="正在读取历史记录" /> : state === "error" && !history.length ? null : <div className="simple-history-list">{history.map((item) => <article key={item.id}><span className={`history-feature ${item.feature}`}><FeatureIcon feature={item.feature} size={20} /></span><div><strong>{item.document?.filename || item.id}</strong><small>{FEATURE_COPY[item.feature].title} · {dateTime(item.updated_at)}</small></div><span className={`run-status ${item.status}`}>{item.status === "completed" ? "已生成" : item.status === "failed" ? "运行失败" : "运行中"}</span><button aria-label={`查看${item.document?.filename || item.id}的${FEATURE_COPY[item.feature].result}`} disabled={busy === `open-${item.id}`} onClick={() => onOpen(item)}>{busy === `open-${item.id}` ? "打开中…" : "查看结果"}</button></article>)}{!history.length && <div className="empty-history-simple"><ClockCounterClockwise size={44} /><strong>还没有结果</strong><p>任选一个功能，上传 PDF 即可开始。</p></div>}</div>}</section>;
}

function SettingsModal({ config, onClose, onSaved }: { config: AppConfig | null; onClose: () => void; onSaved: (config: AppConfig) => void }) {
  const options = useMemo(() => {
    const classroomOptions: ProviderOption[] = [
      { id: "hybrid", label: "Gemini + DeepSeek（推荐）", default_model: "gemini-3.6-flash", secondary_default_model: "deepseek-v4-flash", requires_secondary: true },
      { id: "kimi-hybrid", label: "K3 + DeepSeek（国产备选）", default_model: "k3", secondary_default_model: "deepseek-v4-flash", requires_secondary: true },
    ];
    const supplied = (config?.provider_options || classroomOptions).filter((item) => item.id === "hybrid" || item.id === "kimi-hybrid");
    return supplied.length ? supplied : classroomOptions;
  }, [config?.provider_options]);
  const initial = options.some((item) => item.id === config?.provider) ? config?.provider as ProviderId : "hybrid";
  const [provider, setProvider] = useState<ProviderId>(initial);
  const selected = options.find((item) => item.id === provider) || options[0]!;
  const [model, setModel] = useState((provider === config?.provider ? config?.visual_model || config?.model : "") || selected.default_model);
  const [apiKey, setApiKey] = useState("");
  const [secondaryModel, setSecondaryModel] = useState(config?.secondary_model || selected.secondary_default_model || "deepseek-v4-flash");
  const [secondaryKey, setSecondaryKey] = useState("");
  const [storage, setStorage] = useState<"session" | "keychain">(config?.credential_storage === "keychain" ? "keychain" : "session");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const primaryReusable = Boolean(
    config?.credential_available &&
    primaryProviderFamily(config.provider) === primaryProviderFamily(provider)
  );
  const secondaryReusable = Boolean(config?.secondary_credential_available);
  const requiresSecondary = Boolean(selected.requires_secondary);
  const missingPrimaryCredential = !apiKey.trim() && !primaryReusable;
  const missingSecondaryCredential = requiresSecondary && !secondaryKey.trim() && !secondaryReusable;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
      )).filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [onClose]);

  const changeProvider = (next: ProviderId) => {
    const option = options.find((item) => item.id === next);
    setProvider(next);
    setModel(option?.default_model || "");
    setSecondaryModel(option?.secondary_default_model || "deepseek-v4-flash");
    setApiKey("");
    setError("");
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const next = await api<AppConfig>("/api/v1/ai/config", {
        method: "POST",
        body: JSON.stringify({
          provider,
          model,
          api_key: apiKey.trim() || undefined,
          reuse_primary: !apiKey.trim() && primaryReusable,
          secondary_model: requiresSecondary ? secondaryModel : undefined,
          secondary_api_key: secondaryKey.trim() || undefined,
          reuse_secondary: requiresSecondary && !secondaryKey.trim() && secondaryReusable,
          storage,
        }),
      });
      onSaved(next);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "API 设置没有保存");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div className="settings-heading">
          <div><span>统一设置</span><h2 id="settings-title">API 接入</h2><p>三个功能共用一套模型设置。密钥永不回显。</p></div>
          <button ref={closeButtonRef} type="button" aria-label="关闭设置" onClick={onClose}><X /></button>
        </div>
        <form onSubmit={(event) => void save(event)}>
          <label><span>AI 方案</span><select value={provider} onChange={(event) => changeProvider(event.target.value as ProviderId)}>{options.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          <div className="settings-grid">
            <label><span>{visualProviderName(provider)} 模型</span><input value={model} spellCheck={false} onChange={(event) => setModel(event.target.value)} /></label>
            <label>
              <span>{visualProviderName(provider)} API Key</span>
              <input type="password" value={apiKey} autoComplete="off" aria-describedby={primaryReusable ? "primary-key-help" : undefined} onChange={(event) => setApiKey(event.target.value)} placeholder={primaryReusable ? "留空沿用现有 Key" : `输入 ${visualProviderName(provider)} Key`} />
              {primaryReusable && <small id="primary-key-help" className="credential-reuse-note"><ShieldCheck size={14} />已有安全密钥，仅在更换时输入新 Key</small>}
            </label>
          </div>
          <div className="settings-grid">
            <label><span>DeepSeek 模型</span><input value={secondaryModel} spellCheck={false} onChange={(event) => setSecondaryModel(event.target.value)} /></label>
            <label>
              <span>DeepSeek API Key</span>
              <input type="password" value={secondaryKey} autoComplete="off" aria-describedby={secondaryReusable ? "secondary-key-help" : undefined} onChange={(event) => setSecondaryKey(event.target.value)} placeholder={secondaryReusable ? "留空沿用现有 Key" : "输入 DeepSeek Key"} />
              {secondaryReusable && <small id="secondary-key-help" className="credential-reuse-note"><ShieldCheck size={14} />已有安全密钥，仅在更换时输入新 Key</small>}
            </label>
          </div>
          <div className="storage-options">
            <label><input type="radio" name="storage" checked={storage === "session"} onChange={() => setStorage("session")} /><span><strong>仅本次运行（课堂推荐）</strong><small>关闭本地服务后自动清除</small></span></label>
            {config?.persistent_credentials_supported !== false && <label><input type="radio" name="storage" checked={storage === "keychain"} onChange={() => setStorage("keychain")} /><span><strong>保存到本机钥匙串</strong><small>个人电脑下次启动自动恢复</small></span></label>}
          </div>
          {error && <div className="settings-error" role="alert">{error}</div>}
          <button className="save-settings" disabled={saving || !model.trim() || (requiresSecondary && !secondaryModel.trim()) || missingPrimaryCredential || missingSecondaryCredential}>{saving ? "正在保存…" : "保存 API 设置"}</button>
        </form>
      </section>
    </div>
  );
}
