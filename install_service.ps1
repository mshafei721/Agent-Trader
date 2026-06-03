# Register goldtrader as auto-restarting Windows services using NSSM.
# Prereq: install NSSM (https://nssm.cc) and ensure nssm.exe is on PATH,
#         or set $Nssm to its full path below.
#
# Run this in an ELEVATED PowerShell (Administrator).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "$root\.venv\Scripts\python.exe"
$Nssm = "nssm"  # or full path e.g. "C:\nssm\win64\nssm.exe"

if (-not (Test-Path $python)) { throw "venv python not found at $python — create the venv first." }

Write-Host "Installing GoldTraderSupervisor service..."
& $Nssm install GoldTraderSupervisor $python "-m" "goldtrader.supervisor.loop"
& $Nssm set GoldTraderSupervisor AppDirectory $root
& $Nssm set GoldTraderSupervisor AppStdout "$root\logs\service_supervisor.out.log"
& $Nssm set GoldTraderSupervisor AppStderr "$root\logs\service_supervisor.err.log"
& $Nssm set GoldTraderSupervisor AppEnvironmentExtra "GOLDTRADER_MANAGED_BY_SERVICE=1"
& $Nssm set GoldTraderSupervisor AppExit Default Restart
& $Nssm set GoldTraderSupervisor AppRestartDelay 5000
& $Nssm set GoldTraderSupervisor Start SERVICE_AUTO_START

Write-Host "Installing GoldTraderWatchdog service..."
& $Nssm install GoldTraderWatchdog $python "-m" "goldtrader.healing.watchdog"
& $Nssm set GoldTraderWatchdog AppDirectory $root
& $Nssm set GoldTraderWatchdog AppStdout "$root\logs\service_watchdog.out.log"
& $Nssm set GoldTraderWatchdog AppStderr "$root\logs\service_watchdog.err.log"
& $Nssm set GoldTraderWatchdog AppEnvironmentExtra "GOLDTRADER_MANAGED_BY_SERVICE=1"
& $Nssm set GoldTraderWatchdog AppExit Default Restart
& $Nssm set GoldTraderWatchdog Start SERVICE_AUTO_START

Write-Host "Installing GoldTraderDashboard service..."
& $Nssm install GoldTraderDashboard $python "-m" "goldtrader.dashboard"
& $Nssm set GoldTraderDashboard AppDirectory $root
& $Nssm set GoldTraderDashboard AppStdout "$root\logs\service_dashboard.out.log"
& $Nssm set GoldTraderDashboard AppStderr "$root\logs\service_dashboard.err.log"
# The managed-by-service flag is what makes the dashboard's stop/restart actions
# drive NSSM (nssm stop/restart GoldTraderSupervisor) instead of taskkill.
& $Nssm set GoldTraderDashboard AppEnvironmentExtra "GOLDTRADER_MANAGED_BY_SERVICE=1"
& $Nssm set GoldTraderDashboard AppExit Default Restart
& $Nssm set GoldTraderDashboard AppRestartDelay 5000
& $Nssm set GoldTraderDashboard Start SERVICE_AUTO_START

Write-Host ""
Write-Host "Done. Start with:  nssm start GoldTraderSupervisor ; nssm start GoldTraderWatchdog ; nssm start GoldTraderDashboard"
Write-Host "Stop with:         nssm stop GoldTraderSupervisor  ; nssm stop GoldTraderWatchdog  ; nssm stop GoldTraderDashboard"
Write-Host "Remove with:       nssm remove GoldTraderSupervisor confirm ; nssm remove GoldTraderWatchdog confirm ; nssm remove GoldTraderDashboard confirm"
Write-Host ""
Write-Host "Dashboard:  http://127.0.0.1:8787/  (localhost only)"
Write-Host "NOTE: The MT5 terminal must be running and logged in (Algo Trading button ON)."
Write-Host "      Services run in session 0; ensure the terminal is reachable for that context."
