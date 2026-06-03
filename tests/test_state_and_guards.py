from goldtrader.config import Settings
from goldtrader.safety import guards
from goldtrader.supervisor.state import SupervisorState


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    st = SupervisorState(last_signal_hash="abc", start_equity=100.0)
    st.save(p)
    loaded = SupervisorState.load(p)
    assert loaded.last_signal_hash == "abc"
    assert loaded.start_equity == 100.0


def test_day_roll(tmp_path):
    st = SupervisorState()
    rolled = st.roll_day_if_needed(1000.0)
    assert rolled is True
    assert st.day_anchor_equity == 1000.0
    assert st.start_equity == 1000.0
    # same day -> no roll
    assert st.roll_day_if_needed(900.0) is False
    assert st.day_anchor_equity == 1000.0


def test_demo_guard_blocks_real():
    s = Settings(require_demo=True)
    try:
        guards.assert_demo(2, s)  # 2 = real
        assert False, "should have raised"
    except guards.SafetyViolation:
        pass


def test_demo_guard_allows_demo():
    s = Settings(require_demo=True)
    guards.assert_demo(0, s)  # no raise


def test_daily_loss_gate():
    s = Settings(max_daily_loss_pct=2.0)
    # 1.5% down -> allowed
    assert guards.check_daily_loss(1000.0, 985.0, s).allowed is True
    # 2.5% down -> blocked
    assert guards.check_daily_loss(1000.0, 975.0, s).allowed is False


def test_total_loss_breach():
    s = Settings(max_total_loss_pct=10.0)
    assert guards.total_loss_breached(1000.0, 950.0, s) is False
    assert guards.total_loss_breached(1000.0, 880.0, s) is True
