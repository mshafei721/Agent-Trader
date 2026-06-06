"""Macro real-yield+dollar regime harness: alignment (no look-ahead), signal logic, trades.

Pure logic over synthetic frames — no network, no FRED key. (The live #3 verdict comes from
scripts/exp_macro_regime.py once a FRED key is configured.)
"""
import pandas as pd

from goldtrader.backtest import macro_regime as mr
from goldtrader.config import Settings


def _gold(dates, closes) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates]).tz_localize("UTC")
    c = list(closes)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def _series(dates, vals) -> pd.Series:
    return pd.Series(list(vals), index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates]))


def test_align_macro_lags_one_day_no_lookahead():
    dates = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    gold = _gold(dates, [100, 101, 102, 103])
    real = _series(dates, [1.0, 2.0, 3.0, 4.0])
    usd = _series(dates, [90.0, 91.0, 92.0, 93.0])
    df = mr.align_macro(gold, real, usd)
    # first row macro is NaN (shifted), and each row carries the PRIOR day's macro value
    assert pd.isna(df["real"].iloc[0])
    assert df["real"].iloc[1] == 1.0   # 2021-01-05 sees real from 2021-01-04
    assert df["real"].iloc[3] == 3.0   # 2021-01-07 sees real from 2021-01-06
    assert df["usd"].iloc[2] == 91.0


def test_regime_position_both_falling_is_long():
    n = 30
    dates = pd.bdate_range("2021-01-01", periods=n)
    gold = _gold([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(n)])
    real = _series([d.strftime("%Y-%m-%d") for d in dates], [5.0 - 0.1 * i for i in range(n)])  # falling
    usd = _series([d.strftime("%Y-%m-%d") for d in dates], [100.0 - 0.5 * i for i in range(n)])  # falling
    df = mr.align_macro(gold, real, usd)
    pos = mr.regime_position(df, lookback=5, use_regime=False)
    assert pos.iloc[-1] == 1   # both fell over the lookback -> long


def test_regime_position_both_rising_is_short():
    n = 30
    dates = pd.bdate_range("2021-01-01", periods=n)
    gold = _gold([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(n)])
    real = _series([d.strftime("%Y-%m-%d") for d in dates], [1.0 + 0.1 * i for i in range(n)])  # rising
    usd = _series([d.strftime("%Y-%m-%d") for d in dates], [90.0 + 0.5 * i for i in range(n)])  # rising
    df = mr.align_macro(gold, real, usd)
    pos = mr.regime_position(df, lookback=5, use_regime=False)
    assert pos.iloc[-1] == -1   # both rose -> short
    # mixed -> 0
    usd2 = _series([d.strftime("%Y-%m-%d") for d in dates], [90.0 - 0.5 * i for i in range(n)])  # falling
    df2 = mr.align_macro(gold, real, usd2)
    assert mr.regime_position(df2, lookback=5, use_regime=False).iloc[-1] == 0


def test_regime_trades_long_profits_on_rising_gold():
    n = 20
    dates = pd.bdate_range("2021-01-01", periods=n)
    gold = _gold([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(n)])
    df = mr.align_macro(gold, _series([d.strftime("%Y-%m-%d") for d in dates], [1.0] * n),
                        _series([d.strftime("%Y-%m-%d") for d in dates], [1.0] * n))
    pos = pd.Series(0, index=df.index, dtype=int)
    pos.iloc[2:] = 1  # force long from day 2
    s = Settings(backtest_cost_spread_points=0.0, backtest_cost_slippage_points=0.0)
    trades = mr.regime_trades(df, pos, s, hold=5)
    assert trades and trades[0].ret > 0          # long into a rising series -> profit
    assert trades[0].entry_date < trades[0].exit_date  # no look-ahead


def test_regime_trades_short_exits_on_flip():
    n = 20
    dates = pd.bdate_range("2021-01-01", periods=n)
    gold = _gold([d.strftime("%Y-%m-%d") for d in dates], [100.0] * n)  # flat
    df = mr.align_macro(gold, _series([d.strftime("%Y-%m-%d") for d in dates], [1.0] * n),
                        _series([d.strftime("%Y-%m-%d") for d in dates], [1.0] * n))
    pos = pd.Series(0, index=df.index, dtype=int)
    pos.iloc[2] = 1
    pos.iloc[4:] = -1  # flip to short -> long trade should exit early at the flip (index 4)
    s = Settings(backtest_cost_spread_points=0.0, backtest_cost_slippage_points=0.0)
    trades = mr.regime_trades(df, pos, s, hold=10)
    assert trades[0].exit_date == df.index[4].strftime("%Y-%m-%d")


def test_date_split():
    trades = [mr.SeasonTrade("2019-06-01", "2019-06-10", 0.0, "a"),
              mr.SeasonTrade("2021-06-01", "2021-06-10", 0.0, "b")]
    pre, post = mr.date_split(trades, cut="2020-01-01")
    assert [t.label for t in pre] == ["a"]
    assert [t.label for t in post] == ["b"]
