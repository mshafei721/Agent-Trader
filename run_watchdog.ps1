# Launch the watchdog (foreground). Restarts a hung supervisor.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m goldtrader.healing.watchdog
