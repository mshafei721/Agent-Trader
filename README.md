# goldtrader — Autonomous Gold (XAUUSD) Trading Pipeline

Wraps the [TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent
LLM framework and connects its BUY/SELL/HOLD decisions to your **MetaTrader 5**
account to trade **gold (XAUUSD)** — autonomously, with self-healing and a learning
feedback loop.

> ⚠️ **Read this first.** This is experimental software built on a *research* framework.
> It defaults to a **demo account** and **dry-run** (no real orders). Trading risks real
> money. Past performance does not predict future results. Validate thoroughly on demo
> before you even consider live capital.

---

## What it does — two-speed strategy

```
SLOW TIER (every 4h, ~$1):  TradingAgents LLM → directional BIAS (long/short/flat)
                            cached to data/bias.json
                                     │
FAST TIER (every 15 min, free):      ▼
  H4 trend filter → H1 setup (ADX+MACD) → M30 trigger  =  technical entry
                                     │
        ┌────────────────────────────▼──────────────────────────────┐
        │ enter only if technical side == LLM bias direction         │
        │  → risk sizing (ATR) + volatility gate                     │
        │  → safety gates (demo, daily-loss, kill-switch)            │
        │  → place order on MT5 (or dry-run)                         │
        │  → manage: breakeven + ATR trailing → journal outcome      │
        └────────────────────────────────────────────────────────────┘
```

The LLM plays to its strength (macro/news/sentiment → bias) on a slow, cheap cadence;
a free deterministic multi-timeframe engine times entries within that bias. Most 15-min
ticks cost nothing; the LLM runs only when its cached bias is older than `BIAS_REFRESH_HOURS`.

- **Fundamentals analyst is disabled** (gold has no company financials); Technical,
  News/Macro, and Sentiment analysts feed the bias.
- **Data ticker:** `GC=F` (Yahoo gold futures) feeds TradingAgents; orders execute on
  the broker's `XAUUSD`.
- **Learning:** TradingAgents auto-resolves each prior decision with realized return on
  the next bias refresh (persistent memory), and our SQLite journal shrinks position size
  after losing streaks.
- **More gates = more selective (fewer trades, smaller drawdowns), not guaranteed profit.**

---

## Requirements

- **Windows** with **Python 3.13** (the only interpreter here with `MetaTrader5`).
- **MetaTrader 5 terminal** installed, **logged into your demo account**, with the
  **Algo Trading** button **ON** (toolbar).
- An **OpenAI-compatible LLM endpoint** (see below).

---

## Setup

```powershell
# 1. From the project root, create the venv and install deps
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install MetaTrader5 pydantic-settings structlog python-dotenv pandas numpy pytest requests
.\.venv\Scripts\python.exe -m pip install "git+https://github.com/TauricResearch/TradingAgents.git"

# 2. Create your config
Copy-Item .env.example .env
#   Leave MT5_* blank to attach to the already-running terminal (recommended here).
```

### LLM endpoint — pick ONE

Swappable by editing `.env` only.

**A) Anthropic API key (default, recommended).** Natively supported by TradingAgents —
no proxy, no middleware. Just paste your key:
```
LLM_PROVIDER=anthropic
LLM_BACKEND_URL=            # blank = Anthropic default endpoint
LLM_API_KEY=sk-ant-...your key...
LLM_DEEP_MODEL=claude-sonnet-4-6
LLM_QUICK_MODEL=claude-haiku-4-5
```
> Cost for gold at hourly cadence is small (cents to ~$1 per analysis cycle). Use
> `claude-opus-4-8` as the deep model for higher quality at higher cost.

**B) OpenAI API key.**
```
LLM_PROVIDER=openai
LLM_BACKEND_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...your key...
LLM_DEEP_MODEL=gpt-5.5
LLM_QUICK_MODEL=gpt-5.4-mini
```

**C) Local Ollama (free, needs a capable GPU).**
```
LLM_PROVIDER=ollama
LLM_BACKEND_URL=http://localhost:11434/v1
LLM_DEEP_MODEL=llama3.1:70b
```

