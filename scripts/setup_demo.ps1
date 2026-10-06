<#
MT5 AI ReflexBot - Exness DEMO setup. DEMO account only: REAL accounts and LIVE trading stay locked.

Run from the project folder in PowerShell:
    powershell -ExecutionPolicy Bypass -File scripts\setup_demo.ps1
Options:
    -NoStart   run every check but do not start the bot

Steps: venv, install, .env check, init-db (only if no database), resolve_symbols,
check_mt5_readonly (terminal must report a DEMO account), news probe, preflight,
then the watchdog is started without a prompt. The bot starts PAUSED and autonomous recovery uses
the same reconciliation and safety gates as local resume. Stop/operate with python -m scripts.ops.
No key or password is ever printed.
#>
[CmdletBinding()]
param([switch]$NoStart)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$py = Join-Path (Get-Location) '.venv\Scripts\python.exe'

function Step([string]$name) {
    Write-Host ''
    Write-Host "=== $name ===" -ForegroundColor Cyan
}

function Fail([string]$message) {
    Write-Host "STOPPED: $message" -ForegroundColor Red
    Write-Host 'Nothing was started. Fix the message above and run this script again.'
    exit 1
}

function Invoke-Python([string[]]$arguments, [string]$what) {
    & $py @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE)." }
}

Step '1/9 Python virtual environment'
if (-not (Test-Path -LiteralPath $py)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        Fail 'The Python launcher "py" was not found. Install Python 3.11 x64 from python.org.'
    }
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create .venv with Python 3.11.' }
}
Write-Host 'OK: .venv'

Step '2/9 Install requirements'
Invoke-Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', 'requirements.txt') 'pip install'
Invoke-Python @('-m', 'pip', 'check') 'pip check'

Step '3/9 Demo configuration (.env)'
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.demo.example' -Destination '.env'
    Write-Host 'Created .env from .env.demo.example.' -ForegroundColor Yellow
    Write-Host 'Edit .env and set OPENAI_API_KEY (your Groq key); Telegram reporting is optional.'
    Write-Host 'Keep MT5 open and logged in to your Exness DEMO account, then run this script again.'
    exit 0
}
Invoke-Python @('main.py', 'check-config') 'check-config'
$guard = "import sys; from core.settings import Settings; s = Settings(_env_file='.env'); " +
    "ok = s.mode.value == 'demo' and not s.live_trading and not s.paper_trading and " +
    "s.start_paused and s.autonomous_demo and s.ai_require_approval and not s.demo_fast_track and s.mt5_backend == 'real'; " +
    "print('mode:', s.mode.value, '| live:', s.live_trading, '| start_paused:', s.start_paused, " +
    "'| autonomous_demo:', s.autonomous_demo, '| fast_track:', s.demo_fast_track); sys.exit(0 if ok else 3)"
Invoke-Python @('-c', $guard) 'DEMO safety check (requires AUTONOMOUS_DEMO=true, AI_REQUIRE_APPROVAL=true, DEMO_FAST_TRACK=false and actual DEMO broker mode)'

Step '4/9 Database'
if (Test-Path -LiteralPath 'data\reflexbot.db') {
    Write-Host 'Existing database kept (init-db skipped).'
} else {
    Invoke-Python @('main.py', 'init-db') 'init-db'
}

Step '5/9 Broker symbols (read-only, writes suffixes to .env)'
Invoke-Python @('-m', 'scripts.resolve_symbols', '--env-file', '.env', '--write') 'resolve_symbols'

Step '6/9 MT5 account (read-only, must be a DEMO account)'
$output = & $py -m scripts.check_mt5_readonly --env-file .env
$code = $LASTEXITCODE
$text = ($output | Out-String)
Write-Host $text
if ($code -ne 0) { Fail 'MT5 read-only check failed. Open MT5, log in to your Exness DEMO account.' }
try { $report = $text | ConvertFrom-Json } catch { Fail 'Could not read the MT5 check result.' }
if ($report.actual_account_kind -ne 'demo') {
    Fail ("The MT5 terminal reports a '" + $report.actual_account_kind + "' account. Only a DEMO account is allowed.")
}
Write-Host 'OK: the terminal reports a DEMO account.' -ForegroundColor Green

Step '7/9 News sources (one read-only fetch per source)'
& $py -m scripts.inspect_news_sources --fetch
if ($LASTEXITCODE -ne 0) {
    Write-Host 'WARNING: a news source is unavailable right now. With NEWS_UNAVAILABLE_POLICY=block' -ForegroundColor Yellow
    Write-Host 'the bot opens no new trades until news works again (it keeps protecting open trades).' -ForegroundColor Yellow
}

Step '8/9 Preflight'
Invoke-Python @('-m', 'scripts.preflight', '--profile', 'windows_native', '--env-file', '.env', '--allow-native-import') 'preflight'

Step '9/9 Start (PAUSED)'
if ($NoStart) {
    Write-Host 'All checks passed. -NoStart given: the bot was not started.' -ForegroundColor Green
    exit 0
}
$stopCheck = "import sys; from core.settings import Settings; from app.process_guard import operator_stop_requested; " +
    "sys.exit(3 if operator_stop_requested(Settings(_env_file='.env')) else 0)"
& $py -c $stopCheck
if ($LASTEXITCODE -ne 0) { Fail 'Persistent local stop request is active; review and clear it with python -m scripts.ops clear-stop.' }
Start-Process -FilePath $py -ArgumentList @('watchdog.py', '--env-file', '.env') -WindowStyle Minimized
Write-Host 'Watchdog started in a minimized window; runtime stays PAUSED until reconciliation and all configured gates pass.' -ForegroundColor Green
Write-Host 'Local reports: data\reports\actions.log. View: python -m scripts.live_view. Controls: python -m scripts.ops --help.'
exit 0
