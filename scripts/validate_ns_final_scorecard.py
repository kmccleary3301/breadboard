from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "bb.ns_final.scorecard_validation.v1"
LEDGER_SCHEMA_VERSION = "bb.ns_final_progress.v5"
CROSSWALK_SCHEMA_VERSION = "bb.ns_final_goal_crosswalk.v2"
EXPECTED_PLAN_REVISION = 2
EXPECTED_TOTAL_POINTS = 1000
EXPECTED_ITEM_COUNT = 93
EXPECTED_GOAL_COUNT = 102
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


def _current_passed_broad_run(ledger: Mapping[str, Any], *, gate: str, candidate: str) -> bool:
    for row in ledger.get("broad_runs") or []:
        if not isinstance(row, dict) or row.get("gate") != gate or row.get("state") != "passed":
            continue
        observed_head = row.get("checkout_head") or row.get("candidate") or row.get("head")
        if observed_head == candidate:
            return True
    return False


def _aud2_is_complete(ledger: Mapping[str, Any], *, candidate: str, item_ids: set[str]) -> bool:
    workspace_root = _workspace_root(Path(str(ledger.get("_ledger_path") or ".")))
    for review in ledger.get("reviews") or []:
        if not isinstance(review, dict) or review.get("decision") != "approved":
            continue
        if "AUD-2" not in str(review.get("review_id") or ""):
            continue
        if review.get("candidate_or_run") != candidate or review.get("non_author_check") is not True:
            continue
        if set(review.get("checked_item_ids") or []) != item_ids or review.get("unchecked_item_ids") != []:
            continue
        if {row.get("item_id") for row in review.get("item_checks") or [] if isinstance(row, dict)} != item_ids:
            continue
        reviewer = review.get("reviewer_harness_session_id")
        if reviewer in set(review.get("candidate_author_session_ids") or []):
            continue
        path = _resolve_local_ref(workspace_root=workspace_root, ref=review.get("reviewer_output_ref"))
        if path is None or not path.is_file() or _sha256_bytes(path.read_bytes()) != review.get("reviewer_output_identity"):
            continue
        return True
    return False


def validate_scorecard(
    *,
    ledger: Mapping[str, Any],
    crosswalk: Mapping[str, Any],
    ledger_path: Path,
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
        candidate = str((ledger.get("candidate") or {}).get("current_head") or "")
        if observation_kind == "local" and candidate not in str(card.get("candidate_or_run") or ""):
            errors.append(f"{item_id}: local evidence_card is not bound to current candidate")
        for field_prefix in ("artifact", "observation_receipt"):
            identity_error = _identity_error(
                item_id=item_id,
                field_prefix=field_prefix,
                card=card,
                workspace_root=workspace_root,
            )
            if identity_error:
                errors.append(identity_error)

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

    candidate = str((ledger.get("candidate") or {}).get("current_head") or "")
    if earned_points != EXPECTED_TOTAL_POINTS:
        blockers.append("earned_points must equal 1000 for completion")
    if len(earned_items) != len(items):
        blockers.append("every scored item must be earned for completion")
    for gate_name in EXPECTED_GATES:
        gate = (ledger.get("gates") or {}).get(gate_name) or {}
        if gate.get("status") != "passed" or gate.get("candidate") != candidate:
            blockers.append(f"gate {gate_name} must be passed on the current candidate")
    open_reds = [
        str(red.get("red_id") or "<missing>")
        for red in ledger.get("reds") or []
        if isinstance(red, dict) and red.get("state") in {"open", "blocked"}
    ]
    if open_reds:
        blockers.append("reds must contain no open or blocked rows: " + ", ".join(sorted(open_reds)))
    if (ledger.get("candidate") or {}).get("merged_main_head") != candidate or not candidate:
        blockers.append("merged_main_head must equal the current candidate")
    if not _aud2_is_complete(ledger_material, candidate=candidate, item_ids=item_id_set):
        blockers.append("AUD-2 must be an exact-candidate independent review covering every scored item")
    for gate_name in ("G3", "G4"):
        if not _current_passed_broad_run(ledger, gate=gate_name, candidate=candidate):
            blockers.append(f"a current passed broad run must bind {gate_name}")

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
    dolt_push_green = any(
        isinstance(row, dict)
        and row.get("kind") == "dolt_push"
        and row.get("state") in {"observed", "closed"}
        and row.get("outcome") in {"success", "passed"}
        and row.get("exit_status") in {None, 0}
        for row in ledger.get("issue_operations") or []
    )
    if not dolt_push_green:
        blockers.append("an observed successful bd dolt push operation is required")
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
