from goldtrader.config import Settings
from goldtrader.learning.reflection import (
    compute_stats,
    defensive_state,
    validate_suggestions,
)


class _FakeJournal:
    """outcomes newest-first, like Journal.recent_outcomes."""
    def __init__(self, outcomes):
        self._o = outcomes

    def recent_outcomes(self, limit=10):
        return self._o[:limit]


def _o(pnl, r=None, side="SELL"):
    return {"realized_pnl": pnl, "r_multiple": r if r is not None else (1.0 if pnl > 0 else -1.0),
            "side": side, "action": side}


S = Settings()  # defaults: defensive_loss_streak=4, defensive_pause_streak=6


# ---------------- defensive_state ----------------
def test_defensive_warmup_few_trades():
    d = defensive_state(_FakeJournal([_o(-100), _o(50)]), S)
    assert d.risk_mult == 1.0 and not d.pause


def test_defensive_two_losers_reduces():
    d = defensive_state(_FakeJournal([_o(-1), _o(-1), _o(5), _o(5)]), S)
    assert d.risk_mult == 0.75 and not d.pause


def test_defensive_four_losers_halves():
    d = defensive_state(_FakeJournal([_o(-1)] * 4 + [_o(5)]), S)
    assert d.risk_mult == 0.5 and not d.pause


def test_defensive_six_losers_pauses():
    d = defensive_state(_FakeJournal([_o(-1)] * 6), S)
    assert d.pause is True and d.risk_mult == 0.25


def test_defensive_negative_expectancy():
    # most recent is a win (streak 0) but average R negative over >=5 trades
    rows = [_o(5, 0.2), _o(-100, -1.0), _o(-100, -1.0), _o(-100, -1.0), _o(10, 0.1)]
    d = defensive_state(_FakeJournal(rows), S)
    assert d.risk_mult == 0.75


# ---------------- compute_stats ----------------
def test_stats_basic():
    rows = [_o(100, 1.0, "SELL"), _o(-50, -1.0, "SELL"), _o(200, 2.0, "BUY")]
    s = compute_stats(rows)
    assert s["trades"] == 3
    assert s["win_rate"] == round(2 / 3, 3)
    assert s["profit_factor"] == round(300 / 50, 2)
    assert s["net_pnl"] == 250.0
    assert "SELL" in s["by_direction"] and "BUY" in s["by_direction"]


def test_stats_max_loss_streak():
    rows = [_o(10), _o(-1), _o(-1), _o(-1), _o(5)]
    assert compute_stats(rows)["max_loss_streak"] == 3


# ---------------- validate_suggestions ----------------
def test_validate_keeps_in_bounds():
    out = validate_suggestions([{"param": "adx_min_trend", "suggested": 22, "reason": "x"}])
    assert len(out) == 1 and out[0]["param"] == "adx_min_trend"


def test_validate_drops_forbidden_param():
    out = validate_suggestions([{"param": "risk_pct_per_trade", "suggested": 5, "reason": "no"}])
    assert out == []


def test_validate_drops_out_of_bounds():
    out = validate_suggestions([{"param": "adx_min_trend", "suggested": 999, "reason": "no"}])
    assert out == []


def test_validate_drops_non_numeric():
    out = validate_suggestions([{"param": "trail_atr_mult", "suggested": "abc"}])
    assert out == []
