from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from breadboard.rl.harness.runners import conductor as conductor_module
from breadboard.rl.harness.runners.base import (
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

    @property
    def tool_bindings(self) -> tuple[Any, ...]:
        return ()

    async def invoke_native_phase(
        self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
    ) -> Mapping[str, Any]:
        self.operations.append(operation)
        self.log.append(("operation", operation))
        if operation == "initialize":
            self.init_count += 1
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

    @staticmethod
    def _initialized() -> dict[str, Any]:
        return {
            "schema_version": "bb.hermes-native.v1", "kind": "initialized",
            "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING",
            "iteration": 0, "tool_schemas": tuple({} for _ in _TOOL_ORDER),
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


async def _run(*, fail_ack: bool = False) -> tuple[Any, FakeNativePort, list[tuple[str, str]]]:
    tools = FakeNativePort(fail_ack=fail_ack)
    log = tools.log
    session = HarnessSession(log)
    limits = SimpleNamespace(max_turns=8, action_timeout_ms=40_000, transcript_bytes=1_000_000)
    session._open_request = SimpleNamespace(
        episode_id="hermes-regression", effective_plan_digest="sha256:" + "a" * 64,
        effective_plan=SimpleNamespace(effective_capabilities=SimpleNamespace(limits=limits)),
    )
    session._projection = SimpleNamespace(
        source_profile={"advertisement": {}},
        models=(SimpleNamespace(params={}, policy_slot_id="slot"),),
        modes=(SimpleNamespace(tool_ids=_TOOL_ORDER),),
    )
    session._binding = FakeBinding()
    session._tools = tools
    session._turns = [RunnerTurn(1, (), ())]
    session._events = []

    result = await session._loop_hermes(conductor_module.ConductorRunRequest({"prompt": "test"}))
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


@pytest.mark.asyncio
async def test_history_ack_failure_is_typed_and_stops_followup_operations() -> None:
    with pytest.raises(RunnerProtocolError, match="ack failed"):
        await _run(fail_ack=True)


@pytest.mark.asyncio
async def test_history_checkpoint_continuation_initializes_once() -> None:
    _, tools, _ = await _run()
    assert tools.operations[:2] == ["initialize", "history_ack"]
    assert tools.operations.count("initialize") == 1
