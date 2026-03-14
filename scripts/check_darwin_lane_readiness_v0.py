from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
TOPOLOGY = ROOT / "artifacts" / "darwin" / "topology" / "topology_family_runner_v0.json"
SCORECARD = ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json"
LIVE = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
CLAIMS = ROOT / "artifacts" / "darwin" / "claims" / "claim_ledger_v1.json"
SEARCH = ROOT / "artifacts" / "darwin" / "search" / "search_smoke_summary_v1.json"
SEARCH_ENABLED_LANES = {"lane.harness", "lane.repo_swe", "lane.scheduling"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_readiness(*, include_search: bool = False) -> dict:
    bootstrap = _load_json(BOOTSTRAP)
    topology = _load_json(TOPOLOGY)
    scorecard = _load_json(SCORECARD)
    live = _load_json(LIVE)
    claims = _load_json(CLAIMS)
    search = _load_json(SEARCH) if SEARCH.exists() else {"lanes": []}

    topo_counts: dict[str, int] = {}
    for row in topology.get("matrix") or []:
        topo_counts[row["lane_id"]] = topo_counts.get(row["lane_id"], 0) + 1
    live_status = {row["lane_id"]: row for row in live.get("lanes") or []}
    score_rows = {row["lane_id"]: row for row in scorecard.get("lanes") or []}
    claim_ids = {claim["claim_id"] for claim in claims.get("claims") or []}
    search_lanes = {row["lane_id"] for row in search.get("lanes") or []}

    readiness_rows = []
    for row in bootstrap.get("specs") or []:
        lane_id = row["lane_id"]
        gates = {
            "bootstrap_spec_present": True,
            "topology_matrix_present": topo_counts.get(lane_id, 0) >= 3,
            "live_baseline_present": lane_id in live_status,
            "scorecard_row_present": lane_id in score_rows,
            "claim_present": f"claim.darwin.phase1.{lane_id}.live_baseline.v1" in claim_ids,
            "search_smoke_present": (not include_search) or lane_id not in SEARCH_ENABLED_LANES or lane_id in search_lanes,
        }
        readiness_rows.append(
            {
                "lane_id": lane_id,
                "ok": all(gates.values()),
                "gates": gates,
            }
        )

    return {
        "schema": "breadboard.darwin.lane_readiness.v0",
        "overall_ok": all(row["ok"] for row in readiness_rows),
        "lanes": readiness_rows,
    }


def write_readiness(*, include_search: bool = False) -> dict:
    out_path = ROOT / "artifacts" / "darwin" / "readiness" / "lane_readiness_v0.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_readiness(include_search=include_search)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "overall_ok": payload["overall_ok"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check DARWIN lane readiness gates for the active T1 lanes.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-search", action="store_true")
    args = parser.parse_args()
    summary = write_readiness(include_search=args.include_search)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"lane_readiness={summary['out_path']}")
        print(f"overall_ok={summary['overall_ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
