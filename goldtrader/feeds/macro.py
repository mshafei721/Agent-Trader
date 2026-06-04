"""FRED macro context for the gold bias (V7 P1b).

Pulls the two highest-correlation gold drivers from the St. Louis Fed (FRED):
  - DFII10  : 10-Year Treasury Inflation-Indexed (TIPS) real yield, % — direct real yield
  - DTWEXBGS: Nominal Broad U.S. Dollar Index — the dollar

Requires a FREE FRED API key (config `fred_api_key`). DEGRADES GRACEFULLY: with no key or on
any network/parse failure it returns None, and the bias simply runs without macro context
(charts still lead; nothing blocks). Cached to disk so a refresh isn't paid every call.

Pure helper `direction()` is unit-tested without network.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.macro")

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
_CACHE_TTL_S = 6 * 3600  # macro moves slowly; one fetch per few hours is plenty
_LOOKBACK = 20  # ~1 trading month for the direction read


@dataclass
class MacroSnapshot:
    real_yield: float | None
    real_yield_dir: str
    dollar: float | None
    dollar_dir: str
    ts: str

    def summary(self) -> str:
        parts = []
        if self.real_yield is not None:
            parts.append(f"10y real yield {self.real_yield:.2f}% ({self.real_yield_dir})")
        if self.dollar is not None:
            parts.append(f"broad USD index {self.dollar:.1f} ({self.dollar_dir})")
        return "; ".join(parts) if parts else ""


def direction(latest: float, past: float, eps: float = 1e-9) -> str:
    """'rising' / 'falling' / 'flat' for a value vs an earlier value."""
    if latest > past + eps:
        return "rising"
    if latest < past - eps:
        return "falling"
    return "flat"


class MacroProvider:
    def __init__(self, settings: Settings):
        self.s = settings
        self._snap: MacroSnapshot | None = None
        self._fetched_at: float = 0.0
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.s.macro_cache_file.read_text(encoding="utf-8"))
            snap = data.get("snapshot")
            if snap:
                self._snap = MacroSnapshot(**snap)
            self._fetched_at = float(data.get("fetched_at", 0.0))
        except (OSError, ValueError, TypeError):
            self._snap, self._fetched_at = None, 0.0

    def _save_cache(self) -> None:
        try:
            path = self.s.macro_cache_file
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "fetched_at": self._fetched_at,
                "snapshot": self._snap.__dict__ if self._snap else None,
            }), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("macro_cache_write_failed", error=str(exc))

    def _fetch_series(self, series_id: str, key: str) -> list[float]:
        """Most-recent-first numeric observations (skips FRED's '.' missing marker)."""
        params = {"series_id": series_id, "api_key": key, "file_type": "json",
                  "sort_order": "desc", "limit": str(_LOOKBACK + 5)}
        url = FRED_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "goldtrader/1.0"})
        with urllib.request.urlopen(req, timeout=12.0) as resp:  # noqa: S310 (trusted URL)
            data = json.loads(resp.read().decode("utf-8"))
        out: list[float] = []
        for obs in data.get("observations", []):
            v = obs.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out.append(float(v))
            except ValueError:
                continue
        return out

    def _refresh(self) -> bool:
        key = self.s.fred_api_key.get_secret_value() if self.s.fred_api_key else None
        if not key:
            return False  # no key -> degrade silently (bias runs without macro)
        try:
            ry = self._fetch_series("DFII10", key)
            dx = self._fetch_series("DTWEXBGS", key)
        except Exception as exc:  # noqa: BLE001 — network/parse; keep prior cache
            log.warning("macro_refresh_failed", error=str(exc))
            return False
        ry_val = ry[0] if ry else None
        dx_val = dx[0] if dx else None
        self._snap = MacroSnapshot(
            real_yield=ry_val,
            real_yield_dir=direction(ry[0], ry[min(_LOOKBACK, len(ry) - 1)]) if len(ry) > 1 else "flat",
            dollar=dx_val,
            dollar_dir=direction(dx[0], dx[min(_LOOKBACK, len(dx) - 1)]) if len(dx) > 1 else "flat",
            ts=datetime.now(timezone.utc).isoformat(),
        )
        self._fetched_at = time.time()
        self._save_cache()
        log.info("macro_refreshed", real_yield=ry_val, dollar=dx_val)
        return True

    def snapshot(self) -> MacroSnapshot | None:
        if (time.time() - self._fetched_at) > _CACHE_TTL_S:
            self._refresh()
        return self._snap
