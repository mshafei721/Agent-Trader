"""compute_lot math, validated against the live XAUUSD specs we captured."""
from goldtrader.config import Settings
from goldtrader.mt5.client import MT5Client
from goldtrader.types import SymbolSpec

# Specs captured live from the broker (XAUUSD):
GOLD_SPEC = SymbolSpec(
    name="XAUUSD",
    digits=2,
    point=0.01,
    volume_min=0.01,
    volume_step=0.01,
    volume_max=35.0,
    contract_size=100.0,
    tick_value=1.0,
    tick_size=0.01,
    stops_level=20,
    freeze_level=10,
    filling_mode=3,
)


def _client():
    c = MT5Client(Settings())
    c.spec = GOLD_SPEC
    c.symbol = "XAUUSD"
    return c


def test_lot_for_5usd_stop_500_risk():
    # $5.00 stop = 500 points. loss_per_lot = (5.0/0.01)*1.0 = $500/lot.
    # risk $500 -> 1.0 lot.
    c = _client()
    assert c.compute_lot(5.0, 500.0) == 1.0


def test_lot_rounds_down_to_step():
    # risk $500, $7 stop -> loss_per_lot=$700 -> 0.714 -> rounds down to 0.71
    c = _client()
    assert c.compute_lot(7.0, 500.0) == 0.71


def test_lot_zero_when_below_min():
    # tiny risk that can't afford 0.01 lot
    c = _client()
    # 0.01 lot @ $50 stop = (50/0.01)*1*0.01 = $50 loss; risk $1 -> 0 lots
    assert c.compute_lot(50.0, 1.0) == 0.0


def test_lot_clamped_to_max():
    c = _client()
    # absurd risk -> clamp at volume_max
    assert c.compute_lot(1.0, 10_000_000.0) == 35.0


def test_lot_zero_on_bad_inputs():
    c = _client()
    assert c.compute_lot(0.0, 500.0) == 0.0
    assert c.compute_lot(5.0, 0.0) == 0.0
