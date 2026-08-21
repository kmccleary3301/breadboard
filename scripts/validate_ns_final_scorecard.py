from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "bb.ns_final.scorecard_validation.v1"
LEDGER_SCHEMA_VERSION = "bb.ns_final_progress.v5"
CROSSWALK_SCHEMA_VERSION = "bb.ns_final_goal_crosswalk.v2"
EXPECTED_PLAN_REVISION = 2
EXPECTED_TOTAL_POINTS = 1000
EXPECTED_ITEM_COUNT = 93
EXPECTED_GOAL_COUNT = 102
EXPECTED_PLAN_INVENTORY_SHA256 = "sha256:a2a08213e1871606e86ff3995ed906ebd5bd24a3bd63ec7672fc5e95bd32f5a1"
EXPECTED_CROSSWALK_INVENTORY_SHA256 = "sha256:3740dce457e93cceea1b0fbd120de4d89e555d6d8f223dc075349b11470c4023"
EXPECTED_TRACKS: dict[str, tuple[int, int]] = {
    "SEC": (140, 13),
    "PROV": (50, 5),
    "E4": (120, 10),
    "PRIM": (100, 9),
    "ENG": (60, 6),
    "SRV": (70, 7),
    "RAY": (30, 2),
    "TS": (60, 5),
    "TEST": (50, 4),
    "GOV": (70, 11),
    "PROD": (90, 4),
    "RL": (80, 10),
    "SYNC": (40, 4),
    "AUD": (40, 3),
}
EXPECTED_GATES = ("G0", "G1", "G2", "G3", "G4")
ALLOWED_DISPOSITIONS = {"scored", "conditional", "deferred"}
ALLOWED_OBSERVATION_KINDS = {"local", "target", "external"}
ALLOWED_TEST_DOUBLE_STATUSES = {
    "none",
    "claim_permitted_fixture",
    "target_real",
    "external_real",
}
DEFAULT_CARD_FIELDS = {
    "item_id",
    "candidate_or_run",
    "claim",
    "seam",
    "artifact_ref",
    "artifact_identity",
    "observation_receipt_ref",
    "observation_receipt_identity",
    "observed_result",
    "support_level",
    "observation_kind",
    "method",
    "declared_scope",
    "test_double_status",
}
PLAN_ITEM_RE = re.compile(
    r"^\| ((?:SEC|PROV|E4|PRIM|ENG|SRV|RAY|TS|TEST|GOV|PROD|RL|SYNC|AUD)-\d+) "
    r"\| (\d+) \| ([^|]+?) \| (.*?) \| (.*?) \|$"
)
CROSSWALK_INVENTORY_FIELDS = (
    "goal_id",
    "source_domain",
    "status_at_inventory",
    "statement",
    "disposition",
    "plan_refs",
    "wake_trigger",
    "coverage_rule",
)
AUD2_ITEM_CHECK_FIELDS = {
    "item_id",
    "assigned_dimension",
    "claim",
    "seam",
    "evidence_card_id",
    "artifact_identity",
    "falsification_method",
    "scope_checked",
    "test_double_status",
    "falsification_observation_ref",
    "falsification_observation_identity",
    "observed_result",
    "verdict",
}


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _workspace_root(ledger_path: Path) -> Path:
    resolved = ledger_path.resolve()
    for parent in resolved.parents:
        if parent.name == "docs_tmp":
            return parent.parent
    return resolved.parent


def _resolve_local_ref(*, workspace_root: Path, ref: Any) -> Path | None:
    if not isinstance(ref, str) or not ref or "://" in ref:
        return None
    path = Path(ref)
    if path.is_absolute() or ".." in path.parts:
        return None
    try:
        resolved = (workspace_root / path).resolve()
        resolved.relative_to(workspace_root.resolve())
    except ValueError:
        return None
    return resolved


def _identity_error(
    *,
    item_id: str,
    field_prefix: str,
    card: Mapping[str, Any],
    workspace_root: Path,
) -> str | None:
    ref = card.get(f"{field_prefix}_ref")
    identity = card.get(f"{field_prefix}_identity")
    path = _resolve_local_ref(workspace_root=workspace_root, ref=ref)
    if path is None:
        return f"{item_id}: {field_prefix}_ref must be a portable local immutable reference"
    if not path.is_file():
        return f"{item_id}: {field_prefix}_ref does not exist"
    actual = _sha256_bytes(path.read_bytes())
    if actual != identity:
        return f"{item_id}: {field_prefix}_identity does not match current bytes"
    return None

