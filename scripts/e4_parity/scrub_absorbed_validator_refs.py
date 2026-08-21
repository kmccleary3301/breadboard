#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
PHASE_ROOT = WORKSPACE / "docs_tmp" / "phase_15"
EXEC_ROOT = PHASE_ROOT / "pro_requests" / "e4_breakthrough_20260629" / "execution"
TERMINAL_REPORT_JSON_PATH = PHASE_ROOT / "oh_my_pi_p6" / "BB_E4_OH_MY_PI_P6_TERMINAL_REPORT.json"
ACCEPTED_HASH_MANIFEST_PATH = EXEC_ROOT / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_HASH_MANIFEST.json"

ABSORBED_VALIDATOR_PATHS = (
    "scripts/e4_parity/validate_e4_primitive_readiness.py",
    "scripts/e4_parity/validate_e4_score_subledger.py",
)
ABSORBED_VALIDATOR_PATH_MARKERS = tuple(
    marker
    for path in ABSORBED_VALIDATOR_PATHS
    for marker in (path, f"{ROOT.name}/{path}")
)

_ARTIFACT_LIST_KEYS = ("artifacts", "current_artifacts", "input_artifacts", "artifact_inventory")
_COMMAND_LIST_KEYS = ("validation_commands", "validation_runs", "verification_runs")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_path(path: Path) -> str:
    return _hash_utils.sha256_path(path)


def resolve_ref(raw_ref: str) -> Path:
    raw_path = raw_ref.split("#", 1)[0]
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()
    if raw_path.startswith("docs_tmp/") or raw_path.startswith(f"{ROOT.name}/"):
        return (WORKSPACE / raw_path).resolve()
    return (ROOT / raw_path).resolve()


def contains_absorbed_validator(value: str) -> bool:
    raw = value.split("#", 1)[0]
    return any(marker in raw for marker in ABSORBED_VALIDATOR_PATH_MARKERS)


def prune_absorbed_refs(payload: dict[str, Any]) -> bool:
    changed = False
    for key in _ARTIFACT_LIST_KEYS:
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        kept = [
            item
            for item in values
            if not (
                isinstance(item, Mapping)
                and isinstance(item.get("path"), str)
                and contains_absorbed_validator(item["path"])
            )
        ]
        if len(kept) != len(values):
            payload[key] = kept
            changed = True

    for key in _COMMAND_LIST_KEYS:
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        kept = [
            item
            for item in values
            if not (
                isinstance(item, Mapping)
                and isinstance(item.get("command"), str)
                and contains_absorbed_validator(item["command"])
            )
        ]
        if len(kept) != len(values):
            payload[key] = kept
            changed = True

    refs = payload.get("refs")
    if isinstance(refs, dict):
        for key, value in list(refs.items()):
            if isinstance(value, str) and contains_absorbed_validator(value):
                refs.pop(key, None)
                changed = True
    return changed


def refresh_artifact_entries(payload: dict[str, Any], *, only_paths: set[str]) -> bool:
    changed = False
    for key in _ARTIFACT_LIST_KEYS:
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            raw_path = item["path"].split("#", 1)[0]
            if raw_path not in only_paths:
                continue
            path = resolve_ref(item["path"])
            exists = path.exists()
            if item.get("exists") != exists:
                item["exists"] = exists
                changed = True
            if not exists:
                continue
            digest = sha256_path(path)
            size = path.stat().st_size
            if item.get("sha256") != digest:
                item["sha256"] = digest
                changed = True
            if item.get("bytes") != size:
                item["bytes"] = size
                changed = True
    return changed


def scrub(path: Path, *, refresh_artifact_paths: set[str] | None = None) -> bool:
    if not path.exists():
        return False
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    changed = prune_absorbed_refs(payload)
    if refresh_artifact_paths:
        changed = refresh_artifact_entries(payload, only_paths=refresh_artifact_paths) or changed
    if changed:
        write_json(path, payload)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub deleted B3 validator wrapper refs from auxiliary E4 reports.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    changed = {
        str(TERMINAL_REPORT_JSON_PATH.relative_to(WORKSPACE)): scrub(TERMINAL_REPORT_JSON_PATH),
        str(ACCEPTED_HASH_MANIFEST_PATH.relative_to(WORKSPACE)): scrub(ACCEPTED_HASH_MANIFEST_PATH, refresh_artifact_paths={str(TERMINAL_REPORT_JSON_PATH.relative_to(WORKSPACE))}),
    }
    report = {"ok": True, "changed": changed}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for path, did_change in changed.items():
            print(f"{path}: {'updated' if did_change else 'unchanged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
