"""Application service for asynchronous analysis, human gates, and exports."""

from __future__ import annotations

import hashlib
import html
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Lock
from uuid import uuid4

import httpx

from .credential_store import CredentialStoreError, ProviderCredentialStore
from .database import Database, utc_now
from .engineering_review import build_engineering_review
from .intake import PdfIntake
from .pdf_report import build_review_report_pdf
from .process_plan_pdf import build_process_plan_pdf
from .quote_report import build_quote_report_pdf
from .models import (
    AnalysisRequest,
    BusinessArtifactsV1,
    ClassroomReferenceProfile,
    DecisionRequest,
    DrawingFactsV1,
    EvidenceLocationRequest,
    EvidenceLocalizationBenchmarkRequest,
    FieldCorrectionRequest,
    FinalizeRequest,
    PreQuoteRequest,
    PreQuoteV1,
    ProcessPlanDraft,
    ProcessPlanConfirmationRequest,
    ProcessPlanDraftV1,
    ProcessPlanDraftV2,
    ProcessPlanRequest,
    ProviderConfigurationRequest,
    ReviewDraftV2,
    RuleReport,
)
from .reference_profiles import build_classroom_reference_profile
from .providers import (
    CONDITIONAL_HYBRID_PROVIDERS,
    MockProvider,
    GeminiVisionProvider,
    KimiVisionProvider,
    ProviderSettings,
    create_live_provider,
    provider_settings_from_environment,
    provider_settings_from_session,
    provider_status,
)
from .rules import evaluate_draft
from .workflows import (
    FIELD_CORRECTION_PREFIX,
    assess_feature_inputs,
    build_artifacts,
    build_drawing_facts,
    build_effective_review_draft,
    build_prequote,
    build_process_plan,
)
from .course_stage import MILESTONE


