from __future__ import annotations

from agentic_coder_prototype.ctrees.phase12_live_audit import build_phase12_grounded_live_audit


def test_phase12_grounded_live_audit_marks_mini_rows_as_ungrounded_stops() -> None:
    payload = build_phase12_grounded_live_audit()

    practical_mini = payload["system_summaries"]["practical_gpt54_mini"]
    candidate_mini = payload["system_summaries"]["candidate_a_gpt54_mini"]

    assert practical_mini["row_count"] == 8
    assert candidate_mini["row_count"] == 8
    assert practical_mini["grounded_completion_count"] == 0
    assert candidate_mini["grounded_completion_count"] == 0
    assert practical_mini["ungrounded_stop_count"] >= 7
    assert candidate_mini["ungrounded_stop_count"] == 8


def test_phase12_grounded_live_audit_marks_flagship_rows_as_read_heavy_loops() -> None:
    payload = build_phase12_grounded_live_audit()

    candidate_flagship = payload["system_summaries"]["candidate_a_flagship"]

    assert candidate_flagship["row_count"] == 8
    assert candidate_flagship["grounded_completion_count"] == 0
    assert candidate_flagship["failure_family_counts"]["read_heavy_no_closure_loop"] >= 7


def test_phase12_grounded_live_audit_flags_missing_run_summary_as_harness_instability() -> None:
    payload = build_phase12_grounded_live_audit()

    practical_flagship = payload["system_summaries"]["practical_flagship"]

    assert practical_flagship["runner_instability_count"] >= 1
    target_row = next(row for row in payload["rows"] if row["task_id"] == "downstream_subtree_pressure_v1__r1")
    target = target_row["practical_flagship"]
    assert target["artifacts"]["run_summary_present"] is False
    assert target["classification"]["failure_family"] == "runner_harness_instability"
