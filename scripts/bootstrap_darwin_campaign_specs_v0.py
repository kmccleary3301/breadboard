from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import validate_campaign_spec
DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "bootstrap"


def _base_campaign(*, campaign_id: str, lane_id: str, objective_id: str, suite_id: str, allowed_tools: list[str]) -> dict:
    return {
        "schema": "breadboard.darwin.campaign_spec.v0",
        "campaign_id": campaign_id,
        "lane_id": lane_id,
        "objective_id": objective_id,
        "benchmark_or_eval_suite_id": suite_id,
        "budget_class": "class_a",
        "seed_set": [11, 17, 23],
        "topology_family": "policy.topology.single_v0",
        "policy_bundle_id": "policy.topology.single_v0",
        "memory_policy_id": "policy.memory.flat_v0",
        "environment_digest": "sha256:darwin-phase1-bootstrap",
        "allowed_tools": allowed_tools,
        "promotion_rules": {
            "promotion_metric": "mean_normalized_score",
            "min_retention": 0.9,
        },
        "rollback_rules": {
            "artifact_completeness_floor": 0.99,
            "max_budget_overrun_ratio": 1.5,
        },
        "claim_target": "internal",
    }


def build_bootstrap_specs() -> list[dict]:
    return [
        _base_campaign(
            campaign_id="camp.darwin.phase1.atp.bootstrap.v0",
            lane_id="lane.atp",
            objective_id="objective.atp.corrected_benchmark_baseline",
            suite_id="suite.atp.corrected.phase1.v0",
            allowed_tools=["lean_verifier", "python", "filesystem_read"],
        ),
        _base_campaign(
            campaign_id="camp.darwin.phase1.harness.bootstrap.v0",
            lane_id="lane.harness",
            objective_id="objective.harness.conformance_baseline",
            suite_id="suite.harness.conformance.phase1.v0",
            allowed_tools=["pytest", "diff", "filesystem_read"],
        ),
        _base_campaign(
            campaign_id="camp.darwin.phase1.systems.bootstrap.v0",
            lane_id="lane.systems",
            objective_id="objective.systems.benchmark_baseline",
            suite_id="suite.systems.algorithms.phase1.v0",
            allowed_tools=["python", "benchmark_runner", "filesystem_read"],
        ),
    ]


def write_bootstrap_specs(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = build_bootstrap_specs()
    manifest_rows: list[dict] = []
    for spec in specs:
        issues = validate_campaign_spec(spec)
        if issues:
            joined = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
            raise ValueError(f"invalid bootstrap campaign spec {spec['campaign_id']}: {joined}")
        path = out_dir / f"{spec['campaign_id']}.json"
        path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            path_ref = str(path.relative_to(ROOT))
        except ValueError:
            path_ref = str(path)
        manifest_rows.append(
            {
                "campaign_id": spec["campaign_id"],
                "lane_id": spec["lane_id"],
                "path": path_ref,
            }
        )

    manifest = {
        "schema": "breadboard.darwin.bootstrap_manifest.v0",
        "spec_count": len(manifest_rows),
        "specs": manifest_rows,
    }
    manifest_path = out_dir / "bootstrap_manifest_v0.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "out_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "spec_count": len(manifest_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit initial DARWIN Phase-1 bootstrap campaign specs.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for bootstrap specs.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary.")
    args = parser.parse_args()

    summary = write_bootstrap_specs(Path(args.out_dir))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"wrote {summary['spec_count']} bootstrap specs to {summary['out_dir']}")
        print(f"manifest: {summary['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
