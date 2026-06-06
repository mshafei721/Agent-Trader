"""Live sizing-overlay ensemble (V7 — sustainable growth, not alpha).

The whole edge hunt found NO accessible alpha on gold for this bot — only one marginal return
tilt (winter) and several drawdown controllers. So the live engine compounds risk-managed gold
BETA with stacked, DAMP-ONLY sizing overlays, each grounded in the lab and each <= 1.0 so they:
compose by multiplication, feed the single risk_scaler chokepoint, compose with the defensive
self-heal scaler, and can NEVER breach the risk-% ceiling (they only ever make trading safer).

Three overlays:
  - seasonal   : winter-long tilt — the only return edge          [backtest/seasonal.py]
  - tsmom_regime: damp new LONGS in a ~12-month gold downtrend     [backtest/tsmom.py: halves maxDD]
  - vol_target : damp size when realized vol exceeds the target    (drawdown / tail control)

Pure + config-gated; the supervisor passes recent daily closes (it never reaches MT5 here).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from ..config import Settings
from ..types import Action
from .seasonal_bias import seasonal_size_scaler


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def trailing_return(closes: Sequence[float] | None, n: int) -> float | None:
    """`closes[-1] / closes[-1-n] - 1`, or None when there aren't enough bars / bad data."""
    if closes is None or len(closes) <= n:
        return None
    base = float(closes[-1 - n])
    if base <= 0:
        return None
    return float(closes[-1]) / base - 1.0


def realized_vol_annual(closes: Sequence[float] | None, n: int = 20) -> float | None:
    """Annualized stdev of the last `n` daily returns (sqrt(252) scaling). None if too few."""
    if closes is None or len(closes) <= n:
        return None
    rets = [float(closes[i]) / float(closes[i - 1]) - 1.0
            for i in range(len(closes) - n, len(closes)) if float(closes[i - 1]) > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def tsmom_regime_scaler(side: Action, closes: Sequence[float] | None, s: Settings) -> tuple[float, str]:
    """Damp new LONGS when gold's trailing ~12-month return is negative (the bear-avoidance that
    halved drawdown in the lab). Only touches BUYs; shorts/uptrend longs pass at 1.0."""
    if not s.tsmom_regime_enabled or side != Action.BUY:
        return 1.0, "n/a"
    r = trailing_return(closes, s.tsmom_regime_lookback_days)
    if r is None:
        return 1.0, "no-data"
    if r < 0:
        return _clamp(s.tsmom_downtrend_scaler), f"12mo downtrend ({r:+.1%})"
    return 1.0, f"12mo uptrend ({r:+.1%})"


def vol_target_scaler(closes: Sequence[float] | None, s: Settings) -> tuple[float, str]:
    """Damp size when realized annual vol exceeds the target (never levers up — clamp <=1)."""
    if not s.vol_target_enabled:
        return 1.0, "disabled"
    rv = realized_vol_annual(closes, s.vol_lookback_days)
    if rv is None or rv <= 0:
        return 1.0, "no-data"
    return _clamp(s.vol_target_annual / rv), f"vol {rv:.0%} vs target {s.vol_target_annual:.0%}"


def ensemble_size_scaler(now: datetime, side: Action, closes: Sequence[float] | None,
                         s: Settings) -> tuple[float, dict]:
    """Product of the three damp-only overlays + a per-overlay breakdown for logging."""
    season, sr = seasonal_size_scaler(now, side, s)
    regime, rr = tsmom_regime_scaler(side, closes, s)
    vol, vr = vol_target_scaler(closes, s)
    total = season * regime * vol
    return total, {
        "seasonal": round(season, 3), "tsmom": round(regime, 3), "vol": round(vol, 3),
        "reasons": {"seasonal": sr, "tsmom": rr, "vol": vr},
    }
