from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard_engine.compilation.provider_response import HERMES_RESPONSE_CONSUMER_ID
from breadboard.rl.harness.runners import conductor as conductor_module
from breadboard.rl.harness.hermes_tools import HermesToolRuntime, HermesToolRuntimeError, TOOL_NAMES
from breadboard.rl.harness.runners.base import (
    RunnerDependencyError,
    RunnerProtocolError,
    RunnerTerminationEvent,
    RunnerTurn,
)

_TOOL_ORDER = (
    "patch", "read_file", "search_files", "skill_view",
    "skills_list", "terminal", "write_file",
)
_DIGEST = conductor_module.canonical_sha256(())


class FakeNativePort:
    def __init__(self, *, fail_ack: bool = False) -> None:
        self.operations: list[str] = []
        self.log: list[tuple[str, str]] = []
        self.init_count = 0
        self.fail_ack = fail_ack
        self.schema_overlay = None

    @property
    def tool_bindings(self) -> tuple[Any, ...]:
        return ()
    @property
    def declared_workspace(self) -> str:
        return "/workspace"


    async def begin_native_workspace_effects(self) -> None:
        self.log.append(("effects", "begin"))

    async def close_native_runtime(self) -> dict[str, Any]:
        self.log.append(("effects", "close"))
        return {"kind": "closed", "cleanup": {"all_dead": True}}

    async def measure_workspace_effects(self) -> dict[str, Any]:
        self.log.append(("effects", "measure"))
        return {}

    async def invoke_native_phase(
        self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
    ) -> Mapping[str, Any]:
        self.operations.append(operation)
        self.log.append(("operation", operation))
        if operation == "initialize":
            self.init_count += 1
            self.schema_overlay = payload.get("schema_overlay")
            if self.init_count > 1:
                raise AssertionError("initialize invoked twice")
            return self._checkpoint()
        if operation == "history_ack":
            if self.fail_ack:
                raise RunnerProtocolError("ack failed", code="history_ack_failed")
            return self._initialized()
        if operation == "sample":
            return self._finished_sample()
        raise AssertionError(operation)

    @staticmethod
    def _checkpoint() -> dict[str, Any]:
        return {
            "schema_version": "bb.hermes-native.v1", "kind": "history_checkpoint",
            "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING",
            "iteration": 0,
        }

    def _initialized(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.hermes-native.v1", "kind": "initialized",
            "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING",
            "iteration": 0, "tool_schemas": tuple({} for _ in _TOOL_ORDER),
            "source_runtime": {"workspace": "/workspace"},
        }

    @staticmethod
    def _finished_sample() -> dict[str, Any]:
        return {
            "schema_version": "bb.hermes-native.v1", "kind": "sample_ready",
            "event_delta": (), "history_digest": _DIGEST, "status": "FINISHED",
            "iteration": 0, "public_stop": "completed",
        }


class HarnessSession(conductor_module._ConductorSession):
    def __init__(self, log: list[tuple[str, str]]) -> None:
        self.log = log
        self.sequence = 0

    async def _emit(self, event: Any) -> None:
        self.log.append(("event", type(event).__name__))
        self._events.append(replace(event, sequence=self.sequence))
        self.sequence += 1

    async def _checkpoint(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def _commit_termination(self, termination: Any) -> None:
        await self._emit(
            RunnerTerminationEvent(
                0,
                self._open_request.episode_id,
                self._open_request.effective_plan_digest,
                len(self._turns),
                termination,
            )
        )

    def _context(self) -> dict[str, Any]:
        return {}

    async def _raise_error(self, error: BaseException, **kwargs: Any) -> None:
        raise error

class FakeBinding:
    source_model_config = {"model_name": "model", "max_input_tokens": 1}

    def bind_native_tools(self, tools: tuple[Mapping[str, Any], ...]) -> None:
        self.bound_tools = tools

def _configure_session(
    session: HarnessSession, tools: FakeNativePort, *, overlay: bool = True,
) -> None:
    limits = SimpleNamespace(max_turns=8, action_timeout_ms=40_000, transcript_bytes=1_000_000)
    session._open_request = SimpleNamespace(
        episode_id="hermes-regression", effective_plan_digest="sha256:" + "a" * 64,
        effective_plan=SimpleNamespace(effective_capabilities=SimpleNamespace(limits=limits)),
    )
    session._projection = SimpleNamespace(
        source_consumer_id=HERMES_RESPONSE_CONSUMER_ID,
        source_profile={"advertisement": {}, **({"schema_overlay": {"read_file": {}, "terminal": {}}} if overlay else {})},
        models=(SimpleNamespace(params={}, policy_slot_id="slot"),),
        modes=(SimpleNamespace(tool_ids=_TOOL_ORDER),),
    )
    session._binding = FakeBinding()
    session._tools = tools
    session._turns = [RunnerTurn(1, (), ())]
    session._events = []
    session._native_cleanup_outcome = conductor_module.NativeCleanupOutcome(False, None, None, None)


async def _run(*, overlay: bool = True) -> tuple[Any, FakeNativePort, list[tuple[str, str]]]:
    tools = FakeNativePort()
    log = tools.log
    session = HarnessSession(log)
    _configure_session(session, tools, overlay=overlay)

    result = await session._loop_native_stream(
        conductor_module.ConductorRunRequest({"prompt": "test"}),
        conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID],
    )
    if overlay:
        assert tools.schema_overlay == {"read_file": {}, "terminal": {}}
    return result, tools, log


@pytest.mark.asyncio
async def test_hermes_result_carries_episode_id() -> None:
    result, _, _ = await _run()
    assert result.episode_id == "hermes-regression"
    assert result.response["replay_trace"]["case_id"] == "hermes-regression"


