@echo off
chcp 65001 > nul
echo ===================================================
echo   Antigravity Discord Remote Bridge Launcher
echo ===================================================

cd /d "%~dp0"

if not exist ".env" (
    echo [WARNING] .env file not found. Creating from .env.example...
    copy .env.example .env
    echo Please edit .env and set DISCORD_BOT_TOKEN before continuing.
    pause
    exit /b 1
)

echo Starting Discord Bridge Bot...
python tools\discord_bridge.py
pause
