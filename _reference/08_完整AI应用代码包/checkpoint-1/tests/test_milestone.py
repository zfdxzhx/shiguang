from __future__ import annotations

import tempfile
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


class CheckpointOneMilestoneTests(unittest.TestCase):
    def test_pdf_and_api_foundation_exists_without_ai_routes(self):
        with tempfile.TemporaryDirectory(prefix="drawing-cp1-") as folder:
            with TestClient(create_application(Package, runtime_root=Path(folder) / "runtime")) as client:
                self.assertEqual(1, client.get("/api/v1/config").json()["milestone"])
                paths = {route.path for route in client.app.routes}
                self.assertIn("/api/v1/documents", paths)
                self.assertIn("/api/v1/ai/config", paths)
                self.assertFalse(any("features" in path for path in paths))

                upload = client.post(
                    "/api/v1/documents",
                    files={"file": ("classroom.pdf", minimal_pdf(), "application/pdf")},
                )
                self.assertEqual(201, upload.status_code, upload.text)
                document = upload.json()
                self.assertEqual(1, document["page_count"])
                self.assertNotIn("private_dir", document)
                self.assertTrue(client.get(document["page_urls"][0]).content.startswith(b"\x89PNG"))

                rejected = client.post(
                    "/api/v1/documents",
                    files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
                )
                self.assertEqual(422, rejected.status_code)

        source = (ROOT / "frontend" / "app" / "drawing-review-app.tsx").read_text(encoding="utf-8")
        self.assertIn("Gemini + DeepSeek（推荐）", source)
        self.assertIn("K3 + DeepSeek（国产备选）", source)
        self.assertIn("本检查点没有 AI 运行接口", source)
        self.assertNotIn("mock", source.lower())

        workflows = (ROOT / "backend" / "workflows.py").read_text(encoding="utf-8")
        self.assertIn("TODO CP3", workflows)
        self.assertNotIn("PROCESS_TEMPLATES", workflows)


if __name__ == "__main__":
    unittest.main()
