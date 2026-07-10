from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT.parent
FALLBACK_SCORE_SUBLEDGER_PATH = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "BB_E4_SCORE_SUBLEDGER.json"
FALLBACK_ACCEPTED_CLAIM_REPORT_PATH = (
    WORKSPACE_ROOT
    / "docs_tmp"
    / "phase_15"
    / "pro_requests"
    / "e4_breakthrough_20260629"
    / "execution"
    / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
)
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
_INVENTORY = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
ACCEPTED_INVENTORY_LANES = tuple(
    lane for lane in _INVENTORY["lanes"] if isinstance(lane, Mapping) and lane.get("status") == "accepted"
)
TARGET_SUPPORT_LANES = tuple(lane for lane in ACCEPTED_INVENTORY_LANES if lane.get("kind") == "target_support")
NON_TARGET_ACCOUNTING_LANES = tuple(
    lane for lane in ACCEPTED_INVENTORY_LANES if lane.get("kind") == "non_target_accounting"
)
P3_NON_TARGET_LANES = tuple(
    lane for lane in NON_TARGET_ACCOUNTING_LANES if lane.get("phase") == "P3"
)
_CHECKED_IN_SUBLEDGER = json.loads(FALLBACK_SCORE_SUBLEDGER_PATH.read_text(encoding="utf-8"))
EXPECTED_ACCEPTED_POINTS = int(_CHECKED_IN_SUBLEDGER["current_accepted_points"])
EXPECTED_TARGET_SUPPORT_POINTS = sum(int(lane["points"]) for lane in TARGET_SUPPORT_LANES)
EXPECTED_NON_TARGET_ACCOUNTING_POINTS = EXPECTED_ACCEPTED_POINTS - EXPECTED_TARGET_SUPPORT_POINTS
EXPECTED_ACCEPTED_SUPPORT_CLAIMS = len(TARGET_SUPPORT_LANES)
EXPECTED_ACCEPTED_NON_TARGET_CLAIMS = len(NON_TARGET_ACCOUNTING_LANES)
EXPECTED_P3_NON_TARGET_POINTS = sum(int(lane["points"]) for lane in P3_NON_TARGET_LANES)
P3_1_SCORE_ROW_ID = str(P3_NON_TARGET_LANES[0]["score_row_id"])
P3_1_NODE_GATE_REF_WITHOUT_HASH = str(
    P3_NON_TARGET_LANES[0]["ct"]["command"]["argv"][
        P3_NON_TARGET_LANES[0]["ct"]["command"]["argv"].index("--json-out") + 1
    ]
)
REQUIRED_ACCEPTED_CLAIM_FIELDS = (
    "phase",
    "points",
    "target_family",
    "score_row_ref",
    "ledger_row_refs",
    "live_validator_ref",
)


PathValidator = Callable[..., list[str]]


def _validator_module() -> Any | None:
    return importlib.import_module("scripts.e4_parity.e4_closure_score_section")


def _path_validator() -> PathValidator | None:
    module = _validator_module()
    if module is None:
        return None
    candidate = getattr(module, "collect_score_subledger_errors", None)
    return candidate if callable(candidate) else None


def _score_subledger_path() -> Path:
    module = _validator_module()
    if module is not None and isinstance(getattr(module, "DEFAULT_SUBLEDGER", None), Path):
        return getattr(module, "DEFAULT_SUBLEDGER")
    return FALLBACK_SCORE_SUBLEDGER_PATH


def _accepted_claim_report_path() -> Path:
    module = _validator_module()
    if module is not None and isinstance(getattr(module, "DEFAULT_ACCEPTED_REPORT", None), Path):
        return getattr(module, "DEFAULT_ACCEPTED_REPORT")
    return FALLBACK_ACCEPTED_CLAIM_REPORT_PATH


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    module = _validator_module()
    candidate = getattr(module, "_row_hash", None) if module is not None else None
    if callable(candidate):
        return str(candidate(row_id, row))
    payload = {"row_id": row_id, "row": row}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a JSON object"
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _checked_in_payloads() -> tuple[dict[str, Any], dict[str, Any]]:
    subledger_path = _score_subledger_path()
    accepted_report_path = _accepted_claim_report_path()
    assert subledger_path.is_file(), f"missing score subledger artifact: {subledger_path}"
    assert accepted_report_path.is_file(), f"missing accepted claim report: {accepted_report_path}"
    return _load_json(subledger_path), _load_json(accepted_report_path)


