from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
SUPERSEDED_REASON = "SUPERSEDED BY V2 — NOT COMPLETED"
_BEADS_CUTOVER_RULES = (
    "resolve stable packet keys to actual issue IDs after SPEC_FREEZE",
    "validate mapping/dependencies before superseding legacy",
    "leave closed legacy issues closed",
    "close open/in-progress as SUPERSEDED BY V2 — NOT COMPLETED",
    "close bb-auh as superseded after all 67 map",
    "Beads cannot admit evidence, consume attempts, award score, or grant authority",
)
_LEGACY_PARENT_RESOLUTION_KEYS = {
    "after_child_resolution_count",
    "before_record_sha256",
    "before_status",
    "close_reason",
    "disposition",
    "issue_id",
    "projected_status",
}
REVISION_ID = "v2.0.0-rc5-20260717"
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

_PACKET_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "AT0": (),
    "AT1": ("AT0",),
    "AT2": ("AT1",),
    "AT3": ("AT2",),
    "AT4": ("AT3",),
    "SHARED_TRANSPORT": ("AT4",),
    "TRAINING_PROOF": ("AT4", "SHARED_TRANSPORT"),
    "AT5_F10": ("AT4",),
    "AT5_G4": ("AT4", "AT5_F10"),
    "AT6_F1_D5": ("AT4", "SHARED_TRANSPORT"),
    "AT6_F2": ("AT6_F1_D5",),
    "AT6_F3": ("AT6_F2",),
    "AT6_F4": ("AT6_F3",),
    "AT6_F6": ("AT6_F2",),
    "AT6_F5": ("AT5_G4", "AT6_F4", "AT6_F6"),
    "AT7_F8_F9": ("AT4", "AT6_F2", "SHARED_TRANSPORT"),
    "AT7_F7_TWO_NODE": (
        "AT6_F1_D5",
        "AT6_F2",
        "AT6_F3",
        "AT6_F4",
        "AT6_F5",
        "AT6_F6",
        "SHARED_TRANSPORT",
    ),
    "AT7_F7_FOUR_NODE": ("AT7_F7_TWO_NODE",),
    "AT8_G2_G3": (
        "AT5_F10",
        "AT5_G4",
        "AT7_F7_FOUR_NODE",
        "AT7_F8_F9",
        "TRAINING_PROOF",
    ),
    "AT8_H3": ("AT8_G2_G3",),
    "AT8_H1": ("AT8_H3",),
    "AT8_H2": ("AT8_H1",),
    "AT8_H4": ("AT8_H2",),
}
_ASSURANCE_ITEMS = frozenset(
    {
        "A1", "A2", "A3", "A4", "A5",
        "B1", "B2", "B3", "B4", "B5", "B6", "B7",
        "C1", "C2", "C3", "C4", "C5", "C6",
        "D1", "D2", "D3", "D4", "D5", "D6", "D7",
        "E1", "E2", "E3", "E4", "E5", "E6",
        "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10",
        "G1", "G2", "G3", "G4",
        "H1", "H2", "H3", "H4",
    }
)
_DRAFT_QUEUE_KEYS = {
    "blocked",
    "eligible",
    "escalated",
    "generation",
    "program_id",
    "schema_version",
    "state",
    "target_lease",
    "waiting_external",
    "waiting_human",
}
_DRAFT_STATUS_KEYS = {
    "active",
    "active_attempt",
    "active_packet",
    "allowed_next",
    "checkpoint_disposition",
    "event_cursor",
    "external_acceptance",
    "generation",
    "historical_unresolved",
    "internal_completion",
    "nonclaims",
    "program_id",
    "program_state",
    "promotion",
    "revision_id",
    "schema_version",
    "shared_transport",
    "target_lease",
    "tracks",
}


def _canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("projection input must contain only JSON values") from exc
    return (encoded + "\n").encode()


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a plain dictionary")
    _canonical_bytes(value)
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a sha256:<hex> digest")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _sorted_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: _canonical_bytes(row))


def _require_identity(document: Mapping[str, Any], name: str) -> None:
    if document.get("program_id") != PROGRAM_ID:
        raise ValueError(f"{name}.program_id does not match the frozen program")

def _require_exact_keys(
    document: Mapping[str, Any], expected: set[str], name: str
) -> None:
    if set(document) != expected:
        missing = sorted(expected - set(document))
        unexpected = sorted(set(document) - expected)
        raise ValueError(
            f"{name} fields do not match the frozen schema; "
            f"missing={missing}, unexpected={unexpected}"
        )

