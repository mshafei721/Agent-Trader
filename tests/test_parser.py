from goldtrader.signals.parser import parse_decision, parse_signal
from goldtrader.types import Action


def test_rating_overweight_is_buy():
    s = parse_signal("Overweight", "Gold momentum is constructive...", "2026-06-02")
    assert s.action == Action.BUY
    assert s.confidence == 0.60
    assert "momentum" in s.rationale


def test_rating_buy_high_confidence():
    s = parse_signal("Buy", "Strong macro tailwinds.", "2026-06-02")
    assert s.action == Action.BUY
    assert s.confidence == 0.85


def test_rating_underweight_is_sell():
    s = parse_signal("Underweight", "Rates rising.", "2026-06-02")
    assert s.action == Action.SELL
    assert s.confidence == 0.60


def test_rating_sell():
    s = parse_signal("Sell", "Breakdown.", "2026-06-02")
    assert s.action == Action.SELL
    assert s.confidence == 0.85


def test_rating_hold():
    s = parse_signal("Hold", "Range-bound.", "2026-06-02")
    assert s.action == Action.HOLD
    assert s.confidence == 0.0


def test_rating_with_markdown_bold():
    s = parse_signal("**Buy**", "", "2026-06-02")
    assert s.action == Action.BUY


def test_final_proposal_buy():
    txt = "After debate... FINAL TRANSACTION PROPOSAL: **BUY**. Confidence: 80%."
    s = parse_decision(txt, "2026-06-02")
    assert s.action == Action.BUY
    assert s.confidence == 0.8


def test_final_proposal_sell_slash10():
    txt = "Risks weigh heavy. FINAL DECISION: SELL. confidence 7/10"
    s = parse_decision(txt, "2026-06-02")
    assert s.action == Action.SELL
    assert abs(s.confidence - 0.7) < 1e-9


def test_hold_explicit():
    txt = "The committee recommends we HOLD and wait for clarity."
    s = parse_decision(txt, "2026-06-02")
    assert s.action == Action.HOLD
    assert s.confidence == 0.0


def test_ambiguous_defaults_to_hold():
    # equal buy/sell mentions, no final marker -> fail safe
    txt = "Some argue buy, others argue sell. Unclear."
    s = parse_decision(txt, "2026-06-02")
    assert s.action == Action.HOLD


def test_keyword_buy_without_marker():
    txt = "The technical setup is clearly bullish; we should go long here."
    s = parse_decision(txt, "2026-06-02")
    assert s.action == Action.BUY
    assert s.confidence >= 0.6


def test_dedup_hash_stable():
    a = parse_decision("FINAL TRANSACTION PROPOSAL: BUY confidence 80%", "2026-06-02")
    b = parse_decision("FINAL TRANSACTION PROPOSAL: BUY confidence 82%", "2026-06-02")
    # same bucket (0.8) + same date + same action -> same hash
    assert a.dedup_hash() == b.dedup_hash()
