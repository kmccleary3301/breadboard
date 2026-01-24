from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_coder_prototype.ctrees.backfill import backfill_ctrees_from_eventlog
from agentic_coder_prototype.ctrees.store import CTreeStore
from agentic_coder_prototype.ctrees.summary import build_ctree_hash_summary


def test_backfill_ctrees_from_eventlog_roundtrip(tmp_path: Path) -> None:
    eventlog = tmp_path / "events.jsonl"
    eventlog.write_text(
        "\n".join(
            [
                json.dumps({"type": "user_message", "payload": {"message": {"role": "user", "content": "hi"}}}),
                json.dumps(
                    {
                        "type": "ctree_node",
                        "payload": {"node": {"id": "n00000000_deadbeef", "kind": "message", "turn": 1, "payload": {"role": "user", "content": "hi"}}},
                    }
                ),
                json.dumps(
                    {
                        "type": "ctree_node",
                        "payload": {"node": {"id": "n00000001_beadfeed", "kind": "message", "turn": 1, "payload": {"role": "assistant", "content": "hello"}}},
                    }
                ),
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "ctrees"
    result = backfill_ctrees_from_eventlog(eventlog_path=eventlog, out_dir=out_dir)
    assert Path(result["paths"]["events"]).exists()
    assert Path(result["paths"]["snapshot"]).exists()

    store = CTreeStore.from_dir(out_dir)
    assert store.snapshot()["node_count"] == 2
    assert [node.get("id") for node in store.nodes] == ["n00000000_deadbeef", "n00000001_beadfeed"]

    snapshot_payload = json.loads(Path(result["paths"]["snapshot"]).read_text(encoding="utf-8"))
    assert snapshot_payload.get("backfilled_from_eventlog") is True

    hash_summary = build_ctree_hash_summary(snapshot=snapshot_payload, compiler=None, collapse=None, runner=None)
    assert hash_summary.get("snapshot") is not None
    assert hash_summary.get("summary_sha256")


def test_backfill_ctrees_from_eventlog_refuses_overwrite(tmp_path: Path) -> None:
    eventlog = tmp_path / "events.jsonl"
    eventlog.write_text(json.dumps({"type": "ctree_node", "payload": {"node": {"id": "n0", "kind": "message", "payload": {}}}}) + "\n")

    out_dir = tmp_path / "ctrees"
    backfill_ctrees_from_eventlog(eventlog_path=eventlog, out_dir=out_dir)
    with pytest.raises(FileExistsError):
        backfill_ctrees_from_eventlog(eventlog_path=eventlog, out_dir=out_dir)
