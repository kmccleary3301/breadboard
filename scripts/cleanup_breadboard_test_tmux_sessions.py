#!/usr/bin/env python3
"""
Delete stale breadboard_test_* tmux sessions older than a threshold.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean stale breadboard_test_* tmux sessions.")
    parser.add_argument("--prefix", default="breadboard_test_", help="session prefix to match")
    parser.add_argument("--older-than-minutes", type=float, default=180.0, help="age threshold in minutes")
    parser.add_argument("--dry-run", action="store_true", help="list sessions without deleting")
    parser.add_argument(
        "--protected-sessions",
        default="bb_tui_codex_dev,bb_engine_codex_dev,bb_atp",
        help="comma-separated session names never deleted",
    )
    args = parser.parse_args()

    protected = {token.strip() for token in args.protected_sessions.split(",") if token.strip()}
    now = time.time()
    threshold_seconds = max(0.0, args.older_than_minutes * 60.0)

    proc = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_created}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)

    candidates: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name = parts[0].strip()
        if not name.startswith(args.prefix):
            continue
        if name in protected:
            continue
        try:
            created = float(parts[1].strip())
        except ValueError:
            continue
        age_seconds = now - created
        if age_seconds >= threshold_seconds:
            candidates.append(
                {
                    "session": name,
                    "age_minutes": round(age_seconds / 60.0, 2),
                }
            )

    deleted: list[str] = []
    if not args.dry_run:
        for row in candidates:
            name = str(row["session"])
            subprocess.run(["tmux", "kill-session", "-t", name], check=False)
            deleted.append(name)

    print(
        json.dumps(
            {
                "prefix": args.prefix,
                "older_than_minutes": args.older_than_minutes,
                "dry_run": args.dry_run,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "deleted_count": len(deleted),
                "deleted": deleted,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
