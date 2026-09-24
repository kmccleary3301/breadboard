from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from breadboard.rl.harness.runners import conductor as conductor_module
from breadboard.rl.harness.runners.base import (
    RunnerProtocolError,
    RunnerTerminationEvent,
    RunnerTurn,
)
from conformance.comparators.hermes_agent import compare_cases


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
    ) -> None:
        self.log: list[str] = []
        self.workspace = workspace
        self.worker_workspace = worker_workspace or workspace
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
    ) -> Mapping[str, Any]:
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
    *, worker_workspace: Path | None = None,
) -> dict[str, Any]:
    trace = json.loads((FIXTURE / "trace.json").read_text())
    workspace = FIXTURE / "workspace"
    port = FixtureNativePort(
        trace, workspace, worker_workspace=worker_workspace,
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
        source_profile={"advertisement": {}},
        models=(SimpleNamespace(params={}, policy_slot_id="slot"),),
        modes=(SimpleNamespace(tool_ids=TOOL_ORDER),),
    )
    session._binding = FixtureBinding()
    session._tools = port
    session._turns = []
    session._events = []
    session._policy_responses: list[int] = []
    result = await session._loop_hermes(
        conductor_module.ConductorRunRequest({
            "prompt": trace["requests"][0]["body"]["messages"][1]["content"]
        })
    )
    return conductor_module.thaw_json(result.response["replay_trace"])


@pytest.mark.asyncio
async def test_conductor_trace_matches_committed_rerun3_fixture() -> None:
    trace = await _run_fixture()
    report = compare_cases(FIXTURE, trace)
    assert report["ok"] is True, report
    assert trace["runtime"]["cwd"] == str(FIXTURE / "workspace")
    assert trace["request_count"] == len(trace["requests"]) == 2
    assert trace["file_effects"] == {
        "repaired.txt": "sha256:aa6083f3a3c96f3860a4977f429ed51841511a3716ea3537472fea4365781e2b"
    }

    packet_trace = json.loads((FIXTURE / "trace.json").read_text())
    expected_tools = packet_trace["requests"][0]["body"]["tools"]
    assert len(expected_tools) == 7
    assert trace["requests"][0]["body"]["tools"] == expected_tools
    for tool in trace["requests"][0]["body"]["tools"]:
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]
        assert "properties" in tool["function"]["parameters"]

    tampered = copy.deepcopy(conductor_module.thaw_json(trace))
    tampered["requests"][0]["body"]["messages"][1]["content"] += " tampered"
    report = compare_cases(FIXTURE, tampered)
    assert report["ok"] is False
    assert report["failed"] > 0


@pytest.mark.asyncio
async def test_conductor_rejects_name_only_tool_schemas_fixture() -> None:
    trace = await _run_fixture()
    name_only_trace = copy.deepcopy(conductor_module.thaw_json(trace))
    name_only_trace["requests"][0]["body"]["tools"] = [
        {"type": "function", "function": {"name": name}}
        for name in TOOL_ORDER
    ]
    report = compare_cases(FIXTURE, name_only_trace)
    assert report["ok"] is False
    assert report["failed"] > 0
    assert any("tools" in a.get("detail", "") for a in report["assertions"] if a["status"] == "failed")


@pytest.mark.asyncio
async def test_conductor_rejects_worker_workspace_mismatch() -> None:
    with pytest.raises(RunnerProtocolError, match="workspace"):
        await _run_fixture(worker_workspace=FIXTURE / "wrong-workspace")
