"""Walk-forward fold-selection logic (V7 P2.2) — pure, no engine/network."""
from goldtrader.backtest.engine import Trade
from goldtrader.backtest.walkforward import DEFAULT_GRID, cfg_key, walk_forward


def _t(entry_time: int, r: float) -> Trade:
    return Trade(entry_time=entry_time, exit_time=entry_time + 1, side="BUY", entry=1.0,
                 sl=0.9, tp=1.2, exit_price=1.1, r_gross=r, r_net=r, reason="tp", bars_held=1)


def test_selects_train_best_config_each_fold():
    a = [_t(tt, 1.0) for tt in range(0, 1000, 25)]   # consistently good
    b = [_t(tt, -1.0) for tt in range(0, 1000, 25)]  # consistently bad
    wf = walk_forward({"A": a, "B": b}, n_folds=2, train_frac=0.5, min_train_trades=2, seed=1)
    chosen = [s["chosen"] for s in wf.selections if s.get("chosen")]
    assert chosen and all(c == "A" for c in chosen)
    assert wf.stats.expectancy == 1.0


def test_honest_about_overfit():
    # 'A' looks great in the training half but loses out-of-sample; it still wins TRAIN selection,
    # so its bad OOS trades are counted -> WF-OOS reflects the real loss (the anti-overfit point).
    a = [_t(tt, 2.0) for tt in range(0, 500, 20)] + [_t(tt, -2.0) for tt in range(500, 1000, 20)]
    b = [_t(tt, 0.1) for tt in range(0, 1000, 20)]
    wf = walk_forward({"A": a, "B": b}, n_folds=1, train_frac=0.5, min_train_trades=2, seed=1)
    assert wf.selections[0]["chosen"] == "A"
    assert wf.stats.expectancy < 0


def test_empty_grid_is_safe():
    wf = walk_forward({}, n_folds=2)
    assert wf.stats.trades == 0


def test_default_grid_unique_and_sized():
    keys = [cfg_key(c) for c in DEFAULT_GRID]
    assert len(keys) == len(set(keys)) == 12  # 3 tp x 2 adx x 2 cot
