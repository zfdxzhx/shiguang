"""Checkpoint 1 FastAPI app: private PDF intake and unified API settings."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .credential_store import ProviderCredentialStore
from .intake import IntakeError
from .models import ProviderConfigurationRequest
from .service import AnalysisService, ServiceError


def _local_origin(origin: str) -> bool:
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def create_application(
    legacy: Any,
    *,
    runtime_root: Path | None = None,
    frontend_dist: Path | None = None,
    credential_store: ProviderCredentialStore | None = None,
) -> FastAPI:
    base = Path(legacy.HERE).resolve()
    service = AnalysisService(runtime_root or (base / "runtime"), legacy.PACKAGE_ROOT, credential_store)
    dist = Path(frontend_dist or legacy.FRONTEND_DIST).resolve()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service.restore_persistent_provider()
        yield
        service.close()

    app = FastAPI(title="Drawing AI Course Checkpoint 1", lifespan=lifespan)
    app.state.review_service = service

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"ok": False, "error": "local host required"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and request.method not in {"GET", "HEAD", "OPTIONS"} and not _local_origin(origin):
            return JSONResponse({"ok": False, "error": "origin is not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status_code)

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True, "mode": "local-only", "milestone": 1}

    @app.get("/api/v1/config")
    @app.get("/api/v1/ai/status")
    def configuration() -> dict:
        return service.config()

    @app.post("/api/v1/ai/config")
    def configure_ai(payload: ProviderConfigurationRequest) -> dict:
        return service.configure_provider(payload)

    @app.delete("/api/v1/ai/config/persisted")
    def delete_persisted_ai_config() -> dict:
        return service.delete_persistent_provider()

    @app.post("/api/v1/documents", status_code=201)
    async def upload_document(file: UploadFile = File(...)) -> dict:
        if file.content_type not in {None, "", "application/pdf", "application/octet-stream"}:
            raise ServiceError("only PDF uploads are accepted", 415)
        try:
            await file.seek(0)
            return await run_in_threadpool(service.ingest_document, file.file, file.filename)
        finally:
            await file.close()

    @app.get("/api/v1/documents/{document_id}")
    def document_status(document_id: str) -> dict:
        return service.public_document(service.get_document(document_id))

    @app.get("/api/v1/documents/{document_id}/pages/{page}")
    def document_page(document_id: str, page: int):
        document = service.get_document(document_id)
        try:
            path = service.intake.page_path(document, page)
        except IntakeError as exc:
            raise ServiceError(str(exc), 404) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/{full_path:path}", include_in_schema=False)
    def static_spa(full_path: str):
        if not dist.is_dir():
            raise HTTPException(status_code=503, detail="frontend build is unavailable")
        requested = (dist / (full_path or "index.html")).resolve()
        if requested != dist and dist not in requested.parents:
            raise HTTPException(status_code=403, detail="static path escaped its root")
        target = requested if requested.is_file() else dist / "index.html"
        return FileResponse(target)

    return app


def backend_self_check(app: FastAPI) -> int:
    service: AnalysisService = app.state.review_service
    checks = [
        (service.private_root.is_dir(), "私有运行目录"),
        (bool(shutil.which("pdfinfo")), "pdfinfo 页数校验"),
        (bool(shutil.which("pdftoppm")), "pdftoppm 本地分页"),
        (any(route.path == "/api/v1/documents" for route in app.routes), "PDF 接入 API"),
        (any(route.path == "/api/v1/ai/config" for route in app.routes), "统一 API 设置"),
        (not any("features" in route.path for route in app.routes), "AI 功能留作 CP2/CP3"),
    ]
    for passed, label in checks:
        print(f"{'PASS' if passed else 'FAIL'} {label}")
    return 0 if all(passed for passed, _ in checks) else 1
