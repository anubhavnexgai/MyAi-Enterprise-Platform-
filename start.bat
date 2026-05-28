@echo off
setlocal

cd /d "%~dp0"

if not exist .venv (
    echo Creating virtualenv...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

if not exist .env (
    echo Copying .env.example to .env (first run)...
    copy .env.example .env >nul
)

pip install -q -r requirements.txt

echo.
echo ============================================================
echo  MyAi-Enterprise starting on http://localhost:8002
echo  Docs:    http://localhost:8002/docs
echo  Health:  http://localhost:8002/health
echo ============================================================
echo.

python -m app.main

endlocal
