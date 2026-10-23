@echo off
rem One-click start for Windows: sets up everything on first run,
rem then opens the GUI.
cd /d %~dp0

if not exist .venv (
    echo First run: creating the Python environment, one moment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

if not exist .env (
    echo No .env file found, creating one from .env.example
    copy .env.example .env
    echo Edit .env to pick your model, then run this file again if needed.
)

python run.py
pause
