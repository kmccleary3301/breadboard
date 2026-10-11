"""Production-path regressions for the Codex native-tool independent review."""
from __future__ import annotations

import errno
import itertools
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.conductor.model_output import process_model_output
from breadboard_engine.execution.codex_exec import CodexExecManager, truncate_output
from breadboard_engine.provider.runtime import OpenAIResponsesRuntime, ProviderRuntimeContext
from breadboard_engine.state.session_state import SessionState


@pytest.fixture
def owner(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = {
        "workspace": {"root": str(workspace), "mirror": {"enabled": False},
                      "sandbox": {"driver": "process"}},
        "logging": {"enabled": True, "root_dir": str(tmp_path / "logs")},
        "providers": {"default_model": "gpt-5.5", "models": [{
            "id": "gpt-5.5", "adapter": "openai",
            "tool_output_truncation": {"mode": "bytes", "limit": 10000},
        }]},
        "provider_tools": {"use_native": True, "api_variant": "responses", "openai": {
            "responses_hosted_outputs": ["web_search_call"],
            "responses_stateful": False,
        }},
        "tools": {"native_semantics": "codex_0_139", "registry": {
            "paths": ["implementations/tools/defs_codex_139"],
            "include": ["exec_command", "write_stdin"],
        }},
        "loop": {"sequence": [{"mode": "build"}]},
        "modes": [{"name": "build", "prompt": "", "tools_enabled": ["*"], "tools_disabled": []}],
    }
    conductor = OpenAIConductor.__ray_metadata__.modified_class(
        workspace=str(workspace), config=config, local_mode=True)
    conductor._active_session_state = SessionState(str(workspace), "test", config)
    conductor._current_route_id = "gpt-5.5"
    yield conductor


def call(owner, name, **arguments):
    return owner._exec_raw({"function": name, "arguments": arguments})["__mvi_text_output"]


@pytest.mark.parametrize("length", [10001, 20000, 40000])
def test_model_byte_policy_reaches_production_exec(owner, length):
    # model_info.rs:91 -> protocol.rs:2965-2972 -> tools/context.rs:400-401.
    output = call(owner, "exec_command", cmd=f"printf '%0{length}d' 0", login=False,
                  max_output_tokens=10000)
    body = output.split("\nOutput:\n", 1)[1]
    assert body == truncate_output("0" * length, 2500)
    assert f"Original token count: {(length + 3) // 4}\n" in output


def test_selected_route_not_default_controls_production_poll(owner):
    owner.config["providers"]["models"].append({
        "id": "smaller", "adapter": "openai", "tool_output_truncation": {"mode": "tokens", "limit": 2},
    })
    first = call(owner, "exec_command", cmd="sleep 0.4; printf 012345678", login=False, yield_time_ms=250)
    session_id = int(re.search(r"session ID ([0-9]+)", first).group(1))
    owner._current_route_id = "smaller"
    last = call(owner, "write_stdin", session_id=session_id)
    assert last.split("\nOutput:\n", 1)[1] == truncate_output("012345678", 2)


def stock_exec_output(workspace, tmp_path, arguments):
    from scripts.compaction_lanes.codex_lane import BINARY, EngineMockProvider, run_stock

    if not BINARY.is_file():
        pytest.skip("Pinned stock Codex package is required")
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "exec_command", "call_id": "exec-oracle",
         "arguments": json.dumps(arguments), "usage": usage},
        {"type": "text", "text": "Done.", "usage": usage},
    ]
    recording = tmp_path / "stock-exec.jsonl"
    with EngineMockProvider(script=script, record_path=recording) as provider:
        result = run_stock("mid_turn_compaction", workspace, tmp_path / "stock", provider.base_url,
                           {"model": "gpt-5.5", "window": 272000, "limit": 244800})
    assert all(r["returncode"] == 0 for r in result["runs"])
    outputs = [item["output"] for line in recording.read_text().splitlines()
               for item in json.loads(line)["json"].get("input", [])
               if item.get("type") == "function_call_output" and item.get("call_id") == "exec-oracle"]
    assert len(outputs) == 1
    return outputs[0]


