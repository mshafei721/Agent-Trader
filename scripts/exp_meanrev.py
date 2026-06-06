r"""Standalone mean-reversion experiment runner (NOT collected by pytest).

Loads the 5yr M30 cache, runs the trend baseline for reference, then runs each
mean-reversion variant from meanrev.default_variants(). Prints a comparison table
(trades / expectancy / bootstrap 95% CI / profit factor / win rate / maxDD R).
For any variant whose bootstrap CI excludes zero on the positive side, runs a
split-half anchored OOS check (train on first ~60%, score held-out ~40%).

The PRIMARY model for mean-reversion is fixed SL/TP (engine._walk_exit) — the live
management stack (Chandelier trail) is built for TREND-following entries and is INVALID
for counter-trend ones: the swing-extreme trail can sit beyond the current price, which
the engine reads as an instant favorable fill -> a look-ahead phantom profit. The managed
rows are therefore printed only as a clearly-labelled ARTIFACT, not as a result.

Run:  .\.venv\Scripts\python.exe scripts\exp_meanrev.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# allow `python scripts\exp_meanrev.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Silence the engine's per-trade INFO logging: it is the runtime bottleneck (tens of
# thousands of lines) and its RotatingFileHandler races another engine process over
# logs/goldtrader.jsonl on Windows (WinError 32). WARNING level keeps real errors.
logging.disable(logging.INFO)

from goldtrader.config import get_settings
from goldtrader.backtest.data import load_bars, load_spec
from goldtrader.backtest.engine import run_backtest
from goldtrader.backtest import stats as stats_mod
from goldtrader.backtest.meanrev import default_variants, run_meanrev
from goldtrader.strategy.technical import _tf


def _row(label: str, st) -> str:
    lo, hi = st.expectancy_ci
    pf = "inf" if st.profit_factor == float("inf") else f"{st.profit_factor:6.3f}"
    return (f"{label:<26} {st.trades:>6} {st.expectancy:>+9.4f}  "
            f"[{lo:>+7.4f},{hi:>+7.4f}] {pf:>7} {st.win_rate:>7.1%} {st.max_drawdown_r:>8.2f}")


HEADER = (f"{'variant':<26} {'trades':>6} {'exp(R)':>9}  "
          f"{'   bootstrap 95% CI   ':>17} {'PF':>7} {'winrate':>7} {'maxDD(R)':>8}")


def main() -> int:
    s = get_settings()
    bars = load_bars(s)
    spec = load_spec(s)
    m30 = bars[_tf(s.trigger_timeframe)]
    n = len(m30)
    split = int(n * 0.60)

    print(f"\nXAUUSD M30 cache: {n} bars, "
          f"{(m30['time'].iloc[-1] - m30['time'].iloc[0]) / 86400:.0f} days. "
          f"cost = spread {s.backtest_cost_spread_points} + 2x slippage {s.backtest_cost_slippage_points} "
          f"points (point={spec.point}).\n")
    print(HEADER)
    print("-" * len(HEADER))

    # --- trend baseline (managed) for reference ---
    trend = run_backtest(s, bars, spec, model_management=True, label="TREND(managed)*ref")
    print(_row(trend.label, trend.stats))
    trend_fixed = run_backtest(s, bars, spec, model_management=False, label="TREND(fixed-tp)*ref")
    print(_row(trend_fixed.label, trend_fixed.stats))
    print("-" * len(HEADER))

    # --- mean-reversion variants ---
    promising = []
    results = []
    for v in default_variants():
        # fixed-SL/TP is the natural model for a revert-to-mean target; report it as primary.
        res = run_meanrev(s, bars, spec, v, model_management=False)
        results.append((v, res))
        print(_row(res.label, res.stats))
        lo, _hi = res.stats.expectancy_ci
        if res.stats.trades >= 30 and lo > 0:
            promising.append((v, res))

    # The managed-exit model for the two pure triggers — printed ONLY to expose that it
    # is a look-ahead artifact (see below), NOT added to `promising`.
    print("-" * len(HEADER))
    print("# managed-exit rows below are an ARTIFACT (trail sits beyond price on counter-"
          "trend entries) — do NOT read as an edge:")
    for v in default_variants()[:2]:
        res_m = run_meanrev(s, bars, spec, v, model_management=True)
        print(_row(res_m.label + "·mgd[ARTIFACT]", res_m.stats))

    # --- OOS check for anything promising ---
    print()
    if not promising:
        print("No variant's bootstrap 95% CI excluded zero on the positive side "
              "(trades>=30). No OOS check warranted.\n")
    else:
        print(f"=== Split-half OOS (train idx <{split} ~60%, lock, score idx >={split} ~40%) ===")
        print(f"{'variant':<26} {'IS exp':>9} {'IS CI':>20} {'OOS exp':>9} {'OOS CI':>20} {'OOS n':>6}")
        for v, _res in promising:
            is_r = run_meanrev(s, bars, spec, v, end_idx=split).r_multiples
            oos_r = run_meanrev(s, bars, spec, v, start_idx=split).r_multiples
            is_st = stats_mod.compute(is_r, seed=s.backtest_seed)
            oos_st = stats_mod.compute(oos_r, seed=s.backtest_seed)
            print(f"{v.label:<26} {is_st.expectancy:>+9.4f} "
                  f"[{is_st.expectancy_ci[0]:>+7.4f},{is_st.expectancy_ci[1]:>+7.4f}] "
                  f"{oos_st.expectancy:>+9.4f} "
                  f"[{oos_st.expectancy_ci[0]:>+7.4f},{oos_st.expectancy_ci[1]:>+7.4f}] "
                  f"{oos_st.trades:>6}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
