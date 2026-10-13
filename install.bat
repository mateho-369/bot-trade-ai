@echo off
rem MT5 AI ReflexBot - prompt-free dependency installer. It never starts a broker or runtime.
setlocal
cd /d "%~dp0"
title MT5 AI ReflexBot - install
echo === MT5 AI ReflexBot installer ===
where py >nul 2>nul
if errorlevel 1 goto :nopython
if exist ".venv\Scripts\python.exe" goto :venvready
set "PYTHON_VERSION="
call :try_python 3.14
call :try_python 3.13
call :try_python 3.12
call :try_python 3.11
if not defined PYTHON_VERSION goto :unsupportedpython
echo Creating virtual environment .venv with Python %PYTHON_VERSION% ...
py -%PYTHON_VERSION% -m venv .venv
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
echo INSTALL FAILED: Python launcher "py" was not found. Install CPython 3.11 or newer x64 with the launcher, then rerun.
pause
exit /b 1
:unsupportedpython
echo No supported 64-bit CPython runtime was found. Tried Python 3.14, 3.13, 3.12 and 3.11.
echo Install Python 3.11 or newer x64 with the Python launcher, then rerun.
goto :fail
:fail
echo.
echo INSTALL FAILED - see the messages above. Nothing was started.
pause
exit /b 1
:try_python
if defined PYTHON_VERSION exit /b 0
py -%~1 -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYTHON_VERSION=%~1"
exit /b 0
