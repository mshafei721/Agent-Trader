"""Watchdog: detects a hung supervisor (stale heartbeat) and restarts it.

Crashes are handled by the NSSM service auto-restart. This watchdog covers the
case where the process is alive but stuck (heartbeat stops advancing).

Run standalone:  python -m goldtrader.healing.watchdog
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from ..config import get_settings
from ..logging_setup import get_logger, setup_logging
from .heartbeat import heartbeat_age, read_heartbeat, write_heartbeat

log = get_logger("goldtrader.watchdog")

CHECK_INTERVAL_S = 60
# stale = > 3 missed bars (interval_minutes) OR a hard floor, whichever larger
STALE_FACTOR = 3

# Last recovery action taken (surfaced to the dashboard via the watchdog heartbeat).
_last_action: str | None = None
_last_action_ts: float | None = None


def _beat(settings, supervisor_age: float) -> None:
    """Write the watchdog's own heartbeat so the dashboard can show it as alive."""
    try:
        write_heartbeat(
            settings.watchdog_heartbeat_file,
            {
                "role": "watchdog",
                "supervisor_age_s": round(supervisor_age, 1)
                if supervisor_age != float("inf") else None,
                "last_action": _last_action,
                "last_action_ts": _last_action_ts,
            },
        )
    except Exception as exc:  # noqa: BLE001 — observability must never crash the watchdog
        log.warning("watchdog_heartbeat_failed", error=str(exc))


def _kill_pid(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
        log.warning("killed_stale_supervisor", pid=pid)
    except Exception as exc:  # noqa: BLE001
        log.error("kill_failed", pid=pid, error=str(exc))


def _relaunch_supervisor() -> None:
    """Relaunch when not managed by a service. Uses the current interpreter."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "goldtrader.supervisor.loop"],
            cwd=str(get_settings().state_file.parent.parent),
        )
        log.info("relaunched_supervisor")
    except Exception as exc:  # noqa: BLE001
        log.error("relaunch_failed", error=str(exc))


def main():
    global _last_action, _last_action_ts
    setup_logging()
    s = get_settings()
    stale_threshold = max(STALE_FACTOR * s.interval_minutes * 60, 300)
    log.info("watchdog_started", stale_threshold_s=stale_threshold)
    while True:
        age = heartbeat_age(s.heartbeat_file)
        if age > stale_threshold:
            hb = read_heartbeat(s.heartbeat_file)
            pid = hb.get("pid") if hb else None
            log.error("heartbeat_stale", age_s=round(age), pid=pid)
            if pid:
                _kill_pid(int(pid))
            # If NSSM manages the service it auto-restarts the killed process.
            # Otherwise relaunch directly.
            if not os.environ.get("GOLDTRADER_MANAGED_BY_SERVICE"):
                _relaunch_supervisor()
            _last_action = f"recovered hung supervisor (pid={pid}, age={round(age)}s)"
            _last_action_ts = time.time()
        _beat(s, age)
        time.sleep(CHECK_INTERVAL_S)


if __name__ == "__main__":
    main()
