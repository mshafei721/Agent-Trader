"""Offline backtest / walk-forward validation lab (V7 Phase 2).

Replays the EXACT live TechnicalEngine + RiskManager over cached MT5 history (no live
connection, deterministic) and produces evidence — expectancy with confidence intervals,
win rate, profit factor, max drawdown, Monte-Carlo worst-loss — to turn "more profitable"
from a claim into a measured, out-of-sample-validatable result.
"""
