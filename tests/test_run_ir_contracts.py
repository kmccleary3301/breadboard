from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.parity import (
    EquivalenceLevel,
    RunIR,
    build_expected_run_ir,
    build_run_ir_from_run_dir,
    compare_run_ir,
)


def test_run_ir_roundtrip_and_compare(tmp_path: Path) -> None:
    # Build a minimal logging directory and golden workspace
    run_dir = tmp_path / "run"
    golden = tmp_path / "golden"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    final_dir = run_dir / "final_container_dir"
    final_dir.mkdir(parents=True, exist_ok=True)
    golden.mkdir(parents=True, exist_ok=True)

    # Simple file content
    (final_dir / "foo.txt").write_text("hello\n", encoding="utf-8")
    (golden / "foo.txt").write_text("hello\n", encoding="utf-8")

    # Minimal run_summary.json
    summary = {
        "guardrail_events": [],
        "completion_summary": {"completed": True, "reason": "ok"},
        "guardrails": {},
        "turn_diagnostics": [],
    }
    (meta_dir / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Build actual and expected IRs
    actual_ir = build_run_ir_from_run_dir(run_dir)
    expected_ir = build_expected_run_ir(
        workspace_path=golden,
        guardrail_path=None,
        todo_journal_path=None,
        summary_path=None,
    )

    # Basic invariants
    assert isinstance(actual_ir, RunIR)
    assert isinstance(expected_ir, RunIR)
    assert actual_ir.workspace_path.exists()
    assert expected_ir.workspace_path.exists()

    # Round-trip actual IR through JSON
    encoded = json.dumps(actual_ir.__dict__, default=str)
    decoded = json.loads(encoded)
    # Ensure required keys survive
    for key in ["workspace_path", "completion_summary", "guard_events"]:
        assert key in decoded

    # Compare IRs at SEMANTIC level - should pass (no mismatches)
    mismatches = compare_run_ir(actual_ir, expected_ir, EquivalenceLevel.SEMANTIC)
    # At least workspace diffs should be empty; completion summaries may differ
    assert not any("Missing file in replay workspace" in m or "Content mismatch" in m for m in mismatches)


def test_expected_run_ir_loads_guardrail_events_from_summary(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    golden.mkdir(parents=True, exist_ok=True)

    meta_dir = tmp_path / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    summary_path = meta_dir / "run_summary.json"

    guard_events = [{"type": "zero_tool_abort", "turn": 6}]
    summary = {
        "guardrail_events": guard_events,
        "completion_summary": {"completed": True, "reason": "ok"},
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    expected_ir = build_expected_run_ir(
        workspace_path=golden,
        guardrail_path=None,
        todo_journal_path=None,
        summary_path=summary_path,
    )

    assert expected_ir.guard_events == guard_events

