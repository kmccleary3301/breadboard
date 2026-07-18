#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.e4_parity.path_refs import (  # noqa: E402
    ReferenceResolutionError,
    resolve_declared_reference,
    workspace_root_for_checkout,
)
from scripts.e4_parity.validators import hash_utils  # noqa: E402

WORKSPACE = workspace_root_for_checkout(ROOT)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hash_utils.sha256_path(path)


def _resolve(reference: str, *, label: str) -> Path:
    raw = Path(reference.split("#", 1)[0])
    namespace = (
        "workspace_evidence"
        if raw.parts and raw.parts[0] in {"docs_tmp", ROOT.name}
        else "repo"
    )
    return resolve_declared_reference(
        reference,
        checkout_root=ROOT,
        namespace=namespace,
        label=label,
        workspace_root=WORKSPACE if namespace == "workspace_evidence" else None,
    )


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    payload = {"row_id": row_id, "row": row}
    return hash_utils.sha256_json(payload)


def _validate_row_reference(reference: str, *, label: str, errors: list[str]) -> None:
    parts = reference.split("#")
    if len(parts) != 3 or not parts[1] or not parts[2].startswith("sha256:"):
        errors.append(f"{label} must use path#row_id#sha256:<row_hash>")
        return
    path = _resolve(reference, label=label)
    payload = _load_json(path)
    rows = payload.get("rows")
    row = (
        next(
            (
                item
                for item in rows
                if isinstance(item, Mapping) and item.get("feature_id") == parts[1]
            ),
            None,
        )
        if isinstance(rows, list)
        else None
    )
    if row is None:
        errors.append(f"{label} row {parts[1]!r} is missing")
    elif _row_hash(parts[1], row) != parts[2]:
        errors.append(f"{label} row hash mismatch")


def _validate_freeze_reference(
    reference: str,
    *,
    report: Mapping[str, Any],
    errors: list[str],
) -> None:
    parts = reference.split("#")
    if len(parts) != 3 or not parts[1] or not parts[2].startswith("sha256:"):
        errors.append("frozen support claim freeze_ref must use path#config_id#sha256:<row_hash>")
        return
    path = _resolve(reference, label="frozen freeze manifest")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    configs = payload.get("e4_configs") if isinstance(payload, Mapping) else None
    row = configs.get(parts[1]) if isinstance(configs, Mapping) else None
    if not isinstance(row, Mapping):
        errors.append(f"frozen freeze manifest row {parts[1]!r} is missing")
        return
    actual_row_hash = _row_hash(parts[1], row)
    hashes = report.get("hashes")
    refs = report.get("refs")
    if actual_row_hash != parts[2]:
        errors.append("frozen support claim freeze_ref row hash mismatch")
    if isinstance(hashes, Mapping) and hashes.get("freeze_manifest_row") != actual_row_hash:
        errors.append("frozen validation report freeze_manifest_row hash mismatch")
    if isinstance(hashes, Mapping) and hashes.get("freeze_manifest") != _sha256(path):
        errors.append("frozen validation report freeze_manifest hash mismatch")
    if isinstance(refs, Mapping) and refs.get("freeze_manifest") != reference.split("#", 1)[0]:
        errors.append("frozen validation report freeze_manifest ref mismatch")


