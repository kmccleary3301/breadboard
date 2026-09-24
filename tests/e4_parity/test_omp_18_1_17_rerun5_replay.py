from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import tarfile
from typing import Any

import pytest

from breadboard.rl.harness.runners.omp_semantics import ToolCall, ToolResult, run_tool_batch
from conformance.comparators.oh_my_pi_18_1_17 import project_bb_trace
PACKET = Path(
    os.environ.get(
        "BB_OMP_RERUN5_PACKET",
        "/Users/kylemccleary/projects/breadboard/docs_tmp/bb_direction_assessment/"
        "engine_pr_handoff_20260827/e4_admission_20260914T221653Z/do2-20260923/"
        "omp/packet/omp_supplier_capture_packet_rerun5.tar.gz",
    )
)
CASE_IDS = (
    "normal_multiturn",
    "malformed_tool_call",
    "budget_limit_stop",
    "stream_fragments_broken",
    "length_cutoff_skips_tool",
    "process_lifecycle",
)


def _members(archive: tarfile.TarFile, case_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trace_member = archive.extractfile(f"captures/{case_id}/trace.json")
    transcript_member = archive.extractfile(f"captures/{case_id}/receiver/http-transcript.jsonl")
    assert trace_member is not None and transcript_member is not None
    trace = json.load(trace_member)
    transcript = [json.loads(line) for line in transcript_member.read().decode().splitlines() if line.strip()]
    return trace, transcript


def _response_tool_calls(events: list[dict[str, Any]]) -> tuple[str | None, list[ToolCall]]:
    calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    for event in events:
        for choice in event.get("choices", []):
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            for raw in delta.get("tool_calls", []):
                index = int(raw.get("index", 0))
                entry = calls.setdefault(index, {"id": raw.get("id", f"call-{index}"), "name": "", "arguments": ""})
                function = raw.get("function") or {}
                entry["id"] = raw.get("id", entry["id"])
                entry["name"] = function.get("name", entry["name"])
                entry["arguments"] += function.get("arguments", "")
    result: list[ToolCall] = []
    for index, value in sorted(calls.items()):
        arguments: Any = value["arguments"]
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass
        result.append(ToolCall(value["id"], value["name"], arguments, index))
    return finish_reason, result


def _tool_message_output(message: dict[str, Any]) -> str:
    content = message.get("content", message.get("output", ""))
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    return str(content)


def test_rerun5_receiver_replay_preserves_full_bodies_results_and_effects() -> None:
    if not PACKET.is_file():
        pytest.skip("DO-2 rerun5 packet is not mounted")
    with tarfile.open(PACKET, "r:gz") as archive:
        case_results: dict[str, dict[str, Any]] = {}
        for case_id in CASE_IDS:
            trace, transcript = _members(archive, case_id)
            assert transcript
            request_bodies = [entry["body"] for entry in transcript]
            assert trace["receiver_requests"] == len(request_bodies)
            # Full parsed request bodies are the contract; no projection may drop
            # stream, token, response-format, or other provider fields.
            assert all({"messages", "tools"}.issubset(body) for body in request_bodies)

            dispatched = 0
            recorded_results: list[str] = []
            for index, entry in enumerate(transcript):
                finish_reason, calls = _response_tool_calls(entry.get("events", []))
                if finish_reason is None:
                    continue
                next_messages = transcript[index + 1]["body"]["messages"] if index + 1 < len(transcript) else []
                tool_messages = [message for message in next_messages if message.get("role") in {"tool", "toolResult", "tool_result"}]
                by_id = {message.get("tool_call_id", message.get("toolCallId")): _tool_message_output(message) for message in tool_messages}

                async def execute(call: ToolCall) -> str:
                    return by_id.get(call.id, "")

                results = asyncio.run(run_tool_batch(calls, finish_reason, execute))
                if finish_reason == "length":
                    assert results and all(isinstance(result, ToolResult) and result.skipped for result in results)
                    assert all(result.output.startswith("Tool call was not executed because the assistant hit its output token limit") for result in results)
                    assert tool_messages and _tool_message_output(tool_messages[0]).startswith("Tool call was not executed because the assistant hit its output token limit")
                elif finish_reason in {"tool_calls", "toolUse"}:
                    dispatched += len(calls)
                    recorded_results.extend(str(result) for result in results)
                    assert len(results) == len(calls)
                    assert [str(result) for result in results] == [by_id.get(call.id, "") for call in calls]
                elif entry.get("broken") or finish_reason == "error":
                    assert results == []
            episode = project_bb_trace({
                "requests": [{"body": body} for body in request_bodies],
                "results": [{"output": output} for output in recorded_results],
                "effects": trace["effects"],
                "termination": "timed_out" if trace.get("timed_out") else "submitted",
                "stop_reason": "error" if case_id == "stream_fragments_broken" else "stop",
                "receiver_requests": trace["receiver_requests"],
            })
            assert episode["requests"]
            assert [request["body"] for request in episode["requests"]] == request_bodies
            assert episode["file_effects"] == {
                path: (value.get("sha256") if isinstance(value, dict) else value)
                for path, value in sorted(trace["effects"].items())
            }
            case_results[case_id] = {
                "requests": trace["receiver_requests"],
                "dispatched": dispatched,
                "effects": episode["file_effects"],
            }

    assert {case_id: case_results[case_id]["dispatched"] for case_id in CASE_IDS} == {
        "normal_multiturn": 3,
        "malformed_tool_call": 3,
        "budget_limit_stop": 8,
        "stream_fragments_broken": 0,
        "length_cutoff_skips_tool": 1,
        "process_lifecycle": 2,
    }
    assert set(case_results) == set(CASE_IDS)
    assert [case_results[case_id]["requests"] for case_id in CASE_IDS] == [4, 4, 8, 1, 3, 3]
    assert case_results["stream_fragments_broken"]["dispatched"] == 0
    assert case_results["length_cutoff_skips_tool"]["effects"]["cutoff_marker.txt"] is None
    assert case_results["normal_multiturn"]["effects"]["normal_marker.txt"].startswith("sha256:")
    assert case_results["malformed_tool_call"]["effects"]["must_not_exist.txt"] is None
    assert case_results["process_lifecycle"]["effects"]["lifecycle_child.txt"].startswith("sha256:")
