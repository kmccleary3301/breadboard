from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from breadboard_ext.darwin.stage6 import (  # noqa: E402
    build_stage6_activation_probe_summary,
    build_stage6_activation_transfer_linkage,
    build_stage6_failed_transfer_taxonomy,
    build_stage6_transfer_cases,
    build_stage6_transfer_outcome_summary,
    build_stage6_transfer_quality_scorecard,
)
from scripts.run_darwin_stage6_broader_transfer_canary_v0 import (  # noqa: E402
    OUT_DIR as TRANCHE1_CANARY_DIR,
)
from scripts.run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402
from scripts.run_darwin_stage4_live_economics_pilot_v0 import _campaign_lookup, _write_json  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche2" / "broader_transfer_matrix"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_case_by_lane(compounding_cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rank = {"reuse_lift": 3, "flat": 2, "no_lift": 1, "inconclusive": 0, "invalid": -1}
    by_lane: dict[str, dict[str, Any]] = {}
    for row in compounding_cases:
        lane_id = str(row.get("lane_id") or "")
        current = by_lane.get(lane_id)
        if current is None or rank.get(str(row.get("conclusion") or ""), -2) > rank.get(str(current.get("conclusion") or ""), -2):
            by_lane[lane_id] = dict(row)
    return by_lane


def _run_scheduling_transfer_path(*, out_dir: Path, campaigns: dict[str, dict[str, Any]], systems_case: dict[str, Any]) -> dict[str, Any]:
    spec = campaigns["lane.scheduling"]
    baseline_out = out_dir / "lane.scheduling" / "deadline_first.json"
    transferred_out = out_dir / "lane.scheduling" / "systems_policy_transfer.json"
    replay_out = out_dir / "lane.scheduling" / "systems_policy_transfer_replay.json"

    baseline_row = run_named_lane(
        "lane.scheduling",
        spec,
        out_dir / "runs",
        candidate_id="cand.stage6.transfer.lane.scheduling.deadline_first.v1",
        mutation_operator="baseline_seed",
        topology_id="policy.topology.single_v0",
        policy_bundle_id="policy.topology.single_v0",
        budget_class="class_a",
        perturbation_group="transfer_control",
        task_id="task.darwin.stage6.scheduling.transfer_control",
        trial_label="deadline_first_control",
        command_override=[
            sys.executable,
            "scripts/run_darwin_scheduling_lane_baseline_v0.py",
            "--strategy",
            "deadline_first",
            "--out",
            str(baseline_out.relative_to(ROOT)),
        ],
        result_path_override=str(baseline_out.relative_to(ROOT)),
        kind_override="json_overall_ok",
    )
    transferred_row = run_named_lane(
        "lane.scheduling",
        spec,
        out_dir / "runs",
        candidate_id="cand.stage6.transfer.lane.scheduling.systems_policy.v1",
        mutation_operator="mut.policy.shadow_memory_enable_v1",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        perturbation_group="systems_policy_transfer",
        task_id="task.darwin.stage6.scheduling.transfer.systems_policy",
        trial_label="systems_policy_transfer",
        command_override=[
            sys.executable,
            "scripts/run_darwin_scheduling_lane_baseline_v0.py",
            "--strategy",
            "hybrid_density_deadline",
            "--out",
            str(transferred_out.relative_to(ROOT)),
        ],
        result_path_override=str(transferred_out.relative_to(ROOT)),
        kind_override="json_overall_ok",
    )
    replay_row = run_named_lane(
        "lane.scheduling",
        spec,
        out_dir / "runs",
        candidate_id="cand.stage6.transfer.lane.scheduling.systems_policy.replay.v1",
        mutation_operator="mut.policy.shadow_memory_enable_v1",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        perturbation_group="systems_policy_transfer_replay",
        task_id="task.darwin.stage6.scheduling.transfer.systems_policy.replay",
        trial_label="systems_policy_transfer_replay",
        command_override=[
            sys.executable,
            "scripts/run_darwin_scheduling_lane_baseline_v0.py",
            "--strategy",
            "hybrid_density_deadline",
            "--out",
            str(replay_out.relative_to(ROOT)),
        ],
        result_path_override=str(replay_out.relative_to(ROOT)),
        kind_override="json_overall_ok",
    )
    baseline_score = float(baseline_row["primary_score"])
    transferred_score = float(transferred_row["primary_score"])
    replay_score = float(replay_row["primary_score"])
    return {
        "source_lane_id": "lane.systems",
        "target_lane_id": "lane.scheduling",
        "family_id": str(systems_case.get("family_id") or ""),
        "family_kind": str(systems_case.get("family_kind") or ""),
        "comparison_valid": True,
        "invalid_reason": None,
        "target_baseline_primary_score": baseline_score,
        "target_transferred_primary_score": transferred_score,
        "target_score_lift": round(transferred_score - baseline_score, 6),
        "target_execution_status": "complete",
        "target_candidate_ref": str(transferred_row["candidate_ref"]),
        "target_evaluation_ref": str(transferred_row["evaluation_ref"]),
        "replay_status": "supported" if transferred_score == replay_score else "observed",
        "transfer_policy_mode": "systems_primary_hybrid_scheduling_transfer_v1",
        "transfer_policy_rationale": "test whether the systems-primary active family can retain leverage on the bounded scheduling target using the strongest known scheduling transfer strategy",
        "baseline_candidate_ref": str(baseline_row["candidate_ref"]),
        "baseline_evaluation_ref": str(baseline_row["evaluation_ref"]),
        "replay_candidate_ref": str(replay_row["candidate_ref"]),
        "replay_evaluation_ref": str(replay_row["evaluation_ref"]),
    }


