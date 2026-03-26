from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from breadboard_ext.darwin.search import build_mutation_operator_registry


ROOT = Path(__file__).resolve().parents[2]
LANE_REGISTRY_REF = "docs/contracts/darwin/registries/lane_registry_v0.json"
POLICY_REGISTRY_REF = "docs/contracts/darwin/registries/policy_registry_v0.json"
SHADOW_PROVING_LANES = {"lane.harness", "lane.repo_swe"}
TOPOLOGY_COMPATIBILITY = {
    "lane.harness": ["policy.topology.single_v0", "policy.topology.pev_v0"],
    "lane.repo_swe": ["policy.topology.single_v0", "policy.topology.pev_v0", "policy.topology.pwrv_v0"],
    "lane.atp": ["policy.topology.single_v0", "policy.topology.pev_v0", "policy.topology.pwrv_v0"],
    "lane.scheduling": ["policy.topology.single_v0", "policy.topology.pev_v0"],
    "lane.research": ["policy.topology.single_v0", "policy.topology.pev_v0"],
    "lane.systems": ["policy.topology.single_v0", "policy.topology.pev_v0", "policy.topology.pwrv_v0"],
}
EVALUATOR_RULES = {
    "lane.atp": {
        "evaluator_type": "verifier_rich",
        "runner_kind": "json_overall_ok",
        "invalid_comparison_rules": [
            "ops_digest_task_shape_must_match",
            "topology_lane_pair_must_be_supported",
            "budget_class_must_match",
        ],
        "control_pack": {
            "required_perturbation_group": "nominal",
            "required_invariants": ["same_digest_scope", "same_budget_class"],
        },
    },
    "lane.harness": {
        "evaluator_type": "objective_regression",
        "runner_kind": "pytest_pass_ratio",
        "invalid_comparison_rules": [
            "test_target_set_must_match",
            "topology_lane_pair_must_be_supported",
            "budget_class_must_match",
        ],
        "control_pack": {
            "required_perturbation_group": "nominal",
            "required_invariants": ["same_test_selection", "same_command_shape"],
        },
    },
    "lane.repo_swe": {
        "evaluator_type": "test_and_build",
        "runner_kind": "pytest_pass_ratio",
        "invalid_comparison_rules": [
            "workspace_snapshot_must_match",
            "test_target_set_must_match",
            "budget_class_must_match",
            "topology_lane_pair_must_be_supported",
        ],
        "control_pack": {
            "required_perturbation_group": "nominal",
            "required_invariants": ["same_workspace_shape", "same_test_selection", "same_command_shape"],
        },
    },
    "lane.scheduling": {
        "evaluator_type": "simulator_or_exact_checker",
        "runner_kind": "json_overall_ok",
        "invalid_comparison_rules": [
            "scenario_pack_must_match",
            "constraint_checker_must_match",
            "budget_class_must_match",
            "topology_lane_pair_must_be_supported",
        ],
        "control_pack": {
            "required_perturbation_group": "nominal",
            "required_invariants": ["same_scenario_pack", "same_constraint_checker"],
        },
    },
    "lane.systems": {
        "evaluator_type": "reward_regression",
        "runner_kind": "pytest_pass_ratio",
        "invalid_comparison_rules": [
            "reward_contract_must_match",
            "metric_recorder_shape_must_match",
            "budget_class_must_match",
            "topology_lane_pair_must_be_supported",
        ],
        "control_pack": {
            "required_perturbation_group": "nominal",
            "required_invariants": ["same_reward_contract", "same_metric_recorder_shape", "same_command_shape"],
        },
    },
}


def _sha256_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def should_emit_shadow_artifacts(lane_id: str) -> bool:
    return lane_id in SHADOW_PROVING_LANES


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_registry_rows() -> list[dict[str, Any]]:
    payload = _load_json(ROOT / POLICY_REGISTRY_REF)
    return payload.get("bundles") or []


def _bundle_ids_for(*, policy_class: str, lane_id: str) -> list[str]:
    rows = []
    for bundle in _policy_registry_rows():
        if bundle["policy_class"] != policy_class:
            continue
        if lane_id not in (bundle.get("applies_to_lanes") or []):
            continue
        rows.append(bundle["policy_bundle_id"])
    return rows


