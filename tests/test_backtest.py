"""Backtest lab: pure stats, the SL/TP exit walk, and an offline end-to-end replay (V7 P2.1)."""
import math

import numpy as np
import pandas as pd

from goldtrader.backtest import stats as st
from goldtrader.backtest.engine import _walk_exit, run_backtest
from goldtrader.config import Settings
from goldtrader.types import SymbolSpec

GOLD_SPEC = SymbolSpec(
    name="XAUUSD", digits=2, point=0.01, volume_min=0.01, volume_step=0.01,
    volume_max=35.0, contract_size=100.0, tick_value=1.0, tick_size=0.01,
    stops_level=20, freeze_level=10, filling_mode=3,
)


# ---------------- pure stats ----------------
def test_basic_stats():
    rs = [1.0, -1.0, 1.0, -1.0, 2.0]
    assert math.isclose(st.win_rate(rs), 0.6)
    assert math.isclose(st.expectancy(rs), 0.4)
    assert math.isclose(st.profit_factor(rs), 4.0 / 2.0)


def test_max_drawdown_and_streak():
    rs = [1.0, -1.0, -1.0, 1.0]      # cum 1,0,-1,0 ; peak 1 ; mdd = 1-(-1)=2
    assert math.isclose(st.max_drawdown_r(rs), 2.0)
    assert st.max_consecutive_losses([1, -1, -1, 1, -1]) == 2


def test_profit_factor_edges():
    assert st.profit_factor([1.0, 2.0]) == float("inf")  # no losers
    assert st.profit_factor([-1.0, -2.0]) == 0.0          # no winners


def test_wilson_ci_within_unit_interval():
    lo, hi = st.wilson_ci(6, 10)
    assert 0.0 <= lo < 0.6 < hi <= 1.0


def test_bootstrap_ci_is_deterministic_and_brackets_mean():
    rs = [0.5, -1.0, 1.5, -1.0, 2.0, -1.0, 1.0, -1.0, 0.8, 1.2]
    a = st.bootstrap_ci(rs, 500, seed=7)
    b = st.bootstrap_ci(rs, 500, seed=7)
    assert a == b                       # deterministic for a fixed seed
    assert a[0] <= st.expectancy(rs) <= a[1]


def test_bootstrap_ci_small_sample_returns_point():
    assert st.bootstrap_ci([1.0, -1.0], 500, seed=1) == (0.0, 0.0)


def test_monte_carlo_drawdown_ordering():
    rs = [1.0, -1.0, -1.0, 2.0, -1.0, 1.0, -1.0, 1.0, -1.0, 2.0]
    p50, p95 = st.monte_carlo_drawdown(rs, 500, seed=3)
    assert p50 <= p95


# ---------------- exit walk ----------------
def test_walk_exit_buy_takes_tp():
    highs = np.array([10.0, 12.0]); lows = np.array([9.5, 11.0]); closes = np.array([10.0, 11.5])
    px, reason, idx = _walk_exit(True, sl=9.0, tp=11.5, entry=10.0,
                                 highs=highs, lows=lows, closes=closes, entry_idx=1, n=2)
    assert reason == "tp" and px == 11.5 and idx == 1


def test_walk_exit_buy_takes_sl():
    highs = np.array([10.0, 10.2]); lows = np.array([9.5, 8.9]); closes = np.array([10.0, 9.0])
    px, reason, idx = _walk_exit(True, sl=9.0, tp=12.0, entry=10.0,
                                 highs=highs, lows=lows, closes=closes, entry_idx=1, n=2)
    assert reason == "sl" and px == 9.0


def test_walk_exit_tie_is_pessimistic_sl():
    highs = np.array([10.0, 12.5]); lows = np.array([10.0, 8.5]); closes = np.array([10.0, 11.0])
    px, reason, _ = _walk_exit(True, sl=9.0, tp=12.0, entry=10.0,
                               highs=highs, lows=lows, closes=closes, entry_idx=1, n=2)
    assert reason == "sl(both)" and px == 9.0


def test_walk_exit_eod_marks_to_market():
    highs = np.array([10.0, 10.1, 10.2]); lows = np.array([10.0, 9.9, 9.95])
    closes = np.array([10.0, 10.0, 10.15])
    px, reason, idx = _walk_exit(True, sl=9.0, tp=20.0, entry=10.0,
                                 highs=highs, lows=lows, closes=closes, entry_idx=1, n=3)
    assert reason == "eod" and px == 10.15 and idx == 2


# ---------------- offline end-to-end replay ----------------
def _trend_df(n: int, tf_s: int, t0: int, base: float, drift: float, amp: float, period: int):
    """Oscillating uptrend OHLC so MACD crosses fire and RSI dips below 70."""
    times, o, h, low_, c = [], [], [], [], []
    prev = base
    for i in range(n):
        mid = base + drift * i + amp * math.sin(i / period)
        op = prev
        cl = mid
        hi = max(op, cl) + amp * 0.15
        lo = min(op, cl) - amp * 0.15
        times.append(t0 + i * tf_s); o.append(op); h.append(hi); low_.append(lo); c.append(cl)
        prev = cl
    return pd.DataFrame({"time": times, "open": o, "high": h, "low": low_, "close": c})


def test_run_backtest_end_to_end_offline():
    t0 = 1_700_000_000
    # Independent rising+oscillating series per timeframe over the same span.
    m30 = _trend_df(1200, 1800, t0, base=2000.0, drift=0.12, amp=4.0, period=18)
    h1 = _trend_df(700, 3600, t0 - 3600 * 300, base=1990.0, drift=0.24, amp=5.0, period=16)
    h4 = _trend_df(400, 14400, t0 - 14400 * 200, base=1950.0, drift=0.9, amp=8.0, period=14)
    import MetaTrader5 as mt5  # type: ignore
    bars = {mt5.TIMEFRAME_M30: m30, mt5.TIMEFRAME_H1: h1, mt5.TIMEFRAME_H4: h4}

    s = Settings(backtest_warmup_bars=120, backtest_seed=11, atr_max_pct=99.0)
    res = run_backtest(s, bars, GOLD_SPEC, label="synthetic")
    # The replay must complete and produce a coherent result.
    assert res.stats is not None
    assert isinstance(res.trades, list)
    assert res.stats.trades == len(res.trades)
    assert len(res.trades) > 0  # the oscillating uptrend should trigger at least one buy
    for t in res.trades:
        assert t.side in ("BUY", "SELL")
        assert t.reason in ("tp", "sl", "sl(both)", "eod")
