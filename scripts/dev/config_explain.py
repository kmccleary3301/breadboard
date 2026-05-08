#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


ARTIFACT_TASK_PRESET = {
    "preset_id": "artifact_single_response_materialize_v1",
    "description": "Offline single-artifact task contract for response materialization and external evaluator hooks.",
    "completion_semantics": "success requires required artifact checks and required evaluators to pass",
    "provider_required": False,
    "default_contract": {
        "mode": "response_materialize",
        "completion_policy": "all_required",
        "requirements": [{"path": "candidate.py", "required": True, "min_bytes": 1}],
    },
    "default_materialization": {
        "strategy": "fenced_block",
        "language": "python",
        "output_path": "candidate.py",
        "require_single_block": True,
        "reject_empty": True,
        "overwrite": False,
    },
    "evidence_bundle": [
        "manifest.json",
        "inputs/task.md",
        "responses/raw_response.md",
        "artifacts/artifact_manifest.json",
        "hashes/sha256_manifest.json",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Explain BreadBoard operator-facing config surfaces.")
    parser.add_argument("--preset", default="artifact_single")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.preset not in {"artifact_single", "artifact_single_response_materialize_v1"}:
        print(f"Unknown preset: {args.preset}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(ARTIFACT_TASK_PRESET, indent=2, sort_keys=True))
    else:
        print(f"preset: {ARTIFACT_TASK_PRESET['preset_id']}")
        print(f"description: {ARTIFACT_TASK_PRESET['description']}")
        print(f"completion: {ARTIFACT_TASK_PRESET['completion_semantics']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
