"""Close the learning loop with our own broker outcomes.

Two mechanisms:
  1. A bounded confidence/size scaler derived from recent realized R-multiples
     (reduce size after a losing streak, restore as it recovers).
  2. TradingAgents' own decision-log reflection is automatic: because we keep a
     persistent memory_log_path and always analyze the SAME ticker, each
     propagate() call resolves prior pending entries with realized return. We
     simply must not wipe that memory between runs.
"""
from __future__ import annotations

from ..config import Settings
from ..logging_setup import get_logger
from .journal import Journal

log = get_logger("goldtrader.feedback")


def risk_scaler(journal: Journal, settings: Settings) -> float:
    """Return a multiplier in [0.25, 1.0] applied to risk %, based on recent R.

    Drawdown discipline: after consecutive losers, shrink size; recover as the
    rolling average R turns positive again.
    """
    rows = journal.recent_outcomes(limit=8)
    if len(rows) < 3:
        return 1.0

    # consecutive recent losers (most-recent first)
    streak = 0
    for r in rows:
        if (r["realized_pnl"] or 0) < 0:
            streak += 1
        else:
            break

    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    avg_r = sum(rs) / len(rs) if rs else 0.0

    scaler = 1.0
    if streak >= 3:
        scaler = 0.25
    elif streak == 2:
        scaler = 0.5
    elif avg_r < 0:
        scaler = 0.75
    log.info("risk_scaler", scaler=scaler, loss_streak=streak, avg_r=round(avg_r, 3))
    return scaler


def performance_note(journal: Journal) -> str:
    """A short natural-language note injected into context for transparency/logs."""
    p = journal.performance_summary(last_n=20)
    if p["trades"] == 0:
        return "No closed trades yet."
    return (
        f"Recent performance (last {p['trades']}): win rate {p['win_rate']:.0%}, "
        f"avg R {p['avg_r']:+.2f}, net PnL {p['net_pnl']:+.2f}."
    )
