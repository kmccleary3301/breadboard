from __future__ import annotations
import os

from pathlib import Path
import time
import pytest

from breadboard_engine.orchestration.event_log import EventLog
from breadboard_engine.security import WorkspacePathError

from breadboard_engine.agent_llm_openai import OpenAIConductor


def _make_conductor(config: dict) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.workspace = str(Path.cwd())
    inst.image = "python-dev:latest"
    inst.local_mode = True
    inst._multi_agent_orchestrator = None
    inst._multi_agent_event_log_path = None
    return inst  # type: ignore[return-value]


def test_task_tool_requires_multi_agent_enabled() -> None:
    conductor = _make_conductor({})
    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {
                "description": "x",
                "prompt": "y",
                "subagent_type": "reviewer",
            },
        }
    )
    assert "unknown tool" in str(out.get("error"))


def test_lowercase_task_tool_can_launch_background_multi_agent_job(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("dummy workspace\n", encoding="utf-8")
    config = {
        "multi_agent": {
            "enabled": True,
            "team_config": {
                "team": {
                    "id": "test-team",
                    "version": 1,
                    "agents": {
                        "main": {"role": "main", "entrypoint": True},
                        "repo-scanner": {
                            "role": "repo-scanner",
                            "entrypoint": False,
                            "capabilities": {"read_only": True, "allow_spawn": False},
                        },
                    },
                    "topology": {
                        "edges": [
                            {"from": "main", "to": "repo-scanner", "mode": ["sync"]}
                        ]
                    },
                }
            },
        }
    }
    conductor = _make_conductor(config)
    conductor.workspace = str(tmp_path)
    conductor.image = "python-dev:latest"
    conductor.local_mode = True
    events: list[dict] = []
    conductor._emit_task_event = events.append  # type: ignore[method-assign]

    out = conductor._exec_raw(
        {
            "function": "task",
            "provider_name": "task",
            "arguments": {
                "description": "Scan workspace",
                "prompt": "List the workspace root.",
                "subagent_type": "repo-scanner",
                "run_in_background": True,
            },
        }
    )

    assert out.get("title") == "Scan workspace"
    metadata = out.get("metadata") or {}
    assert metadata.get("agentId")
    assert any(event.get("kind") == "subagent_spawned" for event in events)

    deadline = time.time() + 5
    while time.time() < deadline and not any(
        event.get("kind") == "subagent_completed" for event in events
    ):
        time.sleep(0.05)

    completed = [event for event in events if event.get("kind") == "subagent_completed"]
    assert completed
    assert completed[-1].get("subagent_type") == "repo-scanner"
    assert "README.md" in str(completed[-1].get("output_excerpt"))


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_multi_agent_event_log_rejects_linked_target(
    tmp_path: Path, link_kind: str
) -> None:
    event_log = EventLog()
    event_log.add("run.started", agent_id="main")
    secret = tmp_path / "credential"
    secret.write_text("event-log-link-canary", encoding="utf-8")
    target = tmp_path / "events.jsonl"
    if link_kind == "symlink":
        target.symlink_to(secret)
    else:
        os.link(secret, target)

    with pytest.raises(WorkspacePathError):
        event_log.to_jsonl(str(target))

    assert secret.read_text(encoding="utf-8") == "event-log-link-canary"
