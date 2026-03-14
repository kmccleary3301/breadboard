from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


INPUTS = {
    "bootstrap_manifest": ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json",
    "topology_manifest": ROOT / "artifacts" / "darwin" / "topology" / "topology_family_runner_v0.json",
    "scorecard": ROOT / "artifacts" / "darwin" / "scorecards" / "t1_baseline_scorecard.latest.json",
    "weekly_packet": ROOT / "artifacts" / "darwin" / "weekly" / "weekly_evidence_packet.latest.json",
    "live_baseline_summary": ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json",
    "lane_registry": ROOT / "docs" / "contracts" / "darwin" / "registries" / "lane_registry_v0.json",
    "policy_registry": ROOT / "docs" / "contracts" / "darwin" / "registries" / "policy_registry_v0.json",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_rollup() -> dict:
    loaded = {name: _load_json(path) for name, path in INPUTS.items() if path.exists()}
    return {
        "schema": "breadboard.darwin.bootstrap_rollup.v0",
        "lane_registry_count": len((loaded.get("lane_registry") or {}).get("lanes") or []),
        "policy_registry_count": len((loaded.get("policy_registry") or {}).get("bundles") or []),
        "bootstrap_spec_count": int((loaded.get("bootstrap_manifest") or {}).get("spec_count") or 0),
        "topology_matrix_count": int((loaded.get("topology_manifest") or {}).get("matrix_count") or 0),
        "scorecard_present": "scorecard" in loaded,
        "weekly_packet_present": "weekly_packet" in loaded,
        "live_baseline_present": "live_baseline_summary" in loaded,
        "inputs": {name: str(path.relative_to(ROOT)) for name, path in INPUTS.items() if path.exists()},
    }


def write_rollup() -> dict:
    out_path = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_rollup_v0.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_rollup()
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "bootstrap_spec_count": payload["bootstrap_spec_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DARWIN bootstrap rollup artifact.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_rollup()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"bootstrap_rollup={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