def _read_local_json_ref(*, workspace_root: Path, ref: Any) -> Mapping[str, Any] | None:
    path = _resolve_local_ref(workspace_root=workspace_root, ref=ref)
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _plan_inventory(
    *,
    ledger: Mapping[str, Any],
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], str | None]:
    path = _resolve_local_ref(workspace_root=workspace_root, ref=ledger.get("plan"))
    if path is None or not path.is_file():
        return [], "ledger plan must reference the portable canonical master plan"
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PLAN_ITEM_RE.match(line)
        if match is None:
            continue
        item_id, points, kind, claim, seam = match.groups()
        rows.append(
            {
                "track": item_id.split("-", 1)[0],
                "id": item_id,
                "points": int(points),
                "kind": kind.strip(),
                "claim": claim.strip(),
                "seam": seam.strip(),
            }
        )
    if len(rows) != EXPECTED_ITEM_COUNT:
        return rows, f"canonical plan must project exactly {EXPECTED_ITEM_COUNT} scored rows"
    return rows, None


def _ledger_inventory(tracks: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_name = str(track.get("track") or "")
        for item in track.get("items") or []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "track": track_name,
                    "id": item.get("id"),
                    "points": item.get("points"),
                    "kind": item.get("kind"),
                    "claim": item.get("claim"),
                    "seam": item.get("seam"),
                }
            )
    return rows


def _crosswalk_inventory(crosswalk: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {field: row.get(field) for field in CROSSWALK_INVENTORY_FIELDS}
        for row in crosswalk.get("rows") or []
        if isinstance(row, dict)
    ]


def _receipt_success(payload: Mapping[str, Any]) -> bool:
    if payload.get("passed") is True:
        return True
    return any(
        payload.get(field) in {"approved", "passed", "success", "succeeded"}
        for field in ("decision", "outcome", "result", "state", "status")
    )


def _receipt_candidate(payload: Mapping[str, Any]) -> str | None:
    for field in (
        "candidate_commit",
        "candidate",
        "candidate_or_run",
        "checkout_head",
        "target_run_id",
        "run_id",
        "external_run_id",
    ):
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _evidence_receipt_error(
    *,
    item: Mapping[str, Any],
    card: Mapping[str, Any],
    workspace_root: Path,
) -> str | None:
    item_id = str(item.get("id") or "<missing>")
    receipt = _read_local_json_ref(
        workspace_root=workspace_root,
        ref=card.get("observation_receipt_ref"),
    )
    if receipt is None:
        return f"{item_id}: observation receipt must contain a JSON object"
    if not isinstance(receipt.get("schema_version"), str) or not receipt.get("schema_version"):
        return f"{item_id}: observation receipt must name a schema_version"
    if not _receipt_success(receipt):
        return f"{item_id}: observation receipt does not record success"
    claim_ids = receipt.get("claim_ids")
    if receipt.get("item_id") != item_id and (
        not isinstance(claim_ids, list) or item_id not in claim_ids
    ):
        return f"{item_id}: observation receipt is not scoped to the scored item"
    observed_candidate = _receipt_candidate(receipt)
    if observed_candidate is None or observed_candidate not in str(card.get("candidate_or_run") or ""):
        return f"{item_id}: observation receipt candidate/run does not match the evidence card"
    receipt_test_double = receipt.get("test_double_status")
    if receipt_test_double is not None and receipt_test_double != card.get("test_double_status"):
        return f"{item_id}: observation receipt test-double status does not match the evidence card"
    return None


def _without_validator_observed_result(ledger: Mapping[str, Any]) -> dict[str, Any]:
    material = copy.deepcopy(dict(ledger))
    for track in material.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for item in track.get("items") or []:
            if isinstance(item, dict) and item.get("id") == "AUD-3":
                card = item.get("evidence_card")
                if isinstance(card, dict):
                    card.pop("observed_result", None)
    for review in material.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        for item_check in review.get("item_checks") or []:
            if isinstance(item_check, dict) and item_check.get("item_id") == "AUD-3":
                item_check.pop("observed_result", None)
    return material


