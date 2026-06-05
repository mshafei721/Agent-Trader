"""The autonomous supervisor loop — the orchestrator.

State machine per tick:
  guards -> day-roll -> loss gates -> market-open -> ensure_connected
  -> reconcile closed positions (learning) -> signal -> dedup -> risk -> execute
  -> persist state + heartbeat -> sleep to next bar.

Designed to be crash-safe: state is persisted atomically so a restart never
double-enters a position.
"""
from __future__ import annotations

import json
import signal as os_signal
import time
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..feeds.calendar import EconomicCalendar
from ..feeds.cot import CotProvider
from ..healing.circuit_breaker import CircuitBreaker
from ..healing.heartbeat import write_heartbeat
from ..learning.journal import Journal
from ..learning.reflection import ReflectionEngine, defensive_state
from ..logging_setup import get_logger, setup_logging
from ..mt5.client import MT5Client
from ..observability.notifier import Notifier
import MetaTrader5 as mt5  # type: ignore

from ..risk import indicators
from ..risk.manager import RiskManager, _tf, can_pyramid, half_lot, open_risk_money
from ..safety import guards
from ..strategy.bias import BiasProvider, bias_vetoes
from ..strategy.exits import chandelier_stop, ratchet_stop, should_bias_exit, should_cut_loss
from ..strategy.technical import TechnicalEngine
from ..types import Action, OrderIntent, today_iso
from . import scheduler
from .state import SupervisorState

log = get_logger("goldtrader.loop")