def run_stage6_broader_transfer_matrix(
    *,
    tranche1_dir: Path = TRANCHE1_CANARY_DIR,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    activation_rows = list(_load_json(tranche1_dir / "family_activation_v1.json").get("rows") or [])
    compounding_cases = list(_load_json(tranche1_dir / "compounding_cases_v2.json").get("rows") or [])
    provider_segmentation = _load_json(tranche1_dir / "provider_segmentation_v1.json")
    activation_probe_summary = build_stage6_activation_probe_summary(
        activation_rows=activation_rows,
        compounding_cases=compounding_cases,
    )
    strongest_cases = _source_case_by_lane(compounding_cases)
    campaigns = _campaign_lookup()

    systems_execution = _run_scheduling_transfer_path(
        out_dir=out_dir,
        campaigns=campaigns,
        systems_case=strongest_cases["lane.systems"],
    )
    transfer_execution_rows = [systems_execution]
    inactive_transfer_rows = [
        {
            "transfer_case_id": "transfer_case.stage6.lane.repo_swe.lane.systems.component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0.v2",
            "source_lane_id": "lane.repo_swe",
            "target_lane_id": "lane.systems",
            "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
            "family_kind": "tool_scope",
            "activation_status": "inactive",
            "lane_weight": "challenge_lane",
            "transfer_status": "invalid",
            "transfer_reason": "inactive_family_not_authorized_for_stage6_transfer",
            "allowed_by_policy": False,
            "comparison_envelope_digest": "",
            "source_compounding_case_id": "",
            "replay_status": "not_applicable",
            "transfer_policy_mode": "inactive_family_block_v1",
            "transfer_policy_rationale": "held-back Repo_SWE tool_scope family stays outside bounded Stage-6 transfer scope",
            "source_conclusion": "inactive",
            "target_baseline_primary_score": None,
            "target_transferred_primary_score": None,
            "target_score_lift": None,
            "target_execution_status": "not_authorized",
            "target_candidate_ref": None,
            "target_evaluation_ref": None,
        }
    ]
    transfer_cases = build_stage6_transfer_cases(
        activation_rows=activation_rows,
        compounding_cases=compounding_cases,
        transfer_execution_rows=transfer_execution_rows,
        inactive_transfer_rows=inactive_transfer_rows,
    )
    transfer_summary = build_stage6_transfer_outcome_summary(transfer_cases=transfer_cases)
    failed_taxonomy = build_stage6_failed_transfer_taxonomy(transfer_cases=transfer_cases)
    transfer_scorecard = build_stage6_transfer_quality_scorecard(
        transfer_cases=transfer_cases,
        provider_segmentation=provider_segmentation,
    )
    linkage = build_stage6_activation_transfer_linkage(
        activation_probe_summary=activation_probe_summary,
        transfer_cases=transfer_cases,
    )

    transfer_policy_path = out_dir / "transfer_policy_v1.json"
    transfer_execution_path = out_dir / "transfer_execution_v1.json"
    transfer_cases_path = out_dir / "transfer_cases_v2.json"
    transfer_summary_path = out_dir / "transfer_outcome_summary_v1.json"
    failed_taxonomy_path = out_dir / "failed_transfer_taxonomy_v1.json"
    transfer_scorecard_path = out_dir / "transfer_quality_scorecard_v1.json"
    linkage_path = out_dir / "activation_transfer_linkage_v1.json"
    summary_path = out_dir / "broader_transfer_matrix_v0.json"

    transfer_policy = {
        "schema": "breadboard.darwin.stage6.transfer_policy.v1",
        "row_count": 3,
        "rows": [
            {
                "source_lane_id": "lane.systems",
                "target_lane_id": "lane.scheduling",
                "family_id": str(strongest_cases["lane.systems"].get("family_id") or ""),
                "policy_mode": "systems_primary_hybrid_scheduling_transfer_v1",
                "authorized": True,
                "rationale": "bounded Systems-primary transfer uses the strongest known scheduling strategy for the first live target confirmation",
            },
            {
                "source_lane_id": "lane.repo_swe",
                "target_lane_id": "lane.systems",
                "family_id": str(strongest_cases["lane.repo_swe"].get("family_id") or ""),
                "policy_mode": "challenge_activation_probe_only_v1",
                "authorized": True,
                "rationale": "Repo_SWE remains a bounded challenge transfer surface during the Systems-primary tranche",
            },
            {
                "source_lane_id": "lane.repo_swe",
                "target_lane_id": "lane.systems",
                "family_id": "component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0",
                "policy_mode": "inactive_family_block_v1",
                "authorized": False,
                "rationale": "held-back Repo_SWE tool_scope family is outside current Stage-6 transfer scope",
            },
        ],
    }

    _write_json(transfer_policy_path, transfer_policy)
    _write_json(
        transfer_execution_path,
        {
            "schema": "breadboard.darwin.stage6.transfer_execution_bundle.v1",
            "row_count": len(transfer_execution_rows),
            "rows": transfer_execution_rows,
        },
    )
    _write_json(transfer_cases_path, {"schema": "breadboard.darwin.stage6.transfer_case_bundle.v2", "row_count": len(transfer_cases), "rows": transfer_cases})
    _write_json(transfer_summary_path, transfer_summary)
    _write_json(failed_taxonomy_path, failed_taxonomy)
    _write_json(transfer_scorecard_path, transfer_scorecard)
    _write_json(linkage_path, linkage)

    summary = {
        "schema": "breadboard.darwin.stage6.broader_transfer_matrix.v0",
        "transfer_case_count": len(transfer_cases),
        "retained_count": int(transfer_summary["retained_count"]),
        "degraded_count": int(transfer_summary["degraded_count"]),
        "activation_probe_count": int(transfer_summary["activation_probe_count"]),
        "provider_segmentation_status": str(provider_segmentation.get("provider_segmentation_status") or ""),
        "claim_rows_have_canonical_provider_segmentation": bool(provider_segmentation.get("claim_rows_have_canonical_provider_segmentation")),
        "transfer_policy_ref": path_ref(transfer_policy_path),
        "transfer_execution_ref": path_ref(transfer_execution_path),
        "transfer_cases_ref": path_ref(transfer_cases_path),
        "transfer_summary_ref": path_ref(transfer_summary_path),
        "failed_taxonomy_ref": path_ref(failed_taxonomy_path),
        "transfer_scorecard_ref": path_ref(transfer_scorecard_path),
        "activation_transfer_linkage_ref": path_ref(linkage_path),
    }
    _write_json(summary_path, summary)
    return {"summary_path": str(summary_path), "retained_count": summary["retained_count"], "transfer_case_count": len(transfer_cases)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-6 broader transfer matrix.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    summary = run_stage6_broader_transfer_matrix(out_dir=Path(args.out_dir) if args.out_dir else OUT_DIR)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_broader_transfer_matrix={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
