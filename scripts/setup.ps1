#!/usr/bin/env pwsh
# Environment bootstrapping script for Windows PowerShell
$ErrorActionPreference = "Stop"
# PowerShell 7.4+ を有効にする(存在しない場合は無視)
if ($PSVersionTable.PSVersion.Major -gt 5) {
    $PSNativeCommandUseErrorActionPreference = $true
}

# Nativeコマンドの終了コードを検証するラッパー
function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$StepName)
    if ($LASTEXITCODE -ne 0) {
        throw "Setup step failed ($StepName): native command exited with code $LASTEXITCODE."
    }
}

Write-Host "[SETUP] Bootstrapping Paper Airplane Simulation Environment..." -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Check Python installation
# Check Python installation (requiring Python 3.10+)
$CandidateCmds = @("python", "py")
$PythonCmd = $null
foreach ($cmd in $CandidateCmds) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $isCompatible = & $cmd -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -eq 0) {
            $PythonCmd = $cmd
            break
        }
    }
}
if (-not $PythonCmd) {
    $DetectedRaw = $null
    foreach ($cmd in $CandidateCmds) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $DetectedRaw = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
            if ($DetectedRaw) { break }
        }
    }
    if ($DetectedRaw) {
        Write-Error "[ERROR] Python 3.10+ is required, but detected version is $DetectedRaw."
    } else {
        Write-Error "[ERROR] Python 3.10+ is required but no suitable Python was found in PATH."
    }
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
Assert-LastExitCode "upgrade pip/setuptools/wheel"

# Check for uv if available for accelerated install
$HasUv = Get-Command uv -ErrorAction SilentlyContinue
if ($HasUv) {
    Write-Host "[SETUP] Detected 'uv' package manager. Installing dependencies via uv..." -ForegroundColor Green
    & uv pip install -e ".[dev]" --python $VenvPython
    Assert-LastExitCode "uv pip install"
} else {
    Write-Host "[SETUP] Installing dependencies via pip..." -ForegroundColor Yellow
    & $VenvPython -m pip install -e ".[dev]"
    Assert-LastExitCode "pip install"
}

# Verify CUDA / GPU availability (best-effort: missing GPU is not a failure)
Write-Host "[SETUP] Verifying CUDA GPU acceleration..." -ForegroundColor Cyan
$GpuVerifyWarned = $false
& $VenvPython -c "
import cupy as cp
dev = cp.cuda.runtime.getDevice()
props = cp.cuda.runtime.getDeviceProperties(dev)
name = props['name'].decode('utf-8') if isinstance(props['name'], bytes) else str(props['name'])
print(f'[SUCCESS] GPU Device {dev} detected: {name}')
" 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    # GPUがない環境では検証失敗を致命的としないが、警告は出す
    Write-Host "[SETUP] WARNING: CUDA/GPU verification failed (CPU-only environment is still supported)." -ForegroundColor Yellow
    $GpuVerifyWarned = $true
}

Write-Host "[SETUP] Environment setup complete." -ForegroundColor Green
