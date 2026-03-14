from __future__ import annotations

from scripts.run_darwin_phase1_orchestration_v1 import run_orchestration


def test_run_darwin_phase1_orchestration_executes_full_flow() -> None:
    summary = run_orchestration()
    assert summary["live"]["lane_count"] == 5
    assert summary["typed_search"]["lane_count"] == 3
    assert summary["search"]["mutation_trial_count"] == 6
    assert summary["readiness"]["overall_ok"] is True
