#!/usr/bin/env python3
"""
Validate a tmux capture run directory produced by run_tmux_capture_scenario.py.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def frame_integrity(run_dir: Path) -> dict[str, object]:
    frames_dir = run_dir / "frames"
    result: dict[str, object] = {
        "frames_dir_exists": frames_dir.exists(),
        "frame_count": 0,
        "missing_frame_indices": [],
        "missing_artifacts": [],
    }
    if not frames_dir.exists():
        return result

    frame_re = re.compile(r"^frame_(\d{4})\.(txt|ansi|png)$")
    by_idx: dict[int, set[str]] = {}
    for file in frames_dir.iterdir():
        if not file.is_file():
            continue
        match = frame_re.match(file.name)
        if not match:
            continue
        idx = int(match.group(1))
        ext = match.group(2)
        by_idx.setdefault(idx, set()).add(ext)

    if not by_idx:
        return result
    indices = sorted(by_idx.keys())
    result["frame_count"] = len(indices)
    required = {"txt", "ansi", "png"}
    missing_artifacts = []
    for idx in indices:
        missing = sorted(required - by_idx[idx])
        if missing:
            missing_artifacts.append({"index": idx, "missing": missing})
    result["missing_artifacts"] = missing_artifacts
    min_idx = indices[0]
    max_idx = indices[-1]
    present = set(indices)
    result["missing_frame_indices"] = [idx for idx in range(min_idx, max_idx + 1) if idx not in present]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate tmux capture run output.")
    parser.add_argument("--run-dir", required=True, help="path to run directory containing scenario_manifest.json")
    parser.add_argument("--strict", action="store_true", help="fail on semantic warnings in addition to hard errors")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest_path = run_dir / "scenario_manifest.json"
    summary_path = run_dir / "run_summary.json"
    output: dict[str, object] = {
        "run_dir": str(run_dir),
        "valid": True,
        "errors": [],
        "warnings": [],
    }

    if not run_dir.exists():
        output["valid"] = False
        output["errors"].append("run directory does not exist")
        print(json.dumps(output, indent=2))
        raise SystemExit(2)

    if not manifest_path.exists():
        output["valid"] = False
        output["errors"].append("scenario_manifest.json missing")
        print(json.dumps(output, indent=2))
        raise SystemExit(2)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output["scenario_result"] = manifest.get("scenario_result")
    output["poller_exit_code"] = manifest.get("poller_exit_code")
    output["action_error"] = manifest.get("action_error")

    required_manifest_fields = [
        "scenario",
        "run_id",
        "target",
        "scenario_result",
        "poller_exit_code",
        "executed_actions",
        "provider_analysis",
        "frame_validation",
    ]
    for field in required_manifest_fields:
        if field not in manifest:
            output["valid"] = False
            output["errors"].append(f"manifest missing required field: {field}")

    integrity = frame_integrity(run_dir)
    output["frame_integrity"] = integrity
    if not integrity["frames_dir_exists"]:
        output["valid"] = False
        output["errors"].append("frames directory missing")
    if integrity["missing_frame_indices"]:
        output["valid"] = False
        output["errors"].append(f"missing frame indices: {integrity['missing_frame_indices']}")
    if integrity["missing_artifacts"]:
        output["valid"] = False
        output["errors"].append(f"missing frame artifacts: {integrity['missing_artifacts']}")

    if not summary_path.exists():
        output["warnings"].append("run_summary.json missing")
    else:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        output["summary_result"] = summary.get("scenario_result")
        if summary.get("scenario_result") != manifest.get("scenario_result"):
            output["warnings"].append("run_summary scenario_result differs from manifest")

    semantic_failures = manifest.get("semantic_failures", [])
    if semantic_failures:
        output["warnings"].append(f"semantic failures present: {len(semantic_failures)}")
        if args.strict:
            output["valid"] = False
            output["errors"].append("strict mode enabled and semantic failures are present")

    if manifest.get("poller_exit_code") != 0:
        output["valid"] = False
        output["errors"].append(f"poller_exit_code={manifest.get('poller_exit_code')}")

    print(json.dumps(output, indent=2))
    if output["valid"]:
        raise SystemExit(0)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