def test_missing_workdir_output_equals_real_stock(owner, tmp_path):
    args = {"cmd": "printf unreachable", "shell": "/bin/bash", "login": False,
            "workdir": str(tmp_path / "missing")}
    expected = stock_exec_output(Path(owner.workspace), tmp_path, args)
    assert call(owner, "exec_command", **args) == expected


@pytest.mark.parametrize("command_template", ["apply_patch <<'PATCH'\n{patch}\nPATCH", "cd child && applypatch <<'PATCH'\n{patch}\nPATCH"])
def test_patch_interception_matches_stock_without_path_binary(owner, tmp_path, command_template):
    workspace = Path(owner.workspace)
    (workspace / "child").mkdir()
    patch = "*** Begin Patch\n*** Add File: evidence.txt\n+hello\n*** End Patch"
    args = {"cmd": command_template.format(patch=patch), "shell": "/bin/bash", "login": False}
    expected = stock_exec_output(workspace, tmp_path, args)
    target = workspace / ("child/evidence.txt" if command_template.startswith("cd ") else "evidence.txt")
    assert target.read_bytes() == b"hello\n"
    target.unlink()
    actual = call(owner, "exec_command", **args)
    assert actual == expected
    assert target.read_bytes() == b"hello\n"
    assert actual.startswith("Wall time: 0.0000 seconds\nOutput:\nExit code: 0\nWall time: ")
    assert "Chunk ID:" not in actual
    assert "Original token count:" not in actual


def test_implicit_patch_body_rejection_equals_stock(owner, tmp_path):
    args = {"cmd": "*** Begin Patch\n*** Add File: forbidden.txt\n+bad\n*** End Patch",
            "shell": "/bin/bash", "login": False}
    assert call(owner, "exec_command", **args) == stock_exec_output(Path(owner.workspace), tmp_path, args)
    assert not (Path(owner.workspace) / "forbidden.txt").exists()


@pytest.mark.parametrize("action_type", ["search", "other", "future_action"])
@pytest.mark.parametrize("stream", [False, True])
def test_hosted_item_survives_runtime_conductor_and_request(owner, action_type, stream):
    state = owner._active_session_state
    context = ProviderRuntimeContext(state, owner.config)
    context.extra.update(session_id="hosted-session", input_id="hosted-input", turn_id="hosted-turn-0")
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai", runtime_id="openai_responses"))
    item = {"id": "ws-1", "type": "web_search_call", "status": "completed",
            "action": {"type": action_type, "query": "weather"}}
    response = SimpleNamespace(id="resp-1", status="completed", output=[item], usage=None)

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __iter__(self):
            for phase in ("in_progress", "searching", "completed"):
                yield SimpleNamespace(type=f"response.web_search_call.{phase}")
            yield SimpleNamespace(type="response.output_item.added", item=item, output_index=0)
            yield SimpleNamespace(type="response.output_item.done", item=item, output_index=0)
            yield SimpleNamespace(type="response.completed", response=response)

        def get_final_response(self):
            return response

    client = SimpleNamespace(responses=SimpleNamespace(
        create=lambda **_: response, stream=lambda **_: Stream()))
    from unittest.mock import Mock
    # Exercise the real invoker with this test's injected SDK transport.
    owner.provider_invoker.client_lease = None

    result, used_stream = owner.provider_invoker.invoke(
        runtime=runtime, client=client, model="gpt-5.5",
        send_messages=[{"role": "user", "content": "search"}], tools_schema=None,
        stream_responses=stream, runtime_context=context, session_state=state,
        markdown_logger=Mock(), turn_index=0, route_id="gpt-5.5",
    )
    assert used_stream is stream
    for message in result.messages:
        assert process_model_output(owner, message, object(), [], state, object(), object(), object(), False, "gpt-5.5") is False
    assert isinstance(state.provider_messages[-1]["content"], list)
    expected_action = {"type": "search", "query": "weather"} if action_type == "search" else {"type": "other"}
    expected = {"type": "web_search_call", "status": "completed", "action": expected_action}
    continuation = {}
    next_response = SimpleNamespace(id="resp-2", status="completed", output=[], usage=None)
    next_client = SimpleNamespace(responses=SimpleNamespace(
        create=lambda **body: (continuation.update(body) or next_response)))
    context.extra["turn_id"] = "hosted-turn-1"
    context.turn_id = "hosted-turn-1"
    state.provider_metadata["turn_id"] = "hosted-turn-1"
    owner.provider_invoker.invoke(
        runtime=runtime, client=next_client, model="gpt-5.5",
        send_messages=state.provider_messages, tools_schema=None,
        stream_responses=False, runtime_context=context, session_state=state,
        markdown_logger=Mock(), turn_index=1, route_id="gpt-5.5",
    )
    assert continuation["input"] == [expected]


