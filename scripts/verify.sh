#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
python3 scripts/verify_core.py
python3 scripts/secret_scan.py
git diff --check
git diff --cached --check
