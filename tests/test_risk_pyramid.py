from types import SimpleNamespace

from goldtrader.risk.manager import can_pyramid, half_lot, open_risk_money

# XAUUSD specs
TICK_VALUE, TICK_SIZE, STEP, VMIN = 1.0, 0.01, 0.01, 0.01


def _pos(ptype, price_open, sl, volume=0.10, profit=0.0):
    # ptype: 0=BUY, 1=SELL
    return SimpleNamespace(type=ptype, price_open=price_open, sl=sl, volume=volume, profit=profit)


# ---------------- half_lot ----------------
def test_half_lot_splits_evenly():
    assert half_lot(0.20, STEP, VMIN) == 0.10


def test_half_lot_floors_to_step():
    # 0.21 -> half 0.10 (floor), remaining 0.11
    assert half_lot(0.21, STEP, VMIN) == 0.10


def test_half_lot_min_position_cannot_split():
    # 0.01 -> half would be 0.0 < vmin -> None
    assert half_lot(0.01, STEP, VMIN) is None


# ---------------- can_pyramid ----------------
def test_pyramid_allows_winner_under_max():
    same = [_pos(1, 4500, 4510, profit=12.0)]  # winning short
    ok, _ = can_pyramid(same, max_positions=3, winners_only=True)
    assert ok is True


def test_pyramid_blocks_loser():
    same = [_pos(1, 4500, 4510, profit=-8.0)]  # losing short
    ok, reason = can_pyramid(same, max_positions=3, winners_only=True)
    assert ok is False and "winner" in reason


def test_pyramid_blocks_at_max():
    same = [_pos(1, 4500, 4510, profit=5.0)] * 3
    ok, reason = can_pyramid(same, max_positions=3, winners_only=True)
    assert ok is False and reason == "max_positions_reached"


# ---------------- open_risk_money ----------------
def test_open_risk_short():
    # SELL entry 4500, SL 4510 -> $10 adverse, 0.10 lots -> (10/0.01)*1*0.10 = $100
    pos = [_pos(1, 4500.0, 4510.0, volume=0.10)]
    assert open_risk_money(pos, TICK_VALUE, TICK_SIZE) == 100.0


def test_open_risk_breakeven_is_zero():
    # SELL with SL BELOW entry (locked profit) -> no risk
    pos = [_pos(1, 4500.0, 4490.0, volume=0.10)]
    assert open_risk_money(pos, TICK_VALUE, TICK_SIZE) == 0.0


def test_open_risk_sums_positions():
    pos = [
        _pos(0, 4500.0, 4490.0, volume=0.10),  # BUY, $10 risk -> $100
        _pos(1, 4500.0, 4505.0, volume=0.10),  # SELL, $5 risk -> $50
    ]
    assert open_risk_money(pos, TICK_VALUE, TICK_SIZE) == 150.0