def _pty_identity(descriptor):
    stat = os.fstat(descriptor)
    return stat.st_dev, stat.st_ino, stat.st_rdev


def _assert_released(descriptor, identity):
    # The number may be reused by a later descriptor; the original pty must be gone.
    try:
        current = _pty_identity(descriptor)
    except OSError as error:
        assert error.errno == errno.EBADF
    else:
        assert current != identity


def test_pty_descriptors_closed_after_real_eviction_and_close(tmp_path):
    ids = itertools.count(1000)
    manager = CodexExecManager(str(tmp_path), truncation_policy={"mode": "bytes", "limit": 10000}, process_id=lambda: next(ids))
    try:
        first = manager.exec_command({"cmd": "sleep 60", "tty": True, "login": False, "yield_time_ms": 250})
        removed = manager.sessions[first["session_id"]]
        removed_identity = _pty_identity(removed.reader)
        for _ in range(64):
            manager.exec_command({"cmd": "sleep 60", "tty": True, "login": False, "yield_time_ms": 250})
        assert len(manager.sessions) == 64
        assert removed.reader_closed and not removed.reader_thread.is_alive()
        _assert_released(removed.reader, removed_identity)
        retained = [(entry, _pty_identity(entry.reader)) for entry in manager.sessions.values()]
        manager.close()
        manager.close()  # Closing twice must not touch a reused descriptor.
        assert all(entry.reader_closed and not entry.reader_thread.is_alive() for entry, _ in retained)
        for entry, identity in retained:
            _assert_released(entry.reader, identity)
    finally:
        manager.close()


@pytest.mark.parametrize("tty", [False, True])
def test_native_processes_deny_protected_credential_reads(tmp_path, tty):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "credentials"
    protected.write_text("protected-test-secret")
    manager = CodexExecManager(str(workspace), truncation_policy={"mode": "bytes", "limit": 10000}, protected_paths=(protected,))
    try:
        result = manager.exec_command({"cmd": f"cat {protected}", "tty": tty, "login": False})
        assert result["exit"] != 0
        assert "protected-test-secret" not in result["__mvi_text_output"]
        assert "Operation not permitted" in result["__mvi_text_output"] or "Permission denied" in result["__mvi_text_output"]
    finally:
        manager.close()


