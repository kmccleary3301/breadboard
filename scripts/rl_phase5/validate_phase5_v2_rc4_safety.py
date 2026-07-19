from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
CANDIDATE_REVISION_ID = "v2.0.0-rc5-20260717"
PREDECESSOR_REVISION_ID = "v2.0.0-rc4-20260715"
PRE_PREDECESSOR_REVISION_ID = "v2.0.0-rc3-20260715"
ARCHIVE_ID = "v1-bootstrap-20260709-sealed-rc3"
CANDIDATE_MANIFEST_SHA256 = "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
PREDECESSOR_MANIFEST_SHA256 = "sha256:b5897c0465bfb0cdf4b3aa79427c55e85b8a1d0b600c40e6d6eb62b579e9cbfd"
PRE_PREDECESSOR_MANIFEST_SHA256 = "sha256:57144dd1e87369cc5d0e70065846ec4b2acddcbe9020ca84ed49f84b51117d19"
ARCHIVE_MANIFEST_SHA256 = "sha256:91519465cfc7a45d8a6375a23908753f48bf61f2d3e90f7734f20affee2ca2d8"
V1_ACTIVE_SHA256 = "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
V1_SCORECARD_SHA256 = "sha256:df8e69a610b7ba69237642ff7a49d42fb1819ae919be224e4a1399b246542a23"
BUILD_REPORT_SHA256 = "sha256:73bd2d011fbc83ad7d5081cef8c433222cf33166b3624377abf96d3d5450a2b5"
EXPECTED_SOURCE_ENTRIES = 835
EXPECTED_BUILD_FILE_COUNT = 55

UNCHANGED_FILES = frozenset(
    {
        "ASSURANCE_CATALOG.json",
        "AUTHORITY_POLICY.json",
        "BEADS_MIGRATION.json",
        "CAMPAIGN_MATRIX.yaml",
        "CATALOG_EQUIVALENCE.json",
        "DURABLE_TRANSPORT_CONTRACT.json",
        "EVIDENCE_INDEX.json",
        "EVIDENCE_TAXONOMY.json",
        "LOOP_SPEC.yaml",
        "PACKET_DISPOSITIONS.json",
        "RUN_QUEUE.json",
        "TRAINING_PROOF_CONTRACT.json",
        "WORK_PACKET_DAG.yaml",
    }
)
IDENTITY_ONLY_FILES = frozenset(
    {
        "DRAFT_STATUS.json",
        "FRESH_WORKER_HANDOFF_CONTRACT.json",
        "PROGRAM_SPEC.yaml",
        "QUIESCENCE_CONTRACT.json",
        "SESSION_HANDOFF_CONTRACT.json",
        "SOURCE_MANIFEST.json",
    }
)
REVIEWED_MODIFIED_FILES = frozenset(
    {
        "MIGRATION_PLAN.json",
        "MIGRATION_REPLAY_CONTRACT.json",
        "MIGRATION_TRANSACTION.json",
    }
)
REVIEWED_ADDED_FILES: frozenset[str] = frozenset()
EXPECTED_CANDIDATE_FILES = (
    UNCHANGED_FILES
    | IDENTITY_ONLY_FILES
    | REVIEWED_MODIFIED_FILES
)
EXPECTED_PREDECESSOR_FILES = EXPECTED_CANDIDATE_FILES

EXACT_SEMANTIC_DOMAINS: dict[str, tuple[str, ...]] = {
    "catalog": ("ASSURANCE_CATALOG.json", "CATALOG_EQUIVALENCE.json"),
    "dag": ("WORK_PACKET_DAG.yaml",),
    "queue": ("RUN_QUEUE.json", "CAMPAIGN_MATRIX.yaml", "LOOP_SPEC.yaml"),
    "authority": ("AUTHORITY_POLICY.json",),
    "transport": ("DURABLE_TRANSPORT_CONTRACT.json",),
    "evidence": ("EVIDENCE_INDEX.json", "EVIDENCE_TAXONOMY.json"),
    "score": ("ASSURANCE_CATALOG.json", "TRAINING_PROOF_CONTRACT.json"),
}

EXPECTED_TRANSACTION_STORES = (
    "v2_event_log",
    "beads_projection",
    "root_active_selector",
)
ALLOWED_DOLT_ADAPTERS = ("embedded_dolt_cli", "sql_server")


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON object: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"expected object: {context}")
    return value


def _require_sequence(value: Any, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"expected array: {context}")
    return value


def _require_keys(value: Mapping[str, Any], keys: set[str], context: str) -> None:
    missing = sorted(keys - set(value))
    if missing:
        raise ValueError(f"missing {context} field: {missing[0]}")


def _require_field_set(value: Any, required: set[str], context: str) -> None:
    fields = _require_sequence(value, context)
    actual = set(fields)
    if len(fields) != len(actual):
        raise ValueError(f"duplicate {context} field")
    if actual != required:
        missing = sorted(required - actual)
        extra = sorted(actual - required)
        if missing:
            raise ValueError(f"missing {context} field: {missing[0]}")
        raise ValueError(f"contradictory extra {context} field: {extra[0]}")


