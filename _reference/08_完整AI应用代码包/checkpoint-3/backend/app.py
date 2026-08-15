"""FastAPI application for the local three-feature product."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .credential_store import ProviderCredentialStore
from .course_stage import MILESTONE
from .intake import IntakeError
from .models import (
    AnalysisRequest,
    BusinessArtifactsV1,
    ClassroomReferenceProfile,
    DecisionRequest,
    EvidenceLocalizationBenchmarkRequest,
    EvidenceLocationRequest,
    FieldCorrectionRequest,
    FinalizeRequest,
    PreQuoteRequest,
    ProcessPlanConfirmationRequest,
    ProcessPlanDraftV1,
    ProcessPlanDraftV2,
    ProcessPlanRequest,
    ProviderConfigurationRequest,
)
from .service import AnalysisService, ServiceError


ALLOWED_ORIGINS = {
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:8766",
    "http://localhost:8766",
}


def is_local_origin(origin: str) -> bool:
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
    service = AnalysisService(
        runtime_root or (base / "runtime"),
        legacy.PACKAGE_ROOT,
        credential_store=credential_store,
    )
    dist = Path(frontend_dist or legacy.FRONTEND_DIST).resolve()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service.restore_persistent_provider()
        yield
        service.close()

    app = FastAPI(
        title="Drawing Review Local Application",
        version="2.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.review_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_ORIGINS),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"ok": False, "error": "local host required"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and not is_local_origin(origin):
                return JSONResponse({"ok": False, "error": "origin is not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' "
            "http://127.0.0.1:8766 http://localhost:8766; img-src 'self' data: blob:; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status_code)

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True, "mode": "local-only", "api_version": "v1"}

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

    @app.get("/api/v1/schemas/review-draft-v2")
    def review_draft_schema() -> dict:
        from .models import ReviewDraftV2

        return ReviewDraftV2.model_json_schema()

    @app.get("/api/v1/schemas/business-workflow-v1")
    def business_workflow_schema() -> dict:
        return {
            "artifacts": BusinessArtifactsV1.model_json_schema(),
            "classroom_reference_profile": ClassroomReferenceProfile.model_json_schema(),
            "process_plan_request": ProcessPlanRequest.model_json_schema(),
            "process_plan_confirmation_request": ProcessPlanConfirmationRequest.model_json_schema(),
            "process_plan_v1": ProcessPlanDraftV1.model_json_schema(),
            "process_plan": ProcessPlanDraftV2.model_json_schema(),
            "prequote_request": PreQuoteRequest.model_json_schema(),
        }

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
    @app.get("/api/v1/documents/{document_id}/status")
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

    @app.post("/api/v1/analyses", status_code=202)
    @app.post("/api/v1/reviews", status_code=202)
    def create_analysis(payload: AnalysisRequest) -> dict:
        return service.create_analysis(payload)

    @app.post("/api/v1/features/{feature}/runs", status_code=202)
    def create_feature_run(feature: str, payload: AnalysisRequest) -> dict:
        if feature not in {"review", "process", "quote"}:
            raise ServiceError("feature must be review, process, or quote", 404)
        required_milestone = {"review": 2, "process": 3, "quote": 3}[feature]
        if MILESTONE < required_milestone:
            raise ServiceError(
                f"{feature} is unlocked at checkpoint-{required_milestone}",
                409,
            )
        run = service.create_analysis(payload.model_copy(update={"feature": feature}))
        return service.feature_run_payload(run["id"])

    @app.get("/api/v1/features/history")
    def feature_history(limit: int = 100) -> dict:
        return {"runs": service.feature_history(limit)}

    @app.get("/api/v1/features/runs/{analysis_id}")
    def feature_run(analysis_id: str) -> dict:
        return service.feature_run_payload(analysis_id)

    @app.get("/api/v1/features/runs/{analysis_id}/report")
    def feature_report(analysis_id: str):
        path, feature = service.create_feature_report(analysis_id)
        filenames = {
            "review": "AI-drawing-review-report.pdf",
            "process": "AI-process-route-card.pdf",
            "quote": "AI-reference-quote.pdf",
        }
        return FileResponse(path, media_type="application/pdf", filename=filenames[feature])

    @app.get("/api/v1/analyses")
    @app.get("/api/v1/history")
    def analyses(limit: int = 100) -> dict:
        return {"analyses": service.list_analyses(limit)}

    @app.get("/api/v1/analyses/{analysis_id}")
    @app.get("/api/v1/analyses/{analysis_id}/status")
    @app.get("/api/v1/reviews/{analysis_id}")
    def analysis_status(analysis_id: str) -> dict:
        return service.analysis_payload(analysis_id)

    @app.post("/api/v1/analyses/{analysis_id}/retry", status_code=202)
    def retry_analysis(analysis_id: str) -> dict:
        return service.retry(analysis_id)

    @app.post("/api/v1/benchmarks/evidence-localization")
    def benchmark_evidence_localization(payload: EvidenceLocalizationBenchmarkRequest) -> dict:
        return service.benchmark_evidence_localization(payload)

    @app.patch("/api/v1/reviews/{analysis_id}/findings/{finding_id}")
    def decide_finding(analysis_id: str, finding_id: str, payload: DecisionRequest) -> dict:
        return service.decide(analysis_id, finding_id, payload)

    @app.patch("/api/v1/reviews/{analysis_id}/fields/{field_name}")
    def correct_field(
        analysis_id: str,
        field_name: str,
        payload: FieldCorrectionRequest,
    ) -> dict:
        return service.correct_field(analysis_id, field_name, payload)

    @app.patch("/api/v1/reviews/{analysis_id}/evidence/{evidence_id}/location")
    def locate_evidence(
        analysis_id: str,
        evidence_id: str,
        payload: EvidenceLocationRequest,
    ) -> dict:
        return service.locate_evidence(analysis_id, evidence_id, payload)

    @app.post("/api/v1/reviews/{analysis_id}/finalize")
    def finalize_analysis(
        analysis_id: str,
        payload: FinalizeRequest,
    ) -> dict:
        return service.finalize(analysis_id, payload)

    @app.get("/api/v1/analyses/{analysis_id}/business-artifacts")
    def business_artifacts(analysis_id: str) -> dict:
        return service.business_artifacts(analysis_id).model_dump(mode="json")

    @app.get("/api/v1/analyses/{analysis_id}/classroom-reference-profile")
    def classroom_reference_profile(analysis_id: str) -> dict:
        return service.classroom_reference_profile(analysis_id).model_dump(mode="json")

    @app.post("/api/v1/analyses/{analysis_id}/process-plan")
    def create_process_plan(analysis_id: str, payload: ProcessPlanRequest) -> dict:
        return service.create_process_plan(analysis_id, payload).model_dump(mode="json")

    @app.post("/api/v1/analyses/{analysis_id}/process-plan/confirm")
    def confirm_process_plan(analysis_id: str, payload: ProcessPlanConfirmationRequest) -> dict:
        return service.confirm_process_plan(analysis_id, payload).model_dump(mode="json")

    @app.post("/api/v1/analyses/{analysis_id}/prequote")
    def create_prequote(analysis_id: str, payload: PreQuoteRequest) -> dict:
        return service.create_prequote(analysis_id, payload).model_dump(mode="json")

    @app.get("/api/v1/analyses/{analysis_id}/export")
    def export_analysis(analysis_id: str, format: str = "json"):
        path, media_type, _ = service.create_export(analysis_id, format)
        if format == "process-pdf":
            filename = f"process-route-draft-{analysis_id}.pdf"
        elif format == "pdf-draft":
            filename = f"drawing-engineering-review-draft-{analysis_id}.pdf"
        elif format == "pdf":
            filename = f"drawing-engineering-review-{analysis_id}.pdf"
        else:
            filename = f"drawing-review-{analysis_id}.{format}"
        return FileResponse(
            path,
            media_type=media_type,
            filename=filename,
        )

    # Legacy course console APIs remain available while the product UI migrates.
    @app.get("/api/health")
    def legacy_health() -> dict:
        return {"ok": True, "mode": "local-only"}

    @app.get("/api/bootstrap")
    def legacy_bootstrap() -> dict:
        return legacy.bootstrap_payload()

    @app.post("/api/workspaces", status_code=201)
    async def legacy_workspace(payload: dict = Body(...)) -> dict:
        try:
            workspace, output = await run_in_threadpool(legacy.create_workspace, str(payload.get("group") or ""))
        except legacy.ApiProblem as exc:
            raise ServiceError(str(exc), exc.status) from exc
        return {"ok": True, "workspace": workspace, "output": output}

    @app.post("/api/action")
    async def legacy_action(payload: dict = Body(...)) -> dict:
        try:
            return await run_in_threadpool(
                legacy.run_course_action,
                str(payload.get("workspace_id") or ""),
                str(payload.get("action") or ""),
                str(payload.get("stage_id")) if payload.get("stage_id") is not None else None,
            )
        except legacy.ApiProblem as exc:
            raise ServiceError(str(exc), exc.status) from exc

    @app.post("/api/instructor-action")
    async def legacy_instructor_action(payload: dict = Body(...)) -> dict:
        try:
            return await run_in_threadpool(legacy.run_instructor_action, str(payload.get("action") or ""))
        except legacy.ApiProblem as exc:
            raise ServiceError(str(exc), exc.status) from exc

    @app.post("/api/open-workspace")
    def legacy_open_workspace(payload: dict = Body(...)) -> dict:
        try:
            workspace = legacy.safe_workspace_path(str(payload.get("workspace_id") or ""))
        except legacy.ApiProblem as exc:
            raise ServiceError(str(exc), exc.status) from exc
        if sys.platform != "darwin":
            raise HTTPException(status_code=501, detail="automatic Finder opening is only available on macOS")
        subprocess.Popen(["open", str(workspace)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "message": "已在 Finder 中打开学员工作区。"}

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
        (service.db.path.is_file(), "SQLite 审核库"),
        (bool(__import__("shutil").which("pdfinfo")), "pdfinfo 页数校验"),
        (bool(__import__("shutil").which("pdftoppm")), "pdftoppm 本地分页"),
        (any(route.path == "/api/v1/documents" for route in app.routes), "PDF 接入 API"),
        (any(route.path == "/api/v1/features/{feature}/runs" for route in app.routes), "三功能运行 API"),
    ]
    for passed, label in checks:
        print(f"{'PASS' if passed else 'FAIL'} {label}")
    config = service.config()
    readiness = "PASS" if config["configured"] else "OPTIONAL"
    print(f"{readiness} {config['provider']} 实时模式（需后端密钥 + 视觉模型）")
    return 0 if all(item[0] for item in checks) else 1
