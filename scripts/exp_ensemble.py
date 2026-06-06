"""Validate the LIVE sizing-overlay stack as a gold-allocation overlay: apply
overlays.ensemble_size_scaler (winter tilt x TSMOM regime x vol-target) to a long-gold book
each day and compare drawdown/return vs buy-and-hold over 22yr daily data. NOT pytest-collected.

This is the proof the overlays deliver 'drawdown-controlled gold beta': the bot's intraday entry
signal has no edge, so the honest value is riding gold's uptrend with these damp-only overlays
cutting the drawdown. Causal: the scaler at day t uses closes through t-1 and earns day t's return.

    .\\.venv\\Scripts\\python.exe scripts\\exp_ensemble.py

Writes .omc/research/ensemble-findings.md.
NOTE: Dukascopy daily includes weekends (carried prices), so the 252-bar TSMOM lookback here is
~9 calendar months; the LIVE bot uses broker trading-day D1 bars (252 = ~12 months). Directional
validation only — the buy&hold comparison is apples-to-apples either way.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.backtest import seasonal, stats  # noqa: E402
from goldtrader.config import get_settings  # noqa: E402
from goldtrader.strategy.overlays import ensemble_size_scaler  # noqa: E402
from goldtrader.types import Action  # noqa: E402


def report(name: str, rets: list[float], seed: int) -> str:
    st = stats.compute(rets, seed=seed)
    ann_ret = st.expectancy * 252 * 100
    ann_sharpe = st.sharpe * math.sqrt(252)
    return (f"{name:<22} annRet={ann_ret:+.1f}%  annSharpe={ann_sharpe:+.2f}  "
            f"maxDD={st.max_drawdown_r*100:.0f}%  Calmar={st.calmar:+.2f}  totalR={st.total_r*100:+.0f}%")


def main() -> None:
    s = get_settings()
    seed = s.backtest_seed
    daily = seasonal.load_daily(s)
    closes = daily["close"].to_numpy()
    dates = daily.index
    warm = max(s.tsmom_regime_lookback_days + 5, 260)

    strat, bh, expo = [], [], []
    for t in range(warm, len(closes)):
        if closes[t - 1] <= 0:
            continue
        ret = float(closes[t]) / float(closes[t - 1]) - 1.0
        scaler, _ = ensemble_size_scaler(dates[t].to_pydatetime(), Action.BUY, closes[:t], s)
        strat.append(scaler * ret)
        bh.append(ret)
        expo.append(scaler)

    out = [f"# Overlay ensemble vs buy&hold — long gold, {dates[warm].date()}..{dates[-1].date()}", ""]

    def emit(x: str) -> None:
        print(x); out.append(x)

    emit("## Full sample (overlay stack = winter tilt x TSMOM regime x vol-target, all damp-only)")
    emit("  " + report("buy & hold", bh, seed))
    emit("  " + report("overlay ensemble", strat, seed))
    emit(f"  avg exposure = {sum(expo)/len(expo):.0%} of full (time-in-market reduced by the damps)")
    emit("  (success = materially smaller maxDD and >= buy&hold Sharpe, OOS-robust)")
    emit("")

    half = len(bh) // 2
    emit("## Out-of-sample (chronological halves)")
    emit("  buy&hold   IS : " + report("", bh[:half], seed).strip())
    emit("  buy&hold   OOS: " + report("", bh[half:], seed).strip())
    emit("  ensemble   IS : " + report("", strat[:half], seed).strip())
    emit("  ensemble   OOS: " + report("", strat[half:], seed).strip())
    emit("")
    emit("NOTE: annRet = mean daily x 252; annSharpe = daily Sharpe x sqrt(252); maxDD/totalR in "
         "cumulative daily-return units. The overlays are DRAWDOWN controllers + a winter tilt — "
         "expect lower maxDD and similar/better Sharpe, not higher raw return.")

    rep = ROOT / ".omc" / "research" / "ensemble-findings.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n[written] {rep}")


if __name__ == "__main__":
    main()
