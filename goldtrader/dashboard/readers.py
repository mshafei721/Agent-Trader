"""Read-only data layer for the dashboard.

Every function is individually defensive: it returns a JSON-serializable dict and
NEVER raises into the request handler. SQLite is opened read-only so it cannot
contend with the supervisor's writer. Account/position data is read from the
snapshot file the supervisor writes, so the dashboard never opens its own MT5
connection (which would spam logs and could disturb the live trader's session).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from ..healing.heartbeat import read_heartbeat
from ..logging_setup import get_logger
from ..supervisor.state import SupervisorState

log = get_logger("goldtrader.dashboard")

# Watchdog polls every 60s; treat its heartbeat as alive within ~2 cycles.
_WATCHDOG_FRESH_S = 150

# Low-signal events the dashboard hides from its log feed (still written to disk).
# These fire on every connection and would otherwise drown out the real events.
MUTED_EVENTS = {"symbol_resolved", "mt5_connected", "mt5_reconnecting"}


def is_noise(obj) -> bool:
    return isinstance(obj, dict) and obj.get("event") in MUTED_EVENTS


def _now() -> float:
    return time.time()


def _age_seconds(ts: float | None) -> float:
    if not ts:
        return float("inf")
    return _now() - float(ts)


def _iso_age_hours(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def _stale_threshold(s: Settings) -> int:
    # Mirror the watchdog's definition (watchdog.py): 3 missed bars or a 300s floor.
    return max(3 * s.interval_minutes * 60, 300)


def read_status(s: Settings) -> dict:
    """Supervisor + watchdog liveness, kill-switch, dry-run, symbol."""
    try:
        hb = read_heartbeat(s.heartbeat_file)
        age = _age_seconds(hb.get("ts") if hb else None)
        stale = _stale_threshold(s)
        fresh = max(2 * s.manage_interval_seconds + 15, 30)
        if hb is None:
            health = "down"
        elif age <= fresh:
            health = "up"
        elif age <= stale:
            health = "lagging"
        else:
            health = "stale"

        wd = read_heartbeat(s.watchdog_heartbeat_file)
        wd_age = _age_seconds(wd.get("ts") if wd else None)
        wd_health = "up" if (wd and wd_age <= _WATCHDOG_FRESH_S) else (
            "down" if wd is None else "stale"
        )

        return {
            "health": health,
            "heartbeat_age_s": None if age == float("inf") else round(age, 1),
            "pid": hb.get("pid") if hb else None,
            "symbol": hb.get("symbol") if hb else None,
            "dry_run": hb.get("dry_run") if hb else None,
            "trade_mode": hb.get("trade_mode") if hb else None,  # 0=demo,1=contest,2=real
            "kill_switch": s.kill_switch_file.exists(),
            "settings_pending": s.settings_pending_file.exists(),  # P3.2: restart-to-apply
            "watchdog": {
                "health": wd_health,
                "age_s": None if wd_age == float("inf") else round(wd_age, 1),
                "pid": wd.get("pid") if wd else None,
                "last_action": wd.get("last_action") if wd else None,
                "last_action_ts": wd.get("last_action_ts") if wd else None,
                "supervisor_age_s": wd.get("supervisor_age_s") if wd else None,
            },
            "managed_by_service": bool(os.environ.get("GOLDTRADER_MANAGED_BY_SERVICE")),
            "stale_threshold_s": stale,
            "loops": _read_loops(s, hb),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("read_status_failed", error=str(exc))
        return {"health": "unknown", "error": str(exc)}


def _read_loops(s: Settings, hb: dict | None) -> dict:
    """Loop countdowns (from the supervisor heartbeat) + reflection progress.

    Timers are absolute epoch targets; ``server_now`` lets the client tick them
    down without depending on a synchronized clock. The bias timer falls back to
    the cached bias age when an old supervisor build hasn't published it yet."""
    now = _now()
    hb = hb or {}
    # Bias fallback: cached bias ts + refresh window, when the heartbeat lacks it.
    next_bias = hb.get("next_bias_ts")
    if next_bias is None:
        try:
            d = json.loads(s.bias_file.read_text(encoding="utf-8"))
            t = datetime.fromisoformat(d["ts"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            next_bias = t.timestamp() + s.bias_refresh_hours * 3600
        except (OSError, ValueError, KeyError):
            next_bias = None

    # Reflection is event-driven (N closed trades and/or daily), not a timer.
    refl = {"every_n": s.reflection_every_n_trades, "daily": s.reflection_daily}
    try:
        st = SupervisorState.load(s.state_file)
        conn = _ro_connect(s.journal_db)
        closed = 0
        if conn is not None:
            try:
                closed = int(conn.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"])
            finally:
                conn.close()
        since = max(0, closed - int(getattr(st, "trades_at_last_reflection", 0)))
        last_iso = getattr(st, "last_reflection_iso", None)
        today = datetime.now(timezone.utc).date().isoformat()
        refl.update({
            "since_trades": since,
            "closed_total": closed,
            "last_iso": last_iso,
            "daily_pending": bool(s.reflection_daily and (last_iso or "")[:10] != today and closed > 0),
        })
    except Exception as exc:  # noqa: BLE001
        refl["error"] = str(exc)

    # Management fallback: an older supervisor build doesn't publish next_manage_ts,
    # but it writes a heartbeat every cycle, so ts + interval approximates the next pass.
    manage_period = hb.get("manage_interval_s") or s.manage_interval_seconds
    manage_next = hb.get("next_manage_ts")
    if manage_next is None and hb.get("ts"):
        manage_next = hb["ts"] + manage_period

    return {
        "server_now": now,
        "manage": {"next_ts": manage_next, "period_s": manage_period},
        "entry": {"next_ts": hb.get("next_entry_ts"), "period_s": hb.get("entry_period_s") or s.interval_minutes * 60},
        "bias": {"next_ts": next_bias, "period_s": hb.get("bias_period_s") or s.bias_refresh_hours * 3600},
        "reflection": refl,
    }


def read_state(s: Settings) -> dict:
    """Persistent SupervisorState (never mutated here)."""
    try:
        st = SupervisorState.load(s.state_file)
        return asdict(st)
    except Exception as exc:  # noqa: BLE001
        log.warning("read_state_failed", error=str(exc))
        return {"error": str(exc)}


def read_bias(s: Settings) -> dict:
    """Cached LLM macro bias + freshness. Parses the file directly to avoid the
    heavy TradingAgents import that constructing a BiasProvider triggers."""
    try:
        d = json.loads(s.bias_file.read_text(encoding="utf-8"))
        age_h = _iso_age_hours(d.get("ts"))
        return {
            "direction": d.get("direction"),
            "conviction": d.get("conviction"),
            "ts": d.get("ts"),
            "rationale": d.get("rationale", ""),
            "age_hours": None if age_h == float("inf") else round(age_h, 2),
            "stale": age_h >= s.bias_refresh_hours,
            "refresh_hours": s.bias_refresh_hours,
        }
    except (OSError, ValueError) as exc:
        return {"direction": None, "error": "no cached bias yet", "detail": str(exc)}


def read_positions(s: Settings) -> dict:
    """Live account + open positions from the supervisor's snapshot file.

    The dashboard deliberately does NOT open its own MT5 connection: a second
    process reconnecting every poll spams the log and can disturb the live
    trader's session. The supervisor writes account.json each cycle; we just read
    it (and flag staleness if the supervisor isn't running)."""
    try:
        d = json.loads(s.account_file.read_text(encoding="utf-8"))
        age = _age_seconds(d.get("ts"))
        stale_after = max(3 * s.manage_interval_seconds, 180)
        return {
            "available": True,
            "stale": age > stale_after,
            "age_s": None if age == float("inf") else round(age, 1),
            "symbol": d.get("symbol"),
            "balance": d.get("balance"),
            "equity": d.get("equity"),
            "floating_pnl": d.get("floating_pnl"),
            "positions": d.get("positions", []),
        }
    except (OSError, ValueError):
        return {"available": False, "error": "no account snapshot yet (supervisor not running?)"}


def _ro_connect(db_path: Path) -> sqlite3.Connection | None:
    """Open a read-only connection so we never block/contend with the writer."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def read_journal(s: Settings, recent: int = 30, perf_n: int = 20) -> dict:
    """Performance summary + recent closed trades, read-only."""
    conn = None
    try:
        conn = _ro_connect(s.journal_db)
        if conn is None:
            return {"available": False, "performance": {}, "recent": [], "closed_count": 0}
        closed_count = int(conn.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"])
        perf_rows = conn.execute(
            "SELECT realized_pnl, r_multiple FROM outcomes ORDER BY close_ts DESC, id DESC LIMIT ?",
            (perf_n,),
        ).fetchall()
        performance = _performance_summary(perf_rows)
        rows = conn.execute(
            "SELECT oc.close_ts, oc.realized_pnl, oc.r_multiple, oc.close_reason, "
            "o.side, o.lots, o.entry, d.confidence, d.action "
            "FROM outcomes oc "
            "LEFT JOIN orders o ON oc.order_id = o.id "
            "LEFT JOIN decisions d ON o.decision_id = d.id "
            "ORDER BY oc.close_ts DESC, oc.id DESC LIMIT ?",
            (recent,),
        ).fetchall()
        recent_list = [{
            "close_ts": r["close_ts"],
            "side": r["side"],
            "lots": r["lots"],
            "entry": r["entry"],
            "realized_pnl": round(r["realized_pnl"], 2) if r["realized_pnl"] is not None else None,
            "r_multiple": round(r["r_multiple"], 2) if r["r_multiple"] is not None else None,
            "close_reason": r["close_reason"],
            "confidence": r["confidence"],
        } for r in rows]
        return {
            "available": True,
            "performance": performance,
            "recent": recent_list,
            "closed_count": closed_count,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("read_journal_failed", error=str(exc))
        return {"available": False, "error": str(exc), "performance": {}, "recent": []}
    finally:
        if conn is not None:
            conn.close()


def _performance_summary(rows) -> dict:
    if not rows:
        return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "net_pnl": 0.0}
    wins = sum(1 for r in rows if (r["realized_pnl"] or 0) > 0)
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    return {
        "trades": len(rows),
        "win_rate": round(wins / len(rows), 3),
        "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "net_pnl": round(sum((r["realized_pnl"] or 0) for r in rows), 2),
    }


def read_equity(s: Settings, cap: int = 500) -> dict:
    """Cumulative realized equity + drawdown curve from ALL closed trades (read-only).

    Powers the dashboard equity/drawdown chart. Ordered by insertion id (the order
    trades were recorded = chronological close order). Returns both the money curve
    (cumulative realized PnL) and the R-multiple curve, with running drawdown from
    the peak. Downsampled to `cap` points when there are more (the last point is
    always kept so the headline figures match)."""
    conn = None
    try:
        conn = _ro_connect(s.journal_db)
        if conn is None:
            return {"available": False, "curve": [], "trades": 0}
        rows = conn.execute(
            "SELECT realized_pnl, r_multiple FROM outcomes ORDER BY close_ts ASC, id ASC"
        ).fetchall()
        if not rows:
            return {"available": True, "curve": [], "trades": 0,
                    "net_pnl": 0.0, "net_r": 0.0,
                    "max_drawdown_pnl": 0.0, "max_drawdown_r": 0.0, "current_drawdown_pnl": 0.0}
        cum_pnl = cum_r = 0.0
        peak_pnl = peak_r = 0.0
        max_dd_pnl = max_dd_r = 0.0
        pts = [{"i": 0, "pnl": 0.0, "r": 0.0, "dd": 0.0}]  # baseline
        for k, r in enumerate(rows, start=1):
            cum_pnl += (r["realized_pnl"] or 0.0)
            cum_r += (r["r_multiple"] or 0.0)
            peak_pnl = max(peak_pnl, cum_pnl)
            peak_r = max(peak_r, cum_r)
            dd_pnl = peak_pnl - cum_pnl
            max_dd_pnl = max(max_dd_pnl, dd_pnl)
            max_dd_r = max(max_dd_r, peak_r - cum_r)
            pts.append({"i": k, "pnl": round(cum_pnl, 2), "r": round(cum_r, 3),
                        "dd": round(dd_pnl, 2)})
        curve = _downsample(pts, cap)
        return {
            "available": True,
            "trades": len(rows),
            "net_pnl": round(cum_pnl, 2),
            "net_r": round(cum_r, 3),
            "peak_pnl": round(peak_pnl, 2),
            "max_drawdown_pnl": round(max_dd_pnl, 2),
            "max_drawdown_r": round(max_dd_r, 3),
            "current_drawdown_pnl": round(peak_pnl - cum_pnl, 2),
            "curve": curve,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("read_equity_failed", error=str(exc))
        return {"available": False, "error": str(exc), "curve": [], "trades": 0}
    finally:
        if conn is not None:
            conn.close()


def _downsample(pts: list[dict], cap: int) -> list[dict]:
    """Stride-sample to <= cap points, always keeping the first and last."""
    if len(pts) <= cap:
        return pts
    step = len(pts) / cap
    out = [pts[int(i * step)] for i in range(cap)]
    if out[-1] is not pts[-1]:
        out[-1] = pts[-1]
    return out


def _today_realized_pnl(s: Settings) -> tuple[float, int]:
    """(sum realized_pnl, trade count) for trades closed today (UTC). 0,0 on any error."""
    conn = None
    try:
        conn = _ro_connect(s.journal_db)
        if conn is None:
            return 0.0, 0
        today = datetime.now(timezone.utc).date().isoformat()
        rows = conn.execute(
            "SELECT realized_pnl FROM outcomes WHERE substr(close_ts,1,10)=?", (today,)
        ).fetchall()
        return round(sum((r["realized_pnl"] or 0.0) for r in rows), 2), len(rows)
    except Exception:  # noqa: BLE001
        return 0.0, 0
    finally:
        if conn is not None:
            conn.close()


def read_safety(s: Settings) -> dict:
    """Three plain-English traffic lights: bot health, trading mode, today's loss guard.

    Each light is {state: green|amber|red|unknown, label, detail}. Computed from the
    same read-only artifacts as the rest of the dashboard — never opens MT5, never
    raises into the handler."""
    status = read_status(s)
    health = status.get("health", "unknown")
    kill = bool(status.get("kill_switch"))
    dry = status.get("dry_run")
    trade_mode = status.get("trade_mode")  # 0=demo,1=contest,2=real,None=unknown

    # --- bot light (liveness + kill switch) ---
    if kill:
        bot = {"state": "red", "label": "Halted", "detail": "Kill switch is ON — no new trades."}
    else:
        _bot_map = {
            "up": ("green", "Running", "Supervisor is alive and on schedule."),
            "lagging": ("amber", "Lagging", "Heartbeat is a little behind — watching."),
            "stale": ("red", "Stalled", "No recent heartbeat — the bot may be stuck."),
            "down": ("red", "Stopped", "Supervisor is not running."),
            "unknown": ("unknown", "Unknown", "No heartbeat yet."),
        }
        st, lab, det = _bot_map.get(health, _bot_map["unknown"])
        bot = {"state": st, "label": lab, "detail": det}

    # --- mode light: three honest states (dry-run / demo-live-orders / REAL money) ---
    # require_demo (the hard floor) refuses to TRADE a real account, so real money is only
    # possible when dry_run is off AND the broker reports a real (trade_mode==2) account.
    if dry is True:
        mode = {"state": "green", "label": "Paper (dry-run)",
                "detail": "Simulating only — no orders are sent.", "real_money": False}
    elif dry is None:
        mode = {"state": "unknown", "label": "Unknown", "detail": "Mode not reported yet.",
                "real_money": None}
    elif trade_mode == 2:  # real account + live orders
        mode = {"state": "red", "label": "LIVE money",
                "detail": "Real orders on a REAL-money account.", "real_money": True}
    elif trade_mode in (0, 1):  # demo / contest account, but orders are live
        mode = {"state": "green", "label": "Demo (live orders)",
                "detail": "Placing real orders on a demo account — no real money.",
                "real_money": False}
    else:  # dry_run off but the account type wasn't reported -> warn, don't cry wolf
        mode = {"state": "amber", "label": "Live orders",
                "detail": "Sending real orders; account type not reported yet.",
                "real_money": None}

    # --- loss-guard light (today's realized loss vs the daily cap) ---
    today_pnl, today_n = _today_realized_pnl(s)
    cap_pct = s.max_daily_loss_pct
    pos = read_positions(s)
    balance = pos.get("balance") if pos.get("available") else None
    guard = {"state": "unknown", "label": "Loss guard",
             "detail": "No balance snapshot yet.", "today_pnl": today_pnl,
             "daily_cap_pct": cap_pct}
    if today_pnl >= 0:
        guard.update(state="green",
                     detail=(f"+{today_pnl:.2f} today — no loss." if today_n else "No trades closed today."))
    elif balance and balance > 0:
        used_pct = (-today_pnl) / balance * 100.0
        frac = used_pct / cap_pct if cap_pct > 0 else 0.0
        state = "green" if frac < 0.5 else ("amber" if frac < 0.9 else "red")
        guard.update(state=state, used_pct=round(used_pct, 2),
                     detail=(f"Down {used_pct:.2f}% of {cap_pct:.1f}% daily limit "
                             f"({today_pnl:.2f})."))
    else:
        guard.update(detail=f"Down {today_pnl:.2f} today (cap is {cap_pct:.1f}% of balance).")

    overall = "red" if any(x["state"] == "red" for x in (bot, mode, guard)) else (
        "amber" if any(x["state"] == "amber" for x in (bot, mode, guard)) else (
            "unknown" if any(x["state"] == "unknown" for x in (bot, mode, guard)) else "green"))
    return {"overall": overall, "bot": bot, "mode": mode, "loss_guard": guard}


def read_reflections(s: Settings, n: int = 5) -> dict:
    """Newest N reflection reports (stats + defensive + suggestions + llm_note)."""
    try:
        d = s.reflections_dir
        if not d.exists():
            return {"reports": []}
        files = sorted(d.glob("reflection_*.json"), reverse=True)[:n]
        reports = []
        for f in files:
            try:
                reports.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return {"reports": reports}
    except Exception as exc:  # noqa: BLE001
        log.warning("read_reflections_failed", error=str(exc))
        return {"reports": [], "error": str(exc)}


def tail_log(s: Settings, n: int | None = None) -> list[dict]:
    """Last N parsed log lines (bounded read) for SSE backfill."""
    n = n or s.dashboard_log_tail_lines
    try:
        path = s.log_file
        if not path.exists():
            return []
        # Read extra lines because muting drops some; keep the newest n real events.
        lines = _tail_lines(path, n * 3)
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                obj = {"event": ln, "level": "info"}
            if is_noise(obj):
                continue
            out.append(obj)
        return out[-n:]
    except Exception as exc:  # noqa: BLE001
        log.warning("tail_log_failed", error=str(exc))
        return []


def _tail_lines(path: Path, n: int) -> list[str]:
    """Read the last n lines without loading the whole file."""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        block = 8192
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
        text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-n:]
