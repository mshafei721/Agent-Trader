"""Seasonal sizing bias (V7 profitability — the one lab-validated edge).

Across the whole V7 edge hunt, the only signal with an out-of-sample-positive, cost-surviving
edge was gold's Halloween/winter long-tilt: being long gold Nov->Apr beat zero with a 95% CI
excluding zero (see backtest/seasonal.py). Everything intraday/technical/mean-reverting/macro
was a coin flip.

We express that edge conservatively, as a size DAMP (<= 1.0) on entries that do NOT align with
it — never a boost. So it can only ever REDUCE risk (like the defensive self-heal scaler it
multiplies with), never breach the risk-% ceiling, and degrades safely. A winter long runs at
full size; a winter short, a summer long, or a summer short runs at `seasonal_offseason_scaler`.

Pure and config-gated; unit-tested without a clock or broker.
"""
from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..types import Action

# Baur (2013) winter window: gold's documented strong months.
WINTER_MONTHS = frozenset({11, 12, 1, 2, 3, 4})  # Nov, Dec, Jan, Feb, Mar, Apr


def seasonal_size_scaler(now: datetime, side: Action, s: Settings) -> tuple[float, str]:
    """(scaler in (0,1], reason). 1.0 for the favored winter-long; otherwise the bounded
    off-season scaler. Returns (1.0, 'disabled') when the feature is off."""
    if not s.seasonal_bias_enabled:
        return 1.0, "disabled"
    is_winter = now.month in WINTER_MONTHS
    if is_winter and side == Action.BUY:
        return 1.0, "winter-long (favored edge)"
    scaler = max(0.0, min(1.0, s.seasonal_offseason_scaler))
    season = "winter" if is_winter else "summer"
    role = "long" if side == Action.BUY else "short"
    return scaler, f"{season}-{role} off-edge (x{scaler:g})"
