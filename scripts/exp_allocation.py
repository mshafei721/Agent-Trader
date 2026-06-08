"""Lab proof of the SEASONAL-CORE ALLOCATION (the live 'lean into what works' mode).

Daily long-gold exposure = overlay stack (winter x TSMOM-regime x vol-target), rebalanced only
on meaningful shifts, with turnover cost. Shows the realistic TRADE FREQUENCY and whether the
risk-adjusted edge survives execution, vs buy-and-hold, with an OOS split. NOT pytest-collected.

    .\\.venv\\Scripts\\python.exe scripts\\exp_allocation.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.backtest import seasonal  # noqa: E402
from goldtrader.backtest.allocation import annualized, run_allocation  # noqa: E402
from goldtrader.config import get_settings  # noqa: E402


def fmt(name: str, a: dict, extra: str = "") -> str:
    calmar = (a["total"] / a["max_dd"]) if a["max_dd"] > 0 else float("inf")
    return (f"{name:<24} annRet={a['ann_return']*100:+.1f}%  annSharpe={a['ann_sharpe']:+.2f}  "
            f"maxDD={a['max_dd']*100:.0f}%  Calmar={calmar:+.2f}  total={a['total']*100:+.0f}%  {extra}")


def main() -> None:
    s = get_settings()
    daily = seasonal.load_daily(s)
    res = run_allocation(daily, s)
    strat, bh = res["strat"], res["bh"]
    yrs = max(1.0, (date.fromisoformat(res["to"]) - date.fromisoformat(res["from"])).days / 365.25)

    out = [f"## Seasonal-core allocation (overlay-driven long-gold) — {res['from']}..{res['to']}"]
    out.append("  " + fmt("buy & hold", annualized(bh)))
    out.append("  " + fmt("seasonal-core alloc", annualized(strat),
                          f"rebalances={res['rebalances']} (~{res['rebalances']/yrs:.1f}/yr)  "
                          f"avg_exposure={res['avg_exposure']:.0%}"))
    out.append("")
    out.append("## Out-of-sample (chronological halves)")
    h = len(strat) // 2
    out.append("  buy&hold   IS : " + fmt("", annualized(bh[:h])).strip())
    out.append("  buy&hold   OOS: " + fmt("", annualized(bh[h:])).strip())
    out.append("  alloc      IS : " + fmt("", annualized(strat[:h])).strip())
    out.append("  alloc      OOS: " + fmt("", annualized(strat[h:])).strip())
    out.append("")
    out.append("NOTE: low-frequency by design — rebalances only when season/regime/vol shift. The win "
               "is drawdown-controlled compounding of gold beta + winter tilt, not raw return.")
    text = "\n".join(out)
    print(text)
    rep = ROOT / ".omc" / "research" / "allocation-findings.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(text + "\n", encoding="utf-8")
    print(f"\n[written] {rep}")


if __name__ == "__main__":
    main()
