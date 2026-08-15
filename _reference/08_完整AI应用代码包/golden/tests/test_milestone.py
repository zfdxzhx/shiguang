from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

import server
from backend.app import create_application
from backend.engineering_review import build_engineering_review
from backend.models import (
    DocumentType,
    DraftFinding,
    EngineeringRequirement,
    ManufacturingFamily,
    ProcessPlanRequest,
    ReviewDraftV2,
)
from backend.pdf_report import (
    _analysis_method_for_display,
    _customer_friendly_text,
    _group_page_markers,
)
from backend.providers import (
    GeminiDeepSeekHybridProvider,
    GeminiVisionProvider,
    KimiVisionProvider,
    ProviderResult,
    ProviderSettings,
    _generic_mock,
    provider_status,
)
from backend.rules import evaluate_draft
from backend.reference_profiles import build_classroom_reference_profile
from backend.workflows import (
    assess_feature_inputs,
    build_drawing_facts,
    build_prequote,
    build_process_plan,
)


def minimal_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R >>",
    ]
    stream = b"q 0 0 0 rg 20 20 100 100 re S Q"
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, payload in enumerate(objects, 1):
        offsets.append(len(out)); out.extend(f"{index} 0 obj\n".encode() + payload + b"\nendobj\n")
    xref = len(out); out.extend(f"xref\n0 {len(objects)+1}\n".encode()); out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]: out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)


class MilestoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="course-app-")
        cls.app = create_application(
            server,
            runtime_root=Path(cls.temp.name) / "runtime",
            allow_test_fixtures=True,
        )
        cls.client = TestClient(cls.app); cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None); cls.temp.cleanup()

    def upload(self):
        response = self.client.post("/api/v1/documents", files={"file": ("classroom.pdf", minimal_pdf(), "application/pdf")})
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    def wait(self, analysis_id):
        for _ in range(100):
            payload = self.client.get(f"/api/v1/analyses/{analysis_id}").json()
            if payload["technical_status"] in {"completed", "failed"}: return payload
            time.sleep(.03)
        self.fail("analysis timeout")

    def test_01_contract_and_local_skeleton(self):
        config = self.client.get("/api/v1/config")
        self.assertEqual(200, config.status_code)
        self.assertEqual(3, config.json()["milestone"])
        self.assertNotIn("course_todo", config.json(), config.text)
        self.assertIn("provider", config.json())
        self.assertNotIn("modes", config.json())
        kimi_option = next(item for item in config.json()["provider_options"] if item["id"] == "kimi")
        self.assertEqual("k3", kimi_option["default_model"])
        self.assertIn("Kimi Code", kimi_option["endpoint"])
        hybrid_option = next(item for item in config.json()["provider_options"] if item["id"] == "hybrid")
        self.assertEqual("gemini-3.6-flash", hybrid_option["default_model"])
        kimi_hybrid_option = next(item for item in config.json()["provider_options"] if item["id"] == "kimi-hybrid")
        self.assertEqual("k3", kimi_hybrid_option["default_model"])
        self.assertEqual("deepseek-v4-flash", kimi_hybrid_option["secondary_default_model"])
        self.assertTrue(kimi_hybrid_option["requires_secondary"])
        schema = self.client.get("/api/v1/schemas/review-draft-v2")
        self.assertEqual(200, schema.status_code)
        self.assertIn("document_type", schema.json()["properties"])
        workflow_schema = self.client.get("/api/v1/schemas/business-workflow-v1").json()
        self.assertEqual("2.0", workflow_schema["process_plan"]["properties"]["schema_version"]["const"])
        self.assertIn("engineering_requirements", schema.json()["properties"])
        self.assertNotIn("api_key", json.dumps(config.json()).lower())
        bootstrap = self.client.get("/api/bootstrap")
        self.assertEqual(200, bootstrap.status_code)
        expected_ids = ["A", "B1", "B2", "B3", "C1", "C2", "C3", "C4", "D1", "D2", "D3"]
        self.assertEqual(expected_ids, [item["id"] for item in bootstrap.json()["stages"]])
        self.assertEqual("code-package", bootstrap.json()["workspaces"][0]["id"])
        status = self.client.post("/api/action", json={"workspace_id": "code-package", "action": "status"})
        self.assertEqual(200, status.status_code, status.text)
        self.assertEqual(expected_ids, [item["id"] for item in status.json()["stages"]])

    def test_02_pdf_and_mock_draft(self):
        document = self.upload()
        self.assertEqual(1, document["page_count"])
        self.assertNotIn("private_dir", document)
        page = self.client.get(document["page_urls"][0])
        self.assertTrue(page.content.startswith(b"\x89PNG"))
        created = self.client.post("/api/v1/analyses", json={"document_id": document["id"], "mode": "mock"})
        self.assertEqual(202, created.status_code, created.text)
        analysis = self.wait(created.json()["id"])
        self.assertEqual("completed", analysis["technical_status"])
        self.assertFalse(analysis["live_api"])
        self.assertIsNotNone(analysis["draft"])
        self.assertGreaterEqual(len(analysis["draft"]["engineering_requirements"]), 8)
        self.assertTrue(all(item.get("impact") and item.get("recommendation") for item in analysis["draft"]["findings"]))

    def test_03_multimodal_rules_evidence_and_human_gate(self):
        gemini_payload = _generic_mock({}).model_dump(mode="json")
        gemini_payload["evidence"][0]["bbox"] = [100, 200, 300, 400]
        response = Mock()
        response.headers = {"x-request-id": "safe-request-id"}
        response.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(gemini_payload)}]}}],
            "usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 20},
        }
        response.raise_for_status.return_value = None
        client = Mock(); client.post.return_value = response
        context = Mock(); context.__enter__ = Mock(return_value=client); context.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory(prefix="gemini-contract-") as folder:
            page = Path(folder) / "page-1.png"; page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch.dict(os.environ, {
                "AI_PROVIDER": "gemini", "GEMINI_API_KEY": "secret-never-return",
                "GEMINI_MODEL": "models/gemini-vision-test",
            }, clear=False), patch("backend.providers.httpx.Client", return_value=context):
                result = GeminiVisionProvider().analyze(document={}, page_paths=[page])
                status = provider_status()
        request = client.post.call_args.kwargs
        self.assertEqual(DocumentType.MECHANICAL_DRAWING, result.draft.document_type)
        self.assertEqual([0.2, 0.1, 0.4, 0.3], result.draft.evidence[0].bbox)
        self.assertTrue(client.post.call_args.args[0].endswith("/models/gemini-vision-test:generateContent"))
        self.assertEqual("secret-never-return", request["headers"]["x-goog-api-key"])
        self.assertEqual("image/png", request["json"]["contents"][0]["parts"][-1]["inline_data"]["mime_type"])
        self.assertNotIn("secret-never-return", json.dumps(request["json"]) + json.dumps(status))

        kimi_response = Mock()
        kimi_response.headers = {"x-request-id": "kimi-safe-request-id"}
        kimi_response.json.return_value = {
            "id": "safe-kimi-id",
            "choices": [{"finish_reason": "stop", "message": {"content": _generic_mock({}).model_dump_json()}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        kimi_response.raise_for_status.return_value = None
        kimi_client = Mock(); kimi_client.post.return_value = kimi_response
        kimi_context = Mock(); kimi_context.__enter__ = Mock(return_value=kimi_client); kimi_context.__exit__ = Mock(return_value=False)
        kimi_settings = ProviderSettings(
            provider="kimi", model="k3", api_key="secret-kimi-never-return",
            api_base="https://api.kimi.com/coding/v1", reasoning_effort="high", source="session",
        )
        with tempfile.TemporaryDirectory(prefix="kimi-contract-") as folder:
            page = Path(folder) / "page-1.png"; page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", return_value=kimi_context):
                kimi_result = KimiVisionProvider(kimi_settings).analyze(document={}, page_paths=[page])
                kimi_status = provider_status(kimi_settings)
        kimi_request = kimi_client.post.call_args.kwargs
        self.assertEqual(DocumentType.MECHANICAL_DRAWING, kimi_result.draft.document_type)
        self.assertTrue(kimi_client.post.call_args.args[0].endswith("/coding/v1/chat/completions"))
        self.assertEqual("k3", kimi_request["json"]["model"])
        self.assertEqual("Bearer secret-kimi-never-return", kimi_request["headers"]["Authorization"])
        self.assertEqual("high", kimi_request["json"]["reasoning_effort"])
        self.assertEqual("drawing-review:drawing-review-v2.9-process-rows", kimi_request["json"]["prompt_cache_key"])
        self.assertTrue(kimi_request["json"]["response_format"]["json_schema"]["strict"])
        self.assertEqual("ReviewDraftV2", kimi_request["json"]["response_format"]["json_schema"]["name"])
        self.assertEqual("skipped", kimi_result.metadata["localization_stage"]["status"])
        self.assertNotIn("secret-kimi-never-return", json.dumps(kimi_request["json"]) + json.dumps(kimi_status) + json.dumps(kimi_result.metadata))

        governed = _generic_mock({}).model_copy(deep=True)
        governed.fields[0].value = "减速器总成"
        governed.findings.extend([
            DraftFinding(
                id="page-count-model-false", code="PAGE_COUNT_INCONSISTENCY", field="page_count",
                conclusion="Visible page markers disagree.", confidence=.92,
                requires_human_confirmation=False, evidence_ids=["ev-title"],
            ),
            DraftFinding(
                id="reference-one", code="REFERENCED_DATA_NOT_SUPPLIED", field="dimensional_basis",
                conclusion="CATIA data is referenced but absent.", confidence=.95,
                requires_human_confirmation=True, evidence_ids=["ev-title"],
            ),
            DraftFinding(
                id="reference-two", code="REFERENCED_DATA_NOT_SUPPLIED", field="dimensional_basis",
                conclusion="A separate standard is referenced but absent.", confidence=.93,
                requires_human_confirmation=True, evidence_ids=["ev-body"],
            ),
        ])
        governed_report = evaluate_draft(governed, page_count=1)
        governed_ids = {item.id for item in governed_report.issues}
        self.assertTrue({"page-count-model-false", "reference-one", "reference-two", "rule-document-type-inconsistency"} <= governed_ids)
        self.assertIn("page-count-model-false", governed_report.required_decision_ids)
        self.assertEqual("2.3", governed_report.rule_version)

        document = self.upload()
        with patch.dict(os.environ, {
            "AI_PROVIDER": "gemini", "GEMINI_API_KEY": "", "GEMINI_MODEL": "",
            "OPENAI_API_KEY": "", "OPENAI_MODEL": "",
        }, clear=False):
            no_consent = self.client.post("/api/v1/analyses", json={
                "document_id": document["id"], "mode": "live-training", "external_processing_consent": False,
            })
            self.assertEqual(403, no_consent.status_code)
            not_configured = self.client.post("/api/v1/analyses", json={
                "document_id": document["id"], "mode": "live-training", "external_processing_consent": True,
            })
            self.assertEqual(412, not_configured.status_code)

        analysis = self.wait(self.client.post("/api/v1/analyses", json={"document_id": document["id"], "mode": "mock"}).json()["id"])
        self.assertEqual("needs_review", analysis["business_status"])
        self.assertEqual("pending", analysis["human_status"])
        self.assertTrue(analysis["rules"]["required_decision_ids"])
        blocked = self.client.post(f"/api/v1/reviews/{analysis['id']}/finalize", json={})
        self.assertEqual(422, blocked.status_code)
        for finding_id in analysis["rules"]["required_decision_ids"]:
            decided = self.client.patch(f"/api/v1/reviews/{analysis['id']}/findings/{finding_id}", json={"decision": "confirmed", "note": "checked"})
            self.assertEqual(200, decided.status_code, decided.text)
        no_ack = self.client.post(
            f"/api/v1/reviews/{analysis['id']}/finalize",
            json={"reviewer_name": "Course Tester"},
        )
        self.assertEqual(422, no_ack.status_code)
        finalized = self.client.post(f"/api/v1/reviews/{analysis['id']}/finalize", json={"reviewer_name": "Course Tester", "acknowledgement": True})
        self.assertEqual("finalized", finalized.json()["human_status"])
        immutable = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/findings/{analysis['rules']['required_decision_ids'][0]}",
            json={"decision": "rejected"},
        )
        self.assertEqual(409, immutable.status_code)

    def test_04_retry_history_and_safe_errors(self):
        document = self.upload(); service = self.app.state.review_service; original = service.mock_provider
        class Failing:
            def analyze(self, **_): raise RuntimeError("secret-provider-body")
        service.mock_provider = Failing(); captured = io.StringIO()
        try:
            with redirect_stderr(captured):
                failed = self.wait(self.client.post("/api/v1/analyses", json={"document_id": document["id"], "mode": "mock"}).json()["id"])
        finally: service.mock_provider = original
        self.assertEqual("failed", failed["technical_status"])
        self.assertNotIn("secret-provider-body", failed["error"] + captured.getvalue())
        retried = self.client.post(f"/api/v1/analyses/{failed['id']}/retry")
        self.assertEqual(202, retried.status_code, retried.text)
        history = self.client.get("/api/v1/analyses").json()["analyses"]
        self.assertTrue(any(item["id"] == retried.json()["id"] for item in history))
        self.assertNotIn("private_dir", json.dumps(history))

    def test_05_export_ui_and_complete_e2e(self):
        ui_source = Path("frontend/app/drawing-review-app.tsx").read_text(encoding="utf-8")
        report_source = Path("backend/pdf_report.py").read_text(encoding="utf-8")
        self.assertNotIn("TODO_M3_EXPORT_UI", ui_source, "TODO_M3_EXPORT_UI remains in the frontend source")
        for section in ("一、审核结论", "二、原图问题定位", "三、问题与处理建议", "四、负责人确认"):
            self.assertIn(section, report_source)
        friendly = _customer_friendly_text(
            "未发现程序性阻断。发现 2 项阻断问题和 3 项复核问题，可能阻断版本控制；"
            "需要人工核对受控图纸集，再进入后续工程流转。"
            "H7孔的孔位度、平面度和检验方法需要人工复核，并补充证据链。"
        )
        for internal_term in ("阻断", "H7孔", "孔位度", "平面度", "检验方法", "人工复核", "证据链"):
            self.assertNotIn(internal_term, friendly)
        for customer_term in (
            "必须立即停止处理的问题",
            "H7 精密配合孔",
            "孔的位置精度",
            "表面平整度",
            "检查方法",
            "负责人确认",
            "判断依据",
            "2 项必须先解决的问题",
            "3 项需要确认的问题",
            "负责人核对同一套正式图纸",
            "进入后续工程流程",
        ):
            self.assertIn(customer_term, friendly)
        self.assertNotIn("后续后续", friendly)
        self.assertEqual("是否为正式版", _customer_friendly_text("是否为正式受控版"))
        customer_copy = _customer_friendly_text(
            "必填字段 revision 缺失，由设计/文控工程师核对3D数模；"
            "当前规则包与文档类型不匹配，存在可制造性风险。"
        )
        for customer_term in (
            "图纸版本信息缺失",
            "设计/文件管理负责人",
            "3D 模型",
            "检查规则不适用于这类文件",
            "可能难以制造的风险",
        ):
            self.assertIn(customer_term, customer_copy)
        self.assertIn("story.append(KeepTogether([", report_source)
        self.assertEqual(
            "Gemini 3.6 Flash 看图 + DeepSeek V4 Flash 文字复核",
            _analysis_method_for_display({
                "provider": "hybrid",
                "model": "gemini-3.6-flash → deepseek-v4-flash",
            }),
        )
        self.assertEqual(
            "Kimi K3 high 看图 + DeepSeek V4 Flash 文字复核",
            _analysis_method_for_display({
                "provider": "kimi-hybrid",
                "model": "k3 → deepseek-v4-flash",
            }),
        )
        self.assertEqual(
            "Kimi K3 high 看图 + DeepSeek V4 Flash 强制复核",
            _analysis_method_for_display(
                {"provider": "kimi-hybrid", "model": "k3 → deepseek-v4-flash"},
                {
                    "visual_stage": {"model": "k3"},
                    "secondary_stage": {"status": "completed", "model": "deepseek-v4-flash"},
                    "secondary_review": {"mode": "always"},
                },
            ),
        )
        self.assertEqual(
            "Gemini 看图分析（gemini-3.6-flash）；DeepSeek 按需复核未触发",
            _analysis_method_for_display(
                {"provider": "hybrid", "model": "gemini-3.6-flash"},
                {
                    "visual_stage": {"model": "gemini-3.6-flash"},
                    "secondary_stage": {"status": "skipped", "model": "deepseek-v4-flash"},
                },
            ),
        )
        self.assertEqual(
            "Gemini 3.6 Flash 看图 + DeepSeek V4 Flash 按需复核",
            _analysis_method_for_display(
                {"provider": "hybrid", "model": "gemini-3.6-flash → deepseek-v4-flash"},
                {
                    "visual_stage": {"model": "gemini-3.6-flash"},
                    "secondary_stage": {"status": "completed", "model": "deepseek-v4-flash"},
                },
            ),
        )
        self.assertEqual(
            "Gemini 3.6 Flash 看图 + DeepSeek V4 Flash 强制复核",
            _analysis_method_for_display(
                {"provider": "hybrid", "model": "gemini-3.6-flash → deepseek-v4-flash"},
                {
                    "visual_stage": {"model": "gemini-3.6-flash"},
                    "secondary_stage": {"status": "completed", "model": "deepseek-v4-flash"},
                    "secondary_review": {"mode": "always"},
                },
            ),
        )
        self.assertEqual(
            "Gemini 看图分析（gemini-3.6-flash）；DeepSeek 本次不复核",
            _analysis_method_for_display(
                {"provider": "hybrid", "model": "gemini-3.6-flash"},
                {
                    "visual_stage": {"model": "gemini-3.6-flash"},
                    "secondary_stage": {"status": "skipped", "model": "deepseek-v4-flash"},
                    "secondary_review": {"mode": "never"},
                },
            ),
        )
        self.assertEqual(
            "教学模拟（未调用真实 AI）",
            _analysis_method_for_display({"provider": "mock", "model": "deterministic-classroom-fixture"}),
        )
        for removed_section in ("审核范围与覆盖度", "图纸身份与基础字段", "工程要求清单", "证据索引", "人工责任与使用边界"):
            self.assertNotIn(f'_paragraph("{removed_section}', report_source)
        built_scripts = "".join(path.read_text(encoding="utf-8") for path in Path("frontend/dist-static").rglob("*.js"))
        self.assertNotIn("TODO_M3_EXPORT_UI", built_scripts, "TODO_M3_EXPORT_UI remains in the built frontend")
        document = self.upload()
        analysis = self.wait(self.client.post("/api/v1/analyses", json={"document_id": document["id"], "mode": "mock"}).json()["id"])
        invalid_location = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/evidence/ev-title/location",
            json={"bbox": [0.5, 0.5, 0.4, 0.6]},
        )
        self.assertEqual(422, invalid_location.status_code)
        located = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/evidence/ev-title/location",
            json={"bbox": [0.10, 0.12, 0.34, 0.28], "reviewer": "Course Tester"},
        )
        self.assertEqual(200, located.status_code, located.text)
        located_evidence = next(item for item in located.json()["draft"]["evidence"] if item["id"] == "ev-title")
        self.assertEqual([0.1, 0.12, 0.34, 0.28], located_evidence["bbox"])
        draft_pdf = self.client.get(f"/api/v1/analyses/{analysis['id']}/export?format=pdf-draft")
        self.assertEqual(200, draft_pdf.status_code, draft_pdf.text)
        self.assertEqual("application/pdf", draft_pdf.headers["content-type"])
        self.assertIn("drawing-engineering-review-draft", draft_pdf.headers["content-disposition"])
        self.assertTrue(draft_pdf.content.startswith(b"%PDF-"))
        self.assertIn(b"/Subtype /Image", draft_pdf.content)
        for finding_id in analysis["rules"]["required_decision_ids"]:
            self.client.patch(f"/api/v1/reviews/{analysis['id']}/findings/{finding_id}", json={"decision": "confirmed"})
        self.client.post(f"/api/v1/reviews/{analysis['id']}/finalize", json={"reviewer_name": "Course Tester", "acknowledgement": True})
        exported = self.client.get(f"/api/v1/analyses/{analysis['id']}/export?format=json")
        self.assertEqual(200, exported.status_code, exported.text)
        self.assertEqual("3.0", exported.json()["contract_version"])
        self.assertEqual("Course Tester", exported.json()["review_finalization"]["reviewer"])
        self.assertEqual("final", exported.json()["engineering_review"]["report_stage"])
        self.assertGreaterEqual(len(exported.json()["engineering_review"]["requirements"]), 8)
        self.assertGreaterEqual(len(exported.json()["engineering_review"]["issues"]), 3)
        self.assertTrue(all(item["impact"] and item["recommendation"] for item in exported.json()["engineering_review"]["issues"]))
        self.assertNotIn("private_dir", exported.text)
        html_report = self.client.get(f"/api/v1/analyses/{analysis['id']}/export?format=html")
        self.assertEqual(200, html_report.status_code)
        self.assertIn("图纸工程审核报告", html_report.text)
        pdf_report = self.client.get(f"/api/v1/analyses/{analysis['id']}/export?format=pdf")
        self.assertEqual(200, pdf_report.status_code, pdf_report.text)
        self.assertEqual("application/pdf", pdf_report.headers["content-type"])
        self.assertTrue(pdf_report.content.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_report.content), 10_000)
        immutable_location = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/evidence/ev-title/location",
            json={"bbox": [0.2, 0.2, 0.4, 0.4]},
        )
        self.assertEqual(409, immutable_location.status_code)

    def test_03a_customer_review_collapses_overlapping_missing_fields(self):
        baseline = _generic_mock({})
        draft = baseline.model_copy(update={
            "fields": [
                item.model_copy(update={"value": "", "confidence": 0.0, "evidence_ids": []})
                if item.name in {"revision", "material"}
                else item.model_copy(update={"confidence": 0.80})
                if item.name == "part_name"
                else item
                for item in baseline.fields
            ],
            "findings": [
                DraftFinding(
                    id="finding-title-block",
                    code="MISSING_TITLE_BLOCK_METADATA",
                    field="material",
                    conclusion="标题栏未标注材料牌号、图号和受控版本信息。",
                    category="source_integrity",
                    impact="无法确认材料和有效图纸版本。",
                    recommendation="补齐材料、图号与受控版本后重新发布图纸。",
                    confidence=0.95,
                    requires_human_confirmation=True,
                    evidence_ids=["ev-title"],
                )
            ],
        })
        review = build_engineering_review(
            draft=draft,
            rules=evaluate_draft(draft, page_count=1),
            decisions=[],
            report_stage="draft",
        )
        self.assertEqual(2, len(review.issues))
        self.assertEqual(1, review.blocker_count)
        self.assertEqual(1, review.review_count)
        self.assertNotIn("part_name", review.issues[0].problem)
        title_issue = next(item for item in review.issues if item.code == "MISSING_TITLE_BLOCK_METADATA")
        self.assertEqual("blocked", title_issue.severity)
        self.assertEqual("title_block", title_issue.field)
        self.assertNotIn("MISSING_REQUIRED_FIELD", {item.code for item in review.issues})

    def test_06_session_api_configuration_is_memory_only(self):
        secret = "classroom-secret-not-real-value"
        with tempfile.TemporaryDirectory(prefix="course-config-") as folder:
            with patch.dict(os.environ, {
                "AI_PROVIDER": "kimi", "KIMI_API_KEY": "", "KIMI_MODEL": "",
            }, clear=False):
                app = create_application(
                    server,
                    runtime_root=Path(folder) / "runtime",
                    allow_test_fixtures=True,
                )
                with TestClient(app) as client:
                    initial = client.get("/api/v1/ai/status").json()
                    self.assertEqual("not_configured", initial["verification"]["status"])

                    response = client.post("/api/v1/ai/config", json={
                        "provider": "kimi", "model": "k3", "api_key": secret,
                    })
                    self.assertEqual(200, response.status_code, response.text)
                    payload = response.json()
                    self.assertTrue(payload["configured"])
                    self.assertTrue(payload["credential_available"])
                    self.assertEqual("kimi", payload["provider"])
                    self.assertEqual("high", payload["reasoning_effort"])
                    self.assertEqual("session", payload["configuration_source"])
                    self.assertEqual("unverified", payload["verification"]["status"])
                    self.assertIsNone(payload["verification"]["checked_at"])
                    self.assertNotIn(secret, response.text)
                    self.assertNotIn("api_key", json.dumps(payload).lower())

                    reused_primary = client.post("/api/v1/ai/config", json={
                        "provider": "kimi", "model": "k3", "reuse_primary": True,
                    })
                    self.assertEqual(200, reused_primary.status_code, reused_primary.text)
                    self.assertTrue(reused_primary.json()["credential_available"])
                    self.assertNotIn(secret, reused_primary.text)

                    document = client.post(
                        "/api/v1/documents",
                        files={"file": ("classroom.pdf", minimal_pdf(), "application/pdf")},
                    ).json()

                    class SuccessfulLiveProvider:
                        def analyze(self, **_):
                            return ProviderResult(
                                draft=_generic_mock({}),
                                metadata={"provider": "kimi", "model": "k3", "request_id": "safe-test-id"},
                            )

                    with patch("backend.service.create_live_provider", return_value=SuccessfulLiveProvider()):
                        created = client.post("/api/v1/analyses", json={
                            "document_id": document["id"],
                            "mode": "live-training",
                            "external_processing_consent": True,
                        })
                        self.assertEqual(202, created.status_code, created.text)
                        for _ in range(100):
                            analysis = client.get(f"/api/v1/analyses/{created.json()['id']}").json()
                            if analysis["technical_status"] in {"completed", "failed"}:
                                break
                            time.sleep(.03)
                        self.assertEqual("completed", analysis["technical_status"])

                    verified = client.get("/api/v1/ai/status").json()
                    self.assertEqual("verified", verified["verification"]["status"])
                    self.assertEqual("kimi", verified["verification"]["provider"])
                    self.assertEqual("k3", verified["verification"]["model"])
                    self.assertIsNotNone(verified["verification"]["checked_at"])

                    reset = client.post("/api/v1/ai/config", json={
                        "provider": "kimi", "model": "k3", "api_key": secret + "-replacement",
                    }).json()
                    self.assertEqual("unverified", reset["verification"]["status"])
                    self.assertIsNone(reset["verification"]["checked_at"])

                    class FailingLiveProvider:
                        def analyze(self, **_):
                            raise RuntimeError("provider-secret-body")

                    with patch("backend.service.create_live_provider", return_value=FailingLiveProvider()):
                        created = client.post("/api/v1/analyses", json={
                            "document_id": document["id"],
                            "mode": "live-training",
                            "external_processing_consent": True,
                        })
                        for _ in range(100):
                            analysis = client.get(f"/api/v1/analyses/{created.json()['id']}").json()
                            if analysis["technical_status"] in {"completed", "failed"}:
                                break
                            time.sleep(.03)
                        self.assertEqual("failed", analysis["technical_status"])
                        self.assertNotIn("provider-secret-body", analysis["error"])

                    failed = client.get("/api/v1/ai/status").json()
                    self.assertEqual("failed", failed["verification"]["status"])
            for path in Path(folder).rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

        invalid = _generic_mock({}).model_dump(mode="json")
        invalid["fields"] = invalid["fields"][:-1]
        with self.assertRaises(ValueError):
            ReviewDraftV2.model_validate(invalid)

    def test_06b_keychain_configuration_restores_without_persisting_to_runtime(self):
        secret = "saved-secret-not-real-value"

        class FakeCredentialStore:
            available = True
            saved = None

            def load(self):
                return replace(self.saved, source="keychain") if self.saved else None

            def save(self, settings):
                self.saved = replace(settings, source="keychain")

            def delete(self):
                self.saved = None

        store = FakeCredentialStore()
        with tempfile.TemporaryDirectory(prefix="course-keychain-") as folder:
            environment = {
                "AI_PROVIDER": "kimi",
                "KIMI_API_KEY": "",
                "KIMI_MODEL": "",
            }
            with patch.dict(os.environ, environment, clear=False):
                first = create_application(
                    server,
                    runtime_root=Path(folder) / "runtime-first",
                    credential_store=store,
                )
                with TestClient(first) as client:
                    configured = client.post("/api/v1/ai/config", json={
                        "provider": "kimi",
                        "model": "k3",
                        "api_key": secret,
                        "storage": "keychain",
                    })
                    self.assertEqual(200, configured.status_code, configured.text)
                    payload = configured.json()
                    self.assertEqual("keychain", payload["configuration_source"])
                    self.assertEqual("keychain", payload["credential_storage"])
                    self.assertTrue(payload["persistent_credentials_saved"])
                    self.assertNotIn(secret, configured.text)

                restored = create_application(
                    server,
                    runtime_root=Path(folder) / "runtime-restored",
                    credential_store=store,
                )
                with TestClient(restored) as client:
                    status = client.get("/api/v1/ai/status")
                    self.assertEqual(200, status.status_code, status.text)
                    payload = status.json()
                    self.assertTrue(payload["configured"])
                    self.assertEqual("keychain", payload["configuration_source"])
                    self.assertTrue(payload["persistent_credentials_saved"])
                    self.assertNotIn(secret, status.text)

                    deleted = client.delete("/api/v1/ai/config/persisted")
                    self.assertEqual(200, deleted.status_code, deleted.text)
                    self.assertFalse(deleted.json()["persistent_credentials_saved"])
                    self.assertFalse(deleted.json()["configured"])

            for path in Path(folder).rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

    def test_07_review_to_process_to_prequote_business_chain(self):
        document = self.upload()
        analysis = self.wait(
            self.client.post(
                "/api/v1/analyses",
                json={"document_id": document["id"], "mode": "mock"},
            ).json()["id"]
        )

        blocked = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan",
            json={
                "manufacturing_family": "cnc_machining",
                "quantity": 100,
                "material_form": "圆棒毛坯",
            },
        )
        self.assertEqual(409, blocked.status_code, blocked.text)

        for finding_id in analysis["rules"]["required_decision_ids"]:
            response = self.client.patch(
                f"/api/v1/reviews/{analysis['id']}/findings/{finding_id}",
                json={"decision": "confirmed", "reviewer": "Business Flow Tester"},
            )
            self.assertEqual(200, response.status_code, response.text)
        finalized = self.client.post(
            f"/api/v1/reviews/{analysis['id']}/finalize",
            json={"reviewer": "Business Flow Tester", "acknowledgement": True},
        )
        self.assertEqual(200, finalized.status_code, finalized.text)
        self.assertEqual("1.0", finalized.json()["business_artifacts"]["contract_version"])

        process = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan",
            json={
                "manufacturing_family": "cnc_machining",
                "quantity": 100,
                "material_form": "圆棒毛坯",
                "equipment_capability": "三轴加工中心、液压夹具、常用刀具",
                "inspection_capability": "三坐标、千分尺、粗糙度仪",
                "special_requirements": "关键尺寸首件全检",
            },
        )
        self.assertEqual(200, process.status_code, process.text)
        process_payload = process.json()
        self.assertEqual("2.0", process_payload["process_plan"]["schema_version"])
        self.assertEqual("draft", process_payload["process_plan"]["status"])
        self.assertEqual("pending", process_payload["process_plan"]["review_status"])
        self.assertEqual(6, len(process_payload["process_plan"]["steps"]))
        self.assertTrue(process_payload["process_plan"]["human_confirmation_required"])
        self.assertTrue(process_payload["process_plan"]["route_summary"])
        self.assertEqual(64, len(process_payload["process_plan"]["source_fact_digest"]))
        self.assertGreaterEqual(len(process_payload["process_plan"]["risks"]), 3)
        self.assertNotIn("needs_review", " ".join(process_payload["process_plan"]["warnings"]))
        self.assertNotIn("blocked", " ".join(process_payload["process_plan"]["warnings"]))
        first_step = process_payload["process_plan"]["steps"][0]
        self.assertIn("input_state", first_step)
        self.assertIn("setup_and_datum", first_step)
        self.assertIn("quality_checks", first_step)
        self.assertTrue(any(item["status"] == "needs_confirmation" for item in first_step["parameters"]))
        self.assertIsNone(process_payload["prequote"])

        quote_inputs = {
            "net_weight_kg": 0.5,
            "material_unit_price": 20,
            "material_loss_rate_pct": 10,
            "setup_hours": 2,
            "processing_minutes_per_part": 12,
            "machine_hourly_rate": 100,
            "tooling_cost": 500,
            "outsourcing_cost": 300,
            "inspection_packaging_per_part": 5,
            "logistics_cost": 200,
            "overhead_rate_pct": 10,
            "risk_rate_pct": 5,
            "target_margin_pct": 20,
            "currency": "CNY",
        }
        unreviewed_quote = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/prequote",
            json=quote_inputs,
        )
        self.assertEqual(409, unreviewed_quote.status_code)
        no_process_ack = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan/confirm",
            json={"reviewer": "Process Tester", "acknowledgement": False},
        )
        self.assertEqual(422, no_process_ack.status_code)
        incomplete_process_check = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan/confirm",
            json={"reviewer": "Process Tester", "acknowledgement": True},
        )
        self.assertEqual(422, incomplete_process_check.status_code)
        process_review = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan/confirm",
            json={
                "reviewer": "Process Tester",
                "reviewer_role": "工艺工程师",
                "note": "课堂闭环验证；投产参数仍需现场确认。",
                "route_checked": True,
                "equipment_checked": True,
                "quality_checked": True,
                "acknowledgement": True,
            },
        )
        self.assertEqual(200, process_review.status_code, process_review.text)
        self.assertEqual("confirmed", process_review.json()["process_plan"]["review_status"])
        self.assertEqual("Process Tester", process_review.json()["process_plan"]["reviewed_by"])
        self.assertEqual(3, len(process_review.json()["process_plan"]["review_checklist"]))

        quote = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/prequote",
            json=quote_inputs,
        )
        self.assertEqual(200, quote.status_code, quote.text)
        prequote = quote.json()["prequote"]
        self.assertEqual(4800.0, prequote["direct_cost"])
        self.assertEqual(480.0, prequote["overhead_cost"])
        self.assertEqual(264.0, prequote["risk_cost"])
        self.assertEqual(5544.0, prequote["total_cost"])
        self.assertEqual(6930.0, prequote["target_revenue"])
        self.assertEqual(69.3, prequote["unit_prequote"])
        self.assertEqual("deterministic-cost-v1", prequote["formula_version"])
        self.assertEqual("2.0", prequote["process_plan_version"])

        persisted = self.client.get(
            f"/api/v1/analyses/{analysis['id']}/business-artifacts"
        )
        self.assertEqual(69.3, persisted.json()["prequote"]["unit_prequote"])
        exported = self.client.get(
            f"/api/v1/analyses/{analysis['id']}/export?format=json"
        )
        self.assertEqual(200, exported.status_code, exported.text)
        self.assertEqual(69.3, exported.json()["business_artifacts"]["prequote"]["unit_prequote"])
        html_export = self.client.get(
            f"/api/v1/analyses/{analysis['id']}/export?format=html"
        )
        self.assertEqual(200, html_export.status_code, html_export.text)
        self.assertIn("加工工艺路线卡", html_export.text)
        self.assertIn("确定性预报价", html_export.text)
        self.assertIn("69.3", html_export.text)
        process_pdf = self.client.get(
            f"/api/v1/analyses/{analysis['id']}/export?format=process-pdf"
        )
        self.assertEqual(200, process_pdf.status_code, process_pdf.text)
        self.assertEqual("application/pdf", process_pdf.headers["content-type"])
        self.assertTrue(process_pdf.content.startswith(b"%PDF-"))
        self.assertGreater(len(process_pdf.content), 10_000)
        self.assertIn(b"/Count 2", process_pdf.content)

        regenerated = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan",
            json={
                "manufacturing_family": "sheet_metal",
                "quantity": 80,
                "material_form": "冷轧板",
            },
        )
        self.assertEqual(200, regenerated.status_code, regenerated.text)
        self.assertEqual("pending", regenerated.json()["process_plan"]["review_status"])
        self.assertIsNone(regenerated.json()["prequote"])
        stale_quote = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/prequote",
            json=quote_inputs,
        )
        self.assertEqual(409, stale_quote.status_code)

    def test_07a_classroom_reference_profile_runs_the_complete_gated_chain(self):
        document = self.upload()
        analysis = self.wait(
            self.client.post(
                "/api/v1/analyses",
                json={"document_id": document["id"], "mode": "mock"},
            ).json()["id"]
        )
        reference_url = f"/api/v1/analyses/{analysis['id']}/classroom-reference-profile"

        before_human_review = self.client.get(reference_url)
        self.assertEqual(409, before_human_review.status_code, before_human_review.text)

        for finding_id in analysis["rules"]["required_decision_ids"]:
            decided = self.client.patch(
                f"/api/v1/reviews/{analysis['id']}/findings/{finding_id}",
                json={"decision": "confirmed", "reviewer": "Classroom Flow Tester"},
            )
            self.assertEqual(200, decided.status_code, decided.text)
        finalized = self.client.post(
            f"/api/v1/reviews/{analysis['id']}/finalize",
            json={"reviewer": "Classroom Flow Tester", "acknowledgement": True},
        )
        self.assertEqual(200, finalized.status_code, finalized.text)

        reference = self.client.get(reference_url)
        self.assertEqual(200, reference.status_code, reference.text)
        profile = reference.json()
        self.assertEqual("1.0", profile["schema_version"])
        self.assertEqual("classroom-reference-2026.08", profile["catalog_version"])
        self.assertEqual("ai-facts-public-reference-v1", profile["generated_by"])
        self.assertTrue(profile["human_confirmation_required"])
        self.assertEqual("cnc_machining", profile["manufacturing_family"])
        self.assertEqual(100, profile["quantity"])
        self.assertEqual(25.0, profile["quote_inputs"]["material_unit_price"])
        self.assertGreaterEqual(len(profile["sources"]), 3)
        self.assertTrue(all(item["url"].startswith("https://") for item in profile["sources"]))
        self.assertTrue(any("stats.gov.cn" in item["url"] for item in profile["sources"]))
        self.assertTrue(any("nist.gov" in item["url"] for item in profile["sources"]))
        self.assertIn("不是供应商询价", profile["boundary"])

        process = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan",
            json={
                "manufacturing_family": profile["manufacturing_family"],
                "quantity": profile["quantity"],
                "material_form": profile["material_form"],
                "equipment_capability": profile["equipment_capability"],
                "inspection_capability": profile["inspection_capability"],
                "special_requirements": profile["special_requirements"],
            },
        )
        self.assertEqual(200, process.status_code, process.text)
        self.assertEqual("pending", process.json()["process_plan"]["review_status"])
        self.assertIsNone(process.json()["prequote"])

        blocked_quote = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/prequote",
            json=profile["quote_inputs"],
        )
        self.assertEqual(409, blocked_quote.status_code, blocked_quote.text)

        confirmed = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/process-plan/confirm",
            json={
                "reviewer": "Classroom Flow Tester",
                "reviewer_role": "课堂工艺复核人",
                "note": "仅确认课堂流程，不作为投产放行。",
                "route_checked": True,
                "equipment_checked": True,
                "quality_checked": True,
                "acknowledgement": True,
            },
        )
        self.assertEqual(200, confirmed.status_code, confirmed.text)

        quote = self.client.post(
            f"/api/v1/analyses/{analysis['id']}/prequote",
            json=profile["quote_inputs"],
        )
        self.assertEqual(200, quote.status_code, quote.text)
        prequote = quote.json()["prequote"]
        self.assertEqual("deterministic-cost-v1", prequote["formula_version"])
        self.assertGreater(prequote["unit_prequote"], 0)
        self.assertTrue(prequote["human_confirmation_required"])

    def test_07a_field_correction_preserves_ai_draft_and_flows_to_facts(self):
        document = self.upload()
        analysis = self.wait(
            self.client.post(
                "/api/v1/analyses",
                json={"document_id": document["id"], "mode": "mock"},
            ).json()["id"]
        )
        original_value = next(
            item["value"] for item in analysis["draft"]["fields"]
            if item["name"] == "part_name"
        )
        corrected_value = "7Q47.036.212.DES001 [PART]"

        unknown = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/fields/unknown_field",
            json={"corrected_value": "not allowed"},
        )
        self.assertEqual(404, unknown.status_code, unknown.text)
        blank = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/fields/part_name",
            json={"corrected_value": "   "},
        )
        self.assertEqual(422, blank.status_code, blank.text)

        corrected = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/fields/part_name",
            json={
                "corrected_value": corrected_value,
                "reviewer": "Drawing Engineer",
                "note": "Checked against the controlled source filename.",
            },
        )
        self.assertEqual(200, corrected.status_code, corrected.text)
        corrected_payload = corrected.json()
        self.assertEqual(
            original_value,
            next(
                item["value"] for item in corrected_payload["draft"]["fields"]
                if item["name"] == "part_name"
            ),
            "the stored AI draft must remain immutable",
        )
        self.assertEqual(1, len(corrected_payload["field_corrections"]))
        field_correction = corrected_payload["field_corrections"][0]
        self.assertEqual("part_name", field_correction["field_name"])
        self.assertEqual(corrected_value, field_correction["corrected_value"])
        self.assertEqual("Drawing Engineer", field_correction["reviewer"])

        draft_report = self.app.state.review_service.draft_report_payload(analysis["id"])
        self.assertEqual(corrected_value, draft_report["field_corrections"][0]["corrected_value"])
        self.assertTrue(any(
            corrected_value in item["requirement"]
            for item in draft_report["engineering_review"]["requirements"]
        ))

        for finding_id in analysis["rules"]["required_decision_ids"]:
            decided = self.client.patch(
                f"/api/v1/reviews/{analysis['id']}/findings/{finding_id}",
                json={"decision": "confirmed", "reviewer": "Drawing Engineer"},
            )
            self.assertEqual(200, decided.status_code, decided.text)
        finalized = self.client.post(
            f"/api/v1/reviews/{analysis['id']}/finalize",
            json={"reviewer": "Drawing Engineer", "acknowledgement": True},
        )
        self.assertEqual(200, finalized.status_code, finalized.text)
        facts = finalized.json()["business_artifacts"]["drawing_facts"]["facts"]
        part_name = next(item for item in facts if item["name"] == "part_name")
        self.assertEqual(corrected_value, part_name["value"])
        self.assertEqual("human_correction", part_name["source"])

        immutable = self.client.patch(
            f"/api/v1/reviews/{analysis['id']}/fields/part_name",
            json={"corrected_value": "another value"},
        )
        self.assertEqual(409, immutable.status_code, immutable.text)

    def test_07aa_explicit_drawing_quantity_overrides_classroom_default(self):
        draft = _generic_mock({})
        draft = draft.model_copy(update={
            "engineering_requirements": [
                *draft.engineering_requirements,
                EngineeringRequirement(
                    id="req-quantity",
                    category="process_note",
                    requirement="REQUIRED QUANTITY: 10 PCS",
                    confidence=0.98,
                    evidence_ids=["ev-title"],
                ),
            ],
        })
        facts = build_drawing_facts(
            analysis_id="run-explicit-quantity",
            business_status="needs_review",
            draft=draft,
            rules=evaluate_draft(draft, page_count=1),
            decisions=[],
            source_status="ai_extracted",
        )
        quantity = next(item for item in facts.facts if item.name == "quantity")
        self.assertEqual("10", quantity.value)
        self.assertNotIn("quantity", facts.missing_for_process)
        profile = build_classroom_reference_profile(facts)
        self.assertEqual(10, profile.quantity)
        self.assertTrue(any("数量采用图纸明确标注" in item for item in profile.assumptions))

    def test_07ab_process_document_reuses_visible_route_without_cnc_fallback(self):
        draft = _generic_mock({}).model_copy(deep=True)
        draft.document_type = DocumentType.PROCESS_DOCUMENT
        draft.summary = "工程流程表包含料卷进料、滚压、弯曲、冲切、检验、喷漆与包装。"
        for field in draft.fields:
            replacements = {
                "part_name": "",
                "revision": "",
                "material": "料卷 / 滚压保护膜 / 过程保护膜 / 成品保护膜",
                "dimensions": "长度 1285 mm；料卷外径≤Φ1350，内径Φ380~Φ550",
                "tolerances": "",
            }
            field.value = replacements[field.name]
            field.confidence = 0.90 if field.name == "dimensions" else 0.85 if field.name == "material" else 0.0
            field.evidence_ids = ["ev-body"] if field.name in {"material", "dimensions"} else []
        draft.engineering_requirements = [
            EngineeringRequirement(id="req-insp", category="inspection", requirement="INSP10 料卷进料：外观、尺寸、性能和质保书检验", confidence=0.94, evidence_ids=["ev-body"]),
            EngineeringRequirement(id="req-roll", category="process_note", requirement="OP10 滚压：上料、接料、滚压成型和切断", confidence=0.95, evidence_ids=["ev-body"]),
            EngineeringRequirement(id="req-bend", category="process_note", requirement="OP20 弯曲：一出二弯曲并两端切断", confidence=0.95, evidence_ids=["ev-body"]),
            EngineeringRequirement(id="req-front", category="process_note", requirement="OP30 前端冲切：粗切、精切和 R 角冲切", confidence=0.95, evidence_ids=["ev-body"]),
            EngineeringRequirement(id="req-rear", category="process_note", requirement="OP40 后端冲切：预切、缺口冲切、翻边和精切", confidence=0.95, evidence_ids=["ev-body"]),
            EngineeringRequirement(id="req-coat", category="surface", requirement="OP60 表处喷漆：完成前处理、遮蔽和喷漆", confidence=0.90, evidence_ids=["ev-note-anodize"]),
            EngineeringRequirement(id="req-pack", category="inspection", requirement="OP70 检验贴膜包装：检验、贴膜、贴标签和包装", confidence=0.90, evidence_ids=["ev-note-inspection"]),
        ]
        facts = build_drawing_facts(
            analysis_id="run-process-document",
            business_status="blocked",
            draft=draft,
            rules=evaluate_draft(draft, page_count=1),
            decisions=[],
            source_status="ai_extracted",
        )
        process_facts = [item for item in facts.facts if item.name.startswith("process_operation_")]
        self.assertEqual(7, len(process_facts))
        self.assertTrue(process_facts[0].value.startswith("INSP10"))

        profile = build_classroom_reference_profile(facts)
        self.assertEqual(ManufacturingFamily.SHEET_METAL, profile.manufacturing_family)
        self.assertGreaterEqual(profile.match_confidence, 0.90)
        self.assertIn("金属卷料", profile.material_form)
        self.assertEqual("assumptions_only", assess_feature_inputs(feature="process", facts=facts, profile=profile).result_status)
        self.assertEqual("assumptions_only", assess_feature_inputs(feature="quote", facts=facts, profile=profile).result_status)

        request = ProcessPlanRequest(
            manufacturing_family=profile.manufacturing_family,
            quantity=profile.quantity,
            material_form=profile.material_form,
            equipment_capability=profile.equipment_capability,
            inspection_capability=profile.inspection_capability,
            special_requirements=profile.special_requirements,
        )
        plan = build_process_plan(analysis_id=facts.analysis_id, facts=facts, request=request)
        operation_text = " ".join(item.operation for item in plan.steps)
        self.assertEqual(7, len(plan.steps))
        self.assertIn("滚压", operation_text)
        self.assertIn("弯曲", operation_text)
        self.assertIn("前端冲切", operation_text)
        self.assertIn("喷漆", operation_text)
        self.assertNotIn("CNC", operation_text)
        self.assertNotIn("激光切割", operation_text)
        self.assertIn("检验工位", plan.steps[0].equipment_capability)
        self.assertIn("冲床", plan.steps[4].equipment_capability)
        self.assertIn(profile.material_form, plan.route_summary)
        self.assertNotIn("材料形态暂按 料卷 / 滚压保护膜", plan.route_summary)
        self.assertEqual("本次路线依据为 AI 提取事实，尚未由工程师逐项确认。", plan.risks[0].concern)
        self.assertTrue(any("AI 提取事实尚未由工程师逐项确认" in item for item in plan.warnings))

        quote = build_prequote(
            analysis_id=facts.analysis_id,
            process_plan=plan,
            request=profile.quote_inputs,
            input_source="ai_public_reference",
        )
        self.assertGreater(quote.unit_prequote, 0)
        self.assertTrue(quote.human_confirmation_required)

    def test_07b_review_process_and_quote_are_independent_product_features(self):
        expected_kinds = {
            "review": "review_report",
            "process": "process_plan",
            "quote": "quote_estimate",
        }
        run_ids = []
        for feature in ("review", "process", "quote"):
            document = self.upload()
            created = self.client.post(
                f"/api/v1/features/{feature}/runs",
                json={
                    "document_id": document["id"],
                    "mode": "mock",
                    "external_processing_consent": False,
                },
            )
            self.assertEqual(202, created.status_code, created.text)
            run_id = created.json()["id"]
            run_ids.append(run_id)
            self.wait(run_id)

            result = self.client.get(f"/api/v1/features/runs/{run_id}")
            self.assertEqual(200, result.status_code, result.text)
            payload = result.json()
            self.assertEqual(feature, payload["feature"])
            self.assertEqual("completed", payload["status"])
            self.assertEqual(expected_kinds[feature], payload["output"]["kind"])
            self.assertTrue(payload["output"]["report_url"])
            self.assertTrue(payload["output"]["report_available"])

            if feature == "review":
                self.assertTrue(payload["output"]["review"]["issues"])
            elif feature == "process":
                self.assertEqual("assumptions_only", payload["output"]["result_status"])
                self.assertEqual("needs_review", payload["business_status"])
                self.assertTrue(payload["output"]["warnings"])
                self.assertGreaterEqual(len(payload["output"]["process_plan"]["steps"]), 4)
                self.assertTrue(payload["output"]["sources"])
            else:
                self.assertEqual("assumptions_only", payload["output"]["result_status"])
                self.assertEqual("needs_review", payload["business_status"])
                self.assertTrue(payload["output"]["warnings"])
                quote = payload["output"]["prequote"]
                self.assertGreater(quote["unit_prequote"], 0)
                self.assertEqual("deterministic-cost-v1", quote["formula_version"])
                self.assertTrue(payload["output"]["sources"])

            report = self.client.get(payload["output"]["report_url"])
            self.assertEqual(200, report.status_code, report.text)
            self.assertEqual("application/pdf", report.headers["content-type"])
            self.assertTrue(report.content.startswith(b"%PDF-"))
            self.assertGreater(len(report.content), 5_000)

        history = self.client.get("/api/v1/features/history")
        self.assertEqual(200, history.status_code, history.text)
        history_by_id = {item["id"]: item for item in history.json()["runs"]}
        self.assertEqual(
            {"review", "process", "quote"},
            {history_by_id[run_id]["feature"] for run_id in run_ids},
        )

    def test_07c_insufficient_inputs_do_not_generate_route_amount_or_report(self):
        service = self.app.state.review_service
        original = service.mock_provider

        class FixedProvider:
            name = "mock"

            def __init__(self, draft):
                self.draft = draft

            def analyze(self, **_):
                return ProviderResult(
                    draft=self.draft,
                    metadata={"provider": "mock", "model": "fixed-test-fixture"},
                )

        no_family = _generic_mock({}).model_copy(deep=True)
        for field in no_family.fields:
            replacements = {
                "part_name": "课堂样件",
                "material": "M-01 专用材料",
                "dimensions": "120 × 80 × 12 mm",
                "tolerances": "±0.10 mm",
            }
            if field.name in replacements:
                field.value = replacements[field.name]
                field.confidence = 0.95

        missing_material = _generic_mock({}).model_copy(deep=True)
        material = next(item for item in missing_material.fields if item.name == "material")
        material.value = ""
        material.confidence = 0.20

        try:
            for feature, draft, missing_name in (
                ("process", no_family, "manufacturing_family"),
                ("quote", missing_material, "material"),
            ):
                service.mock_provider = FixedProvider(draft)
                document = self.upload()
                created = self.client.post(
                    f"/api/v1/features/{feature}/runs",
                    json={"document_id": document["id"], "mode": "mock"},
                )
                self.assertEqual(202, created.status_code, created.text)
                run_id = created.json()["id"]
                self.wait(run_id)

                response = self.client.get(f"/api/v1/features/runs/{run_id}")
                self.assertEqual(200, response.status_code, response.text)
                payload = response.json()
                self.assertEqual("completed", payload["status"])
                output = payload["output"]
                self.assertEqual("insufficient_input", output["result_status"])
                self.assertIn(missing_name, output["missing_inputs"])
                self.assertTrue(output["result_message"])
                self.assertTrue(output["warnings"])
                self.assertFalse(output["report_available"])
                self.assertIsNone(output["report_url"])
                self.assertNotIn("reference_profile", output)
                if feature == "process":
                    self.assertIsNone(output["process_plan"])
                    self.assertNotIn("通用 CNC 路线已生成", json.dumps(output, ensure_ascii=False))
                else:
                    self.assertIsNone(output["prequote"])
                    self.assertNotIn("unit_prequote", json.dumps(output))

                report = self.client.get(f"/api/v1/features/runs/{run_id}/report")
                self.assertEqual(409, report.status_code, report.text)
                self.assertIn(output["result_message"], report.json()["error"])
        finally:
            service.mock_provider = original

    def test_07d_mock_mode_is_not_exposed_by_the_product_app(self):
        with tempfile.TemporaryDirectory(prefix="product-no-mock-") as folder:
            app = create_application(server, runtime_root=Path(folder) / "runtime")
            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/v1/documents",
                    files={"file": ("classroom.pdf", minimal_pdf(), "application/pdf")},
                )
                self.assertEqual(201, uploaded.status_code, uploaded.text)
                rejected = client.post(
                    "/api/v1/features/review/runs",
                    json={"document_id": uploaded.json()["id"], "mode": "mock"},
                )
                self.assertEqual(404, rejected.status_code, rejected.text)
                self.assertIn("automated tests", rejected.json()["error"])

    def test_07e_feature_api_exposes_safe_provider_degradation(self):
        document = self.upload()
        created = self.client.post(
            "/api/v1/features/process/runs",
            json={"document_id": document["id"], "mode": "mock"},
        )
        self.assertEqual(202, created.status_code, created.text)
        run_id = created.json()["id"]
        self.wait(run_id)
        notice = "DeepSeek 二次复核未完成，已安全保留视觉模型主结果。"
        self.app.state.review_service.db.update_analysis(
            run_id,
            provider_metadata_json=json.dumps(
                {
                    "routing": "Gemini images → conditional DeepSeek review failed safely",
                    "visual_stage": {
                        "provider": "gemini",
                        "model": "gemini-3.6-flash",
                        "usage": {"totalTokenCount": 100},
                        "localization_stage": {
                            "target_count": 2,
                            "accepted_count": 1,
                            "calls": [{"usage": {"totalTokenCount": 25}}],
                        },
                    },
                    "secondary_stage": {"status": "failed_safely"},
                    "degraded": True,
                    "degradation_notice": notice,
                },
                ensure_ascii=False,
            ),
        )

        response = self.client.get(f"/api/v1/features/runs/{run_id}")
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual("completed", payload["status"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(notice, payload["degradation_notice"])
        self.assertEqual("completed", payload["provider_execution"]["visual_status"])
        self.assertEqual("failed_safely", payload["provider_execution"]["secondary_status"])
        self.assertEqual(125, payload["provider_execution"]["total_token_count"])
        self.assertEqual(1, payload["provider_execution"]["localization_accepted_count"])
        self.assertTrue(payload["output"]["degraded"])
        self.assertIn(notice, payload["output"]["warnings"])

    def test_08_hybrid_gemini_images_then_deepseek_minimized_text(self):
        gemini_response = Mock()
        gemini_response.headers = {"x-request-id": "safe-gemini-request"}
        gemini_response.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": _generic_mock({}).model_dump_json()}]}}],
            "usageMetadata": {"promptTokenCount": 11, "totalTokenCount": 21},
        }
        gemini_response.raise_for_status.return_value = None
        gemini_client = Mock(); gemini_client.post.return_value = gemini_response
        gemini_context = Mock(); gemini_context.__enter__ = Mock(return_value=gemini_client); gemini_context.__exit__ = Mock(return_value=False)

        deepseek_response = Mock()
        deepseek_response.headers = {"x-request-id": "safe-deepseek-request"}
        deepseek_response.json.return_value = {
            "id": "safe-deepseek-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({
                    "summary": "结构化文本复核完成。",
                    "findings": [{
                        "code": "PROCESS_DETAIL_REVIEW",
                        "field": "dimensions",
                        "conclusion": "尺寸描述需要工艺人员确认加工基准。",
                        "category": "requirement_consistency",
                        "impact": "尺寸基准不一致可能造成加工偏差。",
                        "recommendation": "由工艺人员核对尺寸基准后再进入加工。",
                        "confidence": "0.94",
                        "evidence_ids": ["ev-body", "invented-evidence"],
                    }, {
                        "code": "missing_required_field",
                        "field": "part_name",
                        "conclusion": "零件名称缺失。",
                        "confidence": 0.9,
                        "evidence_ids": ["ev-body"],
                    }],
                    "open_questions": ["加工基准是否已由工艺人员确认？"],
                }, ensure_ascii=False)},
            }],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
                "prompt_cache_hit_tokens": 8,
            },
        }
        deepseek_response.raise_for_status.return_value = None
        deepseek_client = Mock(); deepseek_client.post.return_value = deepseek_response
        deepseek_context = Mock(); deepseek_context.__enter__ = Mock(return_value=deepseek_client); deepseek_context.__exit__ = Mock(return_value=False)

        settings = ProviderSettings(
            provider="hybrid",
            model="gemini-vision-test",
            api_key="secret-gemini-never-return",
            secondary_model="deepseek-v4-flash",
            secondary_api_key="secret-deepseek-never-return",
            secondary_api_base="https://api.deepseek.com",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="hybrid-contract-") as folder:
            page = Path(folder) / "page-1.png"; page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", side_effect=[gemini_context, deepseek_context]):
                result = GeminiDeepSeekHybridProvider(
                    settings,
                    secondary_review_mode="always",
                ).analyze(document={}, page_paths=[page])

        gemini_request = gemini_client.post.call_args.kwargs["json"]
        deepseek_request = deepseek_client.post.call_args.kwargs["json"]
        secondary_text = deepseek_request["messages"][1]["content"]
        self.assertIn("inline_data", json.dumps(gemini_request))
        self.assertNotIn("inline_data", secondary_text)
        self.assertNotIn("image_url", secondary_text)
        self.assertNotIn("part_name", secondary_text)
        self.assertNotIn("revision", secondary_text)
        self.assertNotIn("MOCK TITLE BLOCK", secondary_text)
        self.assertEqual({"type": "disabled"}, deepseek_request["thinking"])
        self.assertEqual("deepseek-v4-flash", deepseek_request["model"])
        added = next(item for item in result.draft.findings if item.id.startswith("deepseek-review-"))
        self.assertEqual(["ev-body"], added.evidence_ids)
        self.assertTrue(added.requires_human_confirmation)
        self.assertEqual(0.85, added.confidence)
        self.assertFalse(any(
            item.id.startswith("deepseek-review-") and item.field == "part_name"
            for item in result.draft.findings
        ))
        self.assertEqual("hybrid", result.metadata["provider"])
        self.assertEqual("skipped", result.metadata["visual_stage"]["localization_stage"]["status"])
        self.assertEqual("completed", result.metadata["secondary_stage"]["status"])
        self.assertEqual("always", result.metadata["secondary_review"]["mode"])
        self.assertEqual(["dimensions", "material", "tolerances"], result.metadata["secondary_review"]["eligible_fields"])
        self.assertEqual(1, result.metadata["secondary_stage"]["accepted_findings"])
        serialized = json.dumps(result.metadata) + json.dumps(provider_status(settings))
        self.assertNotIn("secret-gemini-never-return", serialized)
        self.assertNotIn("secret-deepseek-never-return", serialized)

        with tempfile.TemporaryDirectory(prefix="hybrid-config-") as folder:
            app = create_application(
                server,
                runtime_root=Path(folder) / "runtime",
                allow_test_fixtures=True,
            )
            with TestClient(app) as client:
                configured = client.post("/api/v1/ai/config", json={
                    "provider": "hybrid",
                    "model": "models/gemini-vision-test",
                    "api_key": "secret-gemini-never-return",
                    "secondary_model": "deepseek-v4-flash",
                    "secondary_api_key": "secret-deepseek-never-return",
                })
                self.assertEqual(200, configured.status_code, configured.text)
                public = configured.json()
                self.assertTrue(public["configured"])
                self.assertEqual("hybrid", public["provider"])
                self.assertEqual("gemini-vision-test", public["visual_model"])
                self.assertEqual("deepseek-v4-flash", public["secondary_model"])
                self.assertEqual("gemini-vision-test → deepseek-v4-flash", public["model"])
                self.assertNotIn("secret-gemini-never-return", configured.text)
                self.assertNotIn("secret-deepseek-never-return", configured.text)
                self.assertNotIn("api_key", configured.text.lower())

    def test_08a_k3_deepseek_hybrid_images_then_minimized_text(self):
        kimi_response = Mock()
        kimi_response.headers = {"x-request-id": "safe-k3-request"}
        kimi_response.json.return_value = {
            "id": "safe-k3-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": _generic_mock({}).model_dump_json()},
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
        }
        kimi_response.raise_for_status.return_value = None
        kimi_client = Mock(); kimi_client.post.return_value = kimi_response
        kimi_context = Mock(); kimi_context.__enter__ = Mock(return_value=kimi_client); kimi_context.__exit__ = Mock(return_value=False)

        deepseek_response = Mock()
        deepseek_response.headers = {"x-request-id": "safe-k3-deepseek-request"}
        deepseek_response.json.return_value = {
            "id": "safe-k3-deepseek-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({
                    "summary": "最小化文本复核完成。",
                    "findings": [{
                        "code": "PROCESS_DATUM_REVIEW",
                        "field": "dimensions",
                        "conclusion": "尺寸基准需由工艺负责人确认。",
                        "category": "requirement_consistency",
                        "impact": "基准不一致可能导致加工偏差。",
                        "recommendation": "在编制工艺卡前确认加工基准。",
                        "confidence": 0.91,
                        "evidence_ids": ["ev-body"],
                    }],
                    "open_questions": [],
                }, ensure_ascii=False)},
            }],
            "usage": {"prompt_tokens": 16, "completion_tokens": 8, "total_tokens": 24},
        }
        deepseek_response.raise_for_status.return_value = None
        deepseek_client = Mock(); deepseek_client.post.return_value = deepseek_response
        deepseek_context = Mock(); deepseek_context.__enter__ = Mock(return_value=deepseek_client); deepseek_context.__exit__ = Mock(return_value=False)

        settings = ProviderSettings(
            provider="kimi-hybrid",
            model="k3",
            api_key="secret-k3-never-return",
            api_base="https://api.kimi.com/coding/v1",
            reasoning_effort="high",
            secondary_model="deepseek-v4-flash",
            secondary_api_key="secret-deepseek-never-return",
            secondary_api_base="https://api.deepseek.com",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="k3-hybrid-contract-") as folder:
            page = Path(folder) / "page-1.png"
            page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", side_effect=[kimi_context, deepseek_context]):
                result = GeminiDeepSeekHybridProvider(
                    settings,
                    secondary_review_mode="always",
                ).analyze(document={"id": "local-document-id"}, page_paths=[page])

        kimi_request = kimi_client.post.call_args.kwargs["json"]
        deepseek_request = deepseek_client.post.call_args.kwargs["json"]
        secondary_text = deepseek_request["messages"][1]["content"]
        self.assertEqual("k3", kimi_request["model"])
        self.assertEqual("high", kimi_request["reasoning_effort"])
        self.assertTrue(kimi_request["response_format"]["json_schema"]["strict"])
        self.assertIn("data:image/png;base64,", json.dumps(kimi_request))
        self.assertEqual("drawing-review:drawing-review-v2.9-process-rows", kimi_request["prompt_cache_key"])
        for forbidden in ("image_url", "part_name", "revision", "MOCK TITLE BLOCK", "local-document-id"):
            self.assertNotIn(forbidden, secondary_text)
        self.assertEqual("deepseek-v4-flash", deepseek_request["model"])
        self.assertEqual({"type": "disabled"}, deepseek_request["thinking"])
        self.assertEqual("kimi-hybrid", result.metadata["provider"])
        self.assertEqual("kimi", result.metadata["visual_stage"]["provider"])
        self.assertEqual("completed", result.metadata["secondary_stage"]["status"])
        self.assertEqual("Kimi K3 high images → conditionally selected structured text → DeepSeek review", result.metadata["routing"])
        self.assertEqual("k3 → deepseek-v4-flash", result.metadata["model"])
        serialized = json.dumps(result.metadata) + json.dumps(provider_status(settings))
        self.assertNotIn("secret-k3-never-return", serialized)
        self.assertNotIn("secret-deepseek-never-return", serialized)

        with tempfile.TemporaryDirectory(prefix="k3-hybrid-config-") as folder:
            app = create_application(
                server,
                runtime_root=Path(folder) / "runtime",
                allow_test_fixtures=True,
            )
            with TestClient(app) as client:
                configured = client.post("/api/v1/ai/config", json={
                    "provider": "kimi-hybrid",
                    "model": "k3",
                    "api_key": "secret-k3-never-return",
                    "secondary_model": "deepseek-v4-flash",
                    "secondary_api_key": "secret-deepseek-never-return",
                })
                self.assertEqual(200, configured.status_code, configured.text)
                public = configured.json()
                self.assertTrue(public["configured"])
                self.assertEqual("kimi-hybrid", public["provider"])
                self.assertEqual("k3", public["visual_model"])
                self.assertEqual("deepseek-v4-flash", public["secondary_model"])
                self.assertEqual("k3 → deepseek-v4-flash", public["model"])
                self.assertEqual("high", public["reasoning_effort"])
                self.assertNotIn("secret-k3-never-return", configured.text)
                self.assertNotIn("secret-deepseek-never-return", configured.text)
                reused = client.post("/api/v1/ai/config", json={
                    "provider": "kimi-hybrid",
                    "model": "k3",
                    "api_key": "replacement-k3-never-return",
                    "secondary_model": "deepseek-v4-flash",
                    "reuse_secondary": True,
                    "storage": "session",
                })
                self.assertEqual(200, reused.status_code, reused.text)
                self.assertTrue(reused.json()["secondary_credential_available"])
                self.assertEqual("k3 → deepseek-v4-flash", reused.json()["model"])
                self.assertNotIn("secret-deepseek-never-return", reused.text)
                self.assertNotIn("replacement-k3-never-return", reused.text)
                reused_both = client.post("/api/v1/ai/config", json={
                    "provider": "kimi-hybrid",
                    "model": "k3",
                    "secondary_model": "deepseek-v4-flash",
                    "reuse_primary": True,
                    "reuse_secondary": True,
                    "storage": "session",
                })
                self.assertEqual(200, reused_both.status_code, reused_both.text)
                self.assertTrue(reused_both.json()["credential_available"])
                self.assertTrue(reused_both.json()["secondary_credential_available"])
                self.assertNotIn("replacement-k3-never-return", reused_both.text)

    def test_09_hybrid_auto_routes_only_the_signaled_field(self):
        draft = _generic_mock({}).model_copy(deep=True)
        draft.findings = [
            item for item in draft.findings
            if item.field not in GeminiDeepSeekHybridProvider.secondary_review_fields
        ]
        draft.open_questions = []
        for field in draft.fields:
            if field.name in GeminiDeepSeekHybridProvider.secondary_review_fields:
                field.confidence = 0.95
            if field.name == "material":
                field.confidence = 0.61

        eligible_fields, reasons = GeminiDeepSeekHybridProvider._secondary_review_plan(
            draft,
            "auto",
        )
        secondary_input = GeminiDeepSeekHybridProvider._secondary_input(
            draft,
            eligible_fields,
        )
        serialized = json.dumps(secondary_input, ensure_ascii=False)

        self.assertEqual({"material"}, eligible_fields)
        self.assertEqual(["low_confidence:material"], reasons)
        self.assertEqual(["material"], secondary_input["triggered_fields"])
        self.assertEqual(["material"], [item["name"] for item in secondary_input["fields"]])
        self.assertNotIn('"dimensions"', serialized)
        self.assertNotIn('"tolerances"', serialized)
        self.assertNotIn('"part_name"', serialized)
        self.assertNotIn('"revision"', serialized)

    def test_10_hybrid_auto_skips_secondary_without_uncertainty_signal(self):
        clean_draft = _generic_mock({}).model_copy(update={
            "findings": [
                item for item in _generic_mock({}).findings
                if item.field not in GeminiDeepSeekHybridProvider.secondary_review_fields
            ],
            "open_questions": [],
        })
        gemini_response = Mock()
        gemini_response.headers = {"x-request-id": "safe-gemini-auto-skip"}
        gemini_response.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": clean_draft.model_dump_json()}]}}],
            "usageMetadata": {"promptTokenCount": 11, "totalTokenCount": 21},
        }
        gemini_response.raise_for_status.return_value = None
        gemini_client = Mock(); gemini_client.post.return_value = gemini_response
        gemini_context = Mock(); gemini_context.__enter__ = Mock(return_value=gemini_client); gemini_context.__exit__ = Mock(return_value=False)
        settings = ProviderSettings(
            provider="hybrid",
            model="gemini-vision-test",
            api_key="secret-gemini-never-return",
            secondary_model="deepseek-v4-flash",
            secondary_api_key="secret-deepseek-never-return",
            secondary_api_base="https://api.deepseek.com",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="hybrid-auto-skip-") as folder:
            page = Path(folder) / "page-1.png"; page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", return_value=gemini_context):
                result = GeminiDeepSeekHybridProvider(settings).analyze(document={}, page_paths=[page])

        self.assertEqual(1, gemini_client.post.call_count)
        self.assertEqual("gemini-vision-test", result.metadata["model"])
        self.assertEqual("skipped", result.metadata["secondary_stage"]["status"])
        self.assertFalse(result.metadata["secondary_review"]["triggered"])
        self.assertEqual([], result.metadata["secondary_review"]["eligible_fields"])
        self.assertEqual(clean_draft, result.draft)

    def test_10a_deepseek_failure_keeps_primary_result_and_marks_safe_degradation(self):
        primary_draft = _generic_mock({}).model_copy(deep=True)
        for field in primary_draft.fields:
            if field.name == "material":
                field.confidence = 0.61

        class StaticVisualProvider:
            def analyze(self, **_):
                return ProviderResult(
                    draft=primary_draft,
                    metadata={"provider": "gemini", "model": "gemini-vision-test"},
                )

        client = Mock()
        client.post.side_effect = httpx.ReadTimeout("secret-provider-body")
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        settings = ProviderSettings(
            provider="hybrid",
            model="gemini-vision-test",
            api_key="secret-gemini-never-return",
            secondary_model="deepseek-v4-flash",
            secondary_api_key="secret-deepseek-never-return",
            secondary_api_base="https://api.deepseek.com",
            source="session",
        )
        provider = GeminiDeepSeekHybridProvider(settings)
        provider.visual_provider = StaticVisualProvider()
        with patch("backend.providers.httpx.Client", return_value=context):
            result = provider.analyze(document={}, page_paths=[])

        self.assertEqual(primary_draft, result.draft)
        self.assertEqual("failed_safely", result.metadata["secondary_stage"]["status"])
        self.assertEqual("ReadTimeout", result.metadata["secondary_stage"]["failure_type"])
        self.assertEqual(0, result.metadata["secondary_stage"]["accepted_findings"])
        self.assertTrue(result.metadata["degraded"])
        self.assertTrue(result.metadata["degradation_notice"])
        self.assertEqual("gemini-vision-test", result.metadata["model"])
        serialized = json.dumps(result.metadata, ensure_ascii=False)
        self.assertNotIn("secret-provider-body", serialized)
        self.assertNotIn("secret-deepseek-never-return", serialized)

    def test_11_gemini_retries_one_invalid_contract(self):
        invalid_response = Mock()
        invalid_response.headers = {}
        invalid_response.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}],
        }
        invalid_response.raise_for_status.return_value = None

        valid_response = Mock()
        valid_response.headers = {"x-request-id": "safe-gemini-retry"}
        valid_response.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": _generic_mock({}).model_dump_json()}]}}],
            "usageMetadata": {"totalTokenCount": 42},
        }
        valid_response.raise_for_status.return_value = None

        client = Mock()
        client.post.side_effect = [invalid_response, valid_response]
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        settings = ProviderSettings(
            provider="gemini",
            model="gemini-vision-test",
            api_key="secret-gemini-retry",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="gemini-retry-") as folder:
            page = Path(folder) / "page-1.png"
            page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", return_value=context):
                result = GeminiVisionProvider(settings).analyze(document={}, page_paths=[page])

        self.assertEqual(2, client.post.call_count)
        self.assertEqual("gemini", result.metadata["provider"])
        self.assertEqual(42, result.metadata["usage"]["totalTokenCount"])

    def test_12_gemini_localizes_only_problem_evidence_and_rejects_weak_boxes(self):
        draft = _generic_mock({}).model_copy(deep=True)
        for evidence in draft.evidence:
            if evidence.id in {"ev-note-anodize", "ev-note-inspection"}:
                evidence.bbox = None

        primary_response = Mock()
        primary_response.headers = {"x-request-id": "safe-primary-location"}
        primary_response.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": draft.model_dump_json()}]}}],
            "usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 100},
        }
        primary_response.raise_for_status.return_value = None
        primary_client = Mock(); primary_client.post.return_value = primary_response
        primary_context = Mock(); primary_context.__enter__ = Mock(return_value=primary_client); primary_context.__exit__ = Mock(return_value=False)

        location_response = Mock()
        location_response.headers = {"x-request-id": "safe-location-pass"}
        location_response.json.return_value = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps({
                    "locations": [{
                        "evidence_id": "ev-note-anodize",
                        "page": 1,
                        "bbox_2d": [350, 680, 520, 960],
                        "confidence": 0.96,
                        "anchor_text": "ANODIZE 15-20 μm",
                    }, {
                        "evidence_id": "ev-note-inspection",
                        "page": 1,
                        "bbox_2d": [530, 680, 680, 960],
                        "confidence": 0.60,
                        "anchor_text": "KEY FEATURES TO BE INSPECTED",
                    }, {
                        "evidence_id": "model-invented-evidence",
                        "page": 1,
                        "bbox_2d": [10, 10, 50, 50],
                        "confidence": 0.99,
                        "anchor_text": "INVENTED",
                    }]
                }, ensure_ascii=False)}]},
            }],
            "usageMetadata": {"promptTokenCount": 12, "totalTokenCount": 20},
        }
        location_response.raise_for_status.return_value = None
        location_client = Mock(); location_client.post.return_value = location_response
        location_context = Mock(); location_context.__enter__ = Mock(return_value=location_client); location_context.__exit__ = Mock(return_value=False)

        settings = ProviderSettings(
            provider="gemini",
            model="gemini-vision-test",
            api_key="secret-gemini-location",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="gemini-location-") as folder:
            page = Path(folder) / "page-1.png"
            page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", side_effect=[primary_context, location_context]):
                result = GeminiVisionProvider(settings).analyze(document={}, page_paths=[page])

        localized = next(item for item in result.draft.evidence if item.id == "ev-note-anodize")
        rejected = next(item for item in result.draft.evidence if item.id == "ev-note-inspection")
        self.assertEqual([0.68, 0.35, 0.96, 0.52], localized.bbox)
        self.assertIsNone(rejected.bbox)
        stage = result.metadata["localization_stage"]
        self.assertEqual("completed", stage["status"])
        self.assertEqual(2, stage["target_count"])
        self.assertEqual(1, stage["accepted_count"])
        self.assertEqual(["ev-note-anodize"], stage["accepted_evidence_ids"])
        self.assertEqual(1, stage["rejected"]["low_confidence"])
        self.assertEqual(1, stage["rejected"]["unknown_evidence"])
        location_payload = location_client.post.call_args.kwargs["json"]
        location_text = location_payload["contents"][0]["parts"][0]["text"]
        self.assertIn("ev-note-anodize", location_text)
        self.assertNotIn(str(page), location_text)
        self.assertNotIn("secret-gemini-location", json.dumps(result.metadata) + json.dumps(location_payload))

    def test_12a_kimi_localizes_only_problem_evidence_and_rejects_weak_boxes(self):
        draft = _generic_mock({}).model_copy(deep=True)
        for evidence in draft.evidence:
            if evidence.id in {"ev-note-anodize", "ev-note-inspection"}:
                evidence.bbox = None

        primary_response = Mock()
        primary_response.headers = {"x-request-id": "safe-kimi-primary-location"}
        primary_response.json.return_value = {
            "id": "safe-kimi-primary-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": draft.model_dump_json()},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        primary_response.raise_for_status.return_value = None
        primary_client = Mock(); primary_client.post.return_value = primary_response
        primary_context = Mock(); primary_context.__enter__ = Mock(return_value=primary_client); primary_context.__exit__ = Mock(return_value=False)

        location_response = Mock()
        location_response.headers = {"x-request-id": "safe-kimi-location-pass"}
        location_response.json.return_value = {
            "id": "safe-kimi-location-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({
                    "locations": [{
                        "evidence_id": "ev-note-anodize",
                        "page": 1,
                        "bbox_2d": [350, 680, 520, 960],
                        "confidence": 0.96,
                        "anchor_text": "ANODIZE 15-20 μm",
                    }, {
                        "evidence_id": "ev-note-inspection",
                        "page": 1,
                        "bbox_2d": [530, 680, 680, 960],
                        "confidence": 0.60,
                        "anchor_text": "KEY FEATURES TO BE INSPECTED",
                    }, {
                        "evidence_id": "model-invented-evidence",
                        "page": 1,
                        "bbox_2d": [10, 10, 50, 50],
                        "confidence": 0.99,
                        "anchor_text": "INVENTED",
                    }],
                }, ensure_ascii=False)},
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }
        location_response.raise_for_status.return_value = None
        location_client = Mock(); location_client.post.return_value = location_response
        location_context = Mock(); location_context.__enter__ = Mock(return_value=location_client); location_context.__exit__ = Mock(return_value=False)

        settings = ProviderSettings(
            provider="kimi",
            model="k3",
            api_key="secret-kimi-location",
            api_base="https://api.kimi.com/coding/v1",
            reasoning_effort="high",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="kimi-location-") as folder:
            page = Path(folder) / "page-1.png"
            page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", side_effect=[primary_context, location_context]):
                result = KimiVisionProvider(settings).analyze(document={}, page_paths=[page])

        localized = next(item for item in result.draft.evidence if item.id == "ev-note-anodize")
        rejected = next(item for item in result.draft.evidence if item.id == "ev-note-inspection")
        self.assertEqual([0.68, 0.35, 0.96, 0.52], localized.bbox)
        self.assertIsNone(rejected.bbox)
        stage = result.metadata["localization_stage"]
        self.assertEqual("completed", stage["status"])
        self.assertEqual(2, stage["target_count"])
        self.assertEqual(1, stage["accepted_count"])
        self.assertEqual(["ev-note-anodize"], stage["accepted_evidence_ids"])
        self.assertEqual(1, stage["rejected"]["low_confidence"])
        self.assertEqual(1, stage["rejected"]["unknown_evidence"])
        location_payload = location_client.post.call_args.kwargs["json"]
        location_text = location_payload["messages"][1]["content"][0]["text"]
        self.assertEqual("k3-256k", location_payload["model"])
        self.assertEqual("low", location_payload["reasoning_effort"])
        self.assertTrue(location_payload["response_format"]["json_schema"]["strict"])
        self.assertIn("ev-note-anodize", location_text)
        self.assertNotIn(str(page), location_text)
        self.assertNotIn(
            "secret-kimi-location",
            json.dumps(result.metadata) + json.dumps(location_payload),
        )

    def test_12b_kimi_localization_downscales_only_the_follow_up_image(self):
        with tempfile.TemporaryDirectory(prefix="kimi-location-image-") as folder:
            page = Path(folder) / "page-1.png"
            Image.new("RGB", (2400, 1200), "white").save(page, format="PNG")
            data_url, metadata = KimiVisionProvider._localization_data_url(page)

        self.assertTrue(data_url.startswith("data:image/png;base64,"))
        self.assertTrue(metadata["resized"])
        self.assertEqual([2400, 1200], metadata["source_dimensions"])
        self.assertEqual([2000, 1000], metadata["sent_dimensions"])
        self.assertEqual(2000, metadata["max_edge_px"])
        self.assertLess(metadata["sent_bytes"], metadata["source_bytes"] * 2)

    def test_12c_fixed_location_benchmark_reuses_identical_images_without_exposing_them(self):
        document = self.upload()
        source = self.wait(self.client.post(
            "/api/v1/analyses",
            json={"document_id": document["id"], "mode": "mock"},
        ).json()["id"])
        service = self.app.state.review_service
        with service._provider_lock:
            previous_settings = service._provider_settings

        captured: dict[str, str] = {}

        def fake_kimi(_provider, *, draft, page_paths, prepared_images=None):
            targets = GeminiVisionProvider._missing_finding_evidence(
                draft,
                page_count=len(page_paths),
            )
            accepted = [item.id for items in targets.values() for item in items]
            self.assertIsNotNone(prepared_images)
            captured["kimi_image"] = prepared_images[1][0]
            return {
                "status": "completed",
                "target_count": len(accepted),
                "accepted_count": len(accepted),
                "accepted_evidence_ids": accepted,
                "rejected": {},
                "calls": [{
                    "page": 1,
                    "status": "completed",
                    "target_count": len(accepted),
                    "accepted_count": len(accepted),
                    "usage": {"total_tokens": 123},
                    "input_image": prepared_images[1][1],
                }],
            }

        def fake_gemini(_provider, *, draft, page_paths, endpoint, prepared_images=None):
            del endpoint
            targets = GeminiVisionProvider._missing_finding_evidence(
                draft,
                page_count=len(page_paths),
            )
            accepted = [item.id for items in targets.values() for item in items]
            self.assertIsNotNone(prepared_images)
            captured["gemini_image"] = prepared_images[1][0]
            return {
                "status": "completed",
                "target_count": len(accepted),
                "accepted_count": len(accepted),
                "accepted_evidence_ids": accepted,
                "rejected": {},
                "calls": [{
                    "page": 1,
                    "status": "completed",
                    "target_count": len(accepted),
                    "accepted_count": len(accepted),
                    "usage": {"totalTokenCount": 77},
                    "input_image": prepared_images[1][1],
                }],
            }

        request = {
            "source_analysis_ids": [source["id"]],
            "external_processing_consent": True,
        }
        try:
            denied = self.client.post(
                "/api/v1/benchmarks/evidence-localization",
                json={**request, "external_processing_consent": False},
            )
            self.assertEqual(403, denied.status_code)

            with service._provider_lock:
                service._provider_settings = ProviderSettings(
                    provider="kimi",
                    model="k3",
                    api_key="secret-benchmark-kimi",
                    api_base="https://api.kimi.com/coding/v1",
                    reasoning_effort="high",
                    source="session",
                )
            with patch.object(
                KimiVisionProvider,
                "_localize_missing_finding_evidence",
                autospec=True,
                side_effect=fake_kimi,
            ):
                kimi = self.client.post(
                    "/api/v1/benchmarks/evidence-localization",
                    json=request,
                )
            self.assertEqual(200, kimi.status_code, kimi.text)
            kimi_payload = kimi.json()
            self.assertEqual("fixed-evidence-location-v1", kimi_payload["benchmark_version"])
            self.assertEqual(123, kimi_payload["token_count"])
            self.assertEqual(kimi_payload["target_count"], kimi_payload["accepted_count"])

            with service._provider_lock:
                service._provider_settings = ProviderSettings(
                    provider="gemini",
                    model="gemini-vision-test",
                    api_key="secret-benchmark-gemini",
                    source="session",
                )
            with patch.object(
                GeminiVisionProvider,
                "_localize_missing_finding_evidence",
                autospec=True,
                side_effect=fake_gemini,
            ):
                gemini = self.client.post(
                    "/api/v1/benchmarks/evidence-localization",
                    json=request,
                )
            self.assertEqual(200, gemini.status_code, gemini.text)
            gemini_payload = gemini.json()
            self.assertEqual(77, gemini_payload["token_count"])
            self.assertEqual(kimi_payload["target_set_sha256"], gemini_payload["target_set_sha256"])
            self.assertEqual(captured["kimi_image"], captured["gemini_image"])

            public_payloads = kimi.text + gemini.text
            self.assertNotIn("secret-benchmark", public_payloads)
            self.assertNotIn("data:image", public_payloads)
            self.assertNotIn("private_dir", public_payloads)
        finally:
            with service._provider_lock:
                service._provider_settings = previous_settings

    def test_13_missing_field_rule_retains_valid_source_evidence(self):
        draft = _generic_mock({}).model_copy(deep=True)
        material = next(item for item in draft.fields if item.name == "material")
        material.value = ""
        material.confidence = 0.2
        material.evidence_ids = ["ev-title"]
        report = evaluate_draft(draft, page_count=1)
        issue = next(item for item in report.issues if item.id == "rule-missing-material")
        self.assertEqual(["ev-title"], issue.evidence_ids)
        self.assertEqual("2.3", report.rule_version)

    def test_14_gemini_location_failure_keeps_primary_review(self):
        draft = _generic_mock({}).model_copy(deep=True)
        evidence = next(item for item in draft.evidence if item.id == "ev-note-anodize")
        evidence.bbox = None

        primary_response = Mock()
        primary_response.headers = {"x-request-id": "safe-primary-location-fallback"}
        primary_response.json.return_value = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": draft.model_dump_json()}]},
            }],
            "usageMetadata": {"totalTokenCount": 99},
        }
        primary_response.raise_for_status.return_value = None
        primary_client = Mock(); primary_client.post.return_value = primary_response
        primary_context = Mock(); primary_context.__enter__ = Mock(return_value=primary_client); primary_context.__exit__ = Mock(return_value=False)

        invalid_location_response = Mock()
        invalid_location_response.headers = {"x-request-id": "safe-invalid-location"}
        invalid_location_response.json.return_value = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": "{}"}]},
            }]
        }
        invalid_location_response.raise_for_status.return_value = None
        location_client = Mock(); location_client.post.return_value = invalid_location_response
        location_context = Mock(); location_context.__enter__ = Mock(return_value=location_client); location_context.__exit__ = Mock(return_value=False)

        settings = ProviderSettings(
            provider="gemini",
            model="gemini-vision-test",
            api_key="secret-gemini-location-fallback",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="gemini-location-fallback-") as folder:
            page = Path(folder) / "page-1.png"
            page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", side_effect=[primary_context, location_context]):
                result = GeminiVisionProvider(settings).analyze(document={}, page_paths=[page])

        retained = next(item for item in result.draft.evidence if item.id == "ev-note-anodize")
        stage = result.metadata["localization_stage"]
        self.assertIsNone(retained.bbox)
        self.assertEqual("failed_safely", stage["status"])
        self.assertEqual(1, stage["target_count"])
        self.assertEqual(0, stage["accepted_count"])
        self.assertEqual("failed_safely", stage["calls"][0]["status"])
        self.assertEqual("ValidationError", stage["calls"][0]["failure_type"])
        self.assertEqual(99, result.metadata["usage"]["totalTokenCount"])
        self.assertNotIn(
            "secret-gemini-location-fallback",
            json.dumps(result.metadata),
        )

    def test_14a_kimi_location_failure_keeps_primary_review(self):
        draft = _generic_mock({}).model_copy(deep=True)
        evidence = next(item for item in draft.evidence if item.id == "ev-note-anodize")
        evidence.bbox = None

        primary_response = Mock()
        primary_response.headers = {"x-request-id": "safe-kimi-primary-fallback"}
        primary_response.json.return_value = {
            "id": "safe-kimi-primary-fallback-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": draft.model_dump_json()},
            }],
            "usage": {"prompt_tokens": 31, "completion_tokens": 68, "total_tokens": 99},
        }
        primary_response.raise_for_status.return_value = None
        primary_client = Mock(); primary_client.post.return_value = primary_response
        primary_context = Mock(); primary_context.__enter__ = Mock(return_value=primary_client); primary_context.__exit__ = Mock(return_value=False)

        invalid_location_response = Mock()
        invalid_location_response.headers = {"x-request-id": "safe-kimi-invalid-location"}
        invalid_location_response.json.return_value = {
            "id": "safe-kimi-invalid-location-id",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "{}"},
            }],
        }
        invalid_location_response.raise_for_status.return_value = None
        location_client = Mock(); location_client.post.return_value = invalid_location_response
        location_context = Mock(); location_context.__enter__ = Mock(return_value=location_client); location_context.__exit__ = Mock(return_value=False)

        settings = ProviderSettings(
            provider="kimi",
            model="k3",
            api_key="secret-kimi-location-fallback",
            api_base="https://api.kimi.com/coding/v1",
            reasoning_effort="high",
            source="session",
        )
        with tempfile.TemporaryDirectory(prefix="kimi-location-fallback-") as folder:
            page = Path(folder) / "page-1.png"
            page.write_bytes(b"\x89PNG\r\n\x1a\nclassroom")
            with patch("backend.providers.httpx.Client", side_effect=[primary_context, location_context]):
                result = KimiVisionProvider(settings).analyze(document={}, page_paths=[page])

        retained = next(item for item in result.draft.evidence if item.id == "ev-note-anodize")
        stage = result.metadata["localization_stage"]
        self.assertIsNone(retained.bbox)
        self.assertEqual("failed_safely", stage["status"])
        self.assertEqual(1, stage["target_count"])
        self.assertEqual(0, stage["accepted_count"])
        self.assertEqual("failed_safely", stage["calls"][0]["status"])
        self.assertEqual("ValidationError", stage["calls"][0]["failure_type"])
        self.assertEqual(99, result.metadata["usage"]["total_tokens"])
        self.assertNotIn(
            "secret-kimi-location-fallback",
            json.dumps(result.metadata),
        )

    def test_15_overlapping_report_markers_keep_every_problem_number(self):
        grouped = _group_page_markers([
            {"number": 1, "severity": "review", "bbox": [0.1, 0.2, 0.3, 0.4]},
            {"number": 2, "severity": "blocked", "bbox": [0.1, 0.2, 0.3, 0.4]},
            {"number": 3, "severity": "review", "bbox": [0.5, 0.6, 0.7, 0.8]},
        ])

        self.assertEqual(2, len(grouped))
        self.assertEqual([1, 2], grouped[0]["numbers"])
        self.assertEqual("blocked", grouped[0]["severity"])
        self.assertEqual([3], grouped[1]["numbers"])


if __name__ == "__main__": unittest.main()
