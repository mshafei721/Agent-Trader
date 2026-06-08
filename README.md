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

## What it does — charts lead, the LLM advises (V7)

```
SLOW TIER (every ~4h, cents–$1):  TradingAgents LLM (market + news analysts)
   + injected gold context: FRED real-yield/dollar, CFTC COT, gold news
        → directional BIAS (long / short / flat, with conviction)  → data/bias.json
                                     │ (soft veto only)
FAST TIER (every 15 min, free):      ▼
  H4 trend → H1 setup (ADX) → M30 trigger          = deterministic technical entry
                                     │
   ┌─────────────────────────────────▼─────────────────────────────────────────┐
   │ live-safety gates:  spread guard · news/calendar blackout · session window │
   │                     · weekend-flat · COT positioning · daily-loss · kill    │
   │ LLM soft-veto:      block only a STRONGLY opposite bias (conviction ≥ 0.75) │
   │ risk sizing (ATR)  ×  sizing-overlay ensemble (winter · TSMOM · vol-target) │
   │ → place demo order on MT5 (or dry-run) → manage → journal the outcome       │
   └────────────────────────────────────────────────────────────────────────────┘
```

The **charts lead**: a free deterministic multi-timeframe engine times entries. The slow,
cheap **LLM only produces a bias** that can *soft-veto* a strongly-opposite trade (and
tighten/close opposing open trades) — it never forces a trade and an LLM/data outage never
freezes trading. Most 15-min ticks cost nothing; the LLM runs only when its cached bias is
older than `BIAS_REFRESH_HOURS`.

- **Gold-native inputs:** the Fundamentals *and* Sentiment analysts are dropped (gold has no
  earnings, and Reddit/StockTwits sentiment is noise for a commodity). The bias runs the
  **Market + News** analysts with `asset_type=commodity` and an injected macro context —
  **FRED** real yield (DFII10) + dollar (DTWEXBGS), **CFTC COT** positioning, and gold news.
- **Data ticker:** `GC=F` feeds TradingAgents; orders execute on the broker's `XAUUSD`.
- **Learning:** TradingAgents resolves each prior decision with realized return on the next
  refresh; our SQLite journal shrinks size after losing streaks; the reflection loop may
  tune a whitelisted set of parameters (advisory, bounded — never the hard floor).

### The honest verdict (measured, not marketed)

V7 added a full validation lab (below) and used it to test every credible strategy family on
5 years of gold — trend, all filters, mean-reversion, turn-of-month, a real-yield/dollar
regime, and time-series momentum. **None has a tradeable intraday edge after costs.** The one
edge that survives out-of-sample is gold's **winter (Nov→Apr) seasonal long-tilt**, and even
that is partly the secular bull. So this bot is **not an alpha engine** — it is **risk-managed
gold beta**: it rides gold's long-run uptrend with a seasonal tilt and **stacked, damp-only
drawdown controllers** (TSMOM regime + volatility targeting) plus the self-healing loop. The
validated effect of the overlays on a 22-year long-gold book: **max drawdown 53% → 30%,
Sharpe 0.60 → 0.68, out-of-sample**. The goal is *sustainable, survivable compounding* — not a
holy grail. More gates make it **more selective and lower-drawdown, never guaranteed profit.**

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

### Going live (gated — only after demo validation)
1. Run on demo with `DRY_RUN=true` for a while; review `data/journal.sqlite` and logs.
2. Set `DRY_RUN=false` (still demo) to let it place **demo** orders. Watch closely.
3. **Clear the go-live gate** (do not skip): a backtest with positive expectancy whose 95%
   bootstrap CI excludes zero *after costs*, **and** a **3-month demo forward-test** (win 40–55%,
   profit factor 1.4–1.85, max drawdown <15%). Track it with `scripts/forward_test.py` (below).
   *Honest note: the lab found no intraday alpha, so the profitability half of this gate may
   never be met — in which case the bot stays a demo / risk-managed-beta exercise.*
4. Only then consider a live account — set `REQUIRE_DEMO=false` (the guard refuses live
   otherwise), start with tiny `RISK_PCT_PER_TRADE`, and keep the kill-switch handy.

---

## Validation lab & forward-test (V7)

Before risking anything, **prove the edge offline**. The lab (`goldtrader/backtest/`) replays the
*exact* live technical + risk + management code bar-by-bar over years of free **Dukascopy** gold
history (no look-ahead, modeled spread/slippage), and reports expectancy with a bootstrap 95% CI,
win-rate CI, profit factor, max drawdown, Monte-Carlo drawdown, and **Sharpe/Sortino/Calmar**.

