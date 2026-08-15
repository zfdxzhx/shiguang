#!/bin/zsh
set -e
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"
python3 server.py --open
