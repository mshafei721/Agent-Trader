from goldtrader.strategy.bias import bias_vetoes
from goldtrader.types import Action, Bias


def _bias(direction, conviction):
    return Bias(direction=direction, conviction=conviction, ts="2026-06-02T00:00:00+00:00")


def test_strong_opposite_vetoes():
    # charts want SELL, LLM strongly says BUY -> veto
    assert bias_vetoes(Action.SELL, _bias(Action.BUY, 0.85), 0.75) is True
    # charts want BUY, LLM strongly says SELL -> veto
    assert bias_vetoes(Action.BUY, _bias(Action.SELL, 0.85), 0.75) is True


def test_mild_opposite_does_not_veto():
    # Overweight/Underweight map to 0.60 -> below 0.75 threshold -> no veto
    assert bias_vetoes(Action.SELL, _bias(Action.BUY, 0.60), 0.75) is False


def test_agreeing_bias_does_not_veto():
    assert bias_vetoes(Action.SELL, _bias(Action.SELL, 0.85), 0.75) is False
    assert bias_vetoes(Action.BUY, _bias(Action.BUY, 0.85), 0.75) is False


def test_flat_bias_does_not_veto():
    assert bias_vetoes(Action.SELL, _bias(Action.HOLD, 0.0), 0.75) is False


def test_none_bias_does_not_veto():
    assert bias_vetoes(Action.SELL, None, 0.75) is False
