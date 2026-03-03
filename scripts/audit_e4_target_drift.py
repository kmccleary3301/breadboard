#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml


def _git_ls_remote_head(repo_url: str) -> str:
    proc = subprocess.run(
        ["git", "ls-remote", repo_url, "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(stderr or f"git ls-remote failed for {repo_url}")
    line = proc.stdout.strip().splitlines()
    if not line:
        raise RuntimeError(f"empty ls-remote output for {repo_url}")
    return line[0].split()[0]


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be a mapping")
    e4_configs = payload.get("e4_configs")
    if not isinstance(e4_configs, dict):
        raise ValueError("manifest.e4_configs must be a mapping")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit E4 target-freeze manifest drift against upstream HEAD.")
    parser.add_argument("--manifest", default="config/e4_target_freeze_manifest.yaml")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--fail-on-drift", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = (repo_root / args.manifest).resolve()
    payload = _load_manifest(manifest_path)
    e4_configs = payload["e4_configs"]

    repo_cache: dict[str, str] = {}
    report: dict[str, Any] = {
        "manifest_path": str(manifest_path),
        "drifted": [],
        "aligned": [],
        "errors": [],
    }

    for key, entry in sorted(e4_configs.items()):
        if not isinstance(entry, dict):
            report["errors"].append({"key": key, "reason": "entry_not_mapping"})
            continue
        harness = entry.get("harness")
        if not isinstance(harness, dict):
            report["errors"].append({"key": key, "reason": "missing_harness"})
            continue

        repo_url = harness.get("upstream_repo")
        pinned_commit = harness.get("upstream_commit")
        if not isinstance(repo_url, str) or not repo_url:
            report["errors"].append({"key": key, "reason": "missing_upstream_repo"})
            continue
        if not isinstance(pinned_commit, str) or not pinned_commit:
            report["errors"].append({"key": key, "reason": "missing_upstream_commit"})
            continue

        try:
            remote_head = repo_cache.get(repo_url)
            if remote_head is None:
                remote_head = _git_ls_remote_head(repo_url)
                repo_cache[repo_url] = remote_head
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"key": key, "repo": repo_url, "reason": str(exc)})
            continue

        row = {
            "key": key,
            "repo": repo_url,
            "pinned_commit": pinned_commit,
            "remote_head": remote_head,
        }
        if pinned_commit != remote_head:
            report["drifted"].append(row)
        else:
            report["aligned"].append(row)

    report["drift_count"] = len(report["drifted"])
    report["aligned_count"] = len(report["aligned"])
    report["error_count"] = len(report["errors"])

    text = json.dumps(report, indent=2)
    print(text)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")

    if report["error_count"] > 0:
        return 2
    if args.fail_on_drift and report["drift_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

