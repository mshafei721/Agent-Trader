"""Central configuration. All tunables live here; nothing is hardcoded elsewhere.

Reads from .env (via pydantic-settings). Access through `get_settings()` which
returns a cached singleton.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _blank_to_none(v):
    """Treat empty/whitespace env values as unset (None)."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

# Project root = the directory containing this package's parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
RUNTIME_DIR = PROJECT_ROOT / "runtime"


class Settings(BaseSettings):
    """Typed settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- MetaTrader 5 ----------
    mt5_login: int | None = Field(default=None)
    mt5_password: SecretStr | None = Field(default=None)
    mt5_server: str | None = Field(default=None)
    mt5_terminal_path: str | None = Field(default=None)

    # ---------- Symbol mapping ----------
    mt5_symbol_candidates: str = Field(default="XAUUSD,GOLD,XAUUSDm,XAUUSD.,GOLDmicro,#XAUUSD")
    yahoo_ticker: str = Field(default="GC=F")

    # ---------- LLM ----------
    # Default: Anthropic API (clean, natively supported by TradingAgents).
    # Leave llm_backend_url blank to use the provider's default endpoint.
    llm_provider: str = Field(default="anthropic")
    llm_backend_url: str = Field(default="")
    llm_api_key: SecretStr | None = Field(default=None)
    llm_deep_model: str = Field(default="claude-sonnet-4-6")
    llm_quick_model: str = Field(default="claude-haiku-4-5")
    llm_request_timeout_s: int = Field(default=180)
    # Prompt caching (Anthropic): caches repeated prompt prefixes WITHIN a single
    # analysis run. ttl "5m" (cheaper writes) or "1h". No cross-run benefit at >TTL cadence.
    prompt_cache_enabled: bool = Field(default=True)
    prompt_cache_ttl: Literal["5m", "1h"] = Field(default="5m")

    # ---------- Risk ----------
    risk_pct_per_trade: float = Field(default=0.5)
    max_open_positions: int = Field(default=3)
    max_total_risk_pct: float = Field(default=1.5)   # cap on summed open risk across positions
    pyramid_winners_only: bool = Field(default=True)  # only add to same-dir positions in profit
    max_daily_loss_pct: float = Field(default=2.0)
    max_total_loss_pct: float = Field(default=10.0)
    min_confidence: float = Field(default=0.55)
    atr_period: int = Field(default=14)
    atr_sl_mult: float = Field(default=1.5)
    atr_tp_mult: float = Field(default=2.0)
    sl_mode: Literal["atr", "fixed"] = Field(default="atr")
    stop_timeframe: str = Field(default="M30")   # ATR stop sized from this timeframe
    fixed_sl_points: int = Field(default=500)
    fixed_tp_points: int = Field(default=1000)
    deviation_points: int = Field(default=20)
    magic: int = Field(default=770077)

    # ---------- Regime filter (volatility ceiling + ADX floor used by RiskManager) ----------
    regime_filter_enabled: bool = Field(default=True)
    adx_period: int = Field(default=14)
    adx_min_trend: float = Field(default=18.0)
    atr_max_pct: float = Field(default=2.0)
    htf_trend_confirm: bool = Field(default=True)  # legacy; confluence now lives in TechnicalEngine

    # ---------- Two-speed strategy ----------
    bias_refresh_hours: float = Field(default=4.0)
    filter_timeframe: str = Field(default="H4")   # macro trend filter
    setup_timeframe: str = Field(default="H1")    # setup confirmation
    trigger_timeframe: str = Field(default="M30")  # entry trigger
    min_conviction: float = Field(default=0.55)   # (must-agree mode only; inactive in charts-lead)
    # Charts lead: the LLM only BLOCKS a trade when opposite with conviction >= this.
    bias_veto_conviction: float = Field(default=0.75)

    # ---------- Technical indicators ----------
    ema_fast: int = Field(default=20)
    ema_slow: int = Field(default=50)
    rsi_period: int = Field(default=14)
    macd_fast: int = Field(default=12)
    macd_slow: int = Field(default=26)
    macd_signal: int = Field(default=9)

    # ---------- Trade management (fast loop) ----------
    use_trailing: bool = Field(default=True)
    breakeven_at_r: float = Field(default=0.5)    # move SL to entry at +0.5R
    trail_atr_mult: float = Field(default=1.5)
    chandelier_lookback: int = Field(default=22)  # bars for swing extreme in the trail
    partial_tp_r: float = Field(default=1.0)      # close half at +1R (0 = off)
    # Early loss-cut: close a loser before the full stop if fast momentum flips against it
    cut_loss_enabled: bool = Field(default=True)
    cut_loss_at_r: float = Field(default=0.5)
    cut_loss_timeframe: str = Field(default="M15")
    cut_loss_signal: Literal["ema", "macd", "either"] = Field(default="either")
    manage_interval_seconds: int = Field(default=60)  # fast management cadence
    # Bias-aware exit: close/tighten an open trade when the CACHED LLM bias opposes it
    bias_exit_enabled: bool = Field(default=True)
    bias_exit_conviction: float = Field(default=0.6)   # lower than the 0.75 entry veto
    bias_exit_action: Literal["close", "tighten"] = Field(default="close")

    # ---------- Reflection / self-heal / learning ----------
    reflection_enabled: bool = Field(default=True)
    reflection_min_trades: int = Field(default=20)      # gate LLM suggestions until enough sample
    reflection_every_n_trades: int = Field(default=20)
    reflection_daily: bool = Field(default=True)
    reflection_use_llm: bool = Field(default=True)
    defensive_loss_streak: int = Field(default=4)       # consecutive losers -> halve risk
    defensive_pause_streak: int = Field(default=6)      # consecutive losers -> pause new entries

    # ---------- Cadence ----------
    interval_minutes: int = Field(default=15)
    skip_weekends: bool = Field(default=True)
    tick_stale_minutes: int = Field(default=10)

    # ---------- Safety ----------
    require_demo: bool = Field(default=True)
    dry_run: bool = Field(default=True)

    # ---------- Live-safety gates (V7 Phase 0) ----------
    # Spread guard: reject NEW entries when the live spread (broker points) exceeds this.
    spread_guard_enabled: bool = Field(default=True)
    max_entry_spread_points: float = Field(default=50.0)
    # News / economic-calendar blackout around high-impact USD events. FAILS CLOSED:
    # if the calendar is unavailable, default ET windows (08:30 / 14:00) are blacked out.
    news_blackout_enabled: bool = Field(default=True)
    news_blackout_pre_minutes: int = Field(default=30)
    news_blackout_post_minutes: int = Field(default=15)
    finnhub_api_key: SecretStr | None = Field(default=None)
    calendar_refresh_minutes: int = Field(default=60)
    # Session-time gate: only OPEN new entries during the London-NY overlap (UTC hours).
    session_filter_enabled: bool = Field(default=True)
    trading_session_start_utc: int = Field(default=7)
    trading_session_end_utc: int = Field(default=17)
    # Weekend flat: close all positions before the Friday close; grace after Sunday reopen.
    weekend_flat_enabled: bool = Field(default=True)
    weekend_flat_hour_utc: int = Field(default=20)
    weekend_flat_minute_utc: int = Field(default=30)
    monday_grace_minutes: int = Field(default=30)
    # Absolute lot cap: hard clamp on lot size regardless of equity growth.
    max_lots_absolute: float = Field(default=1.0)

    # ---------- Notifications ----------
    telegram_bot_token: SecretStr | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)

    # ---------- Dashboard (localhost monitoring UI) ----------
    dashboard_enabled: bool = Field(default=True)
    dashboard_host: str = Field(default="127.0.0.1")   # loopback only — never expose
    dashboard_port: int = Field(default=8787)
    dashboard_log_tail_lines: int = Field(default=200)
    dashboard_positions_cache_seconds: float = Field(default=4.0)
    # Optional token required on control (POST) actions. Empty/unset = no auth (loopback only).
    dashboard_token: SecretStr | None = Field(default=None)

    # ---------- Derived paths (not from env) ----------
    @property
    def symbol_candidates(self) -> list[str]:
        return [s.strip() for s in self.mt5_symbol_candidates.split(",") if s.strip()]

    @property
    def state_file(self) -> Path:
        return DATA_DIR / "state.json"

    @property
    def journal_db(self) -> Path:
        return DATA_DIR / "journal.sqlite"

    @property
    def bias_file(self) -> Path:
        return DATA_DIR / "bias.json"

    @property
    def reflections_dir(self) -> Path:
        return DATA_DIR / "reflections"

    @property
    def heartbeat_file(self) -> Path:
        return DATA_DIR / "heartbeat.json"

    @property
    def watchdog_heartbeat_file(self) -> Path:
        return DATA_DIR / "watchdog_heartbeat.json"

    @property
    def account_file(self) -> Path:
        # Account + open-positions snapshot the supervisor writes each cycle so the
        # dashboard can show live equity/positions WITHOUT opening its own MT5 link.
        return DATA_DIR / "account.json"

    @property
    def kill_switch_file(self) -> Path:
        return RUNTIME_DIR / "KILL_SWITCH"

    @property
    def circuit_breaker_file(self) -> Path:
        # Persisted circuit-breaker state so a crash/relaunch can't reset the failure count.
        return DATA_DIR / "circuit_breaker.json"

    @property
    def calendar_cache_file(self) -> Path:
        # Cached economic-calendar events (resilient across restarts; protects free-tier quota).
        return DATA_DIR / "calendar_cache.json"

    @property
    def log_file(self) -> Path:
        return LOGS_DIR / "goldtrader.jsonl"

    @property
    def ta_memory_dir(self) -> Path:
        # Keep TradingAgents memory inside the project for portability.
        return DATA_DIR / "ta_memory"

    @field_validator(
        "mt5_login", "mt5_password", "mt5_server", "mt5_terminal_path",
        "telegram_bot_token", "telegram_chat_id", "llm_api_key", "dashboard_token",
        "finnhub_api_key",
        mode="before",
    )
    @classmethod
    def _empty_optional(cls, v):
        return _blank_to_none(v)

    @field_validator("risk_pct_per_trade")
    @classmethod
    def _sane_risk(cls, v: float) -> float:
        if not 0 < v <= 10:
            raise ValueError("risk_pct_per_trade must be in (0, 10]")
        return v

    def ensure_dirs(self) -> None:
        for d in (DATA_DIR, LOGS_DIR, RUNTIME_DIR, self.ta_memory_dir, self.reflections_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
