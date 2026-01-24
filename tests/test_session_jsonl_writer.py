from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_coder_prototype.state.session_jsonl import (
    SessionJsonlStore,
    SessionJsonlWriter,
    build_session_header,
)


@pytest.mark.asyncio
async def test_session_jsonl_writer_appends_entries(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionJsonlStore(path)
    store.write_header(build_session_header(session_id="sess-1", cwd="/tmp"))
    writer = SessionJsonlWriter(store=store)

    events = [
        {"type": "user.message", "payload": {"content": "Hello"}, "ts": 1700000000000},
        {"type": "assistant.message.start", "payload": {"message_id": "m1"}, "ts": 1700000001000},
        {"type": "assistant.message.delta", "payload": {"message_id": "m1", "delta": "Hi"}, "ts": 1700000002000},
        {"type": "assistant.message.end", "payload": {"message_id": "m1"}, "ts": 1700000003000},
        {"type": "assistant.tool_call.start", "payload": {"tool_call_id": "c1", "tool_name": "lookup"}},
        {"type": "assistant.tool_call.delta", "payload": {"tool_call_id": "c1", "args_text_delta": "{\"q\":1}"}},
        {"type": "assistant.tool_call.end", "payload": {"tool_call_id": "c1", "args": {"q": 1}}},
        {"type": "tool.result", "payload": {"tool_call_id": "c1", "result": "ok", "status": "success"}},
        {"type": "session.compaction", "payload": {"summary": "compacted"}},
        {"type": "session.branch_summary", "payload": {"summary": "branch"}},
    ]

    for event in events:
        await writer.append_event(event)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    types = [line.get("type") for line in lines]
    assert types[0] == "session"
    assert "message" in types
    assert "custom" in types
    assert "compaction" in types
    assert "branch_summary" in types