```powershell
# Import multi-year history once (free, no key) — 5yr M30 for the engine replay
.\.venv\Scripts\python.exe -m goldtrader.cli backtest-import
.\.venv\Scripts\python.exe -m goldtrader.cli backtest         # expectancy + CIs + risk metrics
.\.venv\Scripts\python.exe -m goldtrader.cli walkforward      # anchored out-of-sample selection

# Strategy-research experiments (22yr daily; print findings, change no code):
.\.venv\Scripts\python.exe scripts\exp_seasonal.py            # winter / turn-of-month / September
.\.venv\Scripts\python.exe scripts\exp_tsmom.py               # time-series momentum vs buy&hold
.\.venv\Scripts\python.exe scripts\exp_ensemble.py            # the overlay stack vs buy&hold
```

**Live forward-test scorecard** — grades the running demo bot against the go-live gate, counting
only trades that close after a stamped start (so pre-V7 trades don't pollute it):

```powershell
.\.venv\Scripts\python.exe scripts\forward_test.py --start    # stamp the start (now)
.\.venv\Scripts\python.exe scripts\forward_test.py            # status vs the gate (needs >=30 trades)
```

---

## Dashboard (live monitoring)

A localhost-only **FastAPI dashboard** built for **transparency to a non-technical owner**:

- A full-width **mode banner** with three *honest* states — **PAPER (dry-run)** / **DEMO (live
  orders, no real money)** / **LIVE MONEY** — driven by the broker's real `trade_mode`, so a
  demo account is never mislabelled "live".
- A **Safety status** card (3 traffic lights: bot health · trading mode · today's loss guard).
- An **equity + drawdown** chart and performance KPIs (with a forward-test track record).
- A **plain-English** toggle on the live log that narrates events as sentences.
- Loop countdowns, open positions, the macro-bias panel, and the self-heal/reflection panel.
- **Bounded controls**: kill switch, refresh bias, run reflection, run-once (behind a money-grade
  confirmation modal), stop/restart, and a **settings-write with risk presets**
  (Conservative/Balanced/Aggressive) — clamped server-side to safe bounds, and **never** able to
  touch the hard floor (risk %, loss caps, demo guard, `DRY_RUN`).

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
| **Circuit breaker** | Repeated LLM/MT5/order failures → forces HOLD, cools down. **Persisted** to disk so a crash/relaunch can't reset the failure count. |
| **Position cap** | `MAX_OPEN_POSITIONS` (default 3) + `MAX_TOTAL_RISK_PCT` total-risk cap. |
| **Spread guard** | `MAX_ENTRY_SPREAD_POINTS` — rejects new entries when the live spread is too wide (news/rollover). |
| **News/calendar blackout** | Blocks entries around high-impact USD events; **fails CLOSED** to default ET windows if the calendar is unavailable. |
| **Session window** | `SESSION_FILTER_ENABLED` + `TRADING_SESSION_*_UTC` — restrict new entries to chosen hours (set `false` for 24/5). |
| **Weekend-flat** | Flattens positions before the Friday close; grace period after Sunday reopen. |
| **COT positioning gate** | Blocks entries that chase a crowded CFTC managed-money extreme. |
| **Absolute lot cap** | `MAX_LOTS_ABSOLUTE` — hard clamp so growing equity can't silently scale notional. |
| **Sizing overlays** | Winter-tilt × TSMOM-regime × vol-target — **damp-only** size factors that compose with the self-heal scaler and never breach the risk ceiling. |

## Key files

| Path | Role |
|---|---|
| `goldtrader/config.py` | All settings (from `.env`). |
| `goldtrader/mt5/client.py` | MT5 connect, sizing, order placement, `trade_mode`. |
| `goldtrader/signals/adapter.py` `parser.py` | Gold-native TradingAgents (market+news, commodity context) → Signal. |
| `goldtrader/feeds/` | Gold-native data: FRED macro, CFTC COT, economic calendar, gold news. |
| `goldtrader/strategy/` | Technical engine, exits, bias veto, `overlays.py` + `seasonal_bias.py` (sizing ensemble). |
| `goldtrader/risk/manager.py` | Sizing, ATR SL/TP, regime filter, gates. |
| `goldtrader/backtest/` | Validation lab: replay, walk-forward, stats, Dukascopy import, research experiments. |
| `goldtrader/supervisor/loop.py` | The autonomous loop (state machine + entry gauntlet). |
| `goldtrader/healing/` | Retry, circuit breaker (persisted), heartbeat, watchdog. |
| `goldtrader/learning/` | Journal + feedback conditioning + reflection (bounded tuning). |
| `goldtrader/dashboard/` | FastAPI monitor UI + `settings_io.py` (bounded settings-write + presets). |
| `goldtrader/safety/guards.py` | Demo guard, kill-switch, loss limits, spread/session gates. |
| `scripts/forward_test.py` | Forward-test scorecard vs the go-live gate. |

## Disclaimer

TradingAgents is published "for research purposes... not financial, investment, or
trading advice." This wrapper inherits that disclaimer. Use at your own risk.