def _validate_crosswalk(
    *,
    crosswalk: Mapping[str, Any],
    item_ids: set[str],
    deferred_ids: set[str],
) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    rows = crosswalk.get("rows")
    if crosswalk.get("schema_version") != CROSSWALK_SCHEMA_VERSION:
        errors.append(f"crosswalk schema_version must be {CROSSWALK_SCHEMA_VERSION}")
    if crosswalk.get("plan_revision") != EXPECTED_PLAN_REVISION:
        errors.append(f"crosswalk plan_revision must be {EXPECTED_PLAN_REVISION}")
    if crosswalk.get("goal_count") != EXPECTED_GOAL_COUNT:
        errors.append(f"crosswalk goal_count must be {EXPECTED_GOAL_COUNT}")
    if set(crosswalk.get("allowed_dispositions") or []) != ALLOWED_DISPOSITIONS:
        errors.append("crosswalk allowed_dispositions must be scored, conditional, and deferred")
    if not isinstance(rows, list):
        return errors + ["crosswalk rows must be a list"], {"rows": 0, "unique_goal_ids": 0}
    if len(rows) != EXPECTED_GOAL_COUNT:
        errors.append(f"crosswalk must contain exactly {EXPECTED_GOAL_COUNT} rows")
    goal_ids = [str(row.get("goal_id") or "") for row in rows if isinstance(row, dict)]
    if len(goal_ids) != len(rows) or any(not goal_id for goal_id in goal_ids):
        errors.append("every crosswalk row must have a non-empty goal_id")
    if len(goal_ids) != len(set(goal_ids)):
        errors.append("crosswalk goal_ids must be unique")

    annex_ids = {f"AX-{index}" for index in range(1, 6)}
    for row in rows:
        if not isinstance(row, dict):
            errors.append("crosswalk rows must contain objects")
            continue
        goal_id = str(row.get("goal_id") or "<missing>")
        disposition = row.get("disposition")
        refs = row.get("plan_refs")
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append(f"{goal_id}: unknown disposition {disposition!r}")
            continue
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
            errors.append(f"{goal_id}: plan_refs must be a non-empty string list")
            continue
        ref_set = set(refs)
        if disposition == "scored" and not ref_set.issubset(item_ids):
            errors.append(f"{goal_id}: scored refs must name ledger items")
        elif disposition == "conditional":
            if not ref_set.issubset(annex_ids):
                errors.append(f"{goal_id}: conditional refs must name AX-1 through AX-5")
            if row.get("wake_trigger") != "PROC-TRANSFER":
                errors.append(f"{goal_id}: conditional wake_trigger must be PROC-TRANSFER")
        elif disposition == "deferred":
            if not ref_set.intersection(deferred_ids):
                errors.append(f"{goal_id}: deferred refs must name a deferred-register row")
            if not row.get("wake_trigger"):
                errors.append(f"{goal_id}: deferred row must name a wake_trigger")

    validation = crosswalk.get("validation") or {}
    if validation.get("unique_goal_ids") != len(set(goal_ids)):
        errors.append("crosswalk validation.unique_goal_ids is stale")
    if validation.get("unmapped") != []:
        errors.append("crosswalk validation.unmapped must be empty")
    if validation.get("invalid_plan_refs") != []:
        errors.append("crosswalk validation.invalid_plan_refs must be empty")
    if validation.get("unknown_dispositions") != []:
        errors.append("crosswalk validation.unknown_dispositions must be empty")
    return errors, {"rows": len(rows), "unique_goal_ids": len(set(goal_ids))}


def _identity_matches_ref(
    *,
    workspace_root: Path,
    ref: Any,
    identity: Any,
) -> bool:
    path = _resolve_local_ref(workspace_root=workspace_root, ref=ref)
    return (
        path is not None
        and path.is_file()
        and isinstance(identity, str)
        and _sha256_bytes(path.read_bytes()) == identity
    )


def _current_passed_broad_run(
    ledger: Mapping[str, Any],
    *,
    gate: str,
    candidate: str,
    workspace_root: Path,
) -> bool:
    for row in ledger.get("broad_runs") or []:
        if not isinstance(row, dict) or row.get("gate") != gate or row.get("state") != "passed":
            continue
        if row.get("checkout_head") != candidate or row.get("exit_status") != 0:
            continue
        if not isinstance(row.get("operation_nonce"), str) or not row.get("operation_nonce"):
            continue
        if not isinstance(row.get("executor_harness_session_id"), str):
            continue
        if not isinstance(row.get("argv"), (str, list)):
            continue
        if not isinstance(row.get("input_fingerprint"), str) or not row["input_fingerprint"].startswith("sha256:"):
            continue
        if not _identity_matches_ref(
            workspace_root=workspace_root,
            ref=row.get("artifact_ref"),
            identity=row.get("artifact_identity"),
        ):
            continue
        if not _identity_matches_ref(
            workspace_root=workspace_root,
            ref=row.get("start_receipt_ref"),
            identity=row.get("start_receipt_identity"),
        ):
            continue
        start_receipt = _read_local_json_ref(
            workspace_root=workspace_root,
            ref=row.get("start_receipt_ref"),
        )
        if start_receipt is None:
            continue
        if (
            start_receipt.get("operation_nonce") != row.get("operation_nonce")
            or start_receipt.get("gate") != gate
            or _receipt_candidate(start_receipt) != candidate
            or not (start_receipt.get("started") is True or _receipt_success(start_receipt))
        ):
            continue
        return True
    return False


