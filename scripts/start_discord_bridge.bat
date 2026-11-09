@echo off
chcp 65001 > nul
echo ===================================================
echo   Antigravity Discord Remote Bridge Launcher
echo ===================================================

cd /d "%~dp0.."
set "ROOT=%CD%"

if not exist "%ROOT%\.env" (
    echo [WARNING] .env file not found. Creating from .env.example...
    copy "%ROOT%\.env.example" "%ROOT%\.env"
    echo Please edit .env and set DISCORD_BOT_TOKEN before continuing.
    pause
    exit /b 1
)

echo Starting Discord Bridge Bot...
python "%ROOT%\tools\discord_bridge.py"
pause
