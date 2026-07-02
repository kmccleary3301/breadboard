#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping
import yaml

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
    from scripts.e4_parity.validators.gate_errors import apply_gate_error_envelope, gate_exit_code
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils
    from validators.gate_errors import apply_gate_error_envelope, gate_exit_code


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT.parent
DEFAULT_LEDGER = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
DEFAULT_ACCEPTED_REPORT = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "pro_requests" / "e4_breakthrough_20260629" / "execution" / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "BB_E4_PRIMITIVE_FAMILY_READINESS_REPORT.json"
SCHEMA_VERSION = "bb.e4.primitive_family_readiness_report.v1"
REQUIRED_FIXTURE_ROLES = {"freeze", "capture", "replay", "comparator", "support_claim", "evidence_manifest"}
HASH_REQUIRED_ROLES = {"freeze", "capture", "replay", "comparator"}


def _load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return _hash_utils.sha256_path(path)


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    payload = {"row_id": row_id, "row": row}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + _hash_utils.sha256_hex(encoded)

def _load_yaml(path: Path | str) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _freeze_row_hash(freeze_manifest: Mapping[str, Any], config_id: str) -> str | None:
    configs = freeze_manifest.get("e4_configs")
    if not isinstance(configs, Mapping):
        return None
    row = configs.get(config_id)
    if not isinstance(row, Mapping):
        return None
    return _row_hash(config_id, row)

def _validate_fixture_hash(
    *,
    repo_root: Path,
    role: str,
    ref_path: str,
    digest: str | None,
    config_id: str | None,
    errors: list[str],
) -> None:
    resolved = _resolve(repo_root, ref_path)
    if not resolved.exists():
        errors.append(f"fixture_refs.{role}: missing path: {ref_path}")
        return
    if digest is None:
        errors.append(f"fixture_refs.{role}: missing sha256 hash: {ref_path}")
        return
    if role == "freeze":
        parts = ref_path.split("#")
        if len(parts) != 3 or not parts[1]:
            errors.append(f"fixture_refs.freeze must use path#config_id#sha256:<row_hash>: {ref_path}")
            return
        ref_config_id = parts[1]
        if config_id is not None and ref_config_id != config_id:
            errors.append(f"fixture_refs.freeze config_id mismatch: expected {config_id}, got {ref_config_id}")
            return
        payload = _load_yaml(resolved)
        if not isinstance(payload, Mapping):
            errors.append(f"fixture_refs.freeze payload must be a mapping: {ref_path}")
            return
        actual = _freeze_row_hash(payload, ref_config_id)
        if actual != digest:
            errors.append(f"fixture_refs.freeze row hash mismatch for {ref_config_id}: expected {digest}, got {actual}")
        return
    actual = _sha256(resolved)
    if actual != digest:
        errors.append(f"fixture_refs.{role}: hash mismatch for {ref_path}: expected {digest}, got {actual}")


