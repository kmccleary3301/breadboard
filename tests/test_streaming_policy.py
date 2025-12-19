from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from agentic_coder_prototype.streaming_policy import StreamingPolicy


class DummyMetrics:
    def __init__(self) -> None:
        self.overrides: List[Dict[str, Any]] = []

    def add_stream_override(self, route: Optional[str], reason: str) -> None:
        self.overrides.append({"route": route, "reason": reason})


class DummySession:
    def __init__(self) -> None:
        self.metadata: Dict[str, Any] = {}
        self.transcript: List[Dict[str, Any]] = []

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value

    def add_transcript_entry(self, entry: Dict[str, Any]) -> None:
        self.transcript.append(entry)


class DummyMarkdownLogger:
    def __init__(self) -> None:
        self.messages: List[str] = []

    def log_system_message(self, message: str) -> None:
        self.messages.append(message)


class DummyLoggerV2:
    def __init__(self) -> None:
        self.run_dir = "/tmp/dummy"
        self.writes: List[str] = []

    def append_text(self, path: str, content: str) -> None:
        self.writes.append(f"{path}:{content}")


class DummyMDWriter:
    def system(self, text: str) -> str:
        return text


def test_openrouter_tool_turn_disables_streaming() -> None:
    metrics = DummyMetrics()
    policy = StreamingPolicy(metrics)
    session = DummySession()
    markdown_logger = DummyMarkdownLogger()
    logger_v2 = DummyLoggerV2()

    effective, policy_payload = policy.apply(
        model="openrouter/gpt",
        runtime_descriptor=SimpleNamespace(provider_id="openrouter", runtime_id="chat"),
        tools_schema=[{"name": "write"}],
        stream_requested=True,
        session_state=session,
        markdown_logger=markdown_logger,
        turn_index=3,
        route_id="route-openrouter",
        routing_preferences={},
        capability=None,
        logger_v2=logger_v2,
        md_writer=DummyMDWriter(),
    )

    assert effective is False
    assert policy_payload is not None
    assert session.metadata["last_stream_policy"]["reason"] == "openrouter_tool_turn_policy"
    assert metrics.overrides == [{"route": "route-openrouter", "reason": "openrouter_tool_turn_policy"}]
    assert markdown_logger.messages, "System note should be emitted"
    assert session.transcript, "Transcript should capture policy entry"


def test_capability_failure_disables_streaming_and_logs_event() -> None:
    metrics = DummyMetrics()
    policy = StreamingPolicy(metrics)
    session = DummySession()
    markdown_logger = DummyMarkdownLogger()
    captured_events: List[Dict[str, Any]] = []

    def capture_event(**kwargs: Any) -> None:
        captured_events.append(kwargs)

    capability = {
        "attempted": True,
        "stream_success": False,
        "tool_stream_success": True,
        "json_mode_success": True,
    }

    effective, policy_payload = policy.apply(
        model="gpt-5-nano",
        runtime_descriptor=SimpleNamespace(provider_id="openai", runtime_id="chat"),
        tools_schema=None,
        stream_requested=True,
        session_state=session,
        markdown_logger=markdown_logger,
        turn_index=5,
        route_id="route-openai",
        routing_preferences={"disable_stream_on_probe_failure": True},
        capability=capability,
        logger_v2=None,
        md_writer=None,
        log_routing_event=capture_event,
    )

    assert effective is False
    assert policy_payload is not None
    assert policy_payload["reason"] == "capability_probe_stream_failure"
    assert metrics.overrides[-1]["reason"] == "capability_probe_stream_failure"
    assert captured_events, "Routing event should be emitted"
    assert session.metadata["capability_stream_override"]["reason"] == "capability_probe_stream_failure"
