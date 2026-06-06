"""Walk-forward analysis (V7 P2.2).

The honest test of whether a parameter choice is real edge or in-sample luck: optimize on a
training window, then score ONLY on the immediately-following out-of-sample window, and never
grade on data you tuned on. Concatenating every fold's OOS trades gives the walk-forward
equity — the number that actually predicts live behaviour.

`run_grid` is engine-bound (one full backtest per config). `walk_forward` is PURE (given the
per-config trade lists) so the fold selection logic unit-tests without the engine. Parameter
ranges stay inside the reflection TUNABLE_BOUNDS spirit (small grid -> less overfitting).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import Settings
from ..logging_setup import get_logger
from ..types import SymbolSpec
from . import stats as stats_mod
from .engine import Trade, run_backtest

log = get_logger("goldtrader.backtest.wf")

# Small grid in the promising region found by the sweeps (wider TP, stricter trend, COT on/off).
DEFAULT_GRID = [
    {"tp": tp, "adx": adx, "cot": cot}
    for tp in (2.5, 3.0, 3.5)
    for adx in (18.0, 24.0)
    for cot in (None, 1.0)
]


def cfg_key(cfg: dict) -> str:
    return f"tp{cfg['tp']}_adx{cfg['adx']:.0f}_cot{cfg['cot'] if cfg['cot'] else 'off'}"


@dataclass
class WFResult:
    oos_trades: list[Trade] = field(default_factory=list)
    selections: list[dict] = field(default_factory=list)  # per fold: window + chosen config + exps
    stats: object = None


def run_grid(s: Settings, bars_by_tf: dict[int, pd.DataFrame], spec: SymbolSpec,
             cot_zseries, grid: list[dict]) -> dict[str, list[Trade]]:
    """Run one full managed backtest per grid config; return {config_key: trades}."""
    out: dict[str, list[Trade]] = {}
    for cfg in grid:
        ss = Settings(atr_tp_mult=cfg["tp"], adx_min_trend=cfg["adx"],
                      cot_extreme_z=(cfg["cot"] if cfg["cot"] else 1.5))
        zs = cot_zseries if cfg["cot"] else None
        res = run_backtest(ss, bars_by_tf, spec, model_management=True, cot_zseries=zs,
                           label=cfg_key(cfg))
        out[cfg_key(cfg)] = res.trades
        log.info("wf_grid_config", config=cfg_key(cfg), trades=len(res.trades))
    return out


def _exp(rs: list[float]) -> float:
    return sum(rs) / len(rs) if rs else 0.0


def walk_forward(grid_trades: dict[str, list[Trade]], *, n_folds: int = 3,
                 train_frac: float = 0.5, min_train_trades: int = 15,
                 seed: int = 42) -> WFResult:
    """Anchored (expanding-train) walk-forward. PURE given grid_trades.

    The last (1 - train_frac) of the time span is split into n_folds OOS windows. For each,
    train = [start, window_start); pick the config with the best training expectancy
    (>= min_train_trades), then collect THAT config's trades inside the OOS window.
    """
    all_times = sorted(t.entry_time for trades in grid_trades.values() for t in trades)
    if not all_times:
        return WFResult(stats=stats_mod.compute([], seed=seed))
    t0, t1 = all_times[0], all_times[-1]
    span = max(1, t1 - t0)
    test_total = 1.0 - train_frac

    oos: list[Trade] = []
    selections: list[dict] = []
    for k in range(n_folds):
        test_a = t0 + span * (train_frac + test_total * k / n_folds)
        test_b = t0 + span * (train_frac + test_total * (k + 1) / n_folds)
        best_key, best_exp = None, None
        for key, trades in grid_trades.items():
            train_rs = [t.r_net for t in trades if t0 <= t.entry_time < test_a]
            if len(train_rs) < min_train_trades:
                continue
            e = _exp(train_rs)
            if best_exp is None or e > best_exp:
                best_exp, best_key = e, key
        if best_key is None:  # too little training data -> skip this fold
            selections.append({"fold": k, "chosen": None, "reason": "insufficient train data"})
            continue
        fold_oos = [t for t in grid_trades[best_key] if test_a <= t.entry_time < test_b]
        oos.extend(fold_oos)
        selections.append({
            "fold": k, "chosen": best_key, "train_exp": round(best_exp, 3),
            "oos_trades": len(fold_oos), "oos_exp": round(_exp([t.r_net for t in fold_oos]), 3),
        })
    return WFResult(oos_trades=oos, selections=selections,
                    stats=stats_mod.compute([t.r_net for t in oos], seed=seed))


def best_in_sample(grid_trades: dict[str, list[Trade]], seed: int = 42) -> tuple[str, object]:
    """The single config with the best full-history expectancy (for the degradation comparison)."""
    best_key, best = None, None
    for key, trades in grid_trades.items():
        st = stats_mod.compute([t.r_net for t in trades], seed=seed)
        if best is None or st.expectancy > best.expectancy:
            best_key, best = key, st
    return best_key, best


def format_report(wf: WFResult, grid_trades: dict[str, list[Trade]], seed: int) -> str:
    lines = ["=== Walk-forward (out-of-sample) ==="]
    for sel in wf.selections:
        if sel.get("chosen") is None:
            lines.append(f"  fold {sel['fold']}: {sel.get('reason', 'skipped')}")
        else:
            lines.append(f"  fold {sel['fold']}: chose {sel['chosen']} "
                         f"(train exp {sel['train_exp']:+.3f}) -> OOS {sel['oos_trades']} trades, "
                         f"exp {sel['oos_exp']:+.3f}R")
    st = wf.stats
    lines.append(f"  WF-OOS: {st.trades} trades  win {st.win_rate:.1%}  "
                 f"exp {st.expectancy:+.3f}R (95% CI {st.expectancy_ci[0]:+.3f}..{st.expectancy_ci[1]:+.3f})  "
                 f"PF {st.profit_factor:.2f}  total {st.total_r:+.1f}R  maxDD {st.max_drawdown_r:.1f}R  "
                 f"Sharpe {st.sharpe:+.3f}  Calmar {st.calmar:+.2f}")
    bk, bst = best_in_sample(grid_trades, seed)
    lines.append(f"  (best SINGLE config in-sample: {bk} exp {bst.expectancy:+.3f}R, "
                 f"PF {bst.profit_factor:.2f} — degradation to WF-OOS shows overfit risk)")
    verdict = ("WF-OOS edge POSITIVE (CI excludes 0)" if st.expectancy_ci[0] > 0
               else "WF-OOS edge NOT proven (CI includes 0)")
    lines.append(f"  VERDICT: {verdict}")
    return "\n".join(lines)
