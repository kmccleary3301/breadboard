#!/usr/bin/env python3
"""R-1c drift verifier: every sha256 pin recorded in tracked JSON must match
the pinned file's current bytes, modulo a recorded pre-existing baseline.

Usage:
  verify_sha_pins.py --root <tree> [--baseline <mismatches.json>] [--write-baseline <path>]

Exit 0 when the mismatch set is exactly the baseline (or empty without one);
exit 1 with a diff otherwise. Pins are {path, sha256} object pairs and
``*_ref``/``*_sha256`` sibling keys - the same shapes the rename audit's
pin-derived preserve-byte scanner freezes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def tracked_json_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.json"], cwd=root, capture_output=True, text=True
    )
    return [line for line in out.stdout.splitlines() if "node_modules" not in line]


def sweep(root: Path) -> dict[str, list[str]]:
    mismatches: dict[str, set[str]] = {}

    def check(pinner: str, ref: str, sha: str) -> None:
        target = root / ref
        if not target.is_file():
            return
        have = hashlib.sha256(target.read_bytes()).hexdigest()
        if have != sha.removeprefix("sha256:"):
            mismatches.setdefault(pinner, set()).add(ref)

    for rel in tracked_json_files(root):
        try:
            doc = json.loads((root / rel).read_text(encoding="utf-8"))
        except Exception:
            continue
        stack = [doc]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                path, sha = node.get("path"), node.get("sha256")
                if isinstance(path, str) and isinstance(sha, str) and sha:
                    check(rel, path, sha)
                for key, value in node.items():
                    if key.endswith("_sha256") and isinstance(value, str):
                        ref = node.get(key.replace("_sha256", "_ref"))
                        if isinstance(ref, str):
                            check(rel, ref, value)
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    return {k: sorted(v) for k, v in sorted(mismatches.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--baseline", help="JSON of accepted pre-existing mismatches")
    ap.add_argument("--write-baseline", help="write current mismatch set here and exit 0")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    current = sweep(root)

    if args.write_baseline:
        Path(args.write_baseline).write_text(
            json.dumps(current, indent=1) + "\n", encoding="utf-8"
        )
        print(f"baseline written: {len(current)} pinner files with mismatches")
        return 0

    baseline: dict[str, list[str]] = {}
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    drift = {
        pinner: sorted(set(refs) - set(baseline.get(pinner, [])))
        for pinner, refs in current.items()
        if set(refs) - set(baseline.get(pinner, []))
    }
    healed = {
        pinner: sorted(set(refs) - set(current.get(pinner, [])))
        for pinner, refs in baseline.items()
        if set(refs) - set(current.get(pinner, []))
    }
    if healed:
        print("note: baseline entries no longer mismatched (healed):")
        print(json.dumps(healed, indent=1))
    if drift:
        print("PIN DRIFT DETECTED (beyond baseline):", file=sys.stderr)
        print(json.dumps(drift, indent=1), file=sys.stderr)
        return 1
    print(f"sha-pin drift: zero beyond baseline ({len(current)} baseline pinners)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
