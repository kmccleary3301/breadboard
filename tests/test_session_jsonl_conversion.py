from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.state.session_jsonl import convert_eventlog_to_session_jsonl


def _write_eventlog(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n",
        encoding="utf-8",
    )


def test_eventlog_conversion_emits_compaction_and_branch_entries(tmp_path: Path) -> None:
    events = [
        {
            "type": "session.start",
            "session_id": "sess-1",
            "payload": {"workspace": "/tmp/breadboard"},
        },
        {
            "type": "user.message",
            "session_id": "sess-1",
            "payload": {"content": "Hello"},
        },
        {
            "type": "assistant.message.start",
            "session_id": "sess-1",
            "payload": {"message_id": "m1"},
        },
        {
            "type": "assistant.message.delta",
            "session_id": "sess-1",
            "payload": {"message_id": "m1", "delta": "Hi!"},
        },
        {
            "type": "assistant.message.end",
            "session_id": "sess-1",
            "payload": {"message_id": "m1"},
        },
        {
            "type": "session.compaction",
            "session_id": "sess-1",
            "payload": {"summary": "compacted summary"},
        },
        {
            "type": "session.branch_summary",
            "session_id": "sess-1",
            "payload": {"summary": "branch summary"},
        },
    ]

    eventlog_path = tmp_path / "events.jsonl"
    out_path = tmp_path / "session.jsonl"
    _write_eventlog(eventlog_path, events)

    count = convert_eventlog_to_session_jsonl(
        eventlog_path=eventlog_path,
        out_path=out_path,
        overwrite=True,
    )
    assert count >= 3

    lines = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    types = [line.get("type") for line in lines]
    assert types[0] == "session"
    assert "compaction" in types
    assert "branch_summary" in types
