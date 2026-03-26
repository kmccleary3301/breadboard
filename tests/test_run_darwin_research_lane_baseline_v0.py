from __future__ import annotations

from scripts.run_darwin_research_lane_baseline_v0 import run_research_baseline


def test_run_research_baseline_prefers_bridge_synthesis() -> None:
    baseline = run_research_baseline(strategy="precision_first")
    coverage = run_research_baseline(strategy="coverage_first")
    bridge = run_research_baseline(strategy="bridge_synthesis")
    assert baseline["query_count"] == 3
    assert coverage["primary_score"] > baseline["primary_score"]
    assert bridge["primary_score"] >= coverage["primary_score"]
