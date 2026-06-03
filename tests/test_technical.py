import numpy as np
import pandas as pd

from goldtrader.config import Settings
from goldtrader.risk import indicators
from goldtrader.strategy import technical
from goldtrader.strategy.technical import TechnicalEngine
from goldtrader.types import Action


# ---------------- indicators ----------------
def _series(values):
    return pd.DataFrame({
        "open": values, "high": [v + 0.5 for v in values],
        "low": [v - 0.5 for v in values], "close": values,
    })


def test_ema_trend_up_down():
    up = _series([2000 + i for i in range(80)])
    down = _series([2000 - i for i in range(80)])
    assert indicators.ema_trend(up, 20, 50) == 1
    assert indicators.ema_trend(down, 20, 50) == -1


def test_rsi_in_range():
    df = _series([2000 + (i % 5) for i in range(60)])
    r = indicators.rsi(df, 14)
    assert 0 <= r <= 100


def test_macd_cross_detects_upturn():
    # falling then rising -> a bullish MACD cross should appear near the end
    vals = [2000 - i for i in range(40)] + [1960 + 2 * i for i in range(40)]
    df = _series(vals)
    # at the last bar momentum is clearly up; cross should have fired recently
    line, sig, hist = indicators.macd(df)
    assert hist > 0


# ---------------- engine confluence (indicators monkeypatched) ----------------
class _FakeClient:
    def get_rates(self, timeframe, count):
        return _series([2000 + i for i in range(60)])  # content irrelevant when patched


def _engine():
    return TechnicalEngine(Settings(), _FakeClient())


def _patch(monkeypatch, *, h4, h1_dir, adx, macd_sign, cross, hist, rsi):
    monkeypatch.setattr(technical.indicators, "ema_trend",
                        lambda r, f, s: h4 if len(r) else h4)
    # ema_trend is called for both H4 and H1; differentiate via a counter
    calls = {"n": 0}

    def ema_trend(r, f, s):
        calls["n"] += 1
        return h4 if calls["n"] == 1 else h1_dir
    monkeypatch.setattr(technical.indicators, "ema_trend", ema_trend)
    monkeypatch.setattr(technical.indicators, "adx", lambda r, p: adx)
    monkeypatch.setattr(technical.indicators, "macd",
                        lambda r, f, s, sig: (macd_sign * 1.0, 0.0, hist))
    monkeypatch.setattr(technical.indicators, "macd_cross", lambda r, f, s, sig: cross)
    monkeypatch.setattr(technical.indicators, "rsi", lambda r, p: rsi)


def test_engine_buy_on_aligned_cross(monkeypatch):
    _patch(monkeypatch, h4=1, h1_dir=1, adx=25, macd_sign=1, cross=1, hist=0.5, rsi=55)
    sig = _engine().evaluate()
    assert sig.side == Action.BUY


def test_engine_none_when_h4_flat(monkeypatch):
    _patch(monkeypatch, h4=0, h1_dir=0, adx=25, macd_sign=0, cross=0, hist=0.0, rsi=50)
    assert _engine().evaluate().side is None


def test_engine_none_when_adx_too_low(monkeypatch):
    _patch(monkeypatch, h4=1, h1_dir=1, adx=10, macd_sign=1, cross=1, hist=0.5, rsi=55)
    assert _engine().evaluate().side is None


def test_engine_none_when_no_trigger(monkeypatch):
    # aligned setup but no cross and hist against direction -> no entry
    _patch(monkeypatch, h4=1, h1_dir=1, adx=25, macd_sign=1, cross=-1, hist=-0.5, rsi=55)
    assert _engine().evaluate().side is None


def test_engine_continuation_trigger(monkeypatch):
    # no fresh cross, but momentum continues up and RSI not extreme -> BUY
    _patch(monkeypatch, h4=1, h1_dir=1, adx=25, macd_sign=1, cross=0, hist=0.4, rsi=60)
    assert _engine().evaluate().side == Action.BUY


def test_engine_sell_path(monkeypatch):
    _patch(monkeypatch, h4=-1, h1_dir=-1, adx=30, macd_sign=-1, cross=-1, hist=-0.5, rsi=45)
    assert _engine().evaluate().side == Action.SELL


def test_engine_h1_neutral_allowed_balanced(monkeypatch):
    # balanced: H4 up, H1 NEUTRAL (0), trigger present -> BUY (strict would have blocked)
    _patch(monkeypatch, h4=1, h1_dir=0, adx=25, macd_sign=1, cross=1, hist=0.5, rsi=55)
    assert _engine().evaluate().side == Action.BUY


def test_engine_h1_opposes_blocks(monkeypatch):
    # H4 up but H1 clearly DOWN -> opposing -> no trade
    _patch(monkeypatch, h4=1, h1_dir=-1, adx=25, macd_sign=1, cross=1, hist=0.5, rsi=55)
    assert _engine().evaluate().side is None


def test_engine_sell_h1_neutral_downtrend(monkeypatch):
    # the live scenario: H4 down, H1 neutral, M30 sell trigger -> SELL
    _patch(monkeypatch, h4=-1, h1_dir=0, adx=22, macd_sign=-1, cross=-1, hist=-0.4, rsi=40)
    assert _engine().evaluate().side == Action.SELL
