"""Slow-tier directional bias from the LLM (TradingAgents), cached to disk.

The expensive multi-agent analysis runs at most once per ``bias_refresh_hours``.
The fast loop reads the cached bias on every tick; only when it is stale does
``current()`` trigger a fresh (costly) LLM analysis.

Rating -> direction mapping (the adapter already maps the 5-tier rating to an
Action): Buy/Overweight -> BUY (long), Sell/Underweight -> SELL (short),
Hold -> HOLD (flat / no new entries).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from ..config import Settings
from ..logging_setup import get_logger
from ..types import Action, Bias

log = get_logger("goldtrader.bias")


def bias_vetoes(tech_side: Action, bias: "Bias", threshold: float) -> bool:
    """Charts-lead soft veto: block a technical entry only when the LLM bias is the
    OPPOSITE direction with strong conviction (>= threshold). Flat, agreeing, or
    mildly-opposite bias does not veto.
    """
    if bias is None or bias.is_flat():
        return False
    opposite = Action.SELL if tech_side == Action.BUY else Action.BUY
    return bias.direction == opposite and bias.conviction >= threshold


class BiasProvider:
    def __init__(self, settings: Settings, adapter=None):
        self.s = settings
        # Lazy import keeps TradingAgents out of unit tests that inject a fake.
        if adapter is None:
            from ..signals.adapter import SignalAdapter

            adapter = SignalAdapter(settings)
        self.adapter = adapter

    # ---------- persistence ----------
    def _load(self) -> Optional[Bias]:
        try:
            d = json.loads(self.s.bias_file.read_text(encoding="utf-8"))
            return Bias(
                direction=Action(d["direction"]),
                conviction=float(d["conviction"]),
                ts=d["ts"],
                rationale=d.get("rationale", ""),
            )
        except (OSError, ValueError, KeyError):
            return None

    def _save(self, bias: Bias) -> None:
        self.s.bias_file.parent.mkdir(parents=True, exist_ok=True)
        self.s.bias_file.write_text(
            json.dumps(
                {
                    "direction": bias.direction.value,
                    "conviction": bias.conviction,
                    "ts": bias.ts,
                    "rationale": bias.rationale[:1500],
                }
            ),
            encoding="utf-8",
        )

    def _age_hours(self, bias: Bias) -> float:
        try:
            t = datetime.fromisoformat(bias.ts)
        except ValueError:
            return float("inf")
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0

    def is_stale(self, bias: Optional[Bias]) -> bool:
        if bias is None:
            return True
        return self._age_hours(bias) >= self.s.bias_refresh_hours

    # ---------- public ----------
    def current(self, force_refresh: bool = False, run_date: Optional[str] = None) -> Bias:
        cached = self._load()
        if not force_refresh and not self.is_stale(cached):
            log.info("bias_cache_hit", direction=cached.direction.value,
                     conviction=round(cached.conviction, 2),
                     age_h=round(self._age_hours(cached), 2))
            return cached
        return self.refresh(run_date)

    def refresh(self, run_date: Optional[str] = None) -> Bias:
        log.info("bias_refresh_start")
        sig = self.adapter.get_signal(run_date)  # may raise; caller handles
        bias = Bias(
            direction=sig.action,                # BUY/SELL/HOLD
            conviction=sig.confidence,
            ts=datetime.now(timezone.utc).isoformat(),
            rationale=sig.rationale,
        )
        self._save(bias)
        log.info("bias_refreshed", direction=bias.direction.value,
                 conviction=round(bias.conviction, 2))
        return bias
