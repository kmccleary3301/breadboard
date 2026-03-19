from __future__ import annotations

import json
from pathlib import Path

from breadboard_ext.darwin.stage3 import (
    STAGE3_CONSUMED_LANES,
    STAGE3_TARGETABLE_LANES,
    build_stage3_budget_envelope,
    build_stage3_optimization_target,
    consume_execution_plan_bindings,
)
from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs


def _load_spec(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_build_stage3_optimization_target_for_repo_swe() -> None:
    write_bootstrap_specs()
    spec = _load_spec("artifacts/darwin/bootstrap/camp.darwin.phase1.repo_swe.bootstrap.v0.json")
    target = build_stage3_optimization_target(
        lane_id="lane.repo_swe",
        spec=spec,
        baseline_artifact_ref="artifacts/darwin/candidates/cand.lane.repo_swe.baseline.v1.json",
        task_id="task.darwin.repo_swe.patch_workspace_smoke",
        topology_id=spec["topology_family"],
        policy_bundle_id=spec["policy_bundle_id"],
    )
    assert "lane.repo_swe" in STAGE3_TARGETABLE_LANES
    assert target.target_kind == "repo_patch_workspace"
    assert target.support_envelope.models == ["gpt-5.4-mini", "gpt-5.4-nano"]
    assert target.locus_ids() == ["topology.params", "operator.family", "tool.scope", "policy.bundle"]


def test_build_stage3_optimization_target_for_systems() -> None:
    write_bootstrap_specs()
    spec = _load_spec("artifacts/darwin/bootstrap/camp.darwin.phase1.systems.bootstrap.v0.json")
    target = build_stage3_optimization_target(
        lane_id="lane.systems",
        spec=spec,
        baseline_artifact_ref="artifacts/darwin/candidates/cand.lane.systems.baseline.v1.json",
        task_id="task.darwin.systems.reward_smoke",
        topology_id=spec["topology_family"],
        policy_bundle_id=spec["policy_bundle_id"],
    )
    assert target.target_kind == "systems_reward_runtime"
    assert "evaluator.control" in target.locus_ids()


def test_consume_execution_plan_bindings_returns_narrow_runtime_fields() -> None:
    payload = {
        "bindings": {
            "cwd": "/tmp/example",
            "out_dir": "artifacts/out",
            "command": ["python", "-m", "pytest", "-q"],
        }
    }
    consumed = consume_execution_plan_bindings(payload)
    assert consumed["cwd"] == "/tmp/example"
    assert consumed["out_dir"] == "artifacts/out"
    assert consumed["command"] == ["python", "-m", "pytest", "-q"]
    assert consumed["consumed_fields"] == ["bindings.command", "bindings.cwd", "bindings.out_dir"]


def test_stage3_budget_envelope_contains_budget_and_reserves() -> None:
    envelope = build_stage3_budget_envelope(
        budget_class="budget.class_a",
        wall_clock_ms=1250,
        token_counts={"prompt": 10, "completion": 4},
        cost_estimate=0.125,
        route_id="openrouter/openai/gpt-5.4-mini",
        provider_model="openai/gpt-5.4-mini-20260317",
    )
    assert envelope["budget_class"] == "budget.class_a"
    assert envelope["wall_clock_ms"] == 1250
    assert envelope["token_counts"]["prompt_tokens"] == 10
    assert envelope["token_counts"]["total_tokens"] == 14
    assert envelope["cost_classification"] == "estimated_route_priced"
    assert envelope["route_id"] == "openrouter/openai/gpt-5.4-mini"
    assert envelope["replication_reserve_fraction"] == 0.2
    assert envelope["control_reserve_fraction"] == 0.1
