from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_darwin_t1_live_baselines_v1 import run_named_lane


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
SEARCH_SUMMARY = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
TRANSFER_LEDGER = ROOT / "artifacts" / "darwin" / "search" / "transfer_ledger_v1.json"
OUT_PATH = ROOT / "artifacts" / "darwin" / "search" / "phase1_replay_audit_v1.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _campaign_lookup() -> dict[str, dict]:
    manifest = _load_json(BOOTSTRAP_MANIFEST)
    rows = {}
    for row in manifest.get("specs") or []:
        spec = _load_json(ROOT / row["path"])
        rows[spec["lane_id"]] = spec
    return rows


def _run_replay(spec: dict, *, lane_id: str, candidate_id: str, mutation_operator: str, topology_id: str, policy_bundle_id: str, budget_class: str, task_id: str, trial_label: str, command_override: list[str] | None = None, result_path_override: str | None = None, kind_override: str | None = None) -> dict:
    replay_row = run_named_lane(
        lane_id,
        spec,
        ROOT / "artifacts" / "darwin" / "search" / "replay_audit_runs",
        candidate_id=f"{candidate_id}.phase1_replay",
        mutation_operator=mutation_operator,
        topology_id=topology_id,
        policy_bundle_id=policy_bundle_id,
        budget_class=budget_class,
        perturbation_group="phase1_replay_audit",
        task_id=task_id,
        trial_label=trial_label,
        command_override=command_override,
        result_path_override=result_path_override,
        kind_override=kind_override,
    )
    return replay_row


def write_replay_audit() -> dict:
    campaigns = _campaign_lookup()
    transfer = _load_json(TRANSFER_LEDGER)
    audits = []

    scheduling_replay = _run_replay(
        campaigns["lane.scheduling"],
        lane_id="lane.scheduling",
        candidate_id="cand.lane.scheduling.mut.hybrid.v1",
        mutation_operator="mut.scheduler.strategy_hybrid_v1",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        task_id="task.darwin.scheduling.constraint_objective_smoke.search.cycle2.replay",
        trial_label="phase1_final_hybrid_replay",
        command_override=[
            sys.executable,
            "scripts/run_darwin_scheduling_lane_baseline_v0.py",
            "--strategy",
            "hybrid_density_deadline",
            "--out",
            "artifacts/darwin/search/replay_audit_runs/lane.scheduling/hybrid_density_deadline.phase1_replay.json",
        ],
        result_path_override="artifacts/darwin/search/replay_audit_runs/lane.scheduling/hybrid_density_deadline.phase1_replay.json",
        kind_override="json_overall_ok",
    )
    audits.append(
        {
            "audit_id": "audit.phase1.scheduling.hybrid_replay.v1",
            "lane_id": "lane.scheduling",
            "candidate_id": "cand.lane.scheduling.mut.hybrid.v1",
            "expected_primary_score": 1.0,
            "replay_primary_score": scheduling_replay["primary_score"],
            "stable": scheduling_replay["primary_score"] == 1.0,
            "mode": "promoted_candidate",
            "replay_candidate_ref": scheduling_replay["candidate_ref"],
        }
    )

    transfer_attempt = (transfer.get("attempts") or [])[0]
    research_replay = _run_replay(
        campaigns["lane.research"],
        lane_id="lane.research",
        candidate_id="cand.lane.research.mut.prompt.v1",
        mutation_operator="mut.prompt.tighten_acceptance_v1",
        topology_id="policy.topology.single_v0",
        policy_bundle_id="policy.topology.single_v0",
        budget_class="class_a",
        task_id="task.darwin.research.evidence_synthesis_smoke.search.replay",
        trial_label="phase1_final_transfer_replay",
        command_override=[
            sys.executable,
            "scripts/run_darwin_research_lane_baseline_v0.py",
            "--strategy",
            "bridge_synthesis",
            "--out",
            "artifacts/darwin/search/replay_audit_runs/lane.research/bridge_synthesis.phase1_replay.json",
        ],
        result_path_override="artifacts/darwin/search/replay_audit_runs/lane.research/bridge_synthesis.phase1_replay.json",
        kind_override="json_overall_ok",
    )
    audits.append(
        {
            "audit_id": "audit.phase1.research.transfer_replay.v1",
            "lane_id": "lane.research",
            "candidate_id": "cand.lane.research.mut.prompt.v1",
            "expected_primary_score": transfer_attempt["transferred_primary_score"],
            "replay_primary_score": research_replay["primary_score"],
            "stable": research_replay["primary_score"] == transfer_attempt["transferred_primary_score"],
            "mode": "transfer_promoted_candidate",
            "replay_candidate_ref": research_replay["candidate_ref"],
        }
    )

    repo_replay = _run_replay(
        campaigns["lane.repo_swe"],
        lane_id="lane.repo_swe",
        candidate_id="cand.lane.repo_swe.mut.pev.v1",
        mutation_operator="mut.topology.single_to_pev_v1",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        task_id="task.darwin.repo_swe.patch_workspace_smoke.search.replay",
        trial_label="phase1_final_repo_replay",
    )
    audits.append(
        {
            "audit_id": "audit.phase1.repo_swe.search_candidate_replay.v1",
            "lane_id": "lane.repo_swe",
            "candidate_id": "cand.lane.repo_swe.mut.pev.v1",
            "expected_primary_score": 1.0,
            "replay_primary_score": repo_replay["primary_score"],
            "stable": repo_replay["primary_score"] == 1.0,
            "mode": "representative_search_candidate",
            "replay_candidate_ref": repo_replay["candidate_ref"],
        }
    )

    payload = {
        "schema": "breadboard.darwin.phase1_replay_audit.v1",
        "audit_count": len(audits),
        "all_stable": all(row["stable"] for row in audits),
        "audits": audits,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(OUT_PATH), "audit_count": payload["audit_count"], "all_stable": payload["all_stable"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the final DARWIN Phase-1 replay audit set.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_replay_audit()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"phase1_replay_audit={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
