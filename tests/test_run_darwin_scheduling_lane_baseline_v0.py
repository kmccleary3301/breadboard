from __future__ import annotations

from scripts.run_darwin_scheduling_lane_baseline_v0 import run_scheduling_baseline


def test_run_scheduling_baseline_prefers_better_mutation_strategy() -> None:
    baseline = run_scheduling_baseline(strategy="deadline_first")
    density = run_scheduling_baseline(strategy="value_density")
    hybrid = run_scheduling_baseline(strategy="hybrid_density_deadline")
    assert baseline["scenario_count"] == 3
    assert density["primary_score"] >= baseline["primary_score"]
    assert hybrid["primary_score"] >= density["primary_score"]
