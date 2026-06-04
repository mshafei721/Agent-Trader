"""Import years of XAU/USD history from Dukascopy for the backtest lab (V7 P2.2b).

The broker caps history at ~6000 M30 bars (~4 months) — far too little for a meaningful
walk-forward (the OOS sample was 22 trades). Dukascopy serves free multi-year gold data
(no key). We fetch M30 (BID), resample H1/H4 from it (internally consistent, no extra
downloads), and cache in the exact format the backtest reads — so `backtest`/`walkforward`
then run offline over years instead of months.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5  # type: ignore — used only for the timeframe int constants
import pandas as pd

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.backtest.dukascopy")

# Fallback XAUUSD spec if no broker spec.json is present (matches typical 2-digit gold).
_DEFAULT_SPEC = {
    "name": "XAUUSD", "digits": 2, "point": 0.01, "volume_min": 0.01, "volume_step": 0.01,
    "volume_max": 35.0, "contract_size": 100.0, "tick_value": 1.0, "tick_size": 0.01,
    "stops_level": 20, "freeze_level": 10, "filling_mode": 3,
}


def _to_bt(df: pd.DataFrame) -> pd.DataFrame:
    """OHLC DataFrame (tz-aware UTC DatetimeIndex) -> backtest frame: time(epoch s)+OHLC.

    Resolution-agnostic: the index may be ns or us precision, so derive seconds via a
    Timedelta division rather than the raw integer view.
    """
    out = df[["open", "high", "low", "close"]].copy()
    secs = (df.index - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    out.insert(0, "time", pd.Index(secs).astype("int64"))
    return out.sort_values("time").reset_index(drop=True)


def _resample(m30: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample M30 OHLC to a coarser timeframe (bar timestamp = left/open edge)."""
    agg = m30.resample(rule, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"))
    return agg.dropna()


def import_history(s: Settings, years: int = 5, end: datetime | None = None) -> dict:
    import dukascopy_python as d
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD

    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=int(365.25 * years))
    log.info("dukascopy_fetch_start", years=years, start=start.isoformat(), end=end.isoformat())
    m30 = d.fetch(INSTRUMENT_FX_METALS_XAU_USD, d.INTERVAL_MIN_30, d.OFFER_SIDE_BID, start, end)
    if m30 is None or len(m30) == 0:
        raise RuntimeError("Dukascopy returned no M30 data for the requested range")

    frames = {
        mt5.TIMEFRAME_M30: _to_bt(m30),
        mt5.TIMEFRAME_H1: _to_bt(_resample(m30, "1h")),
        mt5.TIMEFRAME_H4: _to_bt(_resample(m30, "4h")),
    }
    out_dir = s.backtest_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for tf, df in frames.items():
        df.to_pickle(out_dir / f"bars_{tf}.pkl")
        summary[tf] = {"bars": len(df), "from": int(df["time"].iloc[0]), "to": int(df["time"].iloc[-1])}
        log.info("dukascopy_tf_cached", tf=tf, bars=len(df))
    # Keep the real broker spec.json if a prior MT5 fetch wrote one; else write a default.
    spec_path = out_dir / "spec.json"
    if not spec_path.exists():
        spec_path.write_text(json.dumps(_DEFAULT_SPEC), encoding="utf-8")
    (out_dir / "meta.json").write_text(
        json.dumps({"symbol": "XAU/USD", "source": "dukascopy", "years": years, "summary": summary}),
        encoding="utf-8")
    return summary
