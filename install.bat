@echo off
rem MT5 AI ReflexBot - prompt-free dependency installer. It never starts a broker or runtime.
setlocal
cd /d "%~dp0"
title MT5 AI ReflexBot - install
echo === MT5 AI ReflexBot installer ===
where py >nul 2>nul
if errorlevel 1 goto :nopython
if exist ".venv\Scripts\python.exe" goto :venvready
echo Creating virtual environment .venv with Python 3.11 ...
py -3.11 -m venv .venv
if errorlevel 1 goto :fail
:venvready
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto :fail
if exist ".env" goto :envready
copy /Y ".env.example" ".env" >nul
if errorlevel 1 goto :fail
echo Created .env from .env.example.
echo Edit .env if using paper/mock; for native autonomous DEMO use scripts\setup_demo.ps1.
goto :configure
:envready
echo Existing .env kept unchanged.
:configure
".venv\Scripts\python.exe" main.py check-config
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" main.py init-db
if errorlevel 1 goto :fail
echo.
echo Install complete.
echo Safety defaults: LIVE_TRADING=false, AUTONOMOUS_DEMO=false, START_PAUSED=true, MT5_BACKEND=mock.
echo AI_FALLBACK_MODE=BLOCK_ON_AI_FAILURE: without a working Groq key NO trades are opened.
echo Next: use start.bat for reviewed paper/mock, or scripts\setup_demo.ps1 for native autonomous DEMO.
exit /b 0
:nopython
echo The Python launcher "py" was not found. Install Python 3.11 x64 from python.org, then rerun.
:fail
echo.
echo INSTALL FAILED - see the messages above. Nothing was started.
exit /b 1
