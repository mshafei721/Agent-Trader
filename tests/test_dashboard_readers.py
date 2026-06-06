"""Dashboard transparency readers (V7 P3.1): equity/drawdown curve + safety lights.

The Settings path properties (journal_db, account_file, heartbeat_file, ...) late-bind
the module-level DATA_DIR / RUNTIME_DIR, so redirecting those globals points every
artifact at a tmp dir without touching the live data/ folder.
"""
import json
import time
from datetime import datetime, timezone

import goldtrader.config as cfg
from goldtrader.dashboard import readers
from goldtrader.learning.journal import Journal


def _settings(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(cfg, "LOGS_DIR", tmp_path / "logs")
    return cfg.Settings(max_daily_loss_pct=2.0, **overrides)


def _record(j: Journal, ticket: int, pnl: float, r: float, close_ts: str) -> None:
    j.record_outcome(mt5_ticket=ticket, close_ts=close_ts, exit_price=0.0,
                     realized_pnl=pnl, r_multiple=r, close_reason="tp")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat() + "T12:00:00+00:00"


# ---------------- equity / drawdown ----------------
def test_read_equity_empty(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    Journal(s.journal_db)  # creates an empty schema
    eq = readers.read_equity(s)
    assert eq["available"] is True
    assert eq["trades"] == 0
    assert eq["curve"] == []


def test_read_equity_curve_and_drawdown(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    j = Journal(s.journal_db)
    # cum pnl: 10, 6, 12, -8  | peak: 10,10,12,12 | dd: 0,4,0,20 -> maxDD 20
    for i, (pnl, r) in enumerate([(10.0, 1.0), (-4.0, -0.4), (6.0, 0.6), (-20.0, -2.0)]):
        _record(j, 1000 + i, pnl, r, _today())
    eq = readers.read_equity(s)
    assert eq["trades"] == 4
    assert eq["net_pnl"] == -8.0
    assert eq["net_r"] == -0.8
    assert eq["max_drawdown_pnl"] == 20.0
    assert eq["current_drawdown_pnl"] == 20.0  # peak 12 - last (-8)
    # curve carries a leading baseline point at 0, then one per trade
    assert len(eq["curve"]) == 5
    assert eq["curve"][0] == {"i": 0, "pnl": 0.0, "r": 0.0, "dd": 0.0}
    assert eq["curve"][-1]["pnl"] == -8.0
    assert eq["curve"][-1]["dd"] == 20.0


def test_read_equity_downsamples(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    j = Journal(s.journal_db)
    for i in range(50):
        _record(j, 2000 + i, 1.0, 0.1, _today())
    eq = readers.read_equity(s, cap=10)
    assert len(eq["curve"]) <= 10
    assert eq["curve"][-1]["pnl"] == eq["net_pnl"]  # last point preserved


def test_today_realized_pnl_filters_by_date(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    j = Journal(s.journal_db)
    _record(j, 3001, -5.0, -0.5, _today())
    _record(j, 3002, -7.0, -0.7, _today())
    _record(j, 3003, 99.0, 9.9, "2020-01-01T00:00:00+00:00")  # old day, excluded
    pnl, n = readers._today_realized_pnl(s)
    assert n == 2
    assert pnl == -12.0


# ---------------- safety lights ----------------
def _write_heartbeat(s, dry_run: bool, trade_mode=None):
    s.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    s.heartbeat_file.write_text(json.dumps(
        {"ts": time.time(), "pid": 123, "symbol": "XAUUSD",
         "dry_run": dry_run, "trade_mode": trade_mode}),
        encoding="utf-8")


def _write_account(s, balance: float):
    s.account_file.write_text(json.dumps(
        {"ts": time.time(), "symbol": "XAUUSD", "balance": balance,
         "equity": balance, "floating_pnl": 0.0, "positions": []}),
        encoding="utf-8")


def test_read_safety_all_green(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    Journal(s.journal_db)
    _write_heartbeat(s, dry_run=True)
    _write_account(s, balance=1000.0)
    out = readers.read_safety(s)
    assert out["bot"]["state"] == "green"
    assert out["mode"]["state"] == "green"   # dry-run, no orders sent
    assert out["mode"]["real_money"] is False
    assert out["loss_guard"]["state"] == "green"  # no loss today
    assert out["overall"] == "green"


def test_read_safety_real_money_is_red(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    Journal(s.journal_db)
    _write_heartbeat(s, dry_run=False, trade_mode=2)  # real account + live orders
    _write_account(s, balance=1000.0)
    out = readers.read_safety(s)
    assert out["mode"]["state"] == "red"
    assert out["mode"]["label"] == "LIVE money"
    assert out["mode"]["real_money"] is True
    assert out["overall"] == "red"


def test_read_safety_demo_live_orders_is_not_real_money(tmp_path, monkeypatch):
    # dry_run off but a DEMO account: real execution, but NOT real money -> must not cry "LIVE money".
    s = _settings(tmp_path, monkeypatch)
    Journal(s.journal_db)
    _write_heartbeat(s, dry_run=False, trade_mode=0)
    _write_account(s, balance=1000.0)
    out = readers.read_safety(s)
    assert out["mode"]["state"] == "green"
    assert out["mode"]["label"] == "Demo (live orders)"
    assert out["mode"]["real_money"] is False


def test_read_safety_live_orders_unknown_account_is_amber(tmp_path, monkeypatch):
    # dry_run off but the account type wasn't reported -> caution, not a false "demo" or "real".
    s = _settings(tmp_path, monkeypatch)
    Journal(s.journal_db)
    _write_heartbeat(s, dry_run=False, trade_mode=None)
    _write_account(s, balance=1000.0)
    out = readers.read_safety(s)
    assert out["mode"]["state"] == "amber"
    assert out["mode"]["real_money"] is None


def test_read_safety_loss_guard_breaches_cap(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    j = Journal(s.journal_db)
    _write_heartbeat(s, dry_run=True)
    _write_account(s, balance=1000.0)
    _record(j, 4001, -30.0, -3.0, _today())  # 3% loss vs 2% daily cap -> red
    out = readers.read_safety(s)
    assert out["loss_guard"]["state"] == "red"
    assert out["overall"] == "red"


def test_read_safety_kill_switch_halts_bot(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch)
    Journal(s.journal_db)
    _write_heartbeat(s, dry_run=True)
    _write_account(s, balance=1000.0)
    s.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    s.kill_switch_file.write_text("halt", encoding="utf-8")
    out = readers.read_safety(s)
    assert out["bot"]["state"] == "red"
    assert "kill" in out["bot"]["detail"].lower()
    assert out["overall"] == "red"
