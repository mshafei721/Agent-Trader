"""Replay engine (V7 P2.1).

Steps bar-by-bar over the trigger timeframe, runs the EXACT live TechnicalEngine +
RiskManager at each closed bar, and simulates a single-position trade per signal:
enter at the NEXT bar's open (no look-ahead), exit on the first SL/TP touch (pessimistic:
SL wins an intrabar tie), subtract a conservative round-trip cost, and record the R-multiple.

Management modeling (trailing/scale-out/breakeven) is intentionally NOT simulated here — this
measures the raw entry+fixed-stop edge, the honest baseline. Adding favorable management can
only improve the result; modeling it is a later refinement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import Settings
from ..logging_setup import get_logger
from ..risk.manager import RiskManager
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
                 *, label: str = "technical-baseline") -> BacktestResult:
    trigger_tf = _tf(s.trigger_timeframe)
    m30 = bars_by_tf[trigger_tf]
    tf_s = TF_SECONDS[trigger_tf]
    n = len(m30)

    spread_pts = s.backtest_cost_spread_points
    bt = BacktestClient(s, spec, bars_by_tf, spread_points=spread_pts)
    tech = TechnicalEngine(s, bt)
    risk = RiskManager(s, bt)

    cost_price = (s.backtest_cost_spread_points + 2 * s.backtest_cost_slippage_points) * spec.point

    times = m30["time"].to_numpy()
    opens = m30["open"].to_numpy()
    highs = m30["high"].to_numpy()
    lows = m30["low"].to_numpy()
    closes = m30["close"].to_numpy()

    trades: list[Trade] = []
    i = max(s.backtest_warmup_bars, 1)
    while i < n - 1:
        bt.set_cursor(int(times[i]) + tf_s)  # decision at the close of bar i
        sig = tech.evaluate()
        if sig.side is None:
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

        exit_price, reason, exit_idx = _walk_exit(is_buy, sl, tp, entry,
                                                  highs, lows, closes, entry_idx, n)
        r_gross = ((exit_price - entry) if is_buy else (entry - exit_price)) / sl_distance
        r_net = r_gross - cost_price / sl_distance
        trades.append(Trade(
            entry_time=int(times[entry_idx]), exit_time=int(times[exit_idx]),
            side=sig.side.value, entry=entry, sl=sl, tp=tp, exit_price=exit_price,
            r_gross=round(r_gross, 4), r_net=round(r_net, 4), reason=reason,
            bars_held=exit_idx - entry_idx,
        ))
        i = exit_idx + 1  # single position: resume after the trade closes

    result = BacktestResult(label=label, trades=trades)
    result.stats = stats_mod.compute([t.r_net for t in trades], seed=s.backtest_seed)
    log.info("backtest_done", label=label, trades=len(trades),
             expectancy=round(result.stats.expectancy, 3))
    return result


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
