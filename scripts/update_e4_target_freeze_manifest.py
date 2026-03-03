#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {stderr}")
    return proc.stdout.strip()


def _resolve_family_repo(
    repo_root: Path,
    harness_root: Path,
    family: str,
    claude_repo: Path | None,
) -> Path | None:
    if family == "codex_cli":
        candidates = [harness_root / "codex"]
    elif family == "opencode":
        candidates = [harness_root / "opencode"]
    elif family == "claude_code":
        candidates = []
        if claude_repo is not None:
            candidates.append(claude_repo)
        candidates.extend(
            [
                harness_root / "sdk_competitors" / "claude_code",
                harness_root / "claude-code",
                harness_root / "claude_code",
            ]
        )
    else:
        return None

    for candidate in candidates:
        p = candidate.resolve()
        if (p / ".git").exists():
            return p
    return None


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be a mapping")
    e4_configs = payload.get("e4_configs")
    if not isinstance(e4_configs, dict):
        raise ValueError("manifest.e4_configs must be a mapping")
    return payload


def _manifest_text(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh E4 target-freeze manifest harness commit/date from local harness clones."
    )
    parser.add_argument("--manifest", default="config/e4_target_freeze_manifest.yaml")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--harness-root", default=None, help="Defaults to <repo-root>/../other_harness_refs")
    parser.add_argument(
        "--claude-repo",
        default=None,
        help="Optional explicit path to claude-code clone for claude_code family entries.",
    )
    parser.add_argument("--write", action="store_true", help="Write updated manifest in place.")
    parser.add_argument("--check", action="store_true", help="Exit 1 when updates would be applied.")
    parser.add_argument("--diff-out", default=None, help="Optional path to write unified diff.")
    parser.add_argument("--json-out", default=None, help="Optional path to write JSON report.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = (repo_root / args.manifest).resolve()
    harness_root = (
        Path(args.harness_root).resolve()
        if args.harness_root
        else (repo_root.parent / "other_harness_refs").resolve()
    )
    claude_repo = Path(args.claude_repo).resolve() if args.claude_repo else None

    payload = _load_manifest(manifest_path)
    before_text = manifest_path.read_text(encoding="utf-8")
    updated = yaml.safe_load(before_text)
    assert isinstance(updated, dict)
    e4_configs = updated.get("e4_configs", {})
    assert isinstance(e4_configs, dict)

    report: dict[str, Any] = {
        "manifest_path": str(manifest_path),
        "harness_root": str(harness_root),
        "changes": [],
        "skipped": [],
        "errors": [],
    }

    cache: dict[Path, tuple[str, str]] = {}
    for key, entry in sorted(e4_configs.items()):
        if not isinstance(entry, dict):
            report["errors"].append({"key": key, "reason": "entry_not_mapping"})
            continue
        harness = entry.get("harness")
        if not isinstance(harness, dict):
            report["errors"].append({"key": key, "reason": "missing_harness"})
            continue
        family = harness.get("family")
        if not isinstance(family, str) or not family:
            report["errors"].append({"key": key, "reason": "missing_family"})
            continue

        family_repo = _resolve_family_repo(repo_root, harness_root, family, claude_repo)
        if family_repo is None:
            report["skipped"].append({"key": key, "family": family, "reason": "missing_local_clone"})
            continue

        if family_repo not in cache:
            try:
                commit = _run_git(family_repo, "rev-parse", "HEAD")
                commit_date = _run_git(family_repo, "show", "-s", "--format=%cI", "HEAD")
                cache[family_repo] = (commit, commit_date)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(
                    {"key": key, "family": family, "repo": str(family_repo), "reason": str(exc)}
                )
                continue

        new_commit, new_date = cache[family_repo]
        old_commit = harness.get("upstream_commit")
        old_date = harness.get("upstream_commit_date")
        if old_commit != new_commit or old_date != new_date:
            harness["upstream_commit"] = new_commit
            harness["upstream_commit_date"] = new_date
            report["changes"].append(
                {
                    "key": key,
                    "family": family,
                    "repo": str(family_repo),
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "old_commit_date": old_date,
                    "new_commit_date": new_date,
                }
            )

    if report["changes"]:
        updated["manifest_updated_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )

    diff_lines: list[str] = []
    after_text = before_text
    if report["changes"]:
        after_text = _manifest_text(updated)
        diff_lines = list(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=str(manifest_path),
                tofile=str(manifest_path),
            )
        )

    report["change_count"] = len(report["changes"])
    report["skipped_count"] = len(report["skipped"])
    report["error_count"] = len(report["errors"])
    report["would_change"] = bool(report["changes"])

    if args.diff_out:
        diff_out = Path(args.diff_out)
        diff_out.parent.mkdir(parents=True, exist_ok=True)
        diff_out.write_text("".join(diff_lines), encoding="utf-8")

    if args.write and diff_lines:
        manifest_path.write_text(after_text, encoding="utf-8")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    if args.check and report["would_change"]:
        return 1
    if report["error_count"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
