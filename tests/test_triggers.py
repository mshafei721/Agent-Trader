"""Pluggable M30 entry triggers (donchian breakout, pullback-to-EMA)."""
import pandas as pd

from goldtrader.config import Settings
from goldtrader.strategy.technical import TechnicalEngine


class _FakeClient:
    def get_rates(self, timeframe, count):
        return None


def _df(highs, lows, closes):
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes})


def _eng(**kw):
    return TechnicalEngine(Settings(**kw), _FakeClient())


def test_donchian_breakout_long_and_short():
    e = _eng(entry_trigger="donchian", donchian_lookback=4)
    highs = [10] * 8
    lows = [9] * 8
    # last close 11 breaks the prior-4-bar high (10) -> long fires when H4 up
    up = _df(highs, lows, [9.5] * 7 + [11.0])
    assert e._m30_trigger(up, h4_dir=1)[0] is True
    # last close 9.8 does NOT break 10 -> no fire
    flat = _df(highs, lows, [9.5] * 7 + [9.8])
    assert e._m30_trigger(flat, h4_dir=1)[0] is False
    # short: last close breaks below the prior-4-bar low (9)
    down = _df(highs, [9] * 7 + [7.0], [9.5] * 7 + [7.0])
    assert e._m30_trigger(down, h4_dir=-1)[0] is True


def test_donchian_warmup_returns_false():
    e = _eng(entry_trigger="donchian", donchian_lookback=20)
    small = _df([10] * 5, [9] * 5, [9.5] * 5)
    assert e._m30_trigger(small, h4_dir=1)[0] is False


def test_pullback_long_fires_on_touch_then_close_above():
    e = _eng(entry_trigger="pullback", pullback_ema=5)
    # steady ~10 then a higher close; EMA ~10.17, last low 9 <= EMA, last close 10.5 > EMA -> fire
    touch = _df([11] * 8, [9] * 8, [10] * 7 + [10.5])
    assert e._m30_trigger(touch, h4_dir=1)[0] is True
    # last low 10.3 never dips to the EMA (~10.17) -> no pullback
    no_touch = _df([11] * 8, [10.3] * 8, [10] * 7 + [10.5])
    assert e._m30_trigger(no_touch, h4_dir=1)[0] is False


def test_macd_cross_default_unchanged():
    # default mode must still be the macd_cross path (label proves the branch)
    e = _eng()
    assert e.s.entry_trigger == "macd_cross"
    # a flat series -> no fresh cross / no continuation -> no fire (label from the macd branch)
    fires, label, _ = e._m30_trigger(_df([10] * 60, [9] * 60, [10] * 60), h4_dir=1)
    assert fires is False
    assert "MACD" in label or "trigger" in label
