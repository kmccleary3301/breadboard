from __future__ import annotations

from breadboard_ext.darwin.stage4 import (
    build_stage4_budget_envelope,
    build_stage4_support_envelope_digest,
    consume_execution_envelope_v2,
    resolve_stage4_route,
    validate_stage4_claim_eligibility,
    validate_stage4_matched_budget_pair,
)


def test_resolve_stage4_route_defaults_to_scaffold_without_provider_call() -> None:
    route = resolve_stage4_route(task_class="repo_patch_workspace", actual_provider_used=False)
    assert route["execution_mode"] == "scaffold"
    assert route["claim_eligible"] is False
    assert route["route_class"] == "default_worker"


def test_build_stage4_budget_envelope_carries_stage4_comparison_fields() -> None:
    envelope = build_stage4_budget_envelope(
        budget_class="class_a",
        wall_clock_ms=1250,
        token_counts={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        cost_estimate=0.0,
        comparison_class="bounded_internal",
        route_id=None,
        provider_model=None,
        execution_mode="scaffold",
        route_class="local_baseline",
        cost_source="local_execution",
        support_envelope_digest="a" * 64,
        evaluator_pack_version="stage4.evalpack.lane.repo_swe.task.v1",
        replication_reserve_fraction=0.2,
        control_reserve_fraction=0.1,
    )
    assert envelope["execution_mode"] == "scaffold"
    assert envelope["route_class"] == "local_baseline"
    assert envelope["cost_source"] == "local_execution"
    assert envelope["control_reserve_policy"] == "replication=0.20;control=0.10"


def test_consume_execution_envelope_v2_requires_stage4_fields() -> None:
    consumed = consume_execution_envelope_v2(
        {
            "bindings": {
                "cwd": "/repo",
                "out_dir": "artifacts/out",
                "task_id": "task.repo",
                "budget_class": "class_a",
                "tool_bindings": ["shell"],
                "environment_digest": "sha256:env",
                "command": ["python", "-m", "pytest"],
            }
        },
        support_envelope_digest="b" * 64,
        evaluator_pack_version="stage4.evalpack.lane.repo_swe.task.v1",
    )
    assert consumed["task_id"] == "task.repo"
    assert consumed["environment_digest"] == "sha256:env"
    assert consumed["consumed_fields"][-1] == "evaluator_pack_version"


def test_validate_stage4_matched_budget_pair_requires_strict_contract() -> None:
    baseline = {
        "lane_id": "lane.repo_swe",
        "budget_class": "class_a",
        "comparison_class": "bounded_internal",
        "route_class": "default_worker",
        "execution_mode": "scaffold",
        "evaluator_pack_version": "stage4.evalpack.repo_swe.v1",
        "support_envelope_digest": "a" * 64,
        "task_id": "task.repo",
        "control_reserve_policy": "replication=0.20;control=0.10",
    }
    candidate = dict(baseline)
    candidate["support_envelope_digest"] = "b" * 64
    result = validate_stage4_matched_budget_pair(baseline=baseline, candidate=candidate)
    assert result.ok is False
    assert result.reason == "support_envelope_digest_mismatch"


def test_validate_stage4_claim_eligibility_rejects_scaffold_rows() -> None:
    result = validate_stage4_claim_eligibility(
        {
            "execution_mode": "scaffold",
            "cost_source": "scaffold_placeholder",
        }
    )
    assert result.ok is False
    assert result.reason == "execution_mode_not_live"


def test_build_stage4_support_envelope_digest_is_stable() -> None:
    digest_a = build_stage4_support_envelope_digest(
        lane_id="lane.repo_swe",
        task_id="task.repo",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        allowed_tools=["pytest", "git_diff"],
        environment_digest="sha256:env",
        claim_target="internal",
    )
    digest_b = build_stage4_support_envelope_digest(
        lane_id="lane.repo_swe",
        task_id="task.repo",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        allowed_tools=["pytest", "git_diff"],
        environment_digest="sha256:env",
        claim_target="internal",
    )
    assert digest_a == digest_b
