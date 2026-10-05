@echo off
rem MT5 AI ReflexBot - start the supervised runtime with .env. Always starts PAUSED.
setlocal
cd /d "%~dp0"
title MT5 AI ReflexBot
if not exist ".venv\Scripts\python.exe" goto :noinstall
if not exist ".env" goto :noinstall
if exist "data\runtime\operator-stop.json" goto :stopped
".venv\Scripts\python.exe" main.py check-config
if errorlevel 1 goto :fail
echo.
echo Starting the supervised runtime - watchdog plus bot. New entries stay PAUSED until /resume in Telegram.
echo Logs: data\logs\bot.log, data\logs\errors.log, data\logs\ai_decisions.log
echo Stop: Ctrl+C here, or .venv\Scripts\python.exe -m scripts.stop_runtime --env-file .env
".venv\Scripts\python.exe" watchdog.py --env-file .env
set "CODE=%ERRORLEVEL%"
echo Runtime exited with code %CODE%.
pause
exit /b %CODE%
:noinstall
echo .venv or .env is missing. Run install.bat first.
goto :fail
:stopped
echo An operator stop request exists: data\runtime\operator-stop.json
echo Review why the bot was stopped, delete that file, then run start.bat again.
:fail
pause
exit /b 1
