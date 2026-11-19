# Setup & Launch (Getting Started)

This page explains how to set up the **3D Paper Airplane High-Performance CFD Simulator** in your local environment and launch the simulator.

---

## 1. Prerequisites & Required Environment

| Item | Requirement | Notes |
|:---|:---|:---|
| **OS** | Windows 10/11, macOS, Linux | |
| **Python** | **Python 3.10+** (3.11 recommended) | 64-bit required |
| **Web Browser** | WebGL 2.0 compatible modern browser | Google Chrome, Edge, Firefox, Safari |
| **GPU (Optional)** | NVIDIA GPU (CUDA 12.x compatible) | For wind field parallel acceleration via CuPy / PyTorch (CPU fallback included) |

---

## 2. Repository Clone & Virtual Environment Creation

```powershell
# Clone repository
git clone https://github.com/minetan1229/air_simulation.git
cd air_simulation

# Create Python virtual environment
python -m venv .venv

# Activate virtual environment (Windows PowerShell)
.venv\Scripts\Activate.ps1

# (macOS / Linux: source .venv/bin/activate)
```

---

## 3. Dependency Package Installation

Install required packages from `requirements.txt`.

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Development & Verification Tools (Optional)
For testing, linting, and type checking, install additional dev tools:
```powershell
pip install ruff mypy pytest
```

### GPU Acceleration Enable (Optional)
If you have CUDA 12.x environment, add CuPy or PyTorch to offload wind field computation to GPU:
```powershell
# CuPy (for CUDA 12.x)
pip install cupy-cuda12x>=13.0

# Or PyTorch
pip install torch
```
> *Note: If GPU packages not installed, automatically runs on optimized NumPy / CPU multi-threaded implementation.*

---

## 4. Environment Variable Configuration (`.env`)

Configure environment variables for notifications & remote integration (simulator runs without them). Copy `.env.example` to create `.env`:

```powershell
Copy-Item .env.example .env
```

### Main Settings (`.env`)
```env
# Discord Webhook notification URL (simulation/test result notifications)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN

# Discord remote execution bot token (only if using)
DISCORD_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_IDS=
ALLOWED_CHANNEL_IDS=
DEFAULT_MODEL=gemini-3.8-flash-low
```
> [!WARNING]
> `.env` contains sensitive info, excluded from Git commits (in `.gitignore`). **Never commit to public repository.**

---

## 5. Simulator Launch

Start the Flask server:

```powershell
python app.py
```

On successful startup, console shows:
```text
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000 (Press CTRL+C to quit)
```

Access **[http://localhost:5000](http://localhost:5000)** in browser. Google Clean style 3D instrument console appears.

---

## 6. Initial Verification (Smoke Test)

Verify server operation, API & physics module integration with smoke test script:

```powershell
python smoke_test.py
```

All items PASS = local setup complete.  
For detailed architecture & code explanation, see [[Architecture Design|Architecture]].