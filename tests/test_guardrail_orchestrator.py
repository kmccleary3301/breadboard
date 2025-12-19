from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

from agentic_coder_prototype.guardrail_coordinator import GuardrailCoordinator
from agentic_coder_prototype.guardrail_orchestrator import GuardrailOrchestrator


class DummySessionState:
    def __init__(self) -> None:
        self._meta: Dict[str, Any] = {}
        self.messages: List[Dict[str, Any]] = []
        self.guardrail_events: List[Dict[str, Any]] = []
        self.counters: Dict[str, int] = {}
        self.transcript: List[Dict[str, Any]] = []
        self._tool_free_streak = 0
        self._todo_activity = False

    # Metadata helpers
    def get_provider_metadata(self, key: str, default=None):
        return self._meta.get(key, default)

    def set_provider_metadata(self, key: str, value) -> None:
        self._meta[key] = value

    # Guardrail event helpers
    def record_guardrail_event(self, type_: str, payload: Dict[str, Any]) -> None:
        self.guardrail_events.append({"type": type_, "payload": dict(payload)})

    def increment_guardrail_counter(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1

    # Transcript helpers
    def add_transcript_entry(self, entry: Dict[str, Any]) -> None:
        self.transcript.append(entry)

    # Messages
    def add_message(self, msg: Dict[str, Any], to_provider: bool = False) -> None:
        self.messages.append(dict(msg, to_provider=to_provider))

    # Zero-tool helpers
    def turn_had_todo_activity(self) -> bool:
        return self._todo_activity

    def reset_tool_free_streak(self) -> None:
        self._tool_free_streak = 0

    def increment_tool_free_streak(self) -> int:
        self._tool_free_streak += 1
        return self._tool_free_streak


class DummyMarkdownLogger:
    def __init__(self) -> None:
        self.user_messages: List[str] = []

    def log_user_message(self, message: str) -> None:
        self.user_messages.append(message)


def _make_orchestrator(
    *,
    zero_warn: int = 1,
    zero_abort: int = 2,
) -> GuardrailOrchestrator:
    # Minimal config; GuardrailCoordinator will fall back to defaults where needed.
    cfg: Dict[str, Any] = {}
    coordinator = GuardrailCoordinator(cfg)
    plan_bootstrapper = SimpleNamespace(config={"warmup_turns": 1}, strategy="none")  # type: ignore[assignment]
    return GuardrailOrchestrator(
        cfg,
        coordinator,
        plan_bootstrapper,  # type: ignore[arg-type]
        completion_guard_abort_threshold=2,
        completion_guard_handler=None,
        zero_tool_warn_turns=zero_warn,
        zero_tool_abort_turns=zero_abort,
        zero_tool_warn_message="WARN_ZERO_TOOL",
        zero_tool_abort_message="ABORT_ZERO_TOOL",
        zero_tool_emit_event=True,
    )


def test_zero_tool_guard_warns_then_aborts() -> None:
    orchestrator = _make_orchestrator(zero_warn=1, zero_abort=2)
    session = DummySessionState()
    logger = DummyMarkdownLogger()

    # First zero-tool turn -> warning, no abort
    abort, payload = orchestrator.handle_zero_tool_turn(
        session_state=session,
        markdown_logger=logger,
        stream_responses=False,
    )
    assert abort is False
    # Warning message should be logged once
    assert any("WARN_ZERO_TOOL" in msg for msg in logger.user_messages)
    assert any(
        ev["type"] == "zero_tool_watchdog" and ev["payload"]["action"] == "warn"
        for ev in session.guardrail_events
    )

    # Second zero-tool turn -> abort
    abort, payload = orchestrator.handle_zero_tool_turn(
        session_state=session,
        markdown_logger=logger,
        stream_responses=False,
    )
    assert abort is True
    assert payload is not None
    # Abort guardrail event recorded
    assert any(
        ev["type"] == "zero_tool_watchdog" and ev["payload"]["action"] == "abort"
        for ev in session.guardrail_events
    )
    # Abort counter incremented
    assert session.counters.get("zero_tool_abort", 0) >= 1


def test_loop_detection_emits_guardrail_event_and_metadata() -> None:
    orchestrator = _make_orchestrator()
    session = DummySessionState()

    class DummyLoopDetector:
        def __init__(self) -> None:
            self.calls = 0

        def check(self) -> str:
            self.calls += 1
            return "loop_detected"

    detector = DummyLoopDetector()
    orchestrator.handle_loop_detection(
        session_state=session,
        loop_detector=detector,
        turn_index=3,
    )

    # Loop detector must be called once
    assert detector.calls == 1
    # A loop_detection guardrail event must be recorded
    assert any(
        ev["type"] == "loop_detection"
        and ev["payload"].get("reason") == "loop_detected"
        and ev["payload"].get("turn") == 3
        for ev in session.guardrail_events
    )
    # Metadata and transcript should carry loop_detection payload
    payload = session.get_provider_metadata("loop_detection_payload")
    assert payload and payload.get("reason") == "loop_detected" and payload.get("turn") == 3
    assert any("loop_detection" in entry for entry in session.transcript)


def test_loop_detection_skipped_in_replay_mode() -> None:
    orchestrator = _make_orchestrator()
    session = DummySessionState()
    session.set_provider_metadata("replay_mode", True)

    class DummyLoopDetector:
        def __init__(self) -> None:
            self.calls = 0

        def check(self) -> str:
            self.calls += 1
            return "loop_detected"

    detector = DummyLoopDetector()
    orchestrator.handle_loop_detection(
        session_state=session,
        loop_detector=detector,
        turn_index=5,
    )

    # Loop detector should not run and no guardrail events recorded during replay.
    assert detector.calls == 0
    assert session.guardrail_events == []
    assert session.get_provider_metadata("loop_detection_payload") is None
    assert not any("loop_detection" in entry for entry in session.transcript)


def test_handle_blocked_calls_emits_workspace_validation_error() -> None:
    orchestrator = _make_orchestrator()
    session = DummySessionState()
    logger = DummyMarkdownLogger()

    blocked_call = SimpleNamespace(function="patch", arguments={})
    turn_ctx = SimpleNamespace(
        blocked_calls=[
            {
                "call": blocked_call,
                "source": "workspace",
                "reason": "<VALIDATION_ERROR>\nneed exploration\n</VALIDATION_ERROR>",
            }
        ]
    )

    orchestrator.handle_blocked_calls(turn_ctx, session, logger)  # type: ignore[arg-type]

    assert any("VALIDATION_ERROR" in msg for msg in logger.user_messages)
    assert any(ev["type"] == "workspace_block" for ev in session.guardrail_events)
    assert session.counters.get("workspace_guard_violation", 0) == 1

