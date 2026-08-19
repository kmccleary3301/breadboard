from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
INVENTORY_PATH = REPO_ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
SCORECARD_PATH = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "BB_E4_PRIMITIVE_PARITY_SCORECARD.json"
SCORE_SUBLEDGER_PATH = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "BB_E4_SCORE_SUBLEDGER.json"
REQUIRED_ACCEPTED_ROLES = {
    "capture",
    "comparator",
    "evidence_manifest",
    "freeze_manifest",
    "node_gate",
    "parity_results",
    "replay",
    "secret_scan_report",
    "support_claim",
    "validator_output",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a JSON object"
    return payload


def _accepted_lanes() -> list[dict[str, Any]]:
    inventory = _load_json(INVENTORY_PATH)
    lanes = inventory.get("lanes")
    assert isinstance(lanes, list), "inventory lanes must be a list"
    return [lane for lane in lanes if isinstance(lane, dict) and lane.get("status") == "accepted"]


def _accounted_lanes() -> list[dict[str, Any]]:
    inventory = _load_json(INVENTORY_PATH)
    lanes = inventory.get("lanes")
    assert isinstance(lanes, list), "inventory lanes must be a list"
    return [
        lane
        for lane in lanes
        if isinstance(lane, dict)
        and (lane.get("status") == "accepted" or lane.get("evidence_status") == "accepted")
    ]


def test_lane_inventory_ids_and_ct_outputs_are_unique() -> None:
    """Accepted inventory lanes must have one stable identity and one node-gate output each."""
    lanes = _accepted_lanes()
    assert lanes, "expected accepted inventory lanes"

    lane_ids = [lane.get("lane_id") for lane in lanes]
    config_ids = [lane.get("config_id") for lane in lanes]
    claim_ids = [lane.get("claim_id") for lane in lanes]
    score_row_ids = [lane.get("score_row_id") for lane in lanes]
    ct_outputs = []
    for lane in lanes:
        command = lane.get("ct", {}).get("command", {}) if isinstance(lane.get("ct"), dict) else {}
        argv = command.get("argv") if isinstance(command, dict) else None
        assert isinstance(argv, list), f"{lane.get('lane_id')} missing ct.command.argv"
        assert "--json-out" in argv, f"{lane.get('lane_id')} missing --json-out"
        output = argv[argv.index("--json-out") + 1]
        assert isinstance(output, str) and output.startswith("artifacts/conformance/node_gate/")
        ct_outputs.append(output)

    for label, values in {
        "lane_id": lane_ids,
        "config_id": config_ids,
        "claim_id": claim_ids,
        "score_row_id": score_row_ids,
        "ct output": ct_outputs,
    }.items():
        assert all(isinstance(value, str) and value for value in values), f"{label} values must be non-empty strings"
        assert len(values) == len(set(values)), f"duplicate {label} values"


def test_accepted_lanes_have_required_artifact_roles() -> None:
    """Every accepted lane keeps the C4 evidence roles consumed by builders and freshness checks."""
    for lane in _accepted_lanes():
        lane_id = str(lane["lane_id"])
        roles = lane.get("artifact_roles")
        assert isinstance(roles, dict), f"{lane_id} artifact_roles must be a mapping"
        missing = REQUIRED_ACCEPTED_ROLES - set(roles)
        assert missing == set(), f"{lane_id} missing roles: {sorted(missing)}"
        for role, role_id in roles.items():
            assert isinstance(role_id, str) and role_id == f"{lane_id}:{role}"


def test_inventory_points_match_score_subledger_accounting() -> None:
    """Inventory-managed points must reconcile with current score-subledger totals without lane lists in tests."""
    lanes = _accounted_lanes()
    target_points = sum(int(lane["points"]) for lane in lanes if lane.get("kind") == "target_support")
    non_target_inventory_points = sum(
        int(lane["points"]) for lane in lanes if lane.get("kind") == "non_target_accounting"
    )

    subledger = _load_json(SCORE_SUBLEDGER_PATH)
    scorecard = _load_json(SCORECARD_PATH)
    score_rows = subledger.get("score_rows")
    assert isinstance(score_rows, list), "score subledger rows must be a list"

    target_rows = [row for row in score_rows if isinstance(row, dict) and row.get("kind") == "target_support_claim"]
    non_target_rows = [row for row in score_rows if isinstance(row, dict) and row.get("kind") == "non_target_accounting"]
    assert sum(int(row["points"]) for row in target_rows) == target_points
    assert subledger["target_support_claim_points"] == target_points
    assert sum(int(row["points"]) for row in non_target_rows) == subledger["non_target_accounting_points"]
    assert subledger["non_target_accounting_points"] >= non_target_inventory_points
    assert subledger["current_accepted_points"] == (
        subledger["target_support_claim_points"] + subledger["non_target_accounting_points"]
    )
    assert scorecard["total_points"] >= subledger["current_accepted_points"]
