"""Standalone runner: gold time-series momentum (TSMOM) vs buy-and-hold over 22yr daily data,
risk-adjusted, with an OOS split. The decisive question: does TSMOM beat buy&hold on Sharpe /
drawdown (getting out of bear phases), or is it just riding the secular bull? NOT pytest-collected.

    .\\.venv\\Scripts\\python.exe scripts\\exp_tsmom.py

Writes .omc/research/tsmom-findings.md.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.backtest import seasonal, stats, tsmom  # noqa: E402
from goldtrader.config import get_settings  # noqa: E402


def line(name: str, trades, seed: int) -> str:
    rs = [t.ret for t in trades]
    if len(rs) < 5:
        return f"{name:<26} n={len(rs)} (too few)"
    st = stats.compute(rs, seed=seed)
    lo, hi = st.expectancy_ci
    excl = "YES" if (lo > 0 or hi < 0) else "no"
    ann_sharpe = st.sharpe * math.sqrt(12)   # monthly -> annualized
    return (f"{name:<26} n={st.trades:<4} mean={st.expectancy*100:+.2f}%/mo  "
            f"CI[{lo*100:+.2f},{hi*100:+.2f}] excl0={excl:<3} "
            f"annSharpe={ann_sharpe:+.2f}  maxDD={st.max_drawdown_r*100:.0f}%  Calmar={st.calmar:+.2f}")


def main() -> None:
    s = get_settings()
    seed = s.backtest_seed
    daily = seasonal.load_daily(s)
    out = [f"# Gold TSMOM vs buy&hold — monthly, {daily.index[0].date()}..{daily.index[-1].date()}", ""]

    def emit(t: str) -> None:
        print(t); out.append(t)

    bh = tsmom.buy_hold_trades(daily, s)
    lf = tsmom.tsmom_trades(daily, s, lookback=12, allow_short=False, label="tsmom12 L/flat")
    ls = tsmom.tsmom_trades(daily, s, lookback=12, allow_short=True, label="tsmom12 L/S")
    lf6 = tsmom.tsmom_trades(daily, s, lookback=6, allow_short=False, label="tsmom6 L/flat")

    emit("## Full sample (the secular-bull-trap control: does TSMOM beat buy&hold risk-adjusted?)")
    emit("  " + line("buy & hold (always long)", bh, seed))
    emit("  " + line("TSMOM 12mo long/flat", lf, seed))
    emit("  " + line("TSMOM 12mo long/short", ls, seed))
    emit("  " + line("TSMOM 6mo long/flat", lf6, seed))
    emit("  (success bar: TSMOM annSharpe > buy&hold AND/OR materially smaller maxDD, OOS-robust)")
    emit("")

    emit("## Out-of-sample (chronological halves)")
    for name, tr in (("buy&hold", bh), ("TSMOM12 L/flat", lf), ("TSMOM12 L/S", ls)):
        a, b = seasonal.split(tr)
        emit(f"  {name} IS : " + line("", a, seed).strip())
        emit(f"  {name} OOS: " + line("", b, seed).strip())
    emit("")
    emit("NOTE: mean is per-MONTH return; annSharpe = monthly Sharpe x sqrt(12); maxDD in cumulative "
         "monthly-return units. TSMOM's value is drawdown/bear avoidance, not extra bull-market return.")

    report = ROOT / ".omc" / "research" / "tsmom-findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n[written] {report}")


if __name__ == "__main__":
    main()
