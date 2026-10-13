@echo off
rem Prompt-free autonomous DEMO startup. Actual DEMO identity and every stage/risk gate remain required.
setlocal
cd /d "%~dp0"
title MT5 AI ReflexBot - AUTONOMOUS DEMO
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall
".venv\Scripts\python.exe" main.py check-config
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -c "import sys; from core.settings import Settings; s=Settings(_env_file='.env'); ok=(s.mode.value=='demo' and s.mt5_backend=='real' and not s.paper_trading and not s.live_trading and s.start_paused and s.autonomous_demo and s.ai_require_approval and not s.demo_fast_track); sys.exit(0 if ok else 3)"
if errorlevel 1 goto :unsafe
".venv\Scripts\python.exe" -c "import sys; from core.settings import Settings; from app.process_guard import operator_stop_requested; sys.exit(3 if operator_stop_requested(Settings(_env_file='.env')) else 0)"
if errorlevel 1 goto :stopped
wscript.exe //B //Nologo "scripts\run_mt5_background.vbs"
if errorlevel 1 goto :terminal
echo Starting the supervised AUTONOMOUS_DEMO runtime.
echo Startup remains PAUSED through reconciliation and required readiness checks.
echo Auto-resume requires every existing gate, successful AI health, and actual MT5 DEMO identity.
echo Local reports: data\reports\actions.log and daily JSONL; viewer: python -m scripts.live_view
".venv\Scripts\python.exe" watchdog.py --env-file .env
set "CODE=%ERRORLEVEL%"
echo Demo runtime exited with code %CODE%.
exit /b %CODE%
:unsafe
echo REFUSED: requires DEMO_MODE=true, MT5_BACKEND=real, PAPER_TRADING=false, LIVE_TRADING=false, START_PAUSED=true, AUTONOMOUS_DEMO=true, AI_REQUIRE_APPROVAL=true and DEMO_FAST_TRACK=false.
exit /b 2
:terminal
echo The MT5 terminal could not be started. Check MT5_TERMINAL_PATH in .env.
exit /b 2
:noinstall
echo .venv or reviewed .env is missing. Run install.bat or scripts\setup_demo.ps1 first.
exit /b 2
:stopped
echo Persistent local stop request is active in data\runtime\operator-stop.json.
echo Review and clear explicitly with: .venv\Scripts\python.exe -m scripts.ops --env-file .env clear-stop
exit /b 2
:fail
echo Configuration check failed. Nothing was started.
exit /b 2