def _require_path_validator() -> PathValidator:
    validator = _path_validator()
    if validator is None:
        pytest.fail(
            "missing scripts.e4_parity.e4_closure_score_section.collect_score_subledger_errors; "
            "mutation tests need a production helper that rejects broken score-subledger payloads"
        )
    return validator


def _run_validator(subledger_path: Path, accepted_report_path: Path) -> list[str]:
    validator = _require_path_validator()
    return [
        str(error)
        for error in validator(
            subledger_path=subledger_path,
            accepted_report_path=accepted_report_path,
            repo_root=ROOT,
        )
    ]


def _artifact_pair(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    subledger, accepted_report = _checked_in_payloads()
    subledger_path = tmp_path / "BB_E4_SCORE_SUBLEDGER.json"
    accepted_report_path = tmp_path / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
    _write_json(subledger_path, subledger)
    _write_json(accepted_report_path, accepted_report)
    return subledger_path, accepted_report_path, subledger, accepted_report


def _score_rows(subledger: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = subledger.get("score_rows")
    assert isinstance(rows, list), "score subledger must expose score_rows"
    assert all(isinstance(row, Mapping) for row in rows), "score_rows entries must be objects"
    return rows


def _accepted_claim_entries(accepted_report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = accepted_report.get("accepted_support_claims")
    assert isinstance(entries, list), "accepted report must expose accepted_support_claims"
    assert all(isinstance(entry, Mapping) for entry in entries), "accepted_support_claims entries must be objects"
    return entries


def _accepted_non_target_claim_entries(accepted_report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = accepted_report.get("accepted_non_target_claims")
    assert isinstance(entries, list), "accepted report must expose accepted_non_target_claims"
    assert all(isinstance(entry, Mapping) for entry in entries), (
        "accepted_non_target_claims entries must be objects"
    )
    return entries


def _p3_non_target_score_row(subledger: Mapping[str, Any]) -> Mapping[str, Any]:
    matches = [
        row
        for row in _score_rows(subledger)
        if row.get("kind") == "non_target_accounting"
        and row.get("phase") == "P3"
        and row.get("evidence_scope") == "helper_runtime_c4_lineage_non_target"
        and row.get("score_row_id") == P3_1_SCORE_ROW_ID
    ]
    assert len(matches) == 1, "score subledger must carry the accepted P3.1 C4 non-target row"
    return matches[0]


def _accepted_non_target_claim(accepted_report: Mapping[str, Any]) -> Mapping[str, Any]:
    entries = [
        entry
        for entry in _accepted_non_target_claim_entries(accepted_report)
        if isinstance(entry.get("score_row_ref"), str)
        and f"#{P3_1_SCORE_ROW_ID}#" in str(entry["score_row_ref"])
    ]
    assert len(entries) == 1, "accepted report must carry the accepted P3.1 non-target claim"
    return entries[0]


def _refresh_non_target_score_row_ref(
    accepted_report: Mapping[str, Any],
    score_row: Mapping[str, Any],
) -> None:
    claim = _accepted_non_target_claim(accepted_report)
    score_row_id = _score_row_id(score_row)
    prefix = str(claim["score_row_ref"]).rsplit("#sha256:", 1)[0]
    claim["score_row_ref"] = prefix + "#" + _row_hash(score_row_id, score_row)


def _score_row_id(row: Mapping[str, Any]) -> str:
    value = row.get("score_row_id")
    assert isinstance(value, str) and value, f"score row must expose score_row_id: {row!r}"
    return value


def _claim_ref(entry: Mapping[str, Any]) -> str:
    value = entry.get("claim_ref")
    assert isinstance(value, str) and value, f"accepted claim must expose claim_ref: {entry!r}"
    return value


def _row_points(row: Mapping[str, Any]) -> int:
    value = row.get("points")
    assert isinstance(value, int), f"score row points must be an integer: {row!r}"
    return value


def _assert_local_score_invariants(subledger: Mapping[str, Any], accepted_report: Mapping[str, Any]) -> None:
    assert subledger.get("current_accepted_points") == EXPECTED_ACCEPTED_POINTS
    assert accepted_report.get("current_accepted_points") == EXPECTED_ACCEPTED_POINTS
    assert accepted_report.get("terminal_state") == "complete_1000_of_1000"
    assert accepted_report.get("remaining_blockers") == []
    assert accepted_report.get("preflight_blocked") is True
    assert subledger.get("preflight_blocked") is True
    assert subledger.get("target_support_claim_points") == EXPECTED_TARGET_SUPPORT_POINTS
    assert subledger.get("non_target_accounting_points") == EXPECTED_NON_TARGET_ACCOUNTING_POINTS
    assert EXPECTED_ACCEPTED_POINTS - EXPECTED_TARGET_SUPPORT_POINTS == EXPECTED_NON_TARGET_ACCOUNTING_POINTS

    rows = _score_rows(subledger)
    entries = _accepted_claim_entries(accepted_report)
    assert len(entries) == EXPECTED_ACCEPTED_SUPPORT_CLAIMS
    non_target_claims = accepted_report.get("accepted_non_target_claims")
    assert isinstance(non_target_claims, list), "accepted report must expose accepted_non_target_claims"
    assert all(isinstance(entry, Mapping) for entry in non_target_claims), (
        "accepted_non_target_claims entries must be objects"
    )
    assert len(non_target_claims) == EXPECTED_ACCEPTED_NON_TARGET_CLAIMS

    row_ids = [_score_row_id(row) for row in rows]
    assert len(row_ids) == len(set(row_ids)), "score row ids must be unique"

    claim_refs = [_claim_ref(entry) for entry in entries]
    assert len(claim_refs) == len(set(claim_refs)), "accepted support claim refs must be unique"
    non_target_claim_refs = [_claim_ref(entry) for entry in non_target_claims]
    assert len(non_target_claim_refs) == len(set(non_target_claim_refs)), (
        "accepted non-target claim refs must be unique"
    )

    row_by_id = {_score_row_id(row): row for row in rows}
    target_support_rows = [row for row in rows if row.get("kind") == "target_support_claim"]
    non_target_rows = [row for row in rows if row.get("kind") == "non_target_accounting"]
    target_support_refs = {row.get("support_claim_ref") for row in target_support_rows}
    p3_non_target_rows = [
        row
        for row in non_target_rows
        if row.get("phase") == "P3" and row.get("evidence_scope") == "helper_runtime_c4_lineage_non_target"
    ]
    assert len(target_support_rows) == EXPECTED_ACCEPTED_SUPPORT_CLAIMS
    assert target_support_refs == set(claim_refs)
    assert len(p3_non_target_rows) == len(P3_NON_TARGET_LANES)
    assert sum(_row_points(row) for row in p3_non_target_rows) == EXPECTED_P3_NON_TARGET_POINTS
    p3_scopes = [row.get("scope") for row in p3_non_target_rows]
    assert all(isinstance(scope, Mapping) for scope in p3_scopes), "accepted P3 non-target rows must carry scopes"
    assert {scope.get("p3_item") for scope in p3_scopes if isinstance(scope, Mapping)} == {
        "P3.1",
        "P3.2",
        "P3.3",
        "P3.4",
        "P3.5",
        "P3.6",
        "P3.7",
        "P3.8",
    }
    p3_non_target_claim_refs = {
        str(row.get("support_claim_ref"))
        for row in p3_non_target_rows
        if row.get("support_claim_ref")
    }
    assert p3_non_target_claim_refs.issubset(set(non_target_claim_refs))
    assert {row.get("support_claim_ref") for row in non_target_rows if row.get("support_claim_ref")} == set(non_target_claim_refs)

    for entry in entries:
        for field in REQUIRED_ACCEPTED_CLAIM_FIELDS:
            value = entry.get(field)
            assert value is not None and value != "", f"accepted claim {_claim_ref(entry)} has null/empty {field}"
        assert isinstance(entry["points"], int), f"accepted claim {_claim_ref(entry)} points must be an integer"
        assert isinstance(entry["ledger_row_refs"], list) and entry["ledger_row_refs"], (
            f"accepted claim {_claim_ref(entry)} must carry row-scoped ledger refs"
        )
        score_row_ref = entry["score_row_ref"]
        assert isinstance(score_row_ref, str)
        row_id = score_row_ref.split("#", 2)[1] if "#" in score_row_ref else score_row_ref
        assert row_id in row_by_id, f"accepted claim {_claim_ref(entry)} points at missing score row {score_row_ref!r}"
        row = row_by_id[row_id]
        assert row.get("kind") == "target_support_claim"
        assert row.get("support_claim_ref") == _claim_ref(entry)
        assert _row_points(row) == entry["points"]

    for entry in non_target_claims:
        for field in REQUIRED_ACCEPTED_CLAIM_FIELDS:
            value = entry.get(field)
            assert value is not None and value != "", (
                f"accepted non-target claim {_claim_ref(entry)} has null/empty {field}"
            )
        assert isinstance(entry["points"], int), (
            f"accepted non-target claim {_claim_ref(entry)} points must be an integer"
        )
        assert isinstance(entry["ledger_row_refs"], list) and entry["ledger_row_refs"], (
            f"accepted non-target claim {_claim_ref(entry)} must carry row-scoped ledger refs"
        )
        score_row_ref = entry["score_row_ref"]
        assert isinstance(score_row_ref, str)
        row_id = score_row_ref.split("#", 2)[1] if "#" in score_row_ref else score_row_ref
        assert row_id in row_by_id, (
            f"accepted non-target claim {_claim_ref(entry)} points at missing score row {score_row_ref!r}"
        )
        row = row_by_id[row_id]
        assert row.get("kind") == "non_target_accounting"
        assert _row_points(row) == entry["points"]

    assert sum(_row_points(row) for row in target_support_rows) == EXPECTED_TARGET_SUPPORT_POINTS
    assert sum(_row_points(row) for row in non_target_rows) == EXPECTED_NON_TARGET_ACCOUNTING_POINTS
    assert sum(_row_points(row) for row in rows) == EXPECTED_ACCEPTED_POINTS


def _assert_invalid(subledger_path: Path, accepted_report_path: Path, *needles: str) -> None:
    errors = _run_validator(subledger_path, accepted_report_path)
    assert errors, "broken score-subledger payload was accepted"
    joined = "\n".join(errors).lower()
    for needle in needles:
        assert needle.lower() in joined

def test_validate_score_subledger_json_helper_envelopes_minimal_invalid_payload(tmp_path: Path) -> None:
    """The JSON helper must preserve rendered legacy errors while exposing structured gate errors."""
    module = _validator_module()
    validate_score_subledger = getattr(module, "validate_score_subledger", None) if module is not None else None
    if not callable(validate_score_subledger):
        pytest.fail("missing scripts.e4_parity.e4_closure_score_section.validate_score_subledger")
    subledger_path = tmp_path / "BB_E4_SCORE_SUBLEDGER.json"
    accepted_report_path = tmp_path / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
    _write_json(
        subledger_path,
        {
            "schema_version": "not.bb.e4.score_subledger.v1",
            "current_accepted_points": 1,
            "target_support_claim_points": 0,
            "score_rows": [],
        },
    )
    _write_json(
        accepted_report_path,
        {
            "current_accepted_points": 2,
            "accepted_support_claims": [],
            "accepted_non_target_claims": [],
        },
    )

    report = validate_score_subledger(
        subledger_path=subledger_path,
        accepted_report_path=accepted_report_path,
        repo_root=ROOT,
    )

    assert report["ok"] is False
    assert report["pin_stale_count"] == 0
    assert report["semantic_count"] == report["error_count"] >= 3
    assert len(report["gate_errors"]) == report["error_count"]
    assert len(report["errors"]) == report["error_count"]
    assert all(error["klass"] == "semantic" for error in report["gate_errors"])
    assert all(isinstance(error, str) and error.startswith("[SEMANTIC] ") for error in report["errors"])
    assert any(
        error["message"] == "subledger.schema_version must be bb.e4.score_subledger.v1"
        for error in report["gate_errors"]
    )
    assert any("score row points sum expected 1, got 0" in error for error in report["errors"])
    assert any(
        "subledger.current_accepted_points must match accepted_report.current_accepted_points" in error
        for error in report["errors"]
    )
    assert module.gate_exit_code(report) == 4


def test_score_subledger_helper_or_checked_in_artifact_accounts_for_inventory_points(tmp_path: Path) -> None:
    """The E4 score ledger accounts for inventory-derived points without broadening target-support rows."""
    subledger_path, accepted_report_path, subledger, accepted_report = _artifact_pair(tmp_path)
    _assert_local_score_invariants(subledger, accepted_report)

    validator = _path_validator()
    if validator is not None:
        assert _run_validator(subledger_path, accepted_report_path) == []


@pytest.mark.parametrize("field", REQUIRED_ACCEPTED_CLAIM_FIELDS)
def test_accepted_support_claim_entries_reject_null_score_linkage(tmp_path: Path, field: str) -> None:
    """Accepted support claims must not retain null phase, point, target, row, ledger, or live-validator links."""
    subledger_path, accepted_report_path, _subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_report = copy.deepcopy(accepted_report)
    mutated_report["accepted_support_claims"][0][field] = None
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, field)


def test_missing_accepted_non_target_claims_are_rejected(tmp_path: Path) -> None:
    """The accepted report must keep one-to-one rows for inventory-managed C4 non-target score rows."""
    subledger_path, accepted_report_path, _subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_report = copy.deepcopy(accepted_report)
    del mutated_report["accepted_non_target_claims"]
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, "accepted_non_target_claims")


@pytest.mark.parametrize(
    ("mutation_name", "mutate_score_row_ref", "needles"),
    (
        (
            "wrong embedded row hash",
            lambda ref: ref.rsplit("#sha256:", 1)[0] + "#sha256:" + "0" * 64,
            ("score_row_ref", "hash"),
        ),
        (
            "wrong score row id",
            lambda ref: ref.replace(
                f"#{P3_1_SCORE_ROW_ID}#",
                "#score_missing_inventory_non_target_row#",
            ),
            ("score_row_ref", "score row"),
        ),
    ),
)
def test_accepted_non_target_claim_score_row_ref_is_rejected_when_stale_or_mispointed(
    tmp_path: Path,
    mutation_name: str,
    mutate_score_row_ref: Callable[[str], str],
    needles: tuple[str, ...],
) -> None:
    """Accepted non-target score_row_ref must name the inventory-derived score row and carry its current row hash."""
    subledger_path, accepted_report_path, _subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_report = copy.deepcopy(accepted_report)
    claim = _accepted_non_target_claim(mutated_report)
    claim["score_row_ref"] = mutate_score_row_ref(str(claim["score_row_ref"]))
    assert claim["score_row_ref"] != accepted_report["accepted_non_target_claims"][0]["score_row_ref"], mutation_name
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, *needles)


@pytest.mark.parametrize(
    ("field", "accepted_field", "bad_value", "needles"),
    (
        ("support_claim_sha256", "claim_sha256", "sha256:" + "0" * 64, ("claim", "hash")),
        ("evidence_manifest_sha256", "evidence_manifest_sha256", "sha256:" + "0" * 64, ("evidence", "hash")),
        (
            "live_validator_ref",
            "live_validator_ref",
            P3_1_NODE_GATE_REF_WITHOUT_HASH,
            ("live_validator_ref", "sha256"),
        ),
    ),
)
def test_non_target_c4_artifact_hashes_and_live_validator_ref_are_rejected_when_invalid(
    tmp_path: Path,
    field: str,
    accepted_field: str,
    bad_value: str,
    needles: tuple[str, ...],
) -> None:
    """Non-target C4 rows must hash-check support, evidence, and live-validator artifacts, not just link rows."""
    subledger_path, accepted_report_path, subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_subledger = copy.deepcopy(subledger)
    mutated_report = copy.deepcopy(accepted_report)
    score_row = _p3_non_target_score_row(mutated_subledger)
    claim = _accepted_non_target_claim(mutated_report)
    score_row[field] = bad_value
    claim[accepted_field] = bad_value
    _refresh_non_target_score_row_ref(mutated_report, score_row)
    _write_json(subledger_path, mutated_subledger)
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, *needles)


