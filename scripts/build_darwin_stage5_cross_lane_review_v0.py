from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402


DEFAULT_POLICY_STABILITY = ROOT / "artifacts" / "darwin" / "stage5" / "policy_stability" / "policy_stability_v0.json"
DEFAULT_REPO_SWE_FAMILY_AB = ROOT / "artifacts" / "darwin" / "stage5" / "repo_swe_family_ab" / "repo_swe_family_ab_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "cross_lane_review"
OUT_JSON = OUT_DIR / "cross_lane_review_v0.json"
OUT_MD = OUT_DIR / "cross_lane_review_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _family_ab_totals(payload: dict[str, Any], *, source_path: Path) -> tuple[dict[str, dict[str, int]], bool]:
    round_summary_paths = sorted(source_path.parent.glob("*/round_*/compounding_pilot_v0.json"))
    if round_summary_paths:
        totals: dict[str, dict[str, int]] = {}
        claim_eligible_counts: list[int] = []
        for summary_path in round_summary_paths:
            family_label = summary_path.parent.parent.name
            summary_payload = _load_json(summary_path)
            claim_eligible_counts.append(int(summary_payload.get("claim_eligible_comparison_count") or 0))
            totals.setdefault(
                family_label,
                {
                    "reuse_lift_count": 0,
                    "flat_count": 0,
                    "no_lift_count": 0,
                },
            )
            totals[family_label]["reuse_lift_count"] += int(summary_payload.get("reuse_lift_count") or 0)
            totals[family_label]["flat_count"] += int(summary_payload.get("flat_count") or 0)
            totals[family_label]["no_lift_count"] += int(summary_payload.get("no_lift_count") or 0)
        if totals:
            stale = any(count == 0 for count in claim_eligible_counts) and any(count > 0 for count in claim_eligible_counts)
            return totals, stale
    rows = list(payload.get("rows") or [])
    if rows:
        totals: dict[str, dict[str, int]] = {}
        for row in rows:
            family_label = str(row.get("family_label") or row.get("family_probe_override_kind") or "")
            totals.setdefault(
                family_label,
                {
                    "reuse_lift_count": 0,
                    "flat_count": 0,
                    "no_lift_count": 0,
                },
            )
            summary_ref = Path(str(row.get("summary_ref") or ""))
            summary_payload = _load_json(summary_ref) if summary_ref.exists() else dict(row)
            totals[family_label]["reuse_lift_count"] += int(summary_payload.get("reuse_lift_count") or 0)
            totals[family_label]["flat_count"] += int(summary_payload.get("flat_count") or 0)
            totals[family_label]["no_lift_count"] += int(summary_payload.get("no_lift_count") or 0)
        if totals:
            return totals, False
    return ({
        str(key): {
            "reuse_lift_count": int(dict(value).get("reuse_lift_count") or 0),
            "flat_count": int(dict(value).get("flat_count") or 0),
            "no_lift_count": int(dict(value).get("no_lift_count") or 0),
        }
        for key, value in dict(payload.get("family_totals") or {}).items()
    }, False)


