#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
# Somente biblioteca padrão: não instala projeto, dependências ou navegador.
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m sentinela.native_login "$@"
fi
exec python3 -m sentinela.native_login "$@"
