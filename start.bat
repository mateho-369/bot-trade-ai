@echo off
rem MT5 AI ReflexBot - prompt-free start. Runtime reconciles and starts PAUSED.
setlocal
cd /d "%~dp0"
title MT5 AI ReflexBot
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall
".venv\Scripts\python.exe" main.py check-config
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -c "import sys; from core.settings import Settings; from app.process_guard import operator_stop_requested; sys.exit(3 if operator_stop_requested(Settings(_env_file='.env')) else 0)"
if errorlevel 1 goto :stopped
echo Starting the supervised runtime. New entries remain PAUSED unless AUTONOMOUS_DEMO gates pass.
echo Local reports: data\reports\actions.log and data\reports\YYYY-MM-DD.jsonl
echo Viewer: .venv\Scripts\python.exe -m scripts.live_view
echo Operator controls: .venv\Scripts\python.exe -m scripts.ops --help
".venv\Scripts\python.exe" watchdog.py --env-file .env
set "CODE=%ERRORLEVEL%"
echo Runtime exited with code %CODE%.
if not "%CODE%"=="0" pause
exit /b %CODE%
:noinstall
echo .venv or reviewed .env is missing. Run install.bat or scripts\setup_demo.ps1 first.
pause
exit /b 2
:stopped
echo Persistent local stop request is active in data\runtime\operator-stop.json.
echo Review and clear it explicitly with: .venv\Scripts\python.exe -m scripts.ops --env-file .env clear-stop
pause
exit /b 2
:fail
echo Configuration check failed. Nothing was started.
pause
exit /b 2
