"""Macro real-yield + dollar regime experiment for gold (V7 profitability research, candidate #3).

Research hypothesis: gold rises when the 10y real yield (FRED DFII10) and the broad dollar
(FRED DTWEXBGS) are BOTH falling, and falls when both rise — but the relationship is
regime-unstable (gold<->real-yield corr ran ~84% pre-2022, ~3% after). So we test it
regime-gated: only take the macro signal while the normal inverse regime is active.

Honesty rails (same as the trend + seasonal labs):
  * No look-ahead. Macro is reindexed onto gold trading days, ffilled, then SHIFTED ONE DAY
    (FRED publishes date t after the close), so the signal at close t uses macro through t-1.
  * Costs subtracted per trade (spread + 2x slippage as a return), same constants as the engine.
  * Out-of-sample by a fixed date split (the regime-break test: does it survive post-2020?).
  * A/B the regime gate: if the gated version doesn't beat the un-gated one OOS, the gate is
    just re-discovering trend and the signal should be killed.

Network (the FRED fetch) needs a free FRED API key in `fred_api_key`; the alignment, signal,
and trade logic are pure and unit-tested without network. Per-trade results are return
fractions feeding the generic backtest.stats helpers, exactly like the seasonal lab.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from ..config import Settings
from ..logging_setup import get_logger
from .seasonal import SeasonTrade, _cost_return

log = get_logger("goldtrader.backtest.macro_regime")

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"


# ---------------- data (network; needs a free FRED key) ----------------
def fetch_fred(s: Settings, series_id: str, *, refresh: bool = False) -> pd.Series:
    """Full-history daily FRED series as a float Series indexed by tz-naive date.

    Cached to data/backtest/fred_<id>.pkl. Raises if no fred_api_key is configured."""
    path = s.backtest_dir / f"fred_{series_id}.pkl"
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    if not s.fred_api_key:
        raise RuntimeError(
            "fetch_fred needs a free FRED API key in .env (fred_api_key). "
            "Get one at https://fred.stlouisfed.org/docs/api/api_key.html")
    params = {"series_id": series_id, "api_key": s.fred_api_key.get_secret_value(),
              "file_type": "json", "sort_order": "asc"}
    url = FRED_OBS_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "goldtrader/1.0"})
    with urllib.request.urlopen(req, timeout=30.0) as resp:  # noqa: S310 (trusted URL)
        data = json.loads(resp.read().decode("utf-8"))
    dates, vals = [], []
    for obs in data.get("observations", []):
        v = obs.get("value")
        if v in (None, ".", ""):
            continue
        try:
            vals.append(float(v))
            dates.append(pd.Timestamp(obs["date"]))
        except (ValueError, KeyError):
            continue
    ser = pd.Series(vals, index=pd.DatetimeIndex(dates), name=series_id).sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    ser.to_pickle(path)
    log.info("fred_cached", series=series_id, n=len(ser),
             first=str(ser.index[0].date()), last=str(ser.index[-1].date()))
    return ser


# ---------------- alignment (pure, no look-ahead) ----------------
def align_macro(daily: pd.DataFrame, real: pd.Series, usd: pd.Series) -> pd.DataFrame:
    """Gold close + macro reindexed to gold trading days, ffilled, and lagged ONE day so the
    value usable at close t reflects FRED data through t-1 (no look-ahead). Returns a frame
    with columns close, real, usd, gold_ret on the gold DatetimeIndex (NaN warmup dropped)."""
    gold_idx = daily.index
    # FRED series are tz-naive; gold index is tz-aware UTC. Compare on date only.
    r = real.copy(); r.index = pd.DatetimeIndex(r.index).tz_localize("UTC")
    u = usd.copy(); u.index = pd.DatetimeIndex(u.index).tz_localize("UTC")
    out = pd.DataFrame(index=gold_idx)
    out["close"] = daily["close"].to_numpy()
    out["real"] = r.reindex(gold_idx, method="ffill").shift(1)
    out["usd"] = u.reindex(gold_idx, method="ffill").shift(1)
    out["gold_ret"] = out["close"].pct_change()
    return out


def regime_position(df: pd.DataFrame, *, lookback: int = 20, regime_window: int = 120,
                    regime_thr: float = -0.3, use_regime: bool = True) -> pd.Series:
    """Daily target position in {-1,0,+1}, decided causally at each close.

    +1 (long) when both real-yield and dollar fell over `lookback` days; -1 when both rose;
    0 when mixed. When use_regime, zeroed unless trailing-`regime_window` corr(gold_ret, Δreal)
    is below regime_thr (the normal inverse regime is active)."""
    d_real = df["real"] - df["real"].shift(lookback)
    d_usd = df["usd"] - df["usd"].shift(lookback)
    pos = pd.Series(0, index=df.index, dtype=int)
    pos[(d_real < 0) & (d_usd < 0)] = 1
    pos[(d_real > 0) & (d_usd > 0)] = -1
    if use_regime:
        corr = df["gold_ret"].rolling(regime_window).corr(d_real)
        pos = pos.where(corr < regime_thr, 0)
    return pos.fillna(0).astype(int)


def regime_trades(df: pd.DataFrame, pos: pd.Series, s: Settings, *, hold: int = 10,
                  label: str = "macro") -> list[SeasonTrade]:
    """Single-position-at-a-time trades from the daily target position: enter at the close
    when flat and pos != 0, exit after `hold` trading days or on a flip to the opposite sign.
    Net return = side * gold move - round-trip cost. No look-ahead (pos is causal)."""
    idx = df.index
    close = df["close"].to_numpy()
    p = pos.to_numpy()
    n = len(df)
    trades: list[SeasonTrade] = []
    i = 0
    while i < n - 1:
        side = p[i]
        if side == 0:
            i += 1
            continue
        entry_idx = i
        exit_idx = min(i + hold, n - 1)
        for j in range(i + 1, min(i + hold, n - 1) + 1):
            if p[j] == -side:   # opposite signal -> exit early
                exit_idx = j
                break
        p_in, p_out = float(close[entry_idx]), float(close[exit_idx])
        if p_in > 0:
            ret = side * (p_out / p_in - 1.0) - _cost_return(s, p_in)
            trades.append(SeasonTrade(str(idx[entry_idx].date()), str(idx[exit_idx].date()),
                                      ret, f"{label} {'L' if side > 0 else 'S'}"))
        i = exit_idx + 1
    return trades


def date_split(trades: list[SeasonTrade], cut: str = "2020-01-01") -> tuple[list, list]:
    """Split trades by entry date into (pre-cut IS, post-cut OOS) — the regime-break test."""
    pre = [t for t in trades if t.entry_date < cut]
    post = [t for t in trades if t.entry_date >= cut]
    return pre, post
