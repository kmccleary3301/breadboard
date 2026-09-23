from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from breadboard.rl.harness.runners.pi_semantics import PiSemanticsState
from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall

PACKET_ROOT = Path("/tmp/bb-pi-0731-packet/packet/cases")
CASES = (
    "normal_workspace_episode",
    "stream_fragments",
    "malformed_invalid_tool",
    "parallel_valid_invalid_valid",
    "bash_lifecycle_and_output",
    "request_cap_eight",
)


def _responses(trace: dict):
    for message in trace["messages"]:
        if message.get("role") != "assistant":
            continue
        calls = tuple(
            NativeToolCall(
                block["id"],
                block["name"],
                json.dumps(block["arguments"], separators=(",", ":")),
            )
            for block in message.get("content", [])
            if block.get("type") == "toolCall"
        )
        finish = {"toolUse": "tool_calls", "stop": "stop", "error": "stop"}.get(
            message.get("stopReason"), "tool_calls"
        )
        content = "".join(
            block.get("text", "")
            for block in message.get("content", [])
            if block.get("type") == "text"
        ) or None
        yield NativeProviderResponse(
            "binding", "request", f"response-{id(message)}", "gpt-4o-mini", content, finish, calls
        )


@pytest.mark.parametrize("case_id", CASES)
def test_pi_packet_case_replays_file_effects(case_id: str, tmp_path: Path):
    case_dir = PACKET_ROOT / case_id
    if not case_dir.is_dir():
        pytest.skip("supplier packet is not extracted at /tmp/bb-pi-0731-packet")
    trace = json.loads((case_dir / "trace.json").read_text())
    state = PiSemanticsState(
        task=trace["messages"][0]["content"][0]["text"], cwd=tmp_path, case_id=case_id
    )
    replay = state.run_episode(
        _responses(trace),
        runtime_inputs={
            "cwd": str(tmp_path),
            "home": str(tmp_path / "home"),
            "current_date": "2026-09-23",
            "package_dir": str(tmp_path / "pi-package"),
        },
    )
    observed = {
        path.relative_to(tmp_path).as_posix(): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    expected = {path: effect["sha256"] for path, effect in trace["effects"].items()}
    assert observed == expected
    assert replay["request_count"] == trace["request_count"]
    if case_id == "request_cap_eight":
        assert replay["stream_fn_issued"] == 9
        assert replay["termination"]["native_stop_reason"] == "error"
    else:
        assert replay["stream_fn_issued"] == trace["stream_fn_issued"]
