from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.messaging.markdown_logger import MarkdownLogger
from agentic_coder_prototype.orchestration.orchestrator import MultiAgentOrchestrator
from agentic_coder_prototype.orchestration.schema import TeamConfig
from agentic_coder_prototype.state.session_state import SessionState


def _make_conductor(*, model_visible_topics: list[str]) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = {}
    inst.workspace = str(Path.cwd())
    inst.image = "python-dev:latest"
    inst.local_mode = True
    inst._multi_agent_last_wakeup_event_id = 0

    team = TeamConfig.from_dict({"team": {"id": "t", "bus": {"model_visible_topics": model_visible_topics}}})
    inst._multi_agent_orchestrator = MultiAgentOrchestrator(team)
    return inst  # type: ignore[return-value]


def _make_session_state() -> SessionState:
    return SessionState(workspace=str(Path.cwd()), image="python-dev:latest", config={})


def test_wakeup_not_injected_when_topic_disabled() -> None:
    conductor = _make_conductor(model_visible_topics=[])
    orchestrator = conductor._multi_agent_orchestrator
    spawn = orchestrator.spawn_subagent(owner_agent="main", agent_id="repo-scanner", async_mode=True)
    orchestrator.emit_wakeup(spawn.job, reason="completed", message="hello")

    state = _make_session_state()
    logger = MarkdownLogger()
    conductor._inject_multi_agent_wakeups(state, logger)

    assert not any(m.get("role") == "system" and "hello" in str(m.get("content")) for m in state.messages)


def test_wakeup_injected_in_seq_order_and_deduped() -> None:
    conductor = _make_conductor(model_visible_topics=["wakeup"])
    orchestrator = conductor._multi_agent_orchestrator

    spawn_a = orchestrator.spawn_subagent(owner_agent="main", agent_id="repo-scanner", async_mode=True)
    spawn_b = orchestrator.spawn_subagent(owner_agent="main", agent_id="grep-summarizer", async_mode=True)
    orchestrator.emit_wakeup(spawn_b.job, reason="completed", message="B")
    orchestrator.emit_wakeup(spawn_a.job, reason="completed", message="A")

    state = _make_session_state()
    logger = MarkdownLogger()
    conductor._inject_multi_agent_wakeups(state, logger)

    injected = [m for m in state.messages if m.get("role") == "system"]
    assert [str(m.get("content")) for m in injected] == ["A", "B"]

    # Second injection call should not duplicate previously injected events.
    conductor._inject_multi_agent_wakeups(state, logger)
    injected2 = [m for m in state.messages if m.get("role") == "system"]
    assert len(injected2) == 2
