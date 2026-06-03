"""Control actions for the dashboard.

Each action returns ``{"ok": bool, "message": str, "mode"?: str}`` and logs via
the shared structlog logger (so it appears in the live log stream). Long-running
actions (LLM bias refresh, reflection, run-once) run on a background daemon thread
and return immediately; the UI observes their effects (new bias ts, new reflection
file, new log lines). Process control adapts to whether NSSM manages the service.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

from ..config import Settings
from ..healing.heartbeat import read_heartbeat
from ..logging_setup import get_logger

log = get_logger("goldtrader.dashboard")

SUPERVISOR_SERVICE = "GoldTraderSupervisor"

# Guard against overlapping run-once / refresh invocations.
_busy_lock = threading.Lock()
_busy: set[str] = set()


def _run_bg(name: str, fn) -> dict:
    """Run fn() on a daemon thread unless an instance is already running."""
    with _busy_lock:
        if name in _busy:
            return {"ok": False, "message": f"{name} already running"}
        _busy.add(name)

    def _wrap():
        try:
            fn()
            log.info("action_done", action=name)
        except Exception as exc:  # noqa: BLE001
            log.error("action_failed", action=name, error=str(exc))
        finally:
            with _busy_lock:
                _busy.discard(name)

    threading.Thread(target=_wrap, name=f"dash-{name}", daemon=True).start()
    log.info("action_started", action=name)
    return {"ok": True, "message": f"{name} started"}


# ---------------- kill switch ----------------
def set_kill_switch(s: Settings, on: bool) -> dict:
    try:
        if on:
            from ..safety.guards import trip_kill_switch

            trip_kill_switch(s, "dashboard manual kill")
            return {"ok": True, "message": "Kill switch ON — supervisor will idle."}
        if s.kill_switch_file.exists():
            s.kill_switch_file.unlink()
            log.info("kill_switch_cleared", source="dashboard")
            return {"ok": True, "message": "Kill switch OFF — trading resumes."}
        return {"ok": True, "message": "Kill switch already absent."}
    except Exception as exc:  # noqa: BLE001
        log.error("kill_switch_action_failed", error=str(exc))
        return {"ok": False, "message": str(exc)}


# ---------------- LLM bias refresh ----------------
def refresh_bias(s: Settings) -> dict:
    def _do():
        from ..strategy.bias import BiasProvider
        from ..types import today_iso

        BiasProvider(s).current(force_refresh=True, run_date=today_iso())

    return _run_bg("bias_refresh", _do)


# ---------------- reflection / self-heal ----------------
def run_reflection(s: Settings) -> dict:
    def _do():
        from ..learning.journal import Journal
        from ..learning.reflection import ReflectionEngine
        from ..observability.notifier import Notifier

        ReflectionEngine(s, Journal(s.journal_db), Notifier(s)).run()

    return _run_bg("reflection", _do)


# ---------------- run-once tick ----------------
def run_once(s: Settings) -> dict:
    """Fire a single supervisor tick. CAN PLACE A REAL ORDER when dry_run is False."""
    def _do():
        from ..supervisor.loop import Supervisor

        sup = Supervisor()
        sup.startup()
        try:
            sup.tick()
        finally:
            sup.client.shutdown()

    return _run_bg("run_once", _do)


# ---------------- process stop / restart ----------------
def _managed() -> bool:
    return bool(os.environ.get("GOLDTRADER_MANAGED_BY_SERVICE"))


def _nssm(*args: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["nssm", *args], capture_output=True, text=True, timeout=30)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out.strip()
    except FileNotFoundError:
        return False, "nssm not found on PATH"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _heartbeat_pid(s: Settings) -> int | None:
    hb = read_heartbeat(s.heartbeat_file)
    pid = hb.get("pid") if hb else None
    return int(pid) if pid else None


def _taskkill(pid: int, force: bool = False) -> tuple[bool, str]:
    args = ["taskkill", "/PID", str(pid)] + (["/F"] if force else [])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def stop_supervisor(s: Settings) -> dict:
    if _managed():
        ok, out = _nssm("stop", SUPERVISOR_SERVICE)
        log.warning("supervisor_stop", mode="service", ok=ok, out=out)
        return {"ok": ok, "mode": "service", "message": out or "service stop issued"}
    pid = _heartbeat_pid(s)
    if not pid:
        return {"ok": False, "mode": "process", "message": "no supervisor PID in heartbeat"}
    ok, out = _taskkill(pid, force=True)
    log.warning("supervisor_stop", mode="process", pid=pid, ok=ok, out=out)
    return {"ok": ok, "mode": "process", "message": out or f"taskkill {pid}"}


def restart_supervisor(s: Settings) -> dict:
    if _managed():
        ok, out = _nssm("restart", SUPERVISOR_SERVICE)
        log.warning("supervisor_restart", mode="service", ok=ok, out=out)
        return {"ok": ok, "mode": "service", "message": out or "service restart issued"}
    # Unmanaged: kill the running process, then relaunch a fresh one.
    pid = _heartbeat_pid(s)
    if pid:
        _taskkill(pid, force=True)
    try:
        root = s.state_file.parent.parent  # project root
        subprocess.Popen(
            [sys.executable, "-m", "goldtrader.supervisor.loop"],
            cwd=str(root),
        )
        log.warning("supervisor_restart", mode="process", killed_pid=pid)
        return {"ok": True, "mode": "process", "message": f"relaunched (killed pid={pid})"}
    except Exception as exc:  # noqa: BLE001
        log.error("supervisor_restart_failed", error=str(exc))
        return {"ok": False, "mode": "process", "message": str(exc)}
