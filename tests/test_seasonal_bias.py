"""Live seasonal sizing bias: winter-long runs full, off-edge entries are damped (<=1.0)."""
from datetime import datetime, timezone

from goldtrader.config import Settings
from goldtrader.strategy.seasonal_bias import seasonal_size_scaler
from goldtrader.types import Action

WINTER = datetime(2026, 1, 15, tzinfo=timezone.utc)   # January -> winter
SUMMER = datetime(2026, 7, 15, tzinfo=timezone.utc)   # July -> summer


def _s(**kw) -> Settings:
    base = {"seasonal_bias_enabled": True, "seasonal_offseason_scaler": 0.6}
    base.update(kw)
    return Settings(**base)


def test_winter_long_is_full_size():
    mult, reason = seasonal_size_scaler(WINTER, Action.BUY, _s())
    assert mult == 1.0
    assert "favored" in reason


def test_winter_short_is_damped():
    mult, _ = seasonal_size_scaler(WINTER, Action.SELL, _s())
    assert mult == 0.6


def test_summer_long_and_short_damped():
    assert seasonal_size_scaler(SUMMER, Action.BUY, _s())[0] == 0.6
    assert seasonal_size_scaler(SUMMER, Action.SELL, _s())[0] == 0.6


def test_disabled_is_neutral():
    mult, reason = seasonal_size_scaler(SUMMER, Action.BUY, _s(seasonal_bias_enabled=False))
    assert mult == 1.0 and reason == "disabled"


def test_scaler_is_clamped_and_only_reduces():
    # an out-of-range scaler is clamped into (0,1]; never a boost
    assert seasonal_size_scaler(SUMMER, Action.BUY, _s(seasonal_offseason_scaler=5.0))[0] == 1.0
    assert seasonal_size_scaler(SUMMER, Action.BUY, _s(seasonal_offseason_scaler=-1.0))[0] == 0.0


def test_april_is_winter_october_is_summer():
    apr = datetime(2026, 4, 20, tzinfo=timezone.utc)
    oct_ = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert seasonal_size_scaler(apr, Action.BUY, _s())[0] == 1.0   # Apr still winter window
    assert seasonal_size_scaler(oct_, Action.BUY, _s())[0] == 0.6  # Oct is summer
