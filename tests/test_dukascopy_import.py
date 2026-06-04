"""Dukascopy importer pure helpers (V7 P2.2b) — no network."""
import pandas as pd

from goldtrader.backtest.dukascopy_import import _resample, _to_bt


def _m30(n: int):
    idx = pd.date_range("2025-01-01 00:00", periods=n, freq="30min", tz="UTC")
    # simple rising OHLC
    base = range(n)
    return pd.DataFrame({
        "open": [100.0 + i for i in base],
        "high": [100.5 + i for i in base],
        "low": [99.5 + i for i in base],
        "close": [100.2 + i for i in base],
        "volume": [1.0] * n,
    }, index=idx)


def test_to_bt_columns_and_epoch():
    df = _to_bt(_m30(3))
    assert list(df.columns) == ["time", "open", "high", "low", "close"]
    # 2025-01-01 00:00 UTC epoch
    assert df["time"].iloc[0] == 1735689600
    assert df["time"].iloc[1] - df["time"].iloc[0] == 1800  # 30 min
    assert df["time"].is_monotonic_increasing


def test_resample_h1_aggregates_two_m30():
    h1 = _resample(_m30(4), "1h")
    assert len(h1) == 2
    # first H1 = first two M30 bars: open=first, high=max, low=min, close=2nd close
    assert h1["open"].iloc[0] == 100.0
    assert h1["high"].iloc[0] == 101.5   # max(100.5, 101.5)
    assert h1["low"].iloc[0] == 99.5     # min(99.5, 100.5)
    assert h1["close"].iloc[0] == 101.2  # close of the 2nd M30 bar


def test_resample_h4_aggregates_eight_m30():
    h4 = _resample(_m30(8), "4h")
    assert len(h4) == 1
    assert h4["open"].iloc[0] == 100.0
    assert h4["close"].iloc[0] == 107.2  # close of the 8th bar
