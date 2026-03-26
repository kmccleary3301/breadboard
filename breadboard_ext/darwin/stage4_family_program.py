from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEEP_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "deep_live_search"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "family_program"

FAMILY_OPERATOR_MAP = {
    "mut.topology.single_to_pev_v1": {
        "family_kind": "topology",
        "family_key": "policy.topology.pev_v0",
        "family_prefix": "component_family.stage4.topology.policy.topology.pev_v0",
        "priority": 0,
    },
    "mut.tool_scope.add_git_diff_v1": {
        "family_kind": "tool_scope",
        "family_key": "policy.tool_scope.add_git_diff_v1",
        "family_prefix": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1",
        "priority": 1,
    },
    "mut.policy.shadow_memory_enable_v1": {
        "family_kind": "policy",
        "family_key": "policy.shadow_memory_enable_v1",
        "family_prefix": "component_family.stage4.policy.policy.shadow_memory_enable_v1",
        "priority": 2,
    },
}

PROMOTION_CLASS_ORDER = {
    "promotion_ready": 0,
    "provisional_family": 1,
    "not_promoted": 2,
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def path_ref(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_stage4_family_id(*, lane_id: str, operator_id: str) -> str:
    mapping = FAMILY_OPERATOR_MAP[operator_id]
    return f"{mapping['family_prefix']}.{lane_id}.v0"


def classify_stage4_family_candidate(
    *,
    valid_count: int,
    positive_count: int,
    invalidity_rate: float,
    replay_supported: bool,
) -> str:
    if valid_count >= 4 and positive_count >= 3 and invalidity_rate <= 0.34 and replay_supported:
        return "promotion_ready"
    if valid_count >= 4 and positive_count >= 2 and invalidity_rate <= 0.50:
        return "provisional_family"
    return "not_promoted"


def candidate_rank(row: dict[str, Any]) -> tuple[int, int, float, int]:
    return (
        PROMOTION_CLASS_ORDER[row["promotion_class"]],
        -int(row["positive_power_signal_count"]),
        -int(row["valid_comparison_count"]),
        int(FAMILY_OPERATOR_MAP[row["source_operator_id"]]["priority"]),
    )


def default_transfer_scope(*, lane_id: str, family_kind: str, promotion_outcome: str) -> dict[str, Any]:
    if promotion_outcome != "promoted":
        return {
            "allowed_target_lanes": [],
            "disallowed_target_lanes": ["lane.systems", "lane.scheduling"],
            "reason": "non_promoted_family",
        }
    if lane_id == "lane.repo_swe" and family_kind == "topology":
        return {
            "allowed_target_lanes": ["lane.systems"],
            "disallowed_target_lanes": ["lane.scheduling"],
            "reason": "topology_family_validated_on_systems_only",
        }
    if lane_id == "lane.systems" and family_kind == "policy":
        return {
            "allowed_target_lanes": ["lane.scheduling"],
            "disallowed_target_lanes": ["lane.repo_swe"],
            "reason": "policy_family_confirmation_only",
        }
    return {
        "allowed_target_lanes": [],
        "disallowed_target_lanes": ["lane.systems", "lane.scheduling"],
        "reason": "unsupported_transfer_scope",
    }
