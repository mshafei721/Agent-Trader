"""Fast-tier multi-timeframe technical engine.

Top-down confluence:
  H4 (filter)  -> macro trend must be clearly up or down (EMA stack)
  H1 (setup)   -> trend aligned with H4 + ADX trend strength + MACD sign agree
  M30 (trigger)-> a fresh MACD cross in the trend direction, or a momentum
                  continuation (RSI not extreme).

Deterministic and free (no LLM). Every gate appends to `reasons` for auditing.
Returns a TechSignal(side, score, reasons). side is None when no entry.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import MetaTrader5 as mt5  # type: ignore

from ..config import Settings
from ..logging_setup import get_logger
from ..risk import indicators
from ..types import Action, TechSignal

if TYPE_CHECKING:
    from ..mt5.client import MT5Client

log = get_logger("goldtrader.technical")

_TF_MAP = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def _tf(name: str) -> int:
    return _TF_MAP.get(name.upper(), mt5.TIMEFRAME_H1)


class TechnicalEngine:
    def __init__(self, settings: Settings, client: "MT5Client"):
        self.s = settings
        self.client = client

    def read(self) -> dict:
        """Return the raw multi-timeframe indicator reads (for diagnostics/CLI)."""
        s = self.s
        h4 = self.client.get_rates(_tf(s.filter_timeframe), 200)
        h1 = self.client.get_rates(_tf(s.setup_timeframe), 200)
        m30 = self.client.get_rates(_tf(s.trigger_timeframe), 200)
        return {
            "h4_trend": indicators.ema_trend(h4, s.ema_fast, s.ema_slow),
            "h1_trend": indicators.ema_trend(h1, s.ema_fast, s.ema_slow),
            "h1_adx": indicators.adx(h1, s.adx_period),
            "h1_macd": indicators.macd(h1, s.macd_fast, s.macd_slow, s.macd_signal)[0],
            "m30_cross": indicators.macd_cross(m30, s.macd_fast, s.macd_slow, s.macd_signal),
            "m30_rsi": indicators.rsi(m30, s.rsi_period),
        }

    def evaluate(self) -> TechSignal:
        s = self.s
        reasons: list[str] = []
        h4 = self.client.get_rates(_tf(s.filter_timeframe), 200)
        h1 = self.client.get_rates(_tf(s.setup_timeframe), 200)
        m30 = self.client.get_rates(_tf(s.trigger_timeframe), 200)

        # --- H4 filter ---
        h4_dir = indicators.ema_trend(h4, s.ema_fast, s.ema_slow)
        reasons.append(f"{s.filter_timeframe} ema_trend={h4_dir}")
        if h4_dir == 0:
            return TechSignal(None, 0.0, reasons + ["no clear H4 trend"])

        # --- H1 setup (balanced: must NOT oppose H4; ADX trend-strength required) ---
        h1_dir = indicators.ema_trend(h1, s.ema_fast, s.ema_slow)
        adx1 = indicators.adx(h1, s.adx_period)
        reasons.append(f"{s.setup_timeframe} dir={h1_dir} adx={adx1:.1f}")
        if h1_dir == -h4_dir:
            return TechSignal(None, 0.0, reasons + ["H1 opposes H4"])
        adx_ok = adx1 == adx1 and adx1 >= s.adx_min_trend
        if not adx_ok:
            return TechSignal(None, 0.0, reasons + [f"trend too weak: ADX < {s.adx_min_trend}"])

        # confluence strength from ADX (capped)
        score = max(0.0, min(1.0, adx1 / 40.0))

        # --- M30 trigger (pluggable: macd_cross | donchian | pullback) ---
        side = Action.BUY if h4_dir > 0 else Action.SELL
        fires, label, mult = self._m30_trigger(m30, h4_dir)
        reasons.append(f"{s.trigger_timeframe} trigger[{s.entry_trigger}]={label}")
        if fires:
            return TechSignal(side, score * mult, reasons)
        return TechSignal(None, score, reasons)

    def _m30_trigger(self, m30, h4_dir: int) -> tuple[bool, str, float]:
        """Whether the M30 timing trigger fires for the (already-confirmed) trend direction.
        Returns (fires, label, score_mult). Mode is `entry_trigger`; default macd_cross keeps
        the original behavior exactly. donchian/pullback fire more often (lab-tested first)."""
        s = self.s
        mode = s.entry_trigger
        if mode == "donchian":
            df = indicators._to_df(m30)
            n = s.donchian_lookback
            if len(df) < n + 2:
                return False, "donchian: warmup", 1.0
            close = float(df["close"].iloc[-1])
            prior_high = float(df["high"].iloc[-(n + 1):-1].max())
            prior_low = float(df["low"].iloc[-(n + 1):-1].min())
            if h4_dir > 0 and close > prior_high:
                return True, f"breakout > {n}-bar high", 1.0
            if h4_dir < 0 and close < prior_low:
                return True, f"breakout < {n}-bar low", 1.0
            return False, "no breakout", 1.0
        if mode == "pullback":
            df = indicators._to_df(m30)
            e = s.pullback_ema
            if len(df) < e + 2:
                return False, "pullback: warmup", 1.0
            ema = float(df["close"].ewm(span=e, adjust=False).mean().iloc[-1])
            close = float(df["close"].iloc[-1])
            low = float(df["low"].iloc[-1])
            high = float(df["high"].iloc[-1])
            if h4_dir > 0 and low <= ema and close > ema:
                return True, "pullback to EMA (long)", 1.0
            if h4_dir < 0 and high >= ema and close < ema:
                return True, "pullback to EMA (short)", 1.0
            return False, "no pullback", 1.0
        # default: macd_cross (fresh cross, else momentum continuation)
        cross = indicators.macd_cross(m30, s.macd_fast, s.macd_slow, s.macd_signal)
        _, _, hist = indicators.macd(m30, s.macd_fast, s.macd_slow, s.macd_signal)
        r = indicators.rsi(m30, s.rsi_period)
        if cross == h4_dir:
            return True, "fresh MACD cross trigger", 1.0
        cont = ((h4_dir > 0 and hist > 0 and r == r and r < 70)
                or (h4_dir < 0 and hist < 0 and r == r and r > 30))
        if cont:
            return True, "momentum continuation trigger", 0.8
        return False, "no M30 trigger", 1.0
