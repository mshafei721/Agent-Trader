"""Seasonal/calendar experiment harness: window selection, cost subtraction, no look-ahead.

Pure calendar logic over synthetic daily frames — no network, no cache. (The live edge
verdict comes from scripts/exp_seasonal.py over real Dukascopy daily data.)
"""
import pandas as pd

from goldtrader.backtest import seasonal
from goldtrader.config import Settings


def _daily(dates, closes) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates]).tz_localize("UTC")
    c = list(closes)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def _no_cost() -> Settings:
    return Settings(backtest_cost_spread_points=0.0, backtest_cost_slippage_points=0.0)


def test_winter_window_bounds_and_direction():
    s = _no_cost()
    dates = pd.date_range("2020-10-20", "2021-05-10", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(len(dates))])
    winter, _ = seasonal.winter_summer_trades(daily, s)
    w = [t for t in winter if t.label.startswith("winter 2020")]
    assert len(w) == 1
    assert w[0].entry_date == "2020-11-01"   # first trading day >= Nov 1
    assert w[0].exit_date == "2021-04-30"    # last trading day <= Apr 30
    assert w[0].ret > 0                       # rising series -> positive


def test_cost_is_subtracted_as_return():
    s = Settings(backtest_cost_spread_points=30.0, backtest_cost_slippage_points=5.0)
    dates = pd.date_range("2020-10-20", "2021-05-10", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [2000.0] * len(dates))  # flat
    winter, _ = seasonal.winter_summer_trades(daily, s)
    w = [t for t in winter if t.label.startswith("winter 2020")][0]
    # flat price -> raw return 0; net = -cost = -(30 + 2*5)*0.01/2000
    assert abs(w.ret - (-(40.0 * 0.01 / 2000.0))) < 1e-12


def test_turn_of_month_window_selection():
    s = _no_cost()
    dates = pd.bdate_range("2021-01-01", "2021-02-26")  # Jan + Feb trading (business) days
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(len(dates))])
    tom, rest = seasonal.turn_of_month_trades(daily, s)
    jan = [d for d in dates if d.month == 1]
    feb = [d for d in dates if d.month == 2]
    assert len(tom) == 1
    assert tom[0].entry_date == jan[-2].strftime("%Y-%m-%d")   # 2nd-to-last Jan trading day
    assert tom[0].exit_date == feb[2].strftime("%Y-%m-%d")     # 3rd Feb trading day
    assert rest and rest[0].entry_date == jan[2].strftime("%Y-%m-%d")  # mid-month complement


def test_no_lookahead_entry_before_exit():
    s = _no_cost()
    dates = pd.date_range("2015-01-01", "2020-12-31", freq="D")
    daily = _daily([d.strftime("%Y-%m-%d") for d in dates], [100.0 + (i % 50) for i in range(len(dates))])
    winter, summer = seasonal.winter_summer_trades(daily, s)
    tom, rest = seasonal.turn_of_month_trades(daily, s)
    for t in winter + summer + tom + rest:
        assert t.entry_date < t.exit_date


def test_split_is_chronological_half():
    trades = [seasonal.SeasonTrade(f"d{i}", f"d{i}b", 0.0, str(i)) for i in range(10)]
    a, b = seasonal.split(trades)
    assert [t.label for t in a] == [str(i) for i in range(5)]
    assert [t.label for t in b] == [str(i) for i in range(5, 10)]
