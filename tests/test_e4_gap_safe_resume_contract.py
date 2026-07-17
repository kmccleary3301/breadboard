from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

import pytest
from fastapi import HTTPException

from agentic_coder_prototype.api.cli_bridge import events as events_module
from agentic_coder_prototype.api.cli_bridge import service as service_module
from agentic_coder_prototype.api.cli_bridge.app import _encode_sse_event, create_app
from agentic_coder_prototype.api.cli_bridge.events import (
    REPLAY_RETENTION_MAX_AGE_MS,
    REPLAY_RETENTION_MAX_EVENTS,
    SNAPSHOT_RECOVERY_ACTION,
    EventType,
    SessionEvent,
    replay_retention_facts,
)
from agentic_coder_prototype.api.cli_bridge.models import (
    SessionInputRequest,
    SessionStatus,
    SessionTurnCancelRequest,
)
from agentic_coder_prototype.api.cli_bridge.registry import (
    CancellationRecord,
    SessionRecord,
    SessionRegistry,
    TurnRecord,
)
from agentic_coder_prototype.api.cli_bridge.service import SessionService


def _logged_event(
    session_id: str,
    sequence: int,
    *,
    event_type: EventType = EventType.ASSISTANT_MESSAGE,
    created_at: int = 2_000_000_000_000,
    input_id: str | None = None,
    turn_id: str | None = None,
    payload: dict | None = None,
) -> SessionEvent:
    return SessionEvent(
        event_type,
        session_id,
        payload or {"sequence": sequence},
        created_at=created_at,
        event_id=f"event-{sequence}",
        seq=sequence,
        input_id=input_id,
        turn_id=turn_id,
    )


async def _stop_dispatcher(record: SessionRecord) -> None:
    task = record.dispatcher_task
    if task is not None and not task.done():
        await record.event_queue.put(None)
        await task


async def _wait_for_sequence(record: SessionRecord, sequence: int) -> None:
    for _ in range(200):
        if record.event_seq >= sequence:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"dispatcher did not reach sequence {sequence}")


@pytest.mark.asyncio
async def test_resume_is_exclusive_limit_cannot_truncate_and_open_facts_match_snapshot() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(session_id="resume", status=SessionStatus.COMPLETED)
    record.event_log.extend(_logged_event(record.session_id, sequence) for sequence in range(1, 5))
    record.event_seq = 4
    await registry.create(record)

    stream = service.event_stream(
        record.session_id,
        from_id="1",
        limit=1,
        include_open_ack=True,
    )
    opened = await stream.__anext__()
    replayed = [await stream.__anext__() for _ in range(3)]
    await stream.aclose()

    assert opened.type is EventType.STREAM_OPEN
    assert opened.stable_cursor is False
    assert [event.seq for event in replayed] == [2, 3, 4]
    assert all(event.event_id != "event-1" for event in replayed)

    snapshot = record.to_summary().model_dump(by_alias=True)
    for key in (
        "replayRetention",
        "earliestRetainedSequence",
        "earliestRetainedEventId",
        "headSequence",
        "headEventId",
        "retainedHistory",
        "sessionReplayContractDigest",
    ):
        assert opened.payload[key] == snapshot[key]

    await _stop_dispatcher(record)