def _derived_author_session_ids(ledger: Mapping[str, Any]) -> list[str]:
    author_keys = {
        "author_session_id",
        "author_session_ids",
        "campaign_author_session_ids",
        "candidate_author_session_ids",
        "plan_author_session_id",
    }
    observed: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in author_keys:
                    values = child if isinstance(child, list) else [child]
                    observed.update(item for item in values if isinstance(item, str) and item)
                elif key not in {"reviews", "plan_audits", "pre_schema_audits"}:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for field in ("candidate", "active_packets", "integration_transactions", "activation_transaction"):
        collect(ledger.get(field))
    return sorted(observed)


def _aud2_input_fingerprint(
    *,
    candidate: str,
    item_by_id: Mapping[str, Mapping[str, Any]],
    author_ids: list[str],
) -> str:
    return _canonical_sha256(
        {
            "candidate": candidate,
            "candidate_author_session_ids": author_ids,
            "items": [
                {
                    "item_id": item_id,
                    "claim": item.get("claim"),
                    "seam": item.get("seam"),
                    "artifact_identity": (item.get("evidence_card") or {}).get("artifact_identity"),
                    "observation_receipt_identity": (item.get("evidence_card") or {}).get(
                        "observation_receipt_identity"
                    ),
                }
                for item_id, item in sorted(item_by_id.items())
            ],
        }
    )

def _aud2_checks_identity(item_checks: Iterable[Any]) -> str:
    material: list[Any] = []
    for row in item_checks:
        if not isinstance(row, dict):
            material.append(row)
            continue
        copied = dict(row)
        copied.pop("falsification_observation_identity", None)
        material.append(copied)
    return _canonical_sha256(material)


def _aud2_is_complete(
    ledger: Mapping[str, Any],
    *,
    candidate: str,
    item_by_id: Mapping[str, Mapping[str, Any]],
    workspace_root: Path,
) -> bool:
    item_ids = set(item_by_id)
    author_ids = _derived_author_session_ids(ledger)
    author_identity = _canonical_sha256(author_ids)
    input_fingerprint = _aud2_input_fingerprint(
        candidate=candidate,
        item_by_id=item_by_id,
        author_ids=author_ids,
    )
    for review in ledger.get("reviews") or []:
        if not isinstance(review, dict) or review.get("decision") != "approved":
            continue
        review_id = str(review.get("review_id") or "")
        if "AUD-2" not in review_id:
            continue
        if review.get("candidate_or_run") != candidate or review.get("non_author_check") is not True:
            continue
        if review.get("candidate_author_session_ids") != author_ids:
            continue
        if review.get("candidate_author_set_identity") != author_identity:
            continue
        reviewer = review.get("reviewer_harness_session_id")
        if not isinstance(reviewer, str) or not reviewer or reviewer in set(author_ids):
            continue
        if review.get("input_fingerprint") != input_fingerprint:
            continue
        if set(review.get("checked_item_ids") or []) != item_ids or review.get("unchecked_item_ids") != []:
            continue
        item_checks = review.get("item_checks")
        if not isinstance(item_checks, list) or len(item_checks) != len(item_ids):
            continue
        checks_by_id = {
            row.get("item_id"): row
            for row in item_checks
            if isinstance(row, dict) and isinstance(row.get("item_id"), str)
        }
        if set(checks_by_id) != item_ids or len(checks_by_id) != len(item_checks):
            continue
        checks_identity = _aud2_checks_identity(item_checks)
        checks_valid = True
        for item_id, check in checks_by_id.items():
            item = item_by_id[item_id]
            card = item.get("evidence_card") or {}
            falsification_observation = _read_local_json_ref(
                workspace_root=workspace_root,
                ref=check.get("falsification_observation_ref"),
            )
            observation_candidate = (
                falsification_observation.get("candidate_commit")
                or falsification_observation.get("candidate")
                if falsification_observation
                else None
            )
            observation_item_ids = (
                set(falsification_observation.get("checked_item_ids") or [])
                if falsification_observation
                else set()
            )
            if not AUD2_ITEM_CHECK_FIELDS.issubset(check):
                checks_valid = False
                break
            if (
                check.get("claim") != item.get("claim")
                or check.get("seam") != item.get("seam")
                or check.get("evidence_card_id") != _canonical_sha256(card)
                or check.get("artifact_identity") != card.get("artifact_identity")
                or check.get("scope_checked") != card.get("declared_scope")
                or check.get("test_double_status") != card.get("test_double_status")
                or check.get("verdict") != "approved"
                or not isinstance(check.get("assigned_dimension"), str)
                or not check.get("assigned_dimension")
                or not isinstance(check.get("falsification_method"), str)
                or not check.get("falsification_method")
                or not isinstance(check.get("observed_result"), str)
                or not check.get("observed_result")
                or not _identity_matches_ref(
                    workspace_root=workspace_root,
                    ref=check.get("falsification_observation_ref"),
                    identity=check.get("falsification_observation_identity"),
                )
                or falsification_observation is None
                or observation_candidate != candidate
                or (
                    falsification_observation.get("item_id") != item_id
                    and item_id not in observation_item_ids
                )
                or falsification_observation.get("decision") != "approved"
                or falsification_observation.get("input_fingerprint") != input_fingerprint
                or falsification_observation.get("findings") != []
                or falsification_observation.get("item_checks_identity") != checks_identity
            ):
                checks_valid = False
                break
        if not checks_valid:
            continue
        if not _identity_matches_ref(
            workspace_root=workspace_root,
            ref=review.get("reviewer_output_ref"),
            identity=review.get("reviewer_output_identity"),
        ):
            continue
        reviewer_output = _read_local_json_ref(
            workspace_root=workspace_root,
            ref=review.get("reviewer_output_ref"),
        )
        if reviewer_output is None:
            continue
        output_candidate = reviewer_output.get("candidate_commit") or reviewer_output.get("candidate")
        if (
            reviewer_output.get("review_id") != review_id
            or reviewer_output.get("reviewer_session_id") != reviewer
            or output_candidate != candidate
            or reviewer_output.get("decision") != "approved"
            or reviewer_output.get("input_fingerprint") != input_fingerprint
            or set(reviewer_output.get("checked_item_ids") or []) != item_ids
            or reviewer_output.get("unchecked_item_ids") != []
            or reviewer_output.get("findings") != []
            or reviewer_output.get("item_checks_identity") != checks_identity
        ):
            continue
        return True
    return False


