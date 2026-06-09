"""Daily trend-regime gate research (research brief 2026-06-09; arXiv:2511.08571).

Hypothesis: nearly all gold trend profits occur when a simple DAILY regime signal says
"bull". pbull = 0.6 x logistic(z of EMA-slope) + 0.4 x 1[close > close 50d ago], computed
on daily closes. Gate: BUY entries need pbull >= thr; SELL entries need pbull <= 1 - thr.

Honesty rails (same as the other labs):
  * No look-ahead: day t's value becomes usable only AFTER day t's close (epoch + 1 day
    in the backtest; live use drops the still-forming current daily bar).
  * A/B against the identical engine run without the gate; IS/OOS date split.
  * Deflated Sharpe reported with the experiment's trial count.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..logging_setup import get_logger

log = get_logger("goldtrader.backtest.daily_regime")

DAY_S = 86400


def _pbull(close: pd.Series, ema_period: int, slope_z_window: int,
           mom_lookback: int, w_slope: float, w_mom: float) -> pd.Series:
    """pbull per bar in [0,1] (NaN during warmup)."""
    ema = close.ewm(span=ema_period, adjust=False).mean()
    slope = ema.diff()
    mu = slope.rolling(slope_z_window).mean()
    sd = slope.rolling(slope_z_window).std()
    z = (slope - mu) / sd.replace(0, np.nan)
    slope_prob = 1.0 / (1.0 + np.exp(-z))
    mom = (close > close.shift(mom_lookback)).astype(float)
    return w_slope * slope_prob + w_mom * mom


def pbull_series(daily: pd.DataFrame, *, ema_period: int = 50, slope_z_window: int = 252,
                 mom_lookback: int = 50, w_slope: float = 0.6, w_mom: float = 0.4,
                 ) -> list[tuple[int, float]]:
    """Causal (epoch, pbull) list from daily bars (DatetimeIndex, 'close' column).

    pbull in [0,1]; the epoch is the bar's midnight + 1 day = first moment the completed
    daily close is actually known. NaN warmup rows are dropped."""
    pb = _pbull(daily["close"], ema_period, slope_z_window, mom_lookback, w_slope, w_mom)
    out: list[tuple[int, float]] = []
    for ts, v in pb.items():
        if v != v:  # NaN warmup
            continue
        out.append((int(ts.timestamp()) + DAY_S, float(v)))
    return out


def pbull_latest(rates, *, drop_last: bool = True, ema_period: int = 50,
                 slope_z_window: int = 252, mom_lookback: int = 50,
                 w_slope: float = 0.6, w_mom: float = 0.4) -> float | None:
    """Latest causal pbull from live D1 rates (MT5 structured array or DataFrame).

    drop_last=True discards the final row — live D1 history ends with the still-FORMING
    current day, which must not leak into the signal. None when warmup is incomplete."""
    df = rates if isinstance(rates, pd.DataFrame) else pd.DataFrame(rates)
    close = df["close"]
    if drop_last:
        close = close.iloc[:-1]
    if len(close) < max(slope_z_window, mom_lookback) + 2:
        return None
    pb = _pbull(close, ema_period, slope_z_window, mom_lookback, w_slope, w_mom)
    v = float(pb.iloc[-1])
    return None if v != v else v


def regime_allows(side_is_buy: bool, pb: float, threshold: float) -> bool:
    """Direction-aware gate: longs need a bull regime, shorts need a bear regime."""
    if side_is_buy:
        return pb >= threshold
    return pb <= 1.0 - threshold
