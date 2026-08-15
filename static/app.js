// 第 2 步：上传 PDF 并预览。上传过程不调用 AI；设置只保留两组 AI 组合（密钥不在前端出现）。
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

  // AI 组合选择：仅前端状态，不落库、不出现密钥
  let provider = "gemini-deepseek";
  document.querySelectorAll('input[name="provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      provider = radio.value;
    });
  });

  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.hidden = false;
  };

  const hideError = () => {
    errorBox.hidden = true;
  };

  const resetToHome = () => {
    home.hidden = false;
    upload.hidden = true;
    hideError();
  };

  const resetUpload = () => {
    docCard.hidden = true;
    previewWrap.hidden = true;
    reviewRun.disabled = true;
    fileInput.value = "";
    hideError();
  };

  // 首页入口 → 上传视图
  entry.addEventListener("click", () => {
    home.hidden = true;
    upload.hidden = false;
    resetUpload();
  });

  // 点击 / 拖放选择文件
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
      renderDocument(data.document);
    } catch (err) {
      showError("网络或服务异常，请确认应用仍在运行后重试。");
    } finally {
      dropZone.classList.remove("busy");
      dropZone.querySelector(".drop-zone-title").textContent = "点击选择，或将 PDF 图纸拖到这里";
    }
  }

  function renderDocument(doc) {
    document.getElementById("doc-filename").textContent = doc.filename;
    document.getElementById("doc-pages").textContent = `${doc.page_count} 页`;
    document.getElementById("doc-size").textContent = formatBytes(doc.size);
    document.getElementById("doc-sha256").textContent = doc.sha256;

    const img = document.getElementById("preview-img");
    img.src = `/api/v1/documents/${doc.document_id}/preview/first`;

    docCard.hidden = false;
    previewWrap.hidden = false;
    reviewRun.disabled = false;
    hideError();
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }

  // 重新选择：清空并回到上传区
  rechoose.addEventListener("click", () => {
    resetUpload();
  });

  // 回到首页入口
  // 点击顶部标题返回首页
  document.querySelector(".app-header").addEventListener("click", (e) => {
    if (e.target.closest(".settings")) return;
    resetToHome();
  });
})();
