from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
DEFAULT_TOPOLOGY_MANIFEST = ROOT / "artifacts" / "darwin" / "topology" / "topology_family_runner_v0.json"
DEFAULT_LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
DEFAULT_CLAIM_LEDGER = ROOT / "artifacts" / "darwin" / "claims" / "claim_ledger_v1.json"
DEFAULT_EVIDENCE_BUNDLE = ROOT / "artifacts" / "darwin" / "evidence" / "darwin_phase1_t1_live_baselines_bundle_v1.json"
DEFAULT_SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "scorecards"


PREREQUISITE_CHECKS: dict[str, dict[str, str]] = {
    "lane.atp": {
        "corrected_benchmark_stack": "docs/contracts/atp/README.md",
        "frozen_slice_policy": "docs/contracts/benchmarks/ATP_HILBERT_TRANCHE_SELECTION_POLICY_V1.md",
        "artifact_complete_replay": "docs/atp_phase1_completion_gate_2026-03-13.md",
    },
    "lane.harness": {
        "parity_runner": "tests/test_parity_runner.py",
        "drift_audit": "tests/test_audit_e4_target_drift.py",
        "replay_bundle": "scripts/run_phase5_replay_reliability_bundle.sh",
    },
    "lane.systems": {
        "compiler_or_runtime": "agentic_coder_prototype",
        "benchmark_harness": "scripts/build_ink_publication_benchmark_pack.py",
        "budget_class_enforcement": "docs/contracts/policies/P3_SPEND_ATTRIBUTION_BASELINE_V1.md",
    },
    "lane.repo_swe": {
        "repo_snapshotting": "docs/contracts/darwin/DARWIN_REPO_SWE_BASELINE_V0.md",
        "test_harness": "tests/test_opencode_patch_apply_codex.py",
        "contamination_controls": "docs/contracts/darwin/DARWIN_CLAIM_LADDER_V0.md",
    },
    "lane.scheduling": {
        "scenario_pack": "docs/contracts/darwin/fixtures/scheduling_scenario_pack_v0.json",
        "constraint_checker": "scripts/run_darwin_scheduling_lane_baseline_v0.py",
        "budget_curves": "docs/contracts/darwin/DARWIN_TYPED_SEARCH_CORE_V1.md",
    },
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _exists(rel_path: str) -> bool:
    return (ROOT / rel_path).exists()


def build_scorecard(
    bootstrap_manifest_path: Path = DEFAULT_BOOTSTRAP_MANIFEST,
    topology_manifest_path: Path = DEFAULT_TOPOLOGY_MANIFEST,
    *,
    include_search: bool = False,
) -> dict:
    bootstrap = _load_json(bootstrap_manifest_path)
    topology = _load_json(topology_manifest_path)
    live_summary = _load_json(DEFAULT_LIVE_SUMMARY) if DEFAULT_LIVE_SUMMARY.exists() else {"lanes": []}
    live_by_lane = {row["lane_id"]: row for row in live_summary.get("lanes") or []}
    claim_ids = set()
    if DEFAULT_CLAIM_LEDGER.exists():
        claim_ledger = _load_json(DEFAULT_CLAIM_LEDGER)
        claim_ids = {claim["claim_id"] for claim in claim_ledger.get("claims") or []}
    evidence_present = DEFAULT_EVIDENCE_BUNDLE.exists()
    search_by_lane: dict[str, dict] = {}
    if include_search and DEFAULT_SEARCH_SUMMARY.exists():
        search_summary = _load_json(DEFAULT_SEARCH_SUMMARY)
        search_by_lane = {row["lane_id"]: row for row in search_summary.get("lanes") or []}

    topology_counts: dict[str, int] = {}
    for row in topology.get("matrix") or []:
        lane_id = str(row["lane_id"])
        topology_counts[lane_id] = topology_counts.get(lane_id, 0) + 1

    lane_rows: list[dict] = []
    for row in bootstrap.get("specs") or []:
        spec = _load_json(ROOT / row["path"])
        lane_id = spec["lane_id"]
        prereq_checks = PREREQUISITE_CHECKS.get(lane_id, {})
        prereq_results = {name: _exists(path) for name, path in prereq_checks.items()}
        campaign_spec_valid = True
        policy_present = True
        topology_family_count = topology_counts.get(lane_id, 0)
        live_lane = live_by_lane.get(lane_id)
        components = {
            "campaign_spec_valid": campaign_spec_valid,
            "policy_present": policy_present,
            "topology_families_present": topology_family_count >= 3,
            "prerequisites_passed": all(prereq_results.values()) if prereq_results else False,
            "live_baseline_passed": bool(live_lane and live_lane.get("verifier_status") == "passed"),
            "evidence_bundle_present": evidence_present,
            "claim_present": f"claim.darwin.phase1.{lane_id}.live_baseline.v1" in claim_ids,
        }
        score = sum(1.0 for ok in components.values() if ok) / len(components)
        search_row = search_by_lane.get(lane_id, {})
        lane_rows.append(
            {
                "lane_id": lane_id,
                "campaign_id": spec["campaign_id"],
                "normalized_score": round(score, 6),
                "status": "ready" if score >= 1.0 else "partial",
                "topology_family_count": topology_family_count,
                "live_primary_score": live_lane.get("primary_score") if live_lane else None,
                "search_enabled": bool(search_row),
                "mutation_trial_count": int(search_row.get("mutation_trial_count") or 0),
                "baseline_primary_score": search_row.get("baseline_primary_score"),
                "best_mutated_score": search_row.get("best_mutated_score"),
                "comparative_delta": search_row.get("comparative_delta"),
                "best_candidate_id": search_row.get("best_candidate_id"),
                "promotion_status": search_row.get("promotion_status"),
                "archive_size": search_row.get("archive_size"),
                "invalid_comparison_count": search_row.get("invalid_comparison_count", 0),
                "search_maturity": "promotion_capable" if search_row else "baseline_only",
                "components": components,
                "prerequisites": prereq_results,
            }
        )

    mean_score = sum(row["normalized_score"] for row in lane_rows) / len(lane_rows) if lane_rows else 0.0
    overall_ok = all(row["status"] == "ready" for row in lane_rows)
    return {
        "schema": "breadboard.darwin.t1_baseline_scorecard.v1",
        "score_type": "live_comparative_readiness" if include_search else "structural_bootstrap_readiness",
        "overall_ok": overall_ok,
        "mean_normalized_score": round(mean_score, 6),
        "lane_count": len(lane_rows),
        "lanes": lane_rows,
    }


def _to_markdown(payload: dict) -> str:
    lines = [
        "# DARWIN T1 Baseline Scorecard v1",
        "",
        f"- overall_ok: `{str(payload['overall_ok']).lower()}`",
        f"- mean_normalized_score: `{payload['mean_normalized_score']}`",
        "",
        "| lane | status | normalized_score | topology_families | search_trials | delta |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for lane in payload.get("lanes") or []:
        lines.append(
            f"| `{lane['lane_id']}` | `{lane['status']}` | `{lane['normalized_score']}` | `{lane['topology_family_count']}` | `{lane.get('mutation_trial_count', 0)}` | `{lane.get('comparative_delta', 'n/a')}` |"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_scorecard(out_dir: Path = DEFAULT_OUT_DIR, *, include_search: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_scorecard(include_search=include_search)
    out_json = out_dir / "t1_baseline_scorecard.latest.json"
    out_md = out_dir / "t1_baseline_scorecard.latest.md"
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    return {"out_json": str(out_json), "out_md": str(out_md), "overall_ok": payload["overall_ok"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the DARWIN T1 structural baseline scorecard.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-search", action="store_true")
    args = parser.parse_args()

    summary = write_scorecard(Path(args.out_dir), include_search=args.include_search)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"scorecard_json={summary['out_json']}")
        print(f"scorecard_md={summary['out_md']}")
        print(f"overall_ok={summary['overall_ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