def _validate_status_fields(
    status: dict[str, Any], name: str, *, prepared: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = set(_DRAFT_STATUS_KEYS)
    if not prepared:
        expected.add("candidate_authority")
    if prepared:
        expected.update({"migration_id", "next_local_packet", "spec_freeze_sha256"})
    _require_exact_keys(status, expected, name)
    _require_exact_keys(
        _object(status.get("external_acceptance"), f"{name}.external_acceptance"),
        {"authority", "state"},
        f"{name}.external_acceptance",
    )
    _require_exact_keys(
        _object(status.get("promotion"), f"{name}.promotion"),
        {"authorized", "state"},
        f"{name}.promotion",
    )
    _require_exact_keys(
        _object(status.get("shared_transport"), f"{name}.shared_transport"),
        {"admitted_hash", "smoke_job", "state"},
        f"{name}.shared_transport",
    )
    _require_exact_keys(
        _object(status.get("historical_unresolved"), f"{name}.historical_unresolved"),
        {
            "F2_r29_cleanup",
            "F3_r50_cleanup",
            "F3_r50_submission",
            "F4_r9_cleanup",
        },
        f"{name}.historical_unresolved",
    )
    nonclaims = _list(status.get("nonclaims"), f"{name}.nonclaims")
    if any(not isinstance(item, str) or not item for item in nonclaims):
        raise ValueError(f"{name}.nonclaims must contain only non-empty strings")
    tracks = _object(status.get("tracks"), f"{name}.tracks")
    _require_exact_keys(tracks, {"assurance", "training_proof"}, f"{name}.tracks")
    assurance = _object(tracks.get("assurance"), f"{name}.tracks.assurance")
    _require_exact_keys(
        assurance,
        {
            "awarded_items",
            "catalog_points",
            "current_verified_points",
            "evidence_ref_count",
            "item_count",
            "pending_items",
            "review_ref_count",
            "state",
        },
        f"{name}.tracks.assurance",
    )
    training = _object(
        tracks.get("training_proof"), f"{name}.tracks.training_proof"
    )
    _require_exact_keys(
        training,
        {
            "completion_decision",
            "evidence_root",
            "satisfied",
            "score_field_present",
            "state",
        },
        f"{name}.tracks.training_proof",
    )
    return assurance, training


def _migration_identifier(value: Any) -> str:
    identifier = _string(value, "migration_id")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", identifier) is None:
        raise ValueError("migration_id must be one safe relative-path segment")
    return identifier


def _validate_migration_binding(migration_id: str, spec_freeze_sha256: str) -> None:
    _migration_identifier(migration_id)
    _digest(spec_freeze_sha256, "spec_freeze_sha256")


def validate_spec_freeze_decision(
    decision: dict[str, Any], *, artifact_sha256: str
) -> None:
    """Validate the exact RC5 human decision that grants preparation authority only."""

    _digest(artifact_sha256, "spec_freeze_decision_sha256")
    _require_exact_keys(
        decision,
        {
            "authority_after_decision",
            "candidate",
            "decision",
            "decision_maker",
            "decision_packet_sha256",
            "decision_scope",
            "forbidden_without_separate_authority",
            "next_required_gate",
            "prior_rc4_spec_freeze_grants_rc5_authority",
            "program_id",
            "root_active_selector_mutated",
            "schema_version",
            "zero_runtime_authority",
        },
        "spec_freeze_decision",
    )
    authority = _object(
        decision.get("authority_after_decision"),
        "spec_freeze_decision.authority_after_decision",
    )
    if authority != {
        "active": False,
        "checkpoint": False,
        "completion": False,
        "external_acceptance": False,
        "migration_cutover": False,
        "migration_preparation": True,
        "promotion": False,
        "quiescence": False,
        "score": False,
        "selector_mutation": False,
        "spec_freeze": True,
        "target_execution": False,
    }:
        raise ValueError("RC5 SPEC_FREEZE authority scope changed")
    if decision.get("candidate") != {
        "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "revision_id": REVISION_ID,
    }:
        raise ValueError("RC5 SPEC_FREEZE candidate binding changed")
    expected = {
        "decision": "SPEC_FREEZE",
        "decision_maker": "Kyle McCleary",
        "decision_packet_sha256": (
            "sha256:ef0bb7cd29e83fa9219bcd1e820e375e9d46a63f897efc0065e45ffcac5876de"
        ),
        "decision_scope": (
            "Freeze only the exact RC5 specification bytes and authorize preparation "
            "of an exact migration bundle under those frozen contracts."
        ),
        "forbidden_without_separate_authority": [
            "quiescence",
            "migration_cutover",
            "store_mutation",
            "root_selector_mutation",
            "target_or_ibm_execution",
            "score_or_completion_award",
            "checkpoint_or_promotion",
            "external_acceptance",
        ],
        "next_required_gate": (
            "Validate and independently review the exact prepared migration bundle, "
            "require fresh RC5-bound runtime evidence, then obtain a separate typed "
            "MIGRATION_CUTOVER decision before quiescence or mutation."
        ),
        "prior_rc4_spec_freeze_grants_rc5_authority": False,
        "program_id": PROGRAM_ID,
        "root_active_selector_mutated": False,
        "schema_version": "bb.rl.phase5.spec_freeze_decision.v1",
        "zero_runtime_authority": True,
    }
    if any(decision.get(key) != value for key, value in expected.items()):
        raise ValueError("RC5 SPEC_FREEZE decision identity or bounds changed")


def _validate_packet_rows(rows: list[Any]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(rows):
        row = _object(value, f"frozen_queue.blocked[{index}]")
        _require_exact_keys(
            row, {"depends_on", "packet_key", "reason"}, f"frozen_queue.blocked[{index}]"
        )
        key = _string(row.get("packet_key"), f"frozen_queue.blocked[{index}].packet_key")
        if key in by_key:
            raise ValueError(f"duplicate frozen packet {key}")
        dependencies = _list(
            row.get("depends_on"), f"frozen_queue.blocked[{index}].depends_on"
        )
        if any(not isinstance(item, str) for item in dependencies):
            raise ValueError(f"dependencies for {key} must be strings")
        _string(row.get("reason"), f"frozen_queue.blocked[{index}].reason")
        expected_reason = "v2 inactive" if key == "AT0" else "dependency not current"
        if row["reason"] != expected_reason:
            raise ValueError(f"frozen reason for {key} does not match rc5")
        by_key[key] = row
    if set(by_key) != set(_PACKET_DEPENDENCIES):
        missing = sorted(set(_PACKET_DEPENDENCIES) - set(by_key))
        unexpected = sorted(set(by_key) - set(_PACKET_DEPENDENCIES))
        raise ValueError(
            f"frozen queue packet set mismatch; missing={missing}, unexpected={unexpected}"
        )
    for key, expected in _PACKET_DEPENDENCIES.items():
        actual = tuple(sorted(by_key[key]["depends_on"]))
        if actual != tuple(sorted(expected)):
            raise ValueError(f"frozen dependencies for {key} do not match the rc5 DAG")
    return by_key


def derive_run_queue(
    frozen_queue: dict[str, Any],
    *,
    migration_id: str,
    spec_freeze_sha256: str,
) -> dict[str, Any]:
    """Prepare the generation-one local-only queue without mutating its draft."""

    frozen = _object(frozen_queue, "frozen_queue")
    _validate_migration_binding(migration_id, spec_freeze_sha256)
    _require_identity(frozen, "frozen_queue")
    _require_exact_keys(frozen, _DRAFT_QUEUE_KEYS, "frozen_queue")
    if frozen.get("schema_version") != "bb.rl.phase5.run_queue.v2":
        raise ValueError("frozen_queue.schema_version is not the rc5 run-queue schema")
    if frozen.get("state") != "DRAFT_WAITING_SPEC_FREEZE":
        raise ValueError("frozen queue is not waiting for SPEC_FREEZE")
    if isinstance(frozen.get("generation"), bool) or frozen.get("generation") != 0:
        raise ValueError("frozen queue generation must be zero")
    if frozen.get("target_lease") is not None:
        raise ValueError("frozen queue already has a target lease")
    if _list(frozen.get("eligible"), "frozen_queue.eligible"):
        raise ValueError("frozen queue is already executable")
    for key in ("escalated", "waiting_external"):
        if _list(frozen.get(key), f"frozen_queue.{key}"):
            raise ValueError(f"frozen_queue.{key} must be empty")

    blocked_by_key = _validate_packet_rows(
        _list(frozen.get("blocked"), "frozen_queue.blocked")
    )
    waiting_human = _list(
        frozen.get("waiting_human"), "frozen_queue.waiting_human"
    )
    if len(waiting_human) != 1:
        raise ValueError("frozen queue must contain exactly one V2_ACTIVATION wait")
    activation = _object(waiting_human[0], "frozen_queue.waiting_human[0]")
    _require_exact_keys(
        activation,
        {"packet_key", "reason", "wake_condition"},
        "frozen_queue.waiting_human[0]",
    )
    if activation != {
        "packet_key": "V2_ACTIVATION",
        "reason": "SPEC_FREEZE not issued",
        "wake_condition": (
            "Kyle approves the reviewed immutable candidate for local-only migration"
        ),
    }:
        raise ValueError("frozen V2_ACTIVATION wait does not match rc5")

    prepared = copy.deepcopy(frozen)
    prepared["blocked"] = []
    prepared["blocked"] = _sorted_rows(
        copy.deepcopy(blocked_by_key[key])
        for key in blocked_by_key
        if key != "AT0"
    )
    prepared["eligible"] = [
        {
            "kind": "local",
            "packet_key": "AT0",
            "reason": "SPEC_FREEZE and migration cutover complete",
        }
    ]
    prepared["waiting_human"] = []
    for key in ("escalated", "waiting_external"):
        prepared[key] = _sorted_rows(
            copy.deepcopy(row) for row in _list(frozen.get(key), f"frozen_queue.{key}")
        )
    prepared.update(
        {
            "generation": 1,
            "migration_id": migration_id,
            "spec_freeze_sha256": spec_freeze_sha256,
            "state": "READY_FOR_LOCAL_MIGRATION_WORK",
            "target_lease": None,
        }
    )
    validate_zero_authority(prepared)
    return prepared


def derive_active_status(
    draft_status: dict[str, Any],
    *,
    migration_id: str,
    spec_freeze_sha256: str,
) -> dict[str, Any]:
    """Prepare the active local-only status reached by the two activation events."""

    draft = _object(draft_status, "draft_status")
    _validate_migration_binding(migration_id, spec_freeze_sha256)
    _require_identity(draft, "draft_status")
    assurance, training = _validate_status_fields(
        draft, "draft_status", prepared=False
    )
    if draft.get("schema_version") != "bb.rl.phase5.active_status.v4":
        raise ValueError("draft_status.schema_version is not the rc5 active-status schema")
    if draft.get("revision_id") != REVISION_ID:
        raise ValueError("draft status revision is not the frozen rc5 revision")
    if draft.get("program_state") != "DRAFT_WAITING_RC5_SPEC_FREEZE":
        raise ValueError("draft status is not waiting for RC5 SPEC_FREEZE")
    if (
        isinstance(draft.get("generation"), bool)
        or isinstance(draft.get("event_cursor"), bool)
        or draft.get("generation") != 0
        or draft.get("event_cursor") != 0
    ):
        raise ValueError("draft status generation and event cursor must be zero")
    if draft.get("active") is not False:
        raise ValueError("draft status is already active")
    if draft.get("target_lease") is not None:
        raise ValueError("draft status already has a target lease")
    if (
        draft.get("active_attempt") is not None
        or draft.get("active_packet") is not None
        or draft.get("checkpoint_disposition") != "unclaimed"
        or draft.get("internal_completion") is not False
    ):
        raise ValueError("draft status already carries result state")
    if draft.get("candidate_authority") != {
        "prior_rc4_spec_freeze_applies": False,
        "required": "new exact rc5 SPEC_FREEZE",
        "superseded_artifact_manifest_sha256": (
            "sha256:b5897c0465bfb0cdf4b3aa79427c55e85b8a1d0b600c40e6d6eb62b579e9cbfd"
        ),
        "superseded_revision_id": "v2.0.0-rc4-20260715",
    }:
        raise ValueError("draft candidate authority does not match rc5")
    if draft.get("external_acceptance") != {
        "authority": "Zyphra only",
        "state": "unclaimed",
    }:
        raise ValueError("draft external acceptance does not match rc5")
    if draft.get("promotion") != {"authorized": False, "state": "unclaimed"}:
        raise ValueError("draft promotion does not match rc5")
    if draft.get("shared_transport") != {
        "admitted_hash": None,
        "smoke_job": None,
        "state": "blocked",
    }:
        raise ValueError("draft shared transport does not match rc5")
    if draft.get("historical_unresolved") != {
        "F2_r29_cleanup": "unknown",
        "F3_r50_cleanup": "unknown",
        "F3_r50_submission": "unknown",
        "F4_r9_cleanup": "unknown",
    }:
        raise ValueError("draft historical-unresolved fields do not match rc5")

    pending_items = _list(
        assurance.get("pending_items"),
        "draft_status.tracks.assurance.pending_items",
    )
    if set(pending_items) != _ASSURANCE_ITEMS or len(pending_items) != 49:
        raise ValueError("assurance pending items do not match the 49-row rc5 catalog")
    if (
        assurance.get("catalog_points") != 1000
        or assurance.get("item_count") != 49
        or assurance.get("state") != "PENDING_AT0"
    ):
        raise ValueError("draft Assurance catalog metadata does not match rc5")
    if training != {
        "completion_decision": None,
        "evidence_root": None,
        "satisfied": False,
        "score_field_present": False,
        "state": "BLOCKED_SHARED_TRANSPORT",
    }:
        raise ValueError("draft Training Proof state does not match rc5")
    validate_zero_authority(draft)

    prepared = copy.deepcopy(draft)
    prepared.pop("candidate_authority")
    prepared.update(
        {
            "active": True,
            "active_attempt": None,
            "active_packet": None,
            "allowed_next": "AT0",
            "event_cursor": 2,
            "generation": 1,
            "migration_id": migration_id,
            "next_local_packet": "AT0",
            "program_state": "READY_FOR_LOCAL_MIGRATION_WORK",
            "spec_freeze_sha256": spec_freeze_sha256,
            "schema_version": "bb.rl.phase5.active_status.v5",
            "target_lease": None,
        }
    )
    prepared["checkpoint_disposition"] = "unclaimed"
    prepared["external_acceptance"] = {
        "authority": "Zyphra only",
        "state": "unclaimed",
    }
    prepared["internal_completion"] = False
    prepared["nonclaims"] = sorted(
        {
            "local migration preparation grants no target execution",
            "no evidence admission",
            "no external acceptance",
            "no IBM admission",
            "no promotion",
            "no score",
            "no track completion",
        }
    )
    prepared["promotion"] = {"authorized": False, "state": "unclaimed"}
    prepared["shared_transport"] = {
        "admitted_hash": None,
        "smoke_job": None,
        "state": "blocked",
    }
    prepared["tracks"]["assurance"].update(
        {
            "awarded_items": [],
            "current_verified_points": 0,
            "evidence_ref_count": 0,
            "pending_items": sorted(pending_items),
            "review_ref_count": 0,
            "state": "PENDING_AT0",
        }
    )
    prepared["tracks"]["training_proof"].update(
        {
            "completion_decision": None,
            "evidence_root": None,
            "satisfied": False,
            "score_field_present": False,
            "state": "BLOCKED_SHARED_TRANSPORT",
        }
    )
    validate_zero_authority(prepared)
    return prepared


def _legacy_suffix(issue_id: str) -> int:
    prefix, separator, suffix = issue_id.partition(".")
    if prefix != "bb-auh" or separator != "." or not suffix.isdigit():
        raise ValueError(f"invalid legacy child issue ID {issue_id!r}")
    return int(suffix)


def _live_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)):
        raw_lines = value.splitlines()
        rows: list[dict[str, Any]] = []
        for line_number, raw_line in enumerate(raw_lines, 1):
            if not raw_line:
                continue
            try:
                decoded = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"invalid live Beads JSONL row {line_number}") from exc
            rows.append(_object(decoded, f"live_beads_rows[{line_number - 1}]"))
        return rows
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("live_beads_rows must be JSONL bytes/text or a sequence of rows")
    return [
        _object(row, f"live_beads_rows[{index}]")
        for index, row in enumerate(value)
    ]


