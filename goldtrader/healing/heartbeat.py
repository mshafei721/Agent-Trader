"""Heartbeat file the watchdog reads to detect a hung/dead supervisor."""
from __future__ import annotations

import json
import os
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
