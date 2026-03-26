from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSFER_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "transfer_ledger_v1.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "reviews"
POLICY_REF = "docs/contracts/darwin/DARWIN_TRANSFER_FAMILY_POLICY_V0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _family_for(row: dict) -> str:
    source = row["source_lane_id"]
    target = row["target_lane_id"]
    operator = row.get("operator_id") or ""
    if source == target:
        return "same_lane_operator_transfer"
    if operator.startswith("mut.prompt."):
        return "cross_lane_prompt_family_transfer"
    return "cross_lane_policy_topology_transfer"


def _result_class(row: dict) -> str:
    if not row.get("comparison_valid"):
        return "invalid_comparison"
    if row.get("replay_required") and not row.get("replay_stable"):
        return "replay_required_pending"
    result = row.get("result")
    if result == "improved":
        return "valid_improved"
    if result == "degraded":
        return "valid_degraded"
    return "valid_noop"


def _component_family(row: dict) -> str:
    operator = row.get("operator_id") or ""
    if operator.startswith("mut.prompt."):
        return "prompt_family"
    if operator.startswith("mut.topology.") or operator.startswith("mut.budget."):
        return "policy_topology_family"
    return "operator_family"


def build_transfer_family_view() -> dict:
    payload = _load_json(TRANSFER_LEDGER)
    rows = []
    for row in payload.get("attempts") or []:
        rows.append(
            {
                "transfer_id": row["transfer_id"],
                "transfer_family": _family_for(row),
                "source_lane_id": row["source_lane_id"],
                "target_lane_id": row["target_lane_id"],
                "source_candidate_id": row["source_candidate_id"],
                "target_candidate_id": row["target_candidate_id"],
                "operator_id": row.get("operator_id"),
                "component_family": _component_family(row),
                "budget_class": row.get("budget_class"),
                "comparison_valid": row.get("comparison_valid", False),
                "replay_required": row.get("replay_required", False),
                "replay_stable": row.get("replay_stable"),
                "result_class": _result_class(row),
                "validity_reason": row.get("validity_reason"),
                "promotion_status": row.get("promotion_status"),
                "decision_status": row.get("promotion_status"),
                "descriptive_only": not (
                    row.get("comparison_valid", False)
                    and row.get("replay_required", False)
                    and row.get("replay_stable", False)
                    and row.get("promotion_status") == "promoted"
                ),
            }
        )
    return {
        "schema": "breadboard.darwin.transfer_family_view.v0",
        "policy_ref": POLICY_REF,
        "attempt_count": len(rows),
        "rows": rows,
    }


def write_transfer_family_view(out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_transfer_family_view()
    out_path = out_dir / "transfer_family_view_v0.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "attempt_count": payload["attempt_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DARWIN transfer-family derived view.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_transfer_family_view()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"transfer_family_view={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
