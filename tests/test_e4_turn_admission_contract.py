from __future__ import annotations

import asyncio
import queue
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from agentic_coder_prototype.agent import AgenticCoder

from agentic_coder_prototype.api.cli_bridge.app import create_app
from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import (
    SessionCreateRequest,
    SessionStatus,
    SessionTurnCancelResponse,
    TurnAdmission,
)
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner, TurnContractConflict


TERMINAL_TYPES = {
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
    EventType.TURN_CANCELLED,
}


class ControlledAgent:
    """Deterministic in-process agent used to exercise the real admission runner."""

    _local_mode = True

    def __init__(self, workspace: Path) -> None:
        self.workspace_dir = str(workspace)
        self.config: dict[str, Any] = {}
        self.agent = self
        self.starts: list[str] = []
        self.started: dict[str, threading.Event] = {}
        self.releases: dict[str, threading.Event] = {}
        self.stop_requested = threading.Event()
        self.same_prompt_calls = 0
        self.same_prompt_provenance: dict[str, Any] | None = None
        self.block_initialize = False
        self.initialize_started = threading.Event()
        self.initialize_release = threading.Event()

    def initialize(self) -> None:
        Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)
        self.initialize_started.set()
        if self.block_initialize:
            self.initialize_release.wait(3.0)

    def request_stop(self) -> None:
        self.stop_requested.set()

    def wait_started(self, task: str, timeout: float = 3.0) -> bool:
        return self.started.setdefault(task, threading.Event()).wait(timeout)

    def release(self, task: str) -> None:
        self.releases.setdefault(task, threading.Event()).set()

    @staticmethod
    def _with_provenance(
        result: dict[str, Any],
        *,
        task: str,
        context: Any,
    ) -> dict[str, Any]:
        invocation_id = (
            context.get("_breadboard_turn_invocation_id")
            if isinstance(context, dict)
            else None
        )
        result["_breadboard_turn_message_provenance"] = {
            "invocation_id": invocation_id,
            "message_start_index": 0,
            "user_message_index": 0,
            "submitted_user_content": task,
        }
        return result

    def run_task(
        self,
        task: str,
        *,
        event_emitter: Any = None,
        control_queue: Any = None,
        context: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.starts.append(task)
        self.started.setdefault(task, threading.Event()).set()

        if task.startswith("block:"):
            release = self.releases.setdefault(task, threading.Event())
            while not release.is_set() and not self.stop_requested.is_set():
                try:
                    command = control_queue.get_nowait() if control_queue is not None else None
                except queue.Empty:
                    command = None
                if isinstance(command, dict) and command.get("kind") == "stop":
                    self.stop_requested.set()
                    break
                time.sleep(0.005)
            if self.stop_requested.is_set():
                self.stop_requested.clear()
                return {
                    "completion_summary": {"completed": False, "reason": "stopped"},
                    "reward_metrics_payload": {},
                    "messages": [{"role": "user", "content": task}],
                    "logging_dir": None,
                }

        if task == "explode":
            raise RuntimeError("synthetic failure")
        if task == "stale":
            return {
                "completion_summary": {"completed": True, "reason": "test"},
                "reward_metrics_payload": {},
                "messages": [{"role": "assistant", "content": "older turn answer"}],
                "logging_dir": None,
            }
        if task == "same prompt":
            self.same_prompt_calls += 1
            unchanged_transcript = {
                "completion_summary": {"completed": True, "reason": "test"},
                "reward_metrics_payload": {},
                "messages": [
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": "older answer for same prompt"},
                ],
                "logging_dir": None,
            }
            if self.same_prompt_calls == 1:
                current = self._with_provenance(
                    unchanged_transcript,
                    task=task,
                    context=context,
                )
                self.same_prompt_provenance = dict(
                    current["_breadboard_turn_message_provenance"]
                )
                return current
            unchanged_transcript["_breadboard_turn_message_provenance"] = dict(
                self.same_prompt_provenance or {}
            )
            return unchanged_transcript
        if task == "stale-final-message":
            return {
                "completion_summary": {
                    "completed": True,
                    "reason": "test",
                    "final_message": "older completion answer",
                },
                "reward_metrics_payload": {},
                "messages": [{"role": "assistant", "content": "older completion answer"}],
                "logging_dir": None,
            }
        if task == "missing":
            return {
                "completion_summary": {"completed": True, "reason": "test"},
                "reward_metrics_payload": {},
                "messages": [{"role": "user", "content": task}],
                "logging_dir": None,
            }
        if task == "fallback":
            return self._with_provenance(
                {
                    "completion_summary": {"completed": True, "reason": "test"},
                    "reward_metrics_payload": {},
                    "messages": [
                        {"role": "user", "content": task},
                        {"role": "assistant", "content": "fresh fallback answer"},
                    ],
                    "logging_dir": None,
                },
                task=task,
                context=context,
            )

        if event_emitter is not None:
            event_emitter("assistant_message", {"text": f"answer:{task}"}, turn=1)
            event_emitter("tool_call", {"call_id": f"call:{task}", "name": "read", "arguments": {}}, turn=1)
            event_emitter("tool_result", {"call_id": f"call:{task}", "output": "ok"}, turn=1)
            event_emitter("task_event", {"kind": "progress", "task": task}, turn=1)
            event_emitter("warning", {"message": "bounded warning"}, turn=1)
        return {
            "completion_summary": {"completed": True, "reason": "test"},
            "reward_metrics_payload": {"score": 1},
            "messages": [
                {"role": "user", "content": task},
                {"role": "assistant", "content": f"answer:{task}"},
            ],
            "logging_dir": None,
        }


async def make_runner(
    tmp_path: Path,
    *,
    initial_task: str = "",
    start: bool = True,
) -> tuple[SessionRunner, SessionRecord, ControlledAgent]:
    registry = SessionRegistry()
    record = SessionRecord(session_id=f"session-{id(registry)}", status=SessionStatus.STARTING)
    await registry.create(record)
    agent = ControlledAgent(tmp_path)
    request = SessionCreateRequest(
        config_path=str(tmp_path / "missing-config.yml"),
        task=initial_task,
        workspace=str(tmp_path),
        stream=True,
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=request,
        agent_factory=lambda *_args, **_kwargs: agent,
    )
    record.runner = runner
    if start:
        await runner.start()
        await asyncio.sleep(0)
    return runner, record, agent


async def collect_until_terminals(
    record: SessionRecord,
    count: int,
    *,
    timeout: float = 5.0,
) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    terminals = 0
    deadline = asyncio.get_running_loop().time() + timeout
    while terminals < count:
        remaining = deadline - asyncio.get_running_loop().time()
        assert remaining > 0, f"timed out after events {[event.type.value for event in events]}"
        event = await asyncio.wait_for(record.event_queue.get(), timeout=remaining)
        assert event is not None
        events.append(event)
        if event.type in TERMINAL_TYPES:
            terminals += 1
    return events


@pytest.mark.asyncio
async def test_idle_create_is_queryable_and_lookup_is_observational(tmp_path: Path) -> None:
    service = SessionService()
    created = await service.create_session(
        SessionCreateRequest(config_path=str(tmp_path / "missing-config.yml"), workspace=str(tmp_path))
    )
    first = (await service.ensure_session(created.session_id)).to_summary()
    second = (await service.ensure_session(created.session_id)).to_summary()

    assert first.turn_admission is TurnAdmission.IDLE
    assert first.active_turn_id is None
    assert first.queued_turn_count == 0
    assert second.turn_admission == first.turn_admission
    assert second.active_turn_id == first.active_turn_id
    assert second.queued_turn_count == first.queued_turn_count

    await service.stop_session(created.session_id)


@pytest.mark.asyncio
async def test_explicit_legacy_initial_task_is_still_admitted(tmp_path: Path) -> None:
    runner, record, agent = await make_runner(tmp_path, initial_task="block:legacy-initial")
    initial_turn = next(iter(record.turns_by_id.values()))
    assert await asyncio.to_thread(agent.wait_started, "block:legacy-initial")

    assert initial_turn.original_disposition == "started"
    assert record.to_summary().active_turn_id == initial_turn.turn_id
    agent.release("block:legacy-initial")
    events = await collect_until_terminals(record, 1)
    assert agent.starts == ["block:legacy-initial"]
    assert [
        event.type
        for event in events
        if event.turn_id == initial_turn.turn_id and event.type in TERMINAL_TYPES
    ] == [EventType.TURN_COMPLETED]

    await runner.stop()


@pytest.mark.asyncio
async def test_serialized_submit_replay_fifo_order_and_event_correlation(tmp_path: Path) -> None:
    runner, record, agent = await make_runner(tmp_path)
    first = await runner.submit_input("block:first", attachments=[], client_message_id="message-1")
    assert await asyncio.to_thread(agent.wait_started, "block:first")
    second = await runner.submit_input("second", attachments=[], client_message_id="message-2")
    third = await runner.submit_input("third", attachments=[], client_message_id="message-3")

    replay = await runner.submit_input("second", attachments=[], client_message_id="message-2")
    assert replay.disposition == "deduplicated"
    assert replay.original_disposition == "queued"
    assert (replay.input_id, replay.turn_id) == (second.input_id, second.turn_id)
    with pytest.raises(TurnContractConflict, match="different input body") as conflict:
        await runner.submit_input("changed", attachments=[], client_message_id="message-2")
    assert conflict.value.code == "input_idempotency_conflict"

    active = record.to_summary()
    assert first.disposition == "started"
    assert second.disposition == third.disposition == "queued"
    assert active.turn_admission is TurnAdmission.ACTIVE
    assert active.active_turn_id == first.turn_id
    assert active.queued_turn_count == 2

    agent.release("block:first")
    events = await collect_until_terminals(record, 3)
    assert agent.starts == ["block:first", "second", "third"]
    assert record.to_summary().turn_admission is TurnAdmission.IDLE

    identities = {
        first.turn_id: first.input_id,
        second.turn_id: second.input_id,
        third.turn_id: third.input_id,
    }
    turn_owned_types = {
        EventType.USER_MESSAGE,
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.TASK_EVENT,
        EventType.WARNING,
        EventType.REWARD_UPDATE,
        EventType.COMPLETION,
        EventType.RUN_FINISHED,
        EventType.TURN_COMPLETED,
    }
    for event in events:
        if event.type not in turn_owned_types:
            continue
        assert event.turn_id in identities
        assert event.input_id == identities[event.turn_id]
    for turn_id in identities:
        per_turn = [event for event in events if event.turn_id == turn_id]
        assert turn_owned_types.issubset({event.type for event in per_turn})
        assert [event.type for event in per_turn].count(EventType.TURN_COMPLETED) == 1
        assert sum(event.type in TERMINAL_TYPES for event in per_turn) == 1

    await runner.stop()


@pytest.mark.asyncio
async def test_admission_races_produce_one_active_and_fifo_queue(tmp_path: Path) -> None:
    runner, record, agent = await make_runner(tmp_path)
    contents = [f"race-{index}" for index in range(16)]
    receipts = await asyncio.gather(
        *(
            runner.submit_input(content, attachments=[], client_message_id=f"key-{index}")
            for index, content in enumerate(contents)
        )
    )

    assert [receipt.disposition for receipt in receipts].count("started") == 1
    assert [receipt.disposition for receipt in receipts].count("queued") == 15
    assert len({receipt.input_id for receipt in receipts}) == 16
    assert len({receipt.turn_id for receipt in receipts}) == 16
    assert record.to_summary().queued_turn_count == 15

    started_id = next(receipt.turn_id for receipt in receipts if receipt.disposition == "started")
    admitted_order = [record.turns_by_id[started_id].content] + [
        record.turns_by_id[turn_id].content for turn_id in record.queued_turn_ids
    ]
    await collect_until_terminals(record, 16)
    assert agent.starts == admitted_order
    assert record.to_summary().turn_admission is TurnAdmission.IDLE

    await runner.stop()


@pytest.mark.asyncio
async def test_active_cancel_before_runner_start_never_executes_agent(tmp_path: Path) -> None:
    runner, record, agent = await make_runner(tmp_path, start=False)
    receipt = await runner.submit_input(
        "dangerous side effect",
        attachments=[],
        client_message_id="pre-start-message",
    )
    acknowledgement = await runner.cancel_turn(
        receipt.turn_id,
        cancellation_request_key="pre-start-cancel",
        reason="user_requested",
    )
    await runner.start()
    events = await collect_until_terminals(record, 1)

    assert acknowledgement.disposition == "cancellation_requested"
    assert agent.starts == []
    assert not any(
        event.type in {EventType.USER_MESSAGE, EventType.TURN_START}
        and event.turn_id == receipt.turn_id
        for event in events
    )
    assert [
        event.type
        for event in events
        if event.turn_id == receipt.turn_id and event.type in TERMINAL_TYPES
    ] == [EventType.TURN_CANCELLED]

    await runner.stop()


@pytest.mark.asyncio
async def test_active_cancel_during_agent_initialization_never_calls_run_task(
    tmp_path: Path,
) -> None:
    runner, record, agent = await make_runner(tmp_path, start=False)
    agent.block_initialize = True
    await runner.start()
    receipt = await runner.submit_input(
        "cancel during initialize",
        attachments=[],
        client_message_id="initialize-message",
    )
    assert await asyncio.to_thread(agent.initialize_started.wait, 3.0)
    acknowledgement = await runner.cancel_turn(
        receipt.turn_id,
        cancellation_request_key="initialize-cancel",
        reason="user_requested",
    )
    agent.initialize_release.set()
    events = await collect_until_terminals(record, 1)

    assert acknowledgement.disposition == "cancellation_requested"
    assert agent.starts == []
    assert [
        event.type
        for event in events
        if event.turn_id == receipt.turn_id and event.type in TERMINAL_TYPES
    ] == [EventType.TURN_CANCELLED]

    await runner.stop()


@pytest.mark.asyncio
async def test_active_cancel_at_final_execution_handshake_never_calls_run_task(
    tmp_path: Path,
) -> None:
    runner, record, agent = await make_runner(tmp_path, start=False)
    handshake_reached = asyncio.Event()
    release_handshake = asyncio.Event()
    original_commit = runner._commit_turn_execution

    async def paused_commit(turn: Any) -> bool:
        handshake_reached.set()
        await release_handshake.wait()
        return await original_commit(turn)

    runner._commit_turn_execution = paused_commit  # type: ignore[method-assign]
    await runner.start()
    receipt = await runner.submit_input(
        "cancel at handshake",
        attachments=[],
        client_message_id="handshake-message",
    )
    await asyncio.wait_for(handshake_reached.wait(), timeout=3.0)
    assert runner._control_queue is not None
    acknowledgement = await runner.cancel_turn(
        receipt.turn_id,
        cancellation_request_key="handshake-cancel",
        reason="user_requested",
    )
    release_handshake.set()
    events = await collect_until_terminals(record, 1)

    assert acknowledgement.disposition == "cancellation_requested"
    assert agent.starts == []
    assert [
        event.type
        for event in events
        if event.turn_id == receipt.turn_id and event.type in TERMINAL_TYPES
    ] == [EventType.TURN_CANCELLED]

    await runner.stop()


@pytest.mark.asyncio
async def test_targeted_active_and_queued_cancel_are_idempotent_and_terminal_once(tmp_path: Path) -> None:
    runner, record, agent = await make_runner(tmp_path)
    active = await runner.submit_input("block:active", attachments=[], client_message_id="active-message")
    assert await asyncio.to_thread(agent.wait_started, "block:active")
    queued = await runner.submit_input("never-start", attachments=[], client_message_id="queued-message")

    queued_ack = await runner.cancel_turn(
        queued.turn_id,
        cancellation_request_key="cancel-queued",
        reason="user_requested",
    )
    assert queued_ack.disposition == "queued_cancelled"
    queued_replay = await runner.cancel_turn(
        queued.turn_id,
        cancellation_request_key="cancel-queued",
        reason="user_requested",
    )
    assert queued_replay.disposition == "deduplicated"
    assert queued_replay.original_disposition == "queued_cancelled"
    assert queued_replay.cancellation_request_id == queued_ack.cancellation_request_id
    with pytest.raises(TurnContractConflict) as body_conflict:
        await runner.cancel_turn(
            queued.turn_id,
            cancellation_request_key="cancel-queued",
            reason="timeout",
        )
    assert body_conflict.value.code == "cancellation_idempotency_conflict"
    with pytest.raises(TurnContractConflict) as target_conflict:
        await runner.cancel_turn(
            active.turn_id,
            cancellation_request_key="cancel-queued",
            reason="user_requested",
        )
    assert target_conflict.value.code == "cancellation_idempotency_conflict"

    active_ack = await runner.cancel_turn(
        active.turn_id,
        cancellation_request_key="cancel-active",
        reason="user_requested",
    )
    assert active_ack.disposition == "cancellation_requested"
    active_replay = await runner.cancel_turn(
        active.turn_id,
        cancellation_request_key="cancel-active",
        reason="user_requested",
    )
    assert active_replay.disposition == "deduplicated"
    assert active_replay.cancellation_request_id == active_ack.cancellation_request_id

    events = await collect_until_terminals(record, 2)
    assert "never-start" not in agent.starts
    for receipt in (active, queued):
        terminals = [
            event
            for event in events
            if event.turn_id == receipt.turn_id and event.type in TERMINAL_TYPES
        ]
        assert len(terminals) == 1
        assert terminals[0].type is EventType.TURN_CANCELLED
        assert terminals[0].input_id == receipt.input_id

    repeated_after_terminal = await runner.cancel_turn(
        active.turn_id,
        cancellation_request_key="cancel-active",
        reason="user_requested",
    )
    assert repeated_after_terminal.cancellation_request_id == active_ack.cancellation_request_id
    with pytest.raises(TurnContractConflict) as terminal_conflict:
        await runner.cancel_turn(
            active.turn_id,
            cancellation_request_key="new-cancel-key",
            reason="user_requested",
        )
    assert terminal_conflict.value.code == "turn_already_terminal"
    assert record.to_summary().turn_admission is TurnAdmission.IDLE

    await runner.stop()


def test_agent_wrapper_preserves_only_producer_bound_current_invocation() -> None:
    transcript = [
        {"role": "user", "content": "same prompt"},
        {"role": "assistant", "content": "older answer"},
    ]
    stale_result = {
        "messages": transcript,
        "_breadboard_turn_message_provenance": {
            "invocation_id": "turn-invocation-first",
            "message_start_index": 0,
            "user_message_index": 0,
            "submitted_user_content": "same prompt",
        },
    }
    AgenticCoder._preserve_turn_message_provenance(
        stale_result,
        context={
            "_breadboard_turn_invocation_id": "turn-invocation-second",
            "_breadboard_submitted_task_text": "same prompt",
        },
    )
    assert "_breadboard_turn_message_provenance" not in stale_result

    current_result = {
        "messages": transcript,
        "_breadboard_turn_message_provenance": {
            "invocation_id": "turn-invocation-second",
            "message_start_index": 0,
            "user_message_index": 0,
            "submitted_user_content": "same prompt",
        },
    }
    AgenticCoder._preserve_turn_message_provenance(
        current_result,
        context={
            "_breadboard_turn_invocation_id": "turn-invocation-second",
            "_breadboard_submitted_task_text": "same prompt",
        },
    )
    assert current_result["_breadboard_turn_message_provenance"]["user_message_index"] == 0


@pytest.mark.asyncio
async def test_repeated_identical_prompt_requires_current_invocation_provenance(
    tmp_path: Path,
) -> None:
    runner, record, _agent = await make_runner(tmp_path)
    first = await runner.submit_input(
        "same prompt",
        attachments=[],
        client_message_id="same-prompt-1",
    )
    first_events = await collect_until_terminals(record, 1)
    second = await runner.submit_input(
        "same prompt",
        attachments=[],
        client_message_id="same-prompt-2",
    )
    second_events = await collect_until_terminals(record, 1)

    assert any(
        event.type is EventType.ASSISTANT_MESSAGE and event.turn_id == first.turn_id
        for event in first_events
    )
    assert not any(
        event.type is EventType.ASSISTANT_MESSAGE and event.turn_id == second.turn_id
        for event in second_events
    )
    assert [
        event.type
        for event in second_events
        if event.turn_id == second.turn_id and event.type in TERMINAL_TYPES
    ] == [EventType.TURN_FAILED]
    assert [
        event.payload
        for event in second_events
        if event.turn_id == second.turn_id and event.type is EventType.ERROR
    ] == [{"code": "missing_assistant_outcome"}]

    await runner.stop()


@pytest.mark.asyncio
async def test_stale_completion_final_message_cannot_complete_turn(tmp_path: Path) -> None:
    runner, record, _agent = await make_runner(tmp_path)
    receipt = await runner.submit_input(
        "stale-final-message",
        attachments=[],
        client_message_id="stale-final-key",
    )
    events = await collect_until_terminals(record, 1)

    assert not any(
        event.type is EventType.ASSISTANT_MESSAGE and event.turn_id == receipt.turn_id
        for event in events
    )
    assert [
        event.type
        for event in events
        if event.turn_id == receipt.turn_id and event.type in TERMINAL_TYPES
    ] == [EventType.TURN_FAILED]
    assert [
        event.payload
        for event in events
        if event.turn_id == receipt.turn_id and event.type is EventType.ERROR
    ] == [{"code": "missing_assistant_outcome"}]

    await runner.stop()


@pytest.mark.asyncio
async def test_turn_local_assistant_gate_and_terminal_uniqueness(tmp_path: Path) -> None:
    runner, record, _agent = await make_runner(tmp_path)
    cases = ["stale", "missing", "explode", "fallback", "normal"]
    receipts = []
    events: list[SessionEvent] = []
    for index, content in enumerate(cases):
        receipt = await runner.submit_input(content, attachments=[], client_message_id=f"case-{index}")
        receipts.append(receipt)
        events.extend(await collect_until_terminals(record, 1))

    outcomes = {
        receipt.turn_id: [
            event.type
            for event in events
            if event.turn_id == receipt.turn_id and event.type in TERMINAL_TYPES
        ]
        for receipt in receipts
    }
    assert outcomes[receipts[0].turn_id] == [EventType.TURN_FAILED]
    assert outcomes[receipts[1].turn_id] == [EventType.TURN_FAILED]
    assert outcomes[receipts[2].turn_id] == [EventType.TURN_FAILED]
    assert outcomes[receipts[3].turn_id] == [EventType.TURN_COMPLETED]
    assert outcomes[receipts[4].turn_id] == [EventType.TURN_COMPLETED]
    assert all(len(terminals) == 1 for terminals in outcomes.values())

    stale_assistant = [
        event
        for event in events
        if event.turn_id == receipts[0].turn_id and event.type is EventType.ASSISTANT_MESSAGE
    ]
    assert stale_assistant == []
    for receipt in receipts[:3]:
        errors = [
            event
            for event in events
            if event.turn_id == receipt.turn_id and event.type is EventType.ERROR
        ]
        assert len(errors) == 1
        assert errors[0].input_id == receipt.input_id

    await runner.stop()


def test_canonical_targeted_cancel_route_uses_typed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.delenv("BREADBOARD_API_TOKEN", raising=False)
    service = SessionService()

    async def fake_cancel(session_id: str, turn_id: str, payload: Any) -> SessionTurnCancelResponse:
        assert session_id == "session-1"
        assert turn_id == "turn-1"
        assert payload.cancellation_request_key == "request-1"
        return SessionTurnCancelResponse(
            cancellation_request_id="cancel-1",
            cancellation_request_key=payload.cancellation_request_key,
            input_id="input-1",
            turn_id=turn_id,
            disposition="cancellation_requested",
            original_disposition="cancellation_requested",
        )

    service.cancel_turn = fake_cancel  # type: ignore[method-assign]
    app = create_app(service)
    response = TestClient(app).post(
        "/v1/sessions/session-1/turns/turn-1/cancel",
        json={"cancellation_request_key": "request-1", "reason": "user_requested"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "cancellation_request_id": "cancel-1",
        "cancellation_request_key": "request-1",
        "input_id": "input-1",
        "turn_id": "turn-1",
        "disposition": "cancellation_requested",
        "original_disposition": "cancellation_requested",
    }
    assert not any(route.path == "/sessions/{session_id}/turns/{turn_id}/cancel" for route in app.routes)
