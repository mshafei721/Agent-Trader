"""Replay engine (V7 P2.1 + P2.1b).

Steps bar-by-bar over the trigger timeframe, runs the EXACT live TechnicalEngine +
RiskManager at each closed bar, and simulates a single-position trade per signal:
enter at the NEXT bar's open (no look-ahead) and record the R-multiple.

Two exit models:
  - model_management=False: fixed SL/TP (first touch; SL wins an intrabar tie) — the raw
    entry+fixed-stop baseline.
  - model_management=True (default): faithfully simulates what the bot actually does —
    early loss-cut, scale-out half at +partial_tp_r, breakeven, and the Chandelier trail —
    reusing the exact exits.py helpers. Indicator series (ATR, swings, fast trend) are
    precomputed causally (rolling/ewm use only past+current bars), so there is no look-ahead.

A conservative round-trip cost (spread + slippage) is subtracted in R. Single position at a
time. Bias-aware exit is NOT modeled (no historical bias series); early-cut uses the trigger
timeframe as a proxy for the M15 cut signal.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..config import Settings
from ..feeds.cot import cot_gate
from ..logging_setup import get_logger
from ..risk.manager import RiskManager
from ..strategy.exits import chandelier_stop, ratchet_stop, should_cut_loss
from ..strategy.technical import _tf, TechnicalEngine
from ..types import Action, OrderIntent, SymbolSpec
from . import stats as stats_mod
from .broker import BacktestClient
from .data import TF_SECONDS

log = get_logger("goldtrader.backtest.engine")


@dataclass
class Trade:
    entry_time: int
    exit_time: int
    side: str
    entry: float
    sl: float
    tp: float
    exit_price: float
    r_gross: float
    r_net: float
    reason: str
    bars_held: int


@dataclass
class BacktestResult:
    label: str
    trades: list[Trade] = field(default_factory=list)
    stats: object = None  # stats_mod.PerfStats

    @property
    def r_multiples(self) -> list[float]:
        return [t.r_net for t in self.trades]


def run_backtest(s: Settings, bars_by_tf: dict[int, pd.DataFrame], spec: SymbolSpec,
                 *, label: str | None = None, model_management: bool = True,
                 apply_session: bool = False, cot_zseries=None) -> BacktestResult:
    """Replay over cached history.

    apply_session: also apply the London-NY session-time entry gate (uses bar UTC time).
    cot_zseries: optional sorted list of (epoch, zscore) for the historical CFTC COT gate;
                 when given, blocks entries that chase a crowded positioning extreme as-of
                 the decision date (FAILS OPEN before the first report).
    """
    trigger_tf = _tf(s.trigger_timeframe)
    m30 = bars_by_tf[trigger_tf]
    tf_s = TF_SECONDS[trigger_tf]
    n = len(m30)
    if label is None:
        gates = "".join(["+sess" if apply_session else "", "+cot" if cot_zseries else ""])
        label = ("managed" if model_management else "fixed-tp") + gates

    bt = BacktestClient(s, spec, bars_by_tf, spread_points=s.backtest_cost_spread_points)
    tech = TechnicalEngine(s, bt)
    risk = RiskManager(s, bt)

    cost_r_unit = (s.backtest_cost_spread_points + 2 * s.backtest_cost_slippage_points) * spec.point

    times = m30["time"].to_numpy()
    opens = m30["open"].to_numpy()
    highs = m30["high"].to_numpy()
    lows = m30["low"].to_numpy()
    closes = m30["close"].to_numpy()
    mgmt = _precompute_management_series(m30, s) if model_management else None

    trades: list[Trade] = []
    i = max(s.backtest_warmup_bars, 1)
    while i < n - 1:
        decision_instant = int(times[i]) + tf_s
        bt.set_cursor(decision_instant)  # decision at the close of bar i
        sig = tech.evaluate()
        if sig.side is None:
            i += 1
            continue
        # --- optional deterministic gold gates (A/B against the bare technical baseline) ---
        if apply_session and not _in_session_utc(decision_instant, s):
            i += 1
            continue
        if cot_zseries is not None:
            z = _cot_z_asof(cot_zseries, decision_instant)
            if z is not None and not cot_gate(sig.side, z, s.cot_extreme_z)[0]:
                i += 1
                continue
        decision = risk.evaluate(OrderIntent(side=sig.side, confidence=sig.score,
                                             rationale="backtest", signal_hash="bt"))
        if not decision.approved:
            i += 1
            continue
        sl_distance = abs(decision.entry_hint - decision.sl)
        tp_distance = abs(decision.tp - decision.entry_hint)
        if sl_distance <= 0:
            i += 1
            continue

        is_buy = sig.side == Action.BUY
        entry_idx = i + 1
        entry = float(opens[entry_idx])
        sl = entry - sl_distance if is_buy else entry + sl_distance
        tp = entry + tp_distance if is_buy else entry - tp_distance

        if model_management:
            r_gross, reason, exit_idx, exit_price, scaled = _simulate_managed_exit(
                s, is_buy, entry, sl, tp, sl_distance, entry_idx, n,
                highs, lows, closes, mgmt)
            cost_r = cost_r_unit / sl_distance * (1.5 if scaled else 1.0)  # extra exit if scaled
        else:
            exit_price, reason, exit_idx = _walk_exit(is_buy, sl, tp, entry,
                                                      highs, lows, closes, entry_idx, n)
            r_gross = ((exit_price - entry) if is_buy else (entry - exit_price)) / sl_distance
            cost_r = cost_r_unit / sl_distance

        r_net = r_gross - cost_r
        trades.append(Trade(
            entry_time=int(times[entry_idx]), exit_time=int(times[exit_idx]),
            side=sig.side.value, entry=entry, sl=round(sl, spec.digits), tp=round(tp, spec.digits),
            exit_price=round(exit_price, spec.digits),
            r_gross=round(r_gross, 4), r_net=round(r_net, 4), reason=reason,
            bars_held=exit_idx - entry_idx,
        ))
        i = exit_idx + 1  # single position: resume after the trade closes

    result = BacktestResult(label=label, trades=trades)
    result.stats = stats_mod.compute([t.r_net for t in trades], seed=s.backtest_seed)
    log.info("backtest_done", label=label, trades=len(trades),
             expectancy=round(result.stats.expectancy, 3))
    return result


def _precompute_management_series(m30: pd.DataFrame, s: Settings) -> dict:
    """Causal (no look-ahead) per-bar series for the management sim: Chandelier ATR + swings
    and the fast-timeframe trend proxy. Mirrors indicators.py (Wilder ATR, EMA stack)."""
    high, low, close = m30["high"], m30["low"], m30["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / s.atr_period, adjust=False).mean()
    swing_high = high.rolling(s.chandelier_lookback).max()
    swing_low = low.rolling(s.chandelier_lookback).min()
    ema_f = close.ewm(span=s.ema_fast, adjust=False).mean()
    ema_s = close.ewm(span=s.ema_slow, adjust=False).mean()
    trend = np.where((ema_f > ema_s) & (close >= ema_s), 1,
                     np.where((ema_f < ema_s) & (close <= ema_s), -1, 0))
    return {
        "atr": atr.to_numpy(), "swing_high": swing_high.to_numpy(),
        "swing_low": swing_low.to_numpy(), "fast_trend": trend,
    }


def _simulate_managed_exit(s: Settings, is_buy: bool, entry: float, sl: float, tp: float,
                           sl_distance: float, entry_idx: int, n: int,
                           highs, lows, closes, mgmt: dict):
    """Walk the trade bar-by-bar applying the live management stack (early-cut, scale-out,
    breakeven, Chandelier trail) via the exact exits.py helpers. Returns
    (r_gross, reason, exit_idx, exit_price, scaled). R is in full-initial-risk units."""
    sign = 1.0 if is_buy else -1.0
    realized = 0.0          # R already banked (scale-out)
    fraction = 1.0          # remaining open fraction
    scaled = False
    eps = sl_distance * 1e-6

    def move_r(price: float) -> float:
        return sign * (price - entry) / sl_distance

    for j in range(entry_idx, n):
        hi, lo, close = float(highs[j]), float(lows[j]), float(closes[j])
        # 1) intrabar stop / take-profit on the CURRENT sl/tp (pessimistic: SL wins a tie)
        hit_sl = lo <= sl if is_buy else hi >= sl
        hit_tp = hi >= tp if is_buy else lo <= tp
        if hit_sl and hit_tp:
            return realized + fraction * move_r(sl), "sl(both)", j, sl, scaled
        if hit_sl:
            reason = "trail" if (move_r(sl) > 0) else "sl"
            return realized + fraction * move_r(sl), reason, j, sl, scaled
        if hit_tp:
            return realized + fraction * move_r(tp), "tp", j, tp, scaled
        # 2) management at the bar close
        r_now = move_r(close)
        if s.cut_loss_enabled and should_cut_loss(r_now, int(mgmt["fast_trend"][j]), is_buy, s.cut_loss_at_r):
            return realized + fraction * r_now, "cut", j, close, scaled
        if not scaled and s.partial_tp_r > 0 and r_now >= s.partial_tp_r:
            realized += 0.5 * fraction * r_now   # bank half at the current price
            fraction *= 0.5
            scaled = True
        if r_now >= s.breakeven_at_r:
            atr_j = mgmt["atr"][j]
            if atr_j == atr_j and atr_j > 0:  # not NaN
                chand = chandelier_stop(is_buy, float(mgmt["swing_high"][j]),
                                        float(mgmt["swing_low"][j]), float(atr_j), s.trail_atr_mult)
                candidate = max(entry, chand) if is_buy else min(entry, chand)
                new_sl, improved = ratchet_stop(is_buy, candidate, sl, eps)
                if s.use_trailing and improved:
                    sl = new_sl
    # end of data -> mark remaining to market
    return realized + fraction * move_r(float(closes[n - 1])), "eod", n - 1, float(closes[n - 1]), scaled


def _in_session_utc(epoch: int, s: Settings) -> bool:
    hour = datetime.fromtimestamp(epoch, tz=timezone.utc).hour
    start, end = s.trading_session_start_utc, s.trading_session_end_utc
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _cot_z_asof(zseries, epoch: int):
    """Most-recent COT z-score with report epoch <= `epoch` (None before the first report).
    `zseries` is a list of (epoch, z) sorted ascending by epoch."""
    epochs = [e for e, _ in zseries]
    idx = bisect.bisect_right(epochs, epoch) - 1
    if idx < 0:
        return None
    return zseries[idx][1]


def _walk_exit(is_buy: bool, sl: float, tp: float, entry: float,
               highs, lows, closes, entry_idx: int, n: int) -> tuple[float, str, int]:
    """First SL/TP touch from entry_idx onward; SL wins an intrabar tie (pessimistic).
    Falls back to mark-to-market at the last bar if neither is ever touched."""
    for j in range(entry_idx, n):
        hi, lo = float(highs[j]), float(lows[j])
        hit_sl = lo <= sl if is_buy else hi >= sl
        hit_tp = hi >= tp if is_buy else lo <= tp
        if hit_sl and hit_tp:
            return sl, "sl(both)", j
        if hit_sl:
            return sl, "sl", j
        if hit_tp:
            return tp, "tp", j
    return float(closes[n - 1]), "eod", n - 1
