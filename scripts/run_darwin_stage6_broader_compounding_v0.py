from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref  # noqa: E402
from breadboard_ext.darwin.stage6 import (  # noqa: E402
    build_stage6_comparison_envelope,
    build_stage6_transferred_family_activation_row,
    classify_stage6_broader_compounding_outcome,
)
from scripts.run_darwin_stage4_live_economics_pilot_v0 import _campaign_lookup, _write_json  # noqa: E402
from scripts.run_darwin_t1_live_baselines_v1 import run_named_lane  # noqa: E402


TRANCHE2_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche2" / "broader_transfer_matrix"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "broader_compounding"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _policy_digest(*, round_index: int, family_id: str, mode: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "round_index": round_index,
                "family_id": family_id,
                "mode": mode,
                "campaign": "stage6_broader_compounding_v0",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _run_mode(
    *,
    campaigns: dict[str, dict[str, Any]],
    out_dir: Path,
    round_index: int,
    mode: str,
    strategy: str,
) -> dict[str, Any]:
    out_path = out_dir / "lane.scheduling" / f"round_{round_index}_{mode}.json"
    out_arg = str(out_path.relative_to(ROOT)) if out_path.is_relative_to(ROOT) else str(out_path)
    candidate_id = f"cand.stage6.broader_compounding.lane.scheduling.r{round_index}.{mode}.v1"
    return run_named_lane(
        "lane.scheduling",
        campaigns["lane.scheduling"],
        out_dir / "runs",
        candidate_id=candidate_id,
        mutation_operator="mut.policy.shadow_memory_enable_v1" if mode == "warm_start" else "baseline_seed",
        topology_id="policy.topology.pev_v0" if mode == "warm_start" else "policy.topology.single_v0",
        policy_bundle_id="policy.topology.pev_v0" if mode == "warm_start" else "policy.topology.single_v0",
        budget_class="class_a",
        perturbation_group=f"stage6_broader_compounding_{mode}",
        task_id=f"task.darwin.stage6.scheduling.broader_compounding.{mode}",
        trial_label=f"round_{round_index}_{mode}",
        command_override=[
            sys.executable,
            "scripts/run_darwin_scheduling_lane_baseline_v0.py",
            "--strategy",
            strategy,
            "--out",
            out_arg,
        ],
        result_path_override=out_arg,
        kind_override="json_overall_ok",
    )


