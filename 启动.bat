@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Travel Planner - AI Travel Assistant
echo ============================================
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: python not found. Please install Python 3.10+ first.
        pause
        exit /b 1
    )
    echo [2/3] Installing dependencies (first time, please wait)...
    ".venv\Scripts\python.exe" -m pip install -r python_backend\requirements.txt
)
echo [3/3] Starting server...
echo       Open http://127.0.0.1:3000/ in browser
".venv\Scripts\python.exe" python_backend\main.py
pause