@pytest.mark.asyncio
async def test_v1_snapshot_and_stream_ack_export_equal_replay_contract_shapes() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(session_id="v1-schema", status=SessionStatus.COMPLETED, event_seq=1)
    record.event_log.append(_logged_event(record.session_id, 1))
    await registry.create(record)
    app = create_app(service, include_atp_routes=False)

    async def close_stream() -> None:
        await asyncio.sleep(0.01)
        await record.event_queue.put(None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        snapshot_response = await client.get(f"/v1/sessions/{record.session_id}")
        closer = asyncio.create_task(close_stream())
        stream_response = await client.get(
            f"/v1/sessions/{record.session_id}/events",
            params={"from_id": "1", "limit": 1},
        )
        await closer

    assert snapshot_response.status_code == 200
    assert stream_response.status_code == 200
    stream_data = [
        line.removeprefix("data: ")
        for line in stream_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(stream_data) == 1

    opened = json.loads(stream_data[0])
    assert opened["type"] == "stream.open"
    assert opened["stable_cursor"] is False
    assert not any(line.startswith("id: ") for line in stream_response.text.splitlines())
    snapshot = snapshot_response.json()
    for key in (
        "replayRetention",
        "earliestRetainedSequence",
        "earliestRetainedEventId",
        "headSequence",
        "headEventId",
        "retainedHistory",
        "sessionReplayContractDigest",
    ):
        assert opened["payload"][key] == snapshot[key]


@pytest.mark.asyncio
async def test_expired_cursor_returns_typed_recovery_without_registering_a_subscriber() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(
        session_id="expired",
        status=SessionStatus.COMPLETED,
        event_seq=3,
        replay_history_partial=True,
    )
    record.event_log.extend((_logged_event(record.session_id, 2), _logged_event(record.session_id, 3)))
    await registry.create(record)

    with pytest.raises(HTTPException) as captured:
        await service.prepare_event_stream(record.session_id, from_id="1")

    assert captured.value.status_code == 409
    detail = captured.value.detail
    assert detail["code"] == "resume_window_exceeded"
    assert detail["first_retained_sequence"] == 2
    assert detail["last_retained_sequence"] == 3
    assert detail["earliestRetainedSequence"] == 2
    assert detail["headSequence"] == 3
    assert detail["retainedHistory"] == "partial"
    assert detail["recovery"]["action"] == SNAPSHOT_RECOVERY_ACTION
    assert detail["recovery"]["snapshot"] == {"method": "GET", "resource": "session_snapshot"}
    assert detail["recovery"]["reconnect"]["cursor"] is None
    assert record.subscribers == {}

    await _stop_dispatcher(record)

@pytest.mark.asyncio
async def test_v1_count_and_age_boundary_gaps_are_typed_before_stream_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ms = 2_000_000_000_000
    monkeypatch.setattr(service_module.time, "time", lambda: now_ms / 1000)
    registry = SessionRegistry()
    service = SessionService(registry)

    count_record = SessionRecord(
        session_id="count-boundary",
        status=SessionStatus.COMPLETED,
        event_seq=REPLAY_RETENTION_MAX_EVENTS + 1,
    )
    count_record.event_log.extend(
        _logged_event(count_record.session_id, sequence, created_at=now_ms)
        for sequence in range(1, REPLAY_RETENTION_MAX_EVENTS + 2)
    )
    age_record = SessionRecord(
        session_id="age-boundary",
        status=SessionStatus.COMPLETED,
        event_seq=1,
    )
    age_record.event_log.append(
        _logged_event(
            age_record.session_id,
            1,
            created_at=now_ms - REPLAY_RETENTION_MAX_AGE_MS - 1,
        )
    )
    await registry.create(count_record)
    await registry.create(age_record)
    app = create_app(service, include_atp_routes=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        count_response = await client.get(
            f"/v1/sessions/{count_record.session_id}/events",
            params={"from_id": "1"},
        )
        age_snapshot_response = await client.get(
            f"/v1/sessions/{age_record.session_id}"
        )
        age_response = await client.get(
            f"/v1/sessions/{age_record.session_id}/events",
            params={"from_id": "1"},
        )

    for response in (count_response, age_response):
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "resume_window_exceeded"
        assert body["detail"]["message"] == (
            "resume cursor is outside the retained replay window"
        )
        assert body["detail"]["last_event_id"] == "1"
        assert body["detail"]["recovery"]["action"] == SNAPSHOT_RECOVERY_ACTION
        assert body["detail"]["recovery"]["reconnect"]["cursor"] is None
        assert body["detail"]["replayRetention"] == {
            "maxEvents": REPLAY_RETENTION_MAX_EVENTS,
            "maxAgeMs": REPLAY_RETENTION_MAX_AGE_MS,
            "configurationDigest": body["detail"]["replayRetention"][
                "configurationDigest"
            ],
        }
    assert count_response.json()["detail"]["first_retained_sequence"] == 2
    assert age_response.json()["detail"]["first_retained_sequence"] is None
    assert age_snapshot_response.status_code == 200
    assert age_snapshot_response.json()["earliestRetainedSequence"] is None
    assert age_snapshot_response.json()["retainedHistory"] == "partial"

    await _stop_dispatcher(count_record)
    await _stop_dispatcher(age_record)


@pytest.mark.asyncio
async def test_synthetic_todo_projection_is_non_cursor_and_has_no_sse_id() -> None:
    registry = SessionRegistry()
    service = SessionService(registry)
    record = SessionRecord(
        session_id="todo",
        status=SessionStatus.RUNNING,
        metadata={
            "todo_last_update": {
                "op": "replace",
                "revision": 7,
                "scopeKey": "main",
                "items": [],
            }
        },
    )
    await registry.create(record)

    stream = service.event_stream(record.session_id)
    projection = await stream.__anext__()
    await stream.aclose()

    assert projection.type is EventType.TOOL_RESULT
    assert projection.stable_cursor is False
    assert projection.seq is None
    assert "id" not in projection.asdict()
    assert "seq" not in projection.asdict()
    assert not _encode_sse_event(projection).startswith(b"id:")
    assert b"\nid:" not in _encode_sse_event(projection)

    await _stop_dispatcher(record)


@pytest.mark.asyncio
async def test_subscriber_overflow_emits_one_terminal_gap_and_stops_later_delivery() -> None:
    registry = SessionRegistry()
    service = SessionService(registry, subscriber_queue_maxsize=1)
    record = SessionRecord(session_id="overflow", status=SessionStatus.RUNNING, event_seq=1)
    record.event_log.append(_logged_event(record.session_id, 1))
    await registry.create(record)

    stream = service.event_stream(record.session_id, from_id="1")
    first_delivery = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    await record.event_queue.put(_logged_event(record.session_id, 2))
    delivered = await first_delivery
    assert delivered.seq == 2

    await record.event_queue.put(_logged_event(record.session_id, 3))
    await record.event_queue.put(_logged_event(record.session_id, 4))
    await _wait_for_sequence(record, 4)

    gap = await stream.__anext__()
    assert gap.type is EventType.STREAM_GAP
    assert gap.stable_cursor is False
    assert gap.payload["code"] == "subscriber_overflow"
    assert gap.payload["last_safely_delivered_cursor"] == {
        "sequence": 2,
        "event_id": "event-2",
    }
    assert gap.payload["recovery"]["snapshot_action"] == SNAPSHOT_RECOVERY_ACTION
    assert record.subscribers == {}

    await record.event_queue.put(_logged_event(record.session_id, 5))
    await _wait_for_sequence(record, 5)
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    await _stop_dispatcher(record)

@pytest.mark.asyncio
async def test_durable_persist_failure_never_fans_out_stable_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PersistFailingRegistry(SessionRegistry):
        fail_persist = False

        async def persist(
            self,
            record: SessionRecord,
            *,
            terminal_event: SessionEvent | None = None,
        ) -> None:
            if self.fail_persist:
                raise OSError("deterministic persist failure")
            await super().persist(record, terminal_event=terminal_event)

    now_ms = 2_000_000_000_000
    clock = {"now_ms": now_ms}
    monkeypatch.setattr(
        service_module.time,
        "time",
        lambda: clock["now_ms"] / 1000,
    )
    registry = PersistFailingRegistry()
    service = SessionService(registry)
    record = SessionRecord(
        session_id="persist-failure",
        status=SessionStatus.RUNNING,
        event_seq=1,
    )
    record.event_log.append(
        _logged_event(
            record.session_id,
            1,
            created_at=now_ms - REPLAY_RETENTION_MAX_AGE_MS,
        )
    )
    await registry.create(record)
    prepared = await service.prepare_event_stream(record.session_id)
    stream = service.prepared_event_stream(prepared)
    clock["now_ms"] += 1

    registry.fail_persist = True
    await record.event_queue.put(
        SessionEvent(
            EventType.TURN_COMPLETED,
            record.session_id,
            {},
            created_at=clock["now_ms"],
            input_id="input-failure",
            turn_id="turn-failure",
        )
    )
    assert record.dispatcher_task is not None
    await record.dispatcher_task

    gap = await stream.__anext__()
    assert gap.type is EventType.STREAM_GAP
    assert gap.stable_cursor is False
    assert gap.payload["code"] == "durable_persist_failed"
    assert gap.payload["last_safely_delivered_cursor"] == {
        "sequence": None,
        "event_id": None,
    }
    assert gap.payload["recovery"]["action"] == SNAPSHOT_RECOVERY_ACTION
    assert record.event_seq == 1
    assert [event.seq for event in record.event_log] == [1]
    assert record.replay_history_partial is False
    assert record.terminal_event_envelopes == []
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


def test_replay_retention_exact_count_age_edges_and_configuration_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ms = 2_000_000_000_000
    monkeypatch.setattr(service_module.time, "time", lambda: now_ms / 1000)
    service = SessionService(SessionRegistry())

    count_record = SessionRecord(
        session_id="count",
        status=SessionStatus.COMPLETED,
        event_seq=REPLAY_RETENTION_MAX_EVENTS + 1,
    )
    count_record.event_log.extend(
        _logged_event(count_record.session_id, sequence, created_at=now_ms)
        for sequence in range(1, REPLAY_RETENTION_MAX_EVENTS + 2)
    )
    service._prune_replay(count_record)
    assert len(count_record.event_log) == REPLAY_RETENTION_MAX_EVENTS
    assert count_record.event_log[0].seq == 2
    assert count_record.replay_history_partial is True

    cutoff = now_ms - REPLAY_RETENTION_MAX_AGE_MS
    age_record = SessionRecord(session_id="age", status=SessionStatus.COMPLETED, event_seq=2)
    age_record.event_log.extend(
        (
            _logged_event(age_record.session_id, 1, created_at=cutoff - 1),
            _logged_event(age_record.session_id, 2, created_at=cutoff),
        )
    )
    service._prune_replay(age_record)
    assert [event.seq for event in age_record.event_log] == [2]

    before = replay_retention_facts(
        age_record.event_log,
        head_sequence=age_record.event_seq,
        retained_history_partial=age_record.replay_history_partial,
    )
    monkeypatch.setattr(events_module, "REPLAY_RETENTION_MAX_EVENTS", REPLAY_RETENTION_MAX_EVENTS + 1)
    after = replay_retention_facts(
        age_record.event_log,
        head_sequence=age_record.event_seq,
        retained_history_partial=age_record.replay_history_partial,
    )
    assert before["replayRetention"]["maxEvents"] == REPLAY_RETENTION_MAX_EVENTS
    assert after["replayRetention"]["maxEvents"] == REPLAY_RETENTION_MAX_EVENTS + 1
    assert before["replayRetention"]["configurationDigest"] != after["replayRetention"]["configurationDigest"]
    assert before["sessionReplayContractDigest"] != after["sessionReplayContractDigest"]


@pytest.mark.asyncio
async def test_terminal_and_idempotency_state_survives_restart_and_replay_expiry_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "runtime_records" / "session_state"
    registry = SessionRegistry(state_root)
    record = SessionRecord(session_id="durable-session", status=SessionStatus.COMPLETED, event_seq=3)
    raw_prompt = "raw prompt credential sk-secret-value"
    raw_attachment = "/private/path/attachment-secret.txt"
    raw_url = "https://user:password@example.invalid/private"
    submit_key = "submit-key-secret-alias"
    cancel_key = "cancel-key-secret-alias"

    outcomes = ("completed", "failed", "cancelled")
    turns: list[TurnRecord] = []
    for index, outcome in enumerate(outcomes, start=1):
        turn = TurnRecord(
            input_id=f"input-{index}",
            turn_id=f"turn-{index}",
            client_message_id=submit_key if index == 1 else f"submit-{index}",
            content=raw_prompt if index == 1 else f"prompt-{index}",
            attachments=(raw_attachment, raw_url) if index == 1 else (),
            original_disposition="started",
            state=outcome,
            terminal_outcome=outcome,
        )
        turns.append(turn)
        record.turns_by_id[turn.turn_id] = turn
        record.submissions_by_key[turn.client_message_id] = turn

    cancellation = CancellationRecord(
        cancellation_request_id="cancel-request-1",
        cancellation_request_key=cancel_key,
        turn_id=turns[2].turn_id,
        input_id=turns[2].input_id,
        reason="user_requested",
        original_disposition="cancellation_requested",
    )
    record.cancellations_by_key[cancel_key] = cancellation
    await registry.create(record)

    terminal_types = (
        EventType.TURN_COMPLETED,
        EventType.TURN_FAILED,
        EventType.TURN_CANCELLED,
    )
    terminal_payloads = ({}, {"error": {"code": "turn_execution_failed"}}, {"reason": "user_requested"})
    terminal_events = []
    for index, (event_type, payload) in enumerate(zip(terminal_types, terminal_payloads), start=1):
        terminal = _logged_event(
            record.session_id,
            index,
            event_type=event_type,
            created_at=1,
            input_id=f"input-{index}",
            turn_id=f"turn-{index}",
            payload=payload,
        )
        terminal_events.append(terminal)
        await registry.persist(record, terminal_event=terminal)

    record.event_log.extend(terminal_events)
    monkeypatch.setattr(service_module.time, "time", lambda: 2_000_000_000)
    SessionService(registry)._prune_replay(record)
    assert list(record.event_log) == []
    await registry.persist(record)

    durable_bytes = b"".join(path.read_bytes() for path in state_root.glob("*.json"))
    for forbidden in (
        raw_prompt,
        raw_attachment,
        raw_url,
        submit_key,
        cancel_key,
        "sk-secret-value",
    ):
        assert forbidden.encode() not in durable_bytes
    assert b"sha256:" in durable_bytes
    assert not list(state_root.glob("*.tmp"))

    restarted_registry = SessionRegistry(state_root)
    restarted_service = SessionService(restarted_registry)
    rehydrated = await restarted_service.ensure_session(record.session_id)
    snapshot = rehydrated.to_summary()
    assert snapshot.status is SessionStatus.COMPLETED
    assert {turn["outcome"] for turn in snapshot.terminal_turns} == set(outcomes)
    assert {event["type"] for event in snapshot.terminal_event_envelopes} == {
        "turn_completed",
        "turn_failed",
        "turn_cancelled",
    }
    assert snapshot.retained_history == "partial"
    assert rehydrated.event_seq == 3
    assert list(rehydrated.event_log) == []

    with pytest.raises(HTTPException) as partial_replay:
        await restarted_service.prepare_event_stream(record.session_id, replay=True)
    assert partial_replay.value.status_code == 409
    assert partial_replay.value.detail["code"] == "resume_window_exceeded"
    assert partial_replay.value.detail["last_event_id"] is None

    replayed_submit = await restarted_service.send_input(
        record.session_id,
        SessionInputRequest(
            content=raw_prompt,
            attachments=[raw_attachment, raw_url],
            client_message_id=submit_key,
        ),
    )
    assert replayed_submit.disposition == "deduplicated"
    assert replayed_submit.original_disposition == "started"
    assert (replayed_submit.input_id, replayed_submit.turn_id) == ("input-1", "turn-1")

    with pytest.raises(HTTPException) as changed_submit:
        await restarted_service.send_input(
            record.session_id,
            SessionInputRequest(
                content="different body",
                attachments=[raw_attachment, raw_url],
                client_message_id=submit_key,
            ),
        )
    assert changed_submit.value.detail["code"] == "input_idempotency_conflict"

    replayed_cancel = await restarted_service.cancel_turn(
        record.session_id,
        "turn-3",
        SessionTurnCancelRequest(
            cancellation_request_key=cancel_key,
            reason="user_requested",
        ),
    )
    assert replayed_cancel.disposition == "deduplicated"
    assert replayed_cancel.cancellation_request_id == "cancel-request-1"
    assert replayed_cancel.turn_id == "turn-3"
    await _stop_dispatcher(rehydrated)


@pytest.mark.asyncio
async def test_destructive_delete_is_repeat_idempotent_and_state_stays_absent(tmp_path: Path) -> None:
    state_root = tmp_path / "session_state"
    registry = SessionRegistry(state_root)
    record = SessionRecord(session_id="delete-me", status=SessionStatus.COMPLETED)
    turn = TurnRecord(
        input_id="input-delete",
        turn_id="turn-delete",
        client_message_id="delete-key",
        content="delete-secret-body",
        attachments=(),
        original_disposition="started",
        state="completed",
        terminal_outcome="completed",
    )
    record.turns_by_id[turn.turn_id] = turn
    record.submissions_by_key[turn.client_message_id] = turn
    await registry.create(record)
    await registry.persist(
        record,
        terminal_event=_logged_event(
            record.session_id,
            1,
            event_type=EventType.TURN_COMPLETED,
            input_id=turn.input_id,
            turn_id=turn.turn_id,
            payload={},
        ),
    )
    service = SessionService(registry)

    await service.stop_session(record.session_id)
    await service.stop_session(record.session_id)
    await registry.persist(record)
    assert list(state_root.glob("*.json")) == []

    with pytest.raises(HTTPException) as lookup:
        await service.ensure_session(record.session_id)
    assert lookup.value.status_code == 404

    absent_after_restart = SessionService(SessionRegistry(state_root))
    with pytest.raises(HTTPException) as attach:
        stream = absent_after_restart.event_stream(record.session_id)
        await stream.__anext__()
    assert attach.value.status_code == 404
