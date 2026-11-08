#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

export PYTHONPATH="$ROOT_DIR"

echo "=========================================================="
echo " PipelineSleuth: Starting Unified Server                  "
echo " Web UI & API available at: http://localhost:8000         "
echo " Vite Dev Server (optional): cd frontend && npm run dev   "
echo "=========================================================="

python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

