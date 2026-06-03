"""Bridge: TradingAgents analysis -> structured Signal.

Constructs the TradingAgentsGraph once with the gold-appropriate analyst set
(fundamentals excluded) and an LLM endpoint pulled from our config (swappable
proxy / API key / Ollama). Each call runs propagate() and parses the decision.
"""
from __future__ import annotations

from typing import Optional

from ..config import Settings
from ..logging_setup import get_logger
from ..types import Action, Signal, today_iso
from .parser import parse_signal

log = get_logger("goldtrader.signals")

# Gold has no company fundamentals -> exclude the fundamentals analyst.
GOLD_ANALYSTS = ["market", "news", "social"]


def cache_control_dict(ttl: str) -> dict:
    """Build the Anthropic automatic-caching control block for a TTL."""
    cc = {"type": "ephemeral"}
    if ttl == "1h":
        cc["ttl"] = "1h"
    return cc


class SignalAdapter:
    def __init__(self, settings: Settings):
        self.s = settings
        self._graph = None  # lazy: importing TradingAgents is heavy

    def _build_config(self) -> dict:
        from tradingagents.default_config import DEFAULT_CONFIG

        cfg = DEFAULT_CONFIG.copy()
        cfg["llm_provider"] = self.s.llm_provider
        # Blank backend_url -> use the provider's default endpoint (e.g. Anthropic API).
        cfg["backend_url"] = self.s.llm_backend_url.strip() or None
        cfg["deep_think_llm"] = self.s.llm_deep_model
        cfg["quick_think_llm"] = self.s.llm_quick_model
        cfg["max_debate_rounds"] = 1
        cfg["max_risk_discuss_rounds"] = 1
        # Persist decision-log memory inside the project (enables auto-reflection
        # across runs for the same ticker).
        cfg["memory_log_path"] = str(self.s.ta_memory_dir / "trading_memory.md")
        cfg["results_dir"] = str(self.s.ta_memory_dir / "logs")
        cfg["data_cache_dir"] = str(self.s.ta_memory_dir / "cache")
        return cfg

    def _ensure_graph(self):
        if self._graph is None:
            import os

            from tradingagents.graph.trading_graph import TradingAgentsGraph
            from tradingagents.llm_clients.api_key_env import get_api_key_env

            # Export the API key under the env var the selected provider expects
            # (e.g. anthropic -> ANTHROPIC_API_KEY, openai -> OPENAI_API_KEY).
            # This keeps the provider swap pure config. A key already present in
            # the environment is respected (setdefault).
            key_env = get_api_key_env(self.s.llm_provider)
            if key_env and self.s.llm_api_key is not None:
                os.environ.setdefault(key_env, self.s.llm_api_key.get_secret_value())
            self._enable_prompt_caching()
            self._graph = TradingAgentsGraph(
                selected_analysts=GOLD_ANALYSTS,
                debug=False,
                config=self._build_config(),
            )
            log.info("trading_graph_built", analysts=GOLD_ANALYSTS,
                     backend_url=self.s.llm_backend_url)
        return self._graph

    def _enable_prompt_caching(self) -> None:
        """Inject Anthropic automatic prompt caching into every LLM the graph builds.

        TradingAgents' AnthropicClient.get_llm() forwards only a fixed kwarg list, so
        we patch it to add model_kwargs={"cache_control": ...}. Caches repeated prompt
        prefixes within a single analysis run (no cross-run benefit at >TTL cadence).
        Idempotent; only active for the anthropic provider.
        """
        if not (self.s.prompt_cache_enabled and self.s.llm_provider == "anthropic"):
            return
        try:
            from tradingagents.llm_clients import anthropic_client as ac
        except Exception:  # noqa: BLE001
            return
        if getattr(ac.AnthropicClient, "_gt_cache_patched", False):
            return
        cc = cache_control_dict(self.s.prompt_cache_ttl)
        _orig = ac.AnthropicClient.get_llm

        def _patched(client_self):
            llm = _orig(client_self)
            try:
                mk = dict(getattr(llm, "model_kwargs", {}) or {})
                mk.setdefault("cache_control", cc)
                llm.model_kwargs = mk
            except Exception as exc:  # noqa: BLE001
                log.warning("prompt_cache_inject_failed", error=str(exc))
            return llm

        ac.AnthropicClient.get_llm = _patched
        ac.AnthropicClient._gt_cache_patched = True
        log.info("prompt_caching_enabled", ttl=self.s.prompt_cache_ttl)

    def get_signal(self, run_date: Optional[str] = None) -> Signal:
        run_date = run_date or today_iso()
        graph = self._ensure_graph()
        log.info("propagate_start", ticker=self.s.yahoo_ticker, date=run_date)
        try:
            final_state, decision = graph.propagate(self.s.yahoo_ticker, run_date, asset_type="stock")
        except Exception as exc:  # noqa: BLE001
            log.error("propagate_failed", error=str(exc))
            raise
        # `decision` is a 5-tier rating label (Buy/Overweight/Hold/Underweight/Sell);
        # the full reasoning lives in final_state["final_trade_decision"].
        rating_text = decision if isinstance(decision, str) else str(decision)
        reasoning = ""
        if isinstance(final_state, dict):
            reasoning = str(final_state.get("final_trade_decision", "") or "")
        log.info("raw_rating", rating=rating_text)
        signal = parse_signal(rating_text, reasoning, run_date)
        log.info(
            "signal_parsed",
            action=signal.action.value,
            confidence=round(signal.confidence, 2),
            hash=signal.dedup_hash(),
        )
        return signal

    @staticmethod
    def to_intent_side(signal: Signal) -> Optional[Action]:
        if signal.action in (Action.BUY, Action.SELL):
            return signal.action
        return None
