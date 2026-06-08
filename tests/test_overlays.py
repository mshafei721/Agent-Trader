"""Live sizing-overlay ensemble: trailing return, realized vol, TSMOM regime, vol-target, product."""
from datetime import datetime, timezone

from goldtrader.config import Settings
from goldtrader.strategy import overlays as ov
from goldtrader.strategy.overlays import rebalance_lots
from goldtrader.types import Action


# ---------------- seasonal-core rebalance sizing ----------------
def _rl(tgt, cur, base=0.10, *, step=0.01, vmin=0.01, mx=1.0, thr=0.10):
    return rebalance_lots(tgt, cur, base, vol_step=step, vol_min=vmin, max_lots=mx, threshold=thr)


def test_rebalance_buy_to_full():
    assert abs(_rl(1.0, 0.0) - 0.10) < 1e-9          # flat -> buy the full base


def test_rebalance_trim_partial():
    assert abs(_rl(0.5, 0.10) - (-0.05)) < 1e-9      # full now, target 0.5 -> trim 0.05


def test_rebalance_within_threshold_is_noop():
    assert _rl(0.55, 0.05) == 0.0                    # cur 0.5 vs 0.55 < 0.10 threshold


def test_rebalance_flat_trims_everything():
    assert abs(_rl(0.0, 0.10) - (-0.10)) < 1e-9


def test_rebalance_clamped_to_max_and_long_only():
    assert abs(_rl(1.0, 0.0, mx=0.05) - 0.05) < 1e-9  # capped at max_lots
    assert _rl(-0.5, 0.0) == 0.0                       # negative target -> long-only, no short

WINTER = datetime(2026, 1, 15, tzinfo=timezone.utc)
SUMMER = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _s(**kw) -> Settings:
    base = dict(seasonal_bias_enabled=True, seasonal_offseason_scaler=0.6,
                tsmom_regime_enabled=True, tsmom_regime_lookback_days=10, tsmom_downtrend_scaler=0.5,
                vol_target_enabled=True, vol_target_annual=0.18, vol_lookback_days=10)
    base.update(kw)
    return Settings(**base)


def _ret_closes(returns):
    c = [100.0]
    for r in returns:
        c.append(c[-1] * (1 + r))
    return c


def test_trailing_return():
    closes = [100.0 + i for i in range(20)]   # last=119, last-10=109
    assert abs(ov.trailing_return(closes, 10) - (119.0 / 109.0 - 1.0)) < 1e-12
    assert ov.trailing_return([1, 2, 3], 10) is None


def test_tsmom_regime_only_damps_longs_in_downtrend():
    s = _s()
    down = [200.0 - i for i in range(20)]
    up = [100.0 + i for i in range(20)]
    assert ov.tsmom_regime_scaler(Action.BUY, down, s)[0] == 0.5     # long in downtrend -> damped
    assert "downtrend" in ov.tsmom_regime_scaler(Action.BUY, down, s)[1]
    assert ov.tsmom_regime_scaler(Action.BUY, up, s)[0] == 1.0       # long in uptrend -> full
    assert ov.tsmom_regime_scaler(Action.SELL, down, s)[0] == 1.0    # shorts unaffected
    assert ov.tsmom_regime_scaler(Action.BUY, down, _s(tsmom_regime_enabled=False))[0] == 1.0
    assert ov.tsmom_regime_scaler(Action.BUY, [1, 2, 3], s)[0] == 1.0  # no data


def test_vol_target_damps_high_vol_only():
    s = _s()
    low = _ret_closes([0.001, -0.001] * 8)    # ~1.6% annual vol -> well under target -> full
    high = _ret_closes([0.03, -0.03] * 8)     # ~48% annual vol -> damped
    assert ov.vol_target_scaler(low, s)[0] == 1.0
    assert 0.0 < ov.vol_target_scaler(high, s)[0] < 1.0
    assert ov.vol_target_scaler(high, _s(vol_target_enabled=False))[0] == 1.0
    assert ov.vol_target_scaler([1, 2], s)[0] == 1.0   # too few bars


def test_ensemble_is_product_and_damp_only():
    s = _s()
    up_calm = [100.0 * (1.001 ** i) for i in range(20)]   # uptrend, ~zero variance
    down_calm = [200.0 * (0.999 ** i) for i in range(20)]  # downtrend, ~zero variance
    # winter + uptrend + calm -> nothing to damp
    total, bd = ov.ensemble_size_scaler(WINTER, Action.BUY, up_calm, s)
    assert total == 1.0 and bd["seasonal"] == 1.0 and bd["tsmom"] == 1.0 and bd["vol"] == 1.0
    # summer + downtrend long -> seasonal 0.6 * tsmom 0.5 * vol 1.0
    total2, bd2 = ov.ensemble_size_scaler(SUMMER, Action.BUY, down_calm, s)
    assert bd2["seasonal"] == 0.6 and bd2["tsmom"] == 0.5
    assert abs(total2 - 0.6 * 0.5 * bd2["vol"]) < 1e-9
    assert 0.0 < total2 <= 1.0   # damp-only: never exceeds 1