def _gate_is_current(
    *,
    ledger: Mapping[str, Any],
    gate_name: str,
    gate: Mapping[str, Any],
    candidate: str,
    workspace_root: Path,
) -> bool:
    if gate.get("status") != "passed" or gate.get("candidate") != candidate:
        return False
    if not isinstance(gate.get("input_fingerprint"), str) or not gate["input_fingerprint"].startswith("sha256:"):
        return False
    review_ids = gate.get("review_ids")
    if not isinstance(review_ids, list) or not review_ids:
        return False
    approved_reviews = {
        review.get("review_id")
        for review in ledger.get("reviews") or []
        if isinstance(review, dict)
        and review.get("decision") == "approved"
        and review.get("candidate_or_run") == candidate
    }
    if not set(review_ids).issubset(approved_reviews):
        return False
    card = gate.get("evidence_card")
    if not isinstance(card, dict):
        return False
    for prefix in ("artifact", "observation_receipt"):
        if _identity_error(
            item_id=gate_name,
            field_prefix=prefix,
            card=card,
            workspace_root=workspace_root,
        ):
            return False
    receipt = _read_local_json_ref(
        workspace_root=workspace_root,
        ref=card.get("observation_receipt_ref"),
    )
    return bool(
        receipt
        and receipt.get("gate_id") == gate_name
        and _receipt_candidate(receipt) == candidate
        and receipt.get("input_fingerprint") == gate.get("input_fingerprint")
        and receipt.get("review_ids") == review_ids
        and _receipt_success(receipt)
    )


