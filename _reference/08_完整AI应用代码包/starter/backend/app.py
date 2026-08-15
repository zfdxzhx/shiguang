"""Starter FastAPI shell: no PDF or AI product routes yet."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .credential_store import ProviderCredentialStore
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
        yield
        service.close()

    app = FastAPI(title="Drawing AI Course Starter", lifespan=lifespan)
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
        return {"ok": True, "mode": "local-only", "milestone": 0}

    @app.get("/api/v1/config")
    def configuration() -> dict:
        return service.config()

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
        (any(route.path == "/api/v1/health" for route in app.routes), "本地产品骨架"),
        (not any(route.path == "/api/v1/documents" for route in app.routes), "PDF 接入留作 CP1"),
        (not any("features" in route.path for route in app.routes), "三功能留作后续检查点"),
    ]
    for passed, label in checks:
        print(f"{'PASS' if passed else 'FAIL'} {label}")
    return 0 if all(passed for passed, _ in checks) else 1
