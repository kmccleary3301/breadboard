from __future__ import annotations
import pytest

from breadboard_engine.provider_routing import provider_router
from breadboard_engine.provider_runtime import ProviderRuntimeContext, provider_registry

EXACT_ZERO_USAGE = {
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheReadTokens": 0,
    "cacheWriteTokens": 0,
    "totalTokens": 0,
}


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


@pytest.mark.parametrize(
    "model_id",
    ("mock/reference", "smoke/reference", "cli_mock/reference"),
)
def test_synthetic_runtimes_report_exact_zero_usage(model_id: str) -> None:
    descriptor, model = provider_router.get_runtime_descriptor(model_id)
    runtime = provider_registry.create_runtime(descriptor)
    client = runtime.create_client(api_key=descriptor.provider_id)
    context = ProviderRuntimeContext(session_state=object(), agent_config={})

    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": "Hello"}],
        tools=None,
        stream=False,
        context=context,
    )

    assert result.usage == EXACT_ZERO_USAGE