def _dolt_push_is_current(
    *,
    ledger: Mapping[str, Any],
    candidate: str,
    workspace_root: Path,
) -> bool:
    for row in ledger.get("issue_operations") or []:
        if not isinstance(row, dict) or row.get("kind") != "dolt_push":
            continue
        if (
            row.get("state") not in {"observed", "closed"}
            or row.get("outcome") not in {"success", "passed"}
            or row.get("exit_status") != 0
            or row.get("candidate") != candidate
            or not isinstance(row.get("operation_nonce"), str)
            or not isinstance(row.get("pre_push_identity"), str)
            or not row["pre_push_identity"].startswith("sha256:")
            or not isinstance(row.get("post_push_identity"), str)
            or not row["post_push_identity"].startswith("sha256:")
            or row.get("remote_head") != row.get("post_push_identity")
            or "bd dolt push" not in str(row.get("argv") or "")
        ):
            continue
        if not _identity_matches_ref(
            workspace_root=workspace_root,
            ref=row.get("receipt_ref"),
            identity=row.get("receipt_identity"),
        ):
            continue
        receipt = _read_local_json_ref(
            workspace_root=workspace_root,
            ref=row.get("receipt_ref"),
        )
        if (
            receipt
            and receipt.get("operation_nonce") == row.get("operation_nonce")
            and _receipt_candidate(receipt) == candidate
            and receipt.get("pre_push_identity") == row.get("pre_push_identity")
            and receipt.get("post_push_identity") == row.get("post_push_identity")
            and receipt.get("remote_head") == row.get("post_push_identity")
            and _receipt_success(receipt)
        ):
            return True
    return False


