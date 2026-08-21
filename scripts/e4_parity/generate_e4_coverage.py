#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
COVERAGE_DIR = WORKSPACE / "docs_tmp" / "phase_16" / "coverage"
SUPPORT_CLAIMS_DIR = ROOT / "docs" / "conformance" / "support_claims"
REGISTRY_ID = "target_families"
REGISTRY_PATH = ROOT / "contracts" / "kernel" / "registries" / "target_families.v1.json"
GENERATED_AT = "2026-07-04T00:00:00Z"
NOT_STARTED_FALLBACK_FAMILIES = frozenset({"claude_code", "opencode", "oh_my_opencode"})
VALID_UNCLAIMED_STATUSES = frozenset({"not_started", "out_of_scope", "blocked"})
BEHAVIOR_FAMILIES = (
    "config_graph",
    "context_pack",
    "capability_surface",
    "tool_execution",
    "command_network_policy",
    "protocol_sessions",
    "resource_access",
    "provider_routing",
    "memory_compaction",
    "work_items",
    "side_effects",
    "projection_ui",
    "session_persistence",
    "replay_capture",
    "other",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE))
    except ValueError:
        return str(path)


def _load_target_families_registry() -> Mapping[str, Any]:
    try:
        from scripts.validators.registries import load_registry
    except (ImportError, ModuleNotFoundError):
        payload = load_json(REGISTRY_PATH)
    else:
        payload = load_registry(REGISTRY_ID)
    if not isinstance(payload, Mapping):
        raise TypeError(f"{REGISTRY_ID} registry must be a JSON object")
    return payload


def _registry_entry_id(entry: Mapping[str, Any]) -> str | None:
    for key in ("value", "id", "identifier", "target_family", "family_id", "name"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _registry_entries(registry: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    for key in ("entries", "items", "values", "target_families", "families"):
        raw_entries = registry.get(key)
        if isinstance(raw_entries, list):
            entries = tuple(entry for entry in raw_entries if isinstance(entry, Mapping) and _registry_entry_id(entry))
            if entries:
                return entries
        if isinstance(raw_entries, Mapping):
            entries = []
            for value, entry in raw_entries.items():
                if isinstance(entry, Mapping):
                    entry_with_id = dict(entry)
                    entry_with_id.setdefault("value", str(value))
                    entries.append(entry_with_id)
            if entries:
                return tuple(entries)
    return ()


def _entry_metadata(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = entry.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _entry_is_coverage_enabled(entry: Mapping[str, Any]) -> bool:
    metadata = _entry_metadata(entry)
    status = str(entry.get("status") or metadata.get("status") or "").lower()
    if status in {"deprecated", "removed", "retired"}:
        return False
    if bool(entry.get("deprecated")) or bool(metadata.get("deprecated")):
        return False
    enabled = entry.get("coverage_enabled", metadata.get("coverage_enabled", True))
    return enabled is not False


def _unclaimed_status(family: str, entry: Mapping[str, Any]) -> str:
    metadata = _entry_metadata(entry)
    for key in ("coverage_unclaimed_status", "coverage_default_status", "target_coverage_status"):
        value = entry.get(key, metadata.get(key))
        if isinstance(value, str) and value in VALID_UNCLAIMED_STATUSES:
            return value
    coverage_class = entry.get("coverage_class", metadata.get("coverage_class"))
    if coverage_class in {"planned", "tracked", "candidate"}:
        return "not_started"
    if family in NOT_STARTED_FALLBACK_FAMILIES:
        return "not_started"
    return "out_of_scope"


def _target_family_entries() -> tuple[tuple[str, Mapping[str, Any]], ...]:
    seen: set[str] = set()
    entries: list[tuple[str, Mapping[str, Any]]] = []
    for entry in _registry_entries(_load_target_families_registry()):
        family = _registry_entry_id(entry)
        if family is None or family in seen or not _entry_is_coverage_enabled(entry):
            continue
        seen.add(family)
        entries.append((family, entry))
    if not entries:
        raise ValueError(f"{REGISTRY_ID} registry has no coverage-enabled target families")
    return tuple(entries)


def _claims() -> list[tuple[Path, Mapping[str, Any]]]:
    claims: list[tuple[Path, Mapping[str, Any]]] = []
    for path in sorted(SUPPORT_CLAIMS_DIR.glob("*_support_claim.json")):
        payload = load_json(path)
        if isinstance(payload, Mapping) and payload.get("schema_version") == "bb.e4.support_claim.v2" and payload.get("accepted") is True:
            claims.append((path, payload))
    return claims


def _behavior_family(behavior_id: str) -> str:
    for family in BEHAVIOR_FAMILIES:
        if behavior_id == family or behavior_id.startswith(f"{family}_"):
            return family
    return "other"


def generate() -> dict[str, Any]:
    target_family_entries = _target_family_entries()
    target_families = tuple(family for family, _ in target_family_entries)
    claims_by_family: dict[str, list[tuple[Path, Mapping[str, Any]]]] = {family: [] for family in target_families}
    errors: list[str] = []
    for path, claim in _claims():
        family = str(claim.get("target_family", ""))
        if family in claims_by_family:
            claims_by_family[family].append((path, claim))
        else:
            errors.append(f"{display(path)}: unregistered target_family {family!r}")

    files: list[str] = []
    for family, entry in target_family_entries:
        rows: list[dict[str, Any]] = []
        represented: set[str] = set()
        versions = sorted({str(claim.get("target_version")) for _, claim in claims_by_family[family] if claim.get("target_version")})
        for claim_path, claim in claims_by_family[family]:
            claim_ref = display(claim_path)
            semantics = claim.get("claim_semantics") if isinstance(claim.get("claim_semantics"), Mapping) else {}
            for behavior in semantics.get("asserted_behaviors", []):
                if not isinstance(behavior, Mapping):
                    continue
                behavior_id = str(behavior.get("behavior_id", ""))
                if not behavior_id:
                    continue
                behavior_family = _behavior_family(behavior_id)
                represented.add(behavior_family)
                rows.append(
                    {
                        "behavior_family": behavior_family,
                        "behavior_id": behavior_id,
                        "description": str(behavior.get("description") or behavior_id.replace("_", " ")),
                        "status": "c4_accepted",
                        "claim_refs": [claim_ref],
                    }
                )
        unclaimed_status = _unclaimed_status(family, entry)
        for behavior_family in BEHAVIOR_FAMILIES:
            if behavior_family in represented:
                continue
            rows.append(
                {
                    "behavior_family": behavior_family,
                    "behavior_id": f"{family}_{behavior_family}_unclaimed",
                    "description": f"No accepted v2 C4 claim currently covers {behavior_family} for {family}.",
                    "status": unclaimed_status,
                    "claim_refs": [],
                }
            )
        rows.sort(key=lambda row: (row["behavior_family"], row["behavior_id"]))
        payload = {
            "schema_version": "bb.e4.target_coverage.v2",
            "target_family": family,
            "frozen_target_versions": versions,
            "generated_at_utc": GENERATED_AT,
            "rows": rows,
        }
        path = COVERAGE_DIR / f"{family}_target_coverage.json"
        write_json(path, payload)
        files.append(display(path))
    return {
        "schema_version": "bb.e4.coverage_generation_report.v1",
        "generated_at_utc": GENERATED_AT,
        "ok": not errors,
        "files": files,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate target coverage matrices from accepted support_claim.v2 records.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = generate()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"generated {len(report['files'])} coverage matrices")
        for error in report["errors"]:
            print(error)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
