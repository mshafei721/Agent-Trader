"""Broker-truth reconciliation: aggregate MT5 deals into closed-position outcomes."""
from types import SimpleNamespace as D

from goldtrader.learning.reconcile import aggregate_closed_positions, close_iso

BOT = 770077


def _deal(pid, type_, entry, magic, profit=0.0, swap=0.0, commission=0.0, price=0.0, time=0):
    return D(position_id=pid, type=type_, entry=entry, magic=magic,
             profit=profit, swap=swap, commission=commission, price=price, time=time)


def test_includes_broker_close_deal_with_magic_zero():
    # The bug: SL/TP CLOSE deals carry magic 0, so the loss was dropped. Must be included.
    deals = [
        _deal(100, 1, 0, BOT, profit=0.0, time=1000),                  # open SELL (bot magic)
        _deal(100, 0, 1, 0, profit=-50.0, commission=-2.0, time=2000, price=2010.0),  # SL close, magic 0
    ]
    out = aggregate_closed_positions(deals, open_tickets=set(), bot_magic=BOT)
    assert len(out) == 1
    assert out[0]["position_id"] == 100
    assert out[0]["pnl"] == -52.0          # full realized P&L incl the magic-0 close + commission
    assert out[0]["close_time"] == 2000
    assert out[0]["exit_price"] == 2010.0


def test_excludes_non_bot_open_and_unclosed():
    deals = [
        _deal(1, 1, 0, 999, time=1), _deal(1, 0, 1, 0, profit=10, time=2),   # non-bot position
        _deal(2, 1, 0, BOT, time=1), _deal(2, 0, 1, 0, profit=5, time=2),    # bot but still open
        _deal(3, 1, 0, BOT, time=1),                                          # bot, no close yet
    ]
    out = aggregate_closed_positions(deals, open_tickets={2}, bot_magic=BOT)
    assert [o["position_id"] for o in out] == []


def test_sums_partial_then_full_close():
    deals = [
        _deal(7, 0, 0, BOT, profit=0.0, time=10),                    # open BUY
        _deal(7, 1, 1, 0, profit=20.0, time=20),                     # scale-out (partial close)
        _deal(7, 1, 1, 0, profit=15.0, time=30, price=2050.0),       # final close
    ]
    out = aggregate_closed_positions(deals, set(), BOT)
    assert len(out) == 1
    assert out[0]["pnl"] == 35.0
    assert out[0]["close_time"] == 30        # latest close
    assert out[0]["exit_price"] == 2050.0


def test_skips_balance_operations():
    deals = [_deal(0, 2, 0, 0, profit=100000.0, time=1)]  # type 2 = balance/deposit, not a trade
    assert aggregate_closed_positions(deals, set(), BOT) == []


def test_sorted_by_close_time():
    deals = [
        _deal(1, 1, 0, BOT, time=1), _deal(1, 0, 1, 0, profit=1, time=300),
        _deal(2, 1, 0, BOT, time=1), _deal(2, 0, 1, 0, profit=1, time=100),
    ]
    out = aggregate_closed_positions(deals, set(), BOT)
    assert [o["position_id"] for o in out] == [2, 1]   # earlier close first


def test_close_iso():
    assert close_iso(1700000000).startswith("2023-11-14")
