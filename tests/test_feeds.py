"""Pure-function tests for the FRED macro + gold-news feeds (V7 P1b)."""
from goldtrader.feeds.macro import MacroSnapshot, direction
from goldtrader.feeds.news import build_digest, gold_relevant


# ---------------- macro ----------------
def test_direction():
    assert direction(2.0, 1.0) == "rising"
    assert direction(1.0, 2.0) == "falling"
    assert direction(1.0, 1.0) == "flat"


def test_macro_summary_includes_present_fields():
    snap = MacroSnapshot(real_yield=1.97, real_yield_dir="rising", dollar=104.3,
                         dollar_dir="falling", ts="2026-06-04T00:00:00+00:00")
    s = snap.summary()
    assert "1.97%" in s and "rising" in s and "104.3" in s and "falling" in s


def test_macro_summary_empty_when_no_data():
    assert MacroSnapshot(None, "flat", None, "flat", "t").summary() == ""


# ---------------- news ----------------
def test_gold_relevant_matches_terms():
    assert gold_relevant("Gold price rallies as the dollar weakens")
    assert gold_relevant("Fed signals possible rate cut amid inflation data")
    assert gold_relevant("XAU/USD eyes safe-haven bid")


def test_gold_relevant_rejects_unrelated():
    assert not gold_relevant("Tesla unveils new electric truck")
    assert not gold_relevant("Premier League results roundup")


def test_build_digest_limits_and_formats():
    titles = [f"Gold headline {i}" for i in range(20)]
    out = build_digest(titles, 5)
    assert out.count("\n") == 4  # 5 bullets -> 4 newlines
    assert out.startswith("- Gold headline 0")


def test_build_digest_empty():
    assert build_digest([], 5) == ""
