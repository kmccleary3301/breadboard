from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.comparators.pi_coding_agent_0_73_1 import (
    PiCodingAgent0731Comparator,
    project_bb_trace,
    project_supplier_case,
)


def _trace(role: str = "supplier"):
    return {
        "schema_version": "bb.e4.pi-capture-trace.v1",
        "role": role,
        "request_count": 1,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "do it"}]},
            {"role": "assistant", "content": [{"type": "toolCall", "id": "pi-tool-x", "name": "write", "arguments": {"path": "x", "content": "ok"}}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolName": "write", "content": [{"type": "text", "text": "Successfully wrote 2 bytes to x"}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stopReason": "stop"},
        ],
        "effects": {"x": {"exists": True, "bytes": 2, "sha256": "sha256:abc"}},
        "termination": {"kind": "Submitted", "native_stop_reason": "stop"},
    }


def test_supplier_and_bb_projections_are_canonical(tmp_path: Path):
    case = tmp_path / "case"
    (case / "receiver").mkdir(parents=True)
    (case / "trace.json").write_text(json.dumps(_trace()))
    (case / "receiver" / "http-transcript.jsonl").write_text(json.dumps({"body": {"model": "x", "messages": [{"role": "user", "content": "do it"}], "tools": []}}) + "\n")
    expected = project_supplier_case(case)
    observed = project_bb_trace({**_trace("replay"), "requests": expected["requests"]})
    assert expected == observed


def test_comparator_rejects_missing_message_source():
    with pytest.raises(ValueError, match="messages or events"):
        project_bb_trace({"requests": []})


def test_comparator_rejects_non_supplier_case(tmp_path: Path):
    trace = _trace("replay")
    (tmp_path / "trace.json").write_text(json.dumps(trace))
    with pytest.raises(ValueError, match="role=supplier"):
        project_supplier_case(tmp_path)


def test_comparator_reports_negative_observation():
    expected = _trace("supplier")
    observed = _trace("replay")
    observed["effects"]["x"]["sha256"] = "sha256:wrong"
    report = PiCodingAgent0731Comparator()({"capture": expected, "replay": observed})
    assert report["passed"] is False
