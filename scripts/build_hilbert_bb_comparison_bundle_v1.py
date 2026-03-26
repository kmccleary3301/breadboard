#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from scripts.build_hilbert_comparison_packs_v1 import canonicalize_task_input_text
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from build_hilbert_comparison_packs_v1 import canonicalize_task_input_text

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v1"
FROZEN_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "cross_system_frozen_slices_v1_n100"


def _dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_manifest_for_benchmark(benchmark_slug: str) -> Path:
    return FROZEN_ROOT / benchmark_slug / "cross_system_manifest.json"


def build_bundle(pack_manifest_path: Path) -> dict[str, str]:
    pack_manifest = _load_json(pack_manifest_path)
    pack_dir = pack_manifest_path.parent
    benchmark_slug = str(pack_manifest["benchmark_slug"]).strip()
    source_manifest_path = _source_manifest_for_benchmark(benchmark_slug)
    source_manifest = _load_json(source_manifest_path)
    source_task_inputs_path = Path(str(pack_manifest["source_task_inputs"]))
    source_task_inputs = _load_json(source_task_inputs_path)
    source_task_map = {row["task_id"]: row for row in source_task_inputs["tasks"]}

    task_ids = [str(row["task_id"]) for row in pack_manifest["tasks"]]
    canonical_tasks = []
    for task_id in task_ids:
        row = dict(source_task_map[task_id])
        row["input_text"] = canonicalize_task_input_text(task_id=task_id, input_text=str(row["input_text"]))
        canonical_tasks.append(row)

    bb_task_inputs = {
        "schema": "breadboard.aristotle_task_inputs.v1",
        "benchmark": source_task_inputs.get("benchmark"),
        "n_tasks": len(task_ids),
        "seed": source_task_inputs.get("seed"),
        "slice_method": f"hilbert_pack_subset::{pack_manifest['pack_name']}",
        "tasks": canonical_tasks,
    }

    source_bb_config_ref = ""
    for system in source_manifest.get("systems", []):
        if str(system.get("system_id")) == "bb_atp":
            source_bb_config_ref = str(system.get("config_ref") or "")
            break

    bundle_manifest = {
        "acceptance": source_manifest["acceptance"],
        "artifacts": {
            "redact_secrets": True,
            "root_dir": str((pack_dir / "cross_system").resolve()),
            "store_logs": True,
            "store_proofs": True,
        },
        "benchmark": {
            "name": source_manifest["benchmark"]["name"],
            "slice": {
                "method": f"hilbert_pack_subset::{pack_manifest['pack_name']}",
                "n_tasks": len(task_ids),
                "seed": source_manifest["benchmark"]["slice"]["seed"],
                "task_ids": task_ids,
            },
            "version": source_manifest["benchmark"]["version"],
        },
        "budget": source_manifest["budget"],
        "created_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner": source_manifest.get("owner", "atp-team"),
        "purpose": "hilbert_comparison",
        "run_id": f"hilbert-compare-{pack_manifest['pack_name']}",
        "systems": [
            {
                "system_id": "bb_atp",
                "config_ref": source_bb_config_ref or "agent_configs/codex_cli_gpt51mini_e4_live.yaml",
            },
            {
                "system_id": "hilbert",
                "config_ref": "other_harness_refs/ml-hilbert",
                "notes": "openai_compatible_end_to_end",
            },
        ],
        "toolchain": source_manifest["toolchain"],
    }

    bb_task_inputs_path = pack_dir / "bb_task_inputs.json"
    bundle_manifest_path = pack_dir / "cross_system_manifest.json"
    _dump_json(bb_task_inputs_path, bb_task_inputs)
    _dump_json(bundle_manifest_path, bundle_manifest)
    return {
        "pack_manifest_path": str(pack_manifest_path),
        "bb_task_inputs_path": str(bb_task_inputs_path),
        "cross_system_manifest_path": str(bundle_manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pack-manifest",
        action="append",
        default=[],
        help="Path to a hilbert comparison pack manifest.json. If omitted, build for all packs.",
    )
    args = parser.parse_args()

    manifest_paths = [Path(item).resolve() for item in args.pack_manifest if str(item).strip()]
    if not manifest_paths:
        manifest_paths = sorted(ARTIFACT_ROOT.glob("*/manifest.json"))

    summary = {
        "schema": "breadboard.hilbert_bb_comparison_bundle_summary.v1",
        "generated": [],
    }
    for path in manifest_paths:
        summary["generated"].append(build_bundle(path))

    summary_path = ARTIFACT_ROOT / "bb_comparison_bundle_summary.json"
    _dump_json(summary_path, summary)
    print(summary_path)


if __name__ == "__main__":
    main()
