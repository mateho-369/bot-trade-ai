@echo off
rem MT5 AI ReflexBot - demo run: MT5 DEMO account data, PAPER orders. Requires typing YES.
setlocal
cd /d "%~dp0"
title MT5 AI ReflexBot - DEMO RUN
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall
if exist "data\runtime\operator-stop.json" goto :stopped
echo ================================ DEMO RUN ================================
echo  - Market data, symbols and account info are READ from your MT5 DEMO terminal
echo    when .env has MT5_BACKEND=real; with MT5_BACKEND=mock the data is synthetic.
echo  - Orders are SIMULATED: PAPER_TRADING=true, LIVE_TRADING=false. Nothing is sent to the broker.
echo  - The bot starts PAUSED. Resume from Telegram with /resume when you are ready.
echo  - AI fallback mode, risk limits and the kill switch apply as configured in .env and Telegram.
echo ==========================================================================
set "CONFIRM="
set /p "CONFIRM=Type YES to start the demo run: "
if not "%CONFIRM%"=="YES" goto :cancelled
".venv\Scripts\python.exe" -c "import sys; from core.settings import Settings; s = Settings(_env_file='.env'); print('mode:', s.mode.value, '| backend:', s.mt5_backend, '| live:', s.live_trading, '| paper:', s.paper_trading, '| start_paused:', s.start_paused, '| ai_fallback:', s.ai_fallback_mode); sys.exit(0 if (not s.live_trading and s.paper_trading and s.start_paused) else 3)"
if errorlevel 1 goto :unsafe
wscript.exe //B //Nologo "scripts\run_mt5_background.vbs"
if errorlevel 1 goto :terminal
echo Starting the supervised demo runtime. Logs: data\logs\bot.log, errors.log, ai_decisions.log
".venv\Scripts\python.exe" watchdog.py --env-file .env
set "CODE=%ERRORLEVEL%"
echo Demo runtime exited with code %CODE%.
pause
exit /b %CODE%
:cancelled
echo Demo run cancelled - you did not type YES.
goto :fail
:unsafe
echo REFUSED: the demo run requires LIVE_TRADING=false, PAPER_TRADING=true and START_PAUSED=true in .env.
goto :fail
:terminal
echo The MT5 terminal could not be started. Check MT5_TERMINAL_PATH in .env.
goto :fail
:noinstall
echo .venv or .env is missing. Run install.bat first.
goto :fail
:stopped
echo An operator stop request exists: data\runtime\operator-stop.json
echo Review why the bot was stopped, delete that file, then run start_demo.bat again.
:fail
pause
exit /b 1
