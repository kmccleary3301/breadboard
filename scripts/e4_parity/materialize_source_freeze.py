#!/usr/bin/env python3
"""Safely materialize the tracked Oh-My-Pi source freeze for legacy lane adapters."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from scripts.e4_parity.compile_lane_lock import extract_source_archive
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from compile_lane_lock import extract_source_archive

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = ROOT / "config/e4_lanes/source_freezes/oh_my_pi_main_5356713e_git_tracked.zip"
DEFAULT_WORKSPACE = Path(os.environ.get("BB_WORKSPACE_ROOT", ROOT.parent))
DEFAULT_OUTPUT = DEFAULT_WORKSPACE / "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safely extract the tracked Oh-My-Pi source freeze into its legacy logical path."
    )
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    tree = extract_source_archive(args.archive, args.output)
    payload = {
        "schema_version": "bb.e4.source_freeze_materialization.v1",
        "archive": str(args.archive),
        "output": str(args.output),
        "sha256": tree.digest,
        "bytes": tree.bytes,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"materialized {args.output} sha256={tree.digest} bytes={tree.bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
