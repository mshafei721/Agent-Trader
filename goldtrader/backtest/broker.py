"""BacktestClient — an MT5Client stand-in that serves cached history up to a time cursor.

Subclasses MT5Client so the REAL TechnicalEngine and RiskManager run against it unchanged
(inheriting compute_lot / _round_price). It only overrides the broker-touching reads
(get_rates / get_tick / equity) to return point-in-time data, so a replay reuses the exact
live decision code with no look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import Settings
from ..mt5.client import MT5Client
from ..types import SymbolSpec
from .data import TF_SECONDS


@dataclass
class _Tick:
    bid: float
    ask: float
    time: float


class BacktestClient(MT5Client):
    def __init__(self, settings: Settings, spec: SymbolSpec, bars_by_tf: dict[int, pd.DataFrame],
                 equity: float = 100_000.0, spread_points: float = 0.0):
        super().__init__(settings)
        self.spec = spec
        self.symbol = spec.name
        self._bars = bars_by_tf
        self._equity = float(equity)
        self._spread_price = spread_points * spec.point
        self._cursor: int = 0  # decision instant (unix seconds): bars CLOSED by this time are visible

    def set_cursor(self, decision_instant: int) -> None:
        self._cursor = int(decision_instant)

    # --- overridden broker reads (point-in-time) ---
    def get_rates(self, timeframe: int, count: int):
        df = self._bars[timeframe]
        tf_s = TF_SECONDS.get(timeframe, 1800)
        # Only bars fully CLOSED by the cursor (open + tf_seconds <= cursor) are visible.
        visible = df[df["time"] + tf_s <= self._cursor]
        return visible.tail(count)

    def get_tick(self):
        # Synthetic tick from the latest visible trigger-timeframe close, with the modeled spread.
        # RiskManager only needs a mid-ish price + a bid/ask split for entry anchoring; the engine
        # re-anchors SL/TP to the actual fill, so the exact tick price is not the edge driver.
        tf = min(self._bars.keys())  # finest timeframe available
        visible = self.get_rates(tf, 1)
        price = float(visible["close"].iloc[-1]) if len(visible) else 0.0
        half = self._spread_price / 2.0
        return _Tick(bid=price - half, ask=price + half, time=float(self._cursor))

    def equity(self) -> float:
        return self._equity

    def balance(self) -> float:
        return self._equity
