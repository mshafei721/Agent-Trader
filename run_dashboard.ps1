# Launch the monitoring dashboard (foreground). Binds to 127.0.0.1 only.
# Open http://127.0.0.1:8787/ in a browser. Reads supervisor artifacts read-only;
# safe to run standalone alongside an already-running supervisor.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m goldtrader.dashboard
