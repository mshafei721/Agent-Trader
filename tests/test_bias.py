from types import SimpleNamespace

from goldtrader.strategy.bias import BiasProvider
from goldtrader.types import Action, Signal


class _FakeAdapter:
    def __init__(self, action=Action.BUY, confidence=0.85):
        self.action = action
        self.confidence = confidence
        self.calls = 0

    def get_signal(self, run_date=None):
        self.calls += 1
        return Signal(
            action=self.action, confidence=self.confidence,
            rationale="why", raw="raw", run_date=run_date or "2026-06-02",
        )


def _settings(tmp_path, refresh_hours=4.0):
    return SimpleNamespace(
        bias_file=tmp_path / "bias.json",
        bias_refresh_hours=refresh_hours,
    )


def test_refresh_then_cache_hit(tmp_path):
    fake = _FakeAdapter(Action.BUY, 0.85)
    bp = BiasProvider(_settings(tmp_path), adapter=fake)
    b1 = bp.current()
    assert b1.direction == Action.BUY and b1.conviction == 0.85
    assert fake.calls == 1
    # within TTL -> served from cache, no second LLM call
    b2 = bp.current()
    assert fake.calls == 1
    assert b2.direction == Action.BUY


def test_force_refresh_calls_again(tmp_path):
    fake = _FakeAdapter(Action.SELL, 0.7)
    bp = BiasProvider(_settings(tmp_path), adapter=fake)
    bp.current()
    bp.current(force_refresh=True)
    assert fake.calls == 2


def test_stale_triggers_refresh(tmp_path):
    fake = _FakeAdapter(Action.BUY, 0.8)
    bp = BiasProvider(_settings(tmp_path, refresh_hours=4.0), adapter=fake)
    bp.current()  # writes a fresh bias
    # hand-write a stale timestamp
    import json
    bp.s.bias_file.write_text(json.dumps({
        "direction": "BUY", "conviction": 0.8,
        "ts": "2000-01-01T00:00:00+00:00", "rationale": "old",
    }), encoding="utf-8")
    bp.current()
    assert fake.calls == 2  # stale -> refreshed


def test_direction_helpers(tmp_path):
    bp = BiasProvider(_settings(tmp_path), adapter=_FakeAdapter(Action.HOLD, 0.0))
    b = bp.current()
    assert b.is_flat() and not b.is_long() and not b.is_short()
