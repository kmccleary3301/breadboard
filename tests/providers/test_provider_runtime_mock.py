from __future__ import annotations

from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import ProviderRuntimeContext, provider_registry


def test_local_deterministic_providers_have_self_contained_api_keys(monkeypatch) -> None:
    """Host-lane deterministic scenarios must not depend on inherited shell secrets."""

    monkeypatch.delenv("MOCK_API_KEY", raising=False)

    for route in ("mock/dev", "cli_mock/dev", "smoke/dev"):
        client_config = provider_router.create_client_config(route)
        assert client_config["api_key"] == "mock"


def test_mock_runtime_no_tools_emits_no_tool_calls() -> None:
    descriptor, model = provider_router.get_runtime_descriptor("mock/no_tools")
    runtime = provider_registry.create_runtime(descriptor)
    client = runtime.create_client(api_key="mock")
    context = ProviderRuntimeContext(session_state=object(), agent_config={})

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "Hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    assert len(result.messages) == 1
    assert result.messages[0].tool_calls == []
    assert result.raw_response.get("mode") == "no_tools"
