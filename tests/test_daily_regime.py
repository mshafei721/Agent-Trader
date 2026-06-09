"""Daily trend-regime gate (research brief 2026-06-09) + Deflated Sharpe Ratio."""
import numpy as np
import pandas as pd

from goldtrader.backtest import stats
from goldtrader.backtest.daily_regime import DAY_S, pbull_series, regime_allows


def _daily(n=400, step=1.0, noise=0.2, start="2020-01-01"):
    rng = np.random.default_rng(7)
    closes = 2000.0 + np.cumsum(np.full(n, step) + rng.normal(0, noise, n))
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


# ---------------- pbull_series ----------------
def test_pbull_bull_market_high():
    pb = pbull_series(_daily(step=2.0, noise=0.1))
    assert pb, "expected values after warmup"
    vals = [v for _, v in pb[-20:]]
    assert all(v >= 0.52 for v in vals)


def test_pbull_bear_market_low():
    pb = pbull_series(_daily(step=-2.0, noise=0.1))
    vals = [v for _, v in pb[-20:]]
    assert all(v <= 0.48 for v in vals)


def test_pbull_bounded_and_causal():
    daily = _daily()
    pb = pbull_series(daily)
    assert all(0.0 <= v <= 1.0 for _, v in pb)
    # epoch must be AFTER the bar's own midnight (next day = value known post-close)
    first_epoch = pb[0][0]
    assert (first_epoch - int(daily.index[0].timestamp())) % DAY_S == 0
    assert first_epoch > int(daily.index[0].timestamp())


def test_regime_allows_direction_aware():
    assert regime_allows(True, 0.60, 0.52) is True    # bull regime -> long ok
    assert regime_allows(True, 0.40, 0.52) is False   # not bull -> no long
    assert regime_allows(False, 0.40, 0.52) is True   # bear regime -> short ok
    assert regime_allows(False, 0.50, 0.52) is False  # dead zone -> no short


# ---------------- deflated sharpe ----------------
def test_dsr_strong_edge_credible():
    rng = np.random.default_rng(1)
    rs = list(rng.normal(0.5, 1.0, 300))  # strong real edge
    assert stats.deflated_sharpe(rs, n_trials=5) > 0.95


def test_dsr_noise_not_credible():
    rng = np.random.default_rng(2)
    rs = list(rng.normal(0.0, 1.0, 300))  # pure noise
    assert stats.deflated_sharpe(rs, n_trials=20) < 0.95


def test_dsr_more_trials_lowers_credibility():
    rng = np.random.default_rng(3)
    rs = list(rng.normal(0.1, 1.0, 200))  # marginal edge
    assert stats.deflated_sharpe(rs, n_trials=50) < stats.deflated_sharpe(rs, n_trials=1)


def test_dsr_too_few_trades_zero():
    assert stats.deflated_sharpe([0.5, -0.2, 0.3], n_trials=5) == 0.0
