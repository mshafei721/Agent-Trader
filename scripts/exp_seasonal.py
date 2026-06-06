"""Standalone runner: test gold's Halloween/winter + turn-of-month seasonal edges over
~22 years of daily Dukascopy closes, with an out-of-sample split. Honest stats, costs
subtracted. NOT collected by pytest.

    .\\.venv\\Scripts\\python.exe scripts\\exp_seasonal.py

Writes a findings report to .omc/research/seasonal-findings.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.backtest import seasonal, stats  # noqa: E402
from goldtrader.config import get_settings  # noqa: E402


def line(name: str, trades, seed: int) -> tuple[str, object]:
    rs = [t.ret for t in trades]
    st = stats.compute(rs, seed=seed)
    lo, hi = st.expectancy_ci
    excl = "YES" if (lo > 0 or hi < 0) else "no"
    txt = (f"{name:<26} n={st.trades:<4} mean={st.expectancy*100:+.2f}%  "
           f"95%CI[{lo*100:+.2f}%,{hi*100:+.2f}%] excl0={excl:<3} "
           f"win={st.win_rate:.0%}  PF={st.profit_factor:.2f}  "
           f"Sharpe={st.sharpe:+.2f}  Calmar={st.calmar:+.2f}")
    return txt, st


def _as_trades(rets):
    return [seasonal.SeasonTrade("", "", r, "") for r in rets]


def sign_consistency(trades) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.ret > 0) / len(trades)


def main() -> None:
    s = get_settings()
    seed = s.backtest_seed
    daily = seasonal.load_daily(s)
    out = [f"# Gold seasonal edges — daily, {daily.index[0].date()}..{daily.index[-1].date()} "
           f"({len(daily)} bars)", ""]

    def emit(text: str) -> None:
        print(text)
        out.append(text)

    # ---- Halloween / winter ----
    winter, summer = seasonal.winter_summer_trades(daily, s)
    emit("## Halloween / winter (long Nov->Apr) vs summer (long May->Oct)")
    wt, wst = line("winter (full)", winter, seed); emit("  " + wt)
    sumt, _ = line("summer (full)", summer, seed); emit("  " + sumt)
    diff = [w.ret for w in winter][:min(len(winter), len(summer))]
    emit(f"  winter positive-years = {sign_consistency(winter):.0%}  "
         f"(success bar: mean>0 with CI excl 0, winter>summer, >=60% years up)")
    # Decisive control: paired (winter_Y - summer_Y) nets out the secular bull market, so a
    # positive CI here means the SEASONALITY is real, not just "gold went up 10x".
    summer_by_year = {t.label.split()[1]: t.ret for t in summer}
    diffs = [w.ret - summer_by_year[w.label.split()[1].split("-")[0]]
             for w in winter if w.label.split()[1].split("-")[0] in summer_by_year]
    dt, _ = line("winter MINUS summer (paired)", _as_trades(diffs), seed); emit("  " + dt)
    bh = [w.ret + summer_by_year.get(w.label.split()[1].split("-")[0], 0.0) for w in winter]
    emit(f"  buy&hold-approx (winter+summer) mean = {sum(bh)/len(bh)*100:+.2f}%/yr  "
         f"(winter alone captures {sum(w.ret for w in winter)/sum(bh):.0%} of it, half the time in market)")
    wi_is, wi_oos = seasonal.split(winter)
    t_is, _ = line("winter IN-SAMPLE", wi_is, seed); emit("  " + t_is)
    t_oos, _ = line("winter OUT-SAMPLE", wi_oos, seed); emit("  " + t_oos)
    emit("")

    # ---- September (Baur's 2nd anomaly; outside the winter window -> diversifier) ----
    emit("## September long (diversifier candidate, outside the Nov-Apr winter window)")
    sep = seasonal.month_long_trades(daily, s, 9)
    st, _ = line("September long (raw)", sep, seed); emit("  " + st)
    sx, _ = line("September EXCESS vs avg month", seasonal.month_excess_trades(daily, 9), seed); emit("  " + sx)
    se_is, se_oos = seasonal.split(sep)
    t1, _ = line("September IN-SAMPLE", se_is, seed); emit("  " + t1)
    t2, _ = line("September OUT-SAMPLE", se_oos, seed); emit("  " + t2)
    # November as a reference (it's inside winter; should also be strong per Baur)
    nx, _ = line("November EXCESS vs avg month", seasonal.month_excess_trades(daily, 11), seed); emit("  " + nx)
    emit("")

    # ---- turn of month ----
    tom, rest = seasonal.turn_of_month_trades(daily, s)
    emit("## Turn-of-month [-1,+3] vs rest-of-month")
    tt, _ = line("TOM (full)", tom, seed); emit("  " + tt)
    rt, _ = line("rest-of-month (full)", rest, seed); emit("  " + rt)
    tom_is, tom_oos = seasonal.split(tom)
    ti, _ = line("TOM IN-SAMPLE", tom_is, seed); emit("  " + ti)
    to, _ = line("TOM OUT-SAMPLE", tom_oos, seed); emit("  " + to)
    emit("")

    # ---- cost sensitivity for TOM (12 trades/yr -> costs matter most here) ----
    s_nocost = get_settings()
    object.__setattr__(s_nocost, "backtest_cost_spread_points", 0.0)
    object.__setattr__(s_nocost, "backtest_cost_slippage_points", 0.0)
    tom0, _ = seasonal.turn_of_month_trades(daily, s_nocost)
    tz, _ = line("TOM (costs ZEROED)", tom0, seed); emit("  " + tz)
    emit("")
    emit("NOTE: mean is per-trade RETURN (not R). Winter ~1 trade/yr, TOM ~12/yr. "
         "Edge bar = OOS mean>0 with bootstrap 95% CI excluding zero AFTER costs.")

    report = ROOT / ".omc" / "research" / "seasonal-findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n[written] {report}")


if __name__ == "__main__":
    main()
