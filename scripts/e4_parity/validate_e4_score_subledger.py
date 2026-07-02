#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
    from scripts.e4_parity.validators.gate_errors import apply_gate_error_envelope, gate_exit_code
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils
    from validators.gate_errors import apply_gate_error_envelope, gate_exit_code


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT.parent
DEFAULT_SUBLEDGER = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "BB_E4_SCORE_SUBLEDGER.json"
DEFAULT_ACCEPTED_REPORT = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "pro_requests" / "e4_breakthrough_20260629" / "execution" / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
SCHEMA_VERSION = "bb.e4.score_subledger.v1"


def _load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256_path(path: Path) -> str:
    return _hash_utils.sha256_path(path)


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    payload = {"row_id": row_id, "row": row}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + _hash_utils.sha256_hex(encoded)


def _strip_ref_suffix(ref: str) -> str:
    return ref.split("#", 1)[0]


def _ref_hash(ref: str) -> str | None:
    if "#" not in ref:
        return None
    for part in ref.replace("#", " ").replace(";", " ").split():
        if part.startswith("sha256:") and len(part) == 71:
            return part
    return None


def _ref_row_id(ref: str) -> str | None:
    parts = ref.split("#")
    if len(parts) < 2 or not parts[1]:
        return None
    return parts[1]


def _resolve(repo_root: Path, ref: str) -> Path:
    raw = _strip_ref_suffix(ref)
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    workspace = repo_root.parent
    if raw.startswith("docs_tmp/") or raw.startswith(f"{repo_root.name}/"):
        return (workspace / raw).resolve()
    return (repo_root / raw).resolve()


def _as_mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: must be an object")
        return {}
    return value


def _as_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label}: must be a list")
        return []
    return value


def _validate_ref_hash(repo_root: Path, label: str, ref: str, errors: list[str]) -> None:
    expected = _ref_hash(ref)
    if expected is None:
        errors.append(f"{label}: missing sha256 hash")
        return
    path = _resolve(repo_root, ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(ref)}")
        return
    actual = _sha256_path(path)
    if actual != expected:
        errors.append(f"{label}: hash mismatch for {_strip_ref_suffix(ref)}: expected {expected}, got {actual}")

def _validate_path_hash(repo_root: Path, label: str, ref: str, expected_hash: Any, errors: list[str]) -> None:
    if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:"):
        errors.append(f"{label}: expected sha256 hash is missing")
        return
    path = _resolve(repo_root, ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(ref)}")
        return
    actual = _sha256_path(path)
    if actual != expected_hash:
        errors.append(f"{label}: hash mismatch for {_strip_ref_suffix(ref)}: expected {expected_hash}, got {actual}")


def _validate_ledger_row_ref(repo_root: Path, label: str, ref: Any, errors: list[str]) -> None:
    if not isinstance(ref, str) or not ref:
        errors.append(f"{label}: ledger row ref must be a non-empty string")
        return
    path = _resolve(repo_root, ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(ref)}")
        return
    parts = ref.split("#")
    if len(parts) != 3 or not parts[1]:
        errors.append(f"{label}: must use path#feature_id#sha256:<row_hash>")
        return
    feature_id = parts[1]
    ref_hash = _ref_hash(ref)
    payload = _load_json(path)
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        errors.append(f"{label}: ledger payload must contain rows list")
        return
    matched = next((row for row in rows if isinstance(row, Mapping) and row.get("feature_id") == feature_id), None)
    if matched is None:
        errors.append(f"{label}: feature_id not found: {feature_id}")
        return
    expected_hash = _row_hash(feature_id, matched)
    if ref_hash is None:
        errors.append(f"{label}: must include current sha256 row hash")
    elif ref_hash != expected_hash:
        errors.append(f"{label}: row hash mismatch for {feature_id}: expected {expected_hash}, got {ref_hash}")



def _is_c4_non_target_accounting_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("kind") == "non_target_accounting"
        and row.get("evidence_scope") == "helper_runtime_c4_lineage_non_target"
        and bool(row.get("support_claim_ref"))
    )


