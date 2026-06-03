"""Shared domain types used across the pipeline."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date as _date
from enum import Enum
from typing import Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    """Output of the signal adapter after parsing TradingAgents' decision."""

    action: Action
    confidence: float
    rationale: str
    raw: str
    run_date: str  # ISO date the analysis was run for

    def dedup_hash(self) -> str:
        """Stable hash keyed on action + date + confidence bucket.

        Used so the loop does not re-enter the same signal every bar.
        """
        bucket = round(self.confidence, 1)
        key = f"{self.action.value}|{self.run_date}|{bucket}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class OrderIntent:
    """A directional intent before risk sizing."""

    side: Action  # BUY or SELL only
    confidence: float
    rationale: str
    signal_hash: str


@dataclass(frozen=True)
class RiskDecision:
    """Result of RiskManager.evaluate()."""

    approved: bool
    reason: str
    lots: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    entry_hint: float = 0.0
    risk_amount: float = 0.0


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    retcode: int
    ticket: Optional[int]
    price: float
    comment: str
    request_dump: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Bias:
    """Slow-tier directional bias from the LLM. direction uses Action:
    BUY=long-biased, SELL=short-biased, HOLD=flat (no new entries)."""

    direction: Action
    conviction: float
    ts: str           # ISO timestamp when computed
    rationale: str = ""

    def is_long(self) -> bool:
        return self.direction == Action.BUY

    def is_short(self) -> bool:
        return self.direction == Action.SELL

    def is_flat(self) -> bool:
        return self.direction == Action.HOLD


@dataclass(frozen=True)
class TechSignal:
    """Fast-tier technical entry decision. side is BUY/SELL or None (no trigger)."""

    side: Optional[Action]
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolSpec:
    """Snapshot of broker symbol specifications."""

    name: str
    digits: int
    point: float
    volume_min: float
    volume_step: float
    volume_max: float
    contract_size: float
    tick_value: float
    tick_size: float
    stops_level: int
    freeze_level: int
    filling_mode: int


def today_iso() -> str:
    return _date.today().isoformat()
