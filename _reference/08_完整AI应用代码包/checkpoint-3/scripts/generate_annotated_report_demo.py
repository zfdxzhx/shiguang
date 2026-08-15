"""Generate a non-sensitive annotated report for visual QA and classroom demo."""

from __future__ import annotations

import io
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

import server
from backend.app import create_application


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "图纸工程审核标注报告_教学样例.pdf"


def synthetic_drawing() -> bytes:
    """Create a landscape teaching drawing aligned with the mock evidence boxes."""

    buffer = io.BytesIO()
    width, height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle("Non-sensitive classroom bracket drawing")
    pdf.setLineWidth(0.8)
    pdf.rect(16, 16, width - 32, height - 32)

    # Main view: a simple flanged bracket with holes and dimension lines.
    pdf.setLineWidth(2)
    pdf.rect(90, 235, 330, 190)
    pdf.rect(150, 295, 210, 70)
    for x, y in ((125, 270), (385, 270), (125, 390), (385, 390)):
        pdf.circle(x, y, 13, stroke=1, fill=0)
        pdf.line(x - 20, y, x + 20, y)
        pdf.line(x, y - 20, x, y + 20)
    pdf.setLineWidth(0.7)
    pdf.line(90, 450, 420, 450)
    pdf.line(90, 442, 90, 458)
    pdf.line(420, 442, 420, 458)
    pdf.drawCentredString(255, 458, "120 +/- 0.10")
    pdf.line(65, 235, 65, 425)
    pdf.line(57, 235, 73, 235)
    pdf.line(57, 425, 73, 425)
    pdf.saveState()
    pdf.translate(48, 330)
    pdf.rotate(90)
    pdf.drawCentredString(0, 0, "80 +/- 0.10")
    pdf.restoreState()
    pdf.drawString(195, 330, "4 x DIA 8 H7")
    pdf.drawString(210, 312, "POSITION DIA 0.10 | A | B")

    # Notes and title block occupy the same normalized areas used by MockProvider.
    pdf.setFont("Helvetica", 8)
    notes_x = 575
    notes_y = 440
    notes = [
        "TECHNICAL REQUIREMENTS",
        "1. MATERIAL: 6061-T6",
        "2. GENERAL TOLERANCE: GB/T 1804-m",
        "3. ANODIZE 15-20 um",
        "4. MACHINED SURFACE Ra 3.2",
        "5. CRITICAL FEATURES REQUIRE INSPECTION",
    ]
    for index, line in enumerate(notes):
        pdf.drawString(notes_x, notes_y - index * 18, line)

    title_x, title_y, title_w, title_h = 575, 28, 235, 135
    pdf.rect(title_x, title_y, title_w, title_h)
    pdf.line(title_x, title_y + 45, title_x + title_w, title_y + 45)
    pdf.line(title_x, title_y + 90, title_x + title_w, title_y + 90)
    pdf.line(title_x + 150, title_y, title_x + 150, title_y + title_h)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(title_x + 8, title_y + 108, "FLANGE BRACKET")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(title_x + 8, title_y + 68, "MATERIAL: 6061-T6")
    pdf.drawString(title_x + 8, title_y + 22, "DRAWING: TRAINING-001")
    pdf.drawString(title_x + 158, title_y + 68, "REV B")
    pdf.drawString(title_x + 158, title_y + 22, "SHEET 1/1")
    pdf.drawString(20, 5, "NON-SENSITIVE CLASSROOM FIXTURE - NOT FOR PRODUCTION")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="annotated-report-demo-") as folder:
        app = create_application(server, runtime_root=Path(folder) / "runtime")
        with TestClient(app) as client:
            uploaded = client.post(
                "/api/v1/documents",
                files={"file": ("teaching-flange-bracket.pdf", synthetic_drawing(), "application/pdf")},
            )
            uploaded.raise_for_status()
            created = client.post(
                "/api/v1/analyses",
                json={"document_id": uploaded.json()["id"], "mode": "mock"},
            )
            created.raise_for_status()
            analysis_id = created.json()["id"]
            for _ in range(100):
                result = client.get(f"/api/v1/analyses/{analysis_id}")
                result.raise_for_status()
                if result.json()["technical_status"] == "completed":
                    break
                time.sleep(0.03)
            else:
                raise RuntimeError("mock analysis did not complete")
            report = client.get(f"/api/v1/analyses/{analysis_id}/export?format=pdf-draft")
            report.raise_for_status()
            OUTPUT.write_bytes(report.content)
    print(OUTPUT)


if __name__ == "__main__":
    main()
