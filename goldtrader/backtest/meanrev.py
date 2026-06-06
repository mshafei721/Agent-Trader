"""Mean-reversion entry experiment (orthogonal to the EMA/ADX/MACD trend strategy).

Hypothesis: gold (XAUUSD) M30 has a tradeable counter-trend snap-back when price
stretches to a band/oscillator extreme *inside a non-trending regime*. This module
generates such signals causally and replays them through the EXACT same exit machinery
as the trend backtest (engine._walk_exit for fixed SL/TP, engine._simulate_managed_exit
for the live management stack), so results are directly comparable and cost-faithful.

No look-ahead (the cardinal rule): every indicator is rolling/ewm only (past+current bar),
the entry decision is taken on the CLOSE of bar i, and the fill happens at the OPEN of bar
i+1. Single position at a time — after an exit we resume from exit_idx+1, mirroring the
engine. Cost is the engine's `cost_r_unit / sl_distance` (x1.5 if a managed scale-out fired).

This is a standalone research module. It imports the engine's exit/cost helpers and the
SymbolSpec; it does NOT modify any existing file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Settings
from ..types import Action, SymbolSpec
from . import stats as stats_mod
from .engine import (
    _efficiency_ratio,
    _precompute_management_series,
    _simulate_managed_exit,
    _walk_exit,
)


# --------------------------------------------------------------------------------------
# Causal indicators (rolling/ewm only — never a future bar)
# --------------------------------------------------------------------------------------

def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Wilder ATR, full causal series (mirrors indicators.atr / engine precompute)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _rsi_series(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI, full causal series."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bollinger(close: pd.Series, period: int, n_std: float):
    """(mid, upper, lower) Bollinger bands — simple MA +/- n_std * rolling std (causal)."""
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    return mid, mid + n_std * sd, mid - n_std * sd


# --------------------------------------------------------------------------------------
# Variant definition
# --------------------------------------------------------------------------------------

@dataclass
class MRVariant:
    """A mean-reversion configuration.

    trigger: "bb" (close beyond Bollinger band), "rsi2" (RSI(2) extreme), or
             "bb_rsi" (both must agree).
    regime: None (no filter), "er" (Kaufman ER below max — i.e. choppy/ranging), or
            "adx" (M30 ADX below max — non-trending).
    target: "mid" (revert to the band/MA midline) or "rr" (fixed R:R take-profit).
    """
    label: str
    trigger: str = "bb"
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 2
    rsi_buy: float = 5.0
    rsi_sell: float = 95.0
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    rr: float = 1.5                 # used when target == "rr"
    target: str = "mid"
    regime: str | None = None
    er_period: int = 20
    er_max: float = 0.30           # ER below this = chop (mean-revert allowed)
    adx_period: int = 14
    adx_max: float = 20.0


@dataclass
class MRResult:
    label: str
    r_multiples: list[float] = field(default_factory=list)
    stats: object = None
    n_signals: int = 0  # raw triggers before single-position/regime suppression


# --------------------------------------------------------------------------------------
# Signal precompute
# --------------------------------------------------------------------------------------

def _adx_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> np.ndarray:
    """Wilder ADX, full causal series (mirrors indicators.adx)."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().to_numpy()


def _build_signals(m30: pd.DataFrame, v: MRVariant) -> dict:
    """Per-bar arrays needed to decide & size an entry. All causal."""
    high, low, close = m30["high"], m30["low"], m30["close"]
    mid, upper, lower = _bollinger(close, v.bb_period, v.bb_std)
    rsi = _rsi_series(close, v.rsi_period)
    atr = _atr_series(high, low, close, v.atr_period)

    regime_ok = np.ones(len(m30), dtype=bool)
    if v.regime == "er":
        er = _efficiency_ratio(close, v.er_period)
        regime_ok = er <= v.er_max
    elif v.regime == "adx":
        adx = _adx_series(high, low, close, v.adx_period)
        regime_ok = adx <= v.adx_max  # NaN warmup -> False (suppressed)

    return {
        "close": close.to_numpy(), "mid": mid.to_numpy(),
        "upper": upper.to_numpy(), "lower": lower.to_numpy(),
        "rsi": rsi.to_numpy(), "atr": atr.to_numpy(),
        "regime_ok": regime_ok,
    }


def _signal_at(sig: dict, i: int, v: MRVariant) -> Action | None:
    """BUY/SELL/None at bar i (decision on the close of bar i). No future data used."""
    if not bool(sig["regime_ok"][i]):
        return None
    c = sig["close"][i]
    up, lo, rsi, atr = sig["upper"][i], sig["lower"][i], sig["rsi"][i], sig["atr"][i]
    if np.isnan(up) or np.isnan(lo) or np.isnan(atr) or atr <= 0:
        return None

    bb_buy = c < lo
    bb_sell = c > up
    rsi_buy = (not np.isnan(rsi)) and rsi < v.rsi_buy
    rsi_sell = (not np.isnan(rsi)) and rsi > v.rsi_sell

    if v.trigger == "bb":
        buy, sell = bb_buy, bb_sell
    elif v.trigger == "rsi2":
        buy, sell = rsi_buy, rsi_sell
    elif v.trigger == "bb_rsi":
        buy, sell = (bb_buy and rsi_buy), (bb_sell and rsi_sell)
    else:
        raise ValueError(f"unknown trigger {v.trigger!r}")

    if buy:
        return Action.BUY
    if sell:
        return Action.SELL
    return None


