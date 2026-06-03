"""Hard safety gates. These are checked before any order is ever placed."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.safety")

# MT5 trade_mode constants
TRADE_MODE_DEMO = 0
TRADE_MODE_CONTEST = 1
TRADE_MODE_REAL = 2


class SafetyViolation(Exception):
    """Raised for hard, non-recoverable safety failures (e.g. real account)."""


@dataclass
class GateResult:
    allowed: bool
    reason: str = "ok"


def assert_demo(trade_mode: int, settings: Settings) -> None:
    """Hard stop if running on a non-demo account while require_demo is set."""
    if settings.require_demo and trade_mode != TRADE_MODE_DEMO:
        raise SafetyViolation(
            f"require_demo is ON but account trade_mode={trade_mode} "
            f"(0=demo,1=contest,2=real). Refusing to trade real money."
        )
    if trade_mode != TRADE_MODE_DEMO:
        log.warning("running_on_non_demo_account", trade_mode=trade_mode)


def kill_switch_active(settings: Settings) -> bool:
    return settings.kill_switch_file.exists()


def check_daily_loss(day_anchor_equity: float, current_equity: float, settings: Settings) -> GateResult:
    """Returns disallowed if daily drawdown exceeds the configured cap."""
    if day_anchor_equity <= 0:
        return GateResult(True)
    drawdown_pct = (day_anchor_equity - current_equity) / day_anchor_equity * 100.0
    if drawdown_pct >= settings.max_daily_loss_pct:
        return GateResult(False, f"daily loss {drawdown_pct:.2f}% >= cap {settings.max_daily_loss_pct}%")
    return GateResult(True)


def total_loss_breached(start_equity: float, current_equity: float, settings: Settings) -> bool:
    """True when the catastrophic total-loss threshold is hit (-> kill switch)."""
    if start_equity <= 0:
        return False
    drawdown_pct = (start_equity - current_equity) / start_equity * 100.0
    return drawdown_pct >= settings.max_total_loss_pct


def trip_kill_switch(settings: Settings, reason: str) -> None:
    settings.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    settings.kill_switch_file.write_text(f"tripped: {reason}\n", encoding="utf-8")
    log.error("kill_switch_tripped", reason=reason)


def entry_spread_ok(spread_points: float, settings: Settings) -> GateResult:
    """Block a NEW entry when the live spread blows out (rollover / news / illiquid)."""
    if not settings.spread_guard_enabled:
        return GateResult(True, "spread guard disabled")
    if spread_points > settings.max_entry_spread_points:
        return GateResult(
            False,
            f"spread {spread_points:.0f}pts > cap {settings.max_entry_spread_points:.0f}pts",
        )
    return GateResult(True)
