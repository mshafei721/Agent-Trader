# Agent Instructions

These instructions apply to all work in this repository.

## 1. Think Before Coding

Do not assume. Do not hide confusion. Surface tradeoffs.

Before implementing:

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them. Do not pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what is confusing. Ask.

## 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No flexibility or configurability that was not requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Do not improve adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice unrelated dead code, mention it. Do not delete it.

When your changes create orphans:

- Remove imports, variables, and functions that your changes made unused.
- Do not remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

- "Add validation" means write tests for invalid inputs, then make them pass.
- "Fix the bug" means write a test that reproduces it, then make it pass.
- "Refactor X" means ensure tests pass before and after.

For multi-step tasks, state a brief plan:

1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]

Strong success criteria let you loop independently. Weak criteria such as "make it work" require constant clarification.

---

## Project: goldtrader

Autonomous XAUUSD (gold) trader — deterministic multi-timeframe technical timing + a slow
TradingAgents LLM macro-bias veto, executing on MetaTrader 5. Full overview in
[README.md](README.md); data/control flow in [SYSTEM_FLOW.md](SYSTEM_FLOW.md).

### Commands (Windows / PowerShell)

Always use the venv interpreter. A bare `python` is an Anaconda shim that **fails from the repo
root** (the repo's `Lib/` dir hijacks the prefix → "No module named 'encodings'").

```powershell
.\.venv\Scripts\python.exe -m pytest -q                # test suite
.\.venv\Scripts\python.exe -m goldtrader.cli status    # broker link + demo guard + sizing (no orders)
.\.venv\Scripts\python.exe -m goldtrader.cli signal    # technical read (free, no LLM)
.\.venv\Scripts\python.exe -m goldtrader.cli run-once  # one full supervisor tick (respects DRY_RUN)
.\run_supervisor.ps1     # launch the trio: supervisor (fg) + watchdog + dashboard
.\run_dashboard.ps1      # dashboard only -> http://127.0.0.1:8787
```

### Architecture

- `goldtrader/supervisor/loop.py` — orchestrator. `manage_cycle()` = fast safety + open-trade
  management every `MANAGE_INTERVAL_SECONDS` (60s); `entry_cycle()` = new-entry evaluation every
  `INTERVAL_MINUTES`; `reconcile_closed()` records closed-trade outcomes.
- `goldtrader/config.py` — all tunables (`Settings`, pydantic-settings). **`.env` overrides these
  defaults** — change cadence/risk there, not in code.
- Modules: `strategy/` (technical + LLM bias), `risk/`, `mt5/` (broker), `learning/` (journal +
  reflection/self-heal), `healing/` (heartbeat + watchdog + circuit-breaker),
  `dashboard/` (FastAPI monitor UI), `safety/` (guards).

### Gotchas

- **`DRY_RUN` lives in `.env`** and is currently `false` → the bot places **real orders on the
  demo account**. Set `DRY_RUN=true` for a no-order dry run.
- **Restart to apply changes.** The supervisor reads `.env` + code only at startup. To deploy:
  stop the trio (kill `goldtrader.healing.watchdog` FIRST so it can't relaunch the supervisor),
  edit, then re-run `.\run_supervisor.ps1`.
- **Edit `data/state.json` only while the supervisor is stopped** — it's overwritten live.
- **Dashboard** serves `static/index.html` fresh per request → Ctrl+F5 to pick up UI edits (no
  restart). It reads supervisor artifacts read-only and never opens its own MT5 link.
- Each goldtrader process shows as a **launcher+worker PID pair** (the venv shim) — not a duplicate.

### Key files

- Entry points: `goldtrader/supervisor/loop.py`, `goldtrader/dashboard/__main__.py`, `goldtrader/cli.py`
- Config: `.env` (live, gitignored) ← `config.py` defaults; `.env.example` template
- State/data: `data/state.json`, `data/journal.sqlite`, `data/heartbeat.json`