def validate_beads_projection(projection: dict[str, Any]) -> None:
    """Validate the closed, ordered, preparation-only Beads projection schema."""

    document = _object(projection, "beads_projection")
    _require_exact_keys(
        document,
        {
            "legacy_parent_resolution",
            "legacy_resolutions",
            "migration_id",
            "program_id",
            "schema_version",
            "source_snapshot_sha256",
            "spec_freeze_sha256",
            "successor_epic",
            "successor_issues",
        },
        "beads_projection",
    )
    _require_identity(document, "beads_projection")
    if document.get("schema_version") != "bb.rl.phase5.beads_resolution.v1":
        raise ValueError("beads_projection.schema_version is not supported")
    _validate_migration_binding(
        document.get("migration_id"), document.get("spec_freeze_sha256")
    )
    _digest(
        document.get("source_snapshot_sha256"),
        "beads_projection.source_snapshot_sha256",
    )

    resolutions = _list(
        document.get("legacy_resolutions"), "beads_projection.legacy_resolutions"
    )
    if len(resolutions) != 67:
        raise ValueError("beads_projection must contain exactly 67 child resolutions")
    for index, value in enumerate(resolutions):
        resolution = _object(
            value, f"beads_projection.legacy_resolutions[{index}]"
        )
        _require_exact_keys(
            resolution,
            {
                "before_status",
                "disposition",
                "legacy_issue_id",
                "projected_status",
                "successor_packet_keys",
            },
            f"beads_projection.legacy_resolutions[{index}]",
        )
        expected_issue_id = f"bb-auh.{index + 1}"
        if resolution.get("legacy_issue_id") != expected_issue_id:
            raise ValueError(
                "beads_projection child resolutions must be ordered bb-auh.1 "
                "through bb-auh.67"
            )
        before_status = resolution.get("before_status")
        if resolution.get("projected_status") != "closed":
            raise ValueError(f"{expected_issue_id} must project to closed")
        if before_status == "closed":
            if resolution.get("disposition") != "preserve_closed":
                raise ValueError(f"{expected_issue_id} closed disposition is invalid")
        elif before_status in {"open", "in_progress"}:
            if resolution.get("disposition") != SUPERSEDED_REASON:
                raise ValueError(
                    f"{expected_issue_id} supersession disposition is invalid"
                )
        else:
            raise ValueError(f"{expected_issue_id} before_status is invalid")
        successor_keys = _list(
            resolution.get("successor_packet_keys"),
            f"beads_projection.legacy_resolutions[{index}].successor_packet_keys",
        )
        if (
            not successor_keys
            or any(
                not isinstance(key, str) or key not in _PACKET_DEPENDENCIES
                for key in successor_keys
            )
            or successor_keys != sorted(set(successor_keys))
        ):
            raise ValueError(f"{expected_issue_id} successor packet keys are invalid")

    parent_resolution = _object(
        document.get("legacy_parent_resolution"),
        "beads_projection.legacy_parent_resolution",
    )
    _require_exact_keys(
        parent_resolution,
        _LEGACY_PARENT_RESOLUTION_KEYS,
        "beads_projection.legacy_parent_resolution",
    )
    if (
        parent_resolution.get("issue_id") != "bb-auh"
        or parent_resolution.get("projected_status") != "closed"
        or parent_resolution.get("after_child_resolution_count") != len(resolutions)
    ):
        raise ValueError(
            "beads_projection legacy parent must close after all 67 child resolutions"
        )
    _digest(
        parent_resolution.get("before_record_sha256"),
        "beads_projection.legacy_parent_resolution.before_record_sha256",
    )
    parent_before_status = parent_resolution.get("before_status")
    if parent_before_status == "closed":
        if parent_resolution.get("disposition") != "preserve_closed":
            raise ValueError(
                "beads_projection closed legacy parent must remain a no-op"
            )
    elif parent_before_status in {"open", "in_progress"}:
        if (
            parent_resolution.get("disposition")
            != "superseded_by_v2_not_completed"
            or parent_resolution.get("close_reason") != SUPERSEDED_REASON
        ):
            raise ValueError(
                "beads_projection legacy parent supersession is invalid"
            )
    else:
        raise ValueError("beads_projection legacy parent before_status is invalid")

    successor_epic = _object(
        document.get("successor_epic"), "beads_projection.successor_epic"
    )
    _require_exact_keys(
        successor_epic,
        {"depends_on", "issue_type", "stable_key", "status", "title"},
        "beads_projection.successor_epic",
    )
    epic_key = _string(
        successor_epic.get("stable_key"),
        "beads_projection.successor_epic.stable_key",
    )
    if (
        successor_epic.get("depends_on") != []
        or successor_epic.get("issue_type") != "epic"
        or successor_epic.get("status") != "open"
    ):
        raise ValueError("beads_projection successor epic is invalid")
    _string(
        successor_epic.get("title"),
        "beads_projection.successor_epic.title",
    )

    successor_issues = _list(
        document.get("successor_issues"), "beads_projection.successor_issues"
    )
    expected_packet_keys = sorted(_PACKET_DEPENDENCIES)
    if len(successor_issues) != len(expected_packet_keys):
        raise ValueError("beads_projection successor issue set is incomplete")
    for index, expected_key in enumerate(expected_packet_keys):
        successor = _object(
            successor_issues[index],
            f"beads_projection.successor_issues[{index}]",
        )
        _require_exact_keys(
            successor,
            {
                "depends_on",
                "frontier_state",
                "issue_type",
                "kind",
                "parent_stable_key",
                "stable_key",
                "status",
                "title",
            },
            f"beads_projection.successor_issues[{index}]",
        )
        if (
            successor.get("stable_key") != expected_key
            or successor.get("title") != expected_key
            or successor.get("depends_on")
            != sorted(_PACKET_DEPENDENCIES[expected_key])
            or successor.get("frontier_state")
            != (
                "READY_FOR_LOCAL_MIGRATION_WORK"
                if expected_key == "AT0"
                else "BLOCKED"
            )
            or successor.get("issue_type") != "task"
            or successor.get("parent_stable_key") != epic_key
            or successor.get("status") != "open"
        ):
            raise ValueError(f"beads_projection successor issue {expected_key} is invalid")
        _string(
            successor.get("kind"),
            f"beads_projection.successor_issues[{index}].kind",
        )

    validate_zero_authority(document)