def _split_role_ref(ref: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(ref, str) or ":" not in ref:
        return None, None, None
    role, rest = ref.split(":", 1)
    if "#" not in rest:
        return role, rest, None
    digest = None
    for part in rest.replace("#", " ").split():
        if part.startswith("sha256:") and len(part) == 71:
            digest = part
            break
    return role, rest, digest


def _strip_ref_hash(ref: str) -> str:
    return ref.split("#", 1)[0]


def _ref_hash(ref: str) -> str | None:
    for part in ref.replace("#", " ").split():
        if part.startswith("sha256:") and len(part) == 71:
            return part
    return None


def _resolve(repo_root: Path, value: str) -> Path:
    raw = _strip_ref_hash(value)
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    workspace = repo_root.parent
    if raw.startswith("docs_tmp/") or raw.startswith(f"{repo_root.name}/"):
        return (workspace / raw).resolve()
    return (repo_root / raw).resolve()


def _display_path(repo_root: Path, path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(repo_root.parent))
    except ValueError:
        return str(path)


def _fixture_role_map(row: Mapping[str, Any]) -> dict[str, list[tuple[str, str | None]]]:
    by_role: dict[str, list[tuple[str, str | None]]] = {}
    fixture_refs = row.get("fixture_refs")
    if not isinstance(fixture_refs, list):
        return by_role
    for ref in fixture_refs:
        role, path, digest = _split_role_ref(ref)
        if role and path:
            by_role.setdefault(role, []).append((path, digest))
    return by_role


def _accepted_claims_by_config(accepted_report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    claim_groups = (
        accepted_report.get("accepted_support_claims"),
        accepted_report.get("accepted_non_target_claims"),
    )
    for claims in claim_groups:
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, Mapping):
                continue
            scope = claim.get("scope")
            if isinstance(scope, Mapping) and isinstance(scope.get("config_id"), str):
                result[str(scope["config_id"])] = claim
    return result


def _validate_ready_row(
    *,
    repo_root: Path,
    row: Mapping[str, Any],
    accepted_claims: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    feature_id = row.get("feature_id")
    if not isinstance(feature_id, str) or not feature_id:
        feature_id = "<missing-feature-id>"
        errors.append("feature_id missing")

    mapping = row.get("breadboard_mapping")
    primitive = mapping.get("primitive") if isinstance(mapping, Mapping) else None
    if not isinstance(primitive, str) or not primitive:
        errors.append("breadboard_mapping.primitive missing")
        primitive = "<missing-primitive>"
    primitive_names = [part.strip() for part in primitive.split("+") if part.strip()]
    if not primitive_names:
        primitive_names = ["<missing-primitive>"]
    schema_refs = []
    for primitive_name in primitive_names:
        schema_path = repo_root / "contracts" / "kernel" / "schemas" / f"{primitive_name}.schema.json"
        if not schema_path.exists():
            errors.append(f"primitive schema missing: contracts/kernel/schemas/{primitive_name}.schema.json")
        else:
            schema_refs.append(f"contracts/kernel/schemas/{primitive_name}.schema.json#{_sha256(schema_path)}")
    schema_ref = schema_refs[0] if len(schema_refs) == 1 else None

    role_map = _fixture_role_map(row)
    missing_roles = sorted(REQUIRED_FIXTURE_ROLES - set(role_map))
    if missing_roles:
        errors.append(f"fixture_refs missing required C4 roles: {', '.join(missing_roles)}")
    config_id = row.get("e4_row_ref")
    row_config_id = config_id if isinstance(config_id, str) else None
    for role in sorted(HASH_REQUIRED_ROLES & set(role_map)):
        for path, digest in role_map[role]:
            _validate_fixture_hash(
                repo_root=repo_root,
                role=role,
                ref_path=path,
                digest=digest,
                config_id=row_config_id,
                errors=errors,
            )

    support_path = role_map.get("support_claim", [(None, None)])[0][0]
    evidence_path = role_map.get("evidence_manifest", [(None, None)])[0][0]
    claim = accepted_claims.get(str(config_id)) if isinstance(config_id, str) else None
    live_ref = claim.get("live_validator_ref") if isinstance(claim, Mapping) else None
    if claim is None:
        errors.append(f"accepted support claim missing for e4_row_ref: {config_id!r}")
    if support_path and isinstance(claim, Mapping) and claim.get("claim_ref") != support_path:
        errors.append("fixture_refs.support_claim does not match accepted claim_ref")
    if evidence_path and isinstance(claim, Mapping) and claim.get("evidence_manifest_ref") != evidence_path:
        errors.append("fixture_refs.evidence_manifest does not match accepted evidence_manifest_ref")
    if not isinstance(live_ref, str) or not live_ref:
        errors.append("accepted claim live_validator_ref missing")
        live_ref = None

    node_gate_ref = None
    if isinstance(live_ref, str):
        node_gate_path = _resolve(repo_root, live_ref)
        expected = _ref_hash(live_ref)
        if not node_gate_path.exists():
            errors.append(f"live_validator_ref missing path: {_strip_ref_hash(live_ref)}")
        else:
            actual = _sha256(node_gate_path)
            if expected != actual:
                errors.append(f"live_validator_ref hash mismatch: expected {expected}, got {actual}")
            node_gate = _load_json(node_gate_path)
            if not isinstance(node_gate, Mapping):
                errors.append("live_validator_ref payload must be an object")
            else:
                if node_gate.get("ok") is not True or node_gate.get("accepted") is not True:
                    errors.append("live_validator_ref must have ok=true and accepted=true")
                if isinstance(config_id, str) and node_gate.get("config_id") != config_id:
                    errors.append("live_validator_ref config_id does not match ledger e4_row_ref")
                hashes = node_gate.get("hashes") if isinstance(node_gate.get("hashes"), Mapping) else {}
                if isinstance(claim, Mapping):
                    if hashes.get("support_claim") != claim.get("claim_sha256"):
                        errors.append("live_validator_ref support_claim hash does not match accepted claim")
                    if hashes.get("evidence_manifest") != claim.get("evidence_manifest_sha256"):
                        errors.append("live_validator_ref evidence_manifest hash does not match accepted claim")
            node_gate_ref = f"{_strip_ref_hash(live_ref)}#{actual}" if 'actual' in locals() else live_ref

    return {
        "feature_id": feature_id,
        "row_sha256": _row_hash(feature_id, row) if isinstance(feature_id, str) and feature_id != "<missing-feature-id>" else None,
        "primitive": primitive,
        "primitive_schema_ref": schema_ref,
        "primitive_schema_refs": schema_refs,
        "e4_row_ref": config_id,
        "support_claim_ref": support_path,
        "evidence_manifest_ref": evidence_path,
        "live_validator_ref": node_gate_ref or live_ref,
        "fixture_roles_present": sorted(role_map),
        "ok": not errors,
        "errors": errors,
    }


def build_primitive_readiness_report(
    *,
    repo_root: Path | str = ROOT,
    ledger_path: Path | str = DEFAULT_LEDGER,
    accepted_report_path: Path | str = DEFAULT_ACCEPTED_REPORT,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    ledger = _load_json(ledger_path)
    accepted = _load_json(accepted_report_path)
    errors: list[str] = []
    if not isinstance(ledger, Mapping):
        return {"schema_version": SCHEMA_VERSION, "ok": False, "errors": ["ledger must be an object"], "rows": []}
    if not isinstance(accepted, Mapping):
        return {"schema_version": SCHEMA_VERSION, "ok": False, "errors": ["accepted report must be an object"], "rows": []}
    rows = ledger.get("rows")
    if not isinstance(rows, list):
        return {"schema_version": SCHEMA_VERSION, "ok": False, "errors": ["ledger.rows must be a list"], "rows": []}
    accepted_by_config = _accepted_claims_by_config(accepted)
    ready_rows = [row for row in rows if isinstance(row, Mapping) and row.get("promotion_state") == "ready" and row.get("evidence_tier") == "C4"]
    row_reports = [
        _validate_ready_row(repo_root=repo, row=row, accepted_claims=accepted_by_config)
        for row in ready_rows
    ]
    for row_report in row_reports:
        for error in row_report["errors"]:
            errors.append(f"{row_report['feature_id']}: {error}")
    report = {
        "schema_version": SCHEMA_VERSION,
        "ok": not errors,
        "ready_row_count": len(ready_rows),
        "accepted_claim_count": len(accepted_by_config),
        "rows": row_reports,
        "errors": errors,
        "repo_root": _display_path(repo, repo),
        "ledger_ref": f"{_display_path(repo, Path(ledger_path).resolve())}#{_sha256(Path(ledger_path).resolve())}",
        "accepted_report_ref": f"{_display_path(repo, Path(accepted_report_path).resolve())}#{_sha256(Path(accepted_report_path).resolve())}",
    }
    return apply_gate_error_envelope(report, "primitive_readiness")


def collect_primitive_readiness_errors(**kwargs: Any) -> list[str]:
    return list(build_primitive_readiness_report(**kwargs).get("errors", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate primitive-family readiness for accepted E4 C4 ledger rows.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--accepted-report", default=str(DEFAULT_ACCEPTED_REPORT))
    parser.add_argument("--json-out", default=str(DEFAULT_REPORT))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    report = build_primitive_readiness_report(
        repo_root=args.repo_root,
        ledger_path=args.ledger,
        accepted_report_path=args.accepted_report,
    )
    if not args.no_write:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report["ok"]:
        print("ok")
    else:
        for error in report["errors"]:
            print(error, file=sys.stderr)
    return gate_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
