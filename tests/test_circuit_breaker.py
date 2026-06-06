"""Circuit-breaker state machine + disk persistence (V7 P0.5).

Persistence closes the crash-loop hole: a relaunch must reload an open breaker
instead of resetting the failure count to zero and retrying a broken broker.
"""
from goldtrader.healing.circuit_breaker import CircuitBreaker


def test_opens_after_threshold():
    b = CircuitBreaker(fail_threshold=3, cooldown_s=600)
    assert not b.is_open
    b.record_failure()
    b.record_failure()
    assert not b.is_open
    b.record_failure()
    assert b.is_open


def test_success_resets():
    b = CircuitBreaker(fail_threshold=2, cooldown_s=600)
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert not b.is_open  # streak was reset, so one failure isn't enough


def test_half_open_after_cooldown():
    b = CircuitBreaker(fail_threshold=1, cooldown_s=0.0)
    b.record_failure()
    # cooldown elapsed (0s) -> next check half-opens and allows a probe
    assert b.is_open is False


def test_persists_open_state_across_restart(tmp_path):
    p = tmp_path / "circuit_breaker.json"
    b = CircuitBreaker(fail_threshold=3, cooldown_s=600, state_path=p)
    b.record_failure()
    b.record_failure()
    b.record_failure()
    assert b.is_open
    # simulate crash + relaunch: a fresh instance must reload the open state.
    b2 = CircuitBreaker(fail_threshold=3, cooldown_s=600, state_path=p)
    assert b2.is_open  # still in cooldown -> stays open, no retry storm


def test_persisted_success_clears_state(tmp_path):
    p = tmp_path / "circuit_breaker.json"
    b = CircuitBreaker(fail_threshold=2, cooldown_s=600, state_path=p)
    b.record_failure()
    b.record_success()
    b2 = CircuitBreaker(fail_threshold=2, cooldown_s=600, state_path=p)
    assert not b2.is_open
