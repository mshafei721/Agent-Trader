# Detached, race-free launcher for the goldtrader trio (supervisor + watchdog + dashboard).
# Safe to run at logon (via the GoldTrader-Resume scheduled task) or by hand. It:
#   1. does nothing if a supervisor is already running (no duplicates),
#   2. waits for the MetaTrader 5 terminal to be up (it must be logged in),
#   3. starts the supervisor, waits for a FRESH heartbeat (the first bias refresh can take
#      ~8 min and blocks the heartbeat), then starts the dashboard + watchdog.
# Starting the watchdog only AFTER the heartbeat is fresh is deliberate: it stops the watchdog
# from mistaking a slow startup for a hang and relaunching a second supervisor.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = Join-Path $root ".venv\Scripts\python.exe"
$hbf  = Join-Path $root "data\heartbeat.json"

function Test-SupervisorRunning {
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -match 'goldtrader\.supervisor\.loop' })
}

if (Test-SupervisorRunning) { Write-Host "supervisor already running - nothing to do."; return }

# 1. Wait for the MT5 terminal (up to 5 min), then a short settle for auto-login.
$deadline = (Get-Date).AddSeconds(300)
while (-not (Get-Process -Name terminal64 -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 10
}
Start-Sleep -Seconds 20

# 2. Supervisor first; await a fresh heartbeat (bias refresh can take ~8 min).
Start-Process -FilePath $py -ArgumentList '-m','goldtrader.supervisor.loop' -WorkingDirectory $root -WindowStyle Hidden
$deadline = (Get-Date).AddSeconds(600); $fresh = $false
do {
    Start-Sleep -Seconds 15
    try {
        $hb = Get-Content $hbf -Raw -ErrorAction Stop | ConvertFrom-Json
        if (([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int64]$hb.ts) -lt 60) { $fresh = $true }
    } catch {}
} until ($fresh -or (Get-Date) -gt $deadline)

# 3. Dashboard always (read-only, independent). Watchdog only once the heartbeat is fresh.
Start-Process -FilePath $py -ArgumentList '-m','goldtrader.dashboard' -WorkingDirectory $root -WindowStyle Hidden
if ($fresh) {
    Start-Process -FilePath $py -ArgumentList '-m','goldtrader.healing.watchdog' -WorkingDirectory $root -WindowStyle Hidden
    Write-Host "trio started (heartbeat fresh)."
} else {
    Write-Host "WARNING: supervisor heartbeat not fresh in 10 min (slow/hung bias refresh) - watchdog NOT started to avoid a duplicate. Investigate."
}