def test_target_support_and_total_score_rows_have_explicit_point_totals(tmp_path: Path) -> None:
    """The ten accepted target-support rows account for 370 points, and non-target rows account for the rest."""
    subledger_path, accepted_report_path, subledger, accepted_report = _artifact_pair(tmp_path)

    _assert_local_score_invariants(subledger, accepted_report)
    assert _run_validator(subledger_path, accepted_report_path) == []


def test_duplicate_score_row_ids_are_rejected(tmp_path: Path) -> None:
    """Score row ids must stay unique so accepted score_row_ref links cannot collapse onto an arbitrary row."""
    subledger_path, accepted_report_path, subledger, _accepted_report = _artifact_pair(tmp_path)
    mutated_subledger = copy.deepcopy(subledger)
    mutated_subledger["score_rows"][1]["score_row_id"] = mutated_subledger["score_rows"][0]["score_row_id"]
    _write_json(subledger_path, mutated_subledger)

    _assert_invalid(subledger_path, accepted_report_path, "duplicate", "score_row_id")


def test_duplicate_support_claim_refs_are_rejected(tmp_path: Path) -> None:
    """Accepted support claim refs need one score mapping each; duplicates double-count one claim and hide another."""
    subledger_path, accepted_report_path, _subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_report = copy.deepcopy(accepted_report)
    mutated_report["accepted_support_claims"][1]["claim_ref"] = mutated_report["accepted_support_claims"][0]["claim_ref"]
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, "duplicate", "claim")


