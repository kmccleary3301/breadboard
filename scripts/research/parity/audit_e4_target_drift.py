#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml


_LOCAL_REPOSITORY_URLS = {"local://breadboard"}


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
    lines = proc.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"empty ls-remote output for {repo_url}")
    head = lines[0].split()[0]
    if not head:
        raise RuntimeError(f"invalid ls-remote output for {repo_url}")
    return head


def _git_local_head(repo_root: Path) -> str:
    resolved_root = repo_root.resolve()
    top_level = subprocess.run(
        ["git", "-C", str(resolved_root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if top_level.returncode != 0:
        detail = (top_level.stderr or top_level.stdout).strip()
        raise RuntimeError(detail or f"{resolved_root} is not a Git checkout")
    observed_root = Path(top_level.stdout.strip()).resolve()
    if observed_root != resolved_root:
        raise RuntimeError(
            f"--repo-root is not the verified checkout root: {resolved_root} != {observed_root}"
        )

    head = subprocess.run(
        ["git", "-C", str(resolved_root), "rev-parse", "--verify", "HEAD^{commit}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if head.returncode != 0:
        detail = (head.stderr or head.stdout).strip()
        raise RuntimeError(detail or f"unable to resolve HEAD for {resolved_root}")
    commit = head.stdout.strip()
    if not commit:
        raise RuntimeError(f"empty HEAD identity for {resolved_root}")
    return commit


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be a mapping")
    e4_configs = payload.get("e4_configs")
    if not isinstance(e4_configs, dict):
        raise ValueError("manifest.e4_configs must be a mapping")
    return payload


def _manifest_repositories(e4_configs: dict[str, Any]) -> set[str]:
    repositories: set[str] = set()
    for entry in e4_configs.values():
        if not isinstance(entry, dict):
            continue
        harness = entry.get("harness")
        if not isinstance(harness, dict):
            continue
        repo_url = harness.get("upstream_repo")
        if isinstance(repo_url, str) and repo_url.strip():
            repositories.add(repo_url)
    return repositories


def _load_snapshot(
    path: Path,
    *,
    required_repositories: set[str] | None = None,
) -> tuple[dict[str, str], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"snapshot JSON is malformed: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("snapshot root must be an object")

    snapshot_id = payload.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("snapshot.snapshot_id must be a non-empty string")
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("snapshot.entries must be an object")
    if not entries:
        raise ValueError("snapshot.entries must not be empty")

    heads: dict[str, str] = {}
    for name, row in entries.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("snapshot entry names must be non-empty strings")
        if not isinstance(row, dict):
            raise ValueError(f"snapshot entry {name!r} must be an object")
        repo_url = row.get("repo_url")
        commit = row.get("commit")
        if not isinstance(repo_url, str) or not repo_url.strip():
            raise ValueError(f"snapshot entry {name!r} has no repository URL")
        if not isinstance(commit, str) or not commit.strip():
            raise ValueError(f"snapshot entry {name!r} has no commit")
        previous = heads.get(repo_url)
        if previous is not None and previous != commit:
            raise ValueError(f"snapshot has conflicting commits for {repo_url}")
        heads[repo_url] = commit

    if required_repositories is not None:
        missing = sorted(required_repositories - heads.keys())
        unexpected = sorted(heads.keys() - required_repositories)
        if missing:
            raise ValueError(f"snapshot is incomplete; missing repositories: {', '.join(missing)}")
        if unexpected:
            raise ValueError(
                f"snapshot contains repositories absent from the manifest: {', '.join(unexpected)}"
            )
    return heads, snapshot_id


def _load_snapshot_heads(
    path: Path,
    *,
    required_repositories: set[str] | None = None,
) -> dict[str, str]:
    heads, _snapshot_id = _load_snapshot(
        path,
        required_repositories=required_repositories,
    )
    return heads


def _build_report(
    *,
    e4_configs: dict[str, Any],
    snapshot_heads: dict[str, str] | None = None,
    remote_head_lookup: Callable[[str], str] = _git_ls_remote_head,
    repo_root: Path | None = None,
    local_head_lookup: Callable[[Path], str] = _git_local_head,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    snapshot_mode = snapshot_heads is not None
    snapshot_heads = snapshot_heads or {}
    repo_cache: dict[str, str] = {}
    local_cache: dict[str, str] = {}
    report: dict[str, Any] = {
        "comparison_source": "snapshot_json" if snapshot_mode else "live_remote_head",
        "snapshot_id": snapshot_id if snapshot_mode else None,
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
        if not isinstance(repo_url, str) or not repo_url.strip():
            report["errors"].append({"key": key, "reason": "missing_upstream_repo"})
            continue
        if not isinstance(pinned_commit, str) or not pinned_commit.strip():
            report["errors"].append({"key": key, "reason": "missing_upstream_commit"})
            continue

        is_local = repo_url.startswith("local://")
        if is_local and repo_url not in _LOCAL_REPOSITORY_URLS:
            report["errors"].append(
                {"key": key, "repo": repo_url, "reason": "unknown_local_namespace"}
            )
            continue

        source_mode: str
        observed_head: str
        try:
            if snapshot_mode:
                observed_head = snapshot_heads.get(repo_url, "")
                if not observed_head:
                    raise RuntimeError("snapshot_missing_repository")
                source_mode = "snapshot_json"
            elif is_local:
                if repo_root is None:
                    raise RuntimeError("unbound_local_namespace")
                cache_key = str(repo_root.resolve())
                observed_head = local_cache.get(cache_key, "")
                if not observed_head:
                    observed_head = local_head_lookup(repo_root)
                    local_cache[cache_key] = observed_head
                source_mode = "local_git"
            else:
                observed_head = repo_cache.get(repo_url, "")
                if not observed_head:
                    observed_head = remote_head_lookup(repo_url)
                    repo_cache[repo_url] = observed_head
                source_mode = "remote_git"
            if not isinstance(observed_head, str) or not observed_head.strip():
                raise RuntimeError(f"empty observed commit for {repo_url}")
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(
                {
                    "key": key,
                    "repo": repo_url,
                    "source_mode": (
                        "snapshot_json"
                        if snapshot_mode
                        else "local_git"
                        if is_local
                        else "remote_git"
                    ),
                    "reason": str(exc),
                }
            )
            continue

        row = {
            "key": key,
            "repo": repo_url,
            "pinned_commit": pinned_commit,
            "observed_commit": observed_head,
            "current_head": observed_head if not snapshot_mode else None,
            "local_head": observed_head if source_mode == "local_git" else None,
            "remote_head": observed_head if source_mode == "remote_git" else None,
            "snapshot_head": observed_head if source_mode == "snapshot_json" else None,
            "source_mode": source_mode,
        }
        if pinned_commit != observed_head:
            report["drifted"].append(row)
        else:
            report["aligned"].append(row)

    report["drift_count"] = len(report["drifted"])
    report["aligned_count"] = len(report["aligned"])
    report["error_count"] = len(report["errors"])
    return report


def _failure_report(
    *,
    comparison_source: str,
    manifest_path: Path,
    snapshot_path: Path | None,
    reason: str,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "comparison_source": comparison_source,
        "manifest_path": str(manifest_path),
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "drifted": [],
        "aligned": [],
        "errors": [{"reason": reason}],
        "drift_count": 0,
        "aligned_count": 0,
        "error_count": 1,
    }
    return report


def _resolve_path(value: str, repo_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit E4 target-freeze manifest drift against upstream HEAD.")
    parser.add_argument("--manifest", default="config/e4_target_freeze_manifest.yaml")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--fail-on-drift", action="store_true")
    parser.add_argument(
        "--snapshot-json",
        default=None,
        help="closed ref snapshot JSON; all manifest repository identities must be present",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = _resolve_path(args.manifest, repo_root)
    snapshot_path = _resolve_path(args.snapshot_json, repo_root) if args.snapshot_json is not None else None
    comparison_source = "snapshot_json" if snapshot_path is not None else "live_remote_head"

    try:
        payload = _load_manifest(manifest_path)
        e4_configs = payload["e4_configs"]
        snapshot_heads: dict[str, str] | None = None
        snapshot_id: str | None = None
        if snapshot_path is not None:
            snapshot_heads, snapshot_id = _load_snapshot(
                snapshot_path,
                required_repositories=_manifest_repositories(e4_configs),
            )
        report = _build_report(
            e4_configs=e4_configs,
            snapshot_heads=snapshot_heads,
            repo_root=repo_root,
            snapshot_id=snapshot_id,
        )
        report["manifest_path"] = str(manifest_path)
        report["snapshot_path"] = str(snapshot_path) if snapshot_path else None
    except Exception as exc:  # noqa: BLE001
        report = _failure_report(
            comparison_source=comparison_source,
            manifest_path=manifest_path,
            snapshot_path=snapshot_path,
            reason=str(exc),
        )

    text = json.dumps(report, indent=2)
    print(text)

    if args.json_out:
        out = _resolve_path(args.json_out, repo_root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")

    if report["error_count"] > 0:
        return 2
    if args.fail_on_drift and report["drift_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