def derive_beads_projection(
    beads_migration: dict[str, Any],
    live_beads_rows: Any,
    *,
    migration_id: str,
    spec_freeze_sha256: str,
) -> dict[str, Any]:
    """Plan deterministic successor issues and legacy supersessions without running bd."""

    frozen = _object(beads_migration, "beads_migration")
    _validate_migration_binding(migration_id, spec_freeze_sha256)
    _require_identity(frozen, "beads_migration")
    _require_exact_keys(
        frozen,
        {
            "cutover_rules",
            "freeze_request_issue_id",
            "legacy_parent",
            "legacy_snapshot",
            "map_decision_snapshot",
            "map_decision_snapshot_sha256",
            "map_decisions",
            "mappings",
            "program_id",
            "schema_version",
            "successor_epic",
            "successor_packet_keys",
        },
        "beads_migration",
    )
    if frozen.get("freeze_request_issue_id") != "bb-6d4.9":
        raise ValueError("Beads freeze request is not the frozen rc3 issue")
    if frozen.get("schema_version") != "bb.rl.phase5.beads_migration.v3":
        raise ValueError("beads_migration.schema_version is not the rc3 mapping schema")
    cutover_rules = _list(
        frozen.get("cutover_rules"), "beads_migration.cutover_rules"
    )
    if tuple(cutover_rules) != _BEADS_CUTOVER_RULES:
        raise ValueError("beads_migration.cutover_rules do not match frozen rc3 order")

    mappings = _list(frozen.get("mappings"), "beads_migration.mappings")
    snapshot = _list(
        frozen.get("legacy_snapshot"), "beads_migration.legacy_snapshot"
    )
    if len(mappings) != 67 or len(snapshot) != 67:
        raise ValueError("the frozen migration must contain all 67 legacy mappings")
    parent = _object(frozen.get("legacy_parent"), "beads_migration.legacy_parent")
    _require_exact_keys(
        parent,
        {
            "child_count",
            "issue_id",
            "snapshot_scope",
            "snapshot_sha256",
            "status_counts",
        },
        "beads_migration.legacy_parent",
    )
    if parent.get("issue_id") != "bb-auh" or parent.get("child_count") != 67:
        raise ValueError("the frozen legacy parent binding is incomplete")
    if parent.get("snapshot_sha256") != _sha256(snapshot):
        raise ValueError("the frozen legacy snapshot digest does not match its rows")

    mapping_by_id: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(mappings):
        mapping = _object(value, f"beads_migration.mappings[{index}]")
        _require_exact_keys(
            mapping,
            {
                "close_reason",
                "dependency_ids",
                "disposition",
                "legacy_issue_id",
                "score_item_id",
                "status",
                "successor_issue_resolution",
                "successor_packet_keys",
                "title",
            },
            f"beads_migration.mappings[{index}]",
        )
        issue_id = _string(
            mapping.get("legacy_issue_id"),
            f"beads_migration.mappings[{index}].legacy_issue_id",
        )
        if issue_id in mapping_by_id:
            raise ValueError(f"duplicate legacy mapping {issue_id}")
        mapping_by_id[issue_id] = mapping
    expected_issue_ids = {f"bb-auh.{number}" for number in range(1, 68)}
    if set(mapping_by_id) != expected_issue_ids:
        raise ValueError("legacy mappings are not the complete bb-auh.1 through bb-auh.67 set")

    snapshot_by_id: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(snapshot):
        row = _object(value, f"beads_migration.legacy_snapshot[{index}]")
        issue_id = _string(row.get("id"), f"legacy_snapshot[{index}].id")
        if issue_id in snapshot_by_id:
            raise ValueError(f"duplicate frozen legacy row {issue_id}")
        snapshot_by_id[issue_id] = row
    if set(snapshot_by_id) != expected_issue_ids:
        raise ValueError("the frozen legacy snapshot is not complete")

    live_by_id: dict[str, dict[str, Any]] = {}
    live_parent: dict[str, Any] | None = None
    for row in _live_rows(live_beads_rows):
        issue_id = row.get("id")
        if issue_id == "bb-auh":
            if live_parent is not None:
                raise ValueError("duplicate live legacy parent row bb-auh")
            live_parent = row
            continue
        if issue_id not in expected_issue_ids:
            continue
        if issue_id in live_by_id:
            raise ValueError(f"duplicate live legacy row {issue_id}")
        live_by_id[issue_id] = row
    if set(live_by_id) != expected_issue_ids:
        missing = sorted(expected_issue_ids - set(live_by_id), key=_legacy_suffix)
        raise ValueError(f"live Beads export is missing legacy rows: {missing}")
    if live_parent is None:
        raise ValueError("live Beads export is missing legacy parent row bb-auh")

    packet_definitions = _list(
        frozen.get("successor_packet_keys"),
        "beads_migration.successor_packet_keys",
    )
    definition_by_key: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(packet_definitions):
        definition = _object(value, f"successor_packet_keys[{index}]")
        _require_exact_keys(
            definition,
            {"depends_on", "key", "kind"},
            f"successor_packet_keys[{index}]",
        )
        key = _string(definition.get("key"), f"successor_packet_keys[{index}].key")
        if key in definition_by_key:
            raise ValueError(f"duplicate successor packet key {key}")
        dependencies = _list(
            definition.get("depends_on"), f"successor_packet_keys[{index}].depends_on"
        )
        if any(not isinstance(item, str) for item in dependencies):
            raise ValueError(f"successor dependencies for {key} must be strings")
        definition_by_key[key] = definition
    if set(definition_by_key) != set(_PACKET_DEPENDENCIES):
        raise ValueError("successor packet keys do not match the frozen rc3 DAG")
    for key, expected_dependencies in _PACKET_DEPENDENCIES.items():
        if tuple(sorted(definition_by_key[key]["depends_on"])) != tuple(
            sorted(expected_dependencies)
        ):
            raise ValueError(f"successor dependencies for {key} do not match rc3")

    def migration_semantic_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if key not in {"comment_count", "comments"}
        }

    resolutions: list[dict[str, Any]] = []
    for issue_id in sorted(expected_issue_ids, key=_legacy_suffix):
        mapping = mapping_by_id[issue_id]
        live = live_by_id[issue_id]
        frozen_row = snapshot_by_id[issue_id]
        if migration_semantic_row(live) != migration_semantic_row(frozen_row):
            raise ValueError(
                f"live Beads row {issue_id} drifted from the frozen migration fields"
            )
        if mapping.get("status") != live.get("status"):
            raise ValueError(f"status mismatch for legacy mapping {issue_id}")
        if mapping.get("title") != live.get("title"):
            raise ValueError(f"title mismatch for legacy mapping {issue_id}")
        live_dependencies = _list(
            live.get("dependencies"), f"live Beads row {issue_id}.dependencies"
        )
        if any(
            not isinstance(edge, dict)
            or not isinstance(edge.get("type"), str)
            or not isinstance(edge.get("depends_on_id"), str)
            for edge in live_dependencies
        ):
            raise ValueError(f"malformed dependency in live Beads row {issue_id}")
        dependency_ids = sorted(
            edge["depends_on_id"]
            for edge in live_dependencies
            if edge["type"] != "parent-child"
        )
        if mapping.get("dependency_ids") != dependency_ids:
            raise ValueError(f"dependency mismatch for legacy mapping {issue_id}")
        if mapping.get("close_reason") != live.get("close_reason"):
            raise ValueError(f"close-reason mismatch for legacy mapping {issue_id}")
        successor_keys = _list(
            mapping.get("successor_packet_keys"),
            f"mapping {issue_id}.successor_packet_keys",
        )
        if not successor_keys or any(key not in definition_by_key for key in successor_keys):
            raise ValueError(f"mapping {issue_id} has an invalid successor packet key")
        status = live.get("status")
        if status == "closed":
            expected_dispositions = {
                "historical_issue_closed_no_score_carry",
                "historical_completed_implementation_candidate",
            }
            if mapping.get("disposition") not in expected_dispositions:
                raise ValueError(f"closed legacy mapping {issue_id} has a mutable disposition")
            resolution = {
                "before_status": "closed",
                "disposition": "preserve_closed",
                "legacy_issue_id": issue_id,
                "projected_status": "closed",
                "successor_packet_keys": sorted(successor_keys),
            }
        elif status in {"open", "in_progress"}:
            if mapping.get("disposition") != "superseded_not_completed":
                raise ValueError(f"open legacy mapping {issue_id} lacks supersession disposition")
            resolution = {
                "before_status": status,
                "disposition": SUPERSEDED_REASON,
                "legacy_issue_id": issue_id,
                "projected_status": "closed",
                "successor_packet_keys": sorted(successor_keys),
            }
        else:
            raise ValueError(f"unsupported live status {status!r} for {issue_id}")
        resolutions.append(resolution)

    successor_epic = _object(
        frozen.get("successor_epic"), "beads_migration.successor_epic"
    )
    _require_exact_keys(
        successor_epic,
        {"creation", "stable_key", "title"},
        "beads_migration.successor_epic",
    )
    epic_key = _string(successor_epic.get("stable_key"), "successor_epic.stable_key")
    epic_title = _string(successor_epic.get("title"), "successor_epic.title")
    successor_epic_spec = {
        "depends_on": [],
        "issue_type": "epic",
        "stable_key": epic_key,
        "status": "open",
        "title": epic_title,
    }
    successor_packet_specs = []
    for key in sorted(definition_by_key):
        definition = definition_by_key[key]
        kind = _string(definition.get("kind"), f"successor packet {key}.kind")
        successor_packet_specs.append(
            {
                "depends_on": sorted(definition["depends_on"]),
                "frontier_state": (
                    "READY_FOR_LOCAL_MIGRATION_WORK" if key == "AT0" else "BLOCKED"
                ),
                "issue_type": "task",
                "kind": kind,
                "parent_stable_key": epic_key,
                "stable_key": key,
                "status": "open",
                "title": key,
            }
        )

    parent_before_status = live_parent.get("status")
    if parent_before_status == "closed":
        parent_disposition = "preserve_closed"
        parent_close_reason = live_parent.get("close_reason")
    elif parent_before_status in {"open", "in_progress"}:
        parent_disposition = "superseded_by_v2_not_completed"
        parent_close_reason = SUPERSEDED_REASON
    else:
        raise ValueError(
            f"unsupported live status {parent_before_status!r} for bb-auh"
        )
    parent_resolution = {
        "after_child_resolution_count": len(resolutions),
        "before_record_sha256": _sha256(live_parent),
        "before_status": parent_before_status,
        "close_reason": parent_close_reason,
        "disposition": parent_disposition,
        "issue_id": "bb-auh",
        "projected_status": "closed",
    }

    result = {
        "legacy_parent_resolution": parent_resolution,
        "legacy_resolutions": resolutions,
        "migration_id": migration_id,
        "program_id": PROGRAM_ID,
        "schema_version": "bb.rl.phase5.beads_resolution.v1",
        "source_snapshot_sha256": _sha256(
            [live_by_id[issue_id] for issue_id in sorted(expected_issue_ids, key=_legacy_suffix)]
        ),
        "spec_freeze_sha256": spec_freeze_sha256,
        "successor_epic": successor_epic_spec,
        "successor_issues": successor_packet_specs,
    }
    validate_beads_projection(result)
    return result


