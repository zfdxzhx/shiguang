// 第 4 步：完善演示——整理上传/运行中/完成/失败/空白各状态，刷新后恢复已完成结果。
// 密钥不出现在前端；DeepSeek 只在需要时由后端复核文字。
(() => {
  const home = document.getElementById("view-home");
  const upload = document.getElementById("view-upload");
  const entry = document.getElementById("review-entry");
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");
  const errorBox = document.getElementById("upload-error");
  const docCard = document.getElementById("doc-card");
  const previewWrap = document.getElementById("preview-wrap");
  const reviewRun = document.getElementById("review-run");
  const rechoose = document.getElementById("rechoose");

  const reviewStatus = document.getElementById("review-status");
  const reviewFail = document.getElementById("review-fail");
  const reviewFailMsg = document.getElementById("review-fail-msg");
  const reviewResults = document.getElementById("review-results");
  const reportSummary = document.getElementById("report-summary");
  const findingsList = document.getElementById("findings-list");
  const reportDownload = document.getElementById("report-download");
  const reviewAgain = document.getElementById("review-again");

  // 刷新后恢复会话：只存文档 id（本机 localStorage），密钥永远不在此处
  const STORAGE_KEY = "review.current_document_id";

  let provider = "gemini-deepseek";
  let currentDocId = null;

  document.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      provider = radio.value;
    });
  });

  // 启动时根据后端配置选择可用组合：默认选中已配置密钥的组合，未配置的标为不可用
  async function initProviders() {
    try {
      const resp = await fetch("/api/v1/settings/providers");
      const data = await resp.json();
      const defaultId = data.default;
      (data.providers || []).forEach((p) => {
        const radio = document.querySelector(`input[name="provider"][value="${p.id}"]`);
        if (!radio) return;
        const item = radio.closest(".settings-item");
        if (!p.enabled) {
          radio.disabled = true;
          if (item) item.classList.add("disabled");
        }
        if (p.id === defaultId) {
          radio.checked = true;
          provider = p.id;
        }
      });
      const anyEnabled = (data.providers || []).some((p) => p.enabled);
      if (!anyEnabled) {
        const hint = document.getElementById("settings-hint");
        if (hint) hint.hidden = false;
      }
    } catch (err) {
      // 拉取失败时保持页面默认选中项
    }
  }

  // ---- 会话恢复（刷新后仍能看到已完成结果）----
  function saveSession() {
    try {
      if (currentDocId) localStorage.setItem(STORAGE_KEY, currentDocId);
      else localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      // 隐私模式等情况下静默降级：仅丢失刷新恢复，不影响正常流程
    }
  }

  async function restoreSession() {
    const savedId = localStorage.getItem(STORAGE_KEY);
    if (!savedId) return;
    try {
      const resp = await fetch(`/api/v1/documents/${savedId}/review`);
      if (!resp.ok) {
        // 文档已失效（服务重启或被清理）：清除本地会话，回到首页
        if (resp.status === 404) saveSession(); // currentDocId 为空 → 清除
        return;
      }
      const data = await resp.json();
      if (!data.ok || !data.document) return;
      currentDocId = savedId;
      home.hidden = true;
      upload.hidden = false;
      renderDocument(data.document, data.review);
    } catch (err) {
      // 网络异常时保持首页，不阻塞进入
    }
  }

  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.hidden = false;
  };

  const hideError = () => {
    errorBox.hidden = true;
  };

  const hideAllReviewUi = () => {
    reviewStatus.hidden = true;
    reviewFail.hidden = true;
    reviewResults.hidden = true;
  };

  const resetToHome = () => {
    home.hidden = false;
    upload.hidden = true;
    currentDocId = null;
    saveSession();
    hideError();
  };

  const resetUpload = () => {
    currentDocId = null;
    saveSession();
    dropZone.hidden = false;
    docCard.hidden = true;
    previewWrap.hidden = true;
    reviewRun.disabled = true;
    fileInput.value = "";
    hideError();
    hideAllReviewUi();
  };

  entry.addEventListener("click", () => {
    home.hidden = true;
    upload.hidden = false;
    resetUpload();
  });

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  ["dragover", "dragenter"].forEach((evt) =>
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.add("drag-over");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
    })
  );
  dropZone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) uploadFile(file);
  });

  async function uploadFile(file) {
    hideError();
    hideAllReviewUi();
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showError("这不是一个 PDF 文件，请选择 .pdf 格式的图纸。");
      return;
    }
    dropZone.classList.add("busy");
    dropZone.querySelector(".drop-zone-title").textContent = "正在上传…";

    const form = new FormData();
    form.append("file", file);
    try {
      const resp = await fetch("/api/v1/documents", { method: "POST", body: form });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        showError(data.message || "上传失败，请重试。");
        return;
      }
      renderDocument(data.document, null);
    } catch (err) {
      showError("网络或服务异常，请确认应用仍在运行后重试。");
    } finally {
      dropZone.classList.remove("busy");
      dropZone.querySelector(".drop-zone-title").textContent = "点击选择，或将 PDF 图纸拖到这里";
    }
  }

  // 渲染"已上传"状态：收起上传区，展示文档卡片 + 预览；有结果则一并展示报告
  function renderDocument(doc, review) {
    currentDocId = doc.document_id;
    saveSession();
    document.getElementById("doc-filename").textContent = doc.filename;
    document.getElementById("doc-pages").textContent = `${doc.page_count} 页`;
    document.getElementById("doc-size").textContent = formatBytes(doc.size);
    document.getElementById("doc-sha256").textContent = doc.sha256;

    const img = document.getElementById("preview-img");
    img.src = `/api/v1/documents/${currentDocId}/preview/first`;

    dropZone.hidden = true;
    docCard.hidden = false;
    previewWrap.hidden = false;
    reviewRun.disabled = false;
    hideError();
    hideAllReviewUi();

    if (review) renderResults(review);
  }

  // 运行 AI 审核
  reviewRun.addEventListener("click", async () => {
    if (!currentDocId) return;
    // 清空旧结果，进入进行中状态
    hideAllReviewUi();
    reviewStatus.hidden = false;
    reviewRun.disabled = true;

    try {
      const resp = await fetch(`/api/v1/documents/${currentDocId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      });
      const data = await resp.json();
      reviewStatus.hidden = true;

      if (!resp.ok || !data.ok) {
        // 明确显示失败，不保留旧结果；文档与预览保留以便重试
        reviewFailMsg.textContent = data.message || "审核失败，请稍后重试。";
        reviewFail.hidden = false;
        return;
      }
      renderResults(data.result);
    } catch (err) {
      reviewStatus.hidden = true;
      reviewFailMsg.textContent = "网络或服务异常，审核未能完成，请确认应用仍在运行后重试。";
      reviewFail.hidden = false;
    } finally {
      reviewRun.disabled = false;
    }
  });

  function renderResults(result) {
    findingsList.textContent = "";
    const findingCount = (result.findings || []).length;

    if (findingCount === 0) {
      reportSummary.textContent = "共 0 条待确认问题；图纸内容完整，未发现明确问题。";
    } else {
      reportSummary.textContent = `共 ${findingCount} 条待确认问题；所有结论均为「待工程确认」。`;
    }

    (result.findings || []).forEach((f) => {
      const li = document.createElement("li");
      li.className = "finding";

      const head = document.createElement("div");
      head.className = "finding-head";
      const badge = document.createElement("span");
      badge.className = "finding-page";
      badge.textContent = `第 ${f.page} 页`;
      const title = document.createElement("span");
      title.className = "finding-title";
      title.textContent = f.title;
      const conclusion = document.createElement("span");
      conclusion.className = "finding-conclusion";
      conclusion.textContent = f.conclusion;
      head.append(badge, title, conclusion);

      const evidence = document.createElement("div");
      evidence.className = "finding-evidence";
      evidence.textContent = `证据：${f.evidence}`;

      li.append(head, evidence);
      findingsList.append(li);
    });

    if (findingCount === 0) {
      const li = document.createElement("li");
      li.className = "finding-empty";
      li.textContent = "图纸内容完整，未发现明确问题。";
      findingsList.append(li);
    }

    reviewResults.hidden = false;
  }

  // 下载《图纸 AI 审核报告》
  reportDownload.addEventListener("click", () => {
    if (!currentDocId) return;
    window.location.href = `/api/v1/documents/${currentDocId}/report`;
  });

  // 重新选择（回到上传区，保留同一视图）
  rechoose.addEventListener("click", () => {
    resetUpload();
  });

  // 重新上传（从报告页回到上传区）
  reviewAgain.addEventListener("click", () => {
    resetUpload();
  });

  document.querySelector(".app-header").addEventListener("click", (e) => {
    if (e.target.closest(".settings")) return;
    resetToHome();
  });

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }

  initProviders();
  restoreSession();
})();
