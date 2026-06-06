"""CFTC COT positioning feed: net series, z-score, and the fail-open gate (V7 P1a)."""
from goldtrader.feeds.cot import cot_gate, net_series, zscore
from goldtrader.types import Action


def test_net_series_long_minus_short():
    rows = [
        {"report_date_as_yyyy_mm_dd": "2026-05-26", "noncomm_positions_long_all": "200000",
         "noncomm_positions_short_all": "50000"},
        {"report_date_as_yyyy_mm_dd": "2026-05-19", "noncomm_positions_long_all": "150000",
         "noncomm_positions_short_all": "60000"},
    ]
    assert net_series(rows) == [150000.0, 90000.0]


def test_net_series_skips_bad_rows():
    rows = [
        {"report_date_as_yyyy_mm_dd": "2026-05-26", "noncomm_positions_long_all": "100",
         "noncomm_positions_short_all": "40"},
        {"report_date_as_yyyy_mm_dd": "2026-05-19", "noncomm_positions_long_all": None,
         "noncomm_positions_short_all": "40"},
        {"other": "x"},
    ]
    assert net_series(rows) == [60.0]


def test_net_series_dedups_by_date():
    rows = [
        {"report_date_as_yyyy_mm_dd": "2026-05-26", "noncomm_positions_long_all": "100",
         "noncomm_positions_short_all": "40"},
        {"report_date_as_yyyy_mm_dd": "2026-05-26", "noncomm_positions_long_all": "999",
         "noncomm_positions_short_all": "1"},  # duplicate date -> ignored
    ]
    assert net_series(rows) == [60.0]


def test_zscore_basic():
    # latest well above a flat-ish history -> large positive z
    z = zscore(100.0, [10.0, 12.0, 8.0, 11.0, 9.0])
    assert z is not None and z > 5


def test_zscore_none_on_flat_history():
    assert zscore(5.0, [3.0, 3.0, 3.0]) is None  # zero variance
    assert zscore(5.0, [3.0]) is None            # too short


def test_cot_gate_blocks_crowded_long():
    ok, reason = cot_gate(Action.BUY, 2.0, 1.5)
    assert ok is False and "crowded long" in reason


def test_cot_gate_blocks_crowded_short():
    ok, _ = cot_gate(Action.SELL, -2.0, 1.5)
    assert ok is False


def test_cot_gate_allows_within_band():
    assert cot_gate(Action.BUY, 0.5, 1.5)[0] is True
    assert cot_gate(Action.SELL, 0.5, 1.5)[0] is True
    # crowded long does not block a SHORT (only blocks the chasing side)
    assert cot_gate(Action.SELL, 2.0, 1.5)[0] is True


def test_cot_gate_fails_open_without_data():
    assert cot_gate(Action.BUY, None, 1.5)[0] is True
    assert cot_gate(Action.SELL, None, 1.5)[0] is True
