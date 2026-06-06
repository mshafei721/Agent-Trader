"""A simple circuit breaker. On repeated failures it trips -> caller forces HOLD.

State (consecutive failures + opened_at) is PERSISTED to disk so a crash + restart
(NSSM or watchdog relaunch) reloads the count instead of resetting to zero — closing
the crash-loop-retries-forever hole against a broken broker/LLM connection.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("goldtrader.breaker")


@dataclass
class CircuitBreaker:
    fail_threshold: int = 3
    cooldown_s: float = 600.0
    state_path: Path | None = None
    _consecutive: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._load()

    # ---------------- persistence ----------------
    def _load(self) -> None:
        if self.state_path is None:
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._consecutive = int(data.get("consecutive", 0))
            opened = data.get("opened_at", None)
            self._opened_at = float(opened) if opened is not None else None
            if self._opened_at is not None:
                log.warning("breaker_state_restored", consecutive=self._consecutive,
                            opened_at=self._opened_at)
        except (OSError, ValueError, TypeError):
            self._consecutive, self._opened_at = 0, None

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"consecutive": self._consecutive, "opened_at": self._opened_at}),
                encoding="utf-8",
            )
            tmp.replace(self.state_path)
        except OSError as exc:
            log.warning("breaker_state_write_failed", error=str(exc))

    # ---------------- state machine ----------------
    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if (time.time() - self._opened_at) >= self.cooldown_s:
            # half-open: allow one probe
            log.info("breaker_half_open")
            self._opened_at = None
            self._consecutive = 0
            self._save()
            return False
        return True

    def record_success(self) -> None:
        if self._consecutive or self._opened_at:
            log.info("breaker_reset")
        self._consecutive = 0
        self._opened_at = None
        self._save()

    def record_failure(self, context: str = "") -> None:
        self._consecutive += 1
        log.warning("breaker_failure", consecutive=self._consecutive, context=context)
        if self._consecutive >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.time()
            log.error("breaker_open", cooldown_s=self.cooldown_s, context=context)
        self._save()
