from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LANE_REGISTRY_REF = "docs/contracts/darwin/registries/lane_registry_v0.json"
POLICY_REGISTRY_REF = "docs/contracts/darwin/registries/policy_registry_v0.json"
SHADOW_PROVING_LANES = {"lane.harness", "lane.repo_swe"}


def _sha256_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def should_emit_shadow_artifacts(lane_id: str) -> bool:
    return lane_id in SHADOW_PROVING_LANES


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
