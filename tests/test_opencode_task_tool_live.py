from __future__ import annotations

import json
import os
from pathlib import Path

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


def test_task_tool_live_nested_subagent_with_replay(tmp_path: Path) -> None:
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
    meta = out.get("metadata") or {}
    assert isinstance(meta, dict)
    assert isinstance(meta.get("sessionId"), str) and meta.get("sessionId")
    summary = meta.get("summary")
    assert isinstance(summary, list)
    assert summary and summary[0].get("tool") == "bash"
    assert out.get("output") == "TASK COMPLETE"
    assert out.get("__mvi_text_output") == "TASK COMPLETE"