class Supervisor:
    def __init__(self):
        self.s = get_settings()
        self.client = MT5Client(self.s)
        self.bias_provider = BiasProvider(self.s)
        self.tech = TechnicalEngine(self.s, self.client)
        self.risk = RiskManager(self.s, self.client)
        self.journal = Journal(self.s.journal_db)
        self.notifier = Notifier(self.s)
        self.reflection = ReflectionEngine(self.s, self.journal, self.notifier)
        self.breaker = CircuitBreaker(state_path=self.s.circuit_breaker_file)
        self.calendar = EconomicCalendar(self.s)
        self.cot = CotProvider(self.s)
        self.state = SupervisorState.load(self.s.state_file)
        self._stop = False

    # ---------------- lifecycle ----------------
    def request_stop(self, *_):
        log.info("stop_requested")
        self._stop = True

    def startup(self):
        self.client.connect()
        eq = self.client.equity()
        self.state.roll_day_if_needed(eq)
        self.state.save(self.s.state_file)
        # We've just read .env, so any dashboard "restart to apply" marker is now satisfied.
        self.s.settings_pending_file.unlink(missing_ok=True)
        self.notifier.notify(
            "supervisor started",
            f"symbol={self.client.symbol} equity={eq:.2f} dry_run={self.s.dry_run}",
        )
        # Warm a cold/stale macro-bias cache at boot (force=False -> a fresh cache
        # is reused, so a quick restart never pays for a redundant LLM run).
        self.refresh_bias_safe(force=False)

    # ---------------- macro bias (slow LLM tier, decoupled from triggers) ----------------
    def refresh_bias_safe(self, *, force: bool = False) -> None:
        """Refresh the LLM macro bias on its own cadence, independent of chart triggers.

        ``current()`` only hits the LLM when the cached bias is stale (or force=True),
        so calling this every wakeup is cheap. Charts lead: an LLM/network outage must
        NEVER block or crash the loop.
        """
        try:
            self.bias_provider.current(force_refresh=force, run_date=today_iso())
            self.breaker.record_success()
        except Exception as exc:  # noqa: BLE001
            log.warning("bias_unavailable_proceeding", error=str(exc))

    # ---------------- account snapshot (for the dashboard) ----------------
    def _write_account_snapshot(self, equity: float) -> None:
        """Persist balance/equity/floating + open positions so the dashboard can
        render live account state by reading a file (no second MT5 connection)."""
        bal = self.client.balance()
        positions = []
        for p in self.client.get_open_positions():
            positions.append({
                "ticket": p.ticket,
                "side": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume,
                "price_open": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
                "profit": round(float(p.profit), 2),
            })
        payload = {
            "ts": time.time(),
            "symbol": self.client.symbol,
            "balance": round(bal, 2),
            "equity": round(equity, 2),
            "floating_pnl": round(equity - bal, 2),
            "positions": positions,
        }
        path = self.s.account_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)

    # ---------------- learning reconciliation ----------------
    def reconcile_closed(self):
        """Detect positions that closed since last check, record outcomes."""
        open_now = {p.ticket for p in self.client.get_open_positions()}
        pending = self.journal.open_tickets()
        closed = pending - open_now
        if not closed:
            return
        # Pull recent deals to attribute realized PnL.
        deals = self.client.get_deals_since(datetime.now(timezone.utc) - timedelta(days=3))
        pnl_by_pos: dict[int, float] = {}
        exit_by_pos: dict[int, float] = {}
        for d in deals:
            pid = getattr(d, "position_id", None)
            if pid is None:
                continue
            pnl_by_pos[pid] = pnl_by_pos.get(pid, 0.0) + float(d.profit) + float(
                getattr(d, "swap", 0.0)
            ) + float(getattr(d, "commission", 0.0))
            exit_by_pos[pid] = float(d.price)
        for ticket in closed:
            order = self.journal.order_for_ticket(ticket)
            pnl = pnl_by_pos.get(ticket, 0.0)
            risk_amount = (order["risk_amount"] if order and order["risk_amount"] else 0.0)
            r_mult = (pnl / risk_amount) if risk_amount else 0.0
            self.journal.record_outcome(
                mt5_ticket=ticket,
                close_ts=today_iso(),
                exit_price=exit_by_pos.get(ticket, 0.0),
                realized_pnl=pnl,
                r_multiple=r_mult,
                close_reason="closed",
            )
            log.info("outcome_recorded", ticket=ticket, pnl=round(pnl, 2),
                     r_multiple=round(r_mult, 2))
            self.notifier.notify("position closed", f"ticket={ticket} pnl={pnl:+.2f} R={r_mult:+.2f}")

    # ---------------- trade management (fast loop, ~every manage_interval_seconds) ----------------
    def manage_open_positions(self):
        """Per open position: cut losers early on momentum flip, scale out half at +Nr,
        then move SL to breakeven and trail with a Chandelier (swing extreme -/+ ATR)."""
        positions = self.client.get_open_positions()
        if not positions:
            return
        s = self.s
        spec = self.client.spec
        # --- shared indicators computed ONCE per cycle (single symbol) ---
        stop_bars = self.client.get_rates(_tf(s.stop_timeframe),
                                          max(s.chandelier_lookback + 5, s.atr_period * 4))
        atr_tf = indicators.atr(stop_bars, s.atr_period)
        if atr_tf != atr_tf or atr_tf <= 0:  # NaN/invalid -> skip management this cycle
            return
        swing_high, swing_low = indicators.recent_swing(stop_bars, s.chandelier_lookback)
        # Fast-timeframe flip for the early loss-cut (ema / macd / either).
        fast_trend = 0
        if s.cut_loss_enabled:
            fast_bars = self.client.get_rates(_tf(s.cut_loss_timeframe), max(s.ema_slow + 10, 60))
            if s.cut_loss_signal == "macd":
                fast_trend = indicators.macd_cross(fast_bars, s.macd_fast, s.macd_slow, s.macd_signal)
            else:
                fast_trend = indicators.ema_trend(fast_bars, s.ema_fast, s.ema_slow)
                if fast_trend == 0 and s.cut_loss_signal == "either":
                    fast_trend = indicators.macd_cross(fast_bars, s.macd_fast, s.macd_slow, s.macd_signal)
        # Cached LLM bias (NO refresh in the fast loop) for the bias-aware exit.
        cached_bias = self.bias_provider._load() if s.bias_exit_enabled else None
        tick = self.client.get_tick()
        eps = spec.point
        for p in positions:
            order = self.journal.order_for_ticket(p.ticket)
            init_sl = order["sl"] if order and order["sl"] else p.sl
            orig_lots = order["lots"] if order and order["lots"] else p.volume
            r_dist = abs(p.price_open - init_sl) if init_sl else 0.0
            if r_dist <= 0:
                continue
            is_buy = p.type == 0
            cur = tick.bid if is_buy else tick.ask
            profit_dist = (cur - p.price_open) if is_buy else (p.price_open - cur)
            r_now = profit_dist / r_dist

            # 0) BIAS-AWARE EXIT: cached LLM bias opposes this position -> close or tighten
            if s.bias_exit_enabled and should_bias_exit(
                Action.BUY if is_buy else Action.SELL, cached_bias, s.bias_exit_conviction
            ):
                if s.bias_exit_action == "tighten":
                    if not s.dry_run:
                        self.client.modify_position(p.ticket, p.price_open, p.tp)
                    log.info("bias_exit_tighten", ticket=p.ticket,
                             bias=cached_bias.direction.value)
                else:
                    if s.dry_run:
                        log.info("bias_exit_dryrun", ticket=p.ticket, bias=cached_bias.direction.value)
                    else:
                        res = self.client.close_position(p)
                        if res.ok:
                            log.info("bias_exit", ticket=p.ticket, r_now=round(r_now, 2),
                                     bias=cached_bias.direction.value)
                    continue

            # 1) EARLY LOSS-CUT: close a loser before the full stop if momentum flipped
            if s.cut_loss_enabled and should_cut_loss(r_now, fast_trend, is_buy, s.cut_loss_at_r):
                if s.dry_run:
                    log.info("early_loss_cut_dryrun", ticket=p.ticket, r_now=round(r_now, 2))
                else:
                    res = self.client.close_position(p)
                    if res.ok:
                        log.info("early_loss_cut", ticket=p.ticket, r_now=round(r_now, 2),
                                 fast_trend=fast_trend)
                continue

            # 2) SCALE-OUT: take half off once at +partial_tp_r
            if s.partial_tp_r > 0 and r_now >= s.partial_tp_r:
                already_scaled = p.volume < orig_lots - (spec.volume_step / 2)
                half = half_lot(orig_lots, spec.volume_step, spec.volume_min)
                if half is not None and not already_scaled:
                    if s.dry_run:
                        log.info("scaled_out_dryrun", ticket=p.ticket, half=half, r_now=round(r_now, 2))
                    else:
                        res = self.client.close_position(p, volume=half)
                        if res.ok:
                            log.info("scaled_out", ticket=p.ticket, closed=half, r_now=round(r_now, 2))

            # 3) BREAKEVEN + CHANDELIER TRAIL on the runner (once +breakeven_at_r reached)
            if r_now < s.breakeven_at_r:
                continue
            chand = chandelier_stop(is_buy, swing_high, swing_low, atr_tf, s.trail_atr_mult)
            be = p.price_open
            candidate = max(be, chand) if is_buy else min(be, chand)
            new_sl, improved = ratchet_stop(is_buy, candidate, p.sl, eps)
            if s.use_trailing and improved:
                res = self.client.modify_position(p.ticket, new_sl, p.tp)
                if res.ok:
                    log.info("trail_sl", ticket=p.ticket, new_sl=round(new_sl, spec.digits),
                             r_now=round(r_now, 2))

    def _close_all_positions(self, reason: str) -> None:
        """Flatten every open position (respects DRY_RUN). Used by the weekend-flat guard."""
        positions = self.client.get_open_positions()
        if not positions:
            return
        for p in positions:
            if self.s.dry_run:
                log.info("flat_all_dryrun", ticket=p.ticket, reason=reason)
                continue
            res = self.client.close_position(p)
            if res.ok:
                log.info("flat_all_close", ticket=p.ticket, reason=reason)
            else:
                log.warning("flat_all_close_failed", ticket=p.ticket, retcode=res.retcode)

    # ---------------- fast management cycle (every manage_interval_seconds) ----------------
    def manage_cycle(self) -> bool:
        """Guards + reconcile + fast trade management. Returns True if NEW ENTRIES are allowed."""
        s = self.s
        if guards.kill_switch_active(s):
            log.warning("kill_switch_active")
            return False
        self.client.ensure_connected()
        equity = self.client.equity()
        self.state.roll_day_if_needed(equity)
        # Publish an account snapshot for the dashboard (so it never opens its own MT5 link).
        try:
            self._write_account_snapshot(equity)
        except Exception as exc:  # noqa: BLE001
            log.warning("account_snapshot_failed", error=str(exc))
        if guards.total_loss_breached(self.state.start_equity, equity, s):
            guards.trip_kill_switch(s, f"total loss breached at equity {equity:.2f}")
            self.notifier.notify("KILL SWITCH", "total loss threshold breached")
            return False
        try:
            age = self.client.tick_age_seconds()
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure(f"tick_age: {exc}")
            return False
        is_open, why = scheduler.market_open(s, age)
        if not is_open:
            log.info("market_closed", reason=why)
            return False
        # Always reconcile + manage open trades while the market is open (the fast safeguard).
        try:
            self.reconcile_closed()
        except Exception as exc:  # noqa: BLE001
            log.warning("reconcile_failed", error=str(exc))
        try:
            self.manage_open_positions()
        except Exception as exc:  # noqa: BLE001
            log.warning("manage_positions_failed", error=str(exc))
        # Weekend flat: close everything before the Friday close so a tight stop can't be
        # gapped through on the Monday reopen. Blocks new entries for the rest of the window.
        if s.weekend_flat_enabled and scheduler.should_close_for_weekend(s):
            self._close_all_positions("weekend_flat")
            return False
        # Daily-loss gate blocks NEW entries only — management above still ran.
        daily = guards.check_daily_loss(self.state.day_anchor_equity, equity, s)
        if not daily.allowed:
            log.warning("daily_loss_halt", reason=daily.reason)
            self.state.save(s.state_file)
            return False
        return True

    # ---------------- entry cycle (every interval_minutes) ----------------
    def entry_cycle(self):
        s = self.s
        if self.breaker.is_open:
            log.warning("breaker_open_hold")
            return

        # technical trigger FIRST — charts lead (free; most ticks end here)
        try:
            tech = self.tech.evaluate()
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure(f"tech: {exc}")
            log.error("tech_error", error=str(exc))
            return
        if tech.side is None:
            log.info("no_trigger", reasons=tech.reasons)
            return

        # --- V7 Phase-0 entry gates (cheap, deterministic; run BEFORE the LLM bias fetch) ---
        now = datetime.now(timezone.utc)
        # session-time gate: only open new trades in the liquid London-NY overlap
        if not scheduler.in_session(s, now):
            log.info("session_filter_skip", hour=now.hour)
            return
        # Monday-grace gate: skip the thin/wide-spread window just after the Sunday reopen
        if scheduler.in_monday_grace(s, now):
            log.info("monday_grace_skip")
            return
        # news/economic-calendar blackout (FAILS CLOSED) around high-impact USD events
        if s.news_blackout_enabled:
            blackout, why = self.calendar.in_blackout(now)
            if blackout:
                log.warning("news_blackout_skip", reason=why)
                return
        # spread guard: refuse new entries when the live spread blows out
        if s.spread_guard_enabled:
            try:
                spread_pts = self.client.current_spread_points()
            except Exception as exc:  # noqa: BLE001 — be conservative: skip this entry
                log.warning("spread_read_failed_skip", error=str(exc))
                return
            sg = guards.entry_spread_ok(spread_pts, s)
            if not sg.allowed:
                log.info("spread_guard_triggered", reason=sg.reason)
                return
        # COT positioning gate: don't chase a crowded managed-money extreme (FAILS OPEN)
        if s.cot_gate_enabled:
            try:
                cot_ok, cot_reason = self.cot.gate(tech.side)
            except Exception as exc:  # noqa: BLE001 — quality filter: never block on its failure
                log.warning("cot_gate_error_proceeding", error=str(exc))
                cot_ok = True
            if not cot_ok:
                log.info("cot_gate_triggered", reason=cot_reason)
                return

        # defensive self-heal gate: pause NEW entries after a bad streak (deterministic)
        defensive = defensive_state(self.journal, s)
        if defensive.pause:
            log.warning("defensive_pause", reason=defensive.reason)
            return

        # 8. LLM soft-veto (bias fetched only now that a trigger exists; lazy LLM refresh)
        bias = None
        try:
            bias = self.bias_provider.current(run_date=today_iso())
            self.breaker.record_success()
        except Exception as exc:  # noqa: BLE001
            # Charts lead: an LLM/network outage must not freeze trading.
            log.warning("bias_unavailable_proceeding", error=str(exc))
        if bias is not None and bias_vetoes(tech.side, bias, s.bias_veto_conviction):
            log.info("bias_veto", tech=tech.side.value,
                     bias=bias.direction.value, conviction=round(bias.conviction, 2))
            return

        side = tech.side
        bias_dir = bias.direction.value if bias is not None else "none"
        dedup_key = f"{side.value}|{today_iso()}|{tech.score:.1f}"
        rationale = "; ".join(tech.reasons) + f"; bias={bias_dir}"
        context_json = json.dumps({
            "reasons": tech.reasons, "score": round(tech.score, 3),
            "bias_dir": bias_dir,
            "bias_conviction": round(bias.conviction, 2) if bias is not None else None,
        })

        decision_id = self.journal.record_decision(
            ts=datetime.now(timezone.utc).isoformat(),
            run_date=today_iso(),
            action=side.value,
            confidence=tech.score,
            signal_hash=dedup_key,
            rationale=rationale,
            raw=(bias.rationale if bias is not None else ""),
            dry_run=s.dry_run,
            context_json=context_json,
        )

        # 9. position reconciliation: reverse on opposite; pyramid into winners only
        positions = self.client.get_open_positions()
        same_dir = [p for p in positions
                    if (p.type == 0 and side == Action.BUY) or (p.type == 1 and side == Action.SELL)]
        opp_dir = [p for p in positions
                   if (p.type == 0 and side == Action.SELL) or (p.type == 1 and side == Action.BUY)]
        # opposite positions -> close (reverse); never hold a hedge
        if opp_dir:
            for p in opp_dir:
                log.info("closing_conflicting", ticket=p.ticket)
                if not s.dry_run:
                    self.client.close_position(p)
            same_dir = []  # after reversing we are flat in our direction
        # same-direction -> pyramiding gate (winners only, capped count)
        if same_dir:
            ok, reason = can_pyramid(same_dir, s.max_open_positions, s.pyramid_winners_only)
            if not ok:
                log.info("add_blocked", reason=reason)
                return

        # 11. risk sizing (defensive self-heal scaler — only ever reduces size)
        intent = OrderIntent(side=side, confidence=tech.score,
                             rationale=rationale, signal_hash=dedup_key)
        decision = self.risk.evaluate(intent, risk_scaler=defensive.risk_mult)
        if not decision.approved:
            log.info("risk_rejected", reason=decision.reason)
            self.state.last_signal_hash = dedup_key
            self.state.save(s.state_file)
            return

        # 11b. total open-risk cap across all positions
        spec = self.client.spec
        existing_risk = open_risk_money(self.client.get_open_positions(),
                                        spec.tick_value, spec.tick_size)
        equity = self.client.equity()
        risk_cap = equity * s.max_total_risk_pct / 100.0
        if existing_risk + decision.risk_amount > risk_cap:
            log.info("total_risk_cap", existing=round(existing_risk, 2),
                     new=round(decision.risk_amount, 2), cap=round(risk_cap, 2))
            return

        # 12. execute (or dry-run)
        if s.dry_run:
            log.info("DRY_RUN_order", side=side.value, lots=decision.lots,
                     sl=round(decision.sl, 2), tp=round(decision.tp, 2))
            self.journal.record_order(decision_id, datetime.now(timezone.utc).isoformat(),
                                      side.value, decision.lots, decision.entry_hint,
                                      decision.sl, decision.tp, decision.risk_amount,
                                      None, 0, ok=False)
            self.notifier.notify("DRY-RUN signal",
                                 f"{side.value} {decision.lots} lots @~{decision.entry_hint:.2f} "
                                 f"SL {decision.sl:.2f} TP {decision.tp:.2f}")
        else:
            result = self.client.place_market_order(side, decision.lots, decision.sl, decision.tp)
            self.journal.record_order(decision_id, datetime.now(timezone.utc).isoformat(),
                                      side.value, decision.lots, decision.entry_hint,
                                      decision.sl, decision.tp, decision.risk_amount,
                                      result.ticket, result.retcode, ok=result.ok)
            if result.ok:
                self.notifier.notify("ORDER FILLED",
                                     f"{side.value} {decision.lots} lots ticket={result.ticket} "
                                     f"@{result.price:.2f}")
            else:
                self.breaker.record_failure(f"order retcode {result.retcode}")
                self.notifier.notify("ORDER FAILED", f"retcode={result.retcode} {result.comment}")

        self.state.last_signal_hash = dedup_key
        self.state.last_run_iso = datetime.now(timezone.utc).isoformat()
        self.state.save(s.state_file)

    # ---------------- one-shot (cli run-once) ----------------
    def tick(self):
        """One full cycle: manage, then evaluate an entry. Used by `cli run-once`."""
        if self.manage_cycle():
            self.entry_cycle()

    # ---------------- run ----------------
    def run(self):
        os_signal.signal(os_signal.SIGINT, self.request_stop)
        os_signal.signal(os_signal.SIGTERM, self.request_stop)
        self.startup()
        last_entry = 0.0
        entry_period = self.s.interval_minutes * 60
        # Macro-bias refresh runs on its OWN cadence (decoupled from triggers); the
        # boot warm-up in startup() already attempted one, so this fires next period.
        last_bias = time.time()
        bias_period = max(self.s.bias_refresh_hours, 0.1) * 3600
        while not self._stop:
            try:
                allowed = self.manage_cycle()  # FAST: guards + trade management every cycle
                if allowed and (time.time() - last_entry) >= entry_period:
                    self.entry_cycle()         # SLOW: new-entry evaluation
                    last_entry = time.time()
                # PERIODIC: refresh the LLM macro bias regardless of triggers/market-open
                if (time.time() - last_bias) >= bias_period:
                    self.refresh_bias_safe(force=False)
                    last_bias = time.time()
                # PERIODIC: reflection / self-heal / learn (N closed trades or daily)
                if self.reflection.maybe_run(self.state):
                    self.state.save(self.s.state_file)
            except guards.SafetyViolation:
                raise
            except Exception as exc:  # noqa: BLE001
                self.breaker.record_failure(f"cycle: {exc}")
                log.error("cycle_unhandled", error=str(exc))
            finally:
                # short sleep = fast management cadence; stays responsive to stop/kill-switch
                interval = max(5, self.s.manage_interval_seconds)
                now = time.time()
                # Publish the loop schedule so the dashboard can show live countdowns.
                write_heartbeat(self.s.heartbeat_file, {
                    "symbol": self.client.symbol,
                    "dry_run": self.s.dry_run,
                    "trade_mode": self.client.trade_mode,  # 0=demo,1=contest,2=real
                    "next_manage_ts": now + interval,
                    "next_entry_ts": last_entry + entry_period,
                    "next_bias_ts": last_bias + bias_period,
                    "manage_interval_s": interval,
                    "entry_period_s": entry_period,
                    "bias_period_s": bias_period,
                })
            slept = 0.0
            while slept < interval and not self._stop:
                time.sleep(min(2.0, interval - slept))
                slept += 2.0
        self.client.shutdown()
        log.info("supervisor_stopped")


def main():
    setup_logging()
    Supervisor().run()


if __name__ == "__main__":
    main()
