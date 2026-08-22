#!/usr/bin/env python3
"""Enforce Kernel Danger-Zone ACR prerequisites for PR changed files.

Exit codes:
- 0: pass
- 2: danger-zone policy failure or malformed manifest
- 3: invalid input/runtime error
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "contracts" / "policies" / "kernel_danger_zone_manifest_v1.json"
EXPECTED_SCHEMA = "breadboard.kernel_danger_zone_manifest.v1"
ACR_PATTERN = "docs/contracts/policies/acr/ACR-*.md"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return payload


def _normalize_path(raw_path: str) -> str:
    path = raw_path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _read_changed_files(path: Path) -> list[str]:
    files: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        normalized = _normalize_path(line)
        if normalized:
            files.append(normalized)
    return files


def _matches_glob(path: str, pattern: str) -> bool:
    normalized_pattern = _normalize_path(pattern)
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, normalized_pattern)


def _find_matching_pattern(path: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if _matches_glob(path, pattern):
            return pattern
    return None


def _extract_classification(acr_text: str, allowed_values: set[str]) -> str | None:
    match = re.search(r"(?im)^\s*-\s*Classification\s*:\s*`?([a-z0-9_-]+)`?", acr_text)
    if not match:
        return None
    value = match.group(1).strip().lower()
    aliases = {"behavioral-change": "breaking"}
    value = aliases.get(value, value)
    if value not in allowed_values:
        return None
    return value


def _acr_sections(acr_text: str) -> dict[str, bool]:
    lowered = acr_text.lower()
    return {
        "acr_id": bool(re.search(r"(?im)^\s*-\s*`?acr_id`?\s*:\s*`?ACR-", acr_text)),
        "implemented_or_approved_status": bool(
            re.search(r"(?im)^\s*-\s*`?status`?\s*:\s*`?(approved|implemented)\b", acr_text)
        ),
        "danger_zone_yes": "danger-zone: yes" in lowered
        or "kernel danger-zone` change? `yes" in lowered
        or "kernel danger-zone change? yes" in lowered,
        "generalization_impact": "coupling and generalization impact" in lowered
        or "generalization risk assessment" in lowered,
        "evidence_plan": "evidence and validation plan" in lowered or "required evidence" in lowered,
        "rollback_plan": "rollback plan" in lowered or "rollback path" in lowered,
        "approvals": "approvals" in lowered or "decision" in lowered,
    }


def _validate_changed_acrs(
    *,
    changed_files: list[str],
    repo_root: Path,
    allowed_classifications: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    acr_paths = [path for path in changed_files if _matches_glob(path, ACR_PATTERN)]
    acr_results: list[dict[str, Any]] = []
    errors: list[str] = []
    if not acr_paths:
        errors.append(f"missing changed ACR decision artifact matching {ACR_PATTERN}")
        return acr_results, errors

    for rel_path in acr_paths:
        full_path = repo_root / rel_path
        result: dict[str, Any] = {
            "path": rel_path,
            "exists": full_path.is_file(),
            "classification": None,
            "checks": {},
            "ok": False,
        }
        if not full_path.is_file():
            errors.append(f"changed ACR artifact is not present in the worktree: {rel_path}")
            acr_results.append(result)
            continue

        text = full_path.read_text(encoding="utf-8")
        checks = _acr_sections(text)
        classification = _extract_classification(text, allowed_classifications)
        result["classification"] = classification
        result["checks"] = checks
        missing = [name for name, ok in checks.items() if not ok]
        if classification is None:
            missing.append("valid_classification")
        result["ok"] = not missing
        result["missing"] = missing
        if missing:
            errors.append(f"{rel_path}: incomplete danger-zone ACR fields: {', '.join(missing)}")
        acr_results.append(result)
    return acr_results, errors


def evaluate_danger_zone(
    *,
    manifest_path: Path,
    changed_files_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    manifest = _load_json(manifest_path)

    if manifest.get("schema") != EXPECTED_SCHEMA:
        errors.append(f"schema mismatch: expected {EXPECTED_SCHEMA!r}, got {manifest.get('schema')!r}")
    if manifest.get("version") != 1:
        errors.append("version must be 1")

    raw_patterns = manifest.get("protected_path_globs")
    if not isinstance(raw_patterns, list) or not raw_patterns:
        errors.append("protected_path_globs must be a non-empty array")
        patterns: list[str] = []
    else:
        patterns = [str(pattern) for pattern in raw_patterns]

    raw_required = manifest.get("required_artifacts")
    required_artifacts = raw_required if isinstance(raw_required, dict) else {}
    if not required_artifacts:
        errors.append("required_artifacts must be a non-empty object")

    required_artifact_results: list[dict[str, Any]] = []
    for key in sorted(required_artifacts):
        value = required_artifacts[key]
        if not isinstance(value, str) or not value:
            errors.append(f"required_artifacts.{key} must be a non-empty repo-relative path")
            continue
        rel_path = _normalize_path(value)
        exists = (repo_root / rel_path).is_file()
        required_artifact_results.append({"key": key, "path": rel_path, "exists": exists})
        if not exists:
            errors.append(f"missing required artifact template: {rel_path}")

    raw_classifications = manifest.get("change_classification_values")
    if not isinstance(raw_classifications, list) or not raw_classifications:
        errors.append("change_classification_values must be a non-empty array")
        allowed_classifications: set[str] = set()
    else:
        allowed_classifications = {str(value).lower() for value in raw_classifications}

    changed_files = _read_changed_files(changed_files_path)
    danger_zone_changes: list[dict[str, str]] = []
    for path in changed_files:
        matched_pattern = _find_matching_pattern(path, patterns)
        if matched_pattern is not None:
            danger_zone_changes.append({"path": path, "matched_pattern": matched_pattern})

    acr_results: list[dict[str, Any]] = []
    if danger_zone_changes:
        acr_results, acr_errors = _validate_changed_acrs(
            changed_files=changed_files,
            repo_root=repo_root,
            allowed_classifications=allowed_classifications,
        )
        errors.extend(acr_errors)

    return {
        "ok": not errors,
        "schema": EXPECTED_SCHEMA,
        "manifest_path": str(manifest_path),
        "changed_files_path": str(changed_files_path),
        "repo_root": str(repo_root),
        "changed_files_count": len(changed_files),
        "danger_zone_change_count": len(danger_zone_changes),
        "danger_zone_changes": danger_zone_changes,
        "required_artifacts": required_artifact_results,
        "acr_artifacts": acr_results,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce Kernel Danger-Zone ACR prerequisites.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to danger-zone manifest JSON.")
    parser.add_argument("--changed-files-file", required=True, help="Newline-delimited PR changed files.")
    parser.add_argument("--repo-root", default=str(ROOT), help="Repository root for artifact checks.")
    parser.add_argument("--json-out", default="", help="Optional path to write full JSON report.")
    parser.add_argument("--output-json", default="", help="Alias for --json-out.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        changed_files_path = Path(args.changed_files_file).expanduser().resolve()
        repo_root = Path(args.repo_root).expanduser().resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"manifest not found: {manifest_path}")
        if not changed_files_path.is_file():
            raise FileNotFoundError(f"changed files file not found: {changed_files_path}")
        if not repo_root.is_dir():
            raise FileNotFoundError(f"repo root not found: {repo_root}")

        result = evaluate_danger_zone(
            manifest_path=manifest_path,
            changed_files_path=changed_files_path,
            repo_root=repo_root,
        )
        out_path = args.json_out or args.output_json
        if out_path:
            target = Path(out_path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(
                json.dumps(
                    {
                        "ok": result["ok"],
                        "changed_files_count": result["changed_files_count"],
                        "danger_zone_change_count": result["danger_zone_change_count"],
                        "errors": result["errors"],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0 if result["ok"] else 2
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
