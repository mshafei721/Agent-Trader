from goldtrader.strategy.exits import (
    chandelier_stop,
    ratchet_stop,
    should_bias_exit,
    should_cut_loss,
)
from goldtrader.types import Action, Bias


def _bias(direction, conviction):
    return Bias(direction=direction, conviction=conviction, ts="2026-06-03T00:00:00+00:00")


# ---------------- should_bias_exit ----------------
def test_bias_exit_opposes_long():
    # long position, bias turns SELL with conviction >= threshold -> exit
    assert should_bias_exit(Action.BUY, _bias(Action.SELL, 0.7), 0.6) is True


def test_bias_exit_opposes_short():
    assert should_bias_exit(Action.SELL, _bias(Action.BUY, 0.7), 0.6) is True


def test_bias_exit_below_threshold():
    assert should_bias_exit(Action.BUY, _bias(Action.SELL, 0.5), 0.6) is False


def test_bias_exit_agreeing_or_flat():
    assert should_bias_exit(Action.BUY, _bias(Action.BUY, 0.9), 0.6) is False
    assert should_bias_exit(Action.SELL, _bias(Action.HOLD, 0.0), 0.6) is False
    assert should_bias_exit(Action.BUY, None, 0.6) is False


# ---------------- chandelier_stop ----------------
def test_chandelier_long():
    # long: highest_high - atr*mult
    assert chandelier_stop(True, swing_high=4500.0, swing_low=4400.0, atr=10.0, mult=1.5) == 4485.0


def test_chandelier_short():
    # short: lowest_low + atr*mult
    assert chandelier_stop(False, swing_high=4500.0, swing_low=4400.0, atr=10.0, mult=1.5) == 4415.0


# ---------------- should_cut_loss ----------------
def test_cut_when_loss_and_momentum_opposed_long():
    assert should_cut_loss(r_now=-0.6, fast_trend=-1, is_buy=True, cut_at_r=0.5) is True


def test_cut_when_loss_and_momentum_opposed_short():
    assert should_cut_loss(r_now=-0.6, fast_trend=1, is_buy=False, cut_at_r=0.5) is True


def test_no_cut_when_momentum_aligned():
    # losing long but trend still up -> hold
    assert should_cut_loss(r_now=-0.6, fast_trend=1, is_buy=True, cut_at_r=0.5) is False


def test_no_cut_when_loss_too_small():
    assert should_cut_loss(r_now=-0.3, fast_trend=-1, is_buy=True, cut_at_r=0.5) is False


def test_no_cut_when_trend_undetermined():
    assert should_cut_loss(r_now=-0.9, fast_trend=0, is_buy=True, cut_at_r=0.5) is False


# ---------------- ratchet_stop (never loosen) ----------------
def test_ratchet_long_tightens_up_only():
    assert ratchet_stop(True, candidate_sl=4490.0, current_sl=4485.0) == (4490.0, True)
    assert ratchet_stop(True, candidate_sl=4480.0, current_sl=4485.0) == (4485.0, False)


def test_ratchet_short_tightens_down_only():
    assert ratchet_stop(False, candidate_sl=4510.0, current_sl=4515.0) == (4510.0, True)
    assert ratchet_stop(False, candidate_sl=4520.0, current_sl=4515.0) == (4515.0, False)
