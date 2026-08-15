"""FastAPI 应用：首页静态托管 + 上传预览接口 + 健康检查。

第 2 步：支持上传 PDF、查看元信息与第一页预览；上传过程不调用任何 AI。
密钥不在代码或数据库中出现；设置区只提供两组 AI 组合供选择。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .intake import IntakeError, ingest_pdf, preview_path_for

# 内存中的文档登记表：document_id -> 元信息（服务重启后清空，私有文件保留在磁盘）
_DOCUMENTS: dict[str, dict] = {}

# 允许的两组 AI 组合（仅展示选择；密钥在第 3 步通过系统钥匙串处理）
PROVIDERS = (
    {"id": "gemini-deepseek", "label": "Gemini + DeepSeek", "default": True},
    {"id": "k3-deepseek", "label": "K3 + DeepSeek", "default": False},
)


def create_application(static_dir: Path) -> FastAPI:
    app = FastAPI(title="图纸 AI 审核助手", version="0.2.0")

    # 统一的业务错误返回（含面向用户的中文提示）
    @app.exception_handler(IntakeError)
    def on_intake_error(_request, exc: IntakeError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"ok": False, "message": exc.message})

    # 健康检查
    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "service": "图纸 AI 审核助手", "step": 2}

    # AI 设置：仅返回两组组合（不含密钥）
    @app.get("/api/v1/settings/providers")
    def providers() -> dict:
        return {"providers": list(PROVIDERS)}

    # 上传 PDF（不做任何 AI 调用）
    @app.post("/api/v1/documents")
    async def upload_document(file: UploadFile = File(...)) -> dict:
        raw = await file.read()
        meta = ingest_pdf(raw, file.filename or "upload.pdf")
        _DOCUMENTS[meta["document_id"]] = meta
        return {"ok": True, "document": meta}

    # 文档元信息
    @app.get("/api/v1/documents/{document_id}")
    def get_document(document_id: str) -> dict:
        meta = _DOCUMENTS.get(document_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="文档不存在或已失效，请重新上传。")
        return {"ok": True, "document": meta}

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
