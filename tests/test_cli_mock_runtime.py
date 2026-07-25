from __future__ import annotations

import json

from agentic_coder_prototype.provider.routing import ProviderDescriptor
from agentic_coder_prototype.provider.runtime import CliMockRuntime, ProviderRuntimeContext


def _runtime() -> CliMockRuntime:
    return CliMockRuntime(
        ProviderDescriptor(
            provider_id="cli_mock",
            runtime_id="cli_mock_chat",
            default_api_variant="mock",
            supports_native_tools=True,
            supports_streaming=False,
            supports_reasoning_traces=False,
            supports_cache_control=False,
            tool_schema_format="openai",
            base_url=None,
            api_key_env="MOCK_API_KEY",
            default_headers={},
        )
    )


def _assistant_call(name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "role": "assistant",
        "tool_calls": [{"function": {"name": name, "arguments": json.dumps(arguments or {})}}],
    }


def _invoke(runtime: CliMockRuntime, messages: list[dict[str, object]]):
    return runtime.invoke(
        client={"cli_mock": True},
        model="reference",
        messages=messages,
        tools=None,
        stream=False,
        context=ProviderRuntimeContext(session_state=None, agent_config={}),
    )


def test_cli_mock_runtime_emits_executable_write_and_shell_sequence() -> None:
    runtime = _runtime()

    todo_result = _invoke(runtime, [])
    assert todo_result.messages[0].tool_calls[0].name == "todo.write_board"

    write_result = _invoke(runtime, [_assistant_call("todo.write_board")])
    write_call = write_result.messages[0].tool_calls[0]
    assert write_call.name == "write"
    assert json.loads(write_call.arguments)["file_name"] == "bubble_sort.py"

    shell_result = _invoke(
        runtime,
        [_assistant_call("todo.write_board"), _assistant_call("write")],
    )
    shell_call = shell_result.messages[0].tool_calls[0]
    assert shell_call.name == "run_shell"
    assert json.loads(shell_call.arguments)["command"] == "python3 bubble_sort.py && make --version"

    final_todo_result = _invoke(
        runtime,
        [
            _assistant_call("todo.write_board"),
            _assistant_call("write"),
            _assistant_call("run_shell"),
        ],
    )
    final_todo_call = final_todo_result.messages[0].tool_calls[0]
    assert final_todo_call.name == "todo.write_board"
    final_todo_arguments = json.loads(final_todo_call.arguments)
    assert {todo["status"] for todo in final_todo_arguments["todos"]} == {"completed"}

    completion = _invoke(
        runtime,
        [
            _assistant_call("todo.write_board"),
            _assistant_call("write"),
            _assistant_call("run_shell"),
            _assistant_call("todo.write_board", final_todo_arguments),
        ],
    )
    assert completion.messages[0].content == "TASK COMPLETE"
    assert completion.messages[0].tool_calls == []
