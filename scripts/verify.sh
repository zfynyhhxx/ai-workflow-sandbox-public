#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s tests -q
python3 -m unittest discover -s vendor/starter/tests -q
python3 - <<'PY'
import ast,json
from pathlib import Path
for folder in ('workflowkit','tests','demo','scripts'):
    for path in Path(folder).glob('*.py'):
        ast.parse(path.read_text(),filename=str(path))
for folder in ('config','.workflow'):
    for path in Path(folder).glob('*.json'):
        json.loads(path.read_text())
PY
python3 scripts/secret_scan.py
git diff --check
