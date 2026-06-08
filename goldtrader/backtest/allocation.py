"""Seasonal-core gold ALLOCATION strategy (V7 'lean into what works').

The lab proved gold has no intraday edge; the one thing that survives OOS is risk-managed
long-gold BETA + the winter seasonal tilt + drawdown controllers. This module turns that into
a tradeable, low-frequency allocation: each day the TARGET long-gold exposure (0..1 of a full
position) is the live overlay stack — `overlays.ensemble_size_scaler(date, BUY, closes)` =
winter tilt x TSMOM-regime x vol-target. The book holds that exposure, rebalancing ONLY when
the target shifts by >= `rebalance_threshold` (so it doesn't churn on tiny vol wiggles), and
pays a turnover cost on each rebalance. Long-only (shorting gold failed OOS). No look-ahead:
the target at day t uses closes through t-1 and the calendar date t, and earns day t's return.

Pure given a daily OHLC frame + Settings; reuses the SAME overlays the live bot would run, so
the backtest is a faithful preview of the live seasonal-core mode.
"""
from __future__ import annotations

import math

import pandas as pd

from ..config import Settings
from ..strategy.overlays import ensemble_size_scaler
from ..types import Action
from .seasonal import _cost_return


def run_allocation(daily: pd.DataFrame, s: Settings, *, rebalance_threshold: float = 0.10,
                   warmup: int | None = None) -> dict:
    """Daily long-gold allocation driven by the overlay stack. Returns the strategy daily
    returns, the buy&hold returns, per-day exposure, and the rebalance count."""
    closes = daily["close"].to_numpy()
    dates = daily.index
    warm = warmup if warmup is not None else max(s.tsmom_regime_lookback_days + 5, 260)

    cur = 0.0                      # current exposure (fraction of a full position)
    strat: list[float] = []
    bh: list[float] = []
    expo: list[float] = []
    rebalances = 0
    for t in range(warm, len(closes)):
        if closes[t - 1] <= 0:
            continue
        ret = float(closes[t]) / float(closes[t - 1]) - 1.0
        target, _ = ensemble_size_scaler(dates[t].to_pydatetime(), Action.BUY, closes[:t], s)
        cost = 0.0
        if abs(target - cur) >= rebalance_threshold:
            turnover = abs(target - cur)
            cost = turnover * _cost_return(s, float(closes[t - 1])) / 2.0   # one-way ~= half round-trip
            cur = target
            rebalances += 1
        strat.append(cur * ret - cost)
        bh.append(ret)
        expo.append(cur)
    return {"strat": strat, "bh": bh, "exposure": expo, "rebalances": rebalances,
            "avg_exposure": (sum(expo) / len(expo)) if expo else 0.0,
            "from": str(dates[warm].date()), "to": str(dates[-1].date())}


def annualized(daily_returns: list[float]) -> dict:
    """Annualized return / Sharpe + max drawdown (cumulative) from a daily-return series."""
    n = len(daily_returns)
    if n < 2:
        return {"ann_return": 0.0, "ann_sharpe": 0.0, "max_dd": 0.0, "total": 0.0}
    mean = sum(daily_returns) / n
    var = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    sd = math.sqrt(var)
    peak = cum = mdd = 0.0
    for r in daily_returns:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {"ann_return": mean * 252, "ann_sharpe": (mean / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "max_dd": mdd, "total": cum}