def _validate_evidence_backed_score_row(
    repo_root: Path,
    *,
    row: Mapping[str, Any],
    row_id: str,
    label: str,
    rows_by_claim_ref: dict[str, Mapping[str, Any]],
    duplicate_label: str,
    errors: list[str],
) -> None:
    support_claim_ref = row.get("support_claim_ref")
    if not isinstance(support_claim_ref, str) or not support_claim_ref:
        errors.append(f"{label}.support_claim_ref: missing")
    elif support_claim_ref in rows_by_claim_ref:
        errors.append(f"duplicate {duplicate_label} in score rows: {support_claim_ref}")
    else:
        rows_by_claim_ref[support_claim_ref] = row
    if isinstance(support_claim_ref, str) and support_claim_ref:
        _validate_path_hash(
            repo_root,
            f"{label}.support_claim_ref",
            support_claim_ref,
            row.get("support_claim_sha256"),
            errors,
        )

    evidence_manifest_ref = row.get("evidence_manifest_ref")
    if isinstance(evidence_manifest_ref, str) and evidence_manifest_ref:
        _validate_path_hash(
            repo_root,
            f"{label}.evidence_manifest_ref",
            evidence_manifest_ref,
            row.get("evidence_manifest_sha256"),
            errors,
        )
    else:
        errors.append(f"{label}.evidence_manifest_ref: missing")

    for ledger_ref in _as_list(row.get("ledger_row_refs"), f"{label}.ledger_row_refs", errors):
        _validate_ledger_row_ref(repo_root, f"{label}.ledger_row_refs", ledger_ref, errors)
    for field in ("phase", "target_family", "live_validator_ref"):
        if not row.get(field):
            errors.append(f"{label}.{field}: missing")
    if isinstance(row.get("live_validator_ref"), str):
        _validate_ref_hash(repo_root, f"{label}.live_validator_ref", str(row["live_validator_ref"]), errors)


def _validate_accepted_claim(
    repo_root: Path,
    *,
    claim: Mapping[str, Any],
    index: int,
    claim_list_label: str,
    rows_by_claim_ref: Mapping[str, Mapping[str, Any]],
    score_row_kind: str,
    seen_claim_refs: set[str],
    duplicate_label: str,
    missing_row_label: str,
    errors: list[str],
) -> None:
    claim_ref = claim.get("claim_ref")
    if not isinstance(claim_ref, str) or not claim_ref:
        errors.append(f"{claim_list_label}[{index}].claim_ref: missing")
        return
    if claim_ref in seen_claim_refs:
        errors.append(f"duplicate {duplicate_label}: {claim_ref}")
    seen_claim_refs.add(claim_ref)

    for field in ("phase", "points", "target_family", "score_row_ref", "live_validator_ref"):
        value = claim.get(field)
        if value is None or value == "":
            errors.append(f"{claim_list_label}[{index}].{field}: missing")
    if not claim.get("ledger_row_refs"):
        errors.append(f"{claim_list_label}[{index}].ledger_row_refs: missing")
    _validate_path_hash(
        repo_root,
        f"{claim_list_label}[{index}].claim_ref",
        claim_ref,
        claim.get("claim_sha256"),
        errors,
    )
    evidence_manifest_ref = claim.get("evidence_manifest_ref")
    if isinstance(evidence_manifest_ref, str) and evidence_manifest_ref:
        _validate_path_hash(
            repo_root,
            f"{claim_list_label}[{index}].evidence_manifest_ref",
            evidence_manifest_ref,
            claim.get("evidence_manifest_sha256"),
            errors,
        )
    else:
        errors.append(f"{claim_list_label}[{index}].evidence_manifest_ref: missing")
    for ledger_ref in _as_list(claim.get("ledger_row_refs"), f"{claim_list_label}[{index}].ledger_row_refs", errors):
        _validate_ledger_row_ref(repo_root, f"{claim_list_label}[{index}].ledger_row_refs", ledger_ref, errors)

    row = rows_by_claim_ref.get(claim_ref)
    if row is None:
        errors.append(f"{claim_list_label}[{index}] has no {missing_row_label}: {claim_ref}")
        return
    if row.get("kind") != score_row_kind:
        errors.append(f"{claim_list_label}[{index}] does not map to a {score_row_kind} score row")

    score_row_ref = claim.get("score_row_ref")
    if isinstance(score_row_ref, str):
        row_id = _ref_row_id(score_row_ref)
        if row_id != row.get("score_row_id"):
            errors.append(f"{claim_list_label}[{index}].score_row_ref does not match score row")
        expected_row_hash = _row_hash(str(row.get("score_row_id")), row)
        ref_hash = _ref_hash(score_row_ref)
        if ref_hash != expected_row_hash:
            errors.append(
                f"{claim_list_label}[{index}].score_row_ref hash mismatch: "
                f"expected {expected_row_hash}, got {ref_hash!r}"
            )
    for field in ("phase", "points", "target_family", "evidence_manifest_ref", "evidence_manifest_sha256", "live_validator_ref", "scope"):
        if claim.get(field) != row.get(field):
            errors.append(f"{claim_list_label}[{index}].{field} does not match score row")
    if claim.get("claim_sha256") != row.get("support_claim_sha256"):
        errors.append(f"{claim_list_label}[{index}].claim_sha256 does not match score row")
    if claim.get("ledger_row_refs") != row.get("ledger_row_refs"):
        errors.append(f"{claim_list_label}[{index}].ledger_row_refs does not match score row")
    if isinstance(claim.get("live_validator_ref"), str):
        _validate_ref_hash(repo_root, f"{claim_list_label}[{index}].live_validator_ref", str(claim["live_validator_ref"]), errors)