@pytest.mark.parametrize("operation", ["update", "add", "delete"])
def test_patch_helper_denies_protected_credential_reads_and_writes(tmp_path, operation):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "credentials"
    protected.write_text("protected-test-secret\n")
    manager = CodexExecManager(str(workspace), truncation_policy={"mode": "bytes", "limit": 10000}, protected_paths=(protected,))
    try:
        patches = {
            "update": f"*** Update File: {protected}\n@@\n-protected-test-secret\n+replaced",
            "add": f"*** Add File: {protected}\n+replaced",
            "delete": f"*** Delete File: {protected}",
        }
        patch = f"*** Begin Patch\n{patches[operation]}\n*** End Patch"
        result = manager.exec_command({"cmd": f"apply_patch <<'PATCH'\n{patch}\nPATCH", "login": False})
        assert result.get("error")
        assert "protected-test-secret" not in result["__mvi_text_output"]
        assert protected.read_text() == "protected-test-secret\n"
    finally:
        manager.close()


def test_lane_id_source_reaches_native_dispatch_and_continuation(owner):
    source = iter([2468])
    owner._ray_get(owner.sandbox.codex_set_process_id_generator.remote(source.__next__))
    first = call(owner, "exec_command", cmd='read word; printf "%s" "$word"',
                 tty=True, login=False, yield_time_ms=250)
    assert "Process running with session ID 2468\n" in first
    last = call(owner, "write_stdin", session_id=2468, chars="continued\n")
    assert "Process exited with code 0\n" in last
    assert last.split("\nOutput:\n", 1)[1] == "continued\r\ncontinued"
    state = owner._ray_get(owner.sandbox.codex_process_id_generator_state.remote())
    assert state == {"allocation_count": 1, "draw_count": 1}
    with pytest.raises(StopIteration):
        call(owner, "exec_command", cmd="touch exhausted-source-must-not-run", login=False)
    assert not (Path(owner.workspace) / "exhausted-source-must-not-run").exists()


@pytest.mark.parametrize("patch", [
    "*** Begin Patch\n*** Add File: custom-evidence.txt\n+hello\n*** End Patch\n",
    "not a patch",
])
def test_custom_apply_patch_output_equals_live_stock(owner, tmp_path, patch):
    from scripts.compaction_lanes.codex_lane import BINARY, EngineMockProvider, run_stock

    if not BINARY.is_file():
        pytest.skip("Pinned stock Codex package is required")
    workspace = Path(owner.workspace)
    script = [
        {"type": "custom_tool_call", "name": "apply_patch", "call_id": "custom-patch",
         "input": patch, "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}},
        {"type": "text", "text": "Done.", "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}},
    ]
    recording = tmp_path / "stock-custom.jsonl"
    with EngineMockProvider(script=script, record_path=recording) as provider:
        result = run_stock("mid_turn_compaction", workspace, tmp_path / "stock", provider.base_url,
                           {"model": "gpt-5.5", "window": 272000, "limit": 244800})
    assert all(run["returncode"] == 0 for run in result["runs"])
    outputs = [item["output"] for row in recording.read_text().splitlines()
               for item in json.loads(row)["json"].get("input", [])
               if item.get("type") == "custom_tool_call_output" and item["call_id"] == "custom-patch"]
    assert len(outputs) == 1
    target = workspace / "custom-evidence.txt"
    if target.exists():
        assert target.read_bytes() == b"hello\n"
        target.unlink()
    actual = call(owner, "apply_patch", input=patch)
    assert actual == outputs[0]
    if patch.startswith("*** Begin Patch"):
        assert target.read_bytes() == b"hello\n"
    assert owner._ray_get(owner.sandbox.codex_process_id_generator_state.remote()) == {
        "allocation_count": 0, "draw_count": 0,
    }


def test_selected_model_original_image_capability_reaches_codec(owner):
    import base64
    from PIL import Image

    path = Path(owner.workspace) / "original.png"
    Image.new("RGB", (1024, 4096), (23, 65, 129)).save(path)
    owner.config["providers"]["models"][0]["supports_image_detail_original"] = True
    result = owner._exec_raw({"function": "view_image", "arguments": {"path": str(path), "detail": "original"}})
    assert result["__mvi_content_output"] == [{
        "type": "input_image", "detail": "original",
        "image_url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode(),
    }]