def run_stage6_broader_compounding(
    *,
    tranche2_dir: Path = TRANCHE2_DIR,
    out_dir: Path = OUT_DIR,
    rounds: int = 2,
) -> dict[str, Any]:
    transfer_summary = _load_json(tranche2_dir / "transfer_outcome_summary_v1.json")
    transfer_rows = list(transfer_summary.get("rows") or [])
    retained = next(row for row in transfer_rows if str(row.get("transfer_status") or "") == "retained")
    challenge = next(row for row in transfer_rows if str(row.get("transfer_status") or "") == "activation_probe")
    broader_transfer_matrix = _load_json(tranche2_dir / "broader_transfer_matrix_v0.json")
    campaigns = _campaign_lookup()

    transferred_activation = build_stage6_transferred_family_activation_row(
        transfer_case=retained,
        target_lane_id="lane.scheduling",
    )
    family_activation_rows = [transferred_activation]
    family_activation_path = out_dir / "family_activation_v1.json"
    _write_json(
        family_activation_path,
        {
            "schema": "breadboard.darwin.stage6.family_activation_bundle.v1",
            "row_count": len(family_activation_rows),
            "rows": family_activation_rows,
        },
    )

    rows: list[dict[str, Any]] = []
    for round_index in range(1, rounds + 1):
        warm_row = _run_mode(
            campaigns=campaigns,
            out_dir=out_dir,
            round_index=round_index,
            mode="warm_start",
            strategy="hybrid_density_deadline",
        )
        family_lockout_row = _run_mode(
            campaigns=campaigns,
            out_dir=out_dir,
            round_index=round_index,
            mode="family_lockout",
            strategy="value_density",
        )
        single_lockout_row = _run_mode(
            campaigns=campaigns,
            out_dir=out_dir,
            round_index=round_index,
            mode="single_family_lockout",
            strategy="deadline_first",
        )
        score_lift_vs_family_lockout = round(
            float(warm_row["primary_score"]) - float(family_lockout_row["primary_score"]),
            6,
        )
        score_lift_vs_single_lockout = round(
            float(warm_row["primary_score"]) - float(single_lockout_row["primary_score"]),
            6,
        )
        runtime_lift_vs_family_lockout_ms = int(warm_row["wall_clock_ms"]) - int(family_lockout_row["wall_clock_ms"])
        runtime_lift_vs_single_lockout_ms = int(warm_row["wall_clock_ms"]) - int(single_lockout_row["wall_clock_ms"])
        cost_lift_vs_family_lockout_usd = 0.0
        cost_lift_vs_single_lockout_usd = 0.0
        status, rationale = classify_stage6_broader_compounding_outcome(
            score_lift_vs_family_lockout=score_lift_vs_family_lockout,
            score_lift_vs_single_lockout=score_lift_vs_single_lockout,
            runtime_lift_vs_family_lockout_ms=runtime_lift_vs_family_lockout_ms,
            runtime_lift_vs_single_lockout_ms=runtime_lift_vs_single_lockout_ms,
            cost_lift_vs_family_lockout_usd=cost_lift_vs_family_lockout_usd,
            cost_lift_vs_single_lockout_usd=cost_lift_vs_single_lockout_usd,
        )
        policy_digest = _policy_digest(
            round_index=round_index,
            family_id=str(retained.get("family_id") or ""),
            mode="warm_start",
        )
        rows.append(
            {
                "schema": "breadboard.darwin.stage6.broader_compounding_row.v1",
                "round_index": round_index,
                "lane_id": "lane.scheduling",
                "source_lane_id": "lane.systems",
                "family_id": str(retained.get("family_id") or ""),
                "family_kind": str(retained.get("family_kind") or ""),
                "family_state": "retained_transfer_source",
                "comparison_mode_pair": ["warm_start", "family_lockout", "single_family_lockout"],
                "policy_digest": policy_digest,
                "comparison_envelope": build_stage6_comparison_envelope(
                    lane_id="lane.scheduling",
                    operator_id="mut.policy.shadow_memory_enable_v1",
                    family_id=str(retained.get("family_id") or ""),
                    family_kind=str(retained.get("family_kind") or ""),
                    activation_status="active",
                    lane_weight="retained_transfer_target",
                    comparison_mode_pair=["warm_start", "family_lockout", "single_family_lockout"],
                    evaluator_pack_version="stage6.evalpack.scheduling.v1",
                    comparison_envelope_digest=policy_digest,
                    policy_digest=policy_digest,
                    active_family_id=str(retained.get("family_id") or ""),
                    source_transfer_basis=str(retained.get("transfer_case_id") or ""),
                    target_lane_id="lane.scheduling",
                ),
                "warm_start": {
                    "strategy": "hybrid_density_deadline",
                    "primary_score": float(warm_row["primary_score"]),
                    "wall_clock_ms": int(warm_row["wall_clock_ms"]),
                    "candidate_ref": str(warm_row["candidate_ref"]),
                    "evaluation_ref": str(warm_row["evaluation_ref"]),
                    "execution_mode": "local_baseline",
                    "provider_origin": "local",
                    "route_class": "local_heuristic",
                    "cost_source": "local_execution",
                },
                "family_lockout": {
                    "strategy": "value_density",
                    "primary_score": float(family_lockout_row["primary_score"]),
                    "wall_clock_ms": int(family_lockout_row["wall_clock_ms"]),
                    "candidate_ref": str(family_lockout_row["candidate_ref"]),
                    "evaluation_ref": str(family_lockout_row["evaluation_ref"]),
                    "execution_mode": "local_baseline",
                    "provider_origin": "local",
                    "route_class": "local_heuristic",
                    "cost_source": "local_execution",
                },
                "single_family_lockout": {
                    "strategy": "deadline_first",
                    "primary_score": float(single_lockout_row["primary_score"]),
                    "wall_clock_ms": int(single_lockout_row["wall_clock_ms"]),
                    "candidate_ref": str(single_lockout_row["candidate_ref"]),
                    "evaluation_ref": str(single_lockout_row["evaluation_ref"]),
                    "execution_mode": "local_baseline",
                    "provider_origin": "local",
                    "route_class": "local_heuristic",
                    "cost_source": "local_execution",
                },
                "score_lift_vs_family_lockout": score_lift_vs_family_lockout,
                "score_lift_vs_single_lockout": score_lift_vs_single_lockout,
                "runtime_lift_vs_family_lockout_ms": runtime_lift_vs_family_lockout_ms,
                "runtime_lift_vs_single_lockout_ms": runtime_lift_vs_single_lockout_ms,
                "cost_lift_vs_family_lockout_usd": cost_lift_vs_family_lockout_usd,
                "cost_lift_vs_single_lockout_usd": cost_lift_vs_single_lockout_usd,
                "broader_compounding_status": status,
                "broader_compounding_rationale": rationale,
                "provider_segmentation_status": str(
                    broader_transfer_matrix.get("provider_segmentation_status") or ""
                ),
                "source_transfer_status": str(retained.get("transfer_status") or ""),
                "source_transfer_case_id": str(retained.get("transfer_case_id") or ""),
            }
        )

    challenge_context = {
        "schema": "breadboard.darwin.stage6.challenge_context.v1",
        "source_lane_id": str(challenge.get("source_lane_id") or ""),
        "target_lane_id": str(challenge.get("target_lane_id") or ""),
        "family_id": str(challenge.get("family_id") or ""),
        "transfer_status": str(challenge.get("transfer_status") or ""),
        "replay_status": str(challenge.get("replay_status") or ""),
        "challenge_role": "challenge_lane_context",
    }
    summary = {
        "schema": "breadboard.darwin.stage6.broader_compounding.v0",
        "source_refs": {
            "tranche2_transfer_summary_ref": path_ref(tranche2_dir / "transfer_outcome_summary_v1.json"),
            "tranche2_transfer_scorecard_ref": path_ref(tranche2_dir / "transfer_quality_scorecard_v1.json"),
            "family_activation_ref": path_ref(family_activation_path),
        },
        "row_count": len(rows),
        "positive_count": sum(1 for row in rows if str(row.get("broader_compounding_status") or "") == "positive_broader_compounding"),
        "flat_count": sum(1 for row in rows if str(row.get("broader_compounding_status") or "") == "flat_broader_compounding"),
        "negative_count": sum(1 for row in rows if str(row.get("broader_compounding_status") or "") == "negative_broader_compounding"),
        "inconclusive_count": sum(1 for row in rows if str(row.get("broader_compounding_status") or "") == "inconclusive_broader_compounding"),
        "provider_segmentation_status": str(broader_transfer_matrix.get("provider_segmentation_status") or ""),
        "claim_rows_have_canonical_provider_segmentation": bool(
            broader_transfer_matrix.get("claim_rows_have_canonical_provider_segmentation")
        ),
        "family_center_decision": "hold_single_retained_family_center",
        "rows": rows,
        "challenge_context": challenge_context,
    }
    summary_path = out_dir / "broader_compounding_v0.json"
    _write_json(summary_path, summary)
    return {
        "out_json": str(summary_path),
        "row_count": len(rows),
        "positive_count": summary["positive_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 6 broader-compounding tranche over the retained transfer base.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage6_broader_compounding()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_broader_compounding={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
