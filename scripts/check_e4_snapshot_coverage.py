#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a mapping")
    return data


def _read_snapshot_header(path: Path) -> dict[str, str]:
    header: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:6]
    except OSError:
        return header
    for line in lines:
        if line.startswith("# source_manifest_entry:"):
            header["source_manifest_entry"] = line.split(":", 1)[1].strip()
        elif line.startswith("# snapshot_tag:"):
            header["snapshot_tag"] = line.split(":", 1)[1].strip()
    return header


def _validate_snapshot_coverage(*, repo_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _load_yaml(manifest_path)
    e4_configs = manifest.get("e4_configs")
    if not isinstance(e4_configs, dict):
        raise ValueError("manifest.e4_configs must be a mapping")

    issues: list[str] = []
    coverage: dict[str, list[str]] = {}
    base_entries: dict[str, dict[str, Any]] = {}
    snapshot_entries: dict[str, dict[str, Any]] = {}

    for stem, entry in sorted(e4_configs.items()):
        if not isinstance(entry, dict):
            issues.append(f"{stem}: manifest entry must be a mapping")
            continue
        if "__" in stem:
            snapshot_entries[stem] = entry
        else:
            base_entries[stem] = entry
            coverage[stem] = []

    for snapshot_stem, entry in sorted(snapshot_entries.items()):
        source = entry.get("snapshot_source_entry")
        tag = entry.get("snapshot_tag")
        config_path = entry.get("config_path")
        if not isinstance(source, str) or not source:
            issues.append(f"{snapshot_stem}: missing snapshot_source_entry")
            continue
        if source not in base_entries:
            issues.append(f"{snapshot_stem}: snapshot_source_entry points to missing base row '{source}'")
            continue
        coverage.setdefault(source, []).append(snapshot_stem)

        if not isinstance(tag, str) or not tag:
            issues.append(f"{snapshot_stem}: missing snapshot_tag")

        if not isinstance(config_path, str) or not config_path:
            issues.append(f"{snapshot_stem}: missing config_path")
            continue
        config_abs = (repo_root / config_path).resolve()
        if not config_abs.exists():
            issues.append(f"{snapshot_stem}: config file missing at {config_path}")
            continue
        header = _read_snapshot_header(config_abs)
        header_source = header.get("source_manifest_entry")
        header_tag = header.get("snapshot_tag")
        if header_source != source:
            issues.append(
                f"{snapshot_stem}: snapshot file header source_manifest_entry mismatch "
                f"(expected '{source}', got '{header_source or '<missing>'}')"
            )
        if isinstance(tag, str) and tag and header_tag != tag:
            issues.append(
                f"{snapshot_stem}: snapshot file header snapshot_tag mismatch "
                f"(expected '{tag}', got '{header_tag or '<missing>'}')"
            )

    missing_snapshot_rows = sorted(stem for stem, rows in coverage.items() if not rows)
    for stem in missing_snapshot_rows:
        issues.append(f"{stem}: no versioned snapshot rows found")

    return {
        "ok": not issues,
        "manifest_path": str(manifest_path),
        "base_row_count": len(base_entries),
        "snapshot_row_count": len(snapshot_entries),
        "missing_snapshot_row_count": len(missing_snapshot_rows),
        "missing_snapshot_rows": missing_snapshot_rows,
        "coverage": {k: sorted(v) for k, v in sorted(coverage.items())},
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that each base E4 manifest row has at least one versioned snapshot row."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument(
        "--manifest",
        default="config/e4_target_freeze_manifest.yaml",
        help="Path to E4 target freeze manifest.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = (repo_root / args.manifest).resolve()
    report = _validate_snapshot_coverage(repo_root=repo_root, manifest_path=manifest_path)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "ok" if report["ok"] else "failed"
        print(
            f"[e4-snapshot-coverage] {status}: "
            f"{report['base_row_count']} base rows, {report['snapshot_row_count']} snapshot rows"
        )
        for issue in report["issues"]:
            print(f"- {issue}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
