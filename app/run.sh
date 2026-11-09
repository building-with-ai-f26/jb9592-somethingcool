#!/usr/bin/env bash
# One command to run Tailor locally: ./app/run.sh  → http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/uvicorn main:app --reload --port 8000
