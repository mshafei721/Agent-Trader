"""Diagnostics & manual control CLI.

Usage (from the venv):
  python -m goldtrader.cli connftest      # connect + demo guard + symbol specs
  python -m goldtrader.cli signal         # run ONE TradingAgents analysis, print signal
  python -m goldtrader.cli bias           # show/refresh the cached LLM bias (slow tier)
  python -m goldtrader.cli tech           # show multi-timeframe technical read (free, fast)
  python -m goldtrader.cli run-once       # run a single supervisor tick (respects DRY_RUN)
  python -m goldtrader.cli reflect        # run the reflection / self-heal report now
  python -m goldtrader.cli status         # show state + defensive mode + journal performance
  python -m goldtrader.cli kill           # create the kill switch
  python -m goldtrader.cli unkill         # remove the kill switch
"""
from __future__ import annotations

import sys

from .config import get_settings
from .logging_setup import setup_logging


def connftest():
    from .mt5.client import MT5Client

    s = get_settings()
    c = MT5Client(s)
    c.connect()
    spec = c.spec
    tick = c.get_tick()
    print(f"Connected. symbol={c.symbol} equity={c.equity():.2f}")
    print(f"  digits={spec.digits} point={spec.point} contract={spec.contract_size}")
    print(f"  vol min/step/max={spec.volume_min}/{spec.volume_step}/{spec.volume_max}")
    print(f"  tick_value={spec.tick_value} tick_size={spec.tick_size} stops_level={spec.stops_level}")
    print(f"  filling_mode_mask={spec.filling_mode}  bid/ask={tick.bid}/{tick.ask}")
    # sizing sanity check: $5 stop, 0.5% of equity
    risk_amt = c.equity() * s.risk_pct_per_trade / 100.0
    lots = c.compute_lot(5.0, risk_amt)
    print(f"  example: risk ${risk_amt:.2f} @ $5 stop -> {lots} lots")
    c.shutdown()


def signal():
    from .signals.adapter import SignalAdapter

    s = get_settings()
    sig = SignalAdapter(s).get_signal()
    print(f"action={sig.action.value} confidence={sig.confidence:.2f} hash={sig.dedup_hash()}")
    print("--- rationale (truncated) ---")
    print(sig.rationale[:800])


def bias():
    from .strategy.bias import BiasProvider

    s = get_settings()
    bp = BiasProvider(s)
    cached = bp._load()
    if cached is not None:
        age = bp._age_hours(cached)
        print(f"cached bias: {cached.direction.value} conviction={cached.conviction:.2f} "
              f"age={age:.2f}h stale={bp.is_stale(cached)}")
    else:
        print("no cached bias yet")
    b = bp.current()  # refreshes (LLM) only if stale/missing
    print(f"current bias: {b.direction.value} conviction={b.conviction:.2f} ts={b.ts}")
    print("--- rationale (truncated) ---")
    print(b.rationale[:600])


def tech():
    from .mt5.client import MT5Client
    from .strategy.technical import TechnicalEngine

    s = get_settings()
    c = MT5Client(s); c.connect()
    eng = TechnicalEngine(s, c)
    reads = eng.read()
    print("multi-timeframe read:")
    for k, v in reads.items():
        print(f"  {k}: {round(v, 3) if isinstance(v, float) else v}")
    sig = eng.evaluate()
    print(f"TechSignal side={sig.side.value if sig.side else None} score={sig.score:.2f}")
    for r in sig.reasons:
        print(f"   - {r}")
    c.shutdown()


def run_once():
    from .supervisor.loop import Supervisor

    sup = Supervisor()
    sup.startup()
    sup.tick()
    sup.client.shutdown()
    print("Single tick complete. See logs/ and data/journal.sqlite.")


def status():
    import sqlite3

    from .learning.journal import Journal
    from .supervisor.state import SupervisorState

    s = get_settings()
    st = SupervisorState.load(s.state_file)
    j = Journal(s.journal_db)

    # --- live account + OPEN positions (the part that was missing) ---
    print("=== account / open positions ===")
    try:
        from .mt5.client import MT5Client

        c = MT5Client(s)
        c.connect()
        bal, eq = c.balance(), c.equity()
        print(f"  balance={bal:.2f}  equity={eq:.2f}  floating_pnl={eq - bal:+.2f}")
        positions = c.get_open_positions()
        if not positions:
            print("  (no open positions)")
        for p in positions:
            side = "BUY" if p.type == 0 else "SELL"
            print(f"  OPEN {side} ticket={p.ticket} vol={p.volume} entry={p.price_open} "
                  f"sl={p.sl} tp={p.tp} floating={p.profit:+.2f}")
        c.shutdown()
    except Exception as exc:  # noqa: BLE001
        print(f"  (MT5 unavailable — is the terminal running? {exc})")

    # --- recent orders the bot placed ---
    print("=== recent orders (last 5) ===")
    conn = sqlite3.connect(s.journal_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts,side,lots,entry,mt5_ticket,retcode,ok FROM orders ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if not rows:
        print("  (none placed yet)")
    for r in rows:
        verdict = "FILLED" if r["ok"] else f"NOT FILLED(retcode {r['retcode']})"
        print(f"  {r['ts'][:19]}  {r['side']} {r['lots']} @ {r['entry']}  "
              f"ticket={r['mt5_ticket']}  {verdict}")
    conn.close()

    # --- CLOSED-trade performance (what 'performance' meant before) ---
    print("=== closed-trade performance (last 20) ===")
    print(f"  {j.performance_summary(20)}")

    # --- internal state ---
    print("=== self-heal / state ===")
    from .learning.reflection import defensive_state

    ds = defensive_state(j, s)
    print(f"  defensive: risk x{ds.risk_mult}  pause={ds.pause}  ({ds.reason})")
    print(f"  last_reflection={st.last_reflection_iso}  closed_trades={j.closed_count()}")
    print(f"  last_signal={st.last_signal_hash}  last_run={st.last_run_iso}")
    print(f"  day_anchor_equity={st.day_anchor_equity}  start_equity={st.start_equity}")
    print(f"  halted_until={st.halted_until}  "
          f"kill_switch={'PRESENT' if s.kill_switch_file.exists() else 'absent'}")


def reflect():
    from .learning.journal import Journal
    from .learning.reflection import ReflectionEngine
    from .observability.notifier import Notifier

    s = get_settings()
    j = Journal(s.journal_db)
    result = ReflectionEngine(s, j, Notifier(s)).run()
    st = result["stats"]
    print(f"trades={st['trades']} win_rate={st['win_rate']:.0%} avg_r={st['avg_r']:+.2f} "
          f"PF={st['profit_factor']} net={st['net_pnl']:+.2f} max_loss_streak={st['max_loss_streak']}")
    print("by_direction:", st["by_direction"])
    print("defensive:", result["defensive"])
    print("suggestions:", result["suggestions"])
    print(f"report written under {s.reflections_dir}")


def kill():
    from .safety.guards import trip_kill_switch

    trip_kill_switch(get_settings(), "manual cli kill")
    print("Kill switch created. Supervisor will idle.")


def unkill():
    s = get_settings()
    if s.kill_switch_file.exists():
        s.kill_switch_file.unlink()
        print("Kill switch removed.")
    else:
        print("No kill switch present.")


_COMMANDS = {
    "connftest": connftest,
    "signal": signal,
    "bias": bias,
    "tech": tech,
    "run-once": run_once,
    "reflect": reflect,
    "status": status,
    "kill": kill,
    "unkill": unkill,
}


def main():
    setup_logging()
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(__doc__)
        sys.exit(1)
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
