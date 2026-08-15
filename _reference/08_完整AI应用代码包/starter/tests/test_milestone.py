from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_application
from backend.intake import IntakeError, PdfIntake


ROOT = Path(__file__).resolve().parents[1]


class Package:
    HERE = ROOT
    PACKAGE_ROOT = ROOT
    FRONTEND_DIST = ROOT / "frontend" / "dist-static"


class StarterMilestoneTests(unittest.TestCase):
    def test_product_shell_has_no_future_product_routes(self):
        with tempfile.TemporaryDirectory(prefix="drawing-starter-") as folder:
            with TestClient(create_application(Package, runtime_root=Path(folder) / "runtime")) as client:
                self.assertEqual(0, client.get("/api/v1/config").json()["milestone"])
                paths = {route.path for route in client.app.routes}
                self.assertNotIn("/api/v1/documents", paths)
                self.assertFalse(any("features" in path for path in paths))
                intake = PdfIntake(Path(folder) / "documents")
                with self.assertRaisesRegex(IntakeError, "TODO CP1"):
                    intake.ingest(stream=None, filename=None)  # type: ignore[arg-type]

        source = (ROOT / "frontend" / "app" / "drawing-review-app.tsx").read_text(encoding="utf-8")
        self.assertIn("三个功能，彼此独立", source)
        self.assertIn("PDF 与 AI 功能尚未实现", source)
        self.assertNotIn("mode", source.lower())
        self.assertNotIn("mock", source.lower())

        workflows = (ROOT / "backend" / "workflows.py").read_text(encoding="utf-8")
        self.assertIn("TODO CP3", workflows)
        self.assertNotIn("PROCESS_TEMPLATES", workflows)


if __name__ == "__main__":
    unittest.main()
