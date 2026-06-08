"""Forward-test tracker: measure the LIVE demo bot against the go-live gate (V7).

The journal also holds pre-V7 trades, so the forward-test counts only trades that CLOSE after
a start marker (stamped once when the V7 demo run begins). Read-only on the journal — safe to
run while the supervisor is trading.

  .\\.venv\\Scripts\\python.exe scripts\\forward_test.py --start   # stamp start (now) + equity
  .\\.venv\\Scripts\\python.exe scripts\\forward_test.py           # status vs the go-live gate

Go-live gate (demo, ~3 months): win 40-55%, profit factor 1.4-1.85, max drawdown < 15%.
Honest expectation: the whole edge-hunt found NO intraday alpha, so the profitability targets
(PF >= 1.4) most likely WON'T be met — the real thing this test validates is that the overlays +
self-heal keep drawdown controlled and the bot never blows up. A clean ~break-even with small
drawdown is the realistic 'pass for demo' outcome.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from goldtrader.config import get_settings  # noqa: E402

GATE = {"win_lo": 0.40, "win_hi": 0.55, "pf_lo": 1.40, "pf_hi": 1.85, "maxdd_pct": 15.0, "days": 90}
MIN_TRADES = 30  # need a sample before any metric is meaningful


def _marker(s) -> Path:
    return s.state_file.parent / "forward_test.json"   # data/forward_test.json


def mark_start(s) -> None:
    eq = None
    try:
        eq = json.loads(s.account_file.read_text(encoding="utf-8")).get("equity")
    except Exception:  # noqa: BLE001
        pass
    now = datetime.now(timezone.utc)
    _marker(s).write_text(json.dumps({"start_iso": now.isoformat(), "start_equity": eq}, indent=2),
                          encoding="utf-8")
    print(f"forward-test START stamped: {now.isoformat()}  start_equity={eq}")
    print("Trades that close from now on count toward the gate. Re-run without --start for status.")


def _band(name: str, val, lo, hi, fmt="{:.2f}") -> str:
    inside = lo <= val <= hi
    return f"  {name:<14} {fmt.format(val):>8}   target {fmt.format(lo)}-{fmt.format(hi)}   {'OK' if inside else 'outside'}"


def report(s) -> None:
    m = None
    try:
        m = json.loads(_marker(s).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    if not m:
        print("No forward-test marker yet. Run with --start to begin.")
        return
    start_iso, start_eq = m["start_iso"], m.get("start_equity")
    conn = sqlite3.connect(f"file:{s.journal_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT realized_pnl, r_multiple FROM outcomes WHERE close_ts >= ? ORDER BY id ASC",
        (start_iso,)).fetchall()
    conn.close()

    pnls = [(r["realized_pnl"] or 0.0) for r in rows]
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    gw = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
    win = wins / n if n else 0.0
    net = sum(pnls)
    avgr = sum(rs) / len(rs) if rs else 0.0
    peak = cum = mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    mdd_pct = (mdd / start_eq * 100) if start_eq else None
    days = (datetime.now(timezone.utc) - datetime.fromisoformat(start_iso)).total_seconds() / 86400

    print(f"=== Forward-test (demo) — since {start_iso[:16]}  ({days:.1f} / {GATE['days']} days) ===")
    print(f"  closed trades : {n}   net PnL {net:+.2f}   avg R {avgr:+.3f}")
    if n < MIN_TRADES:
        print(f"  -> too few trades for a verdict ({n}/{MIN_TRADES}). Keep running.")
        return
    print(_band("win rate", win, GATE["win_lo"], GATE["win_hi"], "{:.0%}"))
    print(_band("profit factor", pf, GATE["pf_lo"], GATE["pf_hi"]))
    if mdd_pct is not None:
        ok = mdd_pct < GATE["maxdd_pct"]
        print(f"  {'max drawdown':<14} {mdd_pct:>7.1f}%   target < {GATE['maxdd_pct']:.0f}%       {'OK' if ok else 'BREACH'}")
    passed = (GATE["win_lo"] <= win <= GATE["win_hi"] and GATE["pf_lo"] <= pf <= GATE["pf_hi"]
              and mdd_pct is not None and mdd_pct < GATE["maxdd_pct"] and days >= GATE["days"])
    print(f"  GATE: {'PASS - eligible to consider live' if passed else 'not met (expected; demo-only stands)'}")


def main() -> None:
    s = get_settings()
    if "--start" in sys.argv[1:]:
        mark_start(s)
    else:
        report(s)


if __name__ == "__main__":
    main()
