"""Seasonal / calendar edge experiments for gold (V7 profitability research).

The 5yr trend lab proved trend + every filter + mean-reversion have NO edge on gold M30.
A cited research pass concluded gold's best-evidenced structure is LOW-FREQUENCY and
calendar-based, not intraday — chiefly the Halloween/winter effect (Baur 2013, the one
peer-reviewed gold-specific anomaly) and the turn-of-month effect. This module tests those
two on ~22 years of DAILY gold closes, honestly:

  * No look-ahead: every entry/exit is a real close on a real trading day; decisions use
    only the calendar date and prior prices.
  * Costs subtracted as a return (spread + 2x slippage over the entry price), reusing the
    same cost constants as the M30 engine so results are comparable.
  * Out-of-sample by design: split() halves the history so a finding must survive on data
    it was never "chosen" on (the trend strategy's downfall was in-sample overfit).

Per-"trade" results are plain return fractions; they feed the generic backtest.stats
helpers (expectancy = mean return, Sharpe, bootstrap CI, etc.) exactly like R-multiples.
A separate daily cache (bars_daily.pkl) is used so the M30 lab cache is never touched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.backtest.seasonal")

DAILY_CACHE = "bars_daily.pkl"


@dataclass
class SeasonTrade:
    entry_date: str
    exit_date: str
    ret: float          # net return fraction (after costs)
    label: str


# ---------------- data ----------------
def load_daily(s: Settings, *, start: datetime | None = None, end: datetime | None = None,
               refresh: bool = False) -> pd.DataFrame:
    """Daily XAU/USD OHLC with a tz-aware UTC DatetimeIndex, cached to bars_daily.pkl.

    Fetches from Dukascopy (free, no key, ~2004->present) on first use or when refresh=True.
    """
    path = s.backtest_dir / DAILY_CACHE
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    import dukascopy_python as d  # lazy: only needed when fetching
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD

    start = start or datetime(2004, 1, 1, tzinfo=timezone.utc)
    end = end or datetime.now(timezone.utc)
    log.info("daily_fetch_start", start=start.isoformat(), end=end.isoformat())
    raw = d.fetch(INSTRUMENT_FX_METALS_XAU_USD, d.INTERVAL_DAY_1, d.OFFER_SIDE_BID, start, end)
    if raw is None or len(raw) == 0:
        raise RuntimeError("Dukascopy returned no daily data for the requested range")
    df = raw[["open", "high", "low", "close"]].copy()
    df.index = pd.DatetimeIndex(raw.index)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    log.info("daily_cached", bars=len(df), first=str(df.index[0]), last=str(df.index[-1]))
    return df


def _cost_return(s: Settings, price: float) -> float:
    """Round-trip cost as a return fraction at `price` (spread + 2x slippage), matching the
    M30 engine's cost constants so seasonal returns are cost-faithful and comparable."""
    if price <= 0:
        return 0.0
    points = s.backtest_cost_spread_points + 2 * s.backtest_cost_slippage_points
    return points * 0.01 / price  # gold point = 0.01 price units


# ---------------- Halloween / winter effect ----------------
def winter_summer_trades(daily: pd.DataFrame, s: Settings) -> tuple[list[SeasonTrade], list[SeasonTrade]]:
    """Long gold over the winter window (first trading day of Nov -> last trading day of
    the following Apr) vs the summer window (first trading day of May -> last of Oct).
    Returns (winter_trades, summer_trades), each a net-return series, one per year."""
    idx = daily.index
    close = daily["close"]
    years = range(int(idx[0].year), int(idx[-1].year) + 1)
    winter, summer = [], []
    for y in years:
        w = _window_trade(idx, close, s, pd.Timestamp(y, 11, 1, tz="UTC"),
                          pd.Timestamp(y + 1, 4, 30, tz="UTC"), f"winter {y}-{y+1}")
        if w:
            winter.append(w)
        su = _window_trade(idx, close, s, pd.Timestamp(y, 5, 1, tz="UTC"),
                          pd.Timestamp(y, 10, 31, tz="UTC"), f"summer {y}")
        if su:
            summer.append(su)
    return winter, summer


def _window_trade(idx, close, s: Settings, start_ts, end_ts, label: str) -> SeasonTrade | None:
    """One long trade: enter at the first trading day >= start_ts, exit at the last trading
    day <= end_ts. None if the window has no/one bar."""
    lo = idx.searchsorted(start_ts, side="left")
    hi = idx.searchsorted(end_ts, side="right") - 1
    if lo >= len(idx) or hi <= lo:
        return None
    p_in, p_out = float(close.iloc[lo]), float(close.iloc[hi])
    ret = p_out / p_in - 1.0 - _cost_return(s, p_in)
    return SeasonTrade(str(idx[lo].date()), str(idx[hi].date()), ret, label)


# ---------------- turn-of-month effect ----------------
def turn_of_month_trades(daily: pd.DataFrame, s: Settings,
                         pre: int = 1, post: int = 3) -> tuple[list[SeasonTrade], list[SeasonTrade]]:
    """TOM window [-pre, +post]: enter at the close `pre` trading days before month-end,
    exit at the close of the `post`-th trading day of the next month. Returns
    (tom_trades, rest_of_month_trades) for an apples-to-apples comparison."""
    idx = daily.index
    close = daily["close"]
    # ordered list of (year,month) -> positional indices of that month's trading days
    months: dict[tuple[int, int], list[int]] = {}
    for pos, ts in enumerate(idx):
        months.setdefault((ts.year, ts.month), []).append(pos)
    keys = sorted(months)
    tom, rest = [], []
    for k_prev, k_next in zip(keys, keys[1:]):
        days_prev, days_next = months[k_prev], months[k_next]
        if len(days_prev) < pre + 1 or len(days_next) < post:
            continue
        entry = days_prev[-(pre + 1)]          # `pre` days before the last trading day
        exit_ = days_next[post - 1]            # `post`-th trading day of the next month
        p_in, p_out = float(close.iloc[entry]), float(close.iloc[exit_])
        ret = p_out / p_in - 1.0 - _cost_return(s, p_in)
        tom.append(SeasonTrade(str(idx[entry].date()), str(idx[exit_].date()), ret,
                               f"tom {k_prev[0]}-{k_prev[1]:02d}"))
        # rest-of-month: 3rd trading day -> the entry day of the SAME month (the bulk middle)
        if len(days_prev) >= post + 1:
            r_in_pos, r_out_pos = days_prev[post - 1], days_prev[-(pre + 1)]
            if r_out_pos > r_in_pos:
                ri, ro = float(close.iloc[r_in_pos]), float(close.iloc[r_out_pos])
                rest.append(SeasonTrade(str(idx[r_in_pos].date()), str(idx[r_out_pos].date()),
                                        ro / ri - 1.0 - _cost_return(s, ri),
                                        f"mid {k_prev[0]}-{k_prev[1]:02d}"))
    return tom, rest


# ---------------- out-of-sample split ----------------
def split(trades: list[SeasonTrade], frac: float = 0.5) -> tuple[list[SeasonTrade], list[SeasonTrade]]:
    """Chronological in-sample / out-of-sample split (trades arrive in date order)."""
    cut = int(len(trades) * frac)
    return trades[:cut], trades[cut:]
