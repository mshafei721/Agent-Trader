"""Broker symbol resolution and spec snapshotting.

MT5 brokers name gold differently (XAUUSD / GOLD / XAUUSDm / ...). We resolve
the first candidate that exists, then MUST symbol_select() it before any tick
or order call.
"""
from __future__ import annotations

from typing import Optional

import MetaTrader5 as mt5  # type: ignore

from ..logging_setup import get_logger
from ..types import SymbolSpec

log = get_logger("goldtrader.symbols")


def resolve_symbol(candidates: list[str]) -> Optional[str]:
    """Return the first candidate the broker exposes, selected in MarketWatch."""
    for name in candidates:
        info = mt5.symbol_info(name)
        if info is not None:
            if not info.visible:
                if not mt5.symbol_select(name, True):
                    log.warning("symbol_select_failed", symbol=name, error=mt5.last_error())
                    continue
            log.info("symbol_resolved", symbol=name)
            return name
    log.error("symbol_unresolved", candidates=candidates)
    return None


def snapshot_spec(name: str) -> SymbolSpec:
    """Capture the current broker specs for `name`. Symbol must be selected."""
    si = mt5.symbol_info(name)
    if si is None:
        raise RuntimeError(f"symbol_info({name}) returned None: {mt5.last_error()}")
    return SymbolSpec(
        name=name,
        digits=si.digits,
        point=si.point,
        volume_min=si.volume_min,
        volume_step=si.volume_step,
        volume_max=si.volume_max,
        contract_size=si.trade_contract_size,
        tick_value=si.trade_tick_value,
        tick_size=si.trade_tick_size,
        stops_level=si.trade_stops_level,
        freeze_level=si.trade_freeze_level,
        filling_mode=si.filling_mode,
    )
