#!/usr/bin/env python3
"""Read-only classroom MCP server; no network and no arbitrary file access."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())).resolve()


def course_status() -> dict:
    root = project_root()
    stage_file = root / "backend" / "course_stage.py"
    milestone = "unknown"
    if stage_file.is_file():
        match = re.search(r"MILESTONE\s*=\s*(\d+)", stage_file.read_text(encoding="utf-8"))
        if match:
            milestone = match.group(1)
    return {
        "snapshot": root.name,
        "milestone": milestone,
        "tests": "python3 -m unittest discover -s tests -v",
        "check": "python3 server.py --check",
        "boundary": "No real PDF, API key, runtime/private data, or network access is exposed by this MCP server.",
    }


def evidence_contract() -> dict:
    return {
        "source": ["document_id", "sha256", "page_count"],
        "finding": ["code", "page", "region", "description", "confidence"],
        "states": ["technical_status", "business_status", "human_status"],
        "gate": "required_decision_ids must be empty before finalized",
    }


TOOLS = [
    {
        "name": "course_project_status",
        "description": "Return the current classroom snapshot, safe verification commands, and data boundary.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "course_evidence_contract",
        "description": "Return the minimum evidence contract used by the drawing review application.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def tool_result(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}], "isError": False}


def handle(message: dict) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion", "2024-11-05")
        result = {
            "protocolVersion": requested,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "course-project", "version": "1.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = (message.get("params") or {}).get("name")
        if name == "course_project_status":
            result = tool_result(course_status())
        elif name == "course_evidence_contract":
            result = tool_result(evidence_contract())
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unknown tool"}}
    elif method in {"resources/list", "prompts/list"}:
        result = {"resources" if method.startswith("resources") else "prompts": []}
    elif method and method.startswith("notifications/"):
        return None
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def self_test() -> int:
    assert handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"] == TOOLS
    status = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "course_project_status", "arguments": {}}})
    assert status and status["result"]["isError"] is False
    print("MCP SELF-TEST PASS tools=2 network_calls=0")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            response = handle(json.loads(raw))
        except Exception as exc:  # fail closed without leaking paths
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": f"server error: {type(exc).__name__}"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
