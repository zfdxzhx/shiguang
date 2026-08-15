#!/bin/zsh
set -e
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/verify_package.py
