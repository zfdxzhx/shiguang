#!/usr/bin/env python3
"""Standalone local course application; no real PDFs or legacy answers are bundled."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE
FRONTEND_DIST = HERE / "frontend" / "dist-static"


class ApiProblem(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def load_local_environment() -> None:
    path = HERE / ".env.local"
    if not path.is_file():
        return
    allowed = {
        "AI_PROVIDER", "KIMI_API_KEY", "KIMI_MODEL", "KIMI_API_BASE",
        "OPENAI_API_KEY", "OPENAI_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
    }
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key in allowed:
            os.environ.setdefault(key, value.strip("\"'"))


load_local_environment()


STAGE_DEFINITIONS = (
    {
        "id": "A", "module": "阶段 A · 读懂产品骨架", "duration_minutes": 60,
        "hands_on_minutes": 35, "theory": ["项目三层记忆", "AGENTS.md", "/init", "安全边界"],
        "practice": "读懂 Starter、明确三个独立功能，只处理当前产品骨架任务。",
        "student_edits": ["AGENTS.md", "CLAUDE.md", "README.md"],
        "acceptance": "能复述产品合同、启动命令、资料边界和停止条件。",
        "hints": ["先读再改", "本轮不发送真实图纸", "不要复制 Golden"],
    },
    {
        "id": "B1", "module": "阶段 B · MCP 与本地服务", "duration_minutes": 30,
        "hands_on_minutes": 20, "theory": ["MCP", "最小权限", "localhost 服务"],
        "practice": "完成本地服务与只读项目工具自检。",
        "student_edits": [".mcp.json", "tools/classroom_mcp.py", "backend/app.py"],
        "acceptance": "服务只绑定本机，工具自检不访问网络。",
        "hints": ["MCP 不放密钥", "只开放必要动作"],
    },
    {
        "id": "B2", "module": "阶段 B · Skill 与结构化合同", "duration_minutes": 30,
        "hands_on_minutes": 20, "theory": ["Skill", "领域 SOP", "结构化输出"],
        "practice": "把图纸证据工作法封装为 Skill，明确输入、输出和拒绝条件。",
        "student_edits": [".claude/skills/drawing-evidence/SKILL.md", "backend/models.py"],
        "acceptance": "Skill 可触发，未知字段和空证据会被拒绝。",
        "hints": ["Skill 写工作法", "不写密钥和答案"],
    },
    {
        "id": "B3", "module": "阶段 B · PDF 接入与 Git 资产", "duration_minutes": 30,
        "hands_on_minutes": 25, "theory": ["PDF 校验", "SHA256", "私有分页", "Git"],
        "practice": "完成 PDF 校验、哈希、本地分页和统一 API 设置。",
        "student_edits": ["backend/intake.py", "backend/app.py", "frontend/app/drawing-review-app.tsx"],
        "acceptance": "CP1 测试通过；原 PDF、分页图和 Key 不进入 Git。",
        "hints": ["先验 PDF 头", "绝对路径不回传", "只显示两种 AI 组合"],
    },
    {
        "id": "C1", "module": "阶段 C · 角色分工与验收", "duration_minutes": 12,
        "hands_on_minutes": 5, "theory": ["目标", "测试", "独立审查", "人工责任"],
        "practice": "写清本轮唯一目标、允许修改文件、测试和停止条件。",
        "student_edits": ["AGENTS.md", "CODEX_REVIEW.md"],
        "acceptance": "实现、审查和业务判断三种责任不混写。",
        "hints": ["先写验收", "修复后重跑同一命令"],
    },
    {
        "id": "C2", "module": "阶段 C · Provider", "duration_minutes": 20,
        "hands_on_minutes": 15, "theory": ["Gemini＋DeepSeek", "K3＋DeepSeek", "最小化复核"],
        "practice": "接入一套默认方案和一套国产备选，产品 UI 不出现测试夹具。",
        "student_edits": ["backend/providers.py", ".env.example"],
        "acceptance": "Key 不回显；分页图只有当次授权后才外发。",
        "hints": ["DeepSeek 不接收图片", "本地 OCR 不是依赖"],
    },
    {
        "id": "C3", "module": "阶段 C · 严格契约", "duration_minutes": 18,
        "hands_on_minutes": 13, "theory": ["ReviewDraftV2", "fail-closed", "证据 ID"],
        "practice": "验证模型 JSON，拒绝额外字段、空证据和非法坐标。",
        "student_edits": ["backend/models.py", "backend/providers.py"],
        "acceptance": "弱坐标被拒绝，不猜框；错误输出不能伪造成结论。",
        "hints": ["先定契约", "模型输出是不可信输入"],
    },
    {
        "id": "C4", "module": "阶段 C · 独立 AI 审核", "duration_minutes": 22,
        "hands_on_minutes": 17, "theory": ["独立入口", "证据定位", "直接报告"],
        "practice": "从新 PDF 运行 AI 审核，完成后直接生成 PDF 报告。",
        "student_edits": ["backend/app.py", "backend/service.py", "backend/pdf_report.py"],
        "acceptance": "CP2 审核路径通过；产品内没有人工定稿页。",
        "hints": ["报告不是正式工程批准", "保留识别边界"],
    },
    {
        "id": "D1", "module": "阶段 D · 独立工艺路线", "duration_minutes": 22,
        "hands_on_minutes": 17, "theory": ["图纸事实", "参考数据包", "工序模板"],
        "practice": "从新 PDF 匹配带来源的课堂资料并生成工艺路线卡。",
        "student_edits": ["backend/reference_profiles.py", "backend/workflows.py", "backend/process_plan_pdf.py"],
        "acceptance": "不依赖审核历史；路线不冒充 NC 程序或投产参数。",
        "hints": ["来源和访问日期可查", "课堂假设明确显示"],
    },
    {
        "id": "D2", "module": "阶段 D · 独立报价", "duration_minutes": 18,
        "hands_on_minutes": 14, "theory": ["参考成本", "确定性公式", "可复算"],
        "practice": "从新 PDF 自动补齐课堂参数，由代码公式生成参考报价单。",
        "student_edits": ["backend/workflows.py", "backend/quote_report.py"],
        "acceptance": "金额可手工复算；不冒充正式报价或商务要约。",
        "hints": ["AI 不猜总价", "企业使用前替换课堂费率"],
    },
    {
        "id": "D3", "module": "阶段 D · 前端、历史、报告与验收", "duration_minutes": 23,
        "hands_on_minutes": 18, "theory": ["三入口", "统一历史", "三份 PDF", "端到端验收"],
        "practice": "完成三个独立入口、结果页、历史和三类 PDF，并重跑全量检查。",
        "student_edits": ["frontend/app/drawing-review-app.tsx", "backend/database.py", "tests/test_milestone.py"],
        "acceptance": "CP3 三条路径均从新 PDF 独立运行，四条固定门禁全绿。",
        "hints": ["产品 UI 不出现 Mock", "真实调用与离线测试分开记录"],
    },
)

STAGE_TO_MILESTONE = {
    "A": 0,
    "B1": 1, "B2": 1, "B3": 1,
    "C1": 2, "C2": 2, "C3": 2, "C4": 2,
    "D1": 3, "D2": 3, "D3": 3,
}


def _package_workspace() -> dict:
    return {
        "id": "code-package", "group": f"{HERE.name} 快照", "accepted": False,
        "contains_golden_answer": HERE.name == "golden", "contains_real_pdf": False,
    }


def bootstrap_payload() -> dict:
    return {
        "app": {"title": "图纸 AI 工程助手", "mode": "course-package", "course": "Claude Code + Codex 完整 AI 应用", "learning_loop": ["需求", "Claude Code 实现", "Codex 审查", "人工验收"]},
        "stages": list(STAGE_DEFINITIONS), "sources": [], "workspaces": [_package_workspace()],
        "real_demo": {"source_id": "NONE", "intake_ready": False, "pages": 0, "rendered_pages": 0, "network_used": False, "source_pdf_copied": False, "status": "pending", "findings": [], "human_gate": "课堂包不含真实资料", "boundary": "请仅使用已授权的课堂 PDF"},
        "safety": {"localhost_only": True, "serves_real_pdf": False, "serves_rendered_pages": False, "serves_golden_source": False},
    }


def _stage_result(stage: dict) -> dict:
    milestone = STAGE_TO_MILESTONE[stage["id"]]
    passed = milestone <= MILESTONE
    message = (
        f"{HERE.name} 已包含该开发切片；请用本版本测试复核。"
        if passed else f"该切片从 checkpoint-{milestone} 开始提供；当前是 {HERE.name}。"
    )
    return {
        "id": stage["id"], "passed": passed, "module": stage["module"],
        "checks": [{"passed": passed, "message": message}],
    }


def create_workspace(group: str):
    del group
    return _package_workspace(), "独立课堂代码包使用当前快照，不会复制 Golden 或真实 PDF。"


def run_course_action(workspace_id: str, action: str, stage_id: str | None = None) -> dict:
    if workspace_id != "code-package":
        raise ApiProblem("未知的代码包快照", 404)
    if action not in {"doctor", "status", "next", "accept", "check"}:
        raise ApiProblem("未知的开发指引动作", 400)
    results = [_stage_result(stage) for stage in STAGE_DEFINITIONS]
    if action == "check":
        results = [item for item in results if item["id"] == stage_id]
        if not results:
            raise ApiProblem("未知的开发切片", 404)
    elif action == "next":
        results = [item for item in results if not item["passed"]][:1]
    ok = bool(results) and all(item["passed"] for item in results)
    if action == "accept":
        ok = all(item["passed"] for item in results)
    passed_count = sum(1 for item in results if item["passed"])
    return {
        "ok": ok, "exit_code": 0 if ok else 1, "action": action,
        "workspace_id": workspace_id, "stages": results,
        "summary": {"green": passed_count, "red": len(results) - passed_count, "total": len(results)},
        "output": f"snapshot={HERE.name} milestone={MILESTONE}; 请运行 python3 -m unittest discover -s tests -v 做真实代码验收。",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def run_instructor_action(action: str) -> dict:
    return {
        "ok": True, "exit_code": 0, "action": action, "title": "代码包检查",
        "summary": "独立包不包含真实图纸预跑；课堂只使用脱敏样件 A / B / C。",
        "output": f"snapshot={HERE.name}; live_network_calls=0",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "boundary": "只有明确授权后，实时模式才发送渲染页。",
    }


def safe_workspace_path(workspace_id: str) -> Path:
    if workspace_id != "code-package":
        raise ApiProblem("未知的代码包快照", 404)
    return HERE


from backend.app import backend_self_check, create_application
from backend.credential_store import MacOSKeychainProviderStore
from backend.course_stage import MILESTONE

runtime = Path(os.environ["DRAWING_REVIEW_RUNTIME"]).resolve() if os.environ.get("DRAWING_REVIEW_RUNTIME") else HERE / "runtime"
app = create_application(
    sys.modules[__name__],
    runtime_root=runtime,
    credential_store=MacOSKeychainProviderStore(),
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="完整 AI 应用课堂代码包")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        frontend_ok = (FRONTEND_DIST / "index.html").is_file()
        print(f"{'PASS' if frontend_ok else 'FAIL'} 预构建前端")
        return max(0 if frontend_ok else 1, backend_self_check(app))
    import uvicorn
    url = f"http://127.0.0.1:{args.port}/"
    print(f"本地完整 AI 应用：{url}")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
