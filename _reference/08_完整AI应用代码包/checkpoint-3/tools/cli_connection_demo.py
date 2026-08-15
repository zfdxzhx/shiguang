#!/usr/bin/env python3
"""Offline CLI contract demo for Feishu, DingTalk and WeCom course paths."""

from __future__ import annotations

import argparse
import json
import os


PROVIDERS = {
    "feishu": {"credential_env": "FEISHU_APP_SECRET", "scope": "message:send", "transport": "app-api"},
    "dingtalk": {"credential_env": "DINGTALK_APP_SECRET", "scope": "robot:message:send", "transport": "robot-api"},
    "wecom": {"credential_env": "WECOM_APP_SECRET", "scope": "message:send", "transport": "app-api"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--provider", choices=sorted(PROVIDERS))
    args = parser.parse_args()
    if args.self_test:
        assert tuple(sorted(PROVIDERS)) == ("dingtalk", "feishu", "wecom")
        assert all(item["credential_env"].endswith("_APP_SECRET") for item in PROVIDERS.values())
        print("CLI SELF-TEST PASS providers=dingtalk,feishu,wecom network_calls=0 secrets_echoed=0")
        return 0
    if not args.provider:
        parser.error("--provider is required unless --self-test is used")
    contract = PROVIDERS[args.provider]
    credential_present = bool(os.environ.get(contract["credential_env"]))
    print(json.dumps({
        "provider": args.provider,
        "transport": contract["transport"],
        "required_scope": contract["scope"],
        "credential_present": credential_present,
        "status": "ready_for_authorized_live" if credential_present else "blocked_missing_credentials",
        "network_calls": 0,
        "next_step": "obtain explicit approval before any live send",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