def _operator_eligibility_for_lane(lane_id: str) -> dict[str, list[str]]:
    registry = build_mutation_operator_registry()
    supported: list[str] = []
    experimental: list[str] = []
    prohibited: list[str] = []
    for operator in registry["operators"]:
        operator_id = operator["operator_id"]
        if lane_id in (operator.get("supported_lanes") or []):
            supported.append(operator_id)
        elif lane_id in (operator.get("experimental_lanes") or []):
            experimental.append(operator_id)
        elif lane_id in (operator.get("prohibited_lanes") or []):
            prohibited.append(operator_id)
    return {
        "supported_operator_ids": supported,
        "experimental_operator_ids": experimental,
        "prohibited_operator_ids": prohibited,
    }


def build_effective_config(
    *,
    spec: dict[str, Any],
    lane_id: str,
    candidate_id: str,
    trial_label: str,
    task_id: str,
    command: list[str],
    campaign_spec_ref: str,
    topology_id: str,
    policy_bundle_id: str,
    budget_class: str,
) -> dict[str, Any]:
    authored_payload = {
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "task_id": task_id,
        "topology_id": topology_id,
        "policy_bundle_id": policy_bundle_id,
        "budget_class": budget_class,
        "memory_policy_id": spec["memory_policy_id"],
        "environment_digest": spec["environment_digest"],
        "allowed_tools": spec["allowed_tools"],
    }
    command_payload = {"command": command}
    compiled_inputs_payload = {
        "authored_payload": authored_payload,
        "command_payload": command_payload,
        "campaign_spec_ref": campaign_spec_ref,
        "lane_registry_ref": LANE_REGISTRY_REF,
        "policy_registry_ref": POLICY_REGISTRY_REF,
    }
    return {
        "schema": "breadboard.darwin.effective_config.v0",
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "candidate_id": candidate_id,
        "trial_label": trial_label,
        "shadow_mode": True,
        "runtime_consumed": False,
        "compiled_from": {
            "campaign_spec_ref": campaign_spec_ref,
            "lane_registry_ref": LANE_REGISTRY_REF,
            "policy_registry_ref": POLICY_REGISTRY_REF,
        },
        "compiled_bindings": {
            "task_id": task_id,
            "topology_id": topology_id,
            "policy_bundle_id": policy_bundle_id,
            "budget_class": budget_class,
            "memory_policy_id": spec["memory_policy_id"],
            "environment_digest": spec["environment_digest"],
            "allowed_tools": spec["allowed_tools"],
            "command": command,
        },
        "override_policy": {
            "mode": "allowlist_only",
            "applied_overrides": [],
            "rejected_overrides": [],
        },
        "digests": {
            "authored_input_sha256": _sha256_payload(authored_payload),
            "command_sha256": _sha256_payload(command_payload),
            "compiled_inputs_sha256": _sha256_payload(compiled_inputs_payload),
        },
    }


def build_execution_plan(
    *,
    spec: dict[str, Any],
    lane_id: str,
    candidate_id: str,
    trial_label: str,
    task_id: str,
    command: list[str],
    topology_id: str,
    budget_class: str,
    effective_config_ref: str,
    candidate_ref: str,
    evaluation_ref: str,
    stdout_ref: str,
    stderr_ref: str,
    out_dir: str,
) -> dict[str, Any]:
    execution_graph = {
        "topology_id": topology_id,
        "nodes": [{"node_id": "node.executor", "role": "executor"}],
        "edges": [],
    }
    bindings = {
        "cwd": str(ROOT),
        "out_dir": out_dir,
        "task_id": task_id,
        "budget_class": budget_class,
        "tool_bindings": spec["allowed_tools"],
        "environment_digest": spec["environment_digest"],
        "command": command,
    }
    plan_digest = _sha256_payload(
        {
            "campaign_id": spec["campaign_id"],
            "lane_id": lane_id,
            "candidate_id": candidate_id,
            "trial_label": trial_label,
            "effective_config_ref": effective_config_ref,
            "execution_graph": execution_graph,
            "bindings": bindings,
        }
    )
    return {
        "schema": "breadboard.darwin.execution_plan.v0",
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "candidate_id": candidate_id,
        "trial_label": trial_label,
        "shadow_mode": True,
        "runtime_consumed": False,
        "effective_config_ref": effective_config_ref,
        "plan_digest": plan_digest,
        "execution_graph": execution_graph,
        "bindings": bindings,
        "runtime_truth_refs": {
            "candidate_ref": candidate_ref,
            "evaluation_ref": evaluation_ref,
            "stdout_ref": stdout_ref,
            "stderr_ref": stderr_ref,
        },
    }


