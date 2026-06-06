"""Economic-calendar provider for the news-event blackout guard (V7 Phase 0).

Primary source: ForexFactory weekly JSON (free, no key). Cached to disk so the guard
survives restarts and to protect the free endpoint from over-polling.

CRITICAL — the blackout FAILS CLOSED. If no usable calendar is available, default ET
windows (08:30 / 14:00 America/New_York — the usual NFP/CPI/PCE/PPI and FOMC release
times) are blacked out on weekdays, so a fetch failure can never silently disable the
guard. The pure functions (`event_blackout`, `default_blackout`) take `now` + events
explicitly so they unit-test without network or MT5.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.calendar")

FF_THISWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_ET = ZoneInfo("America/New_York")
# Fail-closed default release times (ET): 08:30 = NFP/CPI/PCE/PPI, 14:00 = FOMC.
_DEFAULT_ET_TIMES = ((8, 30), (14, 0))
_USABLE_CACHE_AGE_S = 24 * 3600  # a calendar older than this is treated as unavailable


def _parse_ff_events(raw: list) -> list[dict]:
    """Keep only high-impact USD events; normalize to {title, dt_utc(iso)}."""
    out: list[dict] = []
    for e in raw:
        if (e.get("country") or e.get("currency")) != "USD":
            continue
        if (e.get("impact") or "").lower() != "high":
            continue
        ds = e.get("date") or e.get("datetime")
        if not ds:
            continue
        try:
            dt = datetime.fromisoformat(str(ds).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append({
            "title": e.get("title", "event"),
            "dt_utc": dt.astimezone(timezone.utc).isoformat(),
        })
    return out


def event_blackout(now_utc: datetime, events: list[dict], pre_min: int, post_min: int) -> tuple[bool, str]:
    """(blackout, reason) when `now` is within [event-pre, event+post] of any event."""
    for e in events:
        try:
            dt = datetime.fromisoformat(e["dt_utc"])
        except (KeyError, ValueError):
            continue
        if dt - timedelta(minutes=pre_min) <= now_utc <= dt + timedelta(minutes=post_min):
            return True, f"news blackout: {e.get('title', 'event')} at {dt.strftime('%H:%MZ')}"
    return False, "clear"


def default_blackout(now_utc: datetime, pre_min: int, post_min: int) -> tuple[bool, str]:
    """Fail-closed fallback: block around the usual ET release times on weekdays."""
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:  # weekend handled by the market-open guard
        return False, "weekend"
    for hh, mm in _DEFAULT_ET_TIMES:
        release = et.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if release - timedelta(minutes=pre_min) <= et <= release + timedelta(minutes=post_min):
            return True, f"default news blackout near {hh:02d}:{mm:02d} ET (calendar unavailable)"
    return False, "clear"


class EconomicCalendar:
    """Fetches + caches high-impact USD events; answers the blackout question, fail-closed."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._events: list[dict] = []
        self._fetched_at: float = 0.0
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.s.calendar_cache_file.read_text(encoding="utf-8"))
            self._events = data.get("events", [])
            self._fetched_at = float(data.get("fetched_at", 0.0))
        except (OSError, ValueError):
            self._events, self._fetched_at = [], 0.0

    def _save_cache(self) -> None:
        try:
            path = self.s.calendar_cache_file
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"fetched_at": self._fetched_at, "events": self._events}),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            log.warning("calendar_cache_write_failed", error=str(exc))

    def _refresh(self) -> bool:
        try:
            req = urllib.request.Request(FF_THISWEEK_URL, headers={"User-Agent": "goldtrader/1.0"})
            with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310 (trusted URL)
                raw = json.loads(resp.read().decode("utf-8"))
            self._events = _parse_ff_events(raw)
            self._fetched_at = time.time()
            self._save_cache()
            log.info("calendar_refreshed", high_impact_usd=len(self._events))
            return True
        except Exception as exc:  # noqa: BLE001 — network/parse failure -> fail closed downstream
            log.warning("calendar_refresh_failed", error=str(exc))
            return False

    def _have_valid_calendar(self) -> bool:
        # Fresh successful fetch (even with zero events = a genuinely quiet week).
        return self._fetched_at > 0 and (time.time() - self._fetched_at) <= _USABLE_CACHE_AGE_S

    def in_blackout(self, now_utc: datetime | None = None) -> tuple[bool, str]:
        """True if NEW entries should be suppressed right now. FAILS CLOSED."""
        now_utc = now_utc or datetime.now(timezone.utc)
        if (time.time() - self._fetched_at) > self.s.calendar_refresh_minutes * 60:
            self._refresh()
        pre, post = self.s.news_blackout_pre_minutes, self.s.news_blackout_post_minutes
        if self._have_valid_calendar():
            return event_blackout(now_utc, self._events, pre, post)
        return default_blackout(now_utc, pre, post)
