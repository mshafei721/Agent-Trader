"""Risk manager: turns a directional OrderIntent into a sized, gated RiskDecision.

Trend/timeframe confluence is owned by the TechnicalEngine; bias conviction is
checked in the supervisor loop. The RiskManager focuses on:
  1. volatility ceiling (skip when ATR% is too wild)
  2. stop distance (ATR or fixed) >= broker stops_level
  3. position sizing from risk % (rejects if < 1 min lot)
Conflicting-signal and max-position logic is handled in the supervisor loop.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import MetaTrader5 as mt5  # type: ignore

from ..config import Settings
from ..logging_setup import get_logger
from ..types import Action, OrderIntent, RiskDecision
from . import indicators

if TYPE_CHECKING:
    from ..mt5.client import MT5Client

log = get_logger("goldtrader.risk")

# Local timeframe map (mirrors strategy.technical._TF_MAP) so the stop timeframe
# is configurable without coupling the two modules.
_TF_MAP = {
    "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
}


def _tf(name: str) -> int:
    return _TF_MAP.get(name.upper(), mt5.TIMEFRAME_H1)


def open_risk_money(positions, tick_value: float, tick_size: float) -> float:
    """Sum money-at-risk across open positions = adverse distance to each SL.

    A position whose stop is at/through breakeven (no adverse distance) contributes
    0 — so winners that moved to breakeven free up room under the total-risk cap.
    Pure function (positions just need .type/.price_open/.sl/.volume).
    """
    if tick_size <= 0:
        return 0.0
    total = 0.0
    for p in positions:
        if not getattr(p, "sl", 0):
            continue
        is_buy = p.type == 0
        dist = (p.price_open - p.sl) if is_buy else (p.sl - p.price_open)
        if dist <= 0:
            continue  # SL locks profit -> no risk
        total += (dist / tick_size) * tick_value * p.volume
    return total


def half_lot(orig_lots: float, step: float, vmin: float) -> float | None:
    """Half of the original lots, floored to `step`. None if either half < vmin
    (a position too small to split for scale-out)."""
    if step <= 0:
        return None
    half = round(math.floor((orig_lots / 2) / step) * step, 8)
    remaining = round(orig_lots - half, 8)
    if half < vmin or remaining < vmin:
        return None
    return half


def can_pyramid(same_dir, max_positions: int, winners_only: bool) -> tuple[bool, str]:
    """Whether a new same-direction position may be added."""
    if len(same_dir) >= max_positions:
        return False, "max_positions_reached"
    if winners_only and any(getattr(p, "profit", 0) <= 0 for p in same_dir):
        return False, "pyramid_blocked_not_all_winners"
    return True, "ok"


class RiskManager:
    def __init__(self, settings: Settings, client: "MT5Client"):
        self.s = settings
        self.client = client

    def _stop_distance(self) -> float:
        """Stop distance in price units, from ATR or fixed points."""
        spec = self.client.spec
        assert spec is not None
        if self.s.sl_mode == "fixed":
            return self.s.fixed_sl_points * spec.point
        rates = self.client.get_rates(_tf(self.s.stop_timeframe), self.s.atr_period * 4)
        a = indicators.atr(rates, self.s.atr_period)
        if a != a or a <= 0:  # NaN check
            log.warning("atr_unavailable_fallback_fixed")
            return self.s.fixed_sl_points * spec.point
        return a * self.s.atr_sl_mult

    def _tp_distance(self, sl_distance: float) -> float:
        if self.s.sl_mode == "fixed":
            return self.s.fixed_tp_points * self.client.spec.point
        # ATR mode: keep the SL:TP ratio implied by the mults.
        return sl_distance * (self.s.atr_tp_mult / self.s.atr_sl_mult)

    def _volatility_ok(self) -> tuple[bool, str]:
        """Skip when volatility is too high to trade safely (ATR% ceiling)."""
        if not self.s.regime_filter_enabled:
            return True, "volatility gate disabled"
        h1 = self.client.get_rates(mt5.TIMEFRAME_H1, max(self.s.atr_period * 4, 120))
        atr_val = indicators.atr(h1, self.s.atr_period)
        tick = self.client.get_tick()
        price = (tick.bid + tick.ask) / 2.0
        if atr_val == atr_val and price > 0:
            atr_pct = atr_val / price * 100.0
            if atr_pct > self.s.atr_max_pct:
                return False, f"too volatile: ATR {atr_pct:.2f}% > {self.s.atr_max_pct}%"
        return True, "volatility ok"

    def evaluate(self, intent: OrderIntent, risk_scaler: float = 1.0) -> RiskDecision:
        spec = self.client.spec
        assert spec is not None

        ok, reason = self._volatility_ok()
        if not ok:
            return RiskDecision(False, reason)

        sl_distance = self._stop_distance()
        min_stop = spec.stops_level * spec.point
        if sl_distance < min_stop:
            sl_distance = min_stop * 1.5  # widen to a safe margin above broker minimum
        tp_distance = self._tp_distance(sl_distance)

        equity = self.client.equity()
        # Learning feedback shrinks size after losing streaks (scaler in [0.25, 1]).
        effective_risk_pct = self.s.risk_pct_per_trade * max(0.1, min(1.0, risk_scaler))
        risk_amount = equity * effective_risk_pct / 100.0
        lots = self.client.compute_lot(sl_distance, risk_amount)
        if lots <= 0:
            return RiskDecision(False, "risk budget too small for one minimum lot")

        tick = self.client.get_tick()
        if intent.side == Action.BUY:
            entry = tick.ask
            sl = entry - sl_distance
            tp = entry + tp_distance
        else:
            entry = tick.bid
            sl = entry + sl_distance
            tp = entry - tp_distance

        log.info(
            "risk_approved",
            side=intent.side.value,
            lots=lots,
            sl=round(sl, spec.digits),
            tp=round(tp, spec.digits),
            sl_distance=round(sl_distance, spec.digits),
            risk_amount=round(risk_amount, 2),
        )
        return RiskDecision(
            approved=True,
            reason="ok",
            lots=lots,
            sl=sl,
            tp=tp,
            entry_hint=entry,
            risk_amount=risk_amount,
        )