class ServiceError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class AnalysisService:
    def __init__(
        self,
        runtime_root: Path,
        package_root: Path,
        credential_store: ProviderCredentialStore | None = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.private_root = self.runtime_root / "private"
        self.private_root.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.private_root / "drawing_review.sqlite3")
        self.intake = PdfIntake(self.private_root / "documents")
        self.exports_root = self.private_root / "exports"
        self.exports_root.mkdir(parents=True, exist_ok=True)
        self.mock_provider = MockProvider(package_root)
        self.milestone = MILESTONE
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="drawing-ai")
        self._provider_lock = Lock()
        self._provider_settings = provider_settings_from_environment()
        self._provider_verification = self._verification_state(self._provider_settings)
        self._credential_store = credential_store
        self._persistent_credentials_saved = False
        self._credential_store_error: str | None = None
        self._analysis_provider_settings: dict[str, ProviderSettings] = {}

    def restore_persistent_provider(self) -> None:
        """Prefer explicit environment settings, otherwise restore Keychain."""

        store = self._credential_store
        if store is None or not store.available:
            return
        current = provider_settings_from_environment()
        if provider_status(current)["configured"]:
            return
        try:
            saved = store.load()
        except CredentialStoreError as exc:
            self._credential_store_error = str(exc)
            return
        if saved is None:
            return
        with self._provider_lock:
            self._provider_settings = saved
            self._provider_verification = self._verification_state(saved, "unverified")
            self._persistent_credentials_saved = True
            self._credential_store_error = None

    @staticmethod
    def _verification_state(
        settings: ProviderSettings,
        status: str | None = None,
        *,
        secondary_status: str | None = None,
    ) -> dict:
        live = provider_status(settings)
        configured = live["configured"]
        current_status = status or ("unverified" if configured else "not_configured")
        is_hybrid = settings.provider in CONDITIONAL_HYBRID_PROVIDERS
        primary_status = {
            "verified": "verified",
            "primary_verified": "verified",
            "failed": "failed",
        }.get(current_status, current_status)
        if is_hybrid:
            current_secondary_status = secondary_status or (
                "verified" if current_status == "verified" else "unverified"
            )
        else:
            current_secondary_status = "not_applicable"
        return {
            "status": current_status,
            "primary_status": primary_status,
            "secondary_status": current_secondary_status,
            "provider": settings.provider,
            "model": live["model"],
            "visual_model": live["visual_model"],
            "secondary_model": live["secondary_model"],
            "checked_at": utc_now() if current_status in {"verified", "primary_verified", "failed"} else None,
        }

    @staticmethod
    def _primary_provider_family(provider: str) -> str:
        if provider in {"hybrid", "gemini"}:
            return "gemini"
        if provider in {"kimi-hybrid", "kimi"}:
            return "kimi"
        return provider

    def _record_provider_verification(
        self,
        settings: ProviderSettings,
        status: str,
        *,
        secondary_status: str | None = None,
    ) -> None:
        """Record only safe evidence for the currently active in-memory credentials."""

        if status not in {"verified", "primary_verified", "failed"}:
            raise ValueError("provider verification status is invalid")
        with self._provider_lock:
            # A slower request must not verify credentials that the user replaced
            # while the analysis was still running.
            if self._provider_settings != settings:
                return
            self._provider_verification = self._verification_state(
                settings,
                status,
                secondary_status=secondary_status,
            )

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)
        with self._provider_lock:
            self._analysis_provider_settings.clear()
            settings = ProviderSettings(provider="kimi", model="", api_key="")
            self._provider_settings = settings
            self._provider_verification = self._verification_state(settings)

    @staticmethod
    def public_document(document: dict) -> dict:
        return {
            "id": document["id"],
            "filename": document["original_name"],
            "sha256": document["sha256"],
            "size_bytes": document["size_bytes"],
            "page_count": document["page_count"],
            "status": document["status"],
            "error": document.get("error"),
            "created_at": document["created_at"],
            "page_urls": [
                f"/api/v1/documents/{document['id']}/pages/{page}"
                for page in range(1, int(document["page_count"]) + 1)
            ],
        }

    def ingest_document(self, stream, filename: str | None) -> dict:
        try:
            record = self.intake.ingest(stream, filename)
        except Exception as exc:
            raise ServiceError(str(exc), 422) from exc
        self.db.create_document(record)
        return self.public_document(self.db.get_document(record["id"]) or record)

    def get_document(self, document_id: str) -> dict:
        document = self.db.get_document(document_id)
        if not document:
            raise ServiceError("document not found", 404)
        return document

    def config(self) -> dict:
        with self._provider_lock:
            settings = self._provider_settings
            verification = dict(self._provider_verification)
        live = provider_status(settings)
        return {
            "mode": "local-only",
            "milestone": self.milestone,
            "provider": live["provider"],
            "model": live["model"],
            "visual_model": live["visual_model"],
            "secondary_model": live["secondary_model"],
            "credential_available": live["api_key_configured"],
            "secondary_credential_available": live["secondary_api_key_configured"],
            "configured": live["configured"],
            "configuration_source": live["configuration_source"],
            "credential_storage": (
                "keychain" if settings.source == "keychain" else
                "session" if settings.source == "session" else settings.source
            ),
            "persistent_credentials_supported": bool(
                self._credential_store is not None and self._credential_store.available
            ),
            "persistent_credentials_saved": self._persistent_credentials_saved,
            "credential_store_error": self._credential_store_error,
            "reasoning_effort": live["reasoning_effort"],
            "endpoint": live["endpoint"],
            "provider_options": live["provider_options"],
            "verification": verification,
            "consent_required": True,
            "configuration_boundary": (
                "API keys are never returned, written to browser storage, SQLite, exports, logs, Git, or "
                "application files. When selected, persistence uses only the current macOS user's Keychain."
            ),
            "boundary": (
                "The product exposes one live AI workflow. Rendered pages are sent externally only after explicit consent. "
                "Automated test fixtures are not exposed as a product feature or request option. "
                "Hybrid mode sends images only to its configured visual provider and conditionally sends selected minimized fields "
                "to DeepSeek when an uncertainty signal or explicit user request triggers a second review."
            ),
        }

    def configure_provider(self, request: ProviderConfigurationRequest) -> dict:
        api_key = (
            request.api_key.get_secret_value()
            if request.api_key is not None
            else ""
        )
        if request.reuse_primary:
            with self._provider_lock:
                current_settings = self._provider_settings
            if not current_settings.api_key:
                raise ServiceError("当前没有可沿用的视觉模型配置，请输入 API Key。", 412)
            if self._primary_provider_family(current_settings.provider) != self._primary_provider_family(request.provider):
                raise ServiceError("切换视觉模型方案时需要输入对应的新 API Key。", 412)
            api_key = current_settings.api_key
        secondary_api_key = (
            request.secondary_api_key.get_secret_value()
            if request.secondary_api_key is not None
            else ""
        )
        if request.reuse_secondary:
            with self._provider_lock:
                reusable_secondary_key = self._provider_settings.secondary_api_key
            if not reusable_secondary_key:
                raise ServiceError("当前没有可沿用的 DeepSeek 配置，请输入 DeepSeek API Key。", 412)
            secondary_api_key = reusable_secondary_key
        settings = provider_settings_from_session(
            provider=request.provider,
            api_key=api_key,
            model=request.model,
            secondary_api_key=secondary_api_key,
            secondary_model=request.secondary_model or "",
        )
        if request.storage == "keychain":
            store = self._credential_store
            if store is None or not store.available:
                raise ServiceError("当前系统无法使用 macOS 钥匙串，请改用仅本次运行。", 503)
            try:
                store.save(settings)
            except CredentialStoreError as exc:
                raise ServiceError(str(exc), 503) from exc
            settings = replace(settings, source="keychain")
            self._persistent_credentials_saved = True
        with self._provider_lock:
            self._provider_settings = settings
            self._provider_verification = self._verification_state(settings, "unverified")
            self._credential_store_error = None
        return self.config()

    def delete_persistent_provider(self) -> dict:
        store = self._credential_store
        if store is None or not store.available:
            raise ServiceError("当前系统无法使用 macOS 钥匙串。", 503)
        try:
            store.delete()
        except CredentialStoreError as exc:
            raise ServiceError(str(exc), 503) from exc
        with self._provider_lock:
            self._persistent_credentials_saved = False
            self._credential_store_error = None
            if self._provider_settings.source == "keychain":
                settings = provider_settings_from_environment()
                self._provider_settings = settings
                self._provider_verification = self._verification_state(settings)
        return self.config()

    def create_analysis(self, request: AnalysisRequest) -> dict:
        document = self.get_document(request.document_id)
        if document["status"] != "ready":
            raise ServiceError("document is not ready for analysis", 409)
        if request.mode != "mock" and not request.external_processing_consent:
            raise ServiceError("external_processing_consent is required for AI analysis", 403)
        with self._provider_lock:
            settings = self._provider_settings
        live = provider_status(settings)
        if request.mode != "mock" and not live["configured"]:
            raise ServiceError(f"{live['provider']} API key and model are not configured", 412)
        analysis_id = f"run-{uuid4().hex}"
        record = {
            "id": analysis_id,
            "document_id": request.document_id,
            "feature": request.feature,
            "mode": request.mode,
            "provider": "mock" if request.mode == "mock" else live["provider"],
            "model": "deterministic-classroom-fixture" if request.mode == "mock" else live["model"],
            "technical_status": "queued",
            "external_processing_consent": request.external_processing_consent,
        }
        if request.mode != "mock":
            with self._provider_lock:
                self._analysis_provider_settings[analysis_id] = settings
        try:
            self.db.create_analysis(record)
            self.executor.submit(self._run_analysis, analysis_id)
        except Exception:
            with self._provider_lock:
                self._analysis_provider_settings.pop(analysis_id, None)
            raise
        return self.analysis_payload(analysis_id)

    @staticmethod
    def _fixed_location_target_ids(draft: ReviewDraftV2, *, page_count: int) -> list[str]:
        evidence = {item.id: item for item in draft.evidence}
        target_ids: list[str] = []
        for finding in draft.findings:
            for evidence_id in finding.evidence_ids:
                item = evidence.get(evidence_id)
                if (
                    item is None
                    or evidence_id in target_ids
                    or not item.text.strip()
                    or not 1 <= item.page <= page_count
                ):
                    continue
                target_ids.append(evidence_id)
        return target_ids

    @staticmethod
    def _benchmark_token_count(stage: dict) -> int:
        total = 0
        for call in stage.get("calls") or []:
            usage = call.get("usage") or {}
            value = usage.get("total_tokens")
            if value is None:
                value = usage.get("totalTokenCount")
            if isinstance(value, (int, float)):
                total += int(value)
        return total

    def benchmark_evidence_localization(
        self,
        request: EvidenceLocalizationBenchmarkRequest,
    ) -> dict:
        """Compare only fixed evidence-location targets with no primary or DeepSeek call."""

        if not request.external_processing_consent:
            raise ServiceError("external_processing_consent is required for a live benchmark", 403)
        with self._provider_lock:
            settings = self._provider_settings

        if not settings.api_key or not settings.model:
            raise ServiceError("the current visual provider is not configured", 412)
        if settings.provider in {"kimi", "kimi-hybrid"}:
            provider_name = "kimi"
            visual_settings = replace(settings, provider="kimi")
            visual_provider = KimiVisionProvider(visual_settings)
        elif settings.provider in {"gemini", "hybrid"}:
            provider_name = "gemini"
            visual_settings = replace(settings, provider="gemini")
            visual_provider = GeminiVisionProvider(visual_settings)
        else:
            raise ServiceError("fixed evidence localization supports only Kimi or Gemini", 412)

        results: list[dict] = []
        manifest_entries: list[dict] = []
        total_targets = 0
        total_accepted = 0
        total_elapsed_ms = 0
        total_tokens = 0
        for analysis_id in request.source_analysis_ids:
            analysis = self.db.get_analysis(analysis_id)
            if not analysis:
                raise ServiceError(f"source analysis not found: {analysis_id}", 404)
            if analysis.get("technical_status") != "completed" or not analysis.get("draft_json"):
                raise ServiceError(f"source analysis is not completed: {analysis_id}", 409)
            document = self.get_document(analysis["document_id"])
            draft = ReviewDraftV2.model_validate_json(analysis["draft_json"]).model_copy(deep=True)
            target_ids = self._fixed_location_target_ids(
                draft,
                page_count=int(document["page_count"]),
            )
            if not target_ids:
                raise ServiceError(f"source analysis has no fixed location targets: {analysis_id}", 422)
            if len(target_ids) > 12:
                raise ServiceError(f"source analysis exceeds the 12-target safety limit: {analysis_id}", 422)

            evidence = {item.id: item for item in draft.evidence}
            target_manifest = [
                {
                    "evidence_id": evidence_id,
                    "page": evidence[evidence_id].page,
                    "region": evidence[evidence_id].region,
                    "description": evidence[evidence_id].description,
                    "text": evidence[evidence_id].text,
                }
                for evidence_id in target_ids
            ]
            target_manifest_hash = hashlib.sha256(
                json.dumps(target_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            target_tokens = {
                item["evidence_id"]: hashlib.sha256(
                    f"{analysis_id}|{item['evidence_id']}|{item['page']}|{item['text']}".encode("utf-8")
                ).hexdigest()[:20]
                for item in target_manifest
            }
            for evidence_id in target_ids:
                evidence[evidence_id].bbox = None

            page_paths = self.intake.all_page_paths(document)
            target_pages = sorted({evidence[evidence_id].page for evidence_id in target_ids})
            prepared_images = {
                page: KimiVisionProvider._localization_data_url(page_paths[page - 1])
                for page in target_pages
            }
            started = time.monotonic()
            if provider_name == "kimi":
                stage = visual_provider._localize_missing_finding_evidence(
                    draft=draft,
                    page_paths=page_paths,
                    prepared_images=prepared_images,
                )
            else:
                endpoint = f"{visual_provider.api_root}/{visual_provider.model}:generateContent"
                stage = visual_provider._localize_missing_finding_evidence(
                    draft=draft,
                    page_paths=page_paths,
                    endpoint=endpoint,
                    prepared_images=prepared_images,
                )
            elapsed_ms = round((time.monotonic() - started) * 1000)
            if stage.get("target_count") != len(target_ids):
                raise ServiceError("fixed target count changed before provider dispatch", 500)

            accepted_ids = stage.get("accepted_evidence_ids") or []
            safe_calls = [
                {
                    key: call.get(key)
                    for key in (
                        "page", "status", "target_count", "accepted_count", "model",
                        "reasoning_effort", "elapsed_ms", "failure_type", "finish_reason",
                        "usage", "input_image",
                    )
                    if call.get(key) is not None
                }
                for call in stage.get("calls") or []
            ]
            result = {
                "source_analysis_id": analysis_id,
                "document_id": document["id"],
                "filename": document["original_name"],
                "target_manifest_sha256": target_manifest_hash,
                "target_count": len(target_ids),
                "accepted_count": int(stage.get("accepted_count") or 0),
                "accepted_target_hashes": sorted(
                    target_tokens[evidence_id]
                    for evidence_id in accepted_ids
                    if evidence_id in target_tokens
                ),
                "rejected": dict(stage.get("rejected") or {}),
                "status": stage.get("status"),
                "elapsed_ms": elapsed_ms,
                "token_count": self._benchmark_token_count(stage),
                "calls": safe_calls,
            }
            results.append(result)
            manifest_entries.append({
                "source_analysis_id": analysis_id,
                "target_manifest_sha256": target_manifest_hash,
            })
            total_targets += result["target_count"]
            total_accepted += result["accepted_count"]
            total_elapsed_ms += result["elapsed_ms"]
            total_tokens += result["token_count"]

        return {
            "benchmark_version": "fixed-evidence-location-v1",
            "provider": provider_name,
            "model": visual_provider.localization_model if provider_name == "kimi" else visual_provider.model,
            "source_policy": "ordered union of finding-linked evidence from the completed source drafts",
            "image_policy": "identical in-memory PNG bytes, longest edge at most 2000 px",
            "target_set_sha256": hashlib.sha256(
                json.dumps(manifest_entries, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "target_count": total_targets,
            "accepted_count": total_accepted,
            "acceptance_rate": round(total_accepted / total_targets, 4),
            "token_count": total_tokens,
            "elapsed_ms": total_elapsed_ms,
            "results": results,
            "boundary": (
                "This benchmark measures strict programmatic location acceptance only. "
                "It does not establish geometric accuracy until a human-labelled reference is compared."
            ),
        }

    def _run_analysis(self, analysis_id: str) -> None:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            return
        live_settings: ProviderSettings | None = None
        try:
            self.db.update_analysis(analysis_id, technical_status="calling_ai", error=None)
            document = self.get_document(analysis["document_id"])
            page_paths = self.intake.all_page_paths(document)
            if analysis["mode"] == "mock":
                provider = self.mock_provider
            else:
                with self._provider_lock:
                    settings = self._analysis_provider_settings.pop(analysis_id, None)
                if settings is None:
                    raise RuntimeError("live provider settings are unavailable after restart")
                live_settings = settings
                provider = create_live_provider(settings)
            result = provider.analyze(document=document, page_paths=page_paths)
            self.db.update_analysis(analysis_id, technical_status="validating_output")
            draft = ReviewDraftV2.model_validate(result.draft)
            self.db.update_analysis(analysis_id, technical_status="applying_rules")
            rules = evaluate_draft(draft, page_count=int(document["page_count"]))
            human_status = "pending" if rules.required_decision_ids else "ready"
            self.db.update_analysis(
                analysis_id,
                technical_status="completed",
                business_status=rules.status,
                human_status=human_status,
                draft_json=json.dumps(draft.model_dump(mode="json"), ensure_ascii=False),
                rules_json=json.dumps(rules.model_dump(mode="json"), ensure_ascii=False),
                provider_metadata_json=json.dumps(result.metadata, ensure_ascii=False),
                model=result.metadata.get("model"),
                error=None,
            )
            self._ensure_independent_feature_output(analysis_id)
            self.db.audit("analysis", analysis_id, "completed", {"business_status": rules.status, "provider": result.metadata.get("provider")})
            if live_settings is not None:
                secondary_status = (
                    (result.metadata.get("secondary_stage") or {}).get("status")
                    if live_settings.provider in CONDITIONAL_HYBRID_PROVIDERS
                    else None
                )
                # A skipped conditional review proves the visual stage, but it
                # does not verify the configured DeepSeek credential or route.
                if live_settings.provider in CONDITIONAL_HYBRID_PROVIDERS:
                    if secondary_status == "failed_safely":
                        self._record_provider_verification(
                            live_settings,
                            "primary_verified",
                            secondary_status="failed",
                        )
                    elif secondary_status == "skipped":
                        self._record_provider_verification(
                            live_settings,
                            "primary_verified",
                            secondary_status="unverified",
                        )
                    else:
                        self._record_provider_verification(
                            live_settings,
                            "verified",
                            secondary_status="verified",
                        )
                else:
                    self._record_provider_verification(live_settings, "verified")
        except Exception as exc:
            print(f"[AI analysis] {analysis_id} failed with {type(exc).__name__}", file=sys.stderr)
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                safe_error = "AI service did not return within seven minutes. The source is intact; retry as a new task."
            elif isinstance(exc, httpx.HTTPStatusError):
                safe_error = "AI service rejected the request. Check the API key, model name, quota, and network, then retry."
            else:
                safe_error = "Analysis failed safely. Check the local server log, then retry."
            self.db.update_analysis(
                analysis_id,
                technical_status="failed",
                human_status="pending",
                error=safe_error,
            )
            self.db.audit("analysis", analysis_id, "failed", {"error_type": type(exc).__name__})
            if live_settings is not None:
                self._record_provider_verification(live_settings, "failed")

    @staticmethod
    def _loads(value: str | None) -> dict | None:
        return json.loads(value) if value else None

    def analysis_payload(self, analysis_id: str, *, include_detail: bool = True) -> dict:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        document = self.get_document(analysis["document_id"])
        payload = {
            "id": analysis["id"],
            "feature": analysis.get("feature") or "review",
            "document": self.public_document(document),
            "mode": analysis["mode"],
            "provider": analysis["provider"],
            "model": analysis["model"],
            "technical_status": analysis["technical_status"],
            "business_status": analysis["business_status"],
            "human_status": analysis["human_status"],
            "external_processing_consent": bool(analysis["external_processing_consent"]),
            "error": analysis["error"],
            "created_at": analysis["created_at"],
            "updated_at": analysis["updated_at"],
            "finalized_at": analysis["finalized_at"],
            "live_api": analysis["mode"] != "mock",
        }
        if include_detail:
            decisions = [
                {**item, "correction": item.get("corrected_value")}
                for item in self.db.list_decisions(analysis_id)
            ]
            payload.update(
                {
                    "draft": self._loads(analysis["draft_json"]),
                    "rules": self._loads(analysis["rules_json"]),
                    "provider_metadata": self._loads(analysis["provider_metadata_json"]),
                    "decisions": decisions,
                    "field_corrections": [
                        {
                            **item,
                            "field_name": str(item.get("finding_id") or "").removeprefix(
                                FIELD_CORRECTION_PREFIX
                            ),
                        }
                        for item in decisions
                        if str(item.get("finding_id") or "").startswith(FIELD_CORRECTION_PREFIX)
                    ],
                    "business_artifacts": (
                        self.business_artifacts(analysis_id).model_dump(mode="json")
                        if analysis["human_status"] == "finalized"
                        else None
                    ),
                }
            )
        return payload

    def _ai_drawing_facts(self, analysis_id: str) -> DrawingFactsV1:
        """Build source-labelled facts without creating a cross-feature gate."""

        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("complete the AI extraction before generating a result", 409)
        draft = ReviewDraftV2.model_validate(self._loads(analysis["draft_json"]) or {})
        rules = RuleReport.model_validate(self._loads(analysis["rules_json"]) or {})
        return build_drawing_facts(
            analysis_id=analysis_id,
            business_status=analysis["business_status"],
            draft=draft,
            rules=rules,
            decisions=[],
            source_status="ai_extracted",
        )

    @staticmethod
    def _profile_process_request(profile: ClassroomReferenceProfile) -> ProcessPlanRequest:
        return ProcessPlanRequest(
            manufacturing_family=profile.manufacturing_family,
            quantity=profile.quantity,
            material_form=profile.material_form,
            equipment_capability=profile.equipment_capability,
            inspection_capability=profile.inspection_capability,
            special_requirements=profile.special_requirements,
        )

    def _ensure_independent_feature_output(self, analysis_id: str) -> None:
        """Create the selected product result once, with no dependency on another feature."""

        analysis = self.db.get_analysis(analysis_id)
        if not analysis or analysis["technical_status"] != "completed":
            return
        feature = str(analysis.get("feature") or "review")
        if feature == "review":
            return
        stored = self.db.get_business_artifacts(analysis_id)
        if feature == "process" and stored and stored.get("process_plan_json"):
            return
        if feature == "quote" and stored and stored.get("prequote_json"):
            return

        facts = self._ai_drawing_facts(analysis_id)
        profile = build_classroom_reference_profile(facts)
        assessment = assess_feature_inputs(feature=feature, facts=facts, profile=profile)
        if assessment.result_status == "insufficient_input":
            # Technical extraction may complete successfully while the selected
            # business result remains unsafe to generate. Do not persist a
            # generic route or a numeric quote in that case.
            return
        process_plan = build_process_plan(
            analysis_id=analysis_id,
            facts=facts,
            request=self._profile_process_request(profile),
        )
        if feature == "process":
            self.db.save_process_plan(
                analysis_id,
                facts_json=json.dumps(facts.model_dump(mode="json"), ensure_ascii=False),
                process_plan_json=json.dumps(process_plan.model_dump(mode="json"), ensure_ascii=False),
            )
            return

        if feature == "quote":
            prequote = build_prequote(
                analysis_id=analysis_id,
                process_plan=process_plan,
                request=profile.quote_inputs,
                input_source="ai_public_reference",
            )
            self.db.save_prequote(
                analysis_id,
                facts_json=json.dumps(facts.model_dump(mode="json"), ensure_ascii=False),
                process_plan_json=json.dumps(process_plan.model_dump(mode="json"), ensure_ascii=False),
                prequote_json=json.dumps(prequote.model_dump(mode="json"), ensure_ascii=False),
            )

    def feature_output(self, analysis_id: str, *, analysis: dict | None = None) -> dict | None:
        analysis = analysis or self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            return None
        feature = str(analysis.get("feature") or "review")
        document = self.public_document(self.get_document(analysis["document_id"]))
        draft = ReviewDraftV2.model_validate(self._loads(analysis["draft_json"]) or {})
        rules = RuleReport.model_validate(self._loads(analysis["rules_json"]) or {})
        provider_metadata = self._loads(analysis.get("provider_metadata_json")) or {}
        secondary_status = (provider_metadata.get("secondary_stage") or {}).get("status")
        degraded = bool(provider_metadata.get("degraded")) or secondary_status == "failed_safely"
        degradation_notice = (
            provider_metadata.get("degradation_notice")
            if degraded
            else None
        )
        common = {
            "feature": feature,
            "document": document,
            "facts": [item.model_dump(mode="json") for item in draft.fields],
            "sources": [],
            "assumptions": [],
            "boundary": "AI 结果仅供课堂演示和工程参考，不构成正式批准、投产工艺或客户报价。",
            "report_url": f"/api/v1/features/runs/{analysis_id}/report",
            "report_available": True,
            "degraded": degraded,
            "degradation_notice": degradation_notice,
        }
        if feature == "review":
            review = build_engineering_review(
                draft=draft,
                rules=rules,
                decisions=[],
                report_stage="draft",
            )
            return {
                **common,
                "kind": "review_report",
                "summary": draft.summary,
                "requirements": [item.model_dump(mode="json") for item in draft.engineering_requirements],
                "findings": [item.model_dump(mode="json") for item in draft.findings],
                "evidence": [item.model_dump(mode="json") for item in draft.evidence],
                "review": review.model_dump(mode="json"),
            }

        facts = self._ai_drawing_facts(analysis_id)
        profile = build_classroom_reference_profile(facts)
        assessment = assess_feature_inputs(feature=feature, facts=facts, profile=profile)
        warnings = list(assessment.warnings)
        if degradation_notice:
            warnings.insert(0, degradation_notice)
        result_contract = {
            "result_status": assessment.result_status,
            "result_message": assessment.summary,
            "missing_inputs": assessment.missing_inputs,
            "warnings": list(dict.fromkeys(warnings)),
        }
        if assessment.result_status == "insufficient_input":
            # Keep extraction facts visible, but do not expose the low-confidence
            # fallback profile because that would look like a real family match.
            unavailable = {
                **common,
                **result_contract,
                "summary": assessment.summary,
                "report_url": None,
                "report_available": False,
            }
            if feature == "process":
                return {
                    **unavailable,
                    "kind": "process_plan",
                    "process_plan": None,
                }
            return {
                **unavailable,
                "kind": "quote_estimate",
                "prequote": None,
            }

        self._ensure_independent_feature_output(analysis_id)
        stored = self.db.get_business_artifacts(analysis_id)
        if not stored:
            raise ServiceError("feature output was not generated", 500)
        plan_payload = self._loads(stored.get("process_plan_json"))
        process_plan = self._parse_process_plan(plan_payload or {}) if plan_payload else None
        profile_payload = profile.model_dump(mode="json")
        common.update(
            {
                **result_contract,
                "sources": profile_payload["sources"],
                "assumptions": profile_payload["assumptions"],
                "reference_profile": profile_payload,
                "boundary": profile.boundary,
            }
        )
        if feature == "process":
            return {
                **common,
                "kind": "process_plan",
                "summary": process_plan.route_summary if process_plan else "AI 工艺路线已生成",
                "process_plan": process_plan.model_dump(mode="json") if process_plan else None,
            }

        prequote_payload = self._loads(stored.get("prequote_json"))
        prequote = PreQuoteV1.model_validate(prequote_payload or {})
        return {
            **common,
            "kind": "quote_estimate",
            "summary": f"单件参考报价 ¥{prequote.unit_prequote:.2f}",
            "prequote": prequote.model_dump(mode="json"),
        }

    def feature_run_payload(self, analysis_id: str, *, include_output: bool = True) -> dict:
        payload = self.analysis_payload(analysis_id, include_detail=False)
        analysis = self.db.get_analysis(analysis_id)
        metadata = self._loads(analysis.get("provider_metadata_json")) if analysis else None
        metadata = metadata or {}
        secondary_status = (metadata.get("secondary_stage") or {}).get("status")
        degraded = bool(metadata.get("degraded")) or secondary_status == "failed_safely"
        output = self.feature_output(analysis_id) if include_output else None
        business_status = payload["business_status"]
        if payload["feature"] != "review" and output:
            business_status = {
                "ready": "pass",
                "assumptions_only": "needs_review",
                "insufficient_input": "blocked",
            }.get(output.get("result_status"), business_status)
        return {
            "id": payload["id"],
            "feature": payload["feature"],
            "status": payload["technical_status"],
            "business_status": business_status,
            "human_status": payload["human_status"],
            "provider": payload["provider"],
            "model": payload["model"],
            "provider_execution": self._provider_execution_summary(
                metadata,
                provider=payload["provider"],
                model=payload["model"],
            ),
            "document": payload["document"],
            "error": payload["error"],
            "created_at": payload["created_at"],
            "updated_at": payload["updated_at"],
            "degraded": degraded,
            "degradation_notice": metadata.get("degradation_notice") if degraded else None,
            "output": output,
        }

    @classmethod
    def _provider_execution_summary(
        cls,
        metadata: dict,
        *,
        provider: str,
        model: str | None,
    ) -> dict:
        """Expose only learner-safe execution facts, never raw provider data."""

        visual = metadata.get("visual_stage") or {}
        localization = visual.get("localization_stage") or {}
        secondary = metadata.get("secondary_stage") or {}
        visual_provider = str(visual.get("provider") or cls._primary_provider_family(provider))
        visual_model = str(visual.get("model") or model or "")
        secondary_status = str(secondary.get("status") or "") or None

        def token_total(stage: dict) -> int:
            usage = stage.get("usage") or {}
            for key in ("totalTokenCount", "total_tokens", "totalTokens"):
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    return int(value)
            return 0

        localization_tokens = sum(
            token_total(call)
            for call in localization.get("calls") or []
            if isinstance(call, dict)
        )
        total_tokens = token_total(visual) + localization_tokens + token_total(secondary)
        return {
            "visual_provider": visual_provider,
            "visual_model": visual_model,
            "visual_status": "completed" if metadata else None,
            "secondary_provider": str(secondary.get("provider") or "deepseek") if secondary_status else None,
            "secondary_model": secondary.get("model"),
            "secondary_status": secondary_status,
            "routing": metadata.get("routing"),
            "total_token_count": total_tokens or None,
            "localization_target_count": localization.get("target_count"),
            "localization_accepted_count": localization.get("accepted_count"),
        }

    def feature_history(self, limit: int = 100) -> list[dict]:
        return [
            self.feature_run_payload(item["id"], include_output=False)
            for item in self.db.list_analyses(limit)
        ]

    def create_feature_report(self, analysis_id: str) -> tuple[Path, str]:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("feature run is not complete", 409)
        feature = str(analysis.get("feature") or "review")
        output = self.feature_output(analysis_id, analysis=analysis)
        if output is None:
            raise ServiceError("feature output is unavailable", 409)
        if not output.get("report_available", True):
            raise ServiceError(
                str(output.get("result_message") or "feature report is unavailable because critical input is missing"),
                409,
            )
        export_id = f"export-{uuid4().hex}"
        directory = (self.exports_root / analysis_id).resolve()
        if self.exports_root not in directory.parents:
            raise ServiceError("export path escaped its private root", 500)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{export_id}.pdf"
        if feature == "review":
            payload = self.draft_report_payload(analysis_id)
            payload["product_report_type"] = "ai_review"
            document = self.get_document(payload["document"]["id"])
            build_review_report_pdf(
                payload,
                path,
                page_paths=self.intake.all_page_paths(document),
            )
        elif feature == "process":
            business_status = {
                "ready": "pass",
                "assumptions_only": "needs_review",
                "insufficient_input": "blocked",
            }.get(output.get("result_status"), analysis.get("business_status"))
            build_process_plan_pdf(
                {
                    **output,
                    "document": output["document"],
                    "analysis": {
                        "id": analysis_id,
                        "business_status": business_status,
                    },
                    "business_artifacts": {"process_plan": output["process_plan"]},
                },
                path,
            )
        else:
            build_quote_report_pdf(output, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.db.create_export(
            {
                "id": export_id,
                "analysis_id": analysis_id,
                "format": f"{feature}-report",
                "private_path": str(path),
                "sha256": digest,
            }
        )
        return path, feature

    def _finalized_drawing_facts(self, analysis_id: str) -> DrawingFactsV1:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("complete the drawing analysis before downstream work", 409)
        if analysis["human_status"] != "finalized":
            raise ServiceError("finalize the human drawing review before downstream work", 409)
        draft = ReviewDraftV2.model_validate(self._loads(analysis["draft_json"]) or {})
        rules = RuleReport.model_validate(self._loads(analysis["rules_json"]) or {})
        decisions = self.db.list_decisions(analysis_id)
        return build_drawing_facts(
            analysis_id=analysis_id,
            business_status=analysis["business_status"],
            draft=draft,
            rules=rules,
            decisions=decisions,
        )

    @staticmethod
    def _parse_process_plan(payload: dict) -> ProcessPlanDraft:
        if payload.get("schema_version") == "2.0":
            return ProcessPlanDraftV2.model_validate(payload)
        return ProcessPlanDraftV1.model_validate(payload)

    def business_artifacts(self, analysis_id: str) -> BusinessArtifactsV1:
        facts = self._finalized_drawing_facts(analysis_id)
        stored = self.db.get_business_artifacts(analysis_id)
        process_plan = None
        prequote = None
        if stored:
            process_plan_payload = self._loads(stored.get("process_plan_json"))
            prequote_payload = self._loads(stored.get("prequote_json"))
            if process_plan_payload:
                process_plan = self._parse_process_plan(process_plan_payload)
            if prequote_payload and process_plan and process_plan.review_status == "confirmed":
                prequote = PreQuoteV1.model_validate(prequote_payload)
        return build_artifacts(facts=facts, process_plan=process_plan, prequote=prequote)

    def classroom_reference_profile(self, analysis_id: str) -> ClassroomReferenceProfile:
        facts = self._finalized_drawing_facts(analysis_id)
        return build_classroom_reference_profile(facts)

    def create_process_plan(self, analysis_id: str, request: ProcessPlanRequest) -> BusinessArtifactsV1:
        facts = self._finalized_drawing_facts(analysis_id)
        process_plan = build_process_plan(analysis_id=analysis_id, facts=facts, request=request)
        self.db.save_process_plan(
            analysis_id,
            facts_json=json.dumps(facts.model_dump(mode="json"), ensure_ascii=False),
            process_plan_json=json.dumps(process_plan.model_dump(mode="json"), ensure_ascii=False),
        )
        return build_artifacts(facts=facts, process_plan=process_plan)

    def create_prequote(self, analysis_id: str, request: PreQuoteRequest) -> BusinessArtifactsV1:
        facts = self._finalized_drawing_facts(analysis_id)
        stored = self.db.get_business_artifacts(analysis_id)
        if not stored or not stored.get("process_plan_json"):
            raise ServiceError("create and confirm a process-plan draft before calculating a prequote", 409)
        process_plan = self._parse_process_plan(self._loads(stored["process_plan_json"]) or {})
        if process_plan.review_status != "confirmed":
            raise ServiceError("confirm the process-plan draft before calculating a prequote", 409)
        prequote = build_prequote(
            analysis_id=analysis_id,
            process_plan=process_plan,
            request=request,
        )
        self.db.save_prequote(
            analysis_id,
            facts_json=json.dumps(facts.model_dump(mode="json"), ensure_ascii=False),
            process_plan_json=json.dumps(process_plan.model_dump(mode="json"), ensure_ascii=False),
            prequote_json=json.dumps(prequote.model_dump(mode="json"), ensure_ascii=False),
        )
        return build_artifacts(facts=facts, process_plan=process_plan, prequote=prequote)

    def confirm_process_plan(
        self,
        analysis_id: str,
        request: ProcessPlanConfirmationRequest,
    ) -> BusinessArtifactsV1:
        facts = self._finalized_drawing_facts(analysis_id)
        stored = self.db.get_business_artifacts(analysis_id)
        if not stored or not stored.get("process_plan_json"):
            raise ServiceError("create a process-plan draft before confirming it", 409)
        process_plan = self._parse_process_plan(self._loads(stored["process_plan_json"]) or {})
        if process_plan.review_status == "confirmed":
            return self.business_artifacts(analysis_id)
        update = {
            "review_status": "confirmed",
            "reviewed_by": request.reviewer,
            "reviewer_role": request.reviewer_role,
            "reviewed_at": utc_now(),
            "review_note": request.note,
        }
        if isinstance(process_plan, ProcessPlanDraftV2):
            update["review_checklist"] = [
                "已核对工序顺序、输入/输出状态和前后关系",
                "已核对设备、工装、基准和现场能力",
                "已核对关键特性、检验方法和待确认参数",
            ]
        confirmed = type(process_plan).model_validate(process_plan.model_copy(update=update))
        self.db.save_process_plan_review(
            analysis_id,
            process_plan_json=json.dumps(confirmed.model_dump(mode="json"), ensure_ascii=False),
            reviewer=request.reviewer,
            reviewer_role=request.reviewer_role,
        )
        return build_artifacts(facts=facts, process_plan=confirmed)

    def list_analyses(self, limit: int = 100) -> list[dict]:
        return [self.analysis_payload(item["id"], include_detail=False) for item in self.db.list_analyses(limit)]

    def retry(self, analysis_id: str) -> dict:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "failed":
            raise ServiceError("only failed analyses can be retried", 409)
        retried = self.create_analysis(
            AnalysisRequest(
                document_id=analysis["document_id"],
                feature=analysis.get("feature") or "review",
                mode=analysis["mode"],
                external_processing_consent=bool(analysis["external_processing_consent"]),
            )
        )
        self.db.audit("analysis", retried["id"], "retried", {"retry_of": analysis_id})
        return retried

    def decide(self, analysis_id: str, finding_id: str, request: DecisionRequest) -> dict:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("analysis is not ready for human decisions", 409)
        if analysis["human_status"] == "finalized":
            raise ServiceError("finalized human decisions are immutable", 409)
        rules = RuleReport.model_validate(self._loads(analysis["rules_json"]) or {})
        known_ids = {issue.id for issue in rules.issues}
        if finding_id not in known_ids:
            raise ServiceError("finding is not part of this rule report", 404)
        self.db.upsert_decision(analysis_id, finding_id, request.model_dump())
        decided_ids = {item["finding_id"] for item in self.db.list_decisions(analysis_id)}
        human_status = "ready" if set(rules.required_decision_ids) <= decided_ids else "pending"
        self.db.update_analysis(analysis_id, human_status=human_status)
        return self.analysis_payload(analysis_id)

    def correct_field(
        self,
        analysis_id: str,
        field_name: str,
        request: FieldCorrectionRequest,
    ) -> dict:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("analysis is not ready for human field corrections", 409)
        if analysis["human_status"] == "finalized":
            raise ServiceError("finalized human field corrections are immutable", 409)
        draft = ReviewDraftV2.model_validate(self._loads(analysis["draft_json"]) or {})
        known_fields = {item.name for item in draft.fields}
        if field_name not in known_fields:
            raise ServiceError("field is not part of this review draft", 404)
        self.db.upsert_decision(
            analysis_id,
            f"{FIELD_CORRECTION_PREFIX}{field_name}",
            {
                "decision": "corrected",
                "corrected_value": request.corrected_value,
                "reviewer": request.reviewer,
                "note": request.note,
            },
        )
        return self.analysis_payload(analysis_id)

    def finalize(self, analysis_id: str, request: FinalizeRequest) -> dict:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("analysis has not completed successfully", 409)
        if analysis["human_status"] == "finalized":
            raise ServiceError("analysis is already finalized", 409)
        rules = RuleReport.model_validate(self._loads(analysis["rules_json"]) or {})
        decided_ids = {item["finding_id"] for item in self.db.list_decisions(analysis_id)}
        missing = sorted(set(rules.required_decision_ids) - decided_ids)
        if missing:
            raise ServiceError("human decisions are still required: " + ", ".join(missing), 409)
        self.db.update_analysis(
            analysis_id,
            human_status="finalized",
            finalized_at=utc_now(),
        )
        self.db.audit(
            "analysis",
            analysis_id,
            "finalized",
            {"reviewer": request.reviewer, "reviewer_role": request.reviewer_role, "note": request.note},
        )
        return self.analysis_payload(analysis_id)

    def locate_evidence(
        self,
        analysis_id: str,
        evidence_id: str,
        request: EvidenceLocationRequest,
    ) -> dict:
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            raise ServiceError("analysis not found", 404)
        if analysis["technical_status"] != "completed":
            raise ServiceError("analysis has not completed successfully", 409)
        if analysis["human_status"] == "finalized":
            raise ServiceError("finalized evidence locations are immutable; create a new review", 409)
        raw_draft = self._loads(analysis.get("draft_json")) or {}
        evidence_items = raw_draft.get("evidence") or []
        target = next((item for item in evidence_items if item.get("id") == evidence_id), None)
        if target is None:
            raise ServiceError("evidence not found", 404)
        target["bbox"] = request.bbox
        draft = ReviewDraftV2.model_validate(raw_draft)
        self.db.update_analysis(
            analysis_id,
            draft_json=json.dumps(draft.model_dump(mode="json"), ensure_ascii=False),
        )
        self.db.audit(
            "analysis",
            analysis_id,
            "evidence_location_updated",
            {
                "evidence_id": evidence_id,
                "page": target.get("page"),
                "bbox": request.bbox,
                "reviewer": request.reviewer,
                "note": request.note,
            },
        )
        return self.analysis_payload(analysis_id)

    def _report_payload(self, analysis_id: str, *, report_stage: str) -> dict:
        payload = self.analysis_payload(analysis_id)
        if payload["technical_status"] != "completed":
            raise ServiceError("analysis is not complete", 409)
        if report_stage == "final" and payload["human_status"] != "finalized":
            raise ServiceError("finalize human decisions before exporting", 409)
        finalization_audit = self.db.get_latest_audit("analysis", analysis_id, "finalized") or {}
        finalization_details = finalization_audit.get("details") or {}
        draft = ReviewDraftV2.model_validate(payload["draft"] or {})
        rules = RuleReport.model_validate(payload["rules"] or {})
        effective_draft = build_effective_review_draft(
            draft=draft,
            rules=rules,
            decisions=payload.get("decisions") or [],
        )
        engineering_review = build_engineering_review(
            draft=effective_draft,
            rules=rules,
            decisions=payload.get("decisions") or [],
            report_stage=report_stage,
        )
        return {
            "contract_version": "3.0",
            "report_stage": report_stage,
            "generated_at": utc_now(),
            "document": {
                key: payload["document"][key]
                for key in ("id", "filename", "sha256", "size_bytes", "page_count")
            },
            "analysis": {
                key: payload[key]
                for key in (
                    "id", "mode", "provider", "model", "technical_status",
                    "business_status", "human_status", "created_at", "finalized_at",
                )
            },
            "provider_metadata": payload["provider_metadata"],
            "draft": payload["draft"],
            "field_corrections": payload.get("field_corrections") or [],
            "rules": payload["rules"],
            "decisions": payload["decisions"],
            "engineering_review": engineering_review.model_dump(mode="json"),
            "review_finalization": {
                "reviewer": finalization_details.get("reviewer"),
                "reviewer_role": finalization_details.get("reviewer_role"),
                "note": finalization_details.get("note"),
                "recorded_at": finalization_audit.get("created_at"),
            },
            "business_artifacts": payload.get("business_artifacts"),
            "boundary": (
                "本报告是保留原图哈希的标注副本，已记录人工逐项决定，"
                "但仍不自动构成工程放行、投产批准或客户验收。"
                if report_stage == "final"
                else "本报告是保留原图哈希的标注副本，由AI与规则生成工程审核草案；"
                "尚未经授权工程师逐项确认，不得用于工程流转。"
            ),
        }

    def export_payload(self, analysis_id: str) -> dict:
        return self._report_payload(analysis_id, report_stage="final")

    def draft_report_payload(self, analysis_id: str) -> dict:
        return self._report_payload(analysis_id, report_stage="draft")

    @staticmethod
    def _html_report(payload: dict) -> str:
        esc = lambda value: html.escape(str(value))
        engineering_review = payload["engineering_review"]
        issues = engineering_review["issues"]
        rows = []
        for issue in issues:
            rows.append(
                "<tr>"
                f"<td>{esc(issue['code'])}</td><td>{esc(issue.get('category', ''))}</td>"
                f"<td>{esc(issue['severity'])}</td><td>{esc(issue['problem'])}</td>"
                f"<td>{esc(issue['impact'])}</td><td>{esc(issue['recommendation'])}</td>"
                f"<td>{esc(issue.get('human_decision', 'pending'))}</td>"
                "</tr>"
            )
        requirements_html = "".join(
            "<tr>"
            f"<td>{esc(item['category'])}</td><td>{esc(item['criticality'])}</td>"
            f"<td>{esc(item['requirement'])}</td><td>{esc(', '.join(item.get('evidence_ids') or []))}</td>"
            "</tr>"
            for item in engineering_review["requirements"]
        )
        coverage_html = "".join(
            f"<tr><td>{esc(item['area'])}</td><td>{esc(item['status'])}</td><td>{esc(item['conclusion'])}</td></tr>"
            for item in engineering_review["coverage"]
        )
        actions_html = "".join(
            f"<tr><td>{esc(item['priority'])}</td><td>{esc(item['action'])}</td><td>{esc(item['owner_role'])}</td></tr>"
            for item in engineering_review["actions"]
        )
        raw_fields = {
            str(item.get("name")): item.get("value") or "未识别"
            for item in (payload.get("draft") or {}).get("fields") or []
        }
        field_corrections = payload.get("field_corrections") or []
        corrections_html = ""
        if field_corrections:
            corrections_html = (
                "<h2>人工字段修正记录</h2>"
                "<table><thead><tr><th>字段</th><th>AI 原值</th><th>人工值</th><th>复核人</th><th>备注</th></tr></thead><tbody>"
                + "".join(
                    "<tr>"
                    f"<td>{esc(item.get('field_name', ''))}</td>"
                    f"<td>{esc(raw_fields.get(str(item.get('field_name')), '未识别'))}</td>"
                    f"<td>{esc(item.get('corrected_value', ''))}</td>"
                    f"<td>{esc(item.get('reviewer', ''))}</td>"
                    f"<td>{esc(item.get('note', ''))}</td>"
                    "</tr>"
                    for item in field_corrections
                )
                + "</tbody></table>"
            )
        workflow_html = ""
        artifacts = payload.get("business_artifacts") or {}
        facts = (artifacts.get("drawing_facts") or {}).get("facts") or []
        if facts:
            workflow_html += "<h2>已确认图纸事实</h2><table><thead><tr><th>字段</th><th>值</th><th>来源</th></tr></thead><tbody>"
            workflow_html += "".join(
                f"<tr><td>{esc(item['name'])}</td><td>{esc(item.get('value', ''))}</td><td>{esc(item.get('source', ''))}</td></tr>"
                for item in facts
            )
            workflow_html += "</tbody></table>"
        process_plan = artifacts.get("process_plan")
        if process_plan:
            process_review_status = (
                "路线内容已核对"
                if process_plan.get("review_status") == "confirmed"
                else "等待工艺负责人核对"
            )
            review_summary = (
                f" · 路线状态：{esc(process_review_status)}"
                f" · 复核人：{esc(process_plan.get('reviewed_by') or '—')}"
            )
            workflow_html += (
                f"<h2>加工工艺路线卡</h2><p>制造类型：{esc(process_plan['manufacturing_family'])} · "
                f"数量：{esc(process_plan['quantity'])} · 状态：草案{review_summary}</p><ol>"
            )
            workflow_html += "".join(
                f"<li><strong>{esc(step['operation'])}</strong> — {esc(step['purpose'])}"
                f"<br><small>{esc(step.get('resource') or step.get('equipment_capability') or '待现场确认')}</small>"
                + (
                    f"<br><small>输入：{esc(step.get('input_state'))} · 输出：{esc(step.get('output_state'))}</small>"
                    if step.get("input_state") else ""
                )
                + "</li>"
                for step in process_plan["steps"]
            )
            workflow_html += "</ol>"
        prequote = artifacts.get("prequote")
        if prequote:
            workflow_html += (
                "<h2>确定性预报价</h2><table><thead><tr><th>成本项</th><th>计算依据</th><th>金额</th></tr></thead><tbody>"
            )
            workflow_html += "".join(
                f"<tr><td>{esc(item['label'])}</td><td>{esc(item['basis'])}</td><td>¥{esc(item['amount'])}</td></tr>"
                for item in prequote["cost_items"]
            )
            workflow_html += (
                f"</tbody></table><p class=\"status\">单件预报价：¥{esc(prequote['unit_prequote'])}</p>"
                f"<p>本批总成本：¥{esc(prequote['total_cost'])} · 目标收入：¥{esc(prequote['target_revenue'])}</p>"
            )
        return """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<title>图纸工程审核报告</title><style>body{font:15px/1.6 system-ui;margin:40px;color:#202923;max-width:1200px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd3ce;padding:8px;text-align:left;vertical-align:top}small{color:#59645e}.status{font-size:22px;font-weight:700}li{margin:8px 0}</style></head><body>""" + (
            f"<h1>图纸工程审核报告</h1><p class=\"status\">{esc(engineering_review['recommended_disposition'])}</p>"
            f"<p>文件：{esc(payload['document']['filename'])}<br>SHA256：{esc(payload['document']['sha256'])}<br>"
            f"Provider：{esc(payload['analysis']['provider'])} · Model：{esc(payload['analysis']['model'])}</p>"
            + corrections_html
            + f"<h2>工程审核结论</h2><p>{esc(engineering_review['conclusion'])}</p>"
            "<h2>审核覆盖度</h2><table><thead><tr><th>维度</th><th>状态</th><th>说明</th></tr></thead><tbody>"
            + coverage_html
            + "</tbody></table>"
            + "<h2>工程要求清单</h2><table><thead><tr><th>类别</th><th>关键度</th><th>要求</th><th>证据</th></tr></thead><tbody>"
            + requirements_html
            + "</tbody></table>"
            + "<h2>工程问题、影响与建议</h2><table><thead><tr><th>编码</th><th>类别</th><th>级别</th><th>问题</th><th>影响</th><th>建议</th><th>人工决定</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            + "<h2>整改行动清单</h2><table><thead><tr><th>优先级</th><th>动作</th><th>责任角色</th></tr></thead><tbody>"
            + actions_html
            + "</tbody></table>"
            + workflow_html
            + f"<p><small>{esc(payload['boundary'])}</small></p></body></html>"
        )

    def create_export(self, analysis_id: str, format_name: str) -> tuple[Path, str, str]:
        if format_name not in {"json", "html", "pdf", "pdf-draft", "process-pdf"}:
            raise ServiceError("format must be json, html, pdf, pdf-draft, or process-pdf", 400)
        payload = (
            self.draft_report_payload(analysis_id)
            if format_name == "pdf-draft"
            else self.export_payload(analysis_id)
        )
        export_id = f"export-{uuid4().hex}"
        directory = (self.exports_root / analysis_id).resolve()
        if self.exports_root not in directory.parents:
            raise ServiceError("export path escaped its private root", 500)
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "pdf" if format_name in {"pdf", "pdf-draft", "process-pdf"} else format_name
        path = directory / f"{export_id}.{suffix}"
        if format_name == "process-pdf":
            process_plan = (payload.get("business_artifacts") or {}).get("process_plan")
            if not process_plan:
                raise ServiceError("create a process-plan draft before exporting it", 409)
            build_process_plan_pdf(payload, path)
            media_type = "application/pdf"
        elif format_name in {"pdf", "pdf-draft"}:
            document = self.get_document(payload["document"]["id"])
            page_paths = self.intake.all_page_paths(document)
            build_review_report_pdf(payload, path, page_paths=page_paths)
            media_type = "application/pdf"
        elif format_name == "json":
            body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            media_type = "application/json"
            path.write_text(body, encoding="utf-8")
        else:
            body = self._html_report(payload)
            media_type = "text/html"
            path.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.db.create_export(
            {
                "id": export_id,
                "analysis_id": analysis_id,
                "format": format_name,
                "private_path": str(path),
                "sha256": digest,
            }
        )
        return path, media_type, export_id
