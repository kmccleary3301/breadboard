from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from breadboard.rl.harness.runners import conductor as conductor_module
from breadboard_engine.compilation.provider_response import HERMES_RESPONSE_CONSUMER_ID
from breadboard.rl.harness.runners.base import (
    RunnerProtocolError,
    RunnerTerminationEvent,
    RunnerTurn,
)


FIXTURE = (
    Path(__file__).parents[2]
    / "e4_parity"
    / "fixtures"
    / "hermes_agent"
    / "H-04-name-repair-duplicate"
)
TOOL_ORDER = (
    "patch",
    "read_file",
    "search_files",
    "skill_view",
    "skills_list",
    "terminal",
    "write_file",
)
DIGEST = conductor_module.canonical_sha256(())


def _json_b64(value: Mapping[str, Any]) -> str:
    return base64.b64encode(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()


class FixtureNativePort:
    def __init__(
        self,
        trace: Mapping[str, Any],
        workspace: Path,
        *,
        worker_workspace: Path | None = None,
        write_effect: bool = True,
    ) -> None:
        self.log: list[str] = []
        self.workspace = workspace
        self.worker_workspace = worker_workspace or workspace
        self.write_effect = write_effect
        self.baseline: dict[str, str] = {}
        self.requests = tuple(
            row for row in trace["requests"] if row["kind"] == "request"
        )
        self.served = tuple(
            row for row in trace["requests"] if row["kind"] == "served"
        )
        self.history: list[dict[str, Any]] = []
        self.history_digest = DIGEST
        self.sample_index = 0
        self.policy_index = 0
        self.prepare_index = 0
        self.ack_index = 0

    @property
    def tool_bindings(self) -> tuple[Any, ...]:
        return ()
    @property
    def declared_workspace(self) -> str:
        return str(self.workspace)

    async def begin_native_workspace_effects(self) -> None:
        self.begin_calls = getattr(self, "begin_calls", 0) + 1
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.baseline = self._snapshot()

    def _snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.workspace).as_posix(): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.workspace.rglob("*") if path.is_file()
        }

    async def measure_workspace_effects(self) -> dict[str, dict[str, Any]]:
        self.measure_calls = getattr(self, "measure_calls", 0) + 1
        return {
            path: {"sha256": digest, "exists": True}
            for path, digest in self._snapshot().items()
            if self.baseline.get(path) != digest
        }

    async def close_native_runtime(self) -> dict[str, Any]:
        self.close_calls = getattr(self, "close_calls", 0) + 1
        return {"kind": "closed", "cleanup": {"all_dead": True}}


    def _delta(self, rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
        start = len(self.history)
        candidate = [*self.history, *rows]
        before = self.history_digest
        after = conductor_module.bytes_sha256(
            conductor_module.canonical_json_bytes(candidate)
        )
        self.history = candidate
        self.history_digest = after
        return [{
            "kind": "history_revision",
            "start": start,
            "delete_count": 0,
            "insert": rows,
            "before_digest": before,
            "after_digest": after,
        }]

    def _result(
        self,
        kind: str,
        *,
        status: str,
        iteration: int,
        delta: tuple[dict[str, Any], ...] = (),
        **extra: Any,
    ) -> dict[str, Any]:
        event_delta = self._delta(delta) if delta else []
        return {
            "schema_version": "bb.hermes-native.v1",
            "kind": kind,
            "event_delta": event_delta,
            "history_digest": self.history_digest,
            "status": status,
            "iteration": iteration,
            **extra,
        }

    async def invoke_native_phase(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]:
        del payload, timeout_ms
        self.log.append(operation)
        if operation == "initialize":
            return self._result(
                "history_checkpoint",
                status="RUNNING",
                iteration=0,
                source_runtime={"workspace": str(self.worker_workspace)},
            )
        if operation == "history_ack":
            self.ack_index += 1
            if self.ack_index == 1:
                return self._result(
                    "initialized",
                    status="RUNNING",
                    iteration=0,
                    tool_schemas=tuple(copy.deepcopy(self.requests[0]["body"]["tools"])),
                    source_runtime={"workspace": str(self.worker_workspace)},
                )
            return self._result(
                "segment_executed",
                status="RUNNING",
                iteration=0,
                index=0,
                observations=({
                    "action_index": 0,
                    "tool_id": "write_file",
                    "call_id": "same",
                    "result": {
                        "bytes_written": 9,
                        "dirs_created": True,
                        "verified": True,
                        "lint": {
                            "status": "skipped",
                            "message": "No linter for .txt files",
                        },
                        "resolved_path": "/opt/hermes/case/workspace/repaired.txt",
                        "files_modified": [
                            "/opt/hermes/case/workspace/repaired.txt"
                        ],
                    },
                },),
            )
        if operation == "sample":
            index = self.sample_index
            self.sample_index += 1
            return self._result(
                "provider_request",
                status="RUNNING",
                iteration=index,
                http_request={
                    "body_b64": _json_b64(self.requests[index]["body"]),
                },
                raw_response_b64=_json_b64(self.served[index]["response"]),
            )
        if operation == "provider_response":
            status = "RUNNING" if self.policy_index == 0 else "FINISHED"
            self.policy_index += 1
            return self._result(
                "sample_ready",
                status=status,
                iteration=self.policy_index - 1,
            )
        if operation == "prepare":
            index = self.prepare_index
            self.prepare_index += 1
            if index == 0:
                assistant = {
                    "content": "",
                    "finish_reason": "tool_calls",
                    "reasoning": None,
                    "role": "assistant",
                    "tool_calls": [{
                        "call_id": "same",
                        "function": {
                            "arguments": '{"path":"/opt/hermes/case/workspace/repaired.txt","content":"repaired\\n"}',
                            "name": "write_file",
                        },
                        "id": "same",
                        "response_item_id": "fc_same",
                        "type": "function",
                    }],
                }
                return self._result(
                    "prepared",
                    status="RUNNING",
                    iteration=0,
                    delta=(assistant,),
                    actions=({
                        "index": 0,
                        "tool_id": "write_file",
                        "call_id": "same",
                        "arguments_json": '{"path":"/opt/hermes/case/workspace/repaired.txt","content":"repaired\\n"}',
                    },),
                    segments=({
                        "index": 0,
                        "kind": "sequential",
                        "action_indices": (0,),
                    },),
                )
            assistant = {
                "content": "repair complete",
                "finish_reason": "stop",
                "reasoning": None,
                "role": "assistant",
            }
            return self._result(
                "prepared",
                status="FINISHED",
                iteration=1,
                delta=(assistant,),
                actions=(),
                segments=(),
            )
        if operation == "execute_segment":
            if self.write_effect:
                (self.workspace / "repaired.txt").write_text("repaired\n")
            tool_result = {
                "content": json.dumps({
                    "bytes_written": 9,
                    "dirs_created": True,
                    "verified": True,
                    "lint": {
                        "status": "skipped",
                        "message": "No linter for .txt files",
                    },
                    "resolved_path": "/opt/hermes/case/workspace/repaired.txt",
                    "files_modified": [
                        "/opt/hermes/case/workspace/repaired.txt"
                    ],
                }),
                "name": "write_file",
                "role": "tool",
                "tool_call_id": "same",
                "tool_name": "write_file",
            }
            return self._result(
                "history_checkpoint",
                status="RUNNING",
                iteration=0,
                delta=(tool_result,),
                segment_index=0,
                action_index=0,
            )
        if operation == "commit":
            return self._result(
                "committed",
                status="RUNNING",
                iteration=0,
                file_effects={
                    "repaired.txt": "sha256:aa6083f3a3c96f3860a4977f429ed51841511a3716ea3537472fea4365781e2b"
                },
            )
        raise AssertionError(operation)


class FixtureSession(conductor_module._ConductorSession):
    def __init__(self, *, response_bodies: tuple[Mapping[str, Any], ...]) -> None:
        self.response_bodies = response_bodies
        self.sequence = 0

    async def _emit(self, event: Any) -> None:
        self._events.append(replace(event, sequence=self.sequence))
        self.sequence += 1

    async def _checkpoint(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def _commit_termination(self, termination: Any) -> None:
        await self._emit(RunnerTerminationEvent(
            0,
            self._open_request.episode_id,
            self._open_request.effective_plan_digest,
            len(self._turns),
            termination,
        ))

    def _context(self) -> dict[str, Any]:
        return {}

    async def _raise_error(self, error: BaseException, **kwargs: Any) -> None:
        raise error

    async def _invoke_native_policy(
        self, http_request: Mapping[str, Any], *, model: Any, turn: int,
        verify_staged_body: bool = False,
    ) -> Mapping[str, Any]:
        assert verify_staged_body
        del http_request, model, turn
        index = len(self._policy_responses)
        self._policy_responses.append(index)
        return {
            "native_http_response": {
                "body_b64": _json_b64(self.response_bodies[index]),
            },
        }


class FixtureBinding:
    source_model_config = {"model_name": "model", "max_input_tokens": 1}

    def bind_native_tools(self, tools: tuple[Mapping[str, Any], ...]) -> None:
        self.bound_tools = tools


async def _run_fixture(
    workspace: Path, *, worker_workspace: Path | None = None,
    write_effect: bool = True,
) -> tuple[dict[str, Any], FixtureNativePort]:
    trace = json.loads((FIXTURE / "trace.json").read_text())
    port = FixtureNativePort(
        trace, workspace, worker_workspace=worker_workspace, write_effect=write_effect,
    )
    responses = tuple(
        row["response"] for row in trace["requests"] if row["kind"] == "served"
    )
    session = FixtureSession(response_bodies=responses)
    limits = SimpleNamespace(
        max_turns=8,
        action_timeout_ms=40_000,
        transcript_bytes=1_000_000,
    )
    session._open_request = SimpleNamespace(
        episode_id=trace["case_id"],
        effective_plan_digest="sha256:" + "a" * 64,
        effective_plan=SimpleNamespace(
            effective_capabilities=SimpleNamespace(limits=limits)
        ),
    )
    session._projection = SimpleNamespace(
        source_consumer_id=HERMES_RESPONSE_CONSUMER_ID,
        source_profile={"advertisement": {}, "schema_overlay": {"read_file": {}, "terminal": {}}},
        models=(SimpleNamespace(params={}, policy_slot_id="slot"),),
        modes=(SimpleNamespace(tool_ids=TOOL_ORDER),),
    )
    session._binding = FixtureBinding()
    session._tools = port
    session._turns = []
    session._events = []
    session._policy_responses: list[int] = []
    session._native_cleanup_outcome = conductor_module.NativeCleanupOutcome(False, None, None, None)
    result = await session._loop_native_stream(
        conductor_module.ConductorRunRequest({
            "prompt": trace["requests"][0]["body"]["messages"][1]["content"]
        }),
        conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID],
    )
    return conductor_module.thaw_json(result.response["replay_trace"]), port


@pytest.mark.asyncio
async def test_conductor_trace_preserves_sent_body_and_measured_effects(tmp_path: Path) -> None:
    trace, port = await _run_fixture(workspace=tmp_path / "workspace")
    assert trace["runtime"]["cwd"] == str(tmp_path / "workspace")
    assert all("stream" not in row["body"] for row in trace["requests"])
    assert trace["request_count"] == len(trace["requests"]) == 2
    assert trace["file_effects"] == {
        "repaired.txt": "sha256:aa6083f3a3c96f3860a4977f429ed51841511a3716ea3537472fea4365781e2b"
    }
    assert (port.begin_calls, port.close_calls, port.measure_calls) == (1, 1, 1)

    packet_trace = json.loads((FIXTURE / "trace.json").read_text())
    expected_tools = packet_trace["requests"][0]["body"]["tools"]
    assert len(expected_tools) == 7
    assert trace["requests"][0]["body"]["tools"] == expected_tools
    for tool in trace["requests"][0]["body"]["tools"]:
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]
        assert "properties" in tool["function"]["parameters"]



@pytest.mark.asyncio
async def test_worker_reported_effect_without_content_change_is_not_trusted(tmp_path: Path) -> None:
    trace, _ = await _run_fixture(workspace=tmp_path / "workspace", write_effect=False)
    assert trace["file_effects"] == {}


@pytest.mark.asyncio
async def test_conductor_rejects_worker_workspace_mismatch(tmp_path: Path) -> None:
    with pytest.raises(RunnerProtocolError, match="workspace"):
        await _run_fixture(workspace=tmp_path / "workspace", worker_workspace=FIXTURE / "wrong-workspace")
