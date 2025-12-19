#!/usr/bin/env python3
"""
Promote a captured run directory into a golden snapshot.

Copies the workspace and parity metadata into goldens/<scenario>/<provider>/current/,
computes checksums, and writes/updates README + GOLDEN_VERSION.
"""

from __future__ import annotations

import argparse
import json
import shutil
import textwrap
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]

META_FILES: List[str] = [
    "run_summary.json",
    "guard_histogram.json",
    "tool_usage.json",
    "workspace.manifest.json",
    "provider_metrics.json",
    "reward_metrics.json",
    "env_fingerprint.json",
]


def copy_meta(run_dir: Path, dest_meta: Path) -> None:
    source_meta = run_dir / "meta"
    dest_meta.mkdir(parents=True, exist_ok=True)
    for filename in META_FILES:
        src = source_meta / filename
        if not src.exists():
            continue
        shutil.copy2(src, dest_meta / filename)


def copy_workspace(run_dir: Path, dest_workspace: Path) -> None:
    workspace_root = run_dir / "final_container_dir"
    if not workspace_root.exists():
        raise FileNotFoundError(f"missing final_container_dir in {run_dir}")
    if dest_workspace.exists():
        shutil.rmtree(dest_workspace)
    shutil.copytree(workspace_root, dest_workspace)


def write_version_file(golden_dir: Path, version: int) -> None:
    (golden_dir / "GOLDEN_VERSION").write_text(f"{version}\n", encoding="utf-8")


def write_checksums(current_dir: Path, output_path: Path) -> None:
    import hashlib

    entries: List[str] = []
    for path in sorted(current_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(current_dir)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        entries.append(f"{digest}  {rel}")
    output_path.write_text("\n".join(entries) + "\n", encoding="utf-8")


def write_readme(golden_dir: Path, scenario: str, provider: str, run_dir: Path, config: str, task: str) -> None:
    readme = golden_dir / "README.md"
    summary_path = run_dir / "meta" / "run_summary.json"
    completion = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            completion = summary.get("completion_summary") or {}
        except Exception:
            completion = {}
    lines = [
        f"# {scenario} — {provider}",
        "",
        "## Capture Metadata",
        "",
        f"- Source run: `{run_dir}`",
        f"- Config: `{config}`",
        f"- Task: `{task}`",
        f"- Completion summary: `{completion}`",
        "",
        "## Structure",
        "",
        "- `current/workspace/` – final workspace snapshot (`final_container_dir`).",
        "- `current/meta/` – parity metadata (`run_summary.json`, manifests, histograms, tool usage, metrics).",
        "",
        "## Notes",
        "",
        "Update this file with scenario-specific validation steps if needed.",
    ]
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bless a run directory into a golden snapshot.")
    parser.add_argument("--run-dir", required=True, help="Path to the run directory under logging/")
    parser.add_argument("--golden-dir", required=True, help="Destination under goldens/<scenario>/<provider>")
    parser.add_argument("--scenario", required=True, help="Scenario name (for README)")
    parser.add_argument("--provider", required=True, help="Provider/profile name (for README)")
    parser.add_argument("--config", required=True, help="Config path used for the run")
    parser.add_argument("--task", required=True, help="Task path used for the run")
    parser.add_argument("--version", type=int, default=1, help="Golden version to record (default: 1)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    golden_dir = Path(args.golden_dir).resolve()
    current_dir = golden_dir / "current"
    meta_dir = current_dir / "meta"
    workspace_dir = current_dir / "workspace"

    if not run_dir.exists():
        raise FileNotFoundError(run_dir)
    current_dir.mkdir(parents=True, exist_ok=True)

    copy_meta(run_dir, meta_dir)
    copy_workspace(run_dir, workspace_dir)
    write_version_file(golden_dir, args.version)
    write_readme(golden_dir, args.scenario, args.provider, run_dir, args.config, args.task)
    write_checksums(current_dir, golden_dir / "CHECKSUMS")

    print(textwrap.dedent(
        f"""
        [bless-golden] Completed.
          source: {run_dir}
          dest:   {current_dir}
        """
    ).strip())


if __name__ == "__main__":
    main()
