"""SQLite journal of decisions, orders, and realized outcomes.

This is OUR record of actual broker behavior (distinct from TradingAgents'
internal decision-log memory). It powers the feedback conditioning in
feedback.py and the daily performance summary.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_date TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    signal_hash TEXT,
    rationale TEXT,
    raw TEXT,
    dry_run INTEGER NOT NULL DEFAULT 1,
    context_json TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    lots REAL NOT NULL,
    entry REAL,
    sl REAL,
    tp REAL,
    risk_amount REAL,
    mt5_ticket INTEGER,
    retcode INTEGER,
    ok INTEGER,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    mt5_ticket INTEGER,
    close_ts TEXT,
    exit_price REAL,
    realized_pnl REAL,
    r_multiple REAL,
    close_reason TEXT,
    UNIQUE(mt5_ticket),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
"""


class Journal:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Add columns introduced after a DB was first created (idempotent)."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(decisions)").fetchall()}
        if "context_json" not in cols:
            c.execute("ALTER TABLE decisions ADD COLUMN context_json TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- writes ----------
    def record_decision(self, ts, run_date, action, confidence, signal_hash,
                         rationale, raw, dry_run, context_json=None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO decisions(ts,run_date,action,confidence,signal_hash,"
                "rationale,raw,dry_run,context_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts, run_date, action, confidence, signal_hash,
                 rationale, raw, int(dry_run), context_json),
            )
            return int(cur.lastrowid)

    def record_order(self, decision_id, ts, side, lots, entry, sl, tp,
                     risk_amount, mt5_ticket, retcode, ok) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO orders(decision_id,ts,side,lots,entry,sl,tp,risk_amount,"
                "mt5_ticket,retcode,ok) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, ts, side, lots, entry, sl, tp, risk_amount,
                 mt5_ticket, retcode, int(ok)),
            )
            return int(cur.lastrowid)

    def record_outcome(self, mt5_ticket, close_ts, exit_price, realized_pnl,
                       r_multiple, close_reason) -> None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM orders WHERE mt5_ticket=? ORDER BY id DESC LIMIT 1",
                (mt5_ticket,),
            ).fetchone()
            order_id = row["id"] if row else None
            c.execute(
                "INSERT OR IGNORE INTO outcomes(order_id,mt5_ticket,close_ts,exit_price,"
                "realized_pnl,r_multiple,close_reason) VALUES(?,?,?,?,?,?,?)",
                (order_id, mt5_ticket, close_ts, exit_price, realized_pnl,
                 r_multiple, close_reason),
            )

    def upsert_outcome(self, mt5_ticket, close_ts, exit_price, realized_pnl,
                       r_multiple, close_reason) -> bool:
        """Insert or REPLACE the outcome for a position (broker-truth sync). Unlike
        record_outcome (INSERT OR IGNORE), this overwrites a wrong/zero prior record.
        Returns True if it was a brand-new close (for notifications)."""
        with self._conn() as c:
            existed = c.execute(
                "SELECT 1 FROM outcomes WHERE mt5_ticket=?", (mt5_ticket,)).fetchone() is not None
            row = c.execute(
                "SELECT id FROM orders WHERE mt5_ticket=? ORDER BY id DESC LIMIT 1",
                (mt5_ticket,)).fetchone()
            order_id = row["id"] if row else None
            c.execute(
                "INSERT INTO outcomes(order_id,mt5_ticket,close_ts,exit_price,realized_pnl,"
                "r_multiple,close_reason) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(mt5_ticket) DO UPDATE SET close_ts=excluded.close_ts, "
                "exit_price=excluded.exit_price, realized_pnl=excluded.realized_pnl, "
                "r_multiple=excluded.r_multiple, close_reason=excluded.close_reason",
                (order_id, mt5_ticket, close_ts, exit_price, realized_pnl, r_multiple, close_reason),
            )
            return not existed

    def open_tickets(self) -> set[int]:
        """Tickets we have orders for but no recorded outcome yet."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT o.mt5_ticket FROM orders o "
                "LEFT JOIN outcomes oc ON oc.mt5_ticket=o.mt5_ticket "
                "WHERE o.ok=1 AND o.mt5_ticket IS NOT NULL AND oc.id IS NULL"
            ).fetchall()
            return {r["mt5_ticket"] for r in rows}

    # ---------- reads ----------
    def recent_outcomes(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM outcomes ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def performance_summary(self, last_n: int = 20) -> dict:
        rows = self.recent_outcomes(last_n)
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

    def closed_count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) AS n FROM outcomes").fetchone()["n"])

    def recent_closed_detailed(self, limit: int = 30) -> list[sqlite3.Row]:
        """Closed trades joined with their order + decision context (newest first)."""
        with self._conn() as c:
            return c.execute(
                "SELECT oc.close_ts, oc.realized_pnl, oc.r_multiple, oc.close_reason, "
                "o.side, o.lots, o.entry, d.confidence, d.action, d.context_json "
                "FROM outcomes oc "
                "LEFT JOIN orders o ON oc.order_id = o.id "
                "LEFT JOIN decisions d ON o.decision_id = d.id "
                "ORDER BY oc.id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def order_for_ticket(self, ticket: int) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM orders WHERE mt5_ticket=? ORDER BY id DESC LIMIT 1",
                (ticket,),
            ).fetchone()
