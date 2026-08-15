"""图纸 AI 审核助手｜本地启动与自检入口。

仅绑定本机（127.0.0.1 / localhost），不做任何外网开放。
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

import uvicorn

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="图纸 AI 审核助手")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="仅运行自检，不启动服务")
    args = parser.parse_args(argv)

    from backend.app import backend_self_check, create_application

    if args.check:
        ok = backend_self_check()
        return 0 if ok else 1

    app = create_application(static_dir=STATIC_DIR)
    url = f"http://{args.host}:{args.port}/"
    print(f"图纸 AI 审核助手：{url}")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
