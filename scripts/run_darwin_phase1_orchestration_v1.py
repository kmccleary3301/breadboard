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
from build_darwin_future_lane_placeholders_v0 import write_future_lane_placeholders
from build_darwin_topology_family_runner_v0 import write_topology_runner_manifest
from build_darwin_typed_search_core_v1 import write_typed_search_core
from build_darwin_weekly_packet_v1 import write_weekly_packet
from build_darwin_invalid_comparison_ledger_v1 import write_invalid_comparison_ledger
from check_darwin_lane_readiness_v0 import write_readiness
from emit_darwin_evidence_and_claims_v1 import emit_evidence_and_claims
from emit_darwin_search_evidence_v1 import emit_search_evidence
from run_darwin_t1_baseline_scorecard_v1 import write_scorecard
from run_darwin_t1_live_baselines_v1 import run_live_baselines
from run_darwin_t2_search_smoke_v1 import run_search_smoke


def run_orchestration() -> dict:
    bootstrap = write_bootstrap_specs()
    placeholders = write_future_lane_placeholders()
    topology = write_topology_runner_manifest()
    live = run_live_baselines()
    typed_search = write_typed_search_core()
    search = run_search_smoke()
    scorecard = write_scorecard(include_search=True)
    weekly = write_weekly_packet(include_search=True)
    baseline_evidence = emit_evidence_and_claims()
    invalid = write_invalid_comparison_ledger()
    search_evidence = emit_search_evidence()
    readiness = write_readiness(include_search=True)
    rollup = write_rollup()
    return {
        "bootstrap": bootstrap,
        "placeholders": placeholders,
        "topology": topology,
        "live": live,
        "typed_search": typed_search,
        "search": search,
        "scorecard": scorecard,
        "weekly": weekly,
        "baseline_evidence": baseline_evidence,
        "invalid": invalid,
        "search_evidence": search_evidence,
        "readiness": readiness,
        "rollup": rollup,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the DARWIN >60% phase-1 orchestration flow.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_orchestration()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