**D) Claude subscription via proxy (not recommended for 24/5).** Run a local proxy
([claude-max-api-proxy](https://github.com/sethschnrt/claude-max-api-proxy),
[meridian](https://github.com/rynfar/meridian)) and point `LLM_PROVIDER=openai` +
`LLM_BACKEND_URL=http://localhost:<port>/v1` at it. Unofficial, rate-limited, and from
**2026-06-15** subscription usage draws on a separate Agent-SDK credit pool.

### Data keys (may be needed on first real run)

TradingAgents' news/sentiment analysts may require a data vendor key (e.g.
`ALPHA_VANTAGE_API_KEY`). If a `signal` run errors on data fetch, add the key it asks
for to `.env`. The Technical analyst uses free Yahoo data and needs no key.

---

## Verify (do these in order)

```powershell
# 1. Broker link, demo guard, symbol specs, sizing sanity (no orders placed)
.\.venv\Scripts\python.exe -m goldtrader.cli connftest

# 2. Unit tests
.\.venv\Scripts\python.exe -m pytest -q

# 3. Multi-timeframe technical read (FREE, fast, no LLM)
.\.venv\Scripts\python.exe -m goldtrader.cli tech

# 4. LLM bias (slow tier) — refreshes only if cached bias is stale (~$1 when it runs)
.\.venv\Scripts\python.exe -m goldtrader.cli bias

# 5. One full supervisor tick (uses cached bias + technicals; respects DRY_RUN)
.\.venv\Scripts\python.exe -m goldtrader.cli run-once

# 6. Inspect state + journal
.\.venv\Scripts\python.exe -m goldtrader.cli status
```

---

## Run it

**Foreground (watch it work):**
```powershell
.\run_supervisor.ps1        # Ctrl+C stops gracefully
```

**As an auto-restarting Windows service (24/5):** install [NSSM](https://nssm.cc), then
run **as Administrator**:
```powershell
.\install_service.ps1
nssm start GoldTraderSupervisor
nssm start GoldTraderWatchdog
```

### Going live (only after demo validation)
1. Run on demo with `DRY_RUN=true` for a while; review `data/journal.sqlite` and logs.
2. Set `DRY_RUN=false` (still demo) to let it place **demo** orders. Watch closely.
3. Only then consider a live account — set `REQUIRE_DEMO=false` (the guard refuses live
   otherwise), start with tiny `RISK_PCT_PER_TRADE`, and keep the kill-switch handy.

---

## Dashboard (live monitoring)

A localhost-only **FastAPI dashboard** shows the bot's live state — loop countdowns (management,
entry, bias, reflection), open positions, performance KPIs, the macro-bias panel, the
self-heal/reflection progress, and a streaming log — plus manual controls (kill switch, refresh
bias, run reflection, run-once, stop/restart).

```powershell
.\run_dashboard.ps1     # then open http://127.0.0.1:8787
```

`.\run_supervisor.ps1` already launches the dashboard alongside the supervisor + watchdog. It reads
the supervisor's snapshot files **read-only** (never opens its own MT5 link), so it is safe to run
beside a live trader. It binds to loopback only — set `DASHBOARD_TOKEN` in `.env` if you ever expose it.

---

## Code knowledge graph (graphify)

The project keeps a queryable **knowledge graph of the codebase** (most-connected "god nodes",
community detection, module relationships) under `graphify-out/`. That folder is **not committed**
(it is regenerated locally and is in `.gitignore`), so a fresh clone has no graph yet — to make this
*graph memory* available you install graphify and build it:

```powershell
# 1. Install graphify (either)
uv tool install graphifyy
pip install graphifyy

# 2. Build the graph. Easiest inside Claude Code: just run the /graphify slash command
#    (it uses the Claude session for extraction). Standalone CLI needs a Gemini key:
$env:GEMINI_API_KEY = "...your key..."
graphify .                 # writes graphify-out/{graph.html, GRAPH_REPORT.md, graph.json}
graphify . --update        # refresh after code changes
graphify query "how does the reflection self-heal loop decide when to run?"
```

Open `graphify-out/graph.html` for the interactive map, or `graphify-out/GRAPH_REPORT.md` for the
god-nodes / communities summary.

---

## Safety controls

| Control | How |
|---|---|
| **Demo-only guard** | `REQUIRE_DEMO=true` — refuses to run on a real account. |
| **Dry-run** | `DRY_RUN=true` — full pipeline, validates orders, sends none. |
| **Kill switch** | `python -m goldtrader.cli kill` (creates `runtime/KILL_SWITCH`); `unkill` to clear. The loop idles while present. |
| **Daily loss halt** | `MAX_DAILY_LOSS_PCT` — stops trading for the day. |
| **Total loss kill** | `MAX_TOTAL_LOSS_PCT` — trips the kill switch and exits. |
| **Circuit breaker** | Repeated LLM/MT5/order failures → forces HOLD, cools down. |
| **Position cap** | `MAX_OPEN_POSITIONS` (default 3) + `MAX_TOTAL_RISK_PCT` total-risk cap. |

## Key files

| Path | Role |
|---|---|
| `goldtrader/config.py` | All settings (from `.env`). |
| `goldtrader/mt5/client.py` | MT5 connect, sizing, order placement. |
| `goldtrader/signals/adapter.py` `parser.py` | TradingAgents → Signal. |
| `goldtrader/risk/manager.py` | Sizing, ATR SL/TP, regime filter, gates. |
| `goldtrader/supervisor/loop.py` | The autonomous loop (state machine). |
| `goldtrader/healing/` | Retry, circuit breaker, heartbeat, watchdog. |
| `goldtrader/learning/` | Journal + feedback conditioning. |
| `goldtrader/safety/guards.py` | Demo guard, kill-switch, loss limits. |

## Disclaimer

TradingAgents is published "for research purposes... not financial, investment, or
trading advice." This wrapper inherits that disclaimer. Use at your own risk.
