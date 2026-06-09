"""V7 Phase-0 live-safety gates: spread guard, session/weekend/Monday timing,
news-blackout pure functions, and the absolute lot cap."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from goldtrader.config import Settings
from goldtrader.feeds.calendar import _parse_ff_events, default_blackout, event_blackout
from goldtrader.mt5.client import MT5Client
from goldtrader.risk.manager import RiskManager
from goldtrader.safety import guards
from goldtrader.supervisor import scheduler
from goldtrader.types import Action, OrderIntent, SymbolSpec

GOLD_SPEC = SymbolSpec(
    name="XAUUSD", digits=2, point=0.01, volume_min=0.01, volume_step=0.01,
    volume_max=35.0, contract_size=100.0, tick_value=1.0, tick_size=0.01,
    stops_level=20, freeze_level=10, filling_mode=3,
)


# ---------------- P0.1 spread guard ----------------
def test_entry_spread_ok_within_cap():
    s = Settings(spread_guard_enabled=True, max_entry_spread_points=50)
    assert guards.entry_spread_ok(30.0, s).allowed is True


def test_entry_spread_blocks_blowout():
    s = Settings(spread_guard_enabled=True, max_entry_spread_points=50)
    assert guards.entry_spread_ok(80.0, s).allowed is False


def test_entry_spread_disabled_passes():
    s = Settings(spread_guard_enabled=False)
    assert guards.entry_spread_ok(999.0, s).allowed is True


# ---------------- ATR-spike guard ----------------
def test_atr_spike_within_cap_allows():
    s = Settings(atr_spike_guard_enabled=True, atr_spike_mult=2.8)
    assert guards.entry_atr_spike_ok(1.5, s).allowed is True


def test_atr_spike_blocks_shock():
    s = Settings(atr_spike_guard_enabled=True, atr_spike_mult=2.8)
    assert guards.entry_atr_spike_ok(3.2, s).allowed is False


def test_atr_spike_nan_fails_open():
    s = Settings(atr_spike_guard_enabled=True, atr_spike_mult=2.8)
    assert guards.entry_atr_spike_ok(float("nan"), s).allowed is True


def test_atr_spike_disabled_passes():
    s = Settings(atr_spike_guard_enabled=False)
    assert guards.entry_atr_spike_ok(99.0, s).allowed is True


# ---------------- P0.3 session gate ----------------
def test_in_session_overlap_allows():
    s = Settings(session_filter_enabled=True, trading_session_start_utc=7, trading_session_end_utc=17)
    assert scheduler.in_session(s, datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)) is True


def test_in_session_asia_blocked():
    s = Settings(session_filter_enabled=True, trading_session_start_utc=7, trading_session_end_utc=17)
    assert scheduler.in_session(s, datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc)) is False
    # end hour is exclusive
    assert scheduler.in_session(s, datetime(2026, 6, 4, 17, 0, tzinfo=timezone.utc)) is False


def test_in_session_disabled_always_true():
    s = Settings(session_filter_enabled=False)
    assert scheduler.in_session(s, datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc)) is True


# ---------------- P0.4 weekend flat + Monday grace ----------------
def test_should_close_for_weekend_friday_cutoff():
    s = Settings(weekend_flat_enabled=True, weekend_flat_hour_utc=20, weekend_flat_minute_utc=30)
    fri_late = datetime(2026, 6, 5, 20, 45, tzinfo=timezone.utc)  # Friday 20:45 UTC
    assert fri_late.weekday() == 4
    assert scheduler.should_close_for_weekend(s, fri_late) is True
    assert scheduler.should_close_for_weekend(s, datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc)) is False
    assert scheduler.should_close_for_weekend(s, datetime(2026, 6, 4, 21, 0, tzinfo=timezone.utc)) is False  # Thu


def test_monday_grace_window():
    s = Settings(monday_grace_minutes=30)
    sunday = datetime(2026, 6, 7, 22, 15, tzinfo=timezone.utc)  # Sunday 22:15 (15 min after reopen)
    assert sunday.weekday() == 6
    assert scheduler.in_monday_grace(s, sunday) is True
    assert scheduler.in_monday_grace(s, datetime(2026, 6, 7, 23, 0, tzinfo=timezone.utc)) is False  # past grace
    assert scheduler.in_monday_grace(s, datetime(2026, 6, 7, 21, 0, tzinfo=timezone.utc)) is False  # pre-reopen


# ---------------- P0.2 news blackout (pure functions) ----------------
def test_event_blackout_within_window():
    ev = datetime(2026, 6, 5, 12, 30, tzinfo=timezone.utc)
    events = [{"title": "NFP", "dt_utc": ev.isoformat()}]
    now = datetime(2026, 6, 5, 12, 15, tzinfo=timezone.utc)  # 15 min before -> within pre=30
    assert event_blackout(now, events, 30, 15)[0] is True


def test_event_blackout_outside_window():
    ev = datetime(2026, 6, 5, 12, 30, tzinfo=timezone.utc)
    events = [{"title": "NFP", "dt_utc": ev.isoformat()}]
    now = datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc)  # 90 min before
    assert event_blackout(now, events, 30, 15)[0] is False


def test_default_blackout_fails_closed_at_release():
    et = ZoneInfo("America/New_York")
    now_et = datetime(2026, 6, 4, 8, 35, tzinfo=et)  # Thursday 08:35 ET, within +15 of 08:30
    assert default_blackout(now_et.astimezone(timezone.utc), 30, 15)[0] is True


def test_default_blackout_clear_midday():
    et = ZoneInfo("America/New_York")
    now_et = datetime(2026, 6, 4, 11, 0, tzinfo=et)  # 11:00 ET — no default window
    assert default_blackout(now_et.astimezone(timezone.utc), 30, 15)[0] is False


def test_parse_ff_events_filters_to_high_impact_usd():
    raw = [
        {"title": "Non-Farm Payrolls", "country": "USD", "impact": "High", "date": "2026-06-05T08:30:00-04:00"},
        {"title": "EUR thing", "country": "EUR", "impact": "High", "date": "2026-06-05T10:00:00-04:00"},
        {"title": "Low US thing", "country": "USD", "impact": "Low", "date": "2026-06-05T09:00:00-04:00"},
    ]
    out = _parse_ff_events(raw)
    assert len(out) == 1 and out[0]["title"] == "Non-Farm Payrolls"


# ---------------- P0.6 absolute lot cap ----------------
class _Tick:
    ask = 4000.0
    bid = 3999.5
    time = 0


class _FakeClient(MT5Client):
    def __init__(self, settings, equity):
        super().__init__(settings)
        self.spec = GOLD_SPEC
        self.symbol = "XAUUSD"
        self._equity = equity

    def equity(self):
        return self._equity

    def get_tick(self):
        return _Tick()


def _intent():
    return OrderIntent(side=Action.BUY, confidence=1.0, rationale="t", signal_hash="h")


def test_lot_cap_clamps():
    # fixed stop + no vol gate so evaluate() makes no MT5 data calls.
    s = Settings(regime_filter_enabled=False, sl_mode="fixed", fixed_sl_points=500,
                 risk_pct_per_trade=0.5, max_lots_absolute=1.0)
    rm = RiskManager(s, _FakeClient(s, equity=1_000_000.0))
    d = rm.evaluate(_intent())
    assert d.approved and d.lots == 1.0  # raw would be 10 lots; clamped to 1.0


def test_lot_cap_disabled_allows_more():
    s = Settings(regime_filter_enabled=False, sl_mode="fixed", fixed_sl_points=500,
                 risk_pct_per_trade=0.5, max_lots_absolute=0.0)
    rm = RiskManager(s, _FakeClient(s, equity=1_000_000.0))
    d = rm.evaluate(_intent())
    assert d.approved and d.lots == 10.0
