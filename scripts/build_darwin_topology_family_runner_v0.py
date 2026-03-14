from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
DEFAULT_POLICY_REGISTRY = ROOT / "docs" / "contracts" / "darwin" / "registries" / "policy_registry_v0.json"
DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "topology"


TOPOLOGY_FAMILIES = [
    {
        "topology_id": "policy.topology.single_v0",
        "label": "single_agent_verifier",
        "required_policy_bundle_id": "policy.topology.single_v0",
    },
    {
        "topology_id": "policy.topology.pev_v0",
        "label": "planner_executor_verifier",
        "required_policy_bundle_id": "policy.topology.pev_v0",
    },
    {
        "topology_id": "policy.topology.pwrv_v0",
        "label": "planner_workers_referee_verifier",
        "required_policy_bundle_id": "policy.topology.pwrv_v0",
    },
]

TOPOLOGY_COMPATIBILITY = {
    "lane.atp": {
        "policy.topology.single_v0": {
            "supported": True,
            "execution_descriptor": "single agent baseline with verifier-rich evaluator",
            "note": "ATP baseline comparator",
        },
        "policy.topology.pev_v0": {
            "supported": True,
            "execution_descriptor": "planner executor verifier loop",
            "note": "shadow-approved for structured proof search",
        },
        "policy.topology.pwrv_v0": {
            "supported": True,
            "execution_descriptor": "planner workers referee verifier",
            "note": "kept shadow until higher-tier evidence exists",
        },
    },
    "lane.harness": {
        "policy.topology.single_v0": {
            "supported": True,
            "execution_descriptor": "single agent harness baseline",
            "note": "baseline comparator",
        },
        "policy.topology.pev_v0": {
            "supported": True,
            "execution_descriptor": "planner executor verifier loop",
            "note": "first search-enabled lane",
        },
        "policy.topology.pwrv_v0": {
            "supported": False,
            "execution_descriptor": "planner workers referee verifier",
            "note": "deferred until repo-scale evidence exists",
        },
    },
    "lane.systems": {
        "policy.topology.single_v0": {
            "supported": True,
            "execution_descriptor": "single agent systems baseline",
            "note": "baseline comparator",
        },
        "policy.topology.pev_v0": {
            "supported": True,
            "execution_descriptor": "planner executor verifier loop",
            "note": "allowed for bounded systems tuning",
        },
        "policy.topology.pwrv_v0": {
            "supported": True,
            "execution_descriptor": "planner workers referee verifier",
            "note": "shadow-supported for systems-only bakeoffs",
        },
    },
    "lane.repo_swe": {
        "policy.topology.single_v0": {
            "supported": True,
            "execution_descriptor": "single agent patch-and-test baseline",
            "note": "Phase-1 baseline comparator",
        },
        "policy.topology.pev_v0": {
            "supported": True,
            "execution_descriptor": "planner executor verifier patch loop",
            "note": "first repo-scale search topology",
        },
        "policy.topology.pwrv_v0": {
            "supported": True,
            "execution_descriptor": "planner workers referee verifier patch loop",
            "note": "allowed but not yet promoted",
        },
    },
    "lane.scheduling": {
        "policy.topology.single_v0": {
            "supported": True,
            "execution_descriptor": "single agent schedule constructor",
            "note": "placeholder only",
        },
        "policy.topology.pev_v0": {
            "supported": True,
            "execution_descriptor": "planner executor verifier scheduler",
            "note": "placeholder only",
        },
        "policy.topology.pwrv_v0": {
            "supported": False,
            "execution_descriptor": "planner workers referee verifier scheduler",
            "note": "deferred until scenario-pack exists",
        },
    },
    "lane.research": {
        "policy.topology.single_v0": {
            "supported": True,
            "execution_descriptor": "single agent evidence synthesis baseline",
            "note": "placeholder only",
        },
        "policy.topology.pev_v0": {
            "supported": False,
            "execution_descriptor": "planner executor verifier evidence synthesis",
            "note": "deferred until citation checker exists",
        },
        "policy.topology.pwrv_v0": {
            "supported": False,
            "execution_descriptor": "planner workers referee verifier evidence synthesis",
            "note": "deferred until adversarial review harness exists",
        },
    },
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_topology_runner_manifest(
    bootstrap_manifest_path: Path = DEFAULT_BOOTSTRAP_MANIFEST,
    policy_registry_path: Path = DEFAULT_POLICY_REGISTRY,
) -> dict:
    bootstrap = _load_json(bootstrap_manifest_path)
    registry = _load_json(policy_registry_path)

    specs: list[dict] = []
    for row in bootstrap.get("specs") or []:
        spec_path = ROOT / row["path"]
        specs.append(_load_json(spec_path))

    approved_bundles = {bundle["policy_bundle_id"] for bundle in registry.get("bundles") or []}
    matrix: list[dict] = []
    for spec in specs:
        for family in TOPOLOGY_FAMILIES:
            compatibility = TOPOLOGY_COMPATIBILITY.get(spec["lane_id"], {}).get(
                family["topology_id"],
                {
                    "supported": False,
                    "execution_descriptor": family["label"],
                    "note": "no compatibility policy recorded",
                },
            )
            matrix.append(
                {
                    "campaign_id": spec["campaign_id"],
                    "lane_id": spec["lane_id"],
                    "topology_id": family["topology_id"],
                    "label": family["label"],
                    "policy_bundle_present": family["required_policy_bundle_id"] in approved_bundles,
                    "supported": compatibility["supported"],
                    "execution_descriptor": compatibility["execution_descriptor"],
                    "compatibility_note": compatibility["note"],
                    "budget_class": spec["budget_class"],
                }
            )

    return {
        "schema": "breadboard.darwin.topology_family_runner.v0",
        "family_count": len(TOPOLOGY_FAMILIES),
        "campaign_count": len(specs),
        "matrix_count": len(matrix),
        "matrix": matrix,
    }


def write_topology_runner_manifest(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_topology_runner_manifest()
    out_path = out_dir / "topology_family_runner_v0.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "matrix_count": payload["matrix_count"], "family_count": payload["family_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the DARWIN T1 topology-family runner scaffold.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = write_topology_runner_manifest(Path(args.out_dir))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"wrote topology runner scaffold: {summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