def derive_session_projection(
    session_state: dict[str, Any],
    active_status: dict[str, Any],
    run_queue: dict[str, Any],
    *,
    migration_id: str,
) -> dict[str, Any]:
    """Replace legacy session rows with a deterministic view of the prepared queue."""

    _object(session_state, "session_state")
    status = _object(active_status, "active_status")
    queue = _object(run_queue, "run_queue")
    _migration_identifier(migration_id)
    _require_identity(status, "active_status")
    _require_identity(queue, "run_queue")
    _require_exact_keys(
        status,
        _DRAFT_STATUS_KEYS
        | {"migration_id", "next_local_packet", "spec_freeze_sha256"},
        "active_status",
    )
    _require_exact_keys(
        queue,
        _DRAFT_QUEUE_KEYS | {"migration_id", "spec_freeze_sha256"},
        "run_queue",
    )
    assurance, training = _validate_status_fields(
        status, "active_status", prepared=True
    )
    if (
        status.get("revision_id") != REVISION_ID
        or set(_list(assurance.get("pending_items"), "active_status.pending_items"))
        != _ASSURANCE_ITEMS
        or assurance.get("catalog_points") != 1000
        or assurance.get("item_count") != 49
        or training.get("state") != "BLOCKED_SHARED_TRANSPORT"
    ):
        raise ValueError("prepared active status does not match the rc5 catalog")
    if (
        status.get("schema_version") != "bb.rl.phase5.active_status.v5"
        or queue.get("schema_version") != "bb.rl.phase5.run_queue.v2"
        or status.get("active") is not True
        or status.get("allowed_next") != "AT0"
        or status.get("next_local_packet") != "AT0"
        or status.get("shared_transport")
        != {"admitted_hash": None, "smoke_job": None, "state": "blocked"}
        or status.get("spec_freeze_sha256") != queue.get("spec_freeze_sha256")
    ):
        raise ValueError("prepared status and queue do not match the frozen schemas")
    _digest(status.get("spec_freeze_sha256"), "active_status.spec_freeze_sha256")
    if status.get("migration_id") != migration_id or queue.get("migration_id") != migration_id:
        raise ValueError("session projection migration IDs do not match")
    if (
        isinstance(status.get("generation"), bool)
        or isinstance(queue.get("generation"), bool)
        or isinstance(status.get("event_cursor"), bool)
        or status.get("generation") != 1
        or queue.get("generation") != 1
        or status.get("event_cursor") != 2
    ):
        raise ValueError("session projection requires generation one and event cursor two")
    if status.get("program_state") != "READY_FOR_LOCAL_MIGRATION_WORK":
        raise ValueError("active status is not ready for local migration work")
    if queue.get("state") != "READY_FOR_LOCAL_MIGRATION_WORK":
        raise ValueError("run queue is not ready for local migration work")
    if status.get("active_packet") is not None or status.get("target_lease") is not None:
        raise ValueError("prepared status already carries a packet or target lease")
    if queue.get("target_lease") is not None:
        raise ValueError("prepared queue already carries a target lease")
    for key in ("waiting_human", "waiting_external", "escalated"):
        if _list(queue.get(key), f"run_queue.{key}"):
            raise ValueError(f"prepared queue unexpectedly contains {key} rows")
    validate_zero_authority(status, queue)

    eligible = _list(queue.get("eligible"), "run_queue.eligible")
    blocked = _list(queue.get("blocked"), "run_queue.blocked")
    if len(eligible) != 1:
        raise ValueError("prepared queue must have exactly one eligible row")
    eligible_row = copy.deepcopy(_object(eligible[0], "run_queue.eligible[0]"))
    _require_exact_keys(
        eligible_row,
        {"kind", "packet_key", "reason"},
        "run_queue.eligible[0]",
    )
    if eligible_row != {
        "kind": "local",
        "packet_key": "AT0",
        "reason": "SPEC_FREEZE and migration cutover complete",
    }:
        raise ValueError("prepared AT0 row does not match the frozen contract")
    eligible_row["status"] = "pending"

    blocked_rows: list[dict[str, Any]] = []
    for index, value in enumerate(blocked):
        row = copy.deepcopy(_object(value, f"run_queue.blocked[{index}]"))
        _require_exact_keys(
            row,
            {"depends_on", "packet_key", "reason"},
            f"run_queue.blocked[{index}]",
        )
        packet_key = _string(
            row.get("packet_key"), f"run_queue.blocked[{index}].packet_key"
        )
        if (
            packet_key == "AT0"
            or packet_key not in _PACKET_DEPENDENCIES
            or sorted(_list(row.get("depends_on"), f"blocked {packet_key}.depends_on"))
            != sorted(_PACKET_DEPENDENCIES[packet_key])
        ):
            raise ValueError(f"prepared blocked row {packet_key} does not match rc3")
        row["status"] = "blocked"
        blocked_rows.append(row)
    blocked_rows.sort(key=lambda row: row["packet_key"])
    todos = [eligible_row, *blocked_rows]
    if [row["packet_key"] for row in todos] != [
        "AT0",
        *sorted(key for key in _PACKET_DEPENDENCIES if key != "AT0"),
    ]:
        raise ValueError("prepared session todos do not contain the complete packet DAG")

    result = {
        "active_packet": None,
        "migration_id": migration_id,
        "program_id": PROGRAM_ID,
        "queue_sha256": _sha256(queue),
        "revision_id": _string(status.get("revision_id"), "active_status.revision_id"),
        "schema_version": "bb.rl.phase5.session_projection.v1",
        "state": "READY_FOR_LOCAL_MIGRATION_WORK",
        "status_sha256": _sha256(status),
        "target_lease": None,
        "todos": todos,
    }
    validate_zero_authority(result)
    return result


