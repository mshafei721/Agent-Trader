"""CFTC Commitments of Traders (COT) provider for the positioning-extreme gate (V7 P1a).

Source: CFTC public Socrata API (free, no key) — legacy futures report (6dca-aqww).
We pull NON-COMMERCIAL long/short for COMEX gold (code 088691) — large speculators, the
standard gold "smart money" proxy — compute the weekly NET position and a 52-week z-score,
and cache it to disk (COT is weekly, released Fridays). The gate uses the z-score to avoid
chasing a crowded extreme into exhaustion — the top failure mode of momentum trend-followers.

Pure functions (`net_series`, `zscore`, `cot_gate`) take plain numbers so they unit-test
without network. The gate FAILS OPEN: missing COT data never blocks trading (it is a quality
filter, not a safety guard).
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
from ..types import Action

log = get_logger("goldtrader.cot")

CFTC_DATASET_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_MIN_WEEKS = 12  # need at least this much history for a meaningful z-score


@dataclass
class CotSnapshot:
    report_date: str
    net: float
    zscore: float
    weeks: int


def net_series(rows: list[dict]) -> list[float]:
    """Non-commercial NET (long-short) per week, most-recent first, deduped by report date.

    Dedup guards against the dataset carrying more than one report row per week for the
    same contract (which would distort the z-score).
    """
    out: list[float] = []
    seen: set[str] = set()
    for r in rows:
        date = str(r.get("report_date_as_yyyy_mm_dd", ""))[:10]
        if date and date in seen:
            continue
        try:
            lng = float(r["noncomm_positions_long_all"])
            sht = float(r["noncomm_positions_short_all"])
        except (KeyError, TypeError, ValueError):
            continue
        if date:
            seen.add(date)
        out.append(lng - sht)
    return out


def zscore(latest: float, history: list[float]) -> float | None:
    """Z-score of `latest` vs `history` (population std). None if history is too small/flat."""
    n = len(history)
    if n < 2:
        return None
    mean = sum(history) / n
    var = sum((x - mean) ** 2 for x in history) / n
    if var <= 0:
        return None
    return (latest - mean) / (var ** 0.5)


def cot_gate(side: Action, z: float | None, extreme_z: float) -> tuple[bool, str]:
    """Block a NEW entry that chases a crowded managed-money extreme. FAILS OPEN."""
    if z is None:
        return True, "no cot data"
    if side == Action.BUY and z > extreme_z:
        return False, f"COT crowded long (z={z:.2f} > {extreme_z})"
    if side == Action.SELL and z < -extreme_z:
        return False, f"COT crowded short (z={z:.2f} < -{extreme_z})"
    return True, f"cot ok (z={z:.2f})"


class CotProvider:
    """Fetches + caches the COMEX-gold COT snapshot; degrades gracefully to None."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._snapshot: CotSnapshot | None = None
        self._fetched_at: float = 0.0
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.s.cot_cache_file.read_text(encoding="utf-8"))
            snap = data.get("snapshot")
            if snap:
                self._snapshot = CotSnapshot(**snap)
            self._fetched_at = float(data.get("fetched_at", 0.0))
        except (OSError, ValueError, TypeError):
            self._snapshot, self._fetched_at = None, 0.0

    def _save_cache(self) -> None:
        try:
            path = self.s.cot_cache_file
            path.parent.mkdir(parents=True, exist_ok=True)
            snap = self._snapshot.__dict__ if self._snapshot else None
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"fetched_at": self._fetched_at, "snapshot": snap}), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("cot_cache_write_failed", error=str(exc))

    def _refresh(self) -> bool:
        params = {
            "$where": f"cftc_contract_market_code='{self.s.cot_contract_code}'",
            "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": "60",
        }
        url = CFTC_DATASET_URL + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "goldtrader/1.0"})
            with urllib.request.urlopen(req, timeout=12.0) as resp:  # noqa: S310 (trusted URL)
                rows = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — network/parse; keep prior cache, degrade
            log.warning("cot_refresh_failed", error=str(exc))
            return False
        nets = net_series(rows)
        if len(nets) < _MIN_WEEKS:
            log.warning("cot_insufficient_history", weeks=len(nets))
            self._fetched_at = time.time()  # don't hammer the API; retry next interval
            self._save_cache()
            return False
        latest, history = nets[0], nets[1:53]
        z = zscore(latest, history)
        report_date = str(rows[0].get("report_date_as_yyyy_mm_dd", ""))[:10]
        self._snapshot = CotSnapshot(report_date=report_date, net=latest,
                                     zscore=(z if z is not None else 0.0), weeks=len(nets))
        self._fetched_at = time.time()
        self._save_cache()
        log.info("cot_refreshed", report_date=report_date, net=round(latest), z=round(self._snapshot.zscore, 2))
        return True

    def snapshot(self, now: datetime | None = None) -> CotSnapshot | None:
        """Return the latest COT snapshot, refreshing on the configured cadence."""
        now_ts = (now or datetime.now(timezone.utc)).timestamp()
        if (now_ts - self._fetched_at) > self.s.cot_refresh_hours * 3600:
            self._refresh()
        return self._snapshot

    def gate(self, side: Action) -> tuple[bool, str]:
        """Convenience: evaluate the positioning gate for `side` using the current snapshot."""
        if not self.s.cot_gate_enabled:
            return True, "cot gate disabled"
        snap = self.snapshot()
        z = snap.zscore if snap else None
        return cot_gate(side, z, self.s.cot_extreme_z)
