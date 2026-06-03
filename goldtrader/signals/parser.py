"""Parse TradingAgents' decision into a structured Signal.

TradingAgents' ``propagate()`` returns a deterministic 5-tier rating string —
one of ``Buy / Overweight / Hold / Underweight / Sell`` — as the decision, with
the full reasoning in ``final_state["final_trade_decision"]``.

Primary path: map the 5-tier rating directly (reliable).
Fallback path: free-text keyword scan, for robustness if a future version
returns prose. Ambiguity always resolves to HOLD (fail-safe).
"""
from __future__ import annotations

import re

from ..types import Action, Signal

# ---- primary: 5-tier rating (matches tradingagents.agents.utils.rating) ----
RATING_TO_ACTION = {
    "buy": Action.BUY,
    "overweight": Action.BUY,        # lighter-conviction bullish
    "hold": Action.HOLD,
    "underweight": Action.SELL,      # lighter-conviction bearish
    "sell": Action.SELL,
}
RATING_CONFIDENCE = {
    "buy": 0.85,
    "overweight": 0.60,
    "hold": 0.0,
    "underweight": 0.60,
    "sell": 0.85,
}
_RATINGS = set(RATING_TO_ACTION)


# ---- fallback: free-text keyword scan ----
_BUY_PAT = re.compile(r"\b(strong buy|buy|long|bullish|accumulate|overweight)\b", re.I)
_SELL_PAT = re.compile(r"\b(strong sell|sell|short|bearish|reduce|liquidate|underweight)\b", re.I)
_HOLD_PAT = re.compile(r"\b(hold|neutral|wait|no action|stay flat)\b", re.I)
_FINAL_PAT = re.compile(
    r"final\s+(?:transaction\s+)?(?:proposal|decision)\s*[:\-]?\s*\**\s*(BUY|SELL|HOLD)",
    re.I,
)
_HIGH_CONV = re.compile(r"\b(high conviction|strongly|clear|decisive|confident)\b", re.I)
_LOW_CONV = re.compile(r"\b(uncertain|mixed|hedged|cautious|marginal|slight|weak)\b", re.I)
_CONF_PAT = re.compile(r"confidence[^0-9]{0,12}(\d{1,3}(?:\.\d+)?)\s*(%|/\s*10|/\s*100)?", re.I)


def _normalize_rating(text: str) -> str | None:
    """Return the canonical lower-case rating if `text` is a bare 5-tier label."""
    clean = (text or "").strip().strip("*:.,").lower()
    return clean if clean in _RATINGS else None


def _detect_action_freetext(text: str) -> tuple[Action, bool]:
    fm = _FINAL_PAT.search(text)
    if fm:
        return Action(fm.group(1).upper()), True
    buys = len(_BUY_PAT.findall(text))
    sells = len(_SELL_PAT.findall(text))
    holds = len(_HOLD_PAT.findall(text))
    if buys == 0 and sells == 0:
        return Action.HOLD, holds > 0
    if buys > sells:
        return Action.BUY, (buys - sells) >= 1
    if sells > buys:
        return Action.SELL, (sells - buys) >= 1
    return Action.HOLD, False


def _freetext_confidence(text: str, action: Action) -> float:
    if action == Action.HOLD:
        return 0.0
    # Explicit score wins ("80%", "7/10", "0.8").
    m = _CONF_PAT.search(text)
    if m:
        val = float(m.group(1))
        unit = (m.group(2) or "").replace(" ", "")
        if unit == "%" or val > 10:
            return max(0.0, min(1.0, val / 100.0))
        if unit.startswith("/100"):
            return max(0.0, min(1.0, val / 100.0))
        # "/10" or a bare 0-10 number
        return max(0.0, min(1.0, val / 10.0))
    base = 0.6
    if _HIGH_CONV.search(text):
        base += 0.2
    if _LOW_CONV.search(text):
        base -= 0.2
    return max(0.3, min(0.9, base))


def parse_signal(rating: str, reasoning: str, run_date: str) -> Signal:
    """Build a Signal from the rating label (primary) + reasoning text."""
    norm = _normalize_rating(rating)
    if norm is not None:
        action = RATING_TO_ACTION[norm]
        confidence = RATING_CONFIDENCE[norm]
        rationale = (reasoning or rating).strip()
        return Signal(
            action=action,
            confidence=confidence,
            rationale=rationale[:1500],
            raw=f"rating={rating}",
            run_date=run_date,
        )
    # Fallback: scan the combined text.
    combined = f"{rating}\n{reasoning}"
    action, confident = _detect_action_freetext(combined)
    confidence = _freetext_confidence(combined, action)
    if not confident and action != Action.HOLD:
        action, confidence = Action.HOLD, 0.0
    return Signal(
        action=action,
        confidence=confidence,
        rationale=(reasoning or rating).strip()[:1500],
        raw=combined[:2000],
        run_date=run_date,
    )


def parse_decision(decision_text: str, run_date: str) -> Signal:
    """Backwards-compatible free-text entry point (also tries the rating path)."""
    return parse_signal(decision_text, decision_text, run_date)
