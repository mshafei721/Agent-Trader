"""Pure exit/trailing helpers (no MT5 / no I/O) so they are unit-testable.

- chandelier_stop: the Chandelier Exit trailing level (swing extreme -/+ ATR x mult).
- should_cut_loss: early loss-cut decision (down >= cut_at_r AND fast trend opposes).
"""
from __future__ import annotations

from ..types import Action


def should_bias_exit(pos_side: "Action", bias, threshold: float) -> bool:
    """Close/tighten an open position when the (cached) LLM bias OPPOSES it at
    conviction >= threshold. Flat/agreeing/weak bias -> no exit.

    pos_side: the open position's direction (BUY=long, SELL=short).
    """
    if bias is None or bias.is_flat():
        return False
    opposite = Action.SELL if pos_side == Action.BUY else Action.BUY
    return bias.direction == opposite and bias.conviction >= threshold


def chandelier_stop(is_buy: bool, swing_high: float, swing_low: float,
                    atr: float, mult: float) -> float:
    """Chandelier Exit trailing stop level.

    Long:  highest_high - ATR*mult  (stop trails BELOW price)
    Short: lowest_low   + ATR*mult  (stop trails ABOVE price)
    """
    if is_buy:
        return swing_high - atr * mult
    return swing_low + atr * mult


def should_cut_loss(r_now: float, fast_trend: int, is_buy: bool, cut_at_r: float) -> bool:
    """Cut a losing trade early when it is down >= cut_at_r AND the fast-timeframe
    trend now opposes the position (a price dip alone is not enough).

    fast_trend: +1 up, -1 down, 0 undetermined.
    """
    if r_now > -cut_at_r:
        return False
    if fast_trend == 0:
        return False
    return (is_buy and fast_trend < 0) or (not is_buy and fast_trend > 0)


def ratchet_stop(is_buy: bool, candidate_sl: float, current_sl: float, eps: float = 0.0):
    """Return (new_sl, improved): move the stop only in the favorable direction.

    Long: stop may only rise; Short: stop may only fall.
    """
    if is_buy:
        base = current_sl if current_sl else 0.0
        new_sl = max(candidate_sl, base)
        return new_sl, new_sl > base + eps
    base = current_sl if current_sl else float("inf")
    new_sl = min(candidate_sl, base)
    return new_sl, new_sl < base - eps
