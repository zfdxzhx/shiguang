from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_application


ROOT = Path(__file__).resolve().parents[1]


class Package:
    HERE = ROOT
    PACKAGE_ROOT = ROOT
    FRONTEND_DIST = ROOT / "frontend" / "dist-static"


def minimal_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R >>",
    ]
    stream = b"q 0 0 0 rg 20 20 100 100 re S Q"
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, payload in enumerate(objects, 1):
        offsets.append(len(output)); output.extend(f"{index} 0 obj\n".encode() + payload + b"\nendobj\n")
    xref = len(output); output.extend(f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets[1:]: output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def wait_for(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        payload = client.get(f"/api/v1/features/runs/{run_id}").json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("review run timed out")


class CheckpointTwoMilestoneTests(unittest.TestCase):
    def test_review_is_real_while_process_and_quote_are_absent(self):
        with tempfile.TemporaryDirectory(prefix="drawing-cp2-") as folder:
            with TestClient(create_application(Package, runtime_root=Path(folder) / "runtime")) as client:
                self.assertEqual(2, client.get("/api/v1/config").json()["milestone"])
                paths = {route.path for route in client.app.routes}
                self.assertIn("/api/v1/features/review/runs", paths)
                self.assertNotIn("/api/v1/features/{feature}/runs", paths)

                upload = client.post(
                    "/api/v1/documents",
                    files={"file": ("classroom.pdf", minimal_pdf(), "application/pdf")},
                ).json()
                created = client.post(
                    "/api/v1/features/review/runs",
                    json={"document_id": upload["id"], "mode": "mock"},
                )
                self.assertEqual(202, created.status_code, created.text)
                result = wait_for(client, created.json()["id"])
                self.assertEqual("completed", result["status"], result)
                self.assertEqual("review_report", result["output"]["kind"])
                report = client.get(result["output"]["report_url"])
                self.assertEqual("application/pdf", report.headers["content-type"])
                self.assertTrue(report.content.startswith(b"%PDF-"))

                for feature in ("process", "quote"):
                    missing = client.post(
                        f"/api/v1/features/{feature}/runs",
                        json={"document_id": upload["id"], "mode": "mock"},
                    )
                    self.assertEqual(405, missing.status_code)

        source = (ROOT / "frontend" / "app" / "drawing-review-app.tsx").read_text(encoding="utf-8")
        self.assertIn("上传后直接生成图纸 AI 审核报告", source)
        self.assertIn("工艺路线和报价尚未注册产品路由", source)
        self.assertNotIn("Mock", source)

        workflows = (ROOT / "backend" / "workflows.py").read_text(encoding="utf-8")
        self.assertIn("TODO CP3", workflows)
        self.assertNotIn("PROCESS_TEMPLATES", workflows)


if __name__ == "__main__":
    unittest.main()
