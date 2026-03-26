from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BOUNDED_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage3" / "component_transfer"
POLICY_REGISTRY = ROOT / "docs" / "contracts" / "darwin" / "registries" / "policy_registry_v0.json"

OPERATOR_COMPONENT_MAP = {
    "mut.topology.single_to_pev_v1": {
        "component_kind": "topology",
        "component_key": "policy.topology.pev_v0",
        "component_family": "component_family.stage3.topology.policy.topology.pev_v0.v0",
        "priority": 0,
    },
    "mut.policy.shadow_memory_enable_v1": {
        "component_kind": "policy_bundle",
        "component_key": "policy.shadow_memory_enable_v1",
        "component_family": "component_family.stage3.policy.shadow_memory_enable_v1.v0",
        "priority": 1,
    },
    "mut.tool_scope.add_git_diff_v1": {
        "component_kind": "tool_scope",
        "component_key": "policy.tool_scope.add_git_diff_v1",
        "component_family": "component_family.stage3.tool_scope.add_git_diff_v1.v0",
        "priority": 2,
    },
}

PROMOTION_CLASS_ORDER = {
    "promotion_ready": 0,
    "transfer_candidate": 1,
    "observe_only": 2,
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def policy_registry_lookup() -> dict[str, dict[str, Any]]:
    payload = load_json(POLICY_REGISTRY)
    return {row["policy_bundle_id"]: row for row in payload.get("bundles") or []}


def classify_component_candidate(
    *,
    valid_count: int,
    invalid_count: int,
    improvement_count: int,
    retention_rate: float,
    average_runtime_delta_ms: float,
) -> str:
    if valid_count >= 2 and invalid_count == 0 and retention_rate >= 1.0:
        return "promotion_ready"
    if valid_count >= 2 and improvement_count >= 1 and retention_rate >= 1.0:
        return "transfer_candidate"
    return "observe_only"


def candidate_rank(row: dict[str, Any]) -> tuple[int, int, float, int]:
    return (
        PROMOTION_CLASS_ORDER[row["promotion_class"]],
        -int(row["improvement_count"]),
        float(row["average_runtime_delta_ms"]),
        int(row["priority"]),
    )
