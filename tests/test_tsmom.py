"""TSMOM lab: monthly resample, causal signal, no look-ahead, turnover cost."""
import pandas as pd

from goldtrader.backtest import tsmom
from goldtrader.config import Settings


def _daily(dates, closes) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates]).tz_localize("UTC")
    c = list(closes)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def _no_cost() -> Settings:
    return Settings(backtest_cost_spread_points=0.0, backtest_cost_slippage_points=0.0)


def test_monthly_closes_takes_month_end():
    dates = pd.date_range("2021-01-01", "2021-03-31", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(len(dates))])
    m = tsmom.monthly_closes(daily)
    assert len(m) == 3                       # Jan, Feb, Mar
    assert m.iloc[0] == 100.0 + 30           # 2021-01-31 close


def test_tsmom_long_flat_goes_long_in_uptrend():
    # 24 months of steadily rising prices -> trailing 12mo return always > 0 -> always long
    dates = pd.date_range("2020-01-01", "2021-12-31", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i * 0.5 for i in range(len(dates))])
    trades = tsmom.tsmom_trades(daily, _no_cost(), lookback=12, allow_short=False)
    assert trades and all(t.ret >= 0 for t in trades)   # long into a rising series
    assert all("L" in t.label for t in trades)


def test_tsmom_long_flat_is_flat_after_downtrend():
    # rise for 13 months then fall: once trailing-12mo turns negative, long/flat -> flat (0 ret)
    n_up, n_dn = 400, 260
    up = [100.0 + i * 0.5 for i in range(n_up)]
    dn = [up[-1] - i * 0.5 for i in range(n_dn)]
    closes = up + dn
    dates = pd.date_range("2019-01-01", periods=len(closes), freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], closes)
    lf = tsmom.tsmom_trades(daily, _no_cost(), lookback=12, allow_short=False)
    ls = tsmom.tsmom_trades(daily, _no_cost(), lookback=12, allow_short=True)
    assert any(t.ret == 0.0 and t.label.endswith("0") for t in lf)   # flat months exist
    assert any(t.label.endswith("S") for t in ls)                    # short version goes short


def test_no_lookahead_entry_before_exit():
    dates = pd.date_range("2018-01-01", "2021-12-31", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + (i % 100) for i in range(len(dates))])
    for t in tsmom.tsmom_trades(daily, _no_cost()):
        assert t.entry_date < t.exit_date


def test_buy_hold_matches_window_length():
    dates = pd.date_range("2018-01-01", "2021-12-31", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i * 0.1 for i in range(len(dates))])
    bh = tsmom.buy_hold_trades(daily, _no_cost(), lookback=12)
    lf = tsmom.tsmom_trades(daily, _no_cost(), lookback=12)
    assert len(bh) == len(lf)   # same comparison window
    assert all(t.label == "buyhold" for t in bh)