def _missing_claim_refs(rows_by_claim_ref: Mapping[str, Mapping[str, Any]], seen_claim_refs: set[str]) -> list[str]:
    return sorted(set(rows_by_claim_ref) - seen_claim_refs)

def collect_score_subledger_errors(
    *,
    subledger_path: Path | str = DEFAULT_SUBLEDGER,
    accepted_report_path: Path | str = DEFAULT_ACCEPTED_REPORT,
    repo_root: Path | str = ROOT,
) -> list[str]:
    repo = Path(repo_root).resolve()
    subledger_file = Path(subledger_path).resolve()
    accepted_file = Path(accepted_report_path).resolve()
    errors: list[str] = []

    subledger = _as_mapping(_load_json(subledger_file), "subledger", errors)
    accepted_report = _as_mapping(_load_json(accepted_file), "accepted_report", errors)
    if not subledger or not accepted_report:
        return errors

    if subledger.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"subledger.schema_version must be {SCHEMA_VERSION}")

    rows = [
        _as_mapping(row, f"subledger.score_rows[{index}]", errors)
        for index, row in enumerate(
            _as_list(subledger.get("score_rows"), "subledger.score_rows", errors),
            start=1,
        )
    ]
    row_by_id: dict[str, Mapping[str, Any]] = {}
    support_rows: dict[str, Mapping[str, Any]] = {}
    non_target_c4_rows: dict[str, Mapping[str, Any]] = {}
    total_points = 0
    target_support_points = 0
    for index, row in enumerate(rows, start=1):
        row_id = row.get("score_row_id")
        if not isinstance(row_id, str) or not row_id:
            errors.append(f"subledger.score_rows[{index}].score_row_id: missing")
            continue
        if row_id in row_by_id:
            errors.append(f"duplicate score_row_id: {row_id}")
        row_by_id[row_id] = row
        points = row.get("points")
        if not isinstance(points, int) or points < 0:
            errors.append(f"subledger.score_rows[{row_id}].points must be a non-negative integer")
            points = 0
        total_points += points
        if row.get("kind") == "target_support_claim":
            target_support_points += points
            _validate_evidence_backed_score_row(
                repo,
                row=row,
                row_id=row_id,
                label=f"subledger.score_rows[{row_id}]",
                rows_by_claim_ref=support_rows,
                duplicate_label="support_claim_ref",
                errors=errors,
            )
        elif _is_c4_non_target_accounting_row(row):
            _validate_evidence_backed_score_row(
                repo,
                row=row,
                row_id=row_id,
                label=f"subledger.score_rows[{row_id}]",
                rows_by_claim_ref=non_target_c4_rows,
                duplicate_label="non-target support_claim_ref",
                errors=errors,
            )

    expected_total = subledger.get("current_accepted_points")
    if expected_total != total_points:
        errors.append(f"score row points sum expected {expected_total!r}, got {total_points}")
    expected_support_total = subledger.get("target_support_claim_points")
    if expected_support_total != target_support_points:
        errors.append(f"target support score row points sum expected {expected_support_total!r}, got {target_support_points}")

    accepted_claims = _as_list(accepted_report.get("accepted_support_claims"), "accepted_report.accepted_support_claims", errors)
    accepted_non_target_claims = _as_list(
        accepted_report.get("accepted_non_target_claims"),
        "accepted_report.accepted_non_target_claims",
        errors,
    )
    accepted_total = accepted_report.get("current_accepted_points")
    if expected_total != accepted_total:
        errors.append(
            f"subledger.current_accepted_points must match accepted_report.current_accepted_points: "
            f"{expected_total!r} != {accepted_total!r}"
        )
    if len(support_rows) != len(accepted_claims):
        errors.append(
            f"target-support score row count must match accepted support claim count: "
            f"{len(support_rows)} != {len(accepted_claims)}"
        )
    if len(non_target_c4_rows) != len(accepted_non_target_claims):
        errors.append(
            f"C4 non-target score row count must match accepted non-target claim count: "
            f"{len(non_target_c4_rows)} != {len(accepted_non_target_claims)}"
        )

    seen_claim_refs: set[str] = set()
    for index, raw_claim in enumerate(accepted_claims, start=1):
        _validate_accepted_claim(
            repo,
            claim=_as_mapping(raw_claim, f"accepted_report.accepted_support_claims[{index}]", errors),
            index=index,
            claim_list_label="accepted_report.accepted_support_claims",
            rows_by_claim_ref=support_rows,
            score_row_kind="target_support_claim",
            seen_claim_refs=seen_claim_refs,
            duplicate_label="accepted support claim ref",
            missing_row_label="target-support score row",
            errors=errors,
        )

    seen_non_target_claim_refs: set[str] = set()
    for index, raw_claim in enumerate(accepted_non_target_claims, start=1):
        _validate_accepted_claim(
            repo,
            claim=_as_mapping(raw_claim, f"accepted_report.accepted_non_target_claims[{index}]", errors),
            index=index,
            claim_list_label="accepted_report.accepted_non_target_claims",
            rows_by_claim_ref=non_target_c4_rows,
            score_row_kind="non_target_accounting",
            seen_claim_refs=seen_non_target_claim_refs,
            duplicate_label="accepted non-target claim ref",
            missing_row_label="C4 non-target score row",
            errors=errors,
        )

    missing_claims = _missing_claim_refs(support_rows, seen_claim_refs)
    if missing_claims:
        errors.append(f"target-support score rows missing from accepted report: {', '.join(missing_claims)}")
    missing_non_target_claims = _missing_claim_refs(non_target_c4_rows, seen_non_target_claim_refs)
    if missing_non_target_claims:
        errors.append(f"C4 non-target score rows missing from accepted report: {', '.join(missing_non_target_claims)}")
    return errors


def validate_score_subledger(**kwargs: Any) -> dict[str, Any]:
    errors = collect_score_subledger_errors(**kwargs)
    report = {"ok": not errors, "errors": errors}
    return apply_gate_error_envelope(report, "score_subledger")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate E4 score subledger and accepted support-claim score rows.")
    parser.add_argument("--subledger", default=str(DEFAULT_SUBLEDGER))
    parser.add_argument("--accepted-report", default=str(DEFAULT_ACCEPTED_REPORT))
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_score_subledger(
        subledger_path=args.subledger,
        accepted_report_path=args.accepted_report,
        repo_root=args.repo_root,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print("ok")
    else:
        for error in report["errors"]:
            print(error, file=sys.stderr)
    return gate_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
