"""加载本地 .env.local（仅白名单键），密钥只留在进程环境变量中。

密钥不进入代码、数据库、日志、前端或 Git。
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

ALLOWED_KEYS = {
    "AI_PROVIDER",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "KIMI_API_KEY",
    "KIMI_MODEL",
    "KIMI_API_BASE",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
}


def load_local_environment() -> None:
    path = HERE / ".env.local"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key in ALLOWED_KEYS:
            os.environ.setdefault(key, value)
