#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


REQUIRED_TOP_KEYS = {"schema_version", "manifest_updated_utc", "e4_configs"}
REQUIRED_HARNESS_KEYS = {
    "family",
    "upstream_repo",
    "upstream_commit",
    "upstream_commit_date",
    "upstream_release_label",
    "runtime_surface",
}
REQUIRED_ANCHOR_KEYS = {"class", "scenario_id", "evidence_paths"}
REQUIRED_RUNTIME_SURFACE_KEYS = {"provider_model"}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a mapping")
    return data


def _collect_e4_configs(agent_config_root: Path) -> list[str]:
    return sorted(p.name for p in agent_config_root.glob("*e4*.yaml"))


def _validate_manifest(
    manifest_path: Path,
    repo_root: Path,
    strict_evidence: bool,
) -> dict[str, Any]:
    issues: list[str] = []
    manifest = _load_yaml(manifest_path)

    missing_top = sorted(REQUIRED_TOP_KEYS - set(manifest))
    if missing_top:
        issues.append(f"missing top-level keys: {missing_top}")

    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.startswith("e4_target_freeze_manifest_v1"):
        issues.append("schema_version must start with 'e4_target_freeze_manifest_v1'")

    e4_configs = manifest.get("e4_configs")
    if not isinstance(e4_configs, dict):
        issues.append("e4_configs must be a mapping")
        e4_configs = {}

    expected_config_files = _collect_e4_configs(repo_root / "agent_configs")
    expected_stems = {Path(name).stem for name in expected_config_files}
    declared_stems = set(e4_configs.keys())
    if expected_stems != declared_stems:
        missing = sorted(expected_stems - declared_stems)
        extra = sorted(declared_stems - expected_stems)
        if missing:
            issues.append(f"manifest missing E4 config entries for: {missing}")
        if extra:
            issues.append(f"manifest has extra entries not present in agent_configs/*e4*.yaml: {extra}")

    for stem, entry in sorted(e4_configs.items()):
        if not isinstance(entry, dict):
            issues.append(f"{stem}: entry must be a mapping")
            continue
        config_path = entry.get("config_path")
        if not isinstance(config_path, str) or not config_path:
            issues.append(f"{stem}: config_path missing or invalid")
        else:
            expected_name = f"agent_configs/{stem}.yaml"
            if config_path != expected_name:
                issues.append(f"{stem}: config_path must be '{expected_name}', got '{config_path}'")
            resolved = repo_root / config_path
            if not resolved.exists():
                issues.append(f"{stem}: config_path does not exist: {config_path}")

        harness = entry.get("harness")
        if not isinstance(harness, dict):
            issues.append(f"{stem}: harness block missing or invalid")
            continue
        missing_harness = sorted(REQUIRED_HARNESS_KEYS - set(harness))
        if missing_harness:
            issues.append(f"{stem}: harness missing keys: {missing_harness}")

        runtime_surface = harness.get("runtime_surface")
        if not isinstance(runtime_surface, dict):
            issues.append(f"{stem}: harness.runtime_surface missing or invalid")
        else:
            missing_surface = sorted(REQUIRED_RUNTIME_SURFACE_KEYS - set(runtime_surface))
            if missing_surface:
                issues.append(f"{stem}: harness.runtime_surface missing keys: {missing_surface}")

        anchor = entry.get("calibration_anchor")
        if not isinstance(anchor, dict):
            issues.append(f"{stem}: calibration_anchor block missing or invalid")
            continue
        missing_anchor = sorted(REQUIRED_ANCHOR_KEYS - set(anchor))
        if missing_anchor:
            issues.append(f"{stem}: calibration_anchor missing keys: {missing_anchor}")

        evidence_paths = anchor.get("evidence_paths")
        if not isinstance(evidence_paths, list) or not evidence_paths:
            issues.append(f"{stem}: calibration_anchor.evidence_paths must be a non-empty list")
        else:
            for rel in evidence_paths:
                if not isinstance(rel, str) or not rel:
                    issues.append(f"{stem}: invalid evidence path entry: {rel!r}")
                    continue
                if strict_evidence:
                    p = repo_root / rel
                    if not p.exists():
                        issues.append(f"{stem}: missing evidence path: {rel}")

    return {
        "manifest_path": str(manifest_path),
        "expected_e4_config_count": len(expected_config_files),
        "declared_e4_config_count": len(e4_configs),
        "issues": issues,
        "ok": not issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate E4 target freeze manifest coverage and shape.")
    parser.add_argument(
        "--manifest",
        default="config/e4_target_freeze_manifest.yaml",
        help="Path to E4 target freeze manifest.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path.",
    )
    parser.add_argument(
        "--strict-evidence",
        action="store_true",
        help="Fail if calibration evidence files are missing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    manifest_path = (repo_root / args.manifest).resolve()
    report = _validate_manifest(manifest_path=manifest_path, repo_root=repo_root, strict_evidence=args.strict_evidence)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "ok" if report["ok"] else "failed"
        print(f"[e4-target-manifest] {status}: {report['declared_e4_config_count']}/{report['expected_e4_config_count']} entries")
        for issue in report["issues"]:
            print(f"- {issue}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
