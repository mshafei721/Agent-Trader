"""Persistent supervisor state (survives restarts -> no double-trading)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


@dataclass
class SupervisorState:
    last_signal_hash: Optional[str] = None
    last_run_iso: Optional[str] = None
    day_anchor_equity: float = 0.0
    day_anchor_date: Optional[str] = None
    start_equity: float = 0.0
    halted_until: Optional[str] = None
    known_tickets: list[int] = field(default_factory=list)
    last_reflection_iso: Optional[str] = None
    trades_at_last_reflection: int = 0

    # ---------- persistence ----------
    @classmethod
    def load(cls, path: Path) -> "SupervisorState":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def roll_day_if_needed(self, current_equity: float) -> bool:
        """Reset the daily anchor at date rollover. Returns True if rolled."""
        today = date.today().isoformat()
        if self.day_anchor_date != today:
            self.day_anchor_date = today
            self.day_anchor_equity = current_equity
            if self.start_equity <= 0:
                self.start_equity = current_equity
            self.halted_until = None
            return True
        return False
