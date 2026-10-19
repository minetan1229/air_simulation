#!/usr/bin/env pwsh
# Environment bootstrapping script for Windows PowerShell
$ErrorActionPreference = "Stop"

Write-Host "[SETUP] Bootstrapping Paper Airplane Simulation Environment..." -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Check Python installation
$PythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { $null }
if (-not $PythonCmd) {
    Write-Error "[ERROR] Python 3.10+ is required but not found in PATH."
    exit 1
}

$PyVersion = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "[SETUP] Detected Python version: $PyVersion" -ForegroundColor Green

# Create virtual environment if missing
$VenvDir = Join-Path $ScriptDir ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "[SETUP] Creating virtual environment at .venv..." -ForegroundColor Yellow
    & $PythonCmd -m venv $VenvDir
}

# Determine venv python and pip
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

# Upgrade pip and packaging tools
Write-Host "[SETUP] Upgrading pip, setuptools, and wheel..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip setuptools wheel

# Check for uv if available for accelerated install
$HasUv = Get-Command uv -ErrorAction SilentlyContinue
if ($HasUv) {
    Write-Host "[SETUP] Detected 'uv' package manager. Installing dependencies via uv..." -ForegroundColor Green
    & uv pip install -e ".[dev]" --python $VenvPython
} else {
    Write-Host "[SETUP] Installing dependencies via pip..." -ForegroundColor Yellow
    & $VenvPython -m pip install -e ".[dev]"
}

# Verify CUDA / GPU availability
Write-Host "[SETUP] Verifying CUDA GPU acceleration..." -ForegroundColor Cyan
& $VenvPython -c "
import cupy as cp
dev = cp.cuda.runtime.getDevice()
props = cp.cuda.runtime.getDeviceProperties(dev)
name = props['name'].decode('utf-8') if isinstance(props['name'], bytes) else str(props['name'])
print(f'[SUCCESS] GPU Device {dev} detected: {name}')
"

Write-Host "[SETUP] Environment setup complete." -ForegroundColor Green