def _manifest_rows(manifest: Mapping[str, Any], context: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw_row in _require_sequence(manifest.get("files"), f"{context} files"):
        row = dict(_require_mapping(raw_row, f"{context} file row"))
        path_value = row.get("path")
        if not isinstance(path_value, str):
            raise ValueError(f"invalid {context} manifest path")
        pure_path = PurePosixPath(path_value)
        if (
            pure_path.is_absolute()
            or not pure_path.parts
            or any(part in {"", ".", ".."} for part in pure_path.parts)
            or "\\" in path_value
        ):
            raise ValueError(f"unsafe {context} manifest path: {path_value}")
        expected_row_fields = (
            {"path", "media_type", "mode", "sha256", "size"}
            if context in {"candidate", "predecessor"}
            else {"path", "mode", "sha256", "size"}
        )
        if set(row) != expected_row_fields:
            raise ValueError(f"{context} manifest row schema drift: {path_value}")
        if path_value in rows:
            raise ValueError(f"duplicate {context} manifest path: {path_value}")
        rows[path_value] = row
    return rows


def _check_regular_file(path: Path, context: str) -> None:
    if path.is_symlink():
        raise ValueError(f"symlink forbidden in {context}: {path}")
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ValueError(f"missing {context} file: {path}") from error
    if not stat.S_ISREG(mode):
        raise ValueError(f"non-regular {context} file: {path}")


def check_manifest_tree(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    manifest_name: str,
    context: str,
) -> dict[str, dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"invalid {context} root: {root}")
    manifest_path = root / manifest_name
    _check_regular_file(manifest_path, f"{context} manifest")
    if f"{stat.S_IMODE(manifest_path.lstat().st_mode):04o}" != "0444":
        raise ValueError(f"{context} manifest mode mismatch")

    rows = _manifest_rows(manifest, context)
    expected_files = set(rows) | {manifest_name}
    expected_directories = {
        PurePosixPath(*PurePosixPath(path).parts[:index]).as_posix()
        for path in expected_files
        for index in range(1, len(PurePosixPath(path).parts))
    }
    actual_files: set[str] = set()
    pending = [(root, PurePosixPath())]
    while pending:
        directory, relative_directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ValueError(f"cannot enumerate {context} tree: {directory}") from error
        for entry in entries:
            relative = relative_directory / entry.name
            relative_name = relative.as_posix()
            if entry.is_symlink():
                raise ValueError(f"{context} symlink drift: {relative_name}")
            if entry.is_dir(follow_symlinks=False):
                if relative_name not in expected_directories:
                    raise ValueError(f"{context} extra-directory drift: {relative_name}")
                pending.append((Path(entry.path), relative))
            elif entry.is_file(follow_symlinks=False):
                actual_files.add(relative_name)
            else:
                raise ValueError(f"{context} special-node drift: {relative_name}")
    if actual_files != expected_files:
        extra = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        detail = extra[0] if extra else missing[0]
        raise ValueError(f"{context} extra-file drift: {detail}")

    for relative_path, row in rows.items():
        path = root / relative_path
        _check_regular_file(path, context)
        actual_stat = path.lstat()
        actual_mode = f"{stat.S_IMODE(actual_stat.st_mode):04o}"
        if row.get("mode") != "0444" or actual_mode != row.get("mode"):
            raise ValueError(f"{context} mode mismatch: {relative_path}")
        if actual_stat.st_size != row.get("size"):
            raise ValueError(f"{context} size mismatch: {relative_path}")
        if sha256_file(path) != row.get("sha256"):
            raise ValueError(f"{context} byte drift: {relative_path}")
    return rows


def validate_allowed_delta(
    candidate_manifest: Mapping[str, Any],
    predecessor_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidate_rows = _manifest_rows(candidate_manifest, "candidate")
    predecessor_rows = _manifest_rows(predecessor_manifest, "predecessor")
    if set(candidate_rows) != EXPECTED_CANDIDATE_FILES:
        raise ValueError("candidate manifest file set drift")
    if set(predecessor_rows) != EXPECTED_PREDECESSOR_FILES:
        raise ValueError("predecessor manifest file set drift")
    if set(candidate_rows) - set(predecessor_rows) != REVIEWED_ADDED_FILES:
        raise ValueError("unreviewed added-file drift")
    if set(predecessor_rows) - set(candidate_rows):
        raise ValueError("removed-file drift")

    matrix: list[dict[str, Any]] = []
    for path in sorted(EXPECTED_CANDIDATE_FILES):
        candidate_row = candidate_rows[path]
        predecessor_row = predecessor_rows.get(path)
        if candidate_row.get("mode") != "0444":
            raise ValueError(f"candidate mode policy drift: {path}")
        if predecessor_row is not None and predecessor_row.get("mode") != "0444":
            raise ValueError(f"predecessor mode policy drift: {path}")
        if path in UNCHANGED_FILES:
            classification = "byte_identical"
            if predecessor_row is None or candidate_row != predecessor_row:
                raise ValueError(f"unreviewed byte drift: {path}")
        elif path in IDENTITY_ONLY_FILES:
            classification = "normalized_identity_only"
            if (
                predecessor_row is None
                or candidate_row.get("sha256") == predecessor_row.get("sha256")
            ):
                raise ValueError(f"missing identity delta: {path}")
        elif path in REVIEWED_MODIFIED_FILES:
            classification = "reviewed_contract_delta"
            if (
                predecessor_row is None
                or candidate_row.get("sha256") == predecessor_row.get("sha256")
            ):
                raise ValueError(f"missing reviewed delta: {path}")
        else:
            raise ValueError(f"unclassified candidate file: {path}")
        matrix.append(
            {
                "candidate_sha256": candidate_row["sha256"],
                "candidate_size": candidate_row["size"],
                "classification": classification,
                "path": path,
                "predecessor_sha256": predecessor_row["sha256"],
                "predecessor_size": predecessor_row["size"],
            }
        )
    matrix.append(
        {
            "candidate_sha256": CANDIDATE_MANIFEST_SHA256,
            "candidate_size": None,
            "classification": "reviewed_manifest_identity_and_supersession",
            "path": "ARTIFACT_MANIFEST.json",
            "predecessor_sha256": PREDECESSOR_MANIFEST_SHA256,
            "predecessor_size": None,
        }
    )
    return matrix


def _domain_projection(
    documents: Mapping[str, Mapping[str, Any]], names: Sequence[str]
) -> dict[str, Mapping[str, Any]]:
    return {name: documents[name] for name in names}


def _normalize_status(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        candidate.get("revision_id") != CANDIDATE_REVISION_ID
        or candidate.get("schema_version") != "bb.rl.phase5.active_status.v4"
        or baseline.get("revision_id") != PREDECESSOR_REVISION_ID
        or baseline.get("schema_version") != "bb.rl.phase5.active_status.v4"
    ):
        raise ValueError("status revision/schema identity drift")
    baseline_nonclaims = baseline.get("nonclaims")
    candidate_nonclaims = candidate.get("nonclaims")
    if not isinstance(baseline_nonclaims, list) or not isinstance(
        candidate_nonclaims, list
    ):
        raise ValueError("status semantic drift: invalid nonclaims")
    expected_nonclaims = [
        (
            "rc4 SPEC_FREEZE grants no rc5 authority"
            if value == "rc3 SPEC_FREEZE grants no rc4 authority"
            else value
        )
        for value in baseline_nonclaims
    ]
    if candidate_nonclaims != expected_nonclaims:
        raise ValueError("status semantic drift: nonclaims")
    expected_candidate_authority = {
        "prior_rc4_spec_freeze_applies": False,
        "required": "new exact rc5 SPEC_FREEZE",
        "superseded_artifact_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "superseded_revision_id": PREDECESSOR_REVISION_ID,
    }
    if candidate.get("candidate_authority") != expected_candidate_authority:
        raise ValueError("predecessor authority reused")
    if candidate.get("allowed_next") != (
        "independent rc5 candidate reviews then a new exact-revision Kyle "
        "SPEC_FREEZE"
    ):
        raise ValueError("status authority transition drift")
    if candidate.get("program_state") != "DRAFT_WAITING_RC5_SPEC_FREEZE":
        raise ValueError("status semantic drift: program_state")
    normalized = copy.deepcopy(dict(candidate))
    normalized["candidate_authority"] = baseline.get("candidate_authority")
    normalized["nonclaims"] = baseline_nonclaims
    normalized["allowed_next"] = baseline.get("allowed_next")
    normalized["program_state"] = baseline.get("program_state")
    normalized["revision_id"] = baseline.get("revision_id")
    return normalized


def _normalize_program(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        candidate.get("revision_id") != CANDIDATE_REVISION_ID
        or candidate.get("schema_version") != "bb.rl.phase5.program_spec.v4"
        or baseline.get("revision_id") != PREDECESSOR_REVISION_ID
        or baseline.get("schema_version") != "bb.rl.phase5.program_spec.v4"
    ):
        raise ValueError("program revision/schema identity drift")
    expected_contracts = {
        "fresh_worker_program_replay": "FRESH_WORKER_HANDOFF_CONTRACT.json",
        "migration_replay": "MIGRATION_REPLAY_CONTRACT.json",
        "quiescence": "QUIESCENCE_CONTRACT.json",
        "session_handoff": "SESSION_HANDOFF_CONTRACT.json",
        "transaction": "MIGRATION_TRANSACTION.json",
    }
    expected_revision = {
        "candidate_revision_id": CANDIDATE_REVISION_ID,
        "prior_spec_freeze_authority_for_candidate": False,
        "superseded_artifact_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "superseded_revision_id": PREDECESSOR_REVISION_ID,
        "supersession_scope": "migration and cutover mechanics only; catalog, program, queue, score, authority, transport, and target semantics are unchanged",
    }
    if candidate.get("migration_contracts") != expected_contracts:
        raise ValueError("program migration contract reference drift")
    if candidate.get("migration_revision") != expected_revision:
        raise ValueError("program supersession identity drift")
    if candidate.get("status") != "draft_waiting_rc5_spec_freeze":
        raise ValueError("program status drift")
    normalized = copy.deepcopy(dict(candidate))
    normalized["migration_revision"] = baseline.get("migration_revision")
    normalized["revision_id"] = baseline.get("revision_id")
    normalized["status"] = baseline.get("status")
    return normalized


def _normalize_source(source: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(source))
    normalized.pop("supersession", None)
    return normalized
def _normalize_revision_only(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    schema_version: str,
) -> dict[str, Any]:
    if (
        candidate.get("revision_id") != CANDIDATE_REVISION_ID
        or candidate.get("schema_version") != schema_version
        or baseline.get("revision_id") != PREDECESSOR_REVISION_ID
        or baseline.get("schema_version") != schema_version
    ):
        raise ValueError("identity-only contract revision/schema drift")
    normalized = copy.deepcopy(dict(candidate))
    normalized["revision_id"] = baseline.get("revision_id")
    return normalized


def _normalize_fresh_worker(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_revision_only(
        candidate,
        baseline,
        schema_version="bb.rl.phase5.fresh_worker_handoff_contract.v2",
    )
    candidate_derivation = _require_mapping(
        normalized.get("derivation"), "candidate fresh-worker derivation"
    )
    baseline_derivation = _require_mapping(
        baseline.get("derivation"), "predecessor fresh-worker derivation"
    )
    if candidate_derivation.get("current_inactive_action") != (
        "await a new typed Kyle SPEC_FREEZE bound to the exact rc5 artifact "
        "manifest; the rc4 decision has no rc5 authority"
    ):
        raise ValueError("fresh-worker candidate authority drift")
    normalized["derivation"] = dict(candidate_derivation)
    normalized["derivation"]["current_inactive_action"] = (
        baseline_derivation.get("current_inactive_action")
    )
    return normalized


def _validate_catalog_score_archive(
    candidate_documents: Mapping[str, Mapping[str, Any]],
    archive_documents: Mapping[str, Mapping[str, Any]],
) -> None:
    catalog = candidate_documents["ASSURANCE_CATALOG.json"]
    items = _require_sequence(catalog.get("items"), "catalog items")
    if catalog.get("item_count") != 49 or catalog.get("catalog_points") != 1000:
        raise ValueError("catalog item/points drift")
    if len(items) != 49 or sum(item.get("points", -1) for item in items) != 1000:
        raise ValueError("catalog item/points drift")
    forbidden_award_fields = {"state", "awarded_points", "evidence_ids", "review_ids"}
    if any(forbidden_award_fields & set(_require_mapping(item, "catalog item")) for item in items):
        raise ValueError("score or award authority present in catalog")

    scorecard = archive_documents.get("SCORECARD.json")
    if scorecard is None:
        raise ValueError("sealed archive SCORECARD missing")
    score_items = _require_sequence(scorecard.get("items"), "archive scorecard items")
    if len(score_items) != 49:
        raise ValueError("score/archive drift")
    exact_fields = (
        "item_id",
        "description",
        "points",
        "proof_floor",
        "pass_predicate",
        "workstream",
    )
    for index, raw_item in enumerate(items):
        item = _require_mapping(raw_item, f"catalog item {index}")
        score_item = _require_mapping(score_items[index], f"archive score item {index}")
        for field in exact_fields:
            if item.get(field) != score_item.get(field):
                raise ValueError(f"score/archive drift: item {index} {field}")


def _validate_dag_and_zero_state(
    candidate_documents: Mapping[str, Mapping[str, Any]],
) -> None:
    catalog = candidate_documents["ASSURANCE_CATALOG.json"]
    graph = candidate_documents["WORK_PACKET_DAG.yaml"]
    nodes = _require_sequence(graph.get("nodes"), "DAG nodes")
    node_by_id: dict[str, Mapping[str, Any]] = {}
    for raw_node in nodes:
        node = _require_mapping(raw_node, "DAG node")
        node_id = node.get("id")
        if not isinstance(node_id, str) or node_id in node_by_id:
            raise ValueError("DAG drift: duplicate or invalid node")
        node_by_id[node_id] = node
    dependencies = {
        node_id: set(_require_sequence(node.get("depends_on"), f"DAG dependencies {node_id}"))
        for node_id, node in node_by_id.items()
    }
    for node_id, dependency_ids in dependencies.items():
        if dependency_ids - set(node_by_id):
            raise ValueError(f"DAG drift: missing dependency for {node_id}")
    remaining = {node_id: set(values) for node_id, values in dependencies.items()}
    while remaining:
        ready = {node_id for node_id, values in remaining.items() if not values}
        if not ready:
            raise ValueError("DAG drift: cycle")
        for node_id in ready:
            remaining.pop(node_id)
        for values in remaining.values():
            values.difference_update(ready)
    graph_rows = [
        row
        for node in nodes
        for row in _require_sequence(
            _require_mapping(node, "DAG node").get("score_rows"), "DAG score rows"
        )
    ]
    catalog_rows = [
        _require_mapping(item, "catalog item").get("item_id")
        for item in _require_sequence(catalog.get("items"), "catalog items")
    ]
    if len(graph_rows) != 49 or len(set(graph_rows)) != 49 or set(graph_rows) != set(catalog_rows):
        raise ValueError("DAG edge or score-row drift")

    queue = candidate_documents["RUN_QUEUE.json"]
    if queue.get("eligible") not in ([], False) or queue.get("target_lease") is not None:
        raise ValueError("queue eligibility drift")
    status = candidate_documents["DRAFT_STATUS.json"]
    assurance = _require_mapping(
        _require_mapping(status.get("tracks"), "status tracks").get("assurance"),
        "assurance status",
    )
    if (
        status.get("active") is not False
        or status.get("target_lease") is not None
        or status.get("internal_completion") is not False
        or assurance.get("current_verified_points") != 0
        or assurance.get("awarded_items") != []
        or assurance.get("evidence_ref_count") != 0
        or assurance.get("review_ref_count") != 0
    ):
        raise ValueError("score or award authority present in status")
    evidence = candidate_documents["EVIDENCE_INDEX.json"]
    if evidence.get("active_relations") != 0:
        raise ValueError("evidence admission drift")
    rows = _require_sequence(evidence.get("rows"), "evidence rows")
    if any(
        _require_mapping(row, "evidence row").get("active") is not False
        or _require_mapping(row, "evidence row").get("admitted") is not False
        for row in rows
    ):
        raise ValueError("evidence admission drift")
    authority = candidate_documents["AUTHORITY_POLICY.json"]
    cryptographic_trust = _require_mapping(
        authority.get("cryptographic_trust"), "cryptographic trust"
    )
    if (
        cryptographic_trust.get("state") != "not_provisioned"
        or cryptographic_trust.get("public_keys") != []
        or cryptographic_trust.get("required_before_first_target_campaign") is not True
    ):
        raise ValueError("target authority drift")


def validate_migration_contract(transaction: Mapping[str, Any]) -> None:
    if transaction.get("mode") != "stop_the_world_three_store_compensating_transaction":
        raise ValueError("migration transaction mode drift")
    commit_order = tuple(
        _require_sequence(transaction.get("commit_order"), "transaction commit_order")
    )
    stores = _require_sequence(transaction.get("stores"), "transaction stores")
    store_ids = tuple(
        _require_mapping(store, "transaction store").get("id") for store in stores
    )
    if len(stores) != 3 or store_ids != EXPECTED_TRANSACTION_STORES:
        raise ValueError("migration transaction must contain exactly three stores")
    if commit_order != EXPECTED_TRANSACTION_STORES:
        raise ValueError("selector must commit last")
    expected_receipt_production_order = [
        "pre_replay_inputs_complete",
        "migration_and_fresh_worker_replay_receipts_complete",
        "quiescence_release_intent_receipt_complete",
        "lease_released_and_file_descriptor_closed",
        "quiescence_post_release_receipt_complete",
        "session_post_handoff_receipt_complete",
        "migration_transaction_receipt_complete",
    ]
    if transaction.get("receipt_production_order") != expected_receipt_production_order:
        raise ValueError("migration receipt production order is cyclic or incomplete")
    if any("session" in str(store_id).lower() for store_id in store_ids):
        raise ValueError("session transaction store is forbidden")

    receipt = _require_mapping(transaction.get("receipt_required"), "transaction receipt")
    if receipt.get("emission") != (
        "emit and fsync this final immutable summary only after replay, "
        "release-intent, lease release, post-release, and session post-handoff "
        "receipts exist"
    ):
        raise ValueError("migration transaction receipt can precede its dependencies")
    if receipt.get("additional_fields_allowed") is not False:
        raise ValueError("transaction receipt is not strict")
    session_fields = _require_mapping(receipt.get("session_fields"), "session receipt fields")
    if session_fields != {
        "location": "outside stores and commit_order",
        "post_handoff_receipt": "SESSION_HANDOFF_CONTRACT.json post_handoff_receipt",
        "pre_handoff_receipt": "SESSION_HANDOFF_CONTRACT.json pre_handoff_receipt",
    }:
        raise ValueError("session must remain outside transaction stores")
    _require_field_set(
        receipt.get("fields"),
        {
            "migration_id",
            "lease_id",
            "quiescence_receipt_sha256",
            "session_pre_handoff_receipt_sha256",
            "dolt_adapter_kind",
            "migration_journal_sha256",
            "migration_journal_final_sequence",
            "authority_decision_sha256",
            "before_images",
            "after_images",
            "commit_results",
            "prepared_validation_sha256",
            "post_commit_hashes",
            "migration_replay_sha256",
            "fresh_worker_replay_sha256",
            "quiescence_release_intent_receipt_sha256",
            "quiescence_post_release_receipt_sha256",
            "session_post_handoff_receipt_sha256",
            "released_lease",
        },
        "transaction receipt",
    )
    _require_field_set(
        receipt.get("store_fields"),
        {
            "store_id",
            "presence",
            "parent_device",
            "parent_inode",
            "native_adapter",
            "native_revision",
            "bytes_sha256",
            "size",
            "schema_sha256",
            "canonical_rows_sha256",
            "rollback_operation_sha256",
            "reversible",
            "rollback_invariant",
        },
        "transaction store receipt",
    )
    _require_field_set(
        receipt.get("event_store_fields"),
        {
            "store_id",
            "before_presence",
            "before_head_sha256",
            "before_event_count",
            "parent_device",
            "parent_inode",
            "absence_rechecked",
            "genesis_created",
            "genesis_predecessor_sha256",
            "creation_device",
            "creation_inode",
            "committed_event_sha256s",
            "after_head_sha256",
            "file_fsync_ack",
            "compensation_event_sha256",
            "compensation_head_sha256",
            "parent_fsync_ack",
        },
        "event store receipt",
    )
    _require_field_set(
        receipt.get("failure_fields"),
        {
            "failed_phase",
            "failed_store",
            "error",
            "rollback_results",
            "rollback_invariant_results",
            "rollback_post_state_hashes",
            "event_compensation_sha256",
            "rollback_session_handoff_sha256",
            "quarantined",
        },
        "transaction failure receipt",
    )
    locking = _require_mapping(transaction.get("locking"), "transaction locking")
    expected_locking = {
        "client_policy": "BreadBoard, bd/Dolt, and OMP/RPC clients remain stopped; no domain-error behavior is claimed",
        "journal": "durable append-only per-step intent/applied/verified journal is held and fsynced through commit, rollback, replay, handoff, and release",
        "lease_scope": [
            "root_active_selector",
            "beads_projection",
            "v2_event_log",
            "program_root",
            "beads_data_directory",
        ],
        "owner": "external supervisor identified by pid, process group, process-start identity, stable lock inode, and migration_id",
        "release": "after stores, replay, and zero-authority verification, emit and fsync the immutable release-intent receipt while held; release the stable-inode flock and close its file descriptor; then emit the immutable post-release receipt before any fresh OMP/RPC session",
        "rule": "one migration_id and one supervisor-owned verified stable-inode held-flock lease; initial O_CREAT|O_EXCL is provisioning evidence only, and ownership, journal, adapter, or quiescence loss immediately enters failure_contract",
    }
    if locking != expected_locking:
        raise ValueError("stable lock or durable journal contract drift")

    event_store = _require_mapping(stores[0], "event store")
    expected_before_state = {
        "absent": "presence=absent, bytes_sha256=null, size=null, before_head_sha256=null, before_event_count=0, and exact parent device/inode captured",
        "present": "presence=present with exact bytes, size, verified chain head, event count, and parent device/inode",
    }
    expected_prepare = (
        "stage exact canonical event bytes and predecessor hashes; never synthesize "
        "an empty before-image for an absent log"
    )
    expected_commit = (
        "if present, append immutable V1_LINEAGE_IMPORTED and V2_ACTIVATED with the "
        "captured predecessor; if absent, recheck absence and parent identity, "
        "exclusively create with O_CREAT|O_EXCL and a predecessor-null genesis "
        "V1_LINEAGE_IMPORTED event, then append V2_ACTIVATED"
    )
    if (
        event_store.get("before_state") != expected_before_state
        or event_store.get("prepare") != expected_prepare
        or event_store.get("commit") != expected_commit
    ):
        raise ValueError("event absence/genesis contract incomplete")


def validate_quiescence_contract(quiescence: Mapping[str, Any]) -> None:
    if quiescence.get("mode") != "out_of_band_supervisor_owned_stop_the_world":
        raise ValueError("quiescence is not out-of-band stop-the-world")
    client_behavior = _require_mapping(
        quiescence.get("client_behavior"), "quiescence client behavior"
    )
    if client_behavior != {
        "claim": "clients are paused outside the migration window and restarted only by the supervisor",
        "domain_error_claimed": False,
        "forbidden_claim": "ordinary readers receive MIGRATION_IN_PROGRESS",
        "new_clients": "the out-of-band supervisor freezes intake and refuses or stops new BreadBoard, bd/Dolt, and OMP/RPC clients while the lease is held",
    }:
        raise ValueError("MIGRATION_IN_PROGRESS client claim is forbidden")

    discovery = _require_mapping(
        quiescence.get("adapter_discovery"), "adapter discovery"
    )
    expected_discovery = {
        "allowed_dolt_adapters": ["embedded_dolt_cli", "sql_server"],
        "embedded_dolt_cli": {
            "discovery": "bd context --json must report direct or embedded mode and a database path; resolve the actual Dolt repository below .beads/embeddeddolt/<database>",
            "head": "run the installed dolt CLI in the resolved repository and capture the full commit and root from dolt log, never a truncated display value",
            "status": "run the installed dolt CLI in the resolved repository and prove clean working and staged roots",
            "transaction": "run one native transaction using the direct dolt sql CLI in the resolved repository, then one Dolt commit",
            "unsupported": "bd sql is not used or claimed to work in embedded/direct mode",
        },
        "execution_boundary": "run discovery only after the OMP session is closed and under the spawn-frozen supervisor; bd context or SQL may connect to or start Dolt, so inventory every discovery child, stop and reap it after capture, and never describe discovery as a pure read-only preflight",
        "fail_closed": "unknown, unsupported, conflicting, or ambiguous mode, repository, database, branch, socket, DSN, or adapter discovery makes migration non-executable",
        "runtime_evidence": [
            "exact bd context output",
            "bd version",
            "dolt version",
            "resolved store and repository paths",
            "database and branch",
            "adapter selection rationale",
        ],
        "sql_server": {
            "discovery": "bd context plus process and descriptor scans must identify an actual server endpoint and bind its socket or DSN before any bd sql operation is legal",
            "head": "bd sql \"SELECT commit_hash FROM dolt_log ORDER BY date DESC LIMIT 1\"",
            "status": "bd sql \"SELECT table_name, staged, status FROM dolt_status\"",
            "transaction": "one native server transaction followed by one DOLT_COMMIT",
        },
    }
    if discovery != expected_discovery:
        raise ValueError("unknown runtime adapter or adapter fail-closed drift")
    expected_lease_contract = {
        "acquire": "provision the stable lock path once with O_CREAT|O_EXCL, nofollow validation, and file/parent fsync if absent; every migration then opens the existing verified inode without following symlinks and continuously holds an exclusive advisory OS flock on that file descriptor",
        "durability": "initialize and fsync the migration journal before spawn/intake freeze, then append and fsync intent/applied/verified records",
        "identity": "bind migration_id, supervisor pid/pgid/start identity, OS, program root device/inode, Beads store root device/inode, lock device/inode, and adapter",
        "release": "while the flock is held, emit and fsync an immutable release-intent receipt; then unlock and close the held file descriptor and emit a distinct immutable post-release receipt that binds the intent and completed release facts; retain the stable lock inode and journal",
        "scope_limit": "the advisory lock cannot stop escaped processes, so repeated process/descriptor identity scans remain mandatory",
    }
    if quiescence.get("lease_contract") != expected_lease_contract:
        raise ValueError("stable lock or durable journal ordering drift")

    receipt = _require_mapping(quiescence.get("receipt_contract"), "quiescence receipt")
    if receipt.get("additional_fields_allowed") is not False:
        raise ValueError("quiescence receipt is not strict")
    required_field_groups = {
        "required_fields": {
            "migration_id",
            "supervisor_identity",
            "platform_adapter",
            "lease",
            "journal",
            "breadboard_processes",
            "bd_dolt_processes",
            "omp_rpc_session",
            "closed_transcript",
            "child_transcript_manifest",
            "prior_todo_projection",
            "dolt_adapter",
            "dolt_snapshot",
            "filesystem_roots",
            "descriptor_scans",
            "inventory_sha256",
            "quiesced_at",
            "result",
        },
        "supervisor_identity_fields": {
            "pid", "ppid", "pgid", "sid", "uid", "start_identity", "executable", "argv_sha256", "os"
        },
        "child_transcript_fields": {
            "session_id", "parent_session_id", "path", "size", "sha256", "final_cursor"
        },
        "filesystem_root_fields": {
            "kind", "path", "device", "inode", "mode"
        },
        "prior_todo_projection_fields": {
            "source", "path", "size", "sha256", "transcript_cursor",
            "transcript_event_sha256", "captured_at", "cache_authority"
        },
        "process_entry_fields": {
            "kind", "adapter", "pid", "ppid", "pgid", "sid", "uid", "start_identity", "executable", "argv_sha256", "cwd", "root_or_datadir", "discovered_at", "identity_revalidated_at", "stop_method", "stopped_at", "exit_status"
        },
        "descriptor_scan_fields": {
            "platform_adapter", "backend", "root", "started_at", "completed_at", "process_snapshot_sha256", "targets", "coverage", "permission_errors", "result"
        },
        "descriptor_target_fields": {
            "pid", "start_identity", "descriptor", "kind", "device", "inode", "resolved_path", "socket_target"
        },
        "lease_fields": {
            "lease_id", "migration_id", "path", "device", "inode", "holder_pid", "holder_pgid", "holder_start_identity", "os", "adapter_kind", "program_root", "beads_data_directory", "provisioned_with_exclusive_create", "flock_held", "acquired_at"
        },
        "journal_fields": {
            "path", "device", "inode", "opened_at", "sha256", "fsynced_through_sequence"
        },
        "dolt_adapter_fields": {
            "adapter_kind", "discovery_evidence_sha256", "bd_version", "dolt_version", "mode", "store_root", "repository_path", "database", "branch", "server_socket_or_dsn"
        },
        "dolt_snapshot_fields": {
            "adapter_kind", "database", "branch", "store_root", "repository_path", "head_commit", "head_root", "staged_root", "working_root", "status_sha256", "schema_sha256", "canonical_rows_sha256", "clean"
        },
        "omp_rpc_session_fields": {
            "session_id", "pid", "ppid", "pgid", "sid", "uid", "start_identity", "cwd", "state_before_abort", "state_after_abort", "abort_sent", "flush_outcome", "forced_or_timeout_kill", "process_exit_status", "reaped", "commit_prohibited"
        },
        "closed_transcript_fields": {
            "session_id", "cwd", "path", "size", "sha256", "title_slot_size", "title_slot_sha256", "session_header_sha256", "final_cursor", "final_event_sha256", "final_nonempty_record_sha256", "flush_outcome", "supervisor_fsynced_file", "supervisor_fsynced_parent", "stability_observations_sha256", "open_fd_count", "closed_after_process_exit", "snapshot_kind"
        },
    }
    for field_name, required_fields in required_field_groups.items():
        _require_field_set(
            receipt.get(field_name), required_fields, f"quiescence {field_name}"
        )

    acquisition_invariants = set(
        _require_sequence(
            receipt.get("acquisition_receipt_invariants"),
            "quiescence acquisition invariants",
        )
    )
    if acquisition_invariants != {
        "lease is held",
        "released_at is not a field",
        "receipt bytes never change after hashing",
        "result pass requires native_ack or graceful_process_exit",
        "forced_or_timeout_kill requires result fail and commit_prohibited true",
    }:
        raise ValueError("quiescence acquisition or forced-kill invariant drift")
    flush_rules = _require_mapping(
        receipt.get("flush_outcome_rules"), "flush outcome rules"
    )
    expected_flush_rules = {
        "failure_only": {
            "allowed": ["forced_or_timeout_kill_without_flush"],
            "invariants": [
                "result is fail",
                "forced_or_timeout_kill is true",
                "commit_prohibited is true",
            ],
        },
        "success_only": {
            "allowed": ["native_ack", "graceful_process_exit"],
            "invariants": [
                "result is pass",
                "forced_or_timeout_kill is false",
                "commit_prohibited is false",
            ],
        },
    }
    if flush_rules != expected_flush_rules:
        raise ValueError("forced kill accepted as success")

    release_contracts = _require_mapping(
        receipt.get("release_receipt_contracts"), "release receipt contracts"
    )
    if set(release_contracts) != {
        "release_intent_receipt",
        "post_release_receipt",
    }:
        raise ValueError("release receipt contract set drift")
    intent = _require_mapping(
        release_contracts.get("release_intent_receipt"), "release intent receipt"
    )
    post = _require_mapping(
        release_contracts.get("post_release_receipt"), "post-release receipt"
    )
    if (
        intent.get("additional_fields_allowed") is not False
        or post.get("additional_fields_allowed") is not False
    ):
        raise ValueError("release receipt schema is not strict")
    intent_fields = {
        "migration_id",
        "acquisition_receipt_sha256",
        "lease_id",
        "lease_device",
        "lease_inode",
        "journal_final_sequence",
        "journal_sha256",
        "verified_stores_sha256",
        "migration_replay_sha256",
        "zero_authority",
        "release_intent_at",
        "flock_held",
        "receipt_sha256",
    }
    post_fields = {
        "migration_id",
        "release_intent_receipt_sha256",
        "lease_id",
        "lease_device",
        "lease_inode",
        "flock_released_at",
        "file_descriptor_closed",
        "post_release_journal_sha256",
        "receipt_sha256",
    }
    _require_field_set(
        intent.get("required_fields"), intent_fields, "release intent receipt"
    )
    _require_field_set(
        post.get("required_fields"), post_fields, "post-release receipt"
    )
    if (
        intent.get("receipt_sha256_projection")
        != "SHA-256 of canonical release-intent receipt with receipt_sha256 omitted"
        or post.get("receipt_sha256_projection")
        != "SHA-256 of canonical post-release receipt with receipt_sha256 omitted"
    ):
        raise ValueError("release receipt digest projection drift")
    future_facts = {"flock_released_at", "file_descriptor_closed"}
    if future_facts & intent_fields or not future_facts.issubset(post_fields):
        raise ValueError("release receipt future-fact causality violated")


def validate_session_handoff_contract(session: Mapping[str, Any]) -> None:
    model = _require_mapping(session.get("handoff_model"), "session handoff model")
    if model.get("session_store_role") != (
        "typed pre/post handoff outside transaction stores; never an in-place queue/todo commit store"
    ):
        raise ValueError("session must remain outside transaction stores")
    post = _require_mapping(session.get("post_handoff_receipt"), "post handoff receipt")
    if post.get("additional_fields_allowed") is not False:
        raise ValueError("post handoff receipt is not strict")
    if post.get("receipt_sha256_projection") != (
        "SHA-256 of the canonical post-handoff receipt with receipt_sha256 omitted"
    ):
        raise ValueError("post handoff receipt digest projection drift")
    if set(_require_sequence(post.get("allowed_handoff_kinds"), "handoff kinds")) != {
        "committed_cutover",
        "rolled_back",
    }:
        raise ValueError("fresh-session handoff kind drift")
    invariants = set(_require_sequence(post.get("invariants"), "post handoff invariants"))
    required_invariants = {
        "new_session_id differs from prior_session_id",
        "capabilities is empty",
        "active_authority is false",
        "score_authority is false",
        "checkpoint_authority is false",
        "target_execution_allowed is false",
        "ambient_inputs_used is empty",
    }
    if invariants != required_invariants:
        raise ValueError("fresh-session handoff or zero-authority invariant drift")
    _require_field_set(
        post.get("required_fields"),
        {
            "migration_id",
            "handoff_kind",
            "pre_handoff_receipt_sha256",
            "new_session_id",
            "new_session_cwd",
            "new_session_transcript_path",
            "new_session_header_sha256",
            "parent_session_id",
            "started_at",
            "consumed_input_hashes",
            "quiescence_post_release_receipt_sha256",
            "selector_receipt_sha256",
            "event_receipt_sha256",
            "dolt_receipt_sha256",
            "derived_action",
            "execution_frontier",
            "capabilities",
            "active_authority",
            "score_authority",
            "checkpoint_authority",
            "target_execution_allowed",
            "ambient_inputs_used",
            "receipt_sha256",
        },
        "post handoff receipt",
    )
    pre = _require_mapping(session.get("pre_handoff_receipt"), "pre handoff receipt")
    if pre.get("additional_fields_allowed") is not False:
        raise ValueError("pre handoff receipt is not strict")
    if pre.get("receipt_sha256_projection") != (
        "SHA-256 of the canonical pre-handoff receipt with receipt_sha256 omitted"
    ):
        raise ValueError("pre handoff receipt digest projection drift")
    _require_field_set(
        pre.get("derived_handoff_fields"),
        {
            "program_state",
            "allowed_next",
            "execution_frontier",
            "capabilities",
            "active_authority",
            "score_authority",
            "checkpoint_authority",
            "target_execution_allowed",
            "nonclaims",
        },
        "pre handoff derived fields",
    )
    _require_field_set(
        pre.get("frozen_program_input_fields"),
        {"path", "sha256", "size"},
        "pre handoff frozen input",
    )
    _require_field_set(
        pre.get("required_fields"),
        {
            "migration_id",
            "quiescence_receipt_sha256",
            "prior_session_id",
            "prior_session_cwd",
            "closed_transcript_path",
            "closed_transcript_sha256",
            "closed_transcript_title_slot_sha256",
            "closed_transcript_session_header_sha256",
            "closed_transcript_size",
            "closed_transcript_final_cursor",
            "closed_transcript_final_event_sha256",
            "child_transcript_manifest_sha256",
            "prior_todo_projection_sha256",
            "prior_todo_projection_size",
            "prior_todo_projection_cursor",
            "frozen_program_inputs",
            "derived_handoff",
            "receipt_sha256",
            "created_at",
        },
        "pre handoff receipt",
    )


def validate_replay_contract(replay: Mapping[str, Any]) -> None:
    expected_inputs = {
        "ARTIFACT_MANIFEST.json",
        "MIGRATION_PLAN.json",
        "MIGRATION_TRANSACTION.json",
        "QUIESCENCE_CONTRACT.json",
        "SESSION_HANDOFF_CONTRACT.json",
        "QUIESCENCE_RECEIPT.json",
        "SESSION_PRE_HANDOFF_RECEIPT.json",
        "MIGRATION_JOURNAL.jsonl",
        "captured before-images and prepared or committed after-image receipts available before replay",
        "rollback store receipts written before rollback replay",
    }
    actual_inputs = set(
        _require_sequence(replay.get("allowed_inputs"), "migration replay inputs")
    )
    if actual_inputs != expected_inputs:
        raise ValueError("migration replay consumes a future receipt or omits a pre-replay input")
    isolation = _require_mapping(replay.get("isolation"), "migration replay isolation")
    if isolation != {
        "ambient_inputs_forbidden": [
            "live stores",
            "live session state",
            "chat history",
            "agent memory",
            "scratch evidence",
            "target state",
            "score state",
        ],
        "cwd": "new empty temporary directory",
        "environment": "allowlist only",
        "minimum_processes": 2,
    }:
        raise ValueError("migration replay permits live or authoritative input")
    expected_requirements = [
        "validate every allowed pre-replay input digest and journal link without opening a live store",
        "replay the exact three-store commit with root selector last through the pre-replay receipt-selected embedded_dolt_cli or sql_server adapter semantics",
        "accept an absent event-log before-state only when presence is absent, event count is zero, predecessor is null, parent identity is bound, and exclusive genesis creation plus absence recheck is recorded",
        "replay rollback from store receipts written before replay, then derive the expected fresh-session handoff semantics from the prior pre-handoff contract without consuming release, post-release, post-handoff, or transaction receipts",
        "verify every crash-boundary fixture reaches the unique committed or compensated logical result idempotently",
        "require byte-identical semantic outputs from two isolated processes",
    ]
    if replay.get("replay_requirements") != expected_requirements:
        raise ValueError("migration replay invariant drift")
    receipt = _require_mapping(replay.get("receipt_contract"), "migration replay receipt")
    if receipt.get("additional_fields_allowed") is not False:
        raise ValueError("migration replay receipt is not strict")
    _require_field_set(
        receipt.get("required_fields"),
        {
            "migration_id",
            "contract_sha256",
            "worker_count",
            "worker_semantic_sha256",
            "workers",
            "crash_fixture_results",
            "result",
        },
        "migration replay receipt",
    )
    _require_field_set(
        receipt.get("each_worker_fields"),
        {"pid", "input_hashes", "output", "semantic_sha256", "ambient_inputs_used"},
        "migration replay worker receipt",
    )
    _require_field_set(
        receipt.get("store_result_fields"),
        {
            "store_id",
            "before_presence",
            "before_sha256",
            "after_sha256",
            "adapter_kind",
            "commit_valid",
            "rollback_valid",
            "journal_sequences",
        },
        "migration replay store result",
    )
    _require_field_set(
        receipt.get("journal_step_fields"),
        {
            "sequence",
            "migration_id",
            "operation_id",
            "store_id",
            "phase",
            "intent_sha256",
            "effect_sha256",
            "previous_record_sha256",
            "record_sha256",
            "fsynced",
        },
        "migration replay journal",
    )
    _require_field_set(
        receipt.get("worker_output_fields"),
        {
            "migration_id",
            "mode",
            "input_hashes",
            "quiescence_valid",
            "adapter_kind",
            "journal_valid",
            "commit_order",
            "store_results",
            "selector_committed_last",
            "rollback_results",
            "session_handoff_result",
            "zero_authority",
            "semantic_sha256",
        },
        "migration replay worker output",
    )


def _validate_fresh_worker_contract(fresh: Mapping[str, Any]) -> None:
    required_inputs = EXPECTED_CANDIDATE_FILES & {
        "FRESH_WORKER_HANDOFF_CONTRACT.json",
        "PROGRAM_SPEC.yaml",
        "WORK_PACKET_DAG.yaml",
        "RUN_QUEUE.json",
        "DRAFT_STATUS.json",
        "SOURCE_MANIFEST.json",
        "MIGRATION_PLAN.json",
        "MIGRATION_TRANSACTION.json",
        "QUIESCENCE_CONTRACT.json",
        "SESSION_HANDOFF_CONTRACT.json",
        "MIGRATION_REPLAY_CONTRACT.json",
    }
    required_inputs = set(required_inputs) | {"ARTIFACT_MANIFEST.json"}
    if set(_require_sequence(fresh.get("allowed_inputs"), "fresh-worker inputs")) != required_inputs:
        raise ValueError("fresh-worker input set drift")
    derivation = _require_mapping(fresh.get("derivation"), "fresh-worker derivation")
    expected_derivation = {
        "current_inactive_action": "await a new typed Kyle SPEC_FREEZE bound to the exact rc5 artifact manifest; the rc4 decision has no rc5 authority",
        "post_cutover_execution_frontier": ["AT0"],
        "post_cutover_nonexecuting_preparation": [
            "author a new SHARED_TRANSPORT repair packet without submission authority"
        ],
        "post_freeze_pre_cutover_action": "complete supervisor-owned quiescence, prepare and review the three-store migration plus typed session handoff; no execution",
        "target_execution_allowed": False,
    }
    if derivation != expected_derivation:
        raise ValueError("fresh-worker replay grants authority")
    receipt = _require_mapping(fresh.get("receipt"), "fresh-worker receipt")
    if receipt.get("additional_fields_allowed") is not False:
        raise ValueError("fresh-worker receipt is not strict")
    _require_field_set(
        receipt.get("each_worker_fields"),
        {
            "pid",
            "input_hashes",
            "derived_action",
            "execution_frontier",
            "target_execution_allowed",
            "ambient_inputs_used",
        },
        "fresh-worker worker receipt",
    )
    _require_field_set(
        receipt.get("top_level_fields"),
        {
            "artifact_manifest_sha256",
            "contract_sha256",
            "worker_count",
            "worker_semantic_sha256",
            "result",
        },
        "fresh-worker top-level receipt",
    )
    isolation = _require_mapping(fresh.get("isolation"), "fresh-worker isolation")
    if isolation.get("minimum_processes") != 2:
        raise ValueError("fresh-worker replay process count drift")
    forbidden = set(
        _require_sequence(
            isolation.get("ambient_inputs_forbidden"), "fresh-worker forbidden inputs"
        )
    )
    if forbidden != {
        "chat history",
        "agent memory",
        "legacy todo state",
        "scratch evidence",
        "unversioned execution-root artifacts",
        "prior worker process state",
        "live session state",
        "live migration stores",
    }:
        raise ValueError("fresh-worker replay permits ambient state")


def validate_zero_authority_projection(
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    status = documents["DRAFT_STATUS.json"]
    tracks = _require_mapping(status.get("tracks"), "status tracks")
    assurance = _require_mapping(tracks.get("assurance"), "assurance status")
    training = _require_mapping(tracks.get("training_proof"), "training status")
    queue = documents["RUN_QUEUE.json"]
    evidence = documents["EVIDENCE_INDEX.json"]
    program = documents["PROGRAM_SPEC.yaml"]
    program_tracks = _require_mapping(program.get("tracks"), "program tracks")
    training_contract = _require_mapping(
        program_tracks.get("training_proof"), "program training track"
    )
    migration_revision = _require_mapping(
        program.get("migration_revision"), "program migration revision"
    )
    handoff = _require_mapping(
        documents["SESSION_HANDOFF_CONTRACT.json"].get("post_handoff_receipt"),
        "post handoff receipt",
    )
    handoff_invariants = set(
        _require_sequence(handoff.get("invariants"), "post handoff invariants")
    )
    fresh_derivation = _require_mapping(
        documents["FRESH_WORKER_HANDOFF_CONTRACT.json"].get("derivation"),
        "fresh-worker derivation",
    )
    evidence_rows = _require_sequence(evidence.get("rows"), "evidence rows")
    shared_transport = _require_mapping(
        status.get("shared_transport"), "shared transport status"
    )
    evidence_rows_active_or_admitted = any(
        _require_mapping(row, "evidence row").get("active") is not False
        or _require_mapping(row, "evidence row").get("admitted") is not False
        for row in evidence_rows
    )
    expected_handoff_invariants = {
        "new_session_id differs from prior_session_id",
        "capabilities is empty",
        "active_authority is false",
        "score_authority is false",
        "checkpoint_authority is false",
        "target_execution_allowed is false",
        "ambient_inputs_used is empty",
    }
    projection = {
        "active_authority": status.get("active"),
        "program_status": program.get("status"),
        "prior_spec_freeze_authority": migration_revision.get(
            "prior_spec_freeze_authority_for_candidate"
        ),
        "queue_state": queue.get("state"),
        "target_admitted": shared_transport.get("admitted_hash") is not None,
        "score_authority": bool(
            assurance.get("current_verified_points")
            or assurance.get("awarded_items")
            or assurance.get("evidence_ref_count")
            or assurance.get("review_ref_count")
        ),
        "checkpoint_authority": status.get("checkpoint_disposition") != "unclaimed",
        "capabilities": (
            []
            if "capabilities is empty" in handoff_invariants
            else ["contract_drift"]
        ),
        "evidence_admission": bool(
            evidence.get("active_relations") or evidence_rows_active_or_admitted
        ),
        "execution_allowed": fresh_derivation.get("target_execution_allowed"),
        "active_attempt": status.get("active_attempt"),
        "active_packet": status.get("active_packet"),
        "target_lease": status.get("target_lease"),
        "target_execution_allowed": fresh_derivation.get(
            "target_execution_allowed"
        ),
        "queue_eligible": queue.get("eligible"),
        "queue_target_lease": queue.get("target_lease"),
        "current_verified_points": assurance.get("current_verified_points"),
        "awarded_items": assurance.get("awarded_items"),
        "score_evidence_refs": assurance.get("evidence_ref_count"),
        "score_review_refs": assurance.get("review_ref_count"),
        "checkpoint_disposition": status.get("checkpoint_disposition"),
        "internal_completion": status.get("internal_completion"),
        "promotion": status.get("promotion"),
        "external_acceptance": status.get("external_acceptance"),
        "shared_transport": status.get("shared_transport"),
        "training_completion_decision": training.get("completion_decision"),
        "training_satisfied": training.get("satisfied"),
        "training_score_field_present": training.get("score_field_present"),
        "training_track_scored": training_contract.get("scored"),
        "evidence_active_relations": evidence.get("active_relations"),
        "evidence_rows_active_or_admitted": evidence_rows_active_or_admitted,
        "handoff_invariants": sorted(handoff_invariants),
    }
    expected = {
        "active_authority": False,
        "program_status": "draft_waiting_rc5_spec_freeze",
        "prior_spec_freeze_authority": False,
        "queue_state": "DRAFT_WAITING_SPEC_FREEZE",
        "target_admitted": False,
        "score_authority": False,
        "checkpoint_authority": False,
        "capabilities": [],
        "evidence_admission": False,
        "execution_allowed": False,
        "active_attempt": None,
        "active_packet": None,
        "target_lease": None,
        "target_execution_allowed": False,
        "queue_eligible": [],
        "queue_target_lease": None,
        "current_verified_points": 0,
        "awarded_items": [],
        "score_evidence_refs": 0,
        "score_review_refs": 0,
        "checkpoint_disposition": "unclaimed",
        "internal_completion": False,
        "promotion": {"authorized": False, "state": "unclaimed"},
        "external_acceptance": {"authority": "Zyphra only", "state": "unclaimed"},
        "shared_transport": {
            "admitted_hash": None,
            "smoke_job": None,
            "state": "blocked",
        },
        "training_completion_decision": None,
        "training_satisfied": False,
        "training_score_field_present": False,
        "training_track_scored": False,
        "evidence_active_relations": 0,
        "evidence_rows_active_or_admitted": False,
        "handoff_invariants": sorted(expected_handoff_invariants),
    }
    if projection != expected:
        raise ValueError("zero-authority projection drift")
    return {**projection, "zero_authority": True}


def _validate_migration_plan(plan: Mapping[str, Any]) -> None:
    if (
        plan.get("mode") != "supervisor_owned_stop_the_world_compensating_cutover"
        or plan.get("transaction") != "MIGRATION_TRANSACTION.json"
        or plan.get("quiescence_contract") != "QUIESCENCE_CONTRACT.json"
        or plan.get("session_handoff_contract") != "SESSION_HANDOFF_CONTRACT.json"
        or plan.get("migration_replay_contract") != "MIGRATION_REPLAY_CONTRACT.json"
        or plan.get("fresh_worker_contract") != "FRESH_WORKER_HANDOFF_CONTRACT.json"
    ):
        raise ValueError("migration plan contract reference drift")
    superseded = _require_mapping(plan.get("superseded_rc4"), "superseded rc4")
    if superseded != {
        "artifact_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "revision_id": PREDECESSOR_REVISION_ID,
        "spec_freeze_grants_rc5_authority": False,
        "supersession_scope": "migration and cutover mechanics only",
    }:
        raise ValueError("rc4 authority reused")
    post_cutover = _require_mapping(plan.get("post_cutover"), "post-cutover state")
    if (
        post_cutover.get("target_execution_allowed") is not False
        or post_cutover.get("execution_frontier") != ["AT0"]
    ):
        raise ValueError("migration plan grants target authority")
    if plan.get("source_entries_captured_at_build") != EXPECTED_SOURCE_ENTRIES:
        raise ValueError("source-count drift")


def _validate_source_manifests(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any]
) -> None:
    if (
        candidate.get("program_id") != PROGRAM_ID
        or candidate.get("schema_version") != "bb.rl.phase5.source_manifest.v3"
        or baseline.get("program_id") != PROGRAM_ID
        or baseline.get("schema_version") != "bb.rl.phase5.source_manifest.v3"
    ):
        raise ValueError("source revision/schema identity drift")
    if _normalize_source(candidate) != _normalize_source(baseline):
        raise ValueError("source semantics drift")
    supersession = candidate.get("supersession")
    if supersession != {
        "candidate_revision_id": CANDIDATE_REVISION_ID,
        "prior_spec_freeze_grants_candidate_authority": False,
        "scope": "migration and cutover mechanics only",
        "superseded_artifact_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "superseded_revision_id": PREDECESSOR_REVISION_ID,
    }:
        raise ValueError("source supersession identity drift")
    repositories = _require_sequence(candidate.get("repositories"), "source repositories")
    entry_count = 0
    for raw_repository in repositories:
        repository = _require_mapping(raw_repository, "source repository")
        entries = _require_sequence(repository.get("entries"), "source entries")
        if repository.get("dirty_entries") != len(entries):
            raise ValueError("source-count drift")
        entry_count += len(entries)
        if any(
            _require_mapping(entry, "source entry").get("adoption_state")
            != "paused_unadmitted"
            for entry in entries
        ):
            raise ValueError("source entry gained adoption authority")
    if entry_count != EXPECTED_SOURCE_ENTRIES:
        raise ValueError("source-count drift")


def validate_contract_invariants(
    candidate_documents: Mapping[str, Mapping[str, Any]],
    predecessor_documents: Mapping[str, Mapping[str, Any]],
    archive_documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    for name in EXPECTED_CANDIDATE_FILES:
        if name not in candidate_documents:
            raise ValueError(f"missing candidate contract document: {name}")
    for name in EXPECTED_PREDECESSOR_FILES:
        if name not in predecessor_documents:
            raise ValueError(f"missing predecessor contract document: {name}")

    normalized: dict[str, dict[str, Any]] = {}

    def record_equal(
        domain: str,
        candidate_value: Mapping[str, Any],
        predecessor_value: Mapping[str, Any],
    ) -> None:
        if candidate_value != predecessor_value:
            label = "DAG" if domain == "dag" else domain
            raise ValueError(f"{label} drift")
        candidate_digest = sha256_bytes(canonical_bytes(candidate_value))
        predecessor_digest = sha256_bytes(canonical_bytes(predecessor_value))
        normalized[domain] = {
            "candidate_normalized_sha256": candidate_digest,
            "equal": candidate_digest == predecessor_digest,
            "predecessor_normalized_sha256": predecessor_digest,
        }

    for domain, names in EXACT_SEMANTIC_DOMAINS.items():
        record_equal(
            domain,
            _domain_projection(candidate_documents, names),
            _domain_projection(predecessor_documents, names),
        )

    candidate_status = candidate_documents["DRAFT_STATUS.json"]
    predecessor_status = predecessor_documents["DRAFT_STATUS.json"]
    record_equal(
        "status",
        _normalize_status(candidate_status, predecessor_status),
        predecessor_status,
    )

    candidate_program = candidate_documents["PROGRAM_SPEC.yaml"]
    predecessor_program = predecessor_documents["PROGRAM_SPEC.yaml"]
    record_equal(
        "program",
        _normalize_program(candidate_program, predecessor_program),
        predecessor_program,
    )

    _validate_source_manifests(
        candidate_documents["SOURCE_MANIFEST.json"],
        predecessor_documents["SOURCE_MANIFEST.json"],
    )
    record_equal(
        "source_policy",
        _normalize_source(candidate_documents["SOURCE_MANIFEST.json"]),
        _normalize_source(predecessor_documents["SOURCE_MANIFEST.json"]),
    )
    record_equal(
        "quiescence_identity",
        _normalize_revision_only(
            candidate_documents["QUIESCENCE_CONTRACT.json"],
            predecessor_documents["QUIESCENCE_CONTRACT.json"],
            schema_version="bb.rl.phase5.quiescence_contract.v1",
        ),
        predecessor_documents["QUIESCENCE_CONTRACT.json"],
    )
    record_equal(
        "session_handoff_identity",
        _normalize_revision_only(
            candidate_documents["SESSION_HANDOFF_CONTRACT.json"],
            predecessor_documents["SESSION_HANDOFF_CONTRACT.json"],
            schema_version="bb.rl.phase5.session_handoff_contract.v1",
        ),
        predecessor_documents["SESSION_HANDOFF_CONTRACT.json"],
    )
    record_equal(
        "fresh_worker_identity",
        _normalize_fresh_worker(
            candidate_documents["FRESH_WORKER_HANDOFF_CONTRACT.json"],
            predecessor_documents["FRESH_WORKER_HANDOFF_CONTRACT.json"],
        ),
        predecessor_documents["FRESH_WORKER_HANDOFF_CONTRACT.json"],
    )

    _validate_catalog_score_archive(candidate_documents, archive_documents)
    _validate_dag_and_zero_state(candidate_documents)
    validate_migration_contract(candidate_documents["MIGRATION_TRANSACTION.json"])
    validate_quiescence_contract(candidate_documents["QUIESCENCE_CONTRACT.json"])
    validate_session_handoff_contract(candidate_documents["SESSION_HANDOFF_CONTRACT.json"])
    validate_replay_contract(candidate_documents["MIGRATION_REPLAY_CONTRACT.json"])
    _validate_fresh_worker_contract(
        candidate_documents["FRESH_WORKER_HANDOFF_CONTRACT.json"]
    )
    _validate_migration_plan(candidate_documents["MIGRATION_PLAN.json"])
    validate_zero_authority_projection(candidate_documents)
    return dict(sorted(normalized.items()))


def _load_revision_documents(
    root: Path, rows: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    return {name: load_object(root / name) for name in sorted(rows)}


def _validate_manifest_identities(
    candidate_manifest: Mapping[str, Any],
    predecessor_manifest: Mapping[str, Any],
    archive_manifest: Mapping[str, Any],
) -> None:
    if set(candidate_manifest) != {
        "archive_manifest_sha256",
        "files",
        "immutable",
        "program_id",
        "revision_id",
        "schema_version",
        "superseded_artifact_manifest_sha256",
        "superseded_revision_id",
        "supersession_scope",
        "v1_active_status_sha256",
        "v1_scorecard_sha256",
    }:
        raise ValueError("candidate manifest schema drift")
    if set(predecessor_manifest) != {
        "archive_manifest_sha256",
        "files",
        "immutable",
        "program_id",
        "revision_id",
        "schema_version",
        "superseded_artifact_manifest_sha256",
        "superseded_revision_id",
        "supersession_scope",
        "v1_active_status_sha256",
        "v1_scorecard_sha256",
    }:
        raise ValueError("predecessor manifest schema drift")
    if set(archive_manifest) != {
        "archive_id",
        "files",
        "original_active_status_sha256",
        "original_scorecard_sha256",
        "policy",
        "program_id",
        "schema_version",
        "source_root",
    }:
        raise ValueError("archive manifest schema drift")
    if (
        candidate_manifest.get("program_id") != PROGRAM_ID
        or candidate_manifest.get("revision_id") != CANDIDATE_REVISION_ID
        or candidate_manifest.get("schema_version") != "bb.rl.phase5.artifact_manifest.v4"
        or candidate_manifest.get("immutable") is not True
    ):
        raise ValueError("candidate manifest identity drift")
    if (
        candidate_manifest.get("superseded_revision_id") != PREDECESSOR_REVISION_ID
        or candidate_manifest.get("superseded_artifact_manifest_sha256")
        != PREDECESSOR_MANIFEST_SHA256
        or candidate_manifest.get("archive_manifest_sha256")
        != ARCHIVE_MANIFEST_SHA256
        or candidate_manifest.get("v1_active_status_sha256") != V1_ACTIVE_SHA256
        or candidate_manifest.get("v1_scorecard_sha256") != V1_SCORECARD_SHA256
    ):
        raise ValueError("candidate manifest supersession/reference drift")
    if (
        predecessor_manifest.get("program_id") != PROGRAM_ID
        or predecessor_manifest.get("revision_id") != PREDECESSOR_REVISION_ID
        or predecessor_manifest.get("schema_version") != "bb.rl.phase5.artifact_manifest.v4"
        or predecessor_manifest.get("immutable") is not True
        or predecessor_manifest.get("archive_manifest_sha256") != ARCHIVE_MANIFEST_SHA256
        or predecessor_manifest.get("superseded_revision_id")
        != PRE_PREDECESSOR_REVISION_ID
        or predecessor_manifest.get("superseded_artifact_manifest_sha256")
        != PRE_PREDECESSOR_MANIFEST_SHA256
        or predecessor_manifest.get("supersession_scope")
        != "migration and cutover mechanics only; prior rc3 SPEC_FREEZE grants no rc4 authority"
    ):
        raise ValueError("predecessor manifest identity/reference drift")
    if (
        archive_manifest.get("archive_id") != ARCHIVE_ID
        or archive_manifest.get("program_id") != "bb-zyphra-rl-phase5-v1"
        or archive_manifest.get("schema_version")
        != "bb.rl.phase5.v1_archive_manifest.v1"
        or archive_manifest.get("original_active_status_sha256") != V1_ACTIVE_SHA256
        or archive_manifest.get("original_scorecard_sha256") != V1_SCORECARD_SHA256
        or archive_manifest.get("policy")
        != {
            "byte_identical": True,
            "no_v2_authority": True,
            "read_only_historical": True,
        }
    ):
        raise ValueError("archive ID or authority drift")


def _validate_build_report(
    report: Mapping[str, Any],
    candidate_rows: Mapping[str, Any],
    archive_rows: Mapping[str, Any],
) -> None:
    expected = {
        "archive_manifest_sha256": ARCHIVE_MANIFEST_SHA256,
        "artifact_manifest_sha256": CANDIDATE_MANIFEST_SHA256,
        "build_a_file_count": EXPECTED_BUILD_FILE_COUNT,
        "build_b_file_count": EXPECTED_BUILD_FILE_COUNT,
        "byte_identical": True,
        "catalog_sha256": candidate_rows["ASSURANCE_CATALOG.json"]["sha256"],
        "equivalence_sha256": candidate_rows["CATALOG_EQUIVALENCE.json"]["sha256"],
        "installed": True,
        "program_id": PROGRAM_ID,
        "result": "pass",
        "revision_id": CANDIDATE_REVISION_ID,
        "revision_root": "versions/v2-two-track/v2.0.0-rc5-20260717",
        "schema_version": "bb.rl.phase5.freeze_build_report.v1",
        "source_entries": EXPECTED_SOURCE_ENTRIES,
    }
    if dict(report) != expected:
        extra = sorted(set(report) - set(expected))
        if extra:
            raise ValueError(f"build report contradictory extra field: {extra[0]}")
        changed = sorted(
            key for key, value in expected.items() if report.get(key) != value
        )
        raise ValueError(f"build report drift: {changed[0]}")
    if len(candidate_rows) + len(archive_rows) + 2 != EXPECTED_BUILD_FILE_COUNT:
        raise ValueError("build report file-count basis drift")


def validate_safety_bindings(
    candidate_manifest: Mapping[str, Any],
    predecessor_manifest: Mapping[str, Any],
    archive_manifest: Mapping[str, Any],
    build_report: Mapping[str, Any],
    artifact_hashes: Mapping[str, str],
) -> list[dict[str, Any]]:
    expected_hashes = {
        "archive_manifest_sha256": ARCHIVE_MANIFEST_SHA256,
        "build_report_sha256": BUILD_REPORT_SHA256,
        "candidate_artifact_manifest_sha256": CANDIDATE_MANIFEST_SHA256,
        "predecessor_artifact_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
    }
    if set(artifact_hashes) != set(expected_hashes):
        raise ValueError("artifact hash binding schema drift")
    for key, expected_hash in expected_hashes.items():
        if artifact_hashes.get(key) != expected_hash:
            raise ValueError(f"{key.replace('_sha256', '').replace('_', ' ')} pin mismatch")
    _validate_manifest_identities(candidate_manifest, predecessor_manifest, archive_manifest)
    candidate_rows = _manifest_rows(candidate_manifest, "candidate")
    archive_rows = _manifest_rows(archive_manifest, "sealed archive")
    _validate_build_report(build_report, candidate_rows, archive_rows)
    return validate_allowed_delta(candidate_manifest, predecessor_manifest)


def validate_safety(
    candidate_revision: Path,
    predecessor_revision: Path,
    sealed_archive: Path,
    build_report_path: Path,
) -> dict[str, Any]:
    candidate_manifest_path = candidate_revision / "ARTIFACT_MANIFEST.json"
    predecessor_manifest_path = predecessor_revision / "ARTIFACT_MANIFEST.json"
    archive_manifest_path = sealed_archive / "ARCHIVE_MANIFEST.json"
    for root, context in (
        (candidate_revision, "candidate"),
        (predecessor_revision, "predecessor"),
        (sealed_archive, "sealed archive"),
    ):
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"invalid {context} root: {root}")
    _check_regular_file(candidate_manifest_path, "candidate manifest")
    _check_regular_file(predecessor_manifest_path, "predecessor manifest")
    _check_regular_file(archive_manifest_path, "sealed archive manifest")
    _check_regular_file(build_report_path, "build report")
    if sha256_file(candidate_manifest_path) != CANDIDATE_MANIFEST_SHA256:
        raise ValueError("candidate artifact manifest pin mismatch")
    if sha256_file(predecessor_manifest_path) != PREDECESSOR_MANIFEST_SHA256:
        raise ValueError("predecessor artifact manifest pin mismatch")
    if sha256_file(archive_manifest_path) != ARCHIVE_MANIFEST_SHA256:
        raise ValueError("sealed archive manifest pin mismatch")
    if sha256_file(build_report_path) != BUILD_REPORT_SHA256:
        raise ValueError("build report pin mismatch")

    candidate_manifest = load_object(candidate_manifest_path)
    predecessor_manifest = load_object(predecessor_manifest_path)
    archive_manifest = load_object(archive_manifest_path)
    _validate_manifest_identities(
        candidate_manifest, predecessor_manifest, archive_manifest
    )
    candidate_rows = check_manifest_tree(
        candidate_revision,
        candidate_manifest,
        manifest_name="ARTIFACT_MANIFEST.json",
        context="candidate",
    )
    predecessor_rows = check_manifest_tree(
        predecessor_revision,
        predecessor_manifest,
        manifest_name="ARTIFACT_MANIFEST.json",
        context="predecessor",
    )
    archive_rows = check_manifest_tree(
        sealed_archive,
        archive_manifest,
        manifest_name="ARCHIVE_MANIFEST.json",
        context="sealed archive",
    )
    allowed_delta_matrix = validate_allowed_delta(
        candidate_manifest, predecessor_manifest
    )

    if sha256_file(sealed_archive / "ACTIVE_STATUS.json") != V1_ACTIVE_SHA256:
        raise ValueError("sealed archive selector byte drift")
    if sha256_file(sealed_archive / "SCORECARD.json") != V1_SCORECARD_SHA256:
        raise ValueError("sealed archive score byte drift")

    build_report = load_object(build_report_path)
    _validate_build_report(build_report, candidate_rows, archive_rows)
    candidate_documents = _load_revision_documents(
        candidate_revision, candidate_rows
    )
    predecessor_documents = _load_revision_documents(
        predecessor_revision, predecessor_rows
    )
    archive_documents = {
        "ACTIVE_STATUS.json": load_object(sealed_archive / "ACTIVE_STATUS.json"),
        "SCORECARD.json": load_object(sealed_archive / "SCORECARD.json"),
    }
    normalized_semantics = validate_contract_invariants(
        candidate_documents, predecessor_documents, archive_documents
    )
    zero_authority_projection = validate_zero_authority_projection(
        candidate_documents
    )

    return {
        "allowed_delta_matrix": allowed_delta_matrix,
        "archive_file_count": len(archive_rows),
        "archive_id": ARCHIVE_ID,
        "archive_manifest_sha256": ARCHIVE_MANIFEST_SHA256,
        "build_report_sha256": BUILD_REPORT_SHA256,
        "candidate_artifact_manifest_sha256": CANDIDATE_MANIFEST_SHA256,
        "candidate_revision_id": CANDIDATE_REVISION_ID,
        "catalog_items": 49,
        "catalog_points": 1000,
        "live_store_access": False,
        "normalized_semantics": normalized_semantics,
        "predecessor_artifact_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "predecessor_revision_id": PREDECESSOR_REVISION_ID,
        "program_id": PROGRAM_ID,
        "result": "pass",
        "schema_version": "bb.rl.phase5.v2_candidate_safety_validation_report.v1",
        "source_entries": EXPECTED_SOURCE_ENTRIES,
        "target_admitted": zero_authority_projection["target_admitted"],
        "target_execution_allowed": zero_authority_projection["execution_allowed"],
        "validation_mode": "standalone_pure_file_no_live_store_access",
        "verified_points": zero_authority_projection["current_verified_points"],
        "zero_authority": zero_authority_projection["zero_authority"],
        "zero_authority_projection": zero_authority_projection,
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    return validate_safety(
        args.candidate_revision,
        args.predecessor_revision,
        args.sealed_archive,
        args.build_report,
    )


def _paths_disjoint(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=True)
    right_resolved = right.resolve(strict=True)
    try:
        left_resolved.relative_to(right_resolved)
        return False
    except ValueError:
        pass
    try:
        right_resolved.relative_to(left_resolved)
        return False
    except ValueError:
        return True


def _write_new_report(output_root: Path, basename: str, content: bytes) -> None:
    if output_root.is_symlink() or not output_root.is_dir():
        raise ValueError("output root must be an existing non-symlink directory")
    if (
        not basename
        or basename in {".", ".."}
        or Path(basename).name != basename
        or "/" in basename
        or "\\" in basename
    ):
        raise ValueError("report must be one basename beneath output root")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("platform lacks no-follow directory output support")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    directory_fd = os.open(output_root, directory_flags)
    report_fd: int | None = None
    created = False
    try:
        report_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            report_flags |= os.O_CLOEXEC
        report_fd = os.open(
            basename,
            report_flags,
            0o600,
            dir_fd=directory_fd,
        )
        created = True
        remaining = memoryview(content)
        while remaining:
            written = os.write(report_fd, remaining)
            if written <= 0:
                raise OSError("short report write")
            remaining = remaining[written:]
        os.fsync(report_fd)
        os.close(report_fd)
        report_fd = None
        os.fsync(directory_fd)
    except BaseException:
        if report_fd is not None:
            os.close(report_fd)
        if created:
            os.unlink(basename, dir_fd=directory_fd)
            os.fsync(directory_fd)
        raise
    finally:
        os.close(directory_fd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the exact immutable Phase 5 v2 candidate safety delta "
            "using files only."
        )
    )
    parser.add_argument("--candidate-revision", type=Path, required=True)
    parser.add_argument("--predecessor-revision", type=Path, required=True)
    parser.add_argument("--sealed-archive", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--execution-root", type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    if args.output_root.is_symlink() or not args.output_root.is_dir():
        raise ValueError("output root must be an existing non-symlink directory")
    protected_roots = [
        args.candidate_revision,
        args.predecessor_revision,
        args.sealed_archive,
    ]
    protected_roots.extend(
        root for root in (args.execution_root, args.repo_root) if root is not None
    )
    for protected_root in protected_roots:
        if not _paths_disjoint(args.output_root, protected_root):
            raise ValueError("output root overlaps an immutable or authority-bearing tree")
    result = validate(args)
    _write_new_report(args.output_root, args.report, canonical_bytes(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
