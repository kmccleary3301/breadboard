#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List


PATTERNS = (
    "atp_state_ref_fastpath_*_warmup.json",
    "atp_state_ref_fastpath_*_measured.json",
    "atp_benchmark_drift_report*.json",
    "compat_surface_summary*.json",
)


def _collect(root: Path, patterns: Iterable[str]) -> List[Path]:
    files: List[Path] = []
    for pattern in patterns:
        files.extend(root.glob(pattern))
    return sorted((p for p in files if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply retention policy to ATP artifact files.")
    parser.add_argument("--root", default="artifacts", help="Artifact directory.")
    parser.add_argument("--keep", type=int, default=40, help="Number of most-recent files to keep.")
    parser.add_argument("--apply", action="store_true", help="Apply deletions. Default is dry-run.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    keep = max(0, int(args.keep))
    files = _collect(root, PATTERNS)
    stale = files[keep:]

    print(f"artifact_root: {root}")
    print(f"tracked_files: {len(files)}")
    print(f"keep: {keep}")
    print(f"stale_candidates: {len(stale)}")
    for path in stale:
        print(f"stale: {path}")

    if args.apply:
        for path in stale:
            path.unlink(missing_ok=True)
        print(f"deleted: {len(stale)}")
    else:
        print("dry_run: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