def validate_scorecard(
    *,
    ledger: Mapping[str, Any],
    crosswalk: Mapping[str, Any],
    ledger_path: Path,
    expected_plan_inventory_sha256: str = EXPECTED_PLAN_INVENTORY_SHA256,
    expected_crosswalk_inventory_sha256: str = EXPECTED_CROSSWALK_INVENTORY_SHA256,
) -> dict[str, Any]:
    ledger_material = dict(ledger)
    ledger_material["_ledger_path"] = str(ledger_path.resolve())
    workspace_root = _workspace_root(ledger_path)
    errors: list[str] = []
    blockers: list[str] = []

    if ledger.get("schema_version") != LEDGER_SCHEMA_VERSION:
        errors.append(f"ledger schema_version must be {LEDGER_SCHEMA_VERSION}")
    if ledger.get("plan_revision") != EXPECTED_PLAN_REVISION:
        errors.append(f"ledger plan_revision must be {EXPECTED_PLAN_REVISION}")
    if ledger.get("plan_activated") is not True:
        errors.append("ledger plan_activated must be true")
    if ledger.get("total_points") != EXPECTED_TOTAL_POINTS:
        errors.append(f"ledger total_points must be {EXPECTED_TOTAL_POINTS}")

    tracks = ledger.get("tracks")
    if not isinstance(tracks, list):
        tracks = []
        errors.append("ledger tracks must be a list")
    observed_track_names = [track.get("track") for track in tracks if isinstance(track, dict)]
    if set(observed_track_names) != set(EXPECTED_TRACKS) or len(observed_track_names) != len(EXPECTED_TRACKS):
        errors.append("ledger tracks must contain each expected track exactly once")
    plan_inventory, plan_error = _plan_inventory(
        ledger=ledger,
        workspace_root=workspace_root,
    )
    if plan_error:
        errors.append(plan_error)
    plan_inventory_identity = _canonical_sha256(plan_inventory)
    if plan_inventory_identity != expected_plan_inventory_sha256:
        errors.append("canonical plan scored-item inventory identity is not approved")
    ledger_inventory = _ledger_inventory(tracks)
    if ledger_inventory != plan_inventory:
        errors.append("ledger scored-item inventory does not match the canonical plan")

    items: list[dict[str, Any]] = []
    for track in tracks:
        if not isinstance(track, dict):
            errors.append("ledger tracks must contain objects")
            continue
        track_name = str(track.get("track") or "")
        track_items = track.get("items")
        if not isinstance(track_items, list):
            errors.append(f"track {track_name}: items must be a list")
            continue
        items.extend(item for item in track_items if isinstance(item, dict))
        expected = EXPECTED_TRACKS.get(track_name)
        if expected is None:
            continue
        expected_points, expected_count = expected
        item_points = sum(item.get("points", 0) for item in track_items if isinstance(item, dict) and isinstance(item.get("points"), int))
        earned_points = sum(
            item.get("points", 0)
            for item in track_items
            if isinstance(item, dict) and item.get("state") == "earned" and isinstance(item.get("points"), int)
        )
        if track.get("points") != expected_points or item_points != expected_points:
            errors.append(f"track {track_name}: points must sum to {expected_points}")
        if len(track_items) != expected_count:
            errors.append(f"track {track_name}: must contain {expected_count} items")
        if track.get("earned_points") != earned_points:
            errors.append(f"track {track_name}: earned_points does not match earned item sum")

    item_ids = [str(item.get("id") or "") for item in items]
    item_id_set = set(item_ids)
    if len(items) != EXPECTED_ITEM_COUNT:
        errors.append(f"ledger must contain exactly {EXPECTED_ITEM_COUNT} scored items")
    if any(not item_id for item_id in item_ids) or len(item_ids) != len(item_id_set):
        errors.append("ledger item ids must be non-empty and unique")
    if sum(item.get("points", 0) for item in items if isinstance(item.get("points"), int)) != EXPECTED_TOTAL_POINTS:
        errors.append(f"scored item weights must total {EXPECTED_TOTAL_POINTS}")
    item_by_id = {
        str(item.get("id")): item
        for item in items
        if isinstance(item.get("id"), str) and item.get("id")
    }

    earned_items = [item for item in items if item.get("state") == "earned"]
    earned_points = sum(item.get("points", 0) for item in earned_items if isinstance(item.get("points"), int))
    if ledger.get("earned_points") != earned_points:
        errors.append("ledger earned_points does not match earned item sum")

    required_card_fields = set(
        (((ledger.get("record_schemas") or {}).get("evidence_card") or {}).get("required") or DEFAULT_CARD_FIELDS)
    )
    for item in earned_items:
        item_id = str(item.get("id") or "<missing>")
        card = item.get("evidence_card")
        if not isinstance(card, dict):
            errors.append(f"{item_id}: earned item must have an evidence_card")
            continue
        missing = required_card_fields - set(card)
        if missing:
            errors.append(f"{item_id}: evidence_card missing fields {sorted(missing)}")
        if card.get("item_id") != item_id:
            errors.append(f"{item_id}: evidence_card item_id mismatch")
        if card.get("claim") != item.get("claim"):
            errors.append(f"{item_id}: evidence_card claim mismatch")
        if card.get("seam") != item.get("seam"):
            errors.append(f"{item_id}: evidence_card seam mismatch")
        if card.get("support_level") != item.get("required_support_level", "observed"):
            errors.append(f"{item_id}: evidence_card support_level is not sufficient")
        observation_kind = card.get("observation_kind")
        allowed_kinds = set(item.get("allowed_observation_kinds") or ALLOWED_OBSERVATION_KINDS)
        if observation_kind not in ALLOWED_OBSERVATION_KINDS or observation_kind not in allowed_kinds:
            errors.append(f"{item_id}: evidence_card observation_kind is not allowed")
        if card.get("test_double_status") not in ALLOWED_TEST_DOUBLE_STATUSES:
            errors.append(f"{item_id}: evidence_card test_double_status is invalid")
        if not isinstance(card.get("candidate_or_run"), str) or not card.get("candidate_or_run"):
            errors.append(f"{item_id}: evidence_card must name its exact candidate or run")
        for field_prefix in ("artifact", "observation_receipt"):
            identity_error = _identity_error(
                item_id=item_id,
                field_prefix=field_prefix,
                card=card,
                workspace_root=workspace_root,
            )
            if identity_error:
                errors.append(identity_error)
        receipt_error = _evidence_receipt_error(
            item=item,
            card=card,
            workspace_root=workspace_root,
        )
        if receipt_error:
            errors.append(receipt_error)

    deferred_ids = {
        str(row.get("deferred_id") or row.get("id") or "")
        for row in ledger.get("deferred_register") or []
        if isinstance(row, dict)
    }
    crosswalk_errors, crosswalk_counts = _validate_crosswalk(
        crosswalk=crosswalk,
        item_ids=item_id_set,
        deferred_ids=deferred_ids,
    )
    errors.extend(crosswalk_errors)
    crosswalk_inventory_identity = _canonical_sha256(_crosswalk_inventory(crosswalk))
    if crosswalk_inventory_identity != expected_crosswalk_inventory_sha256:
        errors.append("crosswalk goal inventory identity is not approved")

    candidate = str((ledger.get("candidate") or {}).get("current_head") or "")
    if earned_points != EXPECTED_TOTAL_POINTS:
        blockers.append("earned_points must equal 1000 for completion")
    if len(earned_items) != len(items):
        blockers.append("every scored item must be earned for completion")
    for gate_name in EXPECTED_GATES:
        gate = (ledger.get("gates") or {}).get(gate_name) or {}
        if not isinstance(gate, dict) or not _gate_is_current(
            ledger=ledger,
            gate_name=gate_name,
            gate=gate,
            candidate=candidate,
            workspace_root=workspace_root,
        ):
            blockers.append(f"gate {gate_name} must have a current receipt bound to exact inputs")
    open_reds = [
        str(red.get("red_id") or "<missing>")
        for red in ledger.get("reds") or []
        if isinstance(red, dict) and red.get("state") in {"open", "blocked"}
    ]
    if open_reds:
        blockers.append("reds must contain no open or blocked rows: " + ", ".join(sorted(open_reds)))
    if (ledger.get("candidate") or {}).get("merged_main_head") != candidate or not candidate:
        blockers.append("merged_main_head must equal the current candidate")
    if not _aud2_is_complete(
        ledger_material,
        candidate=candidate,
        item_by_id=item_by_id,
        workspace_root=workspace_root,
    ):
        blockers.append("AUD-2 must be an exact-candidate independent review covering every scored item")
    for gate_name in ("G3", "G4"):
        if not _current_passed_broad_run(
            ledger,
            gate=gate_name,
            candidate=candidate,
            workspace_root=workspace_root,
        ):
            blockers.append(f"a receipt-bound current passed broad run must bind {gate_name}")

    mapped_item_ids: set[str] = set()
    issue_rows = (ledger.get("bd_issue_map") or {}).get("packets") or []
    issue_rows_closed = True
    for row in issue_rows:
        if not isinstance(row, dict):
            issue_rows_closed = False
            continue
        mapped_item_ids.update(str(item_id) for item_id in row.get("item_ids") or [])
        if row.get("state") != "closed":
            issue_rows_closed = False
    if mapped_item_ids != item_id_set or not issue_rows_closed:
        blockers.append("bd_issue_map must cover every scored item with closed mapped issues")
    if not _dolt_push_is_current(
        ledger=ledger,
        candidate=candidate,
        workspace_root=workspace_root,
    ):
        blockers.append("a receipt-bound successful bd dolt push must match the current candidate")
    if any(
        isinstance(row, dict) and row.get("state") not in {"closed", "completed"}
        for row in ledger.get("active_packets") or []
    ):
        blockers.append("all active packet records must be terminal")
    if any(
        isinstance(row, dict) and row.get("state") not in {"committed", "aborted"}
        for row in ledger.get("integration_transactions") or []
    ):
        blockers.append("all integration transactions must be terminal")
    control_panel = ledger.get("control_panel") or {}
    if any(value not in {None, "", "none", "closed"} for value in control_panel.values()):
        blockers.append("control panel must be closed")
    if ledger.get("campaign_state") not in {"FINAL_AUDIT", "COMPLETE"}:
        blockers.append("campaign_state must be FINAL_AUDIT or COMPLETE")

    fingerprint_payload = {
        "ledger": _without_validator_observed_result(ledger),
        "crosswalk": crosswalk,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "candidate": candidate or None,
        "valid": not errors,
        "ready": not errors and not blockers,
        "input_fingerprint": _canonical_sha256(fingerprint_payload),
        "canonical_identities": {
            "plan_inventory": plan_inventory_identity,
            "crosswalk_inventory": crosswalk_inventory_identity,
        },
        "counts": {
            "total_points": ledger.get("total_points"),
            "earned_points": earned_points,
            "item_count": len(items),
            "earned_item_count": len(earned_items),
            "crosswalk_goal_count": crosswalk_counts["rows"],
            "crosswalk_unique_goal_ids": crosswalk_counts["unique_goal_ids"],
            "open_or_blocked_red_count": len(open_reds),
        },
        "earned_item_ids": sorted(item.get("id") for item in earned_items),
        "errors": sorted(set(errors)),
        "blockers": sorted(set(blockers)),
    }
    return report


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the NS-FINAL scorecard ledger and 102-goal crosswalk.")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--crosswalk", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="Print the machine-readable validation report.")
    parser.add_argument("--require-ready", action="store_true", help="Exit non-zero unless every completion invariant passes.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        ledger = _read_json(args.ledger)
        crosswalk = _read_json(args.crosswalk)
        report = validate_scorecard(ledger=ledger, crosswalk=crosswalk, ledger_path=args.ledger)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "candidate": None,
            "valid": False,
            "ready": False,
            "input_fingerprint": None,
            "counts": {},
            "earned_item_ids": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
            "blockers": [],
        }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"valid={str(report['valid']).lower()} ready={str(report['ready']).lower()} "
            f"earned_points={report.get('counts', {}).get('earned_points')} "
            f"errors={len(report['errors'])} blockers={len(report['blockers'])}"
        )
        for error in report["errors"]:
            print(f"error={error}")
        for blocker in report["blockers"]:
            print(f"blocker={blocker}")
    if not report["valid"]:
        return 2
    if args.require_ready and not report["ready"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
