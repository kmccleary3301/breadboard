#!/usr/bin/env python3
"""Provision the manifest-verified immutable E4 input bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from scripts.e4_parity.immutable_inputs import ImmutableInputError, provision_immutable_inputs
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from immutable_inputs import ImmutableInputError, provision_immutable_inputs

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = ROOT / "config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.zip"
DEFAULT_MANIFEST = ROOT / "config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = provision_immutable_inputs(
            args.archive,
            args.manifest,
            repo_root=args.repo_root,
            workspace_root=args.workspace_root,
        )
    except ImmutableInputError as exc:
        print(f"immutable input provisioning failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "archive_sha256": result.archive_sha256,
                "bytes": result.bytes,
                "existing": list(result.existing),
                "member_count": result.member_count,
                "ok": True,
                "written": list(result.written),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
