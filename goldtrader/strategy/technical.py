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

        # --- M30 trigger ---
        cross = indicators.macd_cross(m30, s.macd_fast, s.macd_slow, s.macd_signal)
        _, _, hist = indicators.macd(m30, s.macd_fast, s.macd_slow, s.macd_signal)
        r = indicators.rsi(m30, s.rsi_period)
        reasons.append(f"{s.trigger_timeframe} macd_cross={cross} hist={hist:.3f} rsi={r:.1f}")

        side = Action.BUY if h4_dir > 0 else Action.SELL

        if cross == h4_dir:
            return TechSignal(side, score, reasons + ["fresh MACD cross trigger"])

        # continuation: momentum already in direction and RSI not over-extended
        cont = (
            (h4_dir > 0 and hist > 0 and r == r and r < 70)
            or (h4_dir < 0 and hist < 0 and r == r and r > 30)
        )
        if cont:
            return TechSignal(side, score * 0.8, reasons + ["momentum continuation trigger"])

        return TechSignal(None, score, reasons + ["no M30 trigger"])
