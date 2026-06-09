"""Lightweight technical indicators computed from MT5 OHLC bars.

Pure numpy/pandas so they are unit-testable without a broker connection.
Inputs are numpy structured arrays from mt5.copy_rates_* (fields: open, high,
low, close) or plain pandas DataFrames with those columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _to_df(rates) -> pd.DataFrame:
    if isinstance(rates, pd.DataFrame):
        return rates
    return pd.DataFrame(rates)


def atr(rates, period: int = 14) -> float:
    """Average True Range (Wilder) of the most recent bar. Returns price units."""
    df = _to_df(rates)
    if len(df) < period + 1:
        return float("nan")
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing
    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
    return float(atr_series.iloc[-1])


def atr_spike_ratio(rates, period: int = 14, baseline: int = 20) -> float:
    """Latest ATR / its mean over the prior `baseline` bars. >1 = vol expanding;
    a large ratio means an unscheduled volatility shock. NaN if not enough bars."""
    df = _to_df(rates)
    if len(df) < period + baseline + 1:
        return float("nan")
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
    base = float(atr_series.iloc[-baseline - 1:-1].mean())
    if not base or base != base:
        return float("nan")
    return float(atr_series.iloc[-1] / base)


def adx(rates, period: int = 14) -> float:
    """Average Directional Index of the most recent bar."""
    df = _to_df(rates)
    if len(df) < 2 * period:
        return float("nan")
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_series = dx.ewm(alpha=1 / period, adjust=False).mean()
    return float(adx_series.iloc[-1])


def trend_direction(rates, fast: int = 20, slow: int = 50) -> int:
    """+1 uptrend, -1 downtrend, 0 undetermined (SMA crossover on closes)."""
    df = _to_df(rates)
    if len(df) < slow:
        return 0
    close = df["close"]
    f = close.rolling(fast).mean().iloc[-1]
    s = close.rolling(slow).mean().iloc[-1]
    if np.isnan(f) or np.isnan(s):
        return 0
    return 1 if f > s else (-1 if f < s else 0)


def ema(rates, period: int) -> pd.Series:
    """Exponential moving average of closes (full series)."""
    df = _to_df(rates)
    return df["close"].ewm(span=period, adjust=False).mean()


def ema_trend(rates, fast: int = 20, slow: int = 50) -> int:
    """+1 if EMA(fast) > EMA(slow) and price above slow; -1 mirror; else 0."""
    df = _to_df(rates)
    if len(df) < slow:
        return 0
    f = ema(df, fast).iloc[-1]
    s = ema(df, slow).iloc[-1]
    price = df["close"].iloc[-1]
    if np.isnan(f) or np.isnan(s):
        return 0
    if f > s and price >= s:
        return 1
    if f < s and price <= s:
        return -1
    return 0


def rsi(rates, period: int = 14) -> float:
    """Wilder RSI of the most recent bar (0-100)."""
    df = _to_df(rates)
    if len(df) < period + 1:
        return float("nan")
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return float(rsi_series.iloc[-1])


def macd(rates, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram) as the latest scalar values."""
    df = _to_df(rates)
    if len(df) < slow + signal:
        return float("nan"), float("nan"), float("nan")
    close = df["close"]
    macd_line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


def macd_cross(rates, fast: int = 12, slow: int = 26, signal: int = 9) -> int:
    """+1 if MACD crossed ABOVE its signal on the last closed bar, -1 below, 0 none."""
    df = _to_df(rates)
    if len(df) < slow + signal + 2:
        return 0
    close = df["close"]
    macd_line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    diff = macd_line - signal_line
    prev, last = diff.iloc[-2], diff.iloc[-1]
    if np.isnan(prev) or np.isnan(last):
        return 0
    if prev <= 0 < last:
        return 1
    if prev >= 0 > last:
        return -1
    return 0


def recent_swing(rates, lookback: int = 20) -> tuple[float, float]:
    """Return (swing_high, swing_low) over the last `lookback` bars."""
    df = _to_df(rates)
    window = df.tail(lookback)
    return float(window["high"].max()), float(window["low"].min())
