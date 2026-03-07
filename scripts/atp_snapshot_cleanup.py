#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import List


def _list_snapshots(root: Path, prefix: str) -> List[Path]:
    if not root.exists():
        return []
    candidates = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _is_snapshot_dir(path: Path) -> bool:
    return (path / "lean.snap").exists() and (path / "lean.mem").exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean up old ATP Lean REPL snapshots.")
    parser.add_argument("--snapshots-root", default="/tmp", help="Root directory containing snapshots.")
    parser.add_argument("--prefix", default="fc_lean_snap_ready-", help="Snapshot directory prefix.")
    parser.add_argument("--keep", type=int, default=3, help="Number of newest snapshots to keep.")
    parser.add_argument("--apply", action="store_true", help="Delete snapshots (default is dry-run).")
    args = parser.parse_args()

    root = Path(args.snapshots_root).resolve()
    snapshots = [p for p in _list_snapshots(root, args.prefix) if _is_snapshot_dir(p)]
    keep = max(0, args.keep)
    to_delete = snapshots[keep:]

    print(f"[cleanup] root={root} keep={keep} apply={args.apply}")
    print(f"[cleanup] found={len(snapshots)} delete={len(to_delete)}")

    for path in to_delete:
        if args.apply:
            try:
                shutil.rmtree(path)
                print(f"[cleanup] deleted {path}")
            except Exception as exc:
                print(f"[cleanup] delete_failed {path} ({exc})")
        else:
            print(f"[cleanup] dry-run {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
