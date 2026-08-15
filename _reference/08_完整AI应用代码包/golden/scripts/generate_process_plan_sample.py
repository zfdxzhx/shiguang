#!/usr/bin/env python3
"""Generate a safe classroom ProcessPlan V2 PDF with the Mock provider."""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server  # noqa: E402
from backend.app import create_application  # noqa: E402


def classroom_drawing_pdf() -> bytes:
    """Create a synthetic drawing that visibly matches the Mock extraction fixture."""

    stream = io.BytesIO()
    page_width, page_height = landscape(A4)
    drawing = canvas.Canvas(stream, pagesize=(page_width, page_height), pageCompression=1)
    drawing.setTitle("Teaching Flange Bracket REV B")
    drawing.setLineWidth(0.8)
    drawing.rect(24, 24, page_width - 48, page_height - 48)

    # Main view: a 120 x 80 x 12 plate with four precision holes.
    left, bottom, width, height = 90, 175, 390, 260
    drawing.setLineWidth(1.4)
    drawing.roundRect(left, bottom, width, height, 18, stroke=1, fill=0)
    hole_centers = [
        (left + 55, bottom + 55),
        (left + width - 55, bottom + 55),
        (left + 55, bottom + height - 55),
        (left + width - 55, bottom + height - 55),
    ]
    for x_pos, y_pos in hole_centers:
        drawing.circle(x_pos, y_pos, 15, stroke=1, fill=0)
        drawing.line(x_pos - 24, y_pos, x_pos + 24, y_pos)
        drawing.line(x_pos, y_pos - 24, x_pos, y_pos + 24)

    drawing.setFont("Helvetica-Bold", 13)
    drawing.drawString(90, 470, "MAIN VIEW")
    drawing.setFont("Helvetica", 10)
    drawing.drawString(90, 150, "OVERALL 120 x 80 x 12 mm")
    drawing.drawString(90, 135, "4 x DIA 8 H7   HOLE PITCH 100 +/- 0.05")
    drawing.drawString(90, 120, "MIN WALL 3 mm   SURFACE Ra 3.2")
    drawing.drawString(90, 105, "POSITION DIA 0.10 | A | B   DATUM A FLATNESS 0.05")

    # Notes and title block align with the Mock evidence regions.
    notes_x = 530
    drawing.setFont("Helvetica-Bold", 11)
    drawing.drawString(notes_x, 448, "TECHNICAL NOTES")
    drawing.setFont("Helvetica", 9)
    notes = [
        "1. UNSPECIFIED TOLERANCE GB/T 1804-m",
        "2. MATERIAL: 6061-T6 ALUMINUM",
        "3. ANODIZE 15-20 um; MASKING AREA NOT SPECIFIED",
        "4. REMOVE BURRS AND SHARP EDGES",
        "5. KEY FEATURES TO BE INSPECTED; METHOD NOT SPECIFIED",
    ]
    for index, note in enumerate(notes):
        drawing.drawString(notes_x, 425 - index * 23, note)

    block_x, block_y, block_w, block_h = 500, 36, page_width - 536, 125
    drawing.rect(block_x, block_y, block_w, block_h)
    drawing.line(block_x, block_y + 35, block_x + block_w, block_y + 35)
    drawing.line(block_x, block_y + 70, block_x + block_w, block_y + 70)
    drawing.line(block_x + 145, block_y, block_x + 145, block_y + 70)
    drawing.setFont("Helvetica-Bold", 13)
    drawing.drawString(block_x + 10, block_y + 95, "TEACHING FLANGE BRACKET")
    drawing.setFont("Helvetica", 9)
    drawing.drawString(block_x + 10, block_y + 47, "MATERIAL: 6061-T6")
    drawing.drawString(block_x + 155, block_y + 47, "REV: B")
    drawing.drawString(block_x + 10, block_y + 12, "DRAWING: CLASSROOM-FB-001")
    drawing.drawString(block_x + 155, block_y + 12, "SCALE: 1:1")
    drawing.showPage()
    drawing.save()
    return stream.getvalue()


def wait_for_analysis(client: TestClient, analysis_id: str) -> dict:
    for _ in range(200):
        payload = client.get(f"/api/v1/analyses/{analysis_id}").json()
        if payload["technical_status"] in {"completed", "failed"}:
            if payload["technical_status"] == "failed":
                raise RuntimeError(payload.get("error") or "Mock analysis failed")
            return payload
        time.sleep(0.03)
    raise TimeoutError("Mock analysis did not finish")


def generate(destination: Path) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory(prefix="process-plan-sample-") as temp:
        app = create_application(server, runtime_root=Path(temp) / "runtime")
        with TestClient(app) as client:
            uploaded = client.post(
                "/api/v1/documents",
                files={"file": ("课堂样例_教学法兰支架_REV-B.pdf", classroom_drawing_pdf(), "application/pdf")},
            )
            uploaded.raise_for_status()
            created = client.post(
                "/api/v1/analyses",
                json={"document_id": uploaded.json()["id"], "mode": "mock"},
            )
            created.raise_for_status()
            analysis = wait_for_analysis(client, created.json()["id"])
            for finding_id in analysis["rules"]["required_decision_ids"]:
                reviewed = client.patch(
                    f"/api/v1/reviews/{analysis['id']}/findings/{finding_id}",
                    json={"decision": "confirmed", "reviewer": "课堂工艺小组"},
                )
                reviewed.raise_for_status()
            finalized = client.post(
                f"/api/v1/reviews/{analysis['id']}/finalize",
                json={
                    "reviewer": "课堂工艺小组",
                    "reviewer_role": "图纸复核人",
                    "note": "仅用于培训演示，不代表真实工程放行。",
                    "acknowledgement": True,
                },
            )
            finalized.raise_for_status()
            planned = client.post(
                f"/api/v1/analyses/{analysis['id']}/process-plan",
                json={
                    "manufacturing_family": "cnc_machining",
                    "quantity": 100,
                    "material_form": "6061-T6 铝合金板料，厚 12 mm（课堂输入）",
                    "equipment_capability": "三轴加工中心、平口钳/软爪夹具、常用铣削和孔加工刀具",
                    "inspection_capability": "卡尺、千分尺、三坐标和粗糙度仪；关键尺寸可首件全检",
                    "special_requirements": "关键尺寸首件全检；锐边去毛刺；全程保留批次追溯",
                },
            )
            planned.raise_for_status()
            confirmed = client.post(
                f"/api/v1/analyses/{analysis['id']}/process-plan/confirm",
                json={
                    "reviewer": "课堂工艺小组",
                    "reviewer_role": "工艺复核人",
                    "note": "已核对教学路线；实际刀具、工装、参数和节拍仍需现场试制确认。",
                    "route_checked": True,
                    "equipment_checked": True,
                    "quality_checked": True,
                    "acknowledgement": True,
                },
            )
            confirmed.raise_for_status()
            exported = client.get(f"/api/v1/analyses/{analysis['id']}/export?format=process-pdf")
            exported.raise_for_status()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(exported.content)
            return analysis["id"], confirmed.json()["process_plan"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "output" / "pdf" / "工艺路线验收_2026-08-10" / "加工工艺路线卡_教学法兰支架.pdf",
    )
    args = parser.parse_args()
    analysis_id, plan = generate(args.output.resolve())
    print(f"analysis_id={analysis_id}")
    print(f"process_plan_version={plan['schema_version']}")
    print(f"steps={len(plan['steps'])}")
    print(f"risks={len(plan['risks'])}")
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
