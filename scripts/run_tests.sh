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
echo " PipelineSleuth: Running Pytest Test Suite                "
echo "=========================================================="

pytest backend/tests -v

