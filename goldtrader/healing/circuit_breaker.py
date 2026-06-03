"""A simple circuit breaker. On repeated failures it trips -> caller forces HOLD."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..logging_setup import get_logger

log = get_logger("goldtrader.breaker")


@dataclass
class CircuitBreaker:
    fail_threshold: int = 3
    cooldown_s: float = 600.0
    _consecutive: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if (time.time() - self._opened_at) >= self.cooldown_s:
            # half-open: allow one probe
            log.info("breaker_half_open")
            self._opened_at = None
            self._consecutive = 0
            return False
        return True

    def record_success(self) -> None:
        if self._consecutive or self._opened_at:
            log.info("breaker_reset")
        self._consecutive = 0
        self._opened_at = None

    def record_failure(self, context: str = "") -> None:
        self._consecutive += 1
        log.warning("breaker_failure", consecutive=self._consecutive, context=context)
        if self._consecutive >= self.fail_threshold and self._opened_at is None:
            self._opened_at = time.time()
            log.error("breaker_open", cooldown_s=self.cooldown_s, context=context)
