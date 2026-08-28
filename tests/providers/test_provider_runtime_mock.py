from __future__ import annotations
import pytest

from breadboard_engine.provider_routing import provider_router
from breadboard_engine.provider_runtime import ProviderRuntimeContext, provider_registry
from breadboard_engine.provider.normalizer import normalize_provider_result


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


def test_cli_mock_runtime_emits_contract_valid_tool_sequence() -> None:
    descriptor, model = provider_router.get_runtime_descriptor("cli_mock/reference")
    runtime = provider_registry.create_runtime(descriptor)
    client = runtime.create_client(api_key=descriptor.provider_id)
    context = ProviderRuntimeContext(session_state=object(), agent_config={})

    def invoke(messages):
        result = runtime.invoke(
            client=client,
            model=model,
            messages=messages,
            tools=None,
            stream=False,
            context=context,
        )
        normalize_provider_result(result)
        return result.messages[0].tool_calls[0]

    todo_call = invoke([{"role": "user", "content": "Hello"}])
    assert todo_call.id == "cli-mock-call-1-todo-write_board"

    write_call = invoke(
        [{"role": "assistant", "tool_calls": [{"name": "todo.write_board"}]}]
    )
    assert write_call.id == "cli-mock-call-2-write"
    assert write_call.parsed_arguments["filePath"] == "bubble_sort.py"

    shell_call = invoke(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_call", "name": "todo.write_board"},
                    {"type": "tool_call", "name": "Write"},
                ],
            }
        ]
    )
    assert shell_call.id == "cli-mock-call-3-run_shell"
    assert shell_call.parsed_arguments["command"] == "python3 bubble_sort.py"
