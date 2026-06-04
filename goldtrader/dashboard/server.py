"""FastAPI app for the dashboard: read-only GET endpoints, an SSE log stream, and
POST control actions. Binds to loopback only (see __main__). Control actions can
optionally require a token (``dashboard_token``)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..config import get_settings
from ..logging_setup import get_logger
from . import controls, readers

log = get_logger("goldtrader.dashboard")

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="GoldTrader Dashboard", docs_url=None, redoc_url=None)

    def _require_token(token: str | None) -> None:
        if s.dashboard_token is None:
            return
        expected = s.dashboard_token.get_secret_value()
        if token != expected:
            raise HTTPException(status_code=401, detail="invalid or missing dashboard token")

    # ---------------- page ----------------
    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    # ---------------- read-only JSON ----------------
    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        return readers.read_status(s)

    @app.get("/api/state")
    async def state():
        return readers.read_state(s)

    @app.get("/api/positions")
    async def positions():
        return await asyncio.to_thread(readers.read_positions, s)

    @app.get("/api/journal")
    async def journal():
        return await asyncio.to_thread(readers.read_journal, s)

    @app.get("/api/equity")
    async def equity():
        return await asyncio.to_thread(readers.read_equity, s)

    @app.get("/api/safety")
    async def safety():
        return await asyncio.to_thread(readers.read_safety, s)

    @app.get("/api/bias")
    async def bias():
        return readers.read_bias(s)

    @app.get("/api/reflections")
    async def reflections(n: int = 5):
        return readers.read_reflections(s, n)

    @app.get("/api/logs/tail")
    async def logs_tail(n: int | None = None):
        return {"lines": readers.tail_log(s, n)}

    # ---------------- SSE log stream ----------------
    @app.get("/api/logs/stream")
    async def logs_stream(request: Request):
        return StreamingResponse(
            _log_event_generator(request, s),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------------- control actions (POST) ----------------
    @app.post("/api/actions/kill")
    async def action_kill(request: Request, x_dashboard_token: str | None = Header(default=None)):
        _require_token(x_dashboard_token)
        body = await _json_body(request)
        return controls.set_kill_switch(s, bool(body.get("on", True)))

    @app.post("/api/actions/bias/refresh")
    async def action_bias_refresh(x_dashboard_token: str | None = Header(default=None)):
        _require_token(x_dashboard_token)
        return controls.refresh_bias(s)

    @app.post("/api/actions/reflect")
    async def action_reflect(x_dashboard_token: str | None = Header(default=None)):
        _require_token(x_dashboard_token)
        return controls.run_reflection(s)

    @app.post("/api/actions/run-once")
    async def action_run_once(x_dashboard_token: str | None = Header(default=None)):
        _require_token(x_dashboard_token)
        return controls.run_once(s)

    @app.post("/api/actions/supervisor/stop")
    async def action_stop(x_dashboard_token: str | None = Header(default=None)):
        _require_token(x_dashboard_token)
        return await asyncio.to_thread(controls.stop_supervisor, s)

    @app.post("/api/actions/supervisor/restart")
    async def action_restart(x_dashboard_token: str | None = Header(default=None)):
        _require_token(x_dashboard_token)
        return await asyncio.to_thread(controls.restart_supervisor, s)

    @app.get("/api/config")
    async def config():
        # Non-sensitive UI hints only.
        return {
            "symbol_candidates": s.symbol_candidates,
            "dry_run_default": s.dry_run,
            "bias_refresh_hours": s.bias_refresh_hours,
            "interval_minutes": s.interval_minutes,
            "manage_interval_seconds": s.manage_interval_seconds,
            "bias_veto_conviction": s.bias_veto_conviction,
            "bias_exit_conviction": s.bias_exit_conviction,
            "defensive_loss_streak": s.defensive_loss_streak,
            "defensive_pause_streak": s.defensive_pause_streak,
            # Non-sensitive risk figures for the run-once exposure estimate + safety card.
            "risk_pct_per_trade": s.risk_pct_per_trade,
            "max_daily_loss_pct": s.max_daily_loss_pct,
            "max_total_loss_pct": s.max_total_loss_pct,
            "auth_required": s.dashboard_token is not None,
        }

    return app


async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


async def _log_event_generator(request: Request, s):
    """Tail logs/goldtrader.jsonl, pushing each new line as an SSE event.

    Handles rotation/truncation (RotatingFileHandler) by detecting a shrink and
    reopening from the start, and sends a keepalive comment so proxies don't time
    out an idle stream."""
    path = s.log_file
    last_ping = 0.0
    f = None
    pos = 0
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                if f is None and path.exists():
                    f = path.open("r", encoding="utf-8", errors="replace")
                    f.seek(0, 2)  # start at the end; backfill is via /api/logs/tail
                    pos = f.tell()
                if f is not None:
                    size = path.stat().st_size
                    if size < pos:  # rotated/truncated -> reopen
                        f.close()
                        f = path.open("r", encoding="utf-8", errors="replace")
                        pos = 0
                    line = f.readline()
                    while line:
                        pos = f.tell()
                        ln = line.strip()
                        if ln and not _is_noise_line(ln):
                            yield f"data: {_safe_line(ln)}\n\n"
                        line = f.readline()
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'event': 'stream_error', 'level': 'warning', 'error': str(exc)})}\n\n"
            # keepalive every ~15s
            last_ping += 1.0
            if last_ping >= 15.0:
                last_ping = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(1.0)
    finally:
        if f is not None:
            f.close()


def _safe_line(ln: str) -> str:
    """Ensure the SSE payload is a single-line JSON object."""
    try:
        json.loads(ln)
        return ln
    except ValueError:
        return json.dumps({"event": ln, "level": "info"})


def _is_noise_line(ln: str) -> bool:
    """Hide low-signal connection chatter from the live feed (still on disk)."""
    try:
        return readers.is_noise(json.loads(ln))
    except ValueError:
        return False