def validate(validation_report_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    report = _load_json(validation_report_path)
    refs = report.get("refs")
    hashes = report.get("hashes")
    claimed_scope = report.get("claimed_scope")
    if report.get("ok") is not True:
        errors.append("frozen validation report ok must be true")
    if report.get("accepted") is not True:
        errors.append("frozen validation report accepted must be true")
    if not isinstance(refs, Mapping):
        errors.append("frozen validation report refs must be an object")
        refs = {}
    if not isinstance(hashes, Mapping):
        errors.append("frozen validation report hashes must be an object")
        hashes = {}
    if not isinstance(claimed_scope, Mapping):
        errors.append("frozen validation report claimed_scope must be an object")
        claimed_scope = {}

    loaded: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    for role in ("support_claim", "evidence_manifest"):
        reference = refs.get(role)
        expected_hash = hashes.get(role)
        if not isinstance(reference, str) or not reference:
            errors.append(f"frozen validation report refs.{role} must be a path")
            continue
        if report.get(role) != reference:
            errors.append(f"frozen validation report {role} must match refs.{role}")
        try:
            path = _resolve(reference, label=f"frozen {role}")
            paths[role] = path.relative_to(ROOT).as_posix()
            actual_hash = _sha256(path)
            if expected_hash != actual_hash:
                errors.append(
                    f"frozen {role} hash mismatch: expected {expected_hash!r}, got {actual_hash!r}"
                )
            loaded[role] = _load_json(path)
        except (FileNotFoundError, OSError, ReferenceResolutionError, ValueError) as exc:
            errors.append(f"frozen {role} invalid: {exc}")

    support_claim = loaded.get("support_claim")
    evidence_manifest = loaded.get("evidence_manifest")
    if support_claim is not None:
        if support_claim.get("accepted") is not True:
            errors.append("frozen support claim accepted must be true")
        support_scope = support_claim.get("scope")
        if not isinstance(support_scope, Mapping) or not support_scope:
            errors.append("frozen support claim scope must be a non-empty object")
        elif any(claimed_scope.get(key) != value for key, value in support_scope.items()):
            errors.append("frozen support claim scope must match validation report claimed_scope")
        if support_claim.get("evidence_manifest_ref") != refs.get("evidence_manifest"):
            errors.append("frozen support claim evidence_manifest_ref must match validation report")
        freeze_ref = support_claim.get("freeze_ref")
        if not isinstance(freeze_ref, str) or not freeze_ref:
            errors.append("frozen support claim freeze_ref must be a non-empty string")
        else:
            _validate_freeze_reference(freeze_ref, report=report, errors=errors)
        ledger_refs = support_claim.get("ledger_row_refs")
        if not isinstance(ledger_refs, list) or not ledger_refs:
            errors.append("frozen support claim ledger_row_refs must be a non-empty list")
        else:
            for index, ledger_ref in enumerate(ledger_refs):
                if not isinstance(ledger_ref, str):
                    errors.append(f"frozen support claim ledger_row_refs[{index}] must be a string")
                    continue
                _validate_row_reference(
                    ledger_ref,
                    label=f"frozen support claim ledger_row_refs[{index}]",
                    errors=errors,
                )
    if evidence_manifest is not None:
        artifacts = evidence_manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append("frozen evidence manifest artifacts must be a non-empty list")
        else:
            for index, artifact in enumerate(artifacts):
                if not isinstance(artifact, Mapping):
                    errors.append(f"frozen evidence manifest artifacts[{index}] must be an object")
                    continue
                path_ref = artifact.get("path")
                expected_hash = artifact.get("sha256")
                if not isinstance(path_ref, str) or not path_ref:
                    errors.append(f"frozen evidence manifest artifacts[{index}].path must be a string")
                    continue
                if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:"):
                    errors.append(f"frozen evidence manifest artifacts[{index}].sha256 must be a sha256 digest")
                    continue
                artifact_path = _resolve(
                    path_ref,
                    label=f"frozen evidence manifest artifacts[{index}]",
                )
                actual_hash = _sha256(artifact_path)
                if actual_hash != expected_hash:
                    errors.append(
                        f"frozen evidence manifest artifact hash mismatch: {path_ref}"
                    )
    if support_claim is not None and evidence_manifest is not None:
        if evidence_manifest.get("claim_id") != support_claim.get("claim_id"):
            errors.append("frozen evidence manifest claim_id must match support claim")
        support_artifacts = [
            artifact
            for artifact in evidence_manifest.get("artifacts", [])
            if isinstance(artifact, Mapping) and artifact.get("role") == "support_claim_ref"
        ]
        if len(support_artifacts) != 1:
            errors.append("frozen evidence manifest must contain one support_claim_ref artifact")
        else:
            artifact = support_artifacts[0]
            if artifact.get("path") != refs.get("support_claim"):
                errors.append("frozen evidence manifest support_claim_ref path must match validation report")
            if artifact.get("sha256") != hashes.get("support_claim"):
                errors.append("frozen evidence manifest support_claim_ref hash must match validation report")

    return {
        "schema_version": "bb.e4.frozen_evidence_validation.v1",
        "ok": not errors,
        "accepted": not errors,
        "errors": errors,
        "claimed_scope": dict(claimed_scope),
        "validation_report": validation_report_path.relative_to(ROOT).as_posix(),
        "validated_paths": paths,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a hash-bound frozen E4 evidence chain.")
    parser.add_argument("--validation-report", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    output = Path(args.json_out)
    if not output.is_absolute():
        output = ROOT / output
    try:
        report_path = _resolve(args.validation_report, label="frozen validation report")
        result = validate(report_path)
    except (FileNotFoundError, OSError, ReferenceResolutionError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "schema_version": "bb.e4.frozen_evidence_validation.v1",
            "ok": False,
            "accepted": False,
            "errors": [str(exc)],
            "claimed_scope": {},
            "validation_report": args.validation_report,
            "validated_paths": {},
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