def _reference(value: Any, name: str) -> dict[str, Any]:
    reference = _object(value, name)
    if set(reference) != {"path", "sha256", "size"}:
        raise ValueError(f"{name} must contain exactly path, sha256, and size")
    path = _string(reference["path"], f"{name}.path")
    parts = path.split("/")
    if (
        path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"{name}.path must be a canonical relative path")
    digest = _digest(reference["sha256"], f"{name}.sha256")
    size = reference["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"{name}.size must be a non-negative integer")
    return {"path": path, "sha256": digest, "size": size}


def build_root_selector(
    *,
    revision_id: str,
    program_id: str,
    generation: int,
    event_cursor: int,
    migration_id: str,
    artifact_manifest_ref: dict[str, Any],
    active_status_ref: dict[str, Any],
    evidence_index_ref: dict[str, Any],
    authority_policy_ref: dict[str, Any],
    run_queue_ref: dict[str, Any],
) -> dict[str, Any]:
    """Build the complete immutable generation-one root selector after-image."""

    if revision_id != REVISION_ID:
        raise ValueError("revision_id does not match the frozen rc3 revision")
    if program_id != PROGRAM_ID:
        raise ValueError("program_id does not match the frozen program")
    if isinstance(generation, bool) or generation != 1:
        raise ValueError("root selector generation must be one")
    if isinstance(event_cursor, bool) or event_cursor != 2:
        raise ValueError("root selector event cursor must be two")
    _migration_identifier(migration_id)
    artifacts = {
        "active_status": _reference(active_status_ref, "active_status_ref"),
        "artifact_manifest": _reference(
            artifact_manifest_ref, "artifact_manifest_ref"
        ),
        "authority_policy": _reference(authority_policy_ref, "authority_policy_ref"),
        "evidence_index": _reference(evidence_index_ref, "evidence_index_ref"),
        "run_queue": _reference(run_queue_ref, "run_queue_ref"),
    }
    revision_root = f"versions/v2-two-track/{REVISION_ID}"
    expected_paths = {
        "artifact_manifest": f"{revision_root}/ARTIFACT_MANIFEST.json",
        "active_status": f"migrations/{migration_id}/PREPARED_ACTIVE_STATUS.json",
        "authority_policy": f"{revision_root}/AUTHORITY_POLICY.json",
        "evidence_index": f"{revision_root}/EVIDENCE_INDEX.json",
        "run_queue": f"migrations/{migration_id}/PREPARED_RUN_QUEUE.json",
    }
    actual_paths = {key: reference["path"] for key, reference in artifacts.items()}
    if actual_paths != expected_paths:
        raise ValueError("root selector artifact paths do not match the frozen layout")
    if artifacts["artifact_manifest"]["sha256"] != ARTIFACT_MANIFEST_SHA256:
        raise ValueError("artifact manifest reference is not the frozen rc3 manifest")
    paths = [reference["path"] for reference in artifacts.values()]
    if len(paths) != len(set(paths)):
        raise ValueError("root selector references must use distinct paths")
    result = {
        "artifacts": artifacts,
        "event_cursor": event_cursor,
        "generation": generation,
        "migration_id": migration_id,
        "program_id": program_id,
        "revision_id": revision_id,
        "schema_version": "bb.rl.phase5.root_active_selector.v1",
    }
    validate_zero_authority(result)
    return result


def _is_empty(value: Any) -> bool:
    return value is None or value is False or value == 0 or value == "" or value == []


def validate_zero_authority(*documents: dict[str, Any]) -> None:
    """Reject any prepared document that carries target or result authority."""

    if not documents:
        raise ValueError("at least one document is required")

    forbidden_nonempty = {
        "admission",
        "admission_id",
        "admission_ref",
        "admitted_hash",
        "approval_refs",
        "award",
        "awards",
        "awarded_items",
        "completion_decision",
        "evidence_refs",
        "evidence_root",
        "review_refs",
        "score",
        "score_decision",
        "score_points",
        "target_lease",
        "target_lease_id",
    }
    forbidden_true = {
        "admitted",
        "authorized",
        "completed",
        "completion",
        "internal_completion",
        "promoted",
        "satisfied",
        "score_field_present",
        "target_execution_allowed",
    }
    zero_counts = {
        "active_relations",
        "current_verified_points",
        "evidence_count",
        "evidence_ref_count",
        "review_count",
        "review_ref_count",
    }

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"document key at {path} must be a string")
                child_path = f"{path}.{key}" if path else key
                normalized_key = key.lower()
                authority_carrier = any(
                    token in normalized_key
                    for token in (
                        "admit",
                        "award",
                        "completion",
                        "promoted",
                        "promotion_authorized",
                        "score_update",
                        "scorecard_update",
                        "target_action",
                        "target_execution",
                    )
                )
                counted_reference = (
                    ("evidence" in normalized_key or "review" in normalized_key)
                    and ("count" in normalized_key or "ref" in normalized_key)
                )
                if (authority_carrier or counted_reference) and not _is_empty(child):
                    raise ValueError(
                        f"unrecognized target or result authority at {child_path}"
                    )
                if key in {"target_lease", "target_lease_id"} and child is not None:
                    raise ValueError(f"target lease must be absent at {child_path}")
                if key in forbidden_nonempty and not _is_empty(child):
                    raise ValueError(f"target or result authority present at {child_path}")
                if key in forbidden_true and child is not False:
                    raise ValueError(f"authority boolean must be false at {child_path}")
                if (key in zero_counts or key.endswith("_ref_count")) and child != 0:
                    raise ValueError(f"authority count must be zero at {child_path}")
                if key == "points_awarded" and child != 0:
                    raise ValueError(f"awarded points must be zero at {child_path}")
                if key == "external_acceptance":
                    if isinstance(child, bool):
                        if child is not False:
                            raise ValueError("external acceptance authority must be false")
                    else:
                        acceptance = _object(child, child_path)
                        if acceptance.get("state") != "unclaimed":
                            raise ValueError("external acceptance must remain unclaimed")
                if key == "promotion":
                    if isinstance(child, bool):
                        if child is not False:
                            raise ValueError("promotion authority must be false")
                    else:
                        promotion = _object(child, child_path)
                        if promotion.get("authorized") is not False or promotion.get("state") != "unclaimed":
                            raise ValueError("promotion must remain unauthorized and unclaimed")
                if key == "eligible":
                    eligible = _list(child, child_path)
                    packet_keys = []
                    for index, row in enumerate(eligible):
                        if isinstance(row, str):
                            packet_keys.append(row)
                        else:
                            packet_keys.append(
                                _string(
                                    _object(row, f"{child_path}[{index}]").get("packet_key"),
                                    f"{child_path}[{index}].packet_key",
                                )
                            )
                    if packet_keys not in ([], ["AT0"]):
                        raise ValueError("AT0 must be the sole eligible packet")
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    for index, document in enumerate(documents):
        root = _object(document, f"documents[{index}]")
        walk(root, f"documents[{index}]")
