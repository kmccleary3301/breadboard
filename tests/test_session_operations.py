from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.operations.model import OperationContext
from breadboard.product.operations.session import (
    ApproveSessionOutcome,
    ApproveSessionRequest,
    CancelSessionOutcome,
    CancelSessionRequest,
    GetSessionRequest,
    ListSessionArtifactsRequest,
    ListSessionEventsRequest,
    ListSessionsRequest,
    ResumeSessionOutcome,
    ResumeSessionRequest,
    SendSessionInputOutcome,
    SendSessionInputRequest,
    SessionRuntime,
    StartSessionOutcome,
    StartSessionRequest,
)
from breadboard.product.runtime import session_store
from breadboard.product.runtime.events import AnnotationRecord, Session


_HASH = "sha256:" + "a" * 64


def _session(session_id: str, *, terminal: bool = False) -> Session:
    session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": _HASH}),
        "task",
        session_id=session_id,
    )
    if terminal:
        session.complete("done")
    return session


class _LiveReads:
    def __init__(self, sessions: Sequence[Session]) -> None:
        self.sessions = tuple(sessions)

    async def get_live_session(self, session_id: str) -> Session | None:
        return next(
            (
                session
                for session in self.sessions
                if session.read_model.session_id == session_id
            ),
            None,
        )

    async def list_live_sessions(self) -> Sequence[Session]:
        return self.sessions

    async def get_live_artifacts(
        self,
        session_id: str,
    ) -> list[dict[str, object]] | None:
        if any(session.read_model.session_id == session_id for session in self.sessions):
            return [{"name": "live.txt", "digest": _HASH}]
        return None


class _MutationPort:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def start(
        self,
        request: StartSessionRequest,
        context: OperationContext,
        effective_lock: EffectiveHarnessLock,
        source_path: Path,
    ) -> StartSessionOutcome:
        del context, source_path
        self.session = Session.start(
            effective_lock,
            request.task,
            session_id=request.session_id,
        )
        return StartSessionOutcome(self.session.read_model)

    async def send_input(
        self,
        request: SendSessionInputRequest,
        context: OperationContext,
    ) -> SendSessionInputOutcome:
        del context
        return SendSessionInputOutcome(self.session.input(request.content))

    async def approve(
        self,
        request: ApproveSessionRequest,
        context: OperationContext,
    ) -> ApproveSessionOutcome:
        del context
        return ApproveSessionOutcome(
            self.session.resolve_approval(request.request_id, request.decision)
        )

    async def resume(
        self,
        request: ResumeSessionRequest,
        context: OperationContext,
    ) -> ResumeSessionOutcome:
        del context
        return ResumeSessionOutcome(self.session.resume())

    async def cancel(
        self,
        request: CancelSessionRequest,
        context: OperationContext,
    ) -> CancelSessionOutcome:
        del context
        return CancelSessionOutcome(self.session.cancel(request.reason))


@pytest.mark.asyncio
async def test_runtime_reads_durable_sessions_without_mutating_storage(
    tmp_path: Path,
) -> None:
    session = _session("durable", terminal=True)
    session_store.create_session(tmp_path, session)
    runtime = SessionRuntime(OperationContext(workspace=tmp_path))

    listed = await runtime.list_sessions(ListSessionsRequest())
    restored = await runtime.get_session(GetSessionRequest("durable"))
    events = await runtime.list_session_events(ListSessionEventsRequest("durable"))
    artifacts = await runtime.list_session_artifacts(
        ListSessionArtifactsRequest("durable")
    )

    assert listed.ok and listed.data["count"] == 1
    assert restored.ok and restored.data["session"]["status"] == "completed"
    assert events.ok and [event["seq"] for event in events.data["events"]] == [1, 2]
    assert artifacts.ok and artifacts.data["artifacts"] == []
    assert session_store.load_session(tmp_path, "durable")[0].read_model.status == (
        "completed"
    )


@pytest.mark.asyncio
async def test_runtime_event_listing_filters_internal_annotations(
    tmp_path: Path,
) -> None:
    session = _session("annotated")
    session.assistant_message(
        "candidate",
        message_id="message-a",
        trajectory_id="trajectory-a",
    )
    session.annotate(
        AnnotationRecord(
            annotation_id="annotation-1",
            message_id="message-a",
            trajectory_id="trajectory-a",
            label="preferred",
            author="reviewer-1",
            generation="generation-a",
        )
    )
    session.input("visible after annotation")
    session_store.create_session(tmp_path, session)

    listed = await SessionRuntime(
        OperationContext(workspace=tmp_path)
    ).list_session_events(ListSessionEventsRequest("annotated"))
    limited = await SessionRuntime(
        OperationContext(workspace=tmp_path)
    ).list_session_events(
        ListSessionEventsRequest("annotated", after_sequence=2, limit=1)
    )

    assert listed.ok and limited.ok
    assert [event["seq"] for event in listed.data["events"]] == [1, 2, 4]
    assert [event["kind"] for event in listed.data["events"]] == [
        "session.started",
        "assistant_message",
        "input.accepted",
    ]
    assert [event["seq"] for event in limited.data["events"]] == [4]
@pytest.mark.asyncio
async def test_runtime_reads_live_sessions_through_explicit_read_port(
    tmp_path: Path,
) -> None:
    live = _session("live")
    live.input("hello")
    session_store.session_directory(tmp_path).mkdir(parents=True)
    runtime = SessionRuntime(
        OperationContext(workspace=tmp_path),
        live_port=_LiveReads((live,)),
    )

    listed = await runtime.list_sessions(ListSessionsRequest())
    restored = await runtime.get_session(GetSessionRequest("live"))
    batch = await runtime.read_session_event_batch(
        ListSessionEventsRequest("live", after_sequence=1)
    )
    events = await runtime.list_session_events(ListSessionEventsRequest("live"))
    artifacts = await runtime.list_session_artifacts(
        ListSessionArtifactsRequest("live")
    )

    assert listed.ok and listed.data["sessions"] == [
        {"session_id": "live", "status": "running", "event_count": 2}
    ]
    assert restored.ok and restored.record_refs == []
    assert batch.source == "live" and batch.cursor == 2
    assert events.ok and [event["seq"] for event in events.data["events"]] == [1, 2]
    assert artifacts.ok and artifacts.data["artifacts"] == [
        {"name": "live.txt", "digest": _HASH}
    ]


@pytest.mark.asyncio
async def test_runtime_mutations_use_explicit_mutation_port() -> None:
    session = _session("mutating")
    mutation_port = _MutationPort(session)
    runtime = SessionRuntime(
        OperationContext(workspace=Path.cwd()),
        mutation_port=mutation_port,
    )

    accepted = await runtime.send_input(
        SendSessionInputRequest("mutating", "hello")
    )
    canceled = await runtime.cancel(
        CancelSessionRequest("mutating", reason="operator stop")
    )

    assert accepted.ok and accepted.data["session"]["event_count"] == 2
    assert canceled.ok and canceled.data["session"]["status"] == "canceled"
    assert mutation_port.session.read_model.status == "canceled"