def _repo_swe_family_selection_state(payload: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    if str(payload.get("completion_status") or "") == "stale_or_incomplete":
        return {
            "family_selection_status": str(payload.get("family_selection_status") or "stale_or_incomplete"),
            "preferred_family_kind": payload.get("preferred_family_kind"),
            "reason": str(payload.get("family_selection_reason") or "family_ab_bundle_marked_stale_by_source"),
        }
    if str(payload.get("completion_status") or "") == "complete":
        return {
            "family_selection_status": str(payload.get("family_selection_status") or "open"),
            "preferred_family_kind": payload.get("preferred_family_kind"),
            "reason": str(payload.get("family_selection_reason") or "family_ab_bundle_marked_complete_by_source"),
        }
    family_totals, stale = _family_ab_totals(payload, source_path=source_path)
    if stale:
        return {
            "family_selection_status": "stale_or_incomplete",
            "preferred_family_kind": None,
            "reason": "family_ab_round_set_contains_mixed_claim_eligibility_after_interrupted_live_rerun",
        }
    topology = dict(family_totals.get("topology") or {})
    tool_scope = dict(family_totals.get("tool_scope") or {})
    topology_reuse = int(topology.get("reuse_lift_count") or 0)
    topology_no_lift = int(topology.get("no_lift_count") or 0)
    topology_flat = int(topology.get("flat_count") or 0)
    tool_reuse = int(tool_scope.get("reuse_lift_count") or 0)
    tool_no_lift = int(tool_scope.get("no_lift_count") or 0)
    tool_flat = int(tool_scope.get("flat_count") or 0)

    if topology_reuse > tool_reuse and topology_no_lift <= tool_no_lift:
        return {
            "family_selection_status": "settled_topology",
            "preferred_family_kind": "topology",
            "reason": "topology_has_higher_positive_count_and_no_higher_negative_count",
        }
    if tool_reuse > topology_reuse and tool_no_lift <= topology_no_lift:
        return {
            "family_selection_status": "settled_tool_scope",
            "preferred_family_kind": "tool_scope",
            "reason": "tool_scope_has_higher_positive_count_and_no_higher_negative_count",
        }
    if topology_reuse == tool_reuse and topology_no_lift == tool_no_lift and topology_flat == tool_flat:
        return {
            "family_selection_status": "fully_tied",
            "preferred_family_kind": None,
            "reason": "family_ab_surface_is_exactly_tied",
        }
    return {
        "family_selection_status": "open",
        "preferred_family_kind": "topology" if topology_no_lift <= tool_no_lift else "tool_scope",
        "reason": "family_ab_surface_is_not_stable_under_refined_protocol",
    }


def _lane_weight(row: dict[str, Any], *, repo_swe_family_state: dict[str, Any] | None = None) -> tuple[str, str]:
    lane_id = str(row.get("lane_id") or "")
    stability_class = str(row.get("stability_class") or "")
    reuse_lift_count = int(row.get("reuse_lift_count") or 0)
    no_lift_count = int(row.get("no_lift_count") or 0)
    if lane_id == "lane.systems" and stability_class in {"mixed_positive", "stable_positive"} and reuse_lift_count >= no_lift_count:
        return "primary_proving_lane", "systems_has_cleaner_current_compounding_surface"
    if lane_id == "lane.repo_swe" and repo_swe_family_state is not None:
        if str(repo_swe_family_state.get("family_selection_status") or "") == "open":
            return "challenge_lane", "repo_swe_family_selection_is_still_open_under_refined_protocol"
        if str(repo_swe_family_state.get("family_selection_status") or "") == "settled_topology":
            return "challenge_lane", "repo_swe_is_topology_settled_but_still_weaker_than_systems"
    if stability_class in {"mixed_positive", "stable_positive"}:
        return "secondary_proving_lane", "lane_is_positive_but_not_current_primary"
    return "challenge_lane", "lane_requires_protocol_clarity_before_carrying_more_proving_weight"


def build_stage5_cross_lane_review(
    *,
    policy_stability_path: Path = DEFAULT_POLICY_STABILITY,
    repo_swe_family_ab_path: Path = DEFAULT_REPO_SWE_FAMILY_AB,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    policy_payload = _load_json(policy_stability_path)
    repo_swe_family_payload = _load_json(repo_swe_family_ab_path)
    repo_swe_family_state = _repo_swe_family_selection_state(repo_swe_family_payload, source_path=repo_swe_family_ab_path)

    review_rows: list[dict[str, Any]] = []
    primary_lane_id = None
    for row in list(policy_payload.get("rows") or []):
        lane_id = str(row.get("lane_id") or "")
        lane_weight, lane_reason = _lane_weight(row, repo_swe_family_state=repo_swe_family_state)
        if lane_weight == "primary_proving_lane":
            primary_lane_id = lane_id
        review_rows.append(
            {
                "lane_id": lane_id,
                "stability_class": str(row.get("stability_class") or ""),
                "policy_review_conclusion": str(row.get("policy_review_conclusion") or ""),
                "reuse_lift_count": int(row.get("reuse_lift_count") or 0),
                "flat_count": int(row.get("flat_count") or 0),
                "no_lift_count": int(row.get("no_lift_count") or 0),
                "reuse_lift_rate": float(row.get("reuse_lift_rate") or 0.0),
                "lane_weight": lane_weight,
                "lane_weight_reason": lane_reason,
            }
        )

    if primary_lane_id is None:
        primary_lane_id = "lane.systems"
    next_step_reason = (
        "systems_is_currently_cleaner_while_repo_swe_family_selection_remains_open"
        if str(repo_swe_family_state.get("family_selection_status") or "") == "open"
        else "systems_is_currently_cleaner_while_repo_swe_family_ab_surface_is_stale"
        if str(repo_swe_family_state.get("family_selection_status") or "") == "stale_or_incomplete"
        else "systems_is_currently_cleaner_while_repo_swe_remains_topology_settled_but_weaker"
    )
    payload = {
        "schema": "breadboard.darwin.stage5.cross_lane_review.v0",
        "source_refs": {
            "policy_stability_ref": path_ref(policy_stability_path),
            "repo_swe_family_ab_ref": path_ref(repo_swe_family_ab_path),
        },
        "lane_count": len(review_rows),
        "row_count": len(review_rows),
        "rows": review_rows,
        "repo_swe_family_selection": repo_swe_family_state,
        "current_primary_lane_id": primary_lane_id,
        "next_step": "systems_weighted_stage5_compounding_review_with_repo_swe_protocol_challenge",
        "next_step_reason": next_step_reason,
    }

    lines = [
        "# Stage-5 Cross-Lane Review",
        "",
        f"- policy stability: `{path_ref(policy_stability_path)}`",
        f"- repo_swe family A/B: `{path_ref(repo_swe_family_ab_path)}`",
        f"- current primary lane: `{primary_lane_id}`",
        f"- repo_swe family selection: `{repo_swe_family_state['family_selection_status']}`",
    ]
    for row in review_rows:
        lines.append(
            f"- `{row['lane_id']}`: stability=`{row['stability_class']}`, reuse_lift=`{row['reuse_lift_count']}`, "
            f"flat=`{row['flat_count']}`, no_lift=`{row['no_lift_count']}`, weight=`{row['lane_weight']}`"
        )

    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {
        "out_json": str(out_dir / OUT_JSON.name),
        "primary_lane_id": primary_lane_id,
        "repo_swe_family_selection_status": str(repo_swe_family_state["family_selection_status"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 cross-lane review bundle.")
    parser.add_argument("--policy-stability", default=str(DEFAULT_POLICY_STABILITY))
    parser.add_argument("--repo-swe-family-ab", default=str(DEFAULT_REPO_SWE_FAMILY_AB))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_cross_lane_review(
        policy_stability_path=Path(args.policy_stability),
        repo_swe_family_ab_path=Path(args.repo_swe_family_ab),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_cross_lane_review={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
