from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for candidate in (str(ROOT), str(SCRIPTS_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from build_darwin_bootstrap_rollup_v0 import write_rollup
from build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from build_darwin_weekly_packet_v1 import write_weekly_packet
from check_darwin_lane_readiness_v0 import write_readiness
from emit_darwin_evidence_and_claims_v1 import emit_evidence_and_claims
from run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from run_darwin_t1_live_baselines_v1 import run_live_baselines


def run_smoke() -> dict:
    bootstrap = write_bootstrap_specs()
    topology = write_topology_runner_manifest()
    live = run_live_baselines()
    scorecard = write_scorecard()
    weekly = write_weekly_packet()
    evidence = emit_evidence_and_claims()
    readiness = write_readiness()
    rollup = write_rollup()
    return {
        "bootstrap": bootstrap,
        "topology": topology,
        "live": live,
        "scorecard": scorecard,
        "weekly": weekly,
        "evidence": evidence,
        "readiness": readiness,
        "rollup": rollup,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the DARWIN T1 end-to-end smoke flow.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_smoke()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
