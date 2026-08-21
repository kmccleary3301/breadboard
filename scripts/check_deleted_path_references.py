#!/usr/bin/env python3
"""Reject tracked live references to paths removed during a clean cutover.

A reference may be classified as immutable historical evidence only when the
referencing file is under a designated evidence root, is named by an explicitly
supplied manifest, and still matches the manifest's SHA-256 digest. Everything
else is a live reference and blocks deletion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

IMMUTABLE_SNAPSHOT_ROOT = Path("docs/conformance/evidence_snapshots")
IMMUTABLE_EXTERNAL_RECORD_MANIFESTS = {
    "docs_tmp/phase_20/evidence/SP6/implementer.json":
        "config/deletion_audit/immutable_historical_records.v1.json",
}


def _normalize_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    digest = value.removeprefix("sha256:")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
        return None
    return digest.lower()


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _manifest_entries(repo_root: Path, manifest_path: Path) -> dict[str, str]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: dict[str, str] = {}

    for value in _walk(payload):
        if not isinstance(value, dict):
            continue
        raw_path = value.get("path")
        digest = _normalize_digest(value.get("sha256"))
        if isinstance(raw_path, str) and digest is not None:
            entries[raw_path] = digest

        raw_files = value.get("files")
        if not isinstance(raw_files, dict):
            continue
        for raw_name, metadata in raw_files.items():
            if not isinstance(raw_name, str):
                continue
            raw_digest = metadata.get("sha256") if isinstance(metadata, dict) else metadata
            digest = _normalize_digest(raw_digest)
            if digest is not None:
                entries[(manifest_path.parent / raw_name).relative_to(repo_root).as_posix()] = digest

    return entries


def _tracked_paths(repo_root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--cached"],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "git ls-files failed")
    return sorted(path for path in proc.stdout.decode("utf-8").split("\0") if path)


def _reference_tokens(deleted_path: str) -> list[str]:
    normalized = Path(deleted_path).as_posix().removeprefix("./")
    without_suffix = normalized.removesuffix(".py")
    tokens = {normalized, without_suffix}
    if normalized.endswith(".py"):
        tokens.add(without_suffix.replace("/", "."))
        tokens.add(Path(without_suffix).name)
    return sorted((token for token in tokens if token), key=lambda token: (-len(token), token))


def _authorized_snapshots(
    repo_root: Path,
    tracked_paths: set[str],
    manifest_values: Iterable[str],
) -> tuple[set[str], list[str]]:
    authorized: set[str] = set()
    errors: list[str] = []
    immutable_snapshot_root = (repo_root / IMMUTABLE_SNAPSHOT_ROOT).resolve()

    for manifest_value in manifest_values:
        manifest_path = (repo_root / manifest_value).resolve()
        try:
            manifest_rel = manifest_path.relative_to(repo_root).as_posix()
        except ValueError:
            errors.append(f"snapshot manifest is outside repository: {manifest_value}")
            continue
        if manifest_rel not in tracked_paths:
            errors.append(f"snapshot manifest is not tracked: {manifest_rel}")
            continue
        try:
            entries = _manifest_entries(repo_root, manifest_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"cannot read snapshot manifest {manifest_rel}: {exc}")
            continue

        for raw_path, expected_digest in entries.items():
            candidate = (repo_root / raw_path).resolve()
            try:
                candidate_rel = candidate.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            is_snapshot = candidate.is_relative_to(immutable_snapshot_root)
            external_manifest = IMMUTABLE_EXTERNAL_RECORD_MANIFESTS.get(candidate_rel)
            if not is_snapshot and external_manifest != manifest_rel:
                continue
            if candidate_rel not in tracked_paths or not candidate.is_file():
                continue
            actual_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                errors.append(
                    f"immutable snapshot digest mismatch: {candidate_rel} "
                    f"(expected {expected_digest}, got {actual_digest})"
                )
                continue
            authorized.add(candidate_rel)

    return authorized, sorted(errors)


def build_report(repo_root: Path, deleted_paths: Iterable[str], manifest_values: Iterable[str]) -> dict[str, Any]:
    tracked_paths = _tracked_paths(repo_root)
    tracked_set = set(tracked_paths)
    authorized, manifest_errors = _authorized_snapshots(repo_root, tracked_set, manifest_values)
    references: list[dict[str, Any]] = []

    tokens_by_target = {target: _reference_tokens(target) for target in deleted_paths}
    for rel_path in tracked_paths:
        path = repo_root / rel_path
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for target, tokens in tokens_by_target.items():
            matched_tokens = [token for token in tokens if token in text]
            if not matched_tokens:
                continue
            first_token = matched_tokens[0]
            offset = text.index(first_token)
            references.append(
                {
                    "deleted_path": target,
                    "path": rel_path,
                    "line": text.count("\n", 0, offset) + 1,
                    "matched_tokens": matched_tokens,
                    "classification": "immutable_historical" if rel_path in authorized else "live",
                }
            )

    live_references = [reference for reference in references if reference["classification"] == "live"]
    historical_references = [
        reference for reference in references if reference["classification"] == "immutable_historical"
    ]
    return {
        "ok": not live_references and not manifest_errors,
        "deleted_paths": list(tokens_by_target),
        "tracked_file_count": len(tracked_paths),
        "authorized_snapshot_count": len(authorized),
        "live_reference_count": len(live_references),
        "immutable_historical_reference_count": len(historical_references),
        "live_references": live_references,
        "immutable_historical_references": historical_references,
        "manifest_errors": manifest_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Git worktree root to audit")
    parser.add_argument(
        "--deleted-path",
        action="append",
        required=True,
        dest="deleted_paths",
        help="deleted repository-relative path; may be repeated",
    )
    parser.add_argument(
        "--immutable-snapshot-manifest",
        action="append",
        default=[],
        dest="snapshot_manifests",
        help="tracked digest manifest authorizing immutable evidence snapshots; may be repeated",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        report = build_report(repo_root, args.deleted_paths, args.snapshot_manifests)
    except RuntimeError as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "deleted-path reference audit: "
            f"{report['live_reference_count']} live, "
            f"{report['immutable_historical_reference_count']} immutable historical"
        )
        for reference in report["live_references"]:
            print(
                f"LIVE {reference['path']}:{reference['line']} -> "
                f"{reference['deleted_path']}"
            )
        for error in report["manifest_errors"]:
            print(f"MANIFEST ERROR {error}")

    if report["manifest_errors"]:
        return 2
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
