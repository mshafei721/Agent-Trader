"""Reflection / self-heal / learning loop.

Two layers:
  1. defensive_state()  - DETERMINISTIC, always-on, cheap. Reduces risk or pauses
     entries on losing streaks / negative expectancy. Can only make trading SAFER.
  2. ReflectionEngine   - SLOW (every N closed trades and/or daily). Computes stats
     and (optionally) asks an LLM for ADVISORY parameter suggestions, written to a
     report. It NEVER auto-applies parameter changes.

Parameter suggestions are confined to a whitelist with hard bounds and can never
touch risk %, loss caps, total-risk cap, or the demo guard.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Settings
from ..logging_setup import get_logger
from .journal import Journal

log = get_logger("goldtrader.reflection")

# Params the LLM may suggest tuning, with hard [min, max] bounds. Anything outside
# this map (especially risk_pct_per_trade / loss caps / demo guard) is REJECTED.
TUNABLE_BOUNDS = {
    "adx_min_trend": (10.0, 35.0),
    "atr_sl_mult": (1.0, 3.0),
    "atr_tp_mult": (1.0, 4.0),
    "trail_atr_mult": (1.0, 3.5),
    "breakeven_at_r": (0.3, 1.5),
    "cut_loss_at_r": (0.3, 1.0),
    "partial_tp_r": (0.5, 2.0),
    "bias_veto_conviction": (0.6, 0.9),
    "bias_exit_conviction": (0.5, 0.8),
    "min_conviction": (0.4, 0.8),
    "ema_fast": (5.0, 30.0),
    "ema_slow": (30.0, 100.0),
    "cot_extreme_z": (0.5, 2.0),  # COT positioning gate threshold (P3.2 preset uses 1.0)
    # Sizing-overlay strengths (V7 ensemble). TIGHT rails: the self-learning loop may tune how
    # HARD each overlay damps, but the bounds keep them as real damps — it can never set them to
    # 1.0 to silently disable a drawdown control (the classic ensemble-destruction failure mode).
    "seasonal_offseason_scaler": (0.4, 0.9),
    "tsmom_downtrend_scaler": (0.2, 0.9),
    "vol_target_annual": (0.10, 0.30),
}


@dataclass(frozen=True)
class DefensiveState:
    risk_mult: float
    pause: bool
    reason: str


# ---------------- deterministic self-heal (always-on) ----------------
def _loss_streak(rows) -> int:
    streak = 0
    for r in rows:  # newest first
        if (r["realized_pnl"] or 0) < 0:
            streak += 1
        else:
            break
    return streak


def defensive_state(journal: Journal, settings: Settings) -> DefensiveState:
    """Reduce risk / pause entries after losing streaks. Only ever makes trading safer."""
    rows = journal.recent_outcomes(limit=10)
    if len(rows) < 3:
        return DefensiveState(1.0, False, "warmup")
    streak = _loss_streak(rows)
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    avg_r = sum(rs) / len(rs) if rs else 0.0

    if streak >= settings.defensive_pause_streak:
        return DefensiveState(0.25, True, f"pause: {streak} consecutive losers")
    if streak >= settings.defensive_loss_streak:
        return DefensiveState(0.5, False, f"halve risk: {streak} consecutive losers")
    if streak == 2:
        return DefensiveState(0.75, False, "2 consecutive losers")
    if avg_r < 0 and len(rows) >= 5:
        return DefensiveState(0.75, False, f"negative expectancy (avg R {avg_r:.2f})")
    return DefensiveState(1.0, False, "normal")


# ---------------- statistics ----------------
def compute_stats(rows) -> dict:
    """rows = recent_closed_detailed() (each has realized_pnl, r_multiple, side, action)."""
    if not rows:
        return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy": 0.0,
                "profit_factor": 0.0, "net_pnl": 0.0, "max_loss_streak": 0, "by_direction": {}}
    pnls = [(r["realized_pnl"] or 0) for r in rows]
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    # max consecutive losers anywhere in the window
    max_streak = cur = 0
    for p in pnls:
        cur = cur + 1 if p < 0 else 0
        max_streak = max(max_streak, cur)
    by_dir: dict[str, dict] = {}
    for side in ("BUY", "SELL"):
        srows = [r for r in rows if (r["side"] or "") == side]
        if srows:
            sw = sum(1 for r in srows if (r["realized_pnl"] or 0) > 0)
            by_dir[side] = {"trades": len(srows), "win_rate": round(sw / len(srows), 2)}
    return {
        "trades": len(rows),
        "win_rate": round(len(wins) / len(rows), 3),
        "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "expectancy": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "net_pnl": round(sum(pnls), 2),
        "max_loss_streak": max_streak,
        "by_direction": by_dir,
    }


# ---------------- LLM suggestion validation (pure) ----------------
def validate_suggestions(raw_list) -> list[dict]:
    """Keep only whitelisted params with numeric, in-bounds suggested values."""
    out = []
    if not isinstance(raw_list, list):
        return out
    for s in raw_list:
        if not isinstance(s, dict):
            continue
        param = s.get("param")
        if param not in TUNABLE_BOUNDS:
            continue
        try:
            suggested = float(s.get("suggested"))
        except (TypeError, ValueError):
            continue
        lo, hi = TUNABLE_BOUNDS[param]
        if not (lo <= suggested <= hi):
            continue
        out.append({
            "param": param,
            "suggested": suggested,
            "reason": str(s.get("reason", ""))[:300],
            "confidence": s.get("confidence"),
        })
    return out


class ReflectionEngine:
    def __init__(self, settings: Settings, journal: Journal, notifier=None):
        self.s = settings
        self.journal = journal
        self.notifier = notifier

    def is_due(self, state) -> bool:
        if not self.s.reflection_enabled:
            return False
        closed = self.journal.closed_count()
        since = closed - getattr(state, "trades_at_last_reflection", 0)
        if since >= self.s.reflection_every_n_trades:
            return True
        if self.s.reflection_daily:
            last = getattr(state, "last_reflection_iso", None)
            today = datetime.now(timezone.utc).date().isoformat()
            last_day = (last or "")[:10]
            if last_day != today and closed > 0:
                return True
        return False

    def maybe_run(self, state) -> bool:
        closed = self.journal.closed_count()
        since = closed - getattr(state, "trades_at_last_reflection", 0)
        if not self.is_due(state):
            return False
        try:
            self.run()
        except Exception as exc:  # noqa: BLE001 — reflection must never crash the loop
            log.error("reflection_failed", error=str(exc))
        state.last_reflection_iso = datetime.now(timezone.utc).isoformat()
        # Only the N-trade milestone advances the cumulative counter; a daily-only
        # reflection refreshes the report without resetting "trades toward next
        # reflection", so the dashboard shows total closed trades (e.g. 13/20).
        if since >= self.s.reflection_every_n_trades:
            state.trades_at_last_reflection = closed
        return True

    def run(self) -> dict:
        rows = self.journal.recent_closed_detailed(limit=max(self.s.reflection_min_trades, 30))
        stats = compute_stats(rows)
        defensive = defensive_state(self.journal, self.s)
        suggestions: list[dict] = []
        llm_note = ""
        if (self.s.reflection_use_llm and stats["trades"] >= self.s.reflection_min_trades):
            suggestions, llm_note = self._llm_suggestions(stats, rows)
        else:
            llm_note = (f"LLM suggestions gated: {stats['trades']}/"
                        f"{self.s.reflection_min_trades} closed trades.")
        self._write_report(stats, defensive, suggestions, llm_note)
        log.info("reflection_done", trades=stats["trades"], expectancy=stats["expectancy"],
                 profit_factor=stats["profit_factor"], defensive=defensive.reason,
                 suggestions=len(suggestions))
        if self.notifier:
            self.notifier.notify(
                "reflection",
                f"trades={stats['trades']} winrate={stats['win_rate']:.0%} "
                f"avgR={stats['avg_r']:+.2f} PF={stats['profit_factor']} "
                f"defensive={defensive.reason} recs={len(suggestions)}",
            )
        return {"stats": stats, "defensive": defensive.__dict__, "suggestions": suggestions}

    # ---- LLM (advisory only; anthropic provider) ----
    def _llm_suggestions(self, stats, rows) -> tuple[list[dict], str]:
        if self.s.llm_provider != "anthropic":
            return [], f"LLM reflection supported for anthropic provider only (got {self.s.llm_provider})."
        try:
            import os

            from langchain_anthropic import ChatAnthropic
            from langchain_core.messages import HumanMessage, SystemMessage

            if self.s.llm_api_key is not None:
                os.environ.setdefault("ANTHROPIC_API_KEY", self.s.llm_api_key.get_secret_value())
            current = {p: getattr(self.s, p, None) for p in TUNABLE_BOUNDS}
            trades = [{"r": r["r_multiple"], "side": r["side"],
                       "ctx": (r["context_json"] or "")[:160]} for r in rows[:30]]
            system = (
                "You are a trading-strategy reviewer. Given recent CLOSED gold (XAUUSD) "
                "trades, summary stats, and current tunable parameters with hard bounds, "
                "suggest at most 3 small parameter adjustments that could reduce losses or "
                "improve expectancy. ONLY use parameters from the allowed list and stay "
                "within bounds. Prefer no change over risky change. "
                "Respond with ONLY a JSON array: "
                "[{\"param\":str,\"suggested\":number,\"reason\":str,\"confidence\":0..1}]. "
                "Return [] if no change is warranted."
            )
            human = json.dumps({"stats": stats, "recent_trades": trades,
                                "current_params": current, "bounds": TUNABLE_BOUNDS}, default=str)
            llm = ChatAnthropic(model=self.s.llm_deep_model, temperature=0, max_tokens=1200,
                                model_kwargs={"cache_control": {"type": "ephemeral"}})
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            arr = _extract_json_array(text)
            return validate_suggestions(arr), text[:1500]
        except Exception as exc:  # noqa: BLE001
            log.warning("llm_reflection_failed", error=str(exc))
            return [], f"LLM reflection error: {exc}"

    def _write_report(self, stats, defensive: DefensiveState, suggestions, llm_note) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        d = self.s.reflections_dir
        d.mkdir(parents=True, exist_ok=True)
        payload = {"ts": ts, "stats": stats, "defensive": defensive.__dict__,
                   "suggestions": suggestions, "llm_note": llm_note}
        (d / f"reflection_{ts}.json").write_text(json.dumps(payload, indent=2, default=str),
                                                 encoding="utf-8")
        lines = [f"# Reflection {ts}", "",
                 f"- trades: {stats['trades']}  win rate: {stats['win_rate']:.0%}  "
                 f"avg R: {stats['avg_r']:+.2f}  profit factor: {stats['profit_factor']}",
                 f"- net PnL: {stats['net_pnl']:+.2f}  max loss streak: {stats['max_loss_streak']}",
                 f"- by direction: {stats['by_direction']}",
                 f"- defensive state: {defensive.reason} (risk x{defensive.risk_mult}, "
                 f"pause={defensive.pause})", "",
                 "## Advisory parameter suggestions (NOT auto-applied)", ""]
        if suggestions:
            for s in suggestions:
                lines.append(f"- `{s['param']}` -> {s['suggested']}  "
                             f"(conf {s.get('confidence')}): {s['reason']}")
        else:
            lines.append("- (none)")
        lines += ["", "## LLM note", "", "```", llm_note, "```"]
        (d / f"reflection_{ts}.md").write_text("\n".join(lines), encoding="utf-8")


def _extract_json_array(text: str):
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
