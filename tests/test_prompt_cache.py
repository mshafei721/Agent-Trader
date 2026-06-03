from goldtrader.signals.adapter import cache_control_dict


def test_cache_control_5m():
    assert cache_control_dict("5m") == {"type": "ephemeral"}


def test_cache_control_1h():
    assert cache_control_dict("1h") == {"type": "ephemeral", "ttl": "1h"}


def test_patch_injects_cache_control(monkeypatch):
    """The adapter patch should add cache_control to the LLM's model_kwargs."""
    from goldtrader.config import Settings
    from goldtrader.signals.adapter import SignalAdapter

    # Fake TradingAgents anthropic_client module with a patchable get_llm.
    class _FakeLLM:
        def __init__(self):
            self.model_kwargs = {}

    class _FakeClient:
        def get_llm(self):
            return _FakeLLM()

    import types
    fake_mod = types.ModuleType("tradingagents.llm_clients.anthropic_client")
    fake_mod.AnthropicClient = _FakeClient
    import sys
    monkeypatch.setitem(sys.modules, "tradingagents.llm_clients.anthropic_client", fake_mod)

    s = Settings(llm_provider="anthropic", prompt_cache_enabled=True, prompt_cache_ttl="5m")
    SignalAdapter(s)._enable_prompt_caching()

    llm = _FakeClient().get_llm()
    assert llm.model_kwargs.get("cache_control") == {"type": "ephemeral"}
