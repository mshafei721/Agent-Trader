"""Diagnostics & manual control CLI.

Usage (from the venv):
  python -m goldtrader.cli connftest      # connect + demo guard + symbol specs
  python -m goldtrader.cli signal         # run ONE TradingAgents analysis, print signal
  python -m goldtrader.cli bias           # show/refresh the cached LLM bias (slow tier)
  python -m goldtrader.cli tech           # show multi-timeframe technical read (free, fast)
  python -m goldtrader.cli run-once       # run a single supervisor tick (respects DRY_RUN)
  python -m goldtrader.cli reflect        # run the reflection / self-heal report now
  python -m goldtrader.cli status         # show state + defensive mode + journal performance
  python -m goldtrader.cli backtest-fetch # pull + cache MT5 history for the backtest (needs MT5)
  python -m goldtrader.cli backtest-import# import years of XAU/USD M30 from Dukascopy (free, no key)
  python -m goldtrader.cli backtest       # run the offline backtest on cached history (no MT5)
  python -m goldtrader.cli walkforward    # walk-forward (out-of-sample) parameter validation
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


def backtest_fetch():
    from .backtest.data import fetch_and_cache

    s = get_settings()
    print(f"Fetching MT5 history (~{s.backtest_bars} {s.trigger_timeframe} bars)...")
    summary = fetch_and_cache(s)
    for tf, info in summary.items():
        print(f"  tf={tf}: {info['bars']} bars")
    print(f"Cached under {s.backtest_dir}. Now run: python -m goldtrader.cli backtest")


def backtest():
    import json

    from .backtest.data import load_bars, load_spec
    from .backtest.engine import run_backtest

    s = get_settings()
    bars = load_bars(s)
    spec = load_spec(s)
    res = run_backtest(s, bars, spec, model_management=s.backtest_model_management)
    st = res.stats
    print(f"=== Backtest: {res.label} exits "
          f"(tp_mult={s.atr_tp_mult} sl_mult={s.atr_sl_mult} adx_min={s.adx_min_trend}) ===")
    print(f"  trades={st.trades}  win_rate={st.win_rate:.1%} "
          f"(95% CI {st.win_rate_ci[0]:.1%}-{st.win_rate_ci[1]:.1%})")
    print(f"  expectancy={st.expectancy:+.3f}R/trade "
          f"(95% CI {st.expectancy_ci[0]:+.3f}..{st.expectancy_ci[1]:+.3f})")
    print(f"  profit_factor={st.profit_factor:.2f}  total={st.total_r:+.1f}R")
    print(f"  max_drawdown={st.max_drawdown_r:.1f}R  max_consec_losses={st.max_consecutive_losses}")
    print(f"  Monte-Carlo drawdown: p50={st.mc_drawdown_p50:.1f}R  p95={st.mc_drawdown_p95:.1f}R")
    if st.trades < 30:
        print("  EDGE: insufficient sample (need >=30 trades for a meaningful read)")
    elif st.expectancy_ci[0] > 0:
        print("  EDGE: POSITIVE — 95% CI excludes zero (after modeled costs)")
    else:
        print("  EDGE: NOT proven — expectancy CI includes zero")
    # Persist the trade log for inspection.
    out = s.backtest_dir / "last_run.json"
    out.write_text(json.dumps([t.__dict__ for t in res.trades], indent=2), encoding="utf-8")
    print(f"  trade log -> {out}")


def backtest_import():
    from .backtest.dukascopy_import import import_history

    s = get_settings()
    print(f"Importing ~{s.backtest_dukascopy_years}yr XAU/USD M30 from Dukascopy (free, no key)...")
    summary = import_history(s, years=s.backtest_dukascopy_years)
    for tf, info in summary.items():
        print(f"  tf={tf}: {info['bars']} bars")
    print(f"Cached under {s.backtest_dir}. Run: python -m goldtrader.cli backtest  (or walkforward)")


def walkforward():
    from .backtest.data import load_bars, load_spec
    from .backtest.walkforward import DEFAULT_GRID, format_report, run_grid, walk_forward
    from .feeds.cot import historical_zseries

    s = get_settings()
    bars = load_bars(s)
    spec = load_spec(s)
    print(f"Running walk-forward over {len(DEFAULT_GRID)} configs (one backtest each)...")
    zs = historical_zseries(s, weeks=400) or None  # cover multi-year backtests
    if not zs:
        print("  (COT history unavailable — COT-gated configs degrade to no-gate)")
    grid_trades = run_grid(s, bars, spec, zs, DEFAULT_GRID)
    wf = walk_forward(grid_trades, n_folds=3, train_frac=0.5, seed=s.backtest_seed)
    print(format_report(wf, grid_trades, s.backtest_seed))


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
    "backtest-fetch": backtest_fetch,
    "backtest-import": backtest_import,
    "backtest": backtest,
    "walkforward": walkforward,
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
