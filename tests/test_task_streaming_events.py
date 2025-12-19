from __future__ import annotations

import json
import os
from pathlib import Path

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.runtime_context import bind_session_state
from agentic_coder_prototype.state.session_state import SessionState


def test_task_tool_emits_streaming_events(tmp_path: Path) -> None:
    replay_session = tmp_path / "child_replay.json"
    replay_session.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "message_id": "u0",
                    "parts": [{"id": "u0_p0", "type": "text", "text": "Child prompt"}],
                },
                {
                    "role": "assistant",
                    "message_id": "a0",
                    "parts": [
                        {
                            "id": "tool_bash_0",
                            "type": "tool",
                            "tool": "bash",
                            "meta": {
                                "state": {
                                    "status": "completed",
                                    "input": {"command": "echo hi", "description": "Print hi"},
                                    "output": "",
                                }
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "message_id": "a1",
                    "parts": [{"id": "a1_p0", "type": "text", "text": "TASK COMPLETE"}],
                },
            ]
        ),
        encoding="utf-8",
    )

    events: list[tuple[str, dict]] = []

    def emitter(event_type: str, payload: dict, turn: int | None = None) -> None:
        events.append((event_type, payload))

    logging_root = tmp_path / "logs"
    workspace = tmp_path / "ws"

    config = {
        "logging": {"enabled": True, "root_dir": str(logging_root)},
        "provider_tools": {"use_native": True, "suppress_prompts": True},
        "tools": {
            "registry": {
                "paths": ["implementations/tools/defs_oc"],
                "include": ["task", "bash"],
            }
        },
        "loop": {"sequence": [{"mode": "build"}]},
        "modes": [{"name": "build", "prompt": "", "tools_enabled": ["*"], "tools_disabled": []}],
        "task_tool": {
            "subagents": {
                "general": {
                    "model": "replay",
                    "tools": ["bash"],
                    "child_replay_session": str(replay_session),
                }
            }
        },
    }

    os.environ.setdefault("MOCK_API_KEY", "kc_test_key")

    conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = conductor_cls(workspace=str(workspace), config=config, local_mode=True)

    session_state = SessionState(workspace=str(workspace), image="python-dev:latest", config=config, event_emitter=emitter)
    with bind_session_state(session_state):
        out = conductor._exec_raw(
            {
                "function": "task",
                "arguments": {
                    "description": "Child run",
                    "prompt": "ignored (child replay controls turns)",
                    "subagent_type": "general",
                },
            }
        )

    assert out.get("title") == "Child run"
    assert out.get("output") == "TASK COMPLETE"

    task_events = [payload for (etype, payload) in events if etype == "task_event"]
    assert any(evt.get("kind") == "started" for evt in task_events)
    assert any(evt.get("kind") == "completed" for evt in task_events)
    assert any(evt.get("kind") == "tool_call" for evt in task_events)
    assert not any(evt.get("kind") == "assistant_message" for evt in task_events)


def test_task_tool_can_forward_subagent_assistant_messages_when_enabled(tmp_path: Path) -> None:
    replay_session = tmp_path / "child_replay.json"
    replay_session.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "message_id": "u0",
                    "parts": [{"id": "u0_p0", "type": "text", "text": "Child prompt"}],
                },
                {
                    "role": "assistant",
                    "message_id": "a0",
                    "parts": [{"id": "a0_p0", "type": "text", "text": "Hello from child"}],
                },
            ]
        ),
        encoding="utf-8",
    )

    events: list[tuple[str, dict]] = []

    def emitter(event_type: str, payload: dict, turn: int | None = None) -> None:
        events.append((event_type, payload))

    logging_root = tmp_path / "logs"
    workspace = tmp_path / "ws"

    config = {
        "logging": {"enabled": True, "root_dir": str(logging_root)},
        "provider_tools": {"use_native": True, "suppress_prompts": True},
        "tools": {
            "registry": {
                "paths": ["implementations/tools/defs_oc"],
                "include": ["task", "bash"],
            }
        },
        "loop": {"sequence": [{"mode": "build"}]},
        "modes": [{"name": "build", "prompt": "", "tools_enabled": ["*"], "tools_disabled": []}],
        "task_tool": {
            "streaming": {"forward_assistant_messages": True},
            "subagents": {
                "general": {
                    "model": "replay",
                    "tools": ["bash"],
                    "child_replay_session": str(replay_session),
                }
            },
        },
    }

    os.environ.setdefault("MOCK_API_KEY", "kc_test_key")

    conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = conductor_cls(workspace=str(workspace), config=config, local_mode=True)

    session_state = SessionState(workspace=str(workspace), image="python-dev:latest", config=config, event_emitter=emitter)

    with bind_session_state(session_state):
        conductor._exec_raw(
            {
                "function": "task",
                "arguments": {
                    "description": "Child run",
                    "prompt": "ignored (child replay controls turns)",
                    "subagent_type": "general",
                },
            }
        )

    task_events = [payload for (etype, payload) in events if etype == "task_event"]
    assert any(evt.get("kind") == "assistant_message" for evt in task_events)

