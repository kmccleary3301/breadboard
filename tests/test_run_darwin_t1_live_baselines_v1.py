from __future__ import annotations

import json
from pathlib import Path

from breadboard_ext.darwin.phase2 import build_effective_policy
from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_t1_live_baselines_v1 import run_live_baselines


def test_run_live_baselines_emits_six_lane_summary() -> None:
    write_bootstrap_specs()
    summary = run_live_baselines()
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["lane_count"] == 6
    assert {row["lane_id"] for row in payload["lanes"]} == {"lane.atp", "lane.harness", "lane.systems", "lane.repo_swe", "lane.scheduling", "lane.research"}
    rows = {row["lane_id"]: row for row in payload["lanes"]}
    for lane_id in ("lane.harness", "lane.repo_swe"):
        refs = rows[lane_id]["shadow_artifact_refs"]
        assert refs["effective_config"].endswith("_effective_config_v0.json")
        assert refs["execution_plan"].endswith("_execution_plan_v0.json")
        assert refs["effective_policy"].endswith("_effective_policy_v0.json")
        assert refs["evaluator_pack"].endswith("_evaluator_pack_v0.json")
        assert Path(refs["effective_config"]).exists()
        assert Path(refs["execution_plan"]).exists()
        assert Path(refs["effective_policy"]).exists()
        assert Path(refs["evaluator_pack"]).exists()
        assert rows[lane_id]["stage3_execution_plan_consumption"]["consumed_fields"] == [
            "bindings.command",
            "bindings.cwd",
            "bindings.out_dir",
            "bindings.tool_bindings",
            "bindings.budget_class",
        ]
        execution_plan = json.loads(Path(refs["execution_plan"]).read_text(encoding="utf-8"))
        assert execution_plan["runtime_consumed"] is True
        assert execution_plan["consumed_bindings"] == [
            "bindings.command",
            "bindings.cwd",
            "bindings.out_dir",
            "bindings.tool_bindings",
            "bindings.budget_class",
        ]
        assert rows[lane_id]["stage3_execution_plan_consumption"]["budget_class"] == rows[lane_id]["budget_class"]
        assert rows[lane_id]["stage3_execution_plan_consumption"]["tool_bindings"]
    harness_policy = json.loads(Path(rows["lane.harness"]["shadow_artifact_refs"]["effective_policy"]).read_text(encoding="utf-8"))
    assert harness_policy["topology_support"]["allowed_topology_ids"] == ["policy.topology.single_v0", "policy.topology.pev_v0"]
    assert harness_policy["topology_support"]["is_supported"] is True
    assert "mut.topology.single_to_pev_v1" in harness_policy["operator_eligibility"]["supported_operator_ids"]
    assert "mut.budget.class_a_to_class_b_v1" in harness_policy["operator_eligibility"]["prohibited_operator_ids"]
    harness_evaluator_pack = json.loads(Path(rows["lane.harness"]["shadow_artifact_refs"]["evaluator_pack"]).read_text(encoding="utf-8"))
    assert harness_evaluator_pack["budget_envelope"]["cost_classification"] == "exact_local_zero"
    assert harness_evaluator_pack["budget_envelope"]["comparison_class"] == "bounded_internal"
    assert harness_evaluator_pack["budget_envelope"]["wall_clock_ms"] == rows["lane.harness"]["wall_clock_ms"]
    assert rows["lane.repo_swe"]["shadow_artifact_refs"]["optimization_target"].endswith("_optimization_target_v1.json")
    repo_target = json.loads(Path(rows["lane.repo_swe"]["shadow_artifact_refs"]["optimization_target"]).read_text(encoding="utf-8"))
    assert repo_target["target_kind"] == "repo_patch_workspace"
    assert repo_target["support_envelope"]["models"] == ["gpt-5.4-mini", "gpt-5.4-nano"]
    assert rows["lane.harness"]["shadow_artifact_refs"].get("optimization_target") is None
    assert rows["lane.atp"]["stage3_execution_plan_consumption"] is None
    for lane_id in ("lane.atp", "lane.systems", "lane.scheduling", "lane.research"):
        if lane_id == "lane.systems":
            assert rows[lane_id]["shadow_artifact_refs"]["optimization_target"].endswith("_optimization_target_v1.json")
        else:
            assert rows[lane_id]["shadow_artifact_refs"] == {}


def test_effective_policy_builder_supports_secondary_audit_lanes() -> None:
    write_bootstrap_specs()
    atp_spec = json.loads(Path("artifacts/darwin/bootstrap/camp.darwin.phase1.atp.bootstrap.v0.json").read_text(encoding="utf-8"))
    scheduling_spec = json.loads(Path("artifacts/darwin/bootstrap/camp.darwin.phase1.scheduling.bootstrap.v0.json").read_text(encoding="utf-8"))

    atp_policy = build_effective_policy(
        spec=atp_spec,
        lane_id="lane.atp",
        candidate_id="cand.lane.atp.audit.v1",
        trial_label="audit",
        topology_id=atp_spec["topology_family"],
        policy_bundle_id=atp_spec["policy_bundle_id"],
        budget_class=atp_spec["budget_class"],
    )
    scheduling_policy = build_effective_policy(
        spec=scheduling_spec,
        lane_id="lane.scheduling",
        candidate_id="cand.lane.scheduling.audit.v1",
        trial_label="audit",
        topology_id=scheduling_spec["topology_family"],
        policy_bundle_id=scheduling_spec["policy_bundle_id"],
        budget_class=scheduling_spec["budget_class"],
    )

    assert "policy.topology.pwrv_v0" in atp_policy["topology_support"]["allowed_topology_ids"]
    assert scheduling_policy["topology_support"]["allowed_topology_ids"] == ["policy.topology.single_v0", "policy.topology.pev_v0"]
