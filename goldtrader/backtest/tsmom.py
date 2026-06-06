"""Time-series (absolute) momentum on gold (V7 ensemble candidate #1).

Moskowitz-Ooi-Pedersen TSMOM: go long when the trailing N-month return is positive, else flat
(or short). Gold is a classic TSMOM constituent, but the honest PER-MARKET Sharpe is only
~0.1-0.4 (the famous ~1.3 is the 58-market portfolio). Its value vs buy-and-hold is getting
OUT of gold's bear phases (e.g. 2013-2015), NOT extra return in the bull — so the DECISIVE
control is TSMOM vs buy&hold on a RISK-ADJUSTED basis (Sharpe / drawdown), not raw return.

Monthly rebalance on month-end closes. No look-ahead: the signal at the close of month m-1
(trailing N-month return) earns month m's return. Round-trip cost charged only when the
position changes. Per-month net returns feed backtest.stats like the seasonal lab. The monthly
return series INCLUDES flat months (=0) so the Sharpe honestly reflects time out of market.
"""
from __future__ import annotations

import pandas as pd

from ..config import Settings
from .seasonal import SeasonTrade, _cost_return


def monthly_closes(daily: pd.DataFrame) -> pd.Series:
    """Month-end close series from the daily OHLC frame."""
    return daily["close"].resample("ME").last().dropna()


def tsmom_trades(daily: pd.DataFrame, s: Settings, *, lookback: int = 12,
                 allow_short: bool = False, label: str = "tsmom") -> list[SeasonTrade]:
    """One 'trade' per month: position from the trailing-`lookback`-month sign (decided at the
    prior month-end), earning the current month's return, net of turnover cost. Flat months are
    included as 0-return entries so the series reflects real time-in-market."""
    m = monthly_closes(daily)
    rets = m.pct_change()
    trades: list[SeasonTrade] = []
    prev_pos = 0
    for i in range(lookback + 1, len(m)):
        past = m.iloc[i - 1] / m.iloc[i - 1 - lookback] - 1.0   # info through month i-1 only
        sign = 1 if past > 0 else (-1 if past < 0 else 0)
        pos = sign if allow_short else max(0, sign)
        gross = pos * float(rets.iloc[i])
        cost = _cost_return(s, float(m.iloc[i - 1])) if pos != prev_pos else 0.0
        prev_pos = pos
        trades.append(SeasonTrade(str(m.index[i - 1].date()), str(m.index[i].date()),
                                  gross - cost, f"{label} {'L' if pos > 0 else ('S' if pos < 0 else '0')}"))
    return trades


def buy_hold_trades(daily: pd.DataFrame, s: Settings, lookback: int = 12) -> list[SeasonTrade]:
    """Always-long monthly returns over the SAME window as tsmom_trades — the reference the
    risk-adjusted comparison is judged against (the secular-bull baseline)."""
    m = monthly_closes(daily)
    rets = m.pct_change()
    trades: list[SeasonTrade] = []
    for i in range(lookback + 1, len(m)):
        trades.append(SeasonTrade(str(m.index[i - 1].date()), str(m.index[i].date()),
                                  float(rets.iloc[i]), "buyhold"))
    return trades
