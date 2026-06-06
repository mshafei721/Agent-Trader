"""Standalone runner: test the real-yield (DFII10) + dollar (DTWEXBGS) regime signal on gold
over the daily history. Regime-gated vs un-gated A/B + a pre/post-2020 OOS split (the regime-
break test). Honest stats, costs subtracted. NOT collected by pytest.

Needs a free FRED API key in .env (fred_api_key). Then:
    .\\.venv\\Scripts\\python.exe scripts\\exp_macro_regime.py

Writes a findings report to .omc/research/macro-regime-findings.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.backtest import macro_regime as mr, seasonal, stats  # noqa: E402
from goldtrader.config import get_settings  # noqa: E402


def line(name: str, trades, seed: int) -> str:
    rs = [t.ret for t in trades]
    if len(rs) < 2:
        return f"{name:<28} n={len(rs)} (too few)"
    st = stats.compute(rs, seed=seed)
    lo, hi = st.expectancy_ci
    excl = "YES" if (lo > 0 or hi < 0) else "no"
    return (f"{name:<28} n={st.trades:<4} mean={st.expectancy*100:+.2f}%  "
            f"95%CI[{lo*100:+.2f}%,{hi*100:+.2f}%] excl0={excl:<3} "
            f"win={st.win_rate:.0%}  PF={st.profit_factor:.2f}  Sharpe={st.sharpe:+.2f}")


def main() -> None:
    s = get_settings()
    seed = s.backtest_seed
    daily = seasonal.load_daily(s)
    real = mr.fetch_fred(s, "DFII10")
    usd = mr.fetch_fred(s, "DTWEXBGS")
    df = mr.align_macro(daily, real, usd)
    df = df.dropna(subset=["real", "usd"])  # start where both macro series exist
    out = [f"# Gold macro-regime (real-yield + dollar) — "
           f"{df.index[0].date()}..{df.index[-1].date()} ({len(df)} days)", ""]

    def emit(t: str) -> None:
        print(t); out.append(t)

    pos_gated = mr.regime_position(df, use_regime=True)
    pos_raw = mr.regime_position(df, use_regime=False)
    tr_gated = mr.regime_trades(df, pos_gated, s, label="gated")
    tr_raw = mr.regime_trades(df, pos_raw, s, label="ungated")

    emit("## Regime gate A/B (does the corr-regime filter add anything?)")
    emit("  " + line("ungated (both up/down)", tr_raw, seed))
    emit("  " + line("regime-GATED", tr_gated, seed))
    emit("  (success bar: GATED mean>0, CI excl 0 after costs, AND gated beats ungated OOS;")
    emit("   if the gate doesn't help, the signal is just re-finding trend -> kill it)")
    emit("")

    emit("## Out-of-sample regime-break test (pre-2020 IS / 2020+ OOS)")
    for name, trades in (("ungated", tr_raw), ("gated", tr_gated)):
        pre, post = mr.date_split(trades, cut="2020-01-01")
        emit(f"  {name} IS  : " + line("", pre, seed).strip())
        emit(f"  {name} OOS : " + line("", post, seed).strip())
    emit("")
    emit("NOTE: mean is per-trade RETURN. Long when real-yield & dollar both falling, short when "
         "both rising; ~10-day hold or flip. Research predicts 'works pre-2022, breaks after'.")

    report = ROOT / ".omc" / "research" / "macro-regime-findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n[written] {report}")


if __name__ == "__main__":
    main()
