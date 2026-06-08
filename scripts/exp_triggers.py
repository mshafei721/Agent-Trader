"""A/B the M30 entry TRIGGER over 5yr — the owner's question: does trading MORE OFTEN help?

Same H4/H1 trend gate, same exits, same costs; only the trigger changes:
  macd_cross (current, selective) vs donchian breakout (N=20/40/55) vs pullback-to-50-EMA.
Reports trade count (+ frequency multiple vs the current trigger), expectancy with bootstrap
95% CI, profit factor, Sharpe, max drawdown. The bar: fires MORE *and* CI excludes zero after
costs. NOT pytest-collected.

    .\\.venv\\Scripts\\python.exe scripts\\exp_triggers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.backtest import data as btdata  # noqa: E402
from goldtrader.backtest.engine import run_backtest  # noqa: E402
from goldtrader.config import get_settings  # noqa: E402


def line(name: str, res, base_n: int) -> str:
    st = res.stats
    lo, hi = st.expectancy_ci
    excl = "YES" if (lo > 0 or hi < 0) else "no"
    mult = f"{st.trades / base_n:.1f}x" if base_n else "-"
    return (f"{name:<24} trades={st.trades:<5} ({mult:>5})  exp={st.expectancy:+.3f}R  "
            f"CI[{lo:+.3f},{hi:+.3f}] excl0={excl:<3} PF={st.profit_factor:.2f}  "
            f"Sharpe={st.sharpe:+.2f}  maxDD={st.max_drawdown_r:.0f}R")


def main() -> None:
    s = get_settings()
    bars = btdata.load_bars(s)
    spec = btdata.load_spec(s)

    base = run_backtest(s.model_copy(update={"entry_trigger": "macd_cross"}), bars, spec,
                        model_management=True, label="macd_cross")
    bn = base.stats.trades
    out = ["## Entry-trigger A/B (same H4/H1 gate + exits + costs, 5yr) — does trading MORE help?"]
    out.append("  " + line("macd_cross (current)", base, bn))

    variants = [
        ("donchian N=20", {"entry_trigger": "donchian", "donchian_lookback": 20}),
        ("donchian N=40", {"entry_trigger": "donchian", "donchian_lookback": 40}),
        ("donchian N=55", {"entry_trigger": "donchian", "donchian_lookback": 55}),
        ("pullback EMA=50", {"entry_trigger": "pullback", "pullback_ema": 50}),
    ]
    for name, upd in variants:
        res = run_backtest(s.model_copy(update=upd), bars, spec, model_management=True, label=name)
        out.append("  " + line(name, res, bn))

    out.append("  bar: fires MORE than current AND CI excludes zero (>0) after costs, OOS-robust.")
    text = "\n".join(out)
    print(text)
    rep = ROOT / ".omc" / "research" / "trigger-findings.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(text + "\n", encoding="utf-8")
    print(f"\n[written] {rep}")


if __name__ == "__main__":
    main()
