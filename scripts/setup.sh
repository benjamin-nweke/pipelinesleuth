#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " PipelineSleuth: Initializing Development Environment     "
echo "=========================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# 1. Check Python
PYTHON_BIN="python3"
if command -v python3.12 &>/dev/null; then
    PYTHON_BIN="python3.12"
elif command -v python3.11 &>/dev/null; then
    PYTHON_BIN="python3.11"
fi
echo "[+] Using Python binary: $($PYTHON_BIN --version)"

# 2. Virtual environment
if [ ! -d ".venv" ]; then
    echo "[+] Creating Python virtual environment in .venv..."
    $PYTHON_BIN -m venv .venv
fi

source .venv/bin/activate
echo "[+] Installing backend Python dependencies..."
pip install --upgrade pip
pip install -r backend/requirements.txt greenlet

# 3. Frontend packages
if [ -d "frontend" ]; then
    echo "[+] Installing frontend Node.js packages..."
    npm install --legacy-peer-deps --prefix frontend
    echo "[+] Building frontend assets..."
    npm run build --prefix frontend
fi

# 4. Storage directories
mkdir -p storage/uploads storage/workspaces

echo ""
echo "=========================================================="
echo " PipelineSleuth setup complete!                           "
echo " Start the application with: ./scripts/start.sh            "
echo "=========================================================="

