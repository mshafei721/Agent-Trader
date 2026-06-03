# Launch the autonomous supervisor (foreground). Ctrl+C stops gracefully.
# The watchdog (hang-recovery) and dashboard (monitoring UI) start as detached
# background processes first, so foreground mode gets the same supervisor +
# watchdog + dashboard trio as the NSSM service install, and both survive a
# supervisor crash (the dashboard can then render the system as "down").
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = "$root\.venv\Scripts\python.exe"

$watchdog  = Start-Process -FilePath $python -ArgumentList '-m','goldtrader.healing.watchdog' -WorkingDirectory $root -PassThru
$dashboard = Start-Process -FilePath $python -ArgumentList '-m','goldtrader.dashboard'       -WorkingDirectory $root -PassThru
Write-Host "watchdog  pid=$($watchdog.Id)"
Write-Host "dashboard pid=$($dashboard.Id)  ->  http://127.0.0.1:8787/"

try {
    & $python -m goldtrader.supervisor.loop
}
finally {
    # Stop the helpers we started when the supervisor exits / Ctrl+C.
    foreach ($p in @($dashboard, $watchdog)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -ErrorAction SilentlyContinue }
    }
}
