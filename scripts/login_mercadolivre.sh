#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ ! -x .venv/bin/python ]]; then
  python -m venv .venv
fi
.venv/bin/python -m pip install -e '.[marketplace]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m sentinela.cli --ml-login
