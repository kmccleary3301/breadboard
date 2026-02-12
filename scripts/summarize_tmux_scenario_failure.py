#!/usr/bin/env python3
"""
Emit a concise failure summary from scenario_manifest.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize tmux scenario failure from manifest.")
    parser.add_argument("--manifest", required=True, help="path to scenario_manifest.json")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenario_result = str(payload.get("scenario_result", "unknown"))
    semantic_failures = payload.get("semantic_failures", []) or []

    summary = {
        "manifest": str(manifest_path),
        "scenario": payload.get("scenario"),
        "run_id": payload.get("run_id"),
        "target": payload.get("target"),
        "scenario_result": scenario_result,
        "poller_exit_code": payload.get("poller_exit_code"),
        "action_error": payload.get("action_error"),
        "execution_error": payload.get("execution_error"),
        "provider_error": payload.get("provider_error"),
        "final_idle_error": payload.get("final_idle_error"),
        "frame_stall_error": payload.get("frame_stall_error"),
        "semantic_failures_count": len(semantic_failures),
        "semantic_failure_first": semantic_failures[0] if semantic_failures else None,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
