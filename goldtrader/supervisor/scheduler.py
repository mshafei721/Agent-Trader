"""Cadence + market-open logic.

Market-open is judged from LIVE truth (tick freshness) plus a weekend guard,
rather than hardcoded session hours which vary by broker.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import Settings


def seconds_to_next_bar(interval_minutes: int, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    period = interval_minutes * 60
    epoch = now.timestamp()
    return period - (epoch % period)


def is_weekend(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    # Gold (XAUUSD) is closed roughly Fri ~21:00 UTC to Sun ~22:00 UTC.
    wd = now.weekday()  # Mon=0 .. Sun=6
    if wd == 5:  # Saturday
        return True
    if wd == 6 and now.hour < 22:  # Sunday before reopen
        return True
    if wd == 4 and now.hour >= 21:  # Friday after close
        return True
    return False


def market_open(settings: Settings, tick_age_seconds: float, now: datetime | None = None) -> tuple[bool, str]:
    if settings.skip_weekends and is_weekend(now):
        return False, "weekend"
    if tick_age_seconds > settings.tick_stale_minutes * 60:
        return False, f"stale tick ({tick_age_seconds:.0f}s old)"
    return True, "open"
