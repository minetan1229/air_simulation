# Setup Guide

This document specifies the operating requirements, environment setup, and verification procedures for the "3D Paper Airplane High-Performance CFD Simulator".

---

## 1. Operating Environment & Prerequisites

- **Operating System**: Windows 10/11 (64-bit), Linux (Ubuntu 20.04 LTS+ recommended), macOS 12+
- **Python**: 3.10+ (3.11.x recommended)
- **Accelerator (GPU)**: NVIDIA GPU (CUDA 12.x / Compute Capability 5.0+)
- **Packages**: `cupy-cuda12x>=13.0.0` (when using GPU)
  - In environments without GPU or where CUDA initialization fails, the system **automatically transitions to CPU fallback via NumPy / `multiprocessing`** (response schema identical to GPU path). No `RuntimeError` halt on CUDA initialization failure.
  - GPU-optimized batch path (`model = "rigid_body_6dof"`, `model_detail = "gpu_batch_rk4"`) computes 13-state RK4 rigid body model in vectorized form; GPU Monte Carlo ensemble path operates as reduced model (`model = "reduced_surrogate"`).
- **Display Environment**: WebGL 2.0 compatible browser (Google Chrome, Microsoft Edge, Firefox, Safari)

---

## 2. Automated Setup Procedure

The automation scripts placed in the repository root enable single-command execution from virtual environment creation through dependency installation to CUDA device verification.

### 2.1 Windows (PowerShell)
```powershell
.\setup.ps1
```

### 2.2 Linux / macOS / WSL (Bash)
```bash
chmod +x setup.sh
./setup.sh
```

---

## 3. Manual Setup Procedure

### 3.1 Virtual Environment Creation & Activation

**Windows:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2 Package Installation
Install build tools and dependencies.

```bash
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

When using the fast package installer `uv`:
```bash
uv pip install -e ".[dev]"
```

---

## 4. Environment Configuration (.env)

To use external integrations (Discord Webhook, etc.), copy `.env.example` to create `.env`.

```bash
cp .env.example .env
```

`.env` configuration items:
```env
# Discord Webhook notification endpoint (notification processing skipped if not set)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## 5. Hardware Acceleration Verification

Verify CUDA GPU is properly recognized:

```bash
python -c "import cupy as cp; print('Device Count:', cp.cuda.runtime.getDeviceCount()); print('Current Device:', cp.cuda.runtime.getDevice())"
```

Normal output example:
```text
Device Count: 1
Current Device: 0
```

---

## 6. Application Launch & Verification

Start the server:
```bash
python app.py
```

Open `http://127.0.0.1:5000` in a browser and verify the instrument console displays.
Health check endpoint verification:
```bash
curl http://127.0.0.1:5000/api/v1/meta/health
```