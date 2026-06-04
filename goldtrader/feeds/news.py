"""Gold-relevant news digest for the bias context (V7 P1b).

Replaces the broken Reddit/StockTwits/Yahoo sentiment stack with reliable, free RSS feeds
(FXStreet + MarketWatch by default — both verified to parse). Headlines are filtered for
gold relevance, deduped, and turned into a short digest injected into the bias prompt.

Stdlib only (urllib + ElementTree). DEGRADES GRACEFULLY: if every feed fails, returns "".
Pure helpers (`gold_relevant`, `build_digest`) unit-test without network.
"""
from __future__ import annotations

import json
import time
import urllib.request
import xml.etree.ElementTree as ET

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.news")

_CACHE_TTL_S = 3600  # one fetch per hour is plenty for a 4h bias cadence
_GOLD_TERMS = (
    "gold", "xau", "bullion", "precious metal", "fed", "fomc", "rate cut", "rate hike",
    "inflation", "cpi", "yield", "real yield", "dollar", "dxy", "safe haven", "safe-haven",
    "treasury", "geopolit", "central bank", "powell", "nonfarm", "payroll", "pce",
)


def gold_relevant(title: str) -> bool:
    t = title.lower()
    return any(term in t for term in _GOLD_TERMS)


def build_digest(titles: list[str], limit: int) -> str:
    """Bullet digest of the first `limit` (already gold-filtered) headlines."""
    picked = titles[:limit]
    if not picked:
        return ""
    return "\n".join(f"- {t}" for t in picked)


class NewsProvider:
    def __init__(self, settings: Settings):
        self.s = settings
        self._titles: list[str] = []
        self._fetched_at: float = 0.0
        self._load_cache()

    def _urls(self) -> list[str]:
        return [u.strip() for u in self.s.news_rss_urls.split(",") if u.strip()]

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.s.news_cache_file.read_text(encoding="utf-8"))
            self._titles = data.get("titles", [])
            self._fetched_at = float(data.get("fetched_at", 0.0))
        except (OSError, ValueError, TypeError):
            self._titles, self._fetched_at = [], 0.0

    def _save_cache(self) -> None:
        try:
            path = self.s.news_cache_file
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"fetched_at": self._fetched_at, "titles": self._titles}),
                           encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("news_cache_write_failed", error=str(exc))

    def _fetch_feed(self, url: str) -> list[str]:
        req = urllib.request.Request(url, headers={"User-Agent": "goldtrader/1.0"})
        with urllib.request.urlopen(req, timeout=12.0) as resp:  # noqa: S310 (configurable RSS)
            root = ET.fromstring(resp.read())
        out: list[str] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if title:
                out.append(title)
        return out

    def _refresh(self) -> bool:
        seen: set[str] = set()
        gold: list[str] = []
        any_ok = False
        for url in self._urls():
            try:
                titles = self._fetch_feed(url)
                any_ok = True
            except Exception as exc:  # noqa: BLE001 — try the next feed
                log.warning("news_feed_failed", url=url, error=str(exc))
                continue
            for t in titles:
                if t not in seen and gold_relevant(t):
                    seen.add(t)
                    gold.append(t)
        if not any_ok:
            return False
        self._titles = gold[: max(self.s.news_digest_count * 2, 20)]
        self._fetched_at = time.time()
        self._save_cache()
        log.info("news_refreshed", gold_relevant=len(self._titles))
        return True

    def digest(self) -> str:
        if (time.time() - self._fetched_at) > _CACHE_TTL_S:
            self._refresh()
        return build_digest(self._titles, self.s.news_digest_count)
