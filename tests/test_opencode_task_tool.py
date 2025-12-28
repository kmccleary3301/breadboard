from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


def _make_conductor(config: dict) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    return inst  # type: ignore[return-value]


def test_task_tool_replay_session_extracts_summary_and_output(tmp_path: Path) -> None:
    child_session = tmp_path / "child_session.json"
    child_session.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "message_id": "u0",
                    "parts": [{"id": "u0_p0", "type": "text", "text": "Child task"}],
                },
                {
                    "role": "assistant",
                    "message_id": "a0",
                    "parts": [
                        {
                            "id": "tool_bash_0",
                            "type": "tool",
                            "tool": "bash",
                            "meta": {"state": {"input": {"command": "echo hi"}, "status": "completed", "output": "hi"}},
                        },
                        {"id": "a0_p0", "type": "text", "text": "done"},
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    conductor = _make_conductor(
        {
            "task_tool": {
                "subagents": {
                    "general": {
                        "replay_session": str(child_session),
                    }
                }
            }
        }
    )

    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {"description": "Child run", "prompt": "ignored", "subagent_type": "general"},
        }
    )

    assert out.get("title") == "Child run"
    meta = out.get("metadata") or {}
    assert isinstance(meta, dict)
    assert isinstance(meta.get("sessionId"), str) and meta.get("sessionId")
    summary = meta.get("summary")
    assert isinstance(summary, list)
    assert summary and summary[0].get("id") == "tool_bash_0"
    assert out.get("output") == "done"
    assert out.get("__mvi_text_output") == "done"


def test_task_tool_rejects_unknown_subagent_type() -> None:
    conductor = _make_conductor({"task_tool": {"subagents": {}}})
    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {"description": "x", "prompt": "y", "subagent_type": "does-not-exist"},
        }
    )
    assert "Unknown agent type" in str(out.get("error") or out.get("__mvi_text_output") or "")


def test_task_tool_errors_on_missing_replay_session(tmp_path: Path) -> None:
    conductor = _make_conductor({"task_tool": {"subagents": {"general": {"replay_session": str(tmp_path / 'nope.json')}}}})
    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {"description": "x", "prompt": "y", "subagent_type": "general"},
        }
    )
    assert "Failed to load task replay session" in str(out.get("error") or out.get("__mvi_text_output") or "")


def test_task_tool_replay_index_selects_session(tmp_path: Path) -> None:
    child_a = tmp_path / "child_a.json"
    child_b = tmp_path / "child_b.json"
    child_a.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "message_id": "u0",
                    "parts": [{"id": "u0_p0", "type": "text", "text": "Child A"}],
                },
                {
                    "role": "assistant",
                    "message_id": "a0",
                    "parts": [{"id": "a0_p0", "type": "text", "text": "alpha"}],
                },
            ]
        ),
        encoding="utf-8",
    )
    child_b.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "message_id": "u1",
                    "parts": [{"id": "u1_p0", "type": "text", "text": "Child B"}],
                },
                {
                    "role": "assistant",
                    "message_id": "a1",
                    "parts": [{"id": "a1_p0", "type": "text", "text": "bravo"}],
                },
            ]
        ),
        encoding="utf-8",
    )

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"ses_a": str(child_a), "ses_b": str(child_b)}), encoding="utf-8")

    conductor = _make_conductor(
        {
            "task_tool": {
                "subagents": {
                    "general": {
                        "replay_index": str(index_path),
                    }
                }
            }
        }
    )

    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {"description": "Child run", "prompt": "ignored", "subagent_type": "general"},
            "expected_output": "<task_metadata>\\nsession_id: ses_b\\n</task_metadata>",
            "expected_status": "completed",
        }
    )

    assert out.get("output") == "bravo"
    meta = out.get("metadata") or {}
    assert meta.get("sessionId") == "ses_b"


def test_task_tool_resume_returns_error() -> None:
    conductor = _make_conductor({})
    out = conductor._exec_raw(
        {
            "function": "task",
            "arguments": {
                "description": "Resume test",
                "prompt": "No-op",
                "subagent_type": "repo-scanner",
                "resume": "abc123",
            },
        }
    )
    assert "No transcript found for agent ID: abc123" in str(out.get("error") or out.get("__mvi_text_output") or "")
