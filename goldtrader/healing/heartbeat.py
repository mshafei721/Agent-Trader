"""Heartbeat file the watchdog reads to detect a hung/dead supervisor."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def write_heartbeat(path: Path, extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": time.time(), "pid": os.getpid()}
    if extra:
        payload.update(extra)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def read_heartbeat(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_age(path: Path) -> float:
    hb = read_heartbeat(path)
    if not hb:
        return float("inf")
    return time.time() - float(hb.get("ts", 0))


def pid_alive(pid) -> bool:
    """True if a process with this pid currently exists (Windows + POSIX)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)  # signal 0 = existence probe on POSIX
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
