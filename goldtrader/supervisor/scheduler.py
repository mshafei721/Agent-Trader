"""Cadence + market-open logic.

Market-open is judged from LIVE truth (tick freshness) plus a weekend guard,
rather than hardcoded session hours which vary by broker.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def in_session(settings: Settings, now: datetime | None = None) -> bool:
    """True if NEW entries are allowed at this UTC hour (London-NY overlap by default).

    Management/exits are NOT gated by this — only new-entry opening. Supports a
    wrap-around window (start > end) for completeness.
    """
    if not settings.session_filter_enabled:
        return True
    now = now or datetime.now(timezone.utc)
    start, end = settings.trading_session_start_utc, settings.trading_session_end_utc
    h = now.hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end  # wrap-around (e.g. 22 -> 6)


def should_close_for_weekend(settings: Settings, now: datetime | None = None) -> bool:
    """True once Friday reaches the configured weekend-flat cutoff (UTC), so open
    positions are flattened before the close rather than gapped through on Monday."""
    if not settings.weekend_flat_enabled:
        return False
    now = now or datetime.now(timezone.utc)
    if now.weekday() != 4:  # Friday only
        return False
    cutoff = settings.weekend_flat_hour_utc * 60 + settings.weekend_flat_minute_utc
    return (now.hour * 60 + now.minute) >= cutoff


def in_monday_grace(settings: Settings, now: datetime | None = None) -> bool:
    """True during the grace window just after the Sunday ~22:00 UTC reopen, when
    spreads are wide and liquidity thin. Suppresses new entries only."""
    grace = settings.monday_grace_minutes
    if grace <= 0:
        return False
    now = now or datetime.now(timezone.utc)
    if now.weekday() == 6:  # Sunday
        reopen = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now < reopen:
            return False
    elif now.weekday() == 0:  # Monday spillover past midnight
        reopen = (now - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    else:
        return False
    return now < reopen + timedelta(minutes=grace)
