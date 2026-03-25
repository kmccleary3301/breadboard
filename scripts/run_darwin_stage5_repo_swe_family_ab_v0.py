from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import _write_json  # noqa: E402
from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_family_ab"
FAMILY_VARIANTS = (
    ("topology", "topology"),
    ("tool_scope", "tool_scope"),
)


def _family_selection_status(family_totals: dict[str, dict[str, int]], *, complete: bool) -> tuple[str, str, str | None]:
    if not complete:
        return "stale_or_incomplete", "family_ab_bundle_contains_non_claim_eligible_or_missing_rounds", None
    topology = dict(family_totals.get("topology") or {})
    tool_scope = dict(family_totals.get("tool_scope") or {})
    topology_reuse = int(topology.get("reuse_lift_count") or 0)
    topology_no_lift = int(topology.get("no_lift_count") or 0)
    topology_flat = int(topology.get("flat_count") or 0)
    tool_reuse = int(tool_scope.get("reuse_lift_count") or 0)
    tool_no_lift = int(tool_scope.get("no_lift_count") or 0)
    tool_flat = int(tool_scope.get("flat_count") or 0)
    if topology_reuse > tool_reuse and topology_no_lift <= tool_no_lift:
        return "settled_topology", "topology_has_higher_positive_count_and_no_higher_negative_count", "topology"
    if tool_reuse > topology_reuse and tool_no_lift <= topology_no_lift:
        return "settled_tool_scope", "tool_scope_has_higher_positive_count_and_no_higher_negative_count", "tool_scope"
    if topology_reuse == tool_reuse and topology_no_lift == tool_no_lift and topology_flat == tool_flat:
        return "fully_tied", "family_ab_surface_is_exactly_tied", None
    preferred = "topology" if topology_no_lift <= tool_no_lift else "tool_scope"
    return "open", "family_ab_surface_is_not_stable_under_current_protocol", preferred


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp_path.replace(path)


def run_stage5_repo_swe_family_ab(*, rounds: int = 2, out_dir: Path = OUT_DIR) -> dict[str, object]:
    expected_row_count = len(FAMILY_VARIANTS) * int(rounds)
    rows: list[dict[str, object]] = []
    for family_label, family_kind in FAMILY_VARIANTS:
        for round_index in range(1, int(rounds) + 1):
            summary = run_stage5_compounding_pilot(
                lane_id="lane.repo_swe",
                out_dir=out_dir / family_label / f"round_r{round_index}",
                round_index=round_index,
                family_probe_override_kind=family_kind,
            )
            payload = json.loads(Path(str(summary["summary_path"])).read_text(encoding="utf-8"))
            rows.append(
                {
                    "family_label": family_label,
                    "family_probe_override_kind": family_kind,
                    "round_index": round_index,
                    "claim_eligible_comparison_count": int(payload.get("claim_eligible_comparison_count") or 0),
                    "comparison_valid_count": int(payload.get("comparison_valid_count") or 0),
                    "reuse_lift_count": int(payload.get("reuse_lift_count") or 0),
                    "flat_count": int(payload.get("flat_count") or 0),
                    "no_lift_count": int(payload.get("no_lift_count") or 0),
                    "round_is_complete": bool(
                        int(payload.get("comparison_valid_count") or 0) > 0
                        and int(payload.get("claim_eligible_comparison_count") or 0) > 0
                    ),
                    "summary_ref": path_ref(Path(str(summary["summary_path"]))),
                }
            )
    family_totals: dict[str, dict[str, int]] = {}
    valid_round_row_count = 0
    stale_round_row_count = 0
    for row in rows:
        family_totals.setdefault(
            str(row["family_label"]),
            {
                "claim_eligible_comparison_count": 0,
                "comparison_valid_count": 0,
                "reuse_lift_count": 0,
                "flat_count": 0,
                "no_lift_count": 0,
            },
        )
        family_totals[str(row["family_label"])]["claim_eligible_comparison_count"] += int(row["claim_eligible_comparison_count"])
        family_totals[str(row["family_label"])]["comparison_valid_count"] += int(row["comparison_valid_count"])
        family_totals[str(row["family_label"])]["reuse_lift_count"] += int(row["reuse_lift_count"])
        family_totals[str(row["family_label"])]["flat_count"] += int(row["flat_count"])
        family_totals[str(row["family_label"])]["no_lift_count"] += int(row["no_lift_count"])
        if bool(row["round_is_complete"]):
            valid_round_row_count += 1
        else:
            stale_round_row_count += 1
    bundle_complete = len(rows) == expected_row_count and stale_round_row_count == 0
    family_selection_status, family_selection_reason, preferred_family_kind = _family_selection_status(
        family_totals,
        complete=bundle_complete,
    )
    payload = {
        "schema": "breadboard.darwin.stage5.repo_swe_family_ab.v0",
        "round_count": int(rounds),
        "family_count": len(FAMILY_VARIANTS),
        "expected_row_count": expected_row_count,
        "row_count": len(rows),
        "completion_status": "complete" if bundle_complete else "stale_or_incomplete",
        "bundle_complete": bundle_complete,
        "valid_round_row_count": valid_round_row_count,
        "stale_round_row_count": stale_round_row_count,
        "family_selection_status": family_selection_status,
        "family_selection_reason": family_selection_reason,
        "preferred_family_kind": preferred_family_kind,
        "rows": rows,
        "family_totals": family_totals,
    }
    out_path = out_dir / "repo_swe_family_ab_v0.json"
    _atomic_write_json(out_path, payload)
    return {"summary_path": str(out_path), "row_count": len(rows), "family_count": len(FAMILY_VARIANTS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 Repo_SWE family A/B surface.")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage5_repo_swe_family_ab(rounds=args.rounds)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_repo_swe_family_ab={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
