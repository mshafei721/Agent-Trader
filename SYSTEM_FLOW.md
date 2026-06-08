# GoldTrader — System Flow

Two-speed autonomous XAUUSD trader: **deterministic multi-timeframe technical timing & protection** (fast — *leads*) + a **TradingAgents LLM macro bias** (slow — *soft-veto only*).

> **V7 in one line:** the offline lab proved gold has **no tradeable intraday edge**, so this is
> **risk-managed gold beta + a winter seasonal tilt + damp-only drawdown controllers + self-heal** —
> not an alpha engine. V7 added live-safety gates, gold-native feeds (FRED/COT/news), the validation
> lab, a transparency dashboard, and the sizing-overlay ensemble.

> Visual version: open [`system-flow.excalidraw`](system-flow.excalidraw) with the
> **Excalidraw** VS Code extension. The Mermaid diagram below renders on GitHub and in
> VS Code's Markdown preview (with a Mermaid extension).

```mermaid
flowchart TD
  W["Loop wakes every 60s"] --> S{"Safety gates<br/>kill-switch / demo-only<br/>market-open / loss caps"}
  S -->|every 60s| C1
  S -->|every 15 min| E1
  LLM["LLM bias (TradingAgents)<br/>market + news + gold context<br/>(FRED · COT · gold news), ~4h<br/>LONG / SHORT / FLAT"] -. soft-veto .-> E5

  subgraph FAST["FAST loop — manage open trades (60s)"]
    direction TB
    C1["Early loss-cut<br/>down 0.5R AND M15 flips -> close"] --> C2["Scale-out<br/>close half at +1R"] --> C3["Breakeven at +0.5R<br/>then Chandelier ATR trail<br/>(stop never loosens)"]
  end

  subgraph ENTRY["ENTRY eval — find a new trade (15 min)"]
    direction TB
    E1["1. H4 trend = direction<br/>up->buy / down->sell / flat->skip"] --> E2["2. H1 not opposing + ADX strong"] --> E3["3. M30 trigger<br/>MACD cross / momentum"] --> E4["4. Live-safety gates<br/>spread · news/calendar · session<br/>· weekend-flat · COT positioning"] --> E5["5. LLM bias soft-veto<br/>blocks only strong opposite (>=0.75)"] --> E6["6. Pyramid gate<br/>winners only, max 3"] --> E6b["7. Risk 0.5% (ATR) x sizing overlays<br/>winter · TSMOM regime · vol-target<br/>+ total-risk cap 1.5%"] --> E7["PLACE DEMO ORDER<br/>journal + MT5"]
  end

  BR["Hard stop-loss on broker<br/>(ultimate backstop)"] -.-> C3
  INFRA["Infra self-heal<br/>watchdog · circuit-breaker · reconnect · NSSM"] -. restarts .-> W
  E7 --> J["Journal (SQLite) of every trade"]

  subgraph REFLECT["REFLECT & SELF-HEAL — every 20 trades / daily"]
    direction LR
    RR["Read closed trades<br/>+ stats (R, PF, win rate)"] --> RH["Deterministic self-heal<br/>cut risk / pause (AUTO)"] --> RL["LLM review<br/>advisory suggestions"] --> RP["Report<br/>(data/reflections)"]
  end
  J --> RR
  RH -. throttle / pause .-> E6b
```

## How it works

**Every 60 seconds** the loop wakes, clears the safety gates, then runs **two lanes**:

### FAST lane — protect open trades (deterministic, no LLM)
1. **Early loss-cut** — if a trade is down ≥0.5R *and* the M15 trend flipped against it, close it now (don't ride to the full stop).
2. **Scale-out** — bank half the position at +1R.
3. **Breakeven + Chandelier trail** — at +0.5R the stop moves to entry, then trails to `swing extreme ∓ ATR×1.5`, ratcheting only in your favor.
- The broker-side **hard stop** is always present as the ultimate backstop.

### ENTRY lane — find a new trade (every 15 min)
1. **H4 trend** sets the direction (up→buy, down→sell, flat→skip).
2. **H1** must not oppose + **ADX** confirms trend strength.
3. **M30 trigger** (MACD cross / momentum) times the entry.
4. **Live-safety gates** — spread guard, news/economic-calendar blackout (fails *closed*), session window, weekend-flat, and CFTC COT positioning extreme; any one can skip the entry.
5. **LLM bias soft-veto** — the cached TradingAgents view (Market + News analysts with injected FRED/COT/gold-news context) can only *block* a strongly-opposite trade (conviction ≥0.75); it never forces one.
6. **Pyramid gate** — only add into winners, up to 3 positions.
7. **Risk sizing** 0.5% per trade (ATR stop) **× the sizing-overlay ensemble** (winter tilt · TSMOM regime · vol-target — all damp-only, compose with the self-heal scaler, never breach the ceiling) + a **1.5% total-risk cap**, then **place the order**.

### Self-heal & learning
Two kinds of self-healing run independently:

- **Infrastructure self-heal (always on):** retry/backoff, circuit-breaker (forces HOLD after repeated failures), heartbeat + watchdog (restarts a hung process), MT5 auto-reconnect, NSSM auto-restart. Heals *crashes/API/connection* problems.
- **Strategy self-heal + learning (reflect loop, every ~20 trades / daily):**
  1. Read all closed trades from the journal and compute stats (win rate, expectancy, profit factor, loss streaks, by-direction).
  2. **Deterministic self-heal (AUTO):** on losing streaks / negative expectancy it cuts position size or pauses new entries until recovery — it can only ever make trading *safer*.
  3. **LLM review (advisory):** an LLM analyses recent losers + current parameters and writes *suggestions* to `data/reflections/` — these are **never auto-applied**, and are gated until ≥20 closed trades. Suggestions are confined to a whitelist (`TUNABLE_BOUNDS`) that now includes the **sizing-overlay strengths** (so the loop can tune *how hard* each overlay damps, within tight rails — never enough to disable a drawdown control) but can never touch risk %, loss caps, or the demo guard.

Plus a **bias-aware exit** (fast loop): if the cached LLM bias turns against an open trade with enough conviction, the trade is closed/tightened. Run `python -m goldtrader.cli reflect` to produce a report on demand.

## Monitoring (dashboard)

A read-only **FastAPI dashboard** (`goldtrader/dashboard/`, `.\run_dashboard.ps1` → <http://127.0.0.1:8787>)
renders live state from the files the supervisor writes (heartbeat, account snapshot, journal,
bias, reflections) — it never opens its own MT5 link. V7 made it a **transparency layer for a
non-technical owner**: an honest 3-state mode banner (PAPER / DEMO-live-orders / LIVE-money, from the
broker's real `trade_mode`), a Safety-status traffic-light card, an equity + drawdown chart, a
plain-English log toggle, loop countdowns, open positions, the macro-bias + self-heal panels, and
**bounded controls** — kill switch, refresh bias, run reflection, run-once (behind a money-grade
modal), stop/restart, and a clamped **settings-write with risk presets** that can never touch the
hard floor (risk %, loss caps, demo guard, `DRY_RUN`).

## Regenerate the diagram

```powershell
.venv\Scripts\python.exe scripts\gen_flowchart.py   # rewrites system-flow.excalidraw
```