@pytest.mark.asyncio
async def test_history_ack_follows_durable_source_commit() -> None:
    _, tools, log = await _run()
    first_ack = log.index(("operation", "history_ack"))
    first_commit = next(
        index for index, item in enumerate(log)
        if item == ("event", "SourceEventCommitEvent")
    )
    assert first_ack > first_commit
    assert tools.operations.count("initialize") == 1
    assert log.count(("effects", "close")) == 1


@pytest.mark.asyncio
async def test_history_ack_failure_closes_native_runtime_once() -> None:
    class FailingAckPort(FakeNativePort):
        async def close_native_runtime(self) -> dict[str, Any]:
            closed = await super().close_native_runtime()
            assert self.init_count == 1
            return closed

    tools = FailingAckPort(fail_ack=True)
    session = HarnessSession(tools.log)
    _configure_session(session, tools)
    with pytest.raises(RunnerProtocolError, match="ack failed"):
        await session._loop_native_stream(
            conductor_module.ConductorRunRequest({"prompt": "test"}),
            conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID],
        )
    assert tools.log.count(("effects", "close")) == 1
    assert session._native_stream_close_callback is None


@pytest.mark.asyncio
async def test_sample_failure_closes_native_runtime_once() -> None:
    class FailingSamplePort(FakeNativePort):
        async def invoke_native_phase(
            self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
        ) -> Mapping[str, Any]:
            if operation == "sample":
                self.operations.append(operation)
                raise RunnerProtocolError("malformed sample", code="native_response_invalid")
            return await super().invoke_native_phase(operation, payload, timeout_ms=timeout_ms)

    tools = FailingSamplePort()
    session = HarnessSession(tools.log)
    _configure_session(session, tools)
    with pytest.raises(RunnerProtocolError, match="malformed sample"):
        await session._loop_native_stream(
            conductor_module.ConductorRunRequest({"prompt": "test"}),
            conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID],
        )
    assert tools.log.count(("effects", "close")) == 1
    assert session._native_stream_close_callback is None

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("provider", "commit", "cancellation"))
async def test_native_failures_close_after_initialization(failure: str) -> None:
    class FailurePort(FakeNativePort):
        async def invoke_native_phase(
            self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
        ) -> Mapping[str, Any]:
            if operation == "sample" and failure == "provider":
                self.operations.append(operation)
                return {
                    "schema_version": "bb.hermes-native.v1",
                    "kind": "provider_request", "event_delta": (),
                    "history_digest": _DIGEST, "status": "RUNNING",
                    "iteration": 0, "http_request": {"body_b64": "e30="},
                }
            return await super().invoke_native_phase(operation, payload, timeout_ms=timeout_ms)

    class FailureSession(HarnessSession):
        async def _checkpoint(self, checkpoint: str, **kwargs: Any) -> None:
            if failure == "cancellation" and checkpoint == "before_policy":
                raise asyncio.CancelledError

        async def _native_commit_source(
            self, turn: int | None, consumer_id: str, phase_name: str,
            events: tuple[Mapping[str, Any], ...], digest: str,
            state: Mapping[str, Any],
        ) -> None:
            if failure == "commit" and phase_name == "initial":
                raise RunnerProtocolError("publication failed", code="native_response_invalid")
            await super()._native_commit_source(
                turn, consumer_id, phase_name, events, digest, state,
            )

        async def _invoke_native_policy(
            self, http_request: Mapping[str, Any], *, model: Any, turn: int,
            verify_staged_body: bool = False,
        ) -> Mapping[str, Any]:
            assert verify_staged_body
            raise RunnerDependencyError("provider failed", code="policy_invoke_failed")

    tools = FailurePort()
    session = FailureSession(tools.log)
    _configure_session(session, tools)
    expected = asyncio.CancelledError if failure == "cancellation" else (
        RunnerDependencyError if failure == "provider" else RunnerProtocolError
    )
    with pytest.raises(expected):
        await session._loop_native_stream(
            conductor_module.ConductorRunRequest({"prompt": "test"}),
            conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID],
        )
    assert tools.init_count == 1
    assert tools.log.count(("effects", "close")) == 1
    assert session._native_stream_close_callback is None


@pytest.mark.asyncio
async def test_history_checkpoint_continuation_initializes_once() -> None:
    _, tools, _ = await _run()
    assert tools.operations[:2] == ["initialize", "history_ack"]
    assert tools.operations.count("initialize") == 1

@pytest.mark.asyncio
async def test_missing_sealed_overlay_prevents_worker_initialization() -> None:
    with pytest.raises(conductor_module.RunnerPlanError, match="runtime controls differ"):
        await _run(overlay=False)


def test_mismatched_native_schema_sha_fails_closed(tmp_path: Path) -> None:

    native = [
        {"type": "function", "function": {"name": name, "parameters": {}}}
        for name in TOOL_NAMES
    ]
    declared = {
        "native_sha256": "0" * 64,
        "approved_schema_json": json.dumps(native[1], sort_keys=True, separators=(",", ":")),
    }
    declared["approved_sha256"] = hashlib.sha256(declared["approved_schema_json"].encode()).hexdigest()
    runtime = HermesToolRuntime(
        SimpleNamespace(tools=native),
        workspace=tmp_path, scratch=tmp_path, hermes_home=tmp_path,
        remaining=lambda: 35,
        schema_overlay={"read_file": declared, "terminal": declared},
    )
    with pytest.raises(HermesToolRuntimeError, match="native tool schema differs from overlay pin"):
        runtime._bounded_tool_schemas()
