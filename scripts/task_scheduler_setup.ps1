# Explicit operator deployment action; never invoked by Python/runtime/import.
# InteractiveToken/AtLogOn only: NOT SYSTEM, Session 0, S4U or password storage.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [ValidatePattern('^[A-Za-z0-9 _-]{1,64}$')][string]$TaskName = 'MT5 AI ReflexBot',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$user = $identity.Name
if ($identity.User.Value -in @('S-1-5-18', 'S-1-5-19', 'S-1-5-20') -or
    [System.Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
    throw 'Run only as the logged-on interactive owner; not SYSTEM/service/Session 0.'
}
if ($Remove) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Remove logon task ONLY; running child requires graceful stop')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    return
}
$launcher = Join-Path $root 'scripts\run_bot_hidden.vbs'
foreach ($path in @($launcher, (Join-Path $root '.env'), (Join-Path $root '.venv\Scripts\pythonw.exe'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Reviewed .env, VBS and virtualenv required.' }
}
if ($root.Contains('"')) { throw 'Unsupported quoted root path.' }
$action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\wscript.exe') `
    -Argument ('//B //Nologo "' + $launcher + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# No Task Scheduler RestartCount: the guarded supervisor owns its durable budget.
if ($PSCmdlet.ShouldProcess($TaskName, 'Register current-user interactive logon task (always starts PAUSED)')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'Guarded ReflexBot supervisor; logon only; no automatic entry resume.'
}