def build_effective_policy(
    *,
    spec: dict[str, Any],
    lane_id: str,
    candidate_id: str,
    trial_label: str,
    topology_id: str,
    policy_bundle_id: str,
    budget_class: str,
) -> dict[str, Any]:
    policy_inputs = {
        "policy_bundle_id": policy_bundle_id,
        "memory_policy_id": spec["memory_policy_id"],
        "budget_class": budget_class,
        "claim_target": spec["claim_target"],
    }
    resolved_policy_bundle_refs = {
        "topology_bundle_ids": _bundle_ids_for(policy_class="topology_family", lane_id=lane_id),
        "memory_bundle_id": spec["memory_policy_id"],
        "budget_bundle_id": f"policy.budget.{budget_class}_v0",
    }
    topology_support = {
        "requested_topology_id": topology_id,
        "allowed_topology_ids": TOPOLOGY_COMPATIBILITY[lane_id],
        "is_supported": topology_id in TOPOLOGY_COMPATIBILITY[lane_id],
    }
    operator_eligibility = _operator_eligibility_for_lane(lane_id)
    tool_policy = {
        "allowed_tools": spec["allowed_tools"],
        "claim_target": spec["claim_target"],
    }
    return {
        "schema": "breadboard.darwin.effective_policy.v0",
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "candidate_id": candidate_id,
        "trial_label": trial_label,
        "shadow_mode": True,
        "runtime_consumed": False,
        "policy_inputs": policy_inputs,
        "resolved_policy_bundle_refs": resolved_policy_bundle_refs,
        "topology_support": topology_support,
        "operator_eligibility": operator_eligibility,
        "tool_policy": tool_policy,
        "digests": {
            "policy_inputs_sha256": _sha256_payload(policy_inputs),
            "operator_view_sha256": _sha256_payload(operator_eligibility),
            "compiled_policy_sha256": _sha256_payload(
                {
                    "policy_inputs": policy_inputs,
                    "resolved_policy_bundle_refs": resolved_policy_bundle_refs,
                    "topology_support": topology_support,
                    "tool_policy": tool_policy,
                }
            ),
        },
    }


def build_evaluator_pack(
    *,
    spec: dict[str, Any],
    lane_id: str,
    candidate_id: str,
    trial_label: str,
    task_id: str,
    budget_class: str,
) -> dict[str, Any]:
    rules = EVALUATOR_RULES[lane_id]
    return {
        "schema": "breadboard.darwin.evaluator_pack.v0",
        "campaign_id": spec["campaign_id"],
        "lane_id": lane_id,
        "candidate_id": candidate_id,
        "trial_label": trial_label,
        "shadow_mode": True,
        "runtime_consumed": False,
        "evaluator_identity": {
            "evaluator_type": rules["evaluator_type"],
            "task_id": task_id,
            "runner_kind": rules["runner_kind"],
        },
        "metric_semantics": {
            "primary_metric": "pytest_pass_ratio",
            "higher_is_better": True,
            "score_unit": "ratio",
            "success_condition": "subprocess_returncode_zero",
        },
        "invalid_comparison_rules": rules["invalid_comparison_rules"],
        "control_pack": rules["control_pack"],
        "claim_constraints": {
            "claim_target": spec["claim_target"],
            "comparability_requirements": [
                "same_task_id",
                "same_budget_class",
                "same_lane_id",
                "same_perturbation_group",
            ],
        },
    }
