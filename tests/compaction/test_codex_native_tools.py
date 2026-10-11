"""Codex 0.139.0 schema and executable format checks against pinned stock source."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from breadboard_engine.compilation.tool_yaml_loader import load_yaml_tools
from breadboard_engine.execution.codex_exec import (
    CodexExecManager, execute_codex_tool, format_exec_output, truncate_output, view_image,
)
from breadboard_engine.provider.adapters import OpenAIAdapter

ROOT = Path(__file__).resolve().parents[2]
RECORDINGS = ROOT / "tests/compaction/oracles/codex@0.139.0/recordings-gpt55"
DEFS = ROOT / "implementations/tools/defs_codex_139"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def test_actual_model_stock_recordings_cover_three_scenarios():
    assert {path.stem for path in RECORDINGS.glob("*.jsonl")} == {
        "overflow_terminal", "pre_turn_and_second_compaction", "mid_turn_compaction",
    }


@pytest.mark.parametrize("recording", sorted(RECORDINGS.glob("*.jsonl")), ids=lambda p: p.stem)
def test_exact_catalog_matches_every_stock_recorded_request(recording):
    adapter = OpenAIAdapter()
    tools = [adapter.translate_tool_to_native_schema(t) for t in load_yaml_tools(str(DEFS)).tools]
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext

    context = ProviderRuntimeContext(None, {"provider_tools": {"openai": {
        "responses_deferred_tools": ["multi_agent_v1"],
    }}})
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    tools = runtime._convert_tools_to_responses(tools, context)
    assert [t.get("name", t["type"]) for t in tools] == [
        "exec_command", "write_stdin", "update_plan", "request_user_input", "apply_patch",
        "view_image", "get_goal", "create_goal", "update_goal", "tool_search", "web_search",
    ]
    seen = 0
    for row in recording.read_text().splitlines():
        stock = json.loads(row)["json"].get("tools")
        if stock:
            assert canonical(tools) == canonical(stock)
            seen += 1
    assert seen


def test_other_catalogs_keep_the_original_translation():
    # No responses_tool_schema field was added to any existing definition.
    adapter = OpenAIAdapter()
    for catalog in ("defs", "defs_oc", "defs_omo", "defs_cc"):
        for tool in load_yaml_tools(str(ROOT / "implementations/tools" / catalog)).tools:
            before = deepcopy(tool)
            schema = adapter.translate_tool_to_native_schema(tool)
            assert schema["type"] == "function"
            assert "function" in schema
            assert tool == before


@pytest.mark.parametrize(("raw", "budget", "expected"), [
    ("abcdefgh", 2, "abcdefgh"),
    ("abcdefghi", 2, "Total output lines: 1\n\nabcd…1 tokens truncated…fghi"),
    ("a\nb\nc\n", 1, "Total output lines: 3\n\na\n…1 tokens truncated…c\n"),
    ("🙂🙂🙂", 2, "Total output lines: 1\n\n🙂…1 tokens truncated…🙂"),
    ("a🙂b🙂c", 1, "Total output lines: 1\n\na…2 tokens truncated…c"),
    ("hello", 0, "Total output lines: 1\n\n…2 tokens truncated…"),
    ("", 0, ""),
    ("a\u2028bcccc", 1, "Total output lines: 1\n\na…2 tokens truncated…cc"),
])
def test_stock_utf8_token_truncation_boundaries(raw, budget, expected):
    # utils/string/src/truncate.rs:38-152 and output-truncation/src/lib.rs:12-25.
    assert truncate_output(raw, budget) == expected


def test_stock_exit_output_format():
    # core/src/tools/context.rs:409-434 includes original count even without truncation.
    assert format_exec_output(b"test", chunk_id="abc123", wall_time=0.125,
                              exit_code=7, session_id=None) == (
        "Chunk ID: abc123\nWall time: 0.1250 seconds\nProcess exited with code 7\n"
        "Original token count: 1\nOutput:\ntest"
    )


def test_stock_running_output_format_and_policy_cap():
    assert format_exec_output(b"012345678", chunk_id="000001", wall_time=1.5,
                              exit_code=None, session_id=1234,
                              max_output_tokens=99999, policy_tokens=2) == (
        "Chunk ID: 000001\nWall time: 1.5000 seconds\nProcess running with session ID 1234\n"
        "Original token count: 3\nOutput:\nTotal output lines: 1\n\n0123…1 tokens truncated…5678"
    )


@pytest.fixture
def manager(tmp_path):
    value = CodexExecManager(str(tmp_path), truncation_policy={"mode": "bytes", "limit": 10000}, process_id=lambda: 1234, chunk_id=lambda: "abc123")
    yield value
    value.close()


def observable_without_clock(output):
    # Validate the one host-clock field, then compare every remaining byte.
    match = re.fullmatch(r"Chunk ID: abc123\nWall time: ([0-9]+\.[0-9]{4}) seconds\n(.*)", output, re.S)
    assert match, output
    assert float(match.group(1)) >= 0
    return match.group(2)


@pytest.mark.parametrize("code", [0, 7, 126])
def test_real_pipe_exit_status_and_combined_output(manager, code):
    value = manager.exec_command({"cmd": f"printf out; printf err >&2; exit {code}", "login": False})
    assert value["exit"] == code
    assert value["session_id"] is None
    assert observable_without_clock(value["__mvi_text_output"]) == (
        f"Process exited with code {code}\nOriginal token count: 2\nOutput:\nouterr"
    )
    assert manager.sessions == {}


def test_real_session_continuation_consumes_only_new_output(manager):
    first = manager.exec_command({"cmd": "printf first; sleep 0.4; printf last; exit 9", "login": False,
                                  "yield_time_ms": 1})
    assert first["session_id"] == 1234  # Effective minimum yield is 250 ms.
    assert observable_without_clock(first["__mvi_text_output"]) == (
        "Process running with session ID 1234\nOriginal token count: 2\nOutput:\nfirst"
    )
    last = manager.write_stdin({"session_id": 1234, "yield_time_ms": 1})
    assert observable_without_clock(last["__mvi_text_output"]) == (
        "Process exited with code 9\nOriginal token count: 1\nOutput:\nlast"
    )
    assert manager.write_stdin({"session_id": 1234})["__mvi_text_output"] == (
        "write_stdin failed: Unknown process id 1234"
    )


def test_plain_pipes_close_stdin_even_while_process_runs(manager):
    manager.exec_command({"cmd": "sleep 1", "login": False, "yield_time_ms": 250})
    assert manager.write_stdin({"session_id": 1234, "chars": "x"})["__mvi_text_output"] == (
        "write_stdin failed: stdin is closed for this session; rerun exec_command with tty=true to keep stdin open"
    )


def test_real_pty_accepts_input_and_has_controlling_terminal(manager):
    first = manager.exec_command({"cmd": "read line; printf 'got:%s' \"$line\"", "login": False,
                                  "tty": True, "yield_time_ms": 250})
    assert first["session_id"] == 1234
    last = manager.write_stdin({"session_id": 1234, "chars": "yes\n", "yield_time_ms": 1000})
    assert last["exit"] == 0
    assert observable_without_clock(last["__mvi_text_output"]) == (
        "Process exited with code 0\nOriginal token count: 3\nOutput:\nyes\r\ngot:yes"
    )


@pytest.mark.parametrize("tty", [False, True])
def test_real_exec_works_when_host_descriptors_exceed_fd_setsize(manager, tty):
    # A long-lived host can hold more than FD_SETSIZE (1024) descriptors, so
    # the exec readers must not rely on select().
    import os
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < 1200:
        pytest.skip(f"RLIMIT_NOFILE hard limit {hard} cannot exceed FD_SETSIZE")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 1200), hard))
    held = []
    try:
        while not held or held[-1] < 1100:
            held.append(os.open(os.devnull, os.O_RDONLY))
        if tty:
            first = manager.exec_command({"cmd": "read line; printf 'got:%s' \"$line\"", "login": False,
                                          "tty": True, "yield_time_ms": 250})
            assert first["session_id"] == 1234
            value = manager.write_stdin({"session_id": 1234, "chars": "yes\n", "yield_time_ms": 1000})
        else:
            value = manager.exec_command({"cmd": "printf out; exit 7", "login": False})
        assert value["exit"] == (0 if tty else 7)
    finally:
        for descriptor in held:
            os.close(descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_noninteractive_user_input_and_plan_have_stock_strings():
    owner = SimpleNamespace(config={})
    plan = {"plan": [{"step": "test", "status": "in_progress"}]}
    assert execute_codex_tool(owner, "update_plan", plan)["__mvi_text_output"] == "Plan updated"
    assert owner._codex_update_plan_state == plan
    assert execute_codex_tool(owner, "request_user_input", {"questions": []})["__mvi_text_output"] == (
        "request_user_input is unavailable in Default mode"
    )
    assert execute_codex_tool(owner, "shell", {})["__mvi_text_output"] == "unsupported call: shell"


def test_escalation_request_has_stock_never_approval_rejection(manager):
    assert manager.exec_command({"cmd": "printf not-run", "sandbox_permissions": "require_escalated"})[
        "__mvi_text_output"
    ] == "approval policy is Never; reject command — you cannot ask for escalated permissions if the approval policy is Never"


def test_view_image_preserves_stock_png_bytes_and_input_item(tmp_path):
    from PIL import Image

    path = tmp_path / "test.png"
    Image.new("RGB", (2, 3), "red").save(path)
    result = view_image(str(tmp_path), {"path": "test.png"})
    expected = [{"type": "input_image", "detail": "high",
                 "image_url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}]
    assert result["__mvi_content_output"] == expected
    message = OpenAIAdapter().create_tool_result_message("image-call", "view_image", result)
    assert message["role"] == "tool_result"
    assert message["content"][0]["payload"]["output"] == expected


def test_view_image_large_png_uses_stock_triangle_resize(tmp_path):
    from PIL import Image
    from io import BytesIO

    path = tmp_path / "large.png"
    Image.new("RGB", (1024, 4096), "red").save(path)
    result = view_image(str(tmp_path), {"path": "large.png"})
    payload = result["__mvi_content_output"][0]["image_url"].split(",", 1)[1]
    assert Image.open(BytesIO(base64.b64decode(payload))).size == (512, 2048)


def test_image_output_reaches_responses_without_string_conversion(tmp_path):
    from breadboard_engine.messaging.message_formatter import MessageFormatter
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime
    from breadboard_engine.provider.runtime import ProviderRuntimeContext
    from breadboard_engine.provider.contract_requests import normalize_request_messages
    from breadboard_engine.provider.contract_wire import ProviderContractError
    from breadboard_engine.state.session_state import SessionState

    config = {"provider_tools": {"openai": {"responses_tool_output_items": True}}}
    context = ProviderRuntimeContext(SessionState(str(tmp_path), "image", config), config)

    output = [{"type": "input_image", "detail": "high", "image_url": "data:image/png;base64,AA=="}]
    result = {"__mvi_native_responses_output": True, "__mvi_content_output": output}
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    for message in (
        OpenAIAdapter().create_tool_result_message("image-call", "view_image", result),
        MessageFormatter(str(tmp_path)).create_tool_result_entry("view_image", result, call_id="image-call"),
    ):
        canonical = normalize_request_messages([message])
        assert runtime._convert_messages_to_input(canonical, context=context) == [
            {"type": "function_call_output", "call_id": "image-call", "output": output}
        ]
        with pytest.raises(ProviderContractError, match="not enabled"):
            runtime._convert_messages_to_input(canonical)


def test_router_native_image_carrier_preserves_identity_and_scalar_wire(tmp_path):
    from breadboard_engine.provider.adapters import OpenRouterAdapter
    from breadboard_engine.provider.contract_requests import normalize_request_messages
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.state.session_state import SessionState

    adapter = OpenRouterAdapter()
    output = [{"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "high"}]
    message = adapter.create_tool_result_message(
        "router-image", "view_image",
        {"__mvi_native_responses_output": True, "__mvi_content_output": output},
    )
    assert message["content"][0]["provider_id"] == "openrouter"
    assert "call_id" not in message
    config = {"provider_tools": {"openrouter": {"responses_tool_output_items": True}}}
    context = ProviderRuntimeContext(SessionState(str(tmp_path), "router", config), config)
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openrouter", runtime_id="openai_responses"))
    assert runtime._convert_messages_to_input(normalize_request_messages([message]), context=context) == [
        {"type": "function_call_output", "call_id": "router-image", "output": output},
    ]
    assert adapter.create_tool_result_message("scalar", "exec_command", "plain") == {
        "role": "tool", "tool_call_id": "scalar", "name": "exec_command",
        "content": "plain", "call_id": "scalar",
    }
    from unittest.mock import Mock
    from breadboard_engine.provider.invoker import ProviderInvoker

    context.extra.update(session_id="router-session", input_id="router-input", turn_id="router-turn")
    observed = {}
    response = SimpleNamespace(id="router-response", status="completed", output=[], usage=None)
    client = SimpleNamespace(responses=SimpleNamespace(
        create=lambda **body: (observed.update(body) or response)))
    invoker = ProviderInvoker(
        provider_metrics=Mock(), route_health=Mock(is_circuit_open=Mock(return_value=False)),
        logger_v2=SimpleNamespace(run_dir=None), md_writer=SimpleNamespace(system=lambda message: message),
        retry_with_fallback=Mock(return_value=None), update_health_metadata=Mock(),
        set_last_latency=Mock(), set_html_detected=Mock(),
    )
    result, used_stream = invoker.invoke(
        runtime=runtime, client=client, model="gpt-5.5", send_messages=[message],
        tools_schema=None, stream_responses=False, runtime_context=context,
        session_state=context.session_state, markdown_logger=Mock(), turn_index=0, route_id="gpt-5.5",
    )
    assert used_stream is False
    assert result.metadata["provider_exchange"]["terminal"]["finish_reason"] == "stop"
    assert observed["input"] == [
        {"type": "function_call_output", "call_id": "router-image", "output": output},
    ]


def test_regular_content_marker_keeps_other_profiles_legacy_wire(tmp_path):
    from breadboard_engine.messaging.message_formatter import MessageFormatter
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime

    result = {"__mvi_content_output": [{"type": "input_image", "image_url": "legacy"}],
              "__mvi_text_output": "legacy text"}
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    for message in (
        OpenAIAdapter().create_tool_result_message("legacy-call", "view_image", result),
        MessageFormatter(str(tmp_path)).create_tool_result_entry("view_image", result, call_id="legacy-call"),
    ):
        assert message["role"] == "tool"
        assert message["content"] == "legacy text"
        assert runtime._convert_messages_to_input([message]) == [
            {"type": "function_call_output", "call_id": "legacy-call", "output": "legacy text"},
        ]


@pytest.mark.parametrize("item", [
    {"type": "input_image", "image_url": 3},
    {"type": "input_image", "image_url": "x", "detail": "invented"},
    {"type": "input_image", "image_url": "x", "detail": []},
    {"type": "input_image", "image_url": "x", "unexpected": True},
    {"type": "input_text", "text": 3},
    {"type": "arbitrary", "text": "x"},
])
def test_native_image_carrier_rejects_non_stock_items(item):
    from breadboard_engine.provider.contract_requests import normalize_request_messages
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message
    from breadboard_engine.provider.contract_wire import ProviderContractError

    with pytest.raises(ProviderContractError):
        normalize_request_messages([responses_image_tool_result_message("image-call", [item])])


def test_namespace_dispatch_and_replay_are_profile_gated():
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext

    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    item = {"name": "spawn_agent", "namespace": "multi_agent_v1"}
    native = ProviderRuntimeContext(None, {"provider_tools": {"openai": {"responses_namespaces": True}}})
    ordinary = ProviderRuntimeContext(None, {})
    assert runtime._native_function_name(item, native) == "multi_agent_v1.spawn_agent"
    assert runtime._native_function_name(item, ordinary) == "spawn_agent"
    messages = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "call-1", "name": "multi_agent_v1.spawn_agent", "arguments": "{}"},
    ]}]
    expected = [{"type": "function_call", "call_id": "call-1", "name": "spawn_agent",
                 "namespace": "multi_agent_v1", "arguments": "{}"}]
    assert runtime._convert_messages_to_input(messages, include_tool_calls=True, context=native) == expected
    expected[0].pop("namespace")
    expected[0]["name"] = "multi_agent_v1.spawn_agent"
    assert runtime._convert_messages_to_input(messages, include_tool_calls=True, context=ordinary) == expected


def test_missing_image_helper_fails_with_explicit_build_command(tmp_path, monkeypatch):

    path = tmp_path / "test.png"
    path.write_bytes(b"not-decoded-when-helper-missing")
    from breadboard_engine.execution import codex_native

    monkeypatch.setattr(codex_native, "native_binary", lambda helper: tmp_path / "missing-helper")
    assert view_image(str(tmp_path), {"path": "test.png"})["__mvi_text_output"] == (
        "Codex image helper is not built; run `python -m breadboard_engine.execution.codex_native build image`."
    )


def test_resized_image_request_bytes_equal_live_stock(tmp_path):
    # This executes the real pinned Codex binary's view_image, not a codec mock.
    import hashlib
    from PIL import Image
    from breadboard_engine.compaction_lanes.codex_lane import BINARY, EngineMockProvider, run_stock

    if not BINARY.is_file():
        pytest.skip("Pinned @openai/codex@0.139.0 stock package is required")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image_path = workspace / "large.png"
    Image.new("RGB", (1024, 4096), (23, 65, 129)).save(image_path)
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "view_image", "call_id": "resize-oracle",
         "arguments": json.dumps({"path": str(image_path)}), "usage": usage},
        {"type": "text", "text": "Image inspected.", "usage": usage},
    ]
    recording = tmp_path / "stock-resize.jsonl"
    with EngineMockProvider(script=script, record_path=recording) as provider:
        result = run_stock("mid_turn_compaction", workspace, tmp_path / "stock", provider.base_url,
                           {"model": "gpt-5.5", "window": 272000, "limit": 244800})
    assert all(r["returncode"] == 0 for r in result["runs"])
    outputs = [i["output"] for row in recording.read_text().splitlines()
               for i in json.loads(row)["json"].get("input", [])
               if i.get("type") == "function_call_output" and i.get("call_id") == "resize-oracle"]
    bb_output = view_image(str(workspace), {"path": str(image_path)})["__mvi_content_output"]
    assert outputs == [bb_output]
    print(f"stock resize output byte-equal: sha256={hashlib.sha256(canonical(bb_output)).hexdigest()}; recording={recording}")


def test_stock_shell_detection_and_fallback(tmp_path):
    from breadboard_engine.execution.codex_exec import shell_argv

    assert shell_argv("echo x", "/not/a/shell", True) == ["/bin/sh", "-lc", "echo x"]
    assert shell_argv("echo x", "/bin/bash", False) == ["/bin/bash", "-c", "echo x"]
    assert Path(shell_argv("echo x", str(tmp_path / "bash"), True)[0]).name == "bash"


def test_hosted_search_stock_replay_and_gate():
    from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
    from breadboard_engine.provider.contracts import ProviderContractError

    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    ordinary = ProviderRuntimeContext(None, {})
    native = ProviderRuntimeContext(None, {"provider_tools": {"openai": {
        "responses_hosted_outputs": ["web_search_call"],
    }}})
    item = {"type": "web_search_call", "id": "ws-1", "status": "completed",
            "action": {"type": "search", "query": "weather", "queries": ["weather"]}}
    expected = {key: value for key, value in item.items() if key != "id"}
    assert runtime._hosted_search_item(item) == expected
    messages = [{"role": "assistant", "content": [{
        "type": "provider_replay", "provider_id": "openai", "schema_version": "openai.responses.hosted.v1",
        "replay_scope": "same_provider", "payload": expected,
    }]}]
    assert runtime._convert_messages_to_input(messages, include_tool_calls=True, context=native) == [expected]
    with pytest.raises(ProviderContractError):
        runtime._convert_messages_to_input(messages, include_tool_calls=True, context=ordinary)
