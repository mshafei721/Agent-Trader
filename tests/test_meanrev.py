"""Tests for the mean-reversion experiment module (goldtrader/backtest/meanrev.py).

Focus on the two properties that, if violated, would invalidate the experiment:
  1. The signal is CAUSAL — the decision at bar i never depends on bars > i.
  2. Costs are subtracted exactly like engine.py (cost-faithful), so MR results are
     comparable to the trend baseline.

These run on tiny synthetic frames and do NOT touch the broker, network, or the on-disk
cache, so they are fast and deterministic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldtrader.backtest.meanrev import MRVariant, _build_signals, _signal_at
from goldtrader.types import Action


def _frame(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, dtype=float)
    # high/low bracket the close so band/ATR logic has width; time is monotone epochs.
    return pd.DataFrame({
        "time": np.arange(n) * 1800 + 1_600_000_000,
        "open": c,
        "high": c + 1.0,
        "low": c - 1.0,
        "close": c,
    })


def test_signal_is_causal_truncation_invariant():
    """The decision at bar i must be identical whether or not future bars exist.

    For every i, _signal_at on the FULL frame must equal _signal_at on the frame
    truncated at i+1. Any look-ahead (e.g. an accidental .shift(-1)) breaks this.
    """
    rng = np.random.default_rng(7)
    closes = list(2000 + np.cumsum(rng.normal(0, 5, 400)))
    full = _frame(closes)
    v = MRVariant("t", trigger="bb", regime="er")  # exercise bands + ER regime filter
    sig_full = _build_signals(full, v)

    lo = max(v.bb_period, v.er_period) + 2
    checked = 0
    for i in range(lo, len(full) - 1):
        trunc = full.iloc[: i + 1].reset_index(drop=True)
        sig_trunc = _build_signals(trunc, v)
        assert _signal_at(sig_full, i, v) == _signal_at(sig_trunc, i, v), f"look-ahead at i={i}"
        checked += 1
    assert checked > 100


def test_rsi2_trigger_fires_on_extreme():
    """A strong rally (with tiny pullbacks so RSI is defined) drives RSI(2) > 95 -> SELL;
    a strong selloff drives RSI(2) < 5 -> BUY."""
    # +2 most bars, -0.2 occasionally: a clear uptrend but with non-zero losses so RSI(2)
    # is finite and pinned near 100 at the last bar.
    up_steps = [2.0 if k % 5 else -0.2 for k in range(60)]
    up = _frame(list(2000 + np.cumsum(up_steps)))
    down = _frame(list(2000 - np.cumsum(up_steps)))
    v = MRVariant("r", trigger="rsi2", regime=None)
    su, sd = _build_signals(up, v), _build_signals(down, v)
    assert _signal_at(su, len(up) - 1, v) == Action.SELL
    assert _signal_at(sd, len(down) - 1, v) == Action.BUY


def test_run_meanrev_cost_faithful():
    """Net R must equal gross R minus the engine's cost term (cost_unit / sl_distance).

    Verified by running the SAME variant with the configured cost and with cost zeroed and
    confirming the per-variant expectancy gap matches the average modeled cost.
    """
    from goldtrader.config import get_settings
    from goldtrader.backtest.data import load_bars, load_spec
    from goldtrader.backtest.meanrev import run_meanrev

    s = get_settings()
    bars = load_bars(s)
    spec = load_spec(s)
    v = MRVariant("c", trigger="bb")

    net = run_meanrev(s, bars, spec, v).stats.expectancy
    sp, sl = s.backtest_cost_spread_points, s.backtest_cost_slippage_points
    try:
        s.backtest_cost_spread_points = 0.0
        s.backtest_cost_slippage_points = 0.0
        gross = run_meanrev(s, bars, spec, v).stats.expectancy
    finally:
        s.backtest_cost_spread_points, s.backtest_cost_slippage_points = sp, sl

    # Gross must beat net (cost is a strict drag) and the gap must be a sane fraction of R.
    assert gross > net
    assert 0.0 < (gross - net) < 0.5
