#!/usr/bin/env bash
# Environment bootstrapping script for POSIX environments (Linux / macOS / WSL)
set -euo pipefail

echo "[SETUP] Bootstrapping Paper Airplane Simulation Environment..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN=""
for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
        PYTHON_BIN="$cmd"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "[ERROR] Python 3.10+ is required but not found in PATH." >&2
    exit 1
fi

PY_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[SETUP] Detected Python version: $PY_VERSION"

VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[SETUP] Creating virtual environment at .venv..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

echo "[SETUP] Upgrading pip, setuptools, and wheel..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel

if command -v uv >/dev/null 2>&1; then
    echo "[SETUP] Detected 'uv'. Installing dependencies via uv..."
    uv pip install -e ".[dev]" --python "$VENV_PYTHON"
else
    echo "[SETUP] Installing dependencies via pip..."
    "$VENV_PYTHON" -m pip install -e ".[dev]"
fi

echo "[SETUP] Verifying CUDA GPU acceleration..."
"$VENV_PYTHON" -c "
import cupy as cp
dev = cp.cuda.runtime.getDevice()
props = cp.cuda.runtime.getDeviceProperties(dev)
name = props['name'].decode('utf-8') if isinstance(props['name'], bytes) else str(props['name'])
print(f'[SUCCESS] GPU Device {dev} detected: {name}')
"

echo "[SETUP] Environment setup complete."