def test_wrong_accepted_report_support_claim_sha256_is_rejected(tmp_path: Path) -> None:
    """Accepted claim rows must bind claim_sha256 to the current support-claim artifact bytes."""
    subledger_path, accepted_report_path, _subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_report = copy.deepcopy(accepted_report)
    mutated_report["accepted_support_claims"][0]["claim_sha256"] = "sha256:" + "0" * 64
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, "claim", "sha256")


def test_wrong_accepted_report_ledger_row_ref_embedded_hash_is_rejected(tmp_path: Path) -> None:
    """Accepted claim ledger_row_refs must keep the embedded row hash that belongs to the cited ledger row."""
    subledger_path, accepted_report_path, subledger, accepted_report = _artifact_pair(tmp_path)
    mutated_subledger = copy.deepcopy(subledger)
    mutated_report = copy.deepcopy(accepted_report)
    first_claim = mutated_report["accepted_support_claims"][0]
    first_ref = first_claim["ledger_row_refs"][0]
    wrong_ref = first_ref.rsplit("#sha256:", 1)[0] + "#sha256:" + "0" * 64
    first_claim["ledger_row_refs"][0] = wrong_ref
    score_row_id = first_claim["score_row_ref"].split("#", 2)[1]
    matched_row = None
    for row in mutated_subledger["score_rows"]:
        if row.get("score_row_id") == score_row_id:
            row["ledger_row_refs"][0] = wrong_ref
            matched_row = row
            break
    if matched_row is None:
        pytest.fail(f"score row {score_row_id!r} not found")
    first_claim["score_row_ref"] = first_claim["score_row_ref"].rsplit("#sha256:", 1)[0] + "#" + _row_hash(score_row_id, matched_row)
    _write_json(subledger_path, mutated_subledger)
    _write_json(accepted_report_path, mutated_report)

    _assert_invalid(subledger_path, accepted_report_path, "ledger", "hash")
