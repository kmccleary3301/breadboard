#!/usr/bin/env python3
"""
Bless a tmux capture run into the canonical golden pointer.

For tmux captures we intentionally avoid copying large frame trees. Instead,
we keep `goldens/<provider>/<scenario>/current` as a symlink to a specific run
directory under `scenarios/.../<run_id>/<timestamp>`.

This matches how we treat other "current -> run" pointers and makes it cheap
to bless/roll back without duplicating artifacts.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bless tmux capture run as golden via symlink.")
    parser.add_argument("--run-dir", required=True, help="Path to a tmux run directory (contains scenario_manifest.json).")
    parser.add_argument("--provider", required=True, choices=("codex", "claude", "unknown"), help="Provider name.")
    parser.add_argument("--scenario", required=True, help="Scenario id (e.g. e2e_compact_semantic_v1).")
    parser.add_argument(
        "--sce-root",
        default="/shared_folders/querylake_server/ray_testing/ray_SCE",
        help="SCE root used to locate docs_tmp (default: ray_SCE).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sce_root = Path(args.sce_root).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve()

    manifest = run_dir / "scenario_manifest.json"
    if not manifest.exists():
        raise SystemExit(f"run dir missing scenario_manifest.json: {run_dir}")

    # Keep goldens outside the git repo under SCE docs_tmp.
    gold_base = (sce_root / "docs_tmp" / "tmux_captures" / "goldens" / args.provider / args.scenario).resolve()
    gold_base.mkdir(parents=True, exist_ok=True)

    current = gold_base / "current"
    if current.exists() and not current.is_symlink():
        # docs_tmp is disposable, but we still avoid accidentally deleting a real directory.
        raise SystemExit(f"refusing to replace non-symlink current path: {current}")
    if current.is_symlink() or current.exists():
        current.unlink()

    # Use absolute symlink so later runs from other working directories resolve correctly.
    current.symlink_to(run_dir)

    print(str(current))
    print(f"-> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

