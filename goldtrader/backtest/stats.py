"""Pure performance statistics for a stream of trade R-multiples (V7 P2.1).

All functions are deterministic given a seed and take plain lists of floats, so they
unit-test without a broker, network, or the backtest engine. R-multiple = realized PnL
in units of the initial risk (the stop distance); +1 means a winner that made 1x risk.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class PerfStats:
    trades: int
    win_rate: float
    win_rate_ci: tuple[float, float]
    expectancy: float            # mean R per trade
    expectancy_ci: tuple[float, float]
    profit_factor: float
    total_r: float
    max_drawdown_r: float
    max_consecutive_losses: int
    sharpe: float                # per-trade reward/volatility
    sortino: float               # per-trade reward/downside-volatility
    calmar: float                # total R / max drawdown R
    mc_drawdown_p50: float
    mc_drawdown_p95: float


def win_rate(rs: list[float]) -> float:
    if not rs:
        return 0.0
    return sum(1 for r in rs if r > 0) / len(rs)


def expectancy(rs: list[float]) -> float:
    return sum(rs) / len(rs) if rs else 0.0


def profit_factor(rs: list[float]) -> float:
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    if gross_loss <= 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def max_drawdown_r(rs: list[float]) -> float:
    """Largest peak-to-trough drop of the cumulative-R equity curve (in R units)."""
    peak = cum = 0.0
    mdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def max_consecutive_losses(rs: list[float]) -> int:
    streak = worst = 0
    for r in rs:
        if r < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def sharpe(rs: list[float]) -> float:
    """Per-trade Sharpe = mean(R) / sample-stdev(R). Dimensionless 'reward per unit of
    wobble', comparable across strategies. 0.0 when < 2 trades or the series is flat."""
    n = len(rs)
    if n < 2:
        return 0.0
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / (n - 1)
    sd = math.sqrt(var)
    return mean / sd if sd > 0 else 0.0


def sortino(rs: list[float]) -> float:
    """Per-trade Sortino = mean(R) / downside deviation (MAR=0). Penalizes only losing
    variance. inf when there are no losers and the mean is positive; 0.0 when empty/flat."""
    n = len(rs)
    if n == 0:
        return 0.0
    mean = sum(rs) / n
    downside = math.sqrt(sum(min(0.0, r) ** 2 for r in rs) / n)
    if downside <= 0:
        return float("inf") if mean > 0 else 0.0
    return mean / downside


def calmar(rs: list[float]) -> float:
    """Calmar = total return (R) / max drawdown (R). Reward per unit of worst pain.
    inf when there's profit and no drawdown; 0.0 otherwise."""
    total = sum(rs)
    mdd = max_drawdown_r(rs)
    if mdd <= 0:
        return float("inf") if total > 0 else 0.0
    return total / mdd


def deflated_sharpe(rs: list[float], n_trials: int) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014): the probability that the
    TRUE per-trade Sharpe is > 0 after correcting for selection across `n_trials`
    tried configurations and for non-normal returns. > 0.95 = statistically credible.

    Uses the candidate's own SR standard error as the proxy for the variance of trial
    SRs (the standard single-strategy practice when per-trial SRs weren't recorded).
    """
    from statistics import NormalDist

    n = len(rs)
    sr = sharpe(rs)
    if n < 10 or n_trials < 1:
        return 0.0
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1))
    if sd <= 0:
        return 0.0
    skew = sum(((r - mean) / sd) ** 3 for r in rs) / n
    kurt = sum(((r - mean) / sd) ** 4 for r in rs) / n  # Pearson (normal = 3)
    var_sr = (1 - skew * sr + (kurt - 1) / 4.0 * sr * sr) / (n - 1)
    if var_sr <= 0:
        return 0.0
    se_sr = math.sqrt(var_sr)
    nd = NormalDist()
    if n_trials > 1:
        gamma = 0.5772156649015329  # Euler-Mascheroni
        sr0 = se_sr * ((1 - gamma) * nd.inv_cdf(1 - 1.0 / n_trials)
                       + gamma * nd.inv_cdf(1 - 1.0 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    return nd.cdf((sr - sr0) / se_sr)


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a win-rate proportion (better than normal at small n)."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(rs: list[float], n_resamples: int, seed: int,
                 lo: float = 2.5, hi: float = 97.5) -> tuple[float, float]:
    """Percentile bootstrap CI for mean R. Returns (point, point) when too few trades."""
    if len(rs) < 5:
        m = expectancy(rs)
        return (m, m)
    rng = random.Random(seed)
    k = len(rs)
    means = []
    for _ in range(n_resamples):
        sample = [rs[rng.randrange(k)] for _ in range(k)]
        means.append(sum(sample) / k)
    means.sort()
    return (_percentile(means, lo), _percentile(means, hi))


def monte_carlo_drawdown(rs: list[float], n_resamples: int, seed: int) -> tuple[float, float]:
    """Resample the trade order (bootstrap) and return (p50, p95) of max drawdown in R.

    Answers 'how bad could the drawdown plausibly get from this edge?' — the input to
    honest position sizing and a non-technical owner's worst-case framing.
    """
    if len(rs) < 5:
        return (max_drawdown_r(rs), max_drawdown_r(rs))
    rng = random.Random(seed + 1)
    k = len(rs)
    dds = []
    for _ in range(n_resamples):
        sample = [rs[rng.randrange(k)] for _ in range(k)]
        dds.append(max_drawdown_r(sample))
    dds.sort()
    return (_percentile(dds, 50.0), _percentile(dds, 95.0))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute(rs: list[float], *, seed: int = 42, n_resamples: int = 2000) -> PerfStats:
    wins = sum(1 for r in rs if r > 0)
    return PerfStats(
        trades=len(rs),
        win_rate=win_rate(rs),
        win_rate_ci=wilson_ci(wins, len(rs)),
        expectancy=expectancy(rs),
        expectancy_ci=bootstrap_ci(rs, n_resamples, seed),
        profit_factor=profit_factor(rs),
        total_r=sum(rs),
        max_drawdown_r=max_drawdown_r(rs),
        max_consecutive_losses=max_consecutive_losses(rs),
        sharpe=sharpe(rs),
        sortino=sortino(rs),
        calmar=calmar(rs),
        mc_drawdown_p50=monte_carlo_drawdown(rs, n_resamples, seed)[0],
        mc_drawdown_p95=monte_carlo_drawdown(rs, n_resamples, seed)[1],
    )
