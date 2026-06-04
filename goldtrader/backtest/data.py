"""Fetch + cache MT5 history for the backtest (V7 P2.1).

`fetch_and_cache` pulls the timeframes the live engine uses (filter/setup/trigger + H1 for
the volatility gate) via the existing MT5Client and pickles them under data/backtest/, plus
the symbol spec. `load_bars` reads them back so the replay runs fully offline and deterministic.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import MetaTrader5 as mt5  # type: ignore
import pandas as pd

from ..config import Settings
from ..logging_setup import get_logger
from ..strategy.technical import _tf
from ..types import SymbolSpec

log = get_logger("goldtrader.backtest.data")

TF_SECONDS = {
    mt5.TIMEFRAME_M5: 300, mt5.TIMEFRAME_M15: 900, mt5.TIMEFRAME_M30: 1800,
    mt5.TIMEFRAME_H1: 3600, mt5.TIMEFRAME_H4: 14400, mt5.TIMEFRAME_D1: 86400,
}


def needed_timeframes(s: Settings) -> set[int]:
    return {
        _tf(s.filter_timeframe), _tf(s.setup_timeframe), _tf(s.trigger_timeframe),
        _tf(s.stop_timeframe), mt5.TIMEFRAME_H1,
    }


def _bars_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    return df[["time", "open", "high", "low", "close"]].sort_values("time").reset_index(drop=True)


def fetch_and_cache(s: Settings) -> dict:
    """Connect to MT5, pull each needed timeframe, and cache to disk. Returns a summary."""
    from ..mt5.client import MT5Client

    client = MT5Client(s)
    client.connect()
    out_dir = s.backtest_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    trigger_tf = _tf(s.trigger_timeframe)
    span_s = s.backtest_bars * TF_SECONDS[trigger_tf]
    summary = {}
    for tf in needed_timeframes(s):
        # Pull enough bars of each timeframe to cover the trigger span + warmup buffer.
        count = min(s.backtest_bars, math.ceil(span_s / TF_SECONDS[tf]) + s.backtest_warmup_bars + 60)
        count = max(count, s.backtest_warmup_bars + 60)
        rates = client.get_rates(tf, count)
        df = _bars_to_df(rates)
        df.to_pickle(out_dir / f"bars_{tf}.pkl")
        summary[tf] = {"bars": len(df),
                       "from": int(df["time"].iloc[0]), "to": int(df["time"].iloc[-1])}
        log.info("backtest_bars_cached", tf=tf, bars=len(df))
    spec = client.spec
    (out_dir / "spec.json").write_text(json.dumps(spec.__dict__), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps({"symbol": client.symbol, "summary": summary}),
                                       encoding="utf-8")
    client.shutdown()
    return summary


def load_bars(s: Settings) -> dict[int, pd.DataFrame]:
    out_dir = s.backtest_dir
    bars: dict[int, pd.DataFrame] = {}
    for tf in needed_timeframes(s):
        p = out_dir / f"bars_{tf}.pkl"
        if not p.exists():
            raise FileNotFoundError(
                f"missing cached bars for tf={tf} ({p}). Run `backtest-fetch` first.")
        bars[tf] = pd.read_pickle(p)
    return bars


def load_spec(s: Settings) -> SymbolSpec:
    data = json.loads((s.backtest_dir / "spec.json").read_text(encoding="utf-8"))
    return SymbolSpec(**data)
