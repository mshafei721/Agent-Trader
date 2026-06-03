import numpy as np
import pandas as pd

from goldtrader.risk import indicators


def _trending_df(n=120, start=2000.0, step=1.0, noise=0.2):
    rng = np.random.default_rng(42)
    closes = start + np.cumsum(np.full(n, step) + rng.normal(0, noise, n))
    highs = closes + 0.5
    lows = closes - 0.5
    opens = closes - step
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


def test_atr_positive():
    df = _trending_df()
    a = indicators.atr(df, 14)
    assert a > 0


def test_adx_uptrend_strong():
    df = _trending_df(step=2.0, noise=0.1)
    val = indicators.adx(df, 14)
    assert val == val  # not NaN
    assert val > 20  # strong trend


def test_trend_direction_up():
    df = _trending_df(step=1.5, noise=0.1)
    assert indicators.trend_direction(df) == 1


def test_trend_direction_down():
    df = _trending_df(step=-1.5, noise=0.1)
    assert indicators.trend_direction(df) == -1


def test_atr_insufficient_data_is_nan():
    df = _trending_df(n=5)
    a = indicators.atr(df, 14)
    assert a != a  # NaN
