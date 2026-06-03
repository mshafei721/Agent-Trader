# Launch the autonomous supervisor (foreground). Ctrl+C stops gracefully.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m goldtrader.supervisor.loop
