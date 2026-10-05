@echo off
rem MT5 AI ReflexBot - one-time installer. Paper/mock defaults; never connects a broker or trades.
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
echo Edit .env now: TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID and OPENAI_API_KEY - your Groq key.
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
echo Safety defaults: LIVE_TRADING=false, PAPER_TRADING=true, START_PAUSED=true, MT5_BACKEND=mock.
echo AI_FALLBACK_MODE=BLOCK_ON_AI_FAILURE: without a working Groq key NO trades are opened.
echo Next: edit .env, then run start.bat - paper/mock - or start_demo.bat - MT5 demo data, paper orders.
pause
exit /b 0
:nopython
echo The Python launcher "py" was not found. Install Python 3.11 x64 from python.org, then rerun.
:fail
echo.
echo INSTALL FAILED - see the messages above. Nothing was started.
pause
exit /b 1
