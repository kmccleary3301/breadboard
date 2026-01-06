from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.parity import EquivalenceLevel
from agentic_coder_prototype.parity_runner import run_parity_checks


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _write_run_dir(base: Path, guard_event: dict, todo_event: dict) -> None:
    final_dir = base / "final_container_dir"
    (base / "meta").mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "foo.txt").write_text("hello\n")
    summary = {
        "guardrail_events": [guard_event],
        "completion_summary": {"completed": True},
        "guardrails": {"zero_tool_watchdog": 1},
    }
    (base / "meta" / "run_summary.json").write_text(json.dumps(summary, indent=2))
    todo_store = {"journal": [todo_event]}
    todo_path = final_dir / ".breadboard"
    todo_path.mkdir(parents=True, exist_ok=True)
    (todo_path / "todos.json").write_text(json.dumps(todo_store, indent=2))


def test_parity_checks_pass(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    golden = tmp_path / "golden"
    _write_run_dir(
        run_dir,
        {"type": "zero_tool_watchdog", "payload": {"action": "warn"}},
        {"type": "todo.created", "payload": {"todo": {"title": "Sample"}}},
    )
    golden.mkdir()
    (golden / "foo.txt").write_text("hello\n")
    guard_expected = tmp_path / "guardrails.json"
    _write_json(guard_expected, [{"type": "zero_tool_watchdog", "payload": {"action": "warn"}}])
    todo_expected = tmp_path / "todos.jsonl"
    todo_expected.write_text(json.dumps({"type": "todo.created", "payload": {"todo": {"title": "Sample"}}}) + "\n")
    summary_expected = tmp_path / "summary.json"
    _write_json(summary_expected, {"completion_summary": {"completed": True}})

    payload = run_parity_checks(
        run_dir=run_dir,
        golden_workspace=golden,
        guardrail_path=guard_expected,
        todo_journal_path=todo_expected,
        summary_path=summary_expected,
        target_level=EquivalenceLevel.NORMALIZED_TRACE,
    )

    assert payload["status"] == "passed"
    assert payload["failure_level"] is None


def test_parity_checks_detect_workspace_drift(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    golden = tmp_path / "golden"
    _write_run_dir(
        run_dir,
        {"type": "zero_tool_watchdog", "payload": {"action": "warn"}},
        {"type": "todo.created", "payload": {"todo": {"title": "Sample"}}},
    )
    golden.mkdir()
    (golden / "foo.txt").write_text("goodbye\n")
    guard_expected = tmp_path / "guardrails.json"
    _write_json(guard_expected, [{"type": "zero_tool_watchdog", "payload": {"action": "warn"}}])
    todo_expected = tmp_path / "todos.jsonl"
    todo_expected.write_text(json.dumps({"type": "todo.created", "payload": {"todo": {"title": "Sample"}}}) + "\n")
    summary_expected = tmp_path / "summary.json"
    _write_json(summary_expected, {"completion_summary": {"completed": True}})

    payload = run_parity_checks(
        run_dir=run_dir,
        golden_workspace=golden,
        guardrail_path=guard_expected,
        todo_journal_path=todo_expected,
        summary_path=summary_expected,
        target_level=EquivalenceLevel.NORMALIZED_TRACE,
    )

    assert payload["status"] == "failed"
    assert payload["failure_level"] == EquivalenceLevel.SEMANTIC.value
    assert any("foo.txt" in entry for entry in payload.get("mismatches", []))
