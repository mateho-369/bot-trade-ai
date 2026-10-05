# Optional Windows onedir packaging ONLY. Does not install/deploy/start/enable trading.
[CmdletBinding()]
param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Reviewed Python virtualenv required.' }
Push-Location -LiteralPath $root
try {
    & $python -c 'import PyInstaller'
    if ($LASTEXITCODE -ne 0) { throw 'Install reviewed optional PyInstaller version explicitly first.' }
    # Full source remains deployed; frozen executable has its own evidence hash.
    & $python -m PyInstaller --noconfirm --clean --onedir --console --name ReflexBot `
        --distpath (Join-Path $root 'package') --workpath (Join-Path $root 'build\pyinstaller') `
        --specpath (Join-Path $root 'build') --collect-all lightgbm --collect-all sklearn `
        --collect-all aiogram --collect-all apscheduler --collect-all tzdata --hidden-import MetaTrader5 `
        --add-data ((Join-Path $root 'miniapp\static') + ';miniapp/static') (Join-Path $root 'launcher.py')
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed; no deployment or runtime start was performed.' }
    $exe = Join-Path $root 'package\ReflexBot\ReflexBot.exe'
    Get-FileHash -LiteralPath $exe -Algorithm SHA256
    Write-Host 'Package only. Validate on Windows in mock/paper; pass absolute source-root .env. No source-only stage evidence reuse.'
} finally { Pop-Location }
