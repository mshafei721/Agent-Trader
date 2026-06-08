"""Seasonal-core allocation: rebalance threshold, turnover cost, no look-ahead, annualized math."""
import pandas as pd

from goldtrader.backtest.allocation import annualized, run_allocation
from goldtrader.config import Settings


def _daily(n, start=100.0, step=0.05):
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    closes = [start + step * i for i in range(n)]   # gentle uptrend, near-zero vol
    return pd.DataFrame({"open": closes, "high": [c + 0.1 for c in closes],
                         "low": [c - 0.1 for c in closes], "close": closes},
                        index=idx.tz_localize("UTC"))


def _s(**kw):
    base = dict(tsmom_regime_lookback_days=20, vol_target_annual=0.18, vol_lookback_days=10,
                seasonal_offseason_scaler=0.6, tsmom_downtrend_scaler=0.5)
    base.update(kw)
    return Settings(**base)


def test_runs_and_reports_shape():
    daily = _daily(120)
    res = run_allocation(daily, _s(), warmup=30)
    assert set(res) >= {"strat", "bh", "exposure", "rebalances", "avg_exposure"}
    assert len(res["strat"]) == len(res["bh"]) == len(res["exposure"]) == 120 - 30
    assert 0.0 <= res["avg_exposure"] <= 1.0          # long-only, damp-only -> within [0,1]
    assert res["rebalances"] >= 0


def test_exposure_never_exceeds_full():
    # uptrend + calm -> target ~1.0; exposure must stay in [0,1] (damp-only, no leverage)
    res = run_allocation(_daily(300), _s(), warmup=40)
    assert all(0.0 <= e <= 1.0 for e in res["exposure"])


def test_rebalance_threshold_limits_churn():
    daily = _daily(200)
    loose = run_allocation(daily, _s(), warmup=40, rebalance_threshold=0.01)["rebalances"]
    tight = run_allocation(daily, _s(), warmup=40, rebalance_threshold=0.5)["rebalances"]
    assert tight <= loose                              # a bigger threshold trades less


def test_annualized_math():
    a = annualized([0.001] * 252)                      # constant +0.1%/day, zero vol
    assert abs(a["ann_return"] - 0.252) < 1e-9
    assert a["max_dd"] == 0.0                           # monotonic up -> no drawdown
    assert a["ann_sharpe"] == 0.0                       # zero variance -> defined as 0