# --------------------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------------------

def run_meanrev(s: Settings, bars_by_tf: dict[int, pd.DataFrame], spec: SymbolSpec,
                variant: MRVariant, *, model_management: bool = False,
                start_idx: int | None = None, end_idx: int | None = None) -> MRResult:
    """Replay a mean-reversion variant causally over cached M30 history.

    model_management=False -> fixed SL/TP via engine._walk_exit (the natural model for a
    revert-to-mean target). True -> the live management stack via engine._simulate_managed_exit.
    start_idx/end_idx restrict the decision range (for split-half OOS); exits may still walk
    past end_idx, which is correct (a trade opened in-sample finishes wherever it finishes).
    """
    from ..strategy.technical import _tf
    trigger_tf = _tf(s.trigger_timeframe)
    m30 = bars_by_tf[trigger_tf]
    n = len(m30)

    times = m30["time"].to_numpy()
    opens = m30["open"].to_numpy()
    highs = m30["high"].to_numpy()
    lows = m30["low"].to_numpy()
    closes = m30["close"].to_numpy()

    sig = _build_signals(m30, variant)
    mgmt = _precompute_management_series(m30, s) if model_management else None
    cost_r_unit = (s.backtest_cost_spread_points + 2 * s.backtest_cost_slippage_points) * spec.point

    lo_i = max(s.backtest_warmup_bars, variant.bb_period, variant.er_period, 1)
    if start_idx is not None:
        lo_i = max(lo_i, start_idx)
    hi_i = (n - 1) if end_idx is None else min(n - 1, end_idx)

    r_list: list[float] = []
    n_signals = 0
    i = lo_i
    while i < hi_i:
        side = _signal_at(sig, i, variant)
        if side is None:
            i += 1
            continue
        n_signals += 1

        is_buy = side == Action.BUY
        atr_i = float(sig["atr"][i])
        sl_distance = variant.sl_atr_mult * atr_i
        if sl_distance <= 0:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(opens[entry_idx])
        sl = entry - sl_distance if is_buy else entry + sl_distance

        if variant.target == "mid":
            mid = float(sig["mid"][i])
            # revert-to-mean target; if the mean is on the wrong side (degenerate), fall back to 1R
            tp_distance = abs(mid - entry)
            if tp_distance <= 0 or (is_buy and mid <= entry) or (not is_buy and mid >= entry):
                tp_distance = sl_distance
        else:  # "rr"
            tp_distance = variant.rr * sl_distance
        tp = entry + tp_distance if is_buy else entry - tp_distance

        if model_management:
            r_gross, _reason, exit_idx, _exit_price, scaled = _simulate_managed_exit(
                s, is_buy, entry, sl, tp, sl_distance, entry_idx, n,
                highs, lows, closes, mgmt)
            cost_r = cost_r_unit / sl_distance * (1.5 if scaled else 1.0)
        else:
            exit_price, _reason, exit_idx = _walk_exit(
                is_buy, sl, tp, entry, highs, lows, closes, entry_idx, n)
            r_gross = ((exit_price - entry) if is_buy else (entry - exit_price)) / sl_distance
            cost_r = cost_r_unit / sl_distance

        r_list.append(r_gross - cost_r)
        i = exit_idx + 1  # single position: resume after the trade closes

    res = MRResult(label=variant.label, r_multiples=r_list, n_signals=n_signals)
    res.stats = stats_mod.compute(r_list, seed=s.backtest_seed)
    return res


def default_variants() -> list[MRVariant]:
    """A small, deliberate set: pure BB-revert, pure RSI(2)-revert, both with regime
    pre-filters, plus a fixed-R:R cut and a stricter combined trigger. Tuned by hypothesis,
    NOT fitted to the sample."""
    return [
        MRVariant("bb20-2.0_mid", trigger="bb", target="mid"),
        MRVariant("rsi2_5/95_mid", trigger="rsi2", target="mid"),
        MRVariant("bb20-2.0_er<.30_mid", trigger="bb", target="mid", regime="er", er_max=0.30),
        MRVariant("rsi2_er<.30_mid", trigger="rsi2", target="mid", regime="er", er_max=0.30),
        MRVariant("bb20-2.0_adx<20_mid", trigger="bb", target="mid", regime="adx", adx_max=20.0),
        MRVariant("bb+rsi2_er<.30_mid", trigger="bb_rsi", target="mid", regime="er", er_max=0.30),
        MRVariant("bb20-2.0_rr1.5", trigger="bb", target="rr", rr=1.5),
    ]
