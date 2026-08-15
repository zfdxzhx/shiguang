"""FastAPI 应用：首页静态托管 + 上传预览 + AI 审核 + 报告下载。

第 4 步：完整演示。密钥只在启动时读入内存，不进前端/数据库/日志/Git；
审核失败时接口返回明确错误，前端清空旧结果展示失败原因；
GET /documents/{id}/review 返回最近审核状态，供前端刷新后恢复已完成结果。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .engineering_review import ReviewError, run_review
from .env import load_local_environment
from .intake import IntakeError, ingest_pdf, preview_path_for
from .models import ReviewResult
from .pdf_report import build_report_pdf

# 内存中的文档登记表：document_id -> 元信息与最近一次审核结果
_DOCUMENTS: dict[str, dict] = {}

# 允许的两组 AI 组合（仅展示选择与运行映射；密钥不在此处）
# vision_key 指明该组合需要哪个视觉密钥，用于判断组合是否可用
PROVIDERS = (
    {"id": "gemini-deepseek", "label": "Gemini + DeepSeek", "vision": "gemini", "vision_key": "GEMINI_API_KEY"},
    {"id": "k3-deepseek", "label": "K3 + DeepSeek", "vision": "k3", "vision_key": "KIMI_API_KEY"},
)
_VISION_BY_ID = {p["id"]: p["vision"] for p in PROVIDERS}


def _provider_catalog() -> list[dict]:
    """返回可给前端的组合列表：带 enabled（是否已配置视觉密钥）与 default（默认选择）。"""
    items = []
    for p in PROVIDERS:
        enabled = bool(os.environ.get(p["vision_key"], "").strip())
        items.append({"id": p["id"], "label": p["label"], "vision": p["vision"], "enabled": enabled})
    default = next((it["id"] for it in items if it["enabled"]), None)
    return items, default


def create_application(static_dir: Path) -> FastAPI:
    app = FastAPI(title="图纸 AI 审核助手", version="0.4.0")

    load_local_environment()

    @app.exception_handler(IntakeError)
    def on_intake_error(_request, exc: IntakeError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"ok": False, "message": exc.message})

    @app.exception_handler(ReviewError)
    def on_review_error(_request, exc: ReviewError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"ok": False, "message": exc.message})

    # 健康检查
    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "service": "图纸 AI 审核助手", "step": 4}

    # AI 设置：只返回两组组合（不含密钥），并标出默认可用组合
    @app.get("/api/v1/settings/providers")
    def providers() -> dict:
        items, default = _provider_catalog()
        return {"providers": items, "default": default}

    # 上传 PDF（不做任何 AI 调用）
    @app.post("/api/v1/documents")
    async def upload_document(file: UploadFile = File(...)) -> dict:
        raw = await file.read()
        meta = ingest_pdf(raw, file.filename or "upload.pdf")
        _DOCUMENTS[meta["document_id"]] = {"meta": meta, "review": None}
        return {"ok": True, "document": meta}

    # 文档元信息
    @app.get("/api/v1/documents/{document_id}")
    def get_document(document_id: str) -> dict:
        entry = _DOCUMENTS.get(document_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="文档不存在或已失效，请重新上传。")
        return {"ok": True, "document": entry["meta"]}

    # 审核状态：刷新后前端据此恢复"已上传/已审核"视图（不含密钥与路径）
    @app.get("/api/v1/documents/{document_id}/review")
    def get_review_state(document_id: str) -> dict:
        entry = _DOCUMENTS.get(document_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="文档不存在或已失效，请重新上传。")
        review = entry.get("review")
        return {
            "ok": True,
            "document": entry["meta"],
            "review": review.model_dump() if review else None,
        }

    # 第一页预览图（返回图片字节，不暴露本机路径）
    @app.get("/api/v1/documents/{document_id}/preview/first")
    def first_preview(document_id: str) -> FileResponse:
        if document_id not in _DOCUMENTS:
            raise HTTPException(status_code=404, detail="文档不存在或已失效，请重新上传。")
        path = preview_path_for(document_id)
        return FileResponse(
            path,
            media_type="image/png",
            filename="preview.png",
            headers={"Cache-Control": "no-store"},
        )

    # 运行 AI 审核（失败时返回明确错误，不返回半截结果）
    @app.post("/api/v1/documents/{document_id}/review")
    def review_document(document_id: str, body: dict | None = None) -> dict:
        entry = _DOCUMENTS.get(document_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="文档不存在或已失效，请重新上传。")

        combo = (body or {}).get("provider", "gemini-deepseek")
        vision = _VISION_BY_ID.get(combo)
        if vision is None:
            raise HTTPException(status_code=400, detail="未知的 AI 组合设置。")

        meta = entry["meta"]
        # 先清空旧结果：本次审核无论成败，旧的审核报告都不应继续可用
        entry["review"] = None
        result = run_review(document_id, meta["filename"], vision)
        entry["review"] = result
        return {"ok": True, "result": result.model_dump()}

    # 下载《图纸 AI 审核报告》（基于最近一次审核结果）
    @app.get("/api/v1/documents/{document_id}/report")
    def download_report(document_id: str) -> Response:
        entry = _DOCUMENTS.get(document_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="文档不存在或已失效，请重新上传。")
        result: ReviewResult | None = entry.get("review")
        if result is None:
            raise HTTPException(status_code=400, detail="尚未完成 AI 审核，请先运行审核再下载报告。")
        pdf_bytes = build_report_pdf(result)
        filename = f"图纸AI审核报告_{result.filename.rsplit('.', 1)[0]}.pdf"
        # RFC 5987：filename* 用 UTF-8 百分号编码（中文文件名），filename 用 ASCII 兜底
        ascii_fallback = "review_report.pdf"
        encoded = quote(filename, safe="")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}",
                "Cache-Control": "no-store",
            },
        )

    # 首页
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    # 静态资源（style.css / app.js 等）
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def backend_self_check() -> bool:
    """自检：确认首页与静态资源存在，返回是否全部就绪。"""
    from server import STATIC_DIR

    index_ok = (STATIC_DIR / "index.html").is_file()
    css_ok = (STATIC_DIR / "style.css").is_file()
    js_ok = (STATIC_DIR / "app.js").is_file()

    def report(name: str, ok: bool) -> None:
        print(f"{'PASS' if ok else 'FAIL'} {name}")

    report("首页 static/index.html", index_ok)
    report("样式 static/style.css", css_ok)
    report("脚本 static/app.js", js_ok)
    return index_ok and css_ok and js_ok
