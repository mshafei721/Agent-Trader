"""Defensive MetaTrader5 wrapper.

Every MT5 call checks its return value and logs ``mt5.last_error()`` on failure.
MT5's Python binding is NOT thread-safe, so all calls go through a single
client instance guarded by a lock.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5  # type: ignore

from ..config import Settings
from ..healing.retry import with_backoff
from ..logging_setup import get_logger
from ..safety.guards import assert_demo
from ..types import Action, OrderResult, SymbolSpec
from .symbols import resolve_symbol, snapshot_spec

log = get_logger("goldtrader.mt5")

RETCODE_DONE = 10009  # TRADE_RETCODE_DONE


class MT5ConnectionError(RuntimeError):
    pass


class MT5Client:
    def __init__(self, settings: Settings):
        self.s = settings
        self._lock = threading.RLock()
        self.symbol: Optional[str] = None
        self.spec: Optional[SymbolSpec] = None
        self._connected = False
        self.trade_mode: Optional[int] = None  # MT5 account trade_mode: 0=demo,1=contest,2=real

    # ---------------- connection ----------------
    @with_backoff(exceptions=(MT5ConnectionError,), max_tries=4, base=2.0, cap=20.0)
    def connect(self) -> None:
        with self._lock:
            kwargs = {}
            if self.s.mt5_terminal_path:
                kwargs["path"] = self.s.mt5_terminal_path
            if self.s.mt5_login:
                kwargs["login"] = int(self.s.mt5_login)
                if self.s.mt5_password:
                    kwargs["password"] = self.s.mt5_password.get_secret_value()
                if self.s.mt5_server:
                    kwargs["server"] = self.s.mt5_server

            if not mt5.initialize(**kwargs):
                err = mt5.last_error()
                raise MT5ConnectionError(f"mt5.initialize failed: {err}")

            ai = mt5.account_info()
            if ai is None:
                raise MT5ConnectionError(f"account_info() is None after init: {mt5.last_error()}")

            # HARD demo guard.
            assert_demo(ai.trade_mode, self.s)
            self.trade_mode = int(ai.trade_mode)

            if not ai.trade_allowed:
                log.error(
                    "algo_trading_disabled",
                    hint="Enable the 'Algo Trading' button in the MT5 toolbar; "
                    "orders will be rejected otherwise.",
                )

            self.symbol = resolve_symbol(self.s.symbol_candidates)
            if self.symbol is None:
                raise MT5ConnectionError("could not resolve a gold symbol from candidates")
            self.spec = snapshot_spec(self.symbol)
            self._connected = True
            log.info(
                "mt5_connected",
                login=ai.login,
                server=ai.server,
                trade_mode=ai.trade_mode,
                equity=ai.equity,
                symbol=self.symbol,
            )

    def ensure_connected(self) -> None:
        """Reconnect if the link dropped (account_info() returns None)."""
        with self._lock:
            if not self._connected or mt5.account_info() is None:
                log.warning("mt5_reconnecting")
                try:
                    mt5.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                self._connected = False
                self.connect()

    def shutdown(self) -> None:
        with self._lock:
            try:
                mt5.shutdown()
            finally:
                self._connected = False

    # ---------------- account / market ----------------
    def equity(self) -> float:
        with self._lock:
            ai = mt5.account_info()
            if ai is None:
                raise MT5ConnectionError(f"account_info None: {mt5.last_error()}")
            return float(ai.equity)

    def balance(self) -> float:
        with self._lock:
            ai = mt5.account_info()
            return float(ai.balance) if ai else 0.0

    def get_tick(self):
        with self._lock:
            t = mt5.symbol_info_tick(self.symbol)
            if t is None:
                raise MT5ConnectionError(f"tick None for {self.symbol}: {mt5.last_error()}")
            return t

    def tick_age_seconds(self) -> float:
        t = self.get_tick()
        return (datetime.now(timezone.utc).timestamp()) - float(t.time)

    def current_spread_points(self) -> float:
        """Live bid/ask spread in broker points (ask-bid)/point. Used by the entry spread guard."""
        spec = self.spec
        assert spec is not None
        if spec.point <= 0:
            return 0.0
        t = self.get_tick()
        return (float(t.ask) - float(t.bid)) / spec.point

    def get_rates(self, timeframe: int, count: int):
        """Return recent OHLC bars (numpy structured array) for the symbol."""
        with self._lock:
            rates = mt5.copy_rates_from_pos(self.symbol, timeframe, 0, count)
            if rates is None:
                raise MT5ConnectionError(f"copy_rates None: {mt5.last_error()}")
            return rates

    # ---------------- sizing ----------------
    def compute_lot(self, stop_distance_price: float, risk_amount: float) -> float:
        """Convert a money risk + stop distance (in price) into a lot size.

        loss_per_lot = (stop_distance / tick_size) * tick_value
        Rounds DOWN to volume_step and clamps to [volume_min, volume_max].
        Returns 0.0 when the risk budget cannot cover one minimum lot.
        """
        spec = self.spec
        assert spec is not None
        if stop_distance_price <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0:
            return 0.0
        loss_per_lot = (stop_distance_price / spec.tick_size) * spec.tick_value
        if loss_per_lot <= 0:
            return 0.0
        raw_lots = risk_amount / loss_per_lot
        # round DOWN to step
        steps = math.floor(raw_lots / spec.volume_step)
        lots = round(steps * spec.volume_step, 8)
        if lots < spec.volume_min:
            return 0.0
        return min(lots, spec.volume_max)

    # ---------------- filling mode ----------------
    def _pick_filling(self) -> int:
        """Choose a supported filling mode from the symbol bitmask.

        filling_mode bits: 1 = FOK, 2 = IOC (per MT5 SYMBOL_FILLING_*).
        Prefer IOC, then FOK, else RETURN.
        """
        spec = self.spec
        assert spec is not None
        mask = spec.filling_mode
        if mask & 2:
            return mt5.ORDER_FILLING_IOC
        if mask & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _round_price(self, price: float) -> float:
        spec = self.spec
        assert spec is not None
        return round(price, spec.digits)

    def build_request(self, side: Action, lots: float, sl: float, tp: float) -> dict:
        spec = self.spec
        assert spec is not None and side in (Action.BUY, Action.SELL)
        tick = self.get_tick()
        if side == Action.BUY:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        return {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(lots),
            "type": order_type,
            "price": self._round_price(price),
            "sl": self._round_price(sl) if sl else 0.0,
            "tp": self._round_price(tp) if tp else 0.0,
            "deviation": self.s.deviation_points,
            "magic": self.s.magic,
            "comment": "goldtrader",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pick_filling(),
        }

    def order_check(self, request: dict):
        with self._lock:
            return mt5.order_check(request)

    def place_market_order(self, side: Action, lots: float, sl: float, tp: float) -> OrderResult:
        with self._lock:
            request = self.build_request(side, lots, sl, tp)
            # Pre-validate (margin, stops, filling) before sending.
            check = mt5.order_check(request)
            if check is None or check.retcode not in (0, RETCODE_DONE):
                log.error(
                    "order_check_failed",
                    retcode=getattr(check, "retcode", None),
                    comment=getattr(check, "comment", None),
                    request=request,
                )
                return OrderResult(
                    ok=False,
                    retcode=getattr(check, "retcode", -1),
                    ticket=None,
                    price=request["price"],
                    comment=f"order_check: {getattr(check, 'comment', 'None')}",
                    request_dump=request,
                )
            result = mt5.order_send(request)
            if result is None:
                return OrderResult(False, -1, None, request["price"], f"order_send None: {mt5.last_error()}", request)
            ok = result.retcode == RETCODE_DONE
            (log.info if ok else log.error)(
                "order_send",
                ok=ok,
                retcode=result.retcode,
                deal=getattr(result, "deal", None),
                order=getattr(result, "order", None),
                price=getattr(result, "price", None),
                comment=result.comment,
            )
            return OrderResult(
                ok=ok,
                retcode=result.retcode,
                ticket=getattr(result, "order", None) or getattr(result, "deal", None),
                price=getattr(result, "price", request["price"]),
                comment=result.comment,
                request_dump=request,
            )

    # ---------------- positions ----------------
    def get_open_positions(self):
        """Our positions only (filtered by magic)."""
        with self._lock:
            positions = mt5.positions_get(symbol=self.symbol)
            if positions is None:
                return []
            return [p for p in positions if p.magic == self.s.magic]

    def modify_position(self, ticket: int, sl: float, tp: float) -> OrderResult:
        """Adjust SL/TP of an open position (breakeven / trailing)."""
        with self._lock:
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": self.symbol,
                "position": int(ticket),
                "sl": self._round_price(sl) if sl else 0.0,
                "tp": self._round_price(tp) if tp else 0.0,
                "magic": self.s.magic,
            }
            result = mt5.order_send(request)
            ok = result is not None and result.retcode == RETCODE_DONE
            (log.info if ok else log.warning)(
                "modify_position", ok=ok, ticket=ticket,
                sl=request["sl"], tp=request["tp"],
                retcode=getattr(result, "retcode", None),
            )
            return OrderResult(
                ok=ok,
                retcode=getattr(result, "retcode", -1),
                ticket=ticket,
                price=request["sl"],
                comment=getattr(result, "comment", "None"),
                request_dump=request,
            )

    def close_position(self, position, volume: float | None = None) -> OrderResult:
        """Close a position fully, or partially if `volume` is given (scale-out)."""
        with self._lock:
            spec = self.spec
            vol = position.volume if volume is None else min(volume, position.volume)
            if spec is not None and spec.volume_step > 0:
                steps = round(vol / spec.volume_step)
                vol = round(steps * spec.volume_step, 8)
            vol = min(vol, position.volume)
            tick = self.get_tick()
            if position.type == mt5.POSITION_TYPE_BUY:
                close_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            else:
                close_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": vol,
                "type": close_type,
                "position": position.ticket,
                "price": self._round_price(price),
                "deviation": self.s.deviation_points,
                "magic": self.s.magic,
                "comment": "goldtrader-close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._pick_filling(),
            }
            result = mt5.order_send(request)
            ok = result is not None and result.retcode == RETCODE_DONE
            log.info("close_position", ok=ok, ticket=position.ticket,
                     retcode=getattr(result, "retcode", None))
            return OrderResult(
                ok=ok,
                retcode=getattr(result, "retcode", -1),
                ticket=position.ticket,
                price=request["price"],
                comment=getattr(result, "comment", "None"),
                request_dump=request,
            )

    def get_deals_since(self, since: datetime):
        """Realized deals (for PnL / learning) since a datetime."""
        with self._lock:
            now = datetime.now(timezone.utc)
            deals = mt5.history_deals_get(since, now)
            if deals is None:
                return []
            return [d for d in deals if d.magic == self.s.magic]
