#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ ! -x .venv/bin/python ]]; then
  python -m venv .venv
fi
# Login não precisa reinstalar o projeto: a execução abaixo ocorre na raiz.
if ! .venv/bin/python -c 'import requests, bs4, dotenv, playwright.sync_api' >/dev/null 2>&1; then
  if ! .venv/bin/python -c 'import socket; socket.getaddrinfo("pypi.org", 443)' >/dev/null 2>&1; then
    echo 'Sem acesso DNS ao pypi.org. Restabeleça a conexão antes de instalar as dependências.' >&2
    exit 1
  fi
  .venv/bin/python -m pip install -e '.[marketplace]'
fi
ML_BROWSER_CHOICE="$(.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env"); from sentinela.mercadolivre import browser_name; print(browser_name())')"
echo "Preparando navegador do Sentinela: $ML_BROWSER_CHOICE"
.venv/bin/python -m playwright install "$ML_BROWSER_CHOICE"
.venv/bin/python -m sentinela.cli --ml-login
