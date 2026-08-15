#!/usr/bin/env python3
"""Verify the five real course snapshots without calling an external model."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = {
    "starter": 0,
    "checkpoint-1": 1,
    "checkpoint-2": 2,
    "checkpoint-3": 3,
    "golden": 3,
}
REQUIRED_BUNDLE_COPY = {
    "starter": ("三个功能，彼此独立", "PDF 与 AI 功能尚未实现"),
    "checkpoint-1": (
        "先安全接入图纸，再配置统一 API",
        "Gemini + DeepSeek（推荐）",
        "K3 + DeepSeek（国产备选）",
        "本检查点没有 AI 运行接口",
    ),
    "checkpoint-2": (
        "独立 AI 审核",
        "上传后直接生成图纸 AI 审核报告",
        "工艺路线和报价尚未注册产品路由",
    ),
    "checkpoint-3": (
        "三个功能，彼此独立",
        "开始 AI 审核",
        "生成工艺路线",
        "生成参考报价",
    ),
    "golden": (
        "三个功能，彼此独立",
        "开始 AI 审核",
        "生成工艺路线",
        "生成参考报价",
        "确定性公式生成课堂参考报价",
    ),
}
FORBIDDEN_PRODUCT_COPY = (
    "AI 运行模式",
    "Mock / 回放",
    "本地 OCR",
    "等待人工确认",
    "确认工艺路线，进入预报价",
)
FORBIDDEN_NAMES = {".env.local", ".git", "node_modules", "__pycache__", "private"}
FORBIDDEN_SUFFIXES = {".db", ".pdf", ".pyc", ".pyo", ".sqlite", ".sqlite3"}
TEXT_SUFFIXES = {
    "", ".command", ".css", ".example", ".html", ".js", ".json", ".md",
    ".mjs", ".py", ".svg", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"sk-(?:proj-)?[0-9A-Za-z_-]{20,}"),
)
PERSONAL_HOME = "/" + "Users" + "/" + "yangnengkun"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_package() -> None:
    """Reject private data, real drawings, machine paths, symlinks, and likely secrets."""

    problems: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if path.is_symlink():
            problems.append(f"symlink: {relative}")
            continue
        if any(part in FORBIDDEN_NAMES or part == "__MACOSX" or part.startswith("._") for part in relative.parts):
            problems.append(f"forbidden path: {relative}")
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"forbidden artifact: {relative}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PERSONAL_HOME in content:
            problems.append(f"personal absolute path: {relative}")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            problems.append(f"possible API key: {relative}")
    require(not problems, "package hygiene failed:\n" + "\n".join(problems))


def verify_snapshot_structure(name: str, milestone: int) -> None:
    snapshot = ROOT / name
    require(snapshot.is_dir(), f"missing snapshot: {name}")
    for relative in (
        "AGENTS.md", "README.md", "课堂进度.md", "server.py",
        "backend/app.py", "backend/course_stage.py", "frontend/app/drawing-review-app.tsx",
        "frontend/dist-static/index.html", "tests/test_milestone.py",
    ):
        require((snapshot / relative).is_file(), f"{name}: missing {relative}")

    stage_source = (snapshot / "backend" / "course_stage.py").read_text(encoding="utf-8")
    match = re.search(r"^MILESTONE\s*=\s*(\d+)\s*$", stage_source, re.MULTILINE)
    require(match is not None and int(match.group(1)) == milestone, f"{name}: wrong MILESTONE")

    server_source = (snapshot / "server.py").read_text(encoding="utf-8")
    for stage_id in ("A", "B1", "B2", "B3", "C1", "C2", "C3", "C4", "D1", "D2", "D3"):
        require(f'"id": "{stage_id}"' in server_source, f"{name}: missing development stage {stage_id}")
    for official_id in ("18", "19", "20", "21", "22", "23A", "23B", "23C", "23D", "23E", "23F", "24", "30"):
        require(f'"id": "{official_id}"' not in server_source, f"{name}: reused official course id {official_id}")

    scripts = sorted((snapshot / "frontend" / "dist-static").rglob("*.js"))
    styles = sorted((snapshot / "frontend" / "dist-static").rglob("*.css"))
    require(bool(scripts) and bool(styles), f"{name}: missing static bundle")
    bundle = "\n".join(path.read_text(encoding="utf-8") for path in scripts)
    for text in REQUIRED_BUNDLE_COPY[name]:
        require(text in bundle, f"{name}: product copy missing: {text}")
    for text in FORBIDDEN_PRODUCT_COPY:
        require(text not in bundle, f"{name}: forbidden product copy found: {text}")


def verify_real_progression() -> None:
    """Prove that checkpoints change executable code, not only a milestone flag."""

    versions = list(SNAPSHOTS)
    app_hashes = [sha(ROOT / name / "backend" / "app.py") for name in versions]
    frontend_hashes = [sha(ROOT / name / "frontend" / "app" / "drawing-review-app.tsx") for name in versions]
    require(len(set(app_hashes[:4])) == 4, "Starter through CP3 must have four real backend route snapshots")
    require(len(set(frontend_hashes)) == 5, "all five snapshots must have distinct frontend source")

    starter_intake = (ROOT / "starter" / "backend" / "intake.py").read_text(encoding="utf-8")
    cp1_intake = (ROOT / "checkpoint-1" / "backend" / "intake.py").read_text(encoding="utf-8")
    require("TODO CP1" in starter_intake, "Starter must expose the real PDF exercise")
    require("TODO CP1" not in cp1_intake and "def ingest" in cp1_intake, "CP1 must implement PDF intake")

    for name in ("starter", "checkpoint-1", "checkpoint-2"):
        workflow = (ROOT / name / "backend" / "workflows.py").read_text(encoding="utf-8")
        require("TODO CP3" in workflow and "PROCESS_TEMPLATES" not in workflow, f"{name}: future workflows leaked")
    for name in ("checkpoint-3", "golden"):
        workflow = (ROOT / name / "backend" / "workflows.py").read_text(encoding="utf-8")
        require("PROCESS_TEMPLATES" in workflow and "def build_prequote" in workflow, f"{name}: complete workflows missing")

    require(
        sha(ROOT / "checkpoint-3" / "backend" / "pdf_report.py")
        != sha(ROOT / "golden" / "backend" / "pdf_report.py"),
        "Golden must contain the final report improvement beyond CP3",
    )


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    completed = subprocess.run(
        command, cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    print(completed.stdout, end="")
    require(completed.returncode == 0, f"command failed in {cwd.name}: {' '.join(command)}")


def verify_snapshot_runtime(name: str, runtime_root: Path) -> None:
    snapshot = ROOT / name
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["DRAWING_REVIEW_RUNTIME"] = str(runtime_root / name)
    env.pop("AI_PROVIDER", None)
    for key in ("GEMINI_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY"):
        env.pop(key, None)

    print(f"\n== {name}: tests ==")
    run([sys.executable, "-B", "-m", "unittest", "-v", "tests.test_milestone"], snapshot, env)
    print(f"== {name}: server self-check ==")
    run([sys.executable, "-B", "server.py", "--check"], snapshot, env)


def main() -> int:
    print(f"package={ROOT.name}")
    scan_package()
    for name, milestone in SNAPSHOTS.items():
        verify_snapshot_structure(name, milestone)
    verify_real_progression()

    with tempfile.TemporaryDirectory(prefix="drawing-ai-package-check-") as folder:
        runtime_root = Path(folder)
        for name in SNAPSHOTS:
            verify_snapshot_runtime(name, runtime_root)

    scan_package()
    print("\nversions=5")
    print("real_code_progression=PASS")
    print("live_network_calls=0")
    print("package_verification=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
