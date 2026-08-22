from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from breadboard.rl.phase5.migration_projections import (  # noqa: E402
    build_root_selector,
    derive_active_status,
    derive_beads_projection,
    derive_run_queue,
    derive_session_projection,
    validate_zero_authority,
    validate_spec_freeze_decision,
)
from breadboard.rl.phase5.migration_transaction import (  # noqa: E402
    build_event,
    canonical_bytes,
    sha256_bytes,
    verify_event_chain,
)
from scripts.rl_phase5.prepare_phase5_v2_migration import (  # noqa: E402
    BUNDLE_FILES,
    SESSION_SOURCE_ID,
    SPEC_FREEZE_SOURCE_ID,
    _decode_beads_rows,
    extract_spec_freeze,
)
from scripts.rl_phase5.replay_phase5_v2_prepared_handoff import (  # noqa: E402
    ARTIFACT_MANIFEST_SHA256,
    BEFORE_IMAGE_FILES,
    COMPLETE_BUNDLE_FILES,
    FRESH_WORKER_CONTRACT_SHA256,
    MIGRATION_TRANSACTION_SHA256,
    PREPARED_INPUT_FILES,
    PROGRAM_ID,
    REVISION_ID,
    STORE_IDS,
    SESSION_SOURCE_FILE,
    SPEC_FREEZE_DECISION_FILE,
    SPEC_FREEZE_DECISION_SHA256,
    SPEC_FREEZE_DECISION_SIZE,
    V1_ACTIVE_SHA256,
    _bundle_documents,
    _decode_json,
    _file_ref,
    _image_map,
    _read_regular_nofollow,
    _require_exact_keys,
    _require_object,
    _require_string,
    _resolved_directory,
    _validate_prepared_bundle,
    _verify_revision,
    _write_new_canonical,
    derive_semantic,
)

SPEC_FREEZE_SHA256 = SPEC_FREEZE_DECISION_SHA256
_PREPARATION_REPORT_KEYS = {
    "after_images",
    "after_images_sha256",
    "artifact_manifest_sha256",
    "authority_decision_sha256",
    "before_images",
    "before_images_sha256",
    "bundle_artifact_hashes",
    "commit_results",
    "consumer_barrier_acquired",
    "consumer_barrier_feasibility",
    "consumer_barrier_released",
    "cutover_ready",
    "event_append_metadata_ref",
    "event_append_payload_ref",
    "fresh_worker_preparation_report_sha256",
    "frozen_handoff_contract_passed",
    "gate_exercise",
    "migration_id",
    "migration_transaction_sha256",
    "native_revision_binding",
    "post_commit_hashes",
    "prepared_only",
    "prepared_validation",
    "prepared_validation_sha256",
    "program_id",
    "released_lease",
    "revision_id",
    "rollback_descriptors_ref",
    "schema_version",
    "spec_freeze_decision",
    "spec_freeze_decision_sha256",
    "target_execution_allowed",
}
_BARRIER_KEYS = {
    "affected_consumer_classes",
    "feasible",
    "live_native_binding_available",
    "required_remediation",
    "status",
    "temporary_gate_is_native_evidence",
}
_NATIVE_BINDING_KEYS = {
    "beads_dolt_native_revision",
    "beads_dolt_revision_bound",
    "native_revision_bound",
    "omp_state_native_revision",
    "omp_state_revision_bound",
    "revision_type",
}
_PREPARED_VALIDATION_KEYS = {
    "artifact_manifest_sha256",
    "bundle_input_hashes",
    "execution_frontier",
    "migration_id",
    "migration_transaction_sha256",
    "program_id",
    "revision_id",
    "schema_version",
    "spec_freeze_sha256",
    "target_execution_allowed",
    "zero_authority",
}


def _read_bundle_object(bundle: Path, name: str) -> tuple[dict[str, Any], bytes, os.stat_result]:
    raw, metadata = _read_regular_nofollow(
        bundle / name,
        label=f"prepared bundle artifact {name}",
        reject_hardlinks=True,
    )
    if stat.S_IMODE(metadata.st_mode) & 0o222:
        raise ValueError(f"prepared bundle artifact is writable: {name}")
    value = _decode_json(raw, name)
    return _require_object(value, name), raw, metadata


def _verified_revision_object(
    revision_state: dict[str, Any], name: str
) -> tuple[dict[str, Any], bytes]:
    row = revision_state["manifest_rows"].get(name)
    if row is None:
        raise ValueError(f"revision manifest lacks required input: {name}")
    raw, metadata = _read_regular_nofollow(
        revision_state["revision"] / name, label=f"revision input {name}"
    )
    if (
        sha256_bytes(raw) != row["sha256"]
        or len(raw) != row["size"]
        or stat.S_IMODE(metadata.st_mode) != 0o444
    ):
        raise ValueError(f"revision input drift: {name}")
    return _require_object(_decode_json(raw, name), name), raw


def _ref_from_bytes(path: str, payload: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": sha256_bytes(payload), "size": len(payload)}


def _capture_live_sources(
    execution_root: Path,
    beads_export: Path,
    session_state: Path,
    spec_freeze_decision: Path,
) -> dict[str, dict[str, Any]]:
    source_paths = {
        "v2_event_log": execution_root / "EVENT_CHAIN.json",
        "beads_projection": beads_export,
        SESSION_SOURCE_ID: session_state,
        SPEC_FREEZE_SOURCE_ID: spec_freeze_decision,
        "root_active_selector": execution_root / "ACTIVE_STATUS.json",
    }
    captures: dict[str, dict[str, Any]] = {}
    identities: set[tuple[int, int]] = set()
    for store_id in (*STORE_IDS, SESSION_SOURCE_ID, SPEC_FREEZE_SOURCE_ID):
        source_path = source_paths[store_id]
        try:
            raw, metadata = _read_regular_nofollow(
                source_path, label=f"live source {store_id}"
            )
        except ValueError:
            if store_id != "v2_event_log" or os.path.lexists(source_path):
                raise
            parent = os.stat(source_path.parent, follow_symlinks=False)
            if not stat.S_ISDIR(parent.st_mode):
                raise ValueError("live event-log parent is not a directory")
            captures[store_id] = {
                "metadata": None,
                "parent_metadata": parent,
                "path": source_path,
                "presence": "absent",
                "raw": None,
            }
            continue
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in identities:
            raise ValueError("live sources contain a hardlink/inode alias")
        identities.add(identity)
        captures[store_id] = {
            "metadata": metadata,
            "path": source_path,
            "presence": "present",
            "raw": raw,
        }
    if sha256_bytes(captures["root_active_selector"]["raw"]) != V1_ACTIVE_SHA256:
        raise ValueError("root ACTIVE_STATUS is not the exact frozen v1 selector")
    event_raw = captures["v2_event_log"]["raw"]
    events = [] if event_raw is None else _decode_json(
        event_raw, "live EVENT_CHAIN.json"
    )
    if not isinstance(events, list):
        raise ValueError("live EVENT_CHAIN must be a JSON list")
    verify_event_chain(events)
    session = _require_object(
        _decode_json(
            captures[SESSION_SOURCE_ID]["raw"], "live session state"
        ),
        "live session state",
    )
    validate_zero_authority(session)
    decision_raw = captures[SPEC_FREEZE_SOURCE_ID]["raw"]
    if (
        len(decision_raw) != SPEC_FREEZE_DECISION_SIZE
        or sha256_bytes(decision_raw) != SPEC_FREEZE_DECISION_SHA256
    ):
        raise ValueError("live RC5 SPEC_FREEZE decision bytes changed")
    decision = _require_object(
        _decode_json(decision_raw, "live RC5 SPEC_FREEZE decision"),
        "live RC5 SPEC_FREEZE decision",
    )
    validate_spec_freeze_decision(
        decision,
        artifact_sha256=SPEC_FREEZE_DECISION_SHA256,
    )
    return captures


def _verify_live_before_images(
    captures: dict[str, dict[str, Any]],
    documents: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    before_document = _require_object(documents["BEFORE_IMAGES.json"], "BEFORE_IMAGES.json")
    before_images = _image_map(before_document, "BEFORE_IMAGES.json", before=True)
    for store_id in STORE_IDS:
        capture = captures[store_id]
        image = before_images[store_id]
        if capture["presence"] == "absent":
            if (
                store_id != "v2_event_log"
                or image.get("presence") != "absent"
                or image["before_image_ref"] is not None
                or BEFORE_IMAGE_FILES[store_id] in documents["__raw_inputs__"]
                or image["parent_device"]
                != capture["parent_metadata"].st_dev
                or image["parent_inode"]
                != capture["parent_metadata"].st_ino
            ):
                raise ValueError("event-log absence binding differs from live source")
            continue
        metadata = capture["metadata"]
        raw = capture["raw"]
        retained = documents[BEFORE_IMAGE_FILES[store_id]]
        snapshot = image["snapshot"]
        source_mode = stat.S_IMODE(metadata.st_mode)
        if raw != retained:
            raise ValueError(
                f"retained before-image bytes differ from live source: {store_id}"
            )
        if image["source_mode"] != source_mode:
            raise ValueError(
                f"live source mode differs from captured source_mode: {store_id}"
            )
        if (
            image["bytes_sha256"] != sha256_bytes(raw)
            or image["size"] != len(raw)
            or snapshot
            != {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "mode": stat.S_IMODE(metadata.st_mode),
                "mtime_ns": metadata.st_mtime_ns,
            }
        ):
            raise ValueError(f"live snapshot metadata/digest mismatch: {store_id}")
    for source_id, filename in (
        (SESSION_SOURCE_ID, SESSION_SOURCE_FILE),
        (SPEC_FREEZE_SOURCE_ID, SPEC_FREEZE_DECISION_FILE),
    ):
        if captures[source_id]["raw"] != documents["__raw_inputs__"][filename]:
            raise ValueError(f"retained source bytes differ from live source: {source_id}")
        source_mode = stat.S_IMODE(captures[source_id]["metadata"].st_mode)
        expected_retained_mode = source_mode & ~(
            stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        )
        if not expected_retained_mode & (
            stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        ):
            expected_retained_mode |= stat.S_IRUSR
        retained_mode = stat.S_IMODE(
            documents["__metadata_inputs__"][filename].st_mode
        )
        if retained_mode != expected_retained_mode:
            raise ValueError(f"retained source mode differs from live source: {source_id}")
    return before_images


def _assert_sources_unchanged(captures: dict[str, dict[str, Any]]) -> None:
    for store_id, capture in captures.items():
        if capture["presence"] == "absent":
            try:
                os.stat(capture["path"], follow_symlinks=False)
            except FileNotFoundError:
                parent = os.stat(
                    capture["path"].parent, follow_symlinks=False
                )
                original_parent = capture["parent_metadata"]
                if (
                    parent.st_dev != original_parent.st_dev
                    or parent.st_ino != original_parent.st_ino
                ):
                    raise ValueError(
                        f"live source parent drifted during validation: {store_id}"
                    )
                continue
            raise ValueError(f"live source appeared during validation: {store_id}")
        current = os.stat(capture["path"], follow_symlinks=False)
        original = capture["metadata"]
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            stat.S_IMODE(current.st_mode),
        ) != (
            original.st_dev,
            original.st_ino,
            original.st_size,
            original.st_mtime_ns,
            stat.S_IMODE(original.st_mode),
        ):
            raise ValueError(f"live source drifted during validation: {store_id}")


def _assert_retained_modes_unchanged(bundle: Path, documents: dict[str, Any]) -> None:
    metadata_inputs = documents["__metadata_inputs__"]
    retained_inputs = {
        **BEFORE_IMAGE_FILES,
        SESSION_SOURCE_ID: SESSION_SOURCE_FILE,
        SPEC_FREEZE_SOURCE_ID: SPEC_FREEZE_DECISION_FILE,
    }
    for store_id, filename in retained_inputs.items():
        if filename not in metadata_inputs:
            if store_id != "v2_event_log":
                raise ValueError(f"retained before-image is missing: {store_id}")
            continue
        current = os.stat(bundle / filename, follow_symlinks=False)
        original = metadata_inputs[filename]
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            stat.S_IMODE(current.st_mode),
        ) != (
            original.st_dev,
            original.st_ino,
            original.st_size,
            original.st_mtime_ns,
            stat.S_IMODE(original.st_mode),
        ):
            raise ValueError(f"retained before-image drifted during validation: {store_id}")


def _recompute_projections(
    revision_state: dict[str, Any],
    documents: dict[str, Any],
    captures: dict[str, dict[str, Any]],
    migration_id: str,
    spec_freeze_sha256: str,
) -> None:
    draft_status = _require_object(
        revision_state["input_values"]["DRAFT_STATUS.json"], "DRAFT_STATUS.json"
    )
    frozen_queue = _require_object(
        revision_state["input_values"]["RUN_QUEUE.json"], "RUN_QUEUE.json"
    )
    beads_migration, _ = _verified_revision_object(revision_state, "BEADS_MIGRATION.json")
    authority_policy, authority_raw = _verified_revision_object(
        revision_state, "AUTHORITY_POLICY.json"
    )
    evidence_index, evidence_raw = _verified_revision_object(
        revision_state, "EVIDENCE_INDEX.json"
    )
    del authority_policy, evidence_index
    live_rows = _decode_beads_rows(captures["beads_projection"]["raw"])
    session_before = _require_object(
        _decode_json(
            captures[SESSION_SOURCE_ID]["raw"], "captured session state"
        ),
        "captured session state",
    )
    active = derive_active_status(
        draft_status,
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    queue = derive_run_queue(
        frozen_queue,
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    beads = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    session = derive_session_projection(
        session_before,
        active,
        queue,
        migration_id=migration_id,
    )
    expected = {
        "BEADS_RESOLUTION.json": beads,
        "SESSION_PROJECTION.json": session,
        "PREPARED_ACTIVE_STATUS.json": active,
        "PREPARED_RUN_QUEUE.json": queue,
    }
    for name, value in expected.items():
        if documents[name] != value:
            raise ValueError(f"prepared projection is not derived from captured bytes: {name}")
    active_raw = documents["__raw_inputs__"]["PREPARED_ACTIVE_STATUS.json"]
    queue_raw = documents["__raw_inputs__"]["PREPARED_RUN_QUEUE.json"]
    revision_prefix = f"versions/v2-two-track/{REVISION_ID}"
    selector = build_root_selector(
        revision_id=REVISION_ID,
        program_id=PROGRAM_ID,
        generation=active["generation"],
        event_cursor=active["event_cursor"],
        migration_id=migration_id,
        artifact_manifest_ref=_ref_from_bytes(
            f"{revision_prefix}/ARTIFACT_MANIFEST.json",
            revision_state["input_bytes"]["ARTIFACT_MANIFEST.json"],
        ),
        active_status_ref=_ref_from_bytes(
            f"migrations/{migration_id}/PREPARED_ACTIVE_STATUS.json", active_raw
        ),
        evidence_index_ref=_ref_from_bytes(
            f"{revision_prefix}/EVIDENCE_INDEX.json", evidence_raw
        ),
        authority_policy_ref=_ref_from_bytes(
            f"{revision_prefix}/AUTHORITY_POLICY.json", authority_raw
        ),
        run_queue_ref=_ref_from_bytes(
            f"migrations/{migration_id}/PREPARED_RUN_QUEUE.json", queue_raw
        ),
    )
    if documents["PREPARED_ROOT_SELECTOR.json"] != selector:
        raise ValueError("prepared root selector is not the exact captured-byte derivation")

    event_raw = captures["v2_event_log"]["raw"]
    before_events = (
        []
        if event_raw is None
        else _decode_json(event_raw, "captured event before-image")
    )
    before_head = verify_event_chain(before_events)
    lineage = build_event(
        "V1_LINEAGE_IMPORTED",
        {
            "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
            "before_images_sha256": sha256_bytes(
                documents["__raw_inputs__"]["BEFORE_IMAGES.json"]
            ),
            "migration_id": migration_id,
            "program_id": PROGRAM_ID,
            "revision_id": REVISION_ID,
            "root_v1_active_sha256": V1_ACTIVE_SHA256,
            "spec_freeze_decision_sha256": spec_freeze_sha256,
            "migration_transaction_sha256": MIGRATION_TRANSACTION_SHA256,
        },
        before_head,
    )
    activation = build_event(
        "V2_ACTIVATED",
        {
            "beads_resolution_ref": _ref_from_bytes(
                "BEADS_RESOLUTION.json",
                documents["__raw_inputs__"]["BEADS_RESOLUTION.json"],
            ),
            "migration_id": migration_id,
            "prepared_active_status_ref": _ref_from_bytes(
                "PREPARED_ACTIVE_STATUS.json", active_raw
            ),
            "prepared_root_selector_ref": _ref_from_bytes(
                "PREPARED_ROOT_SELECTOR.json",
                documents["__raw_inputs__"]["PREPARED_ROOT_SELECTOR.json"],
            ),
            "prepared_run_queue_ref": _ref_from_bytes(
                "PREPARED_RUN_QUEUE.json", queue_raw
            ),
            "program_id": PROGRAM_ID,
            "session_projection_ref": _ref_from_bytes(
                "SESSION_PROJECTION.json",
                documents["__raw_inputs__"]["SESSION_PROJECTION.json"],
            ),
            "target_execution_allowed": False,
        },
        lineage["event_sha256"],
    )
    expected_append = [lineage, activation]
    if documents["EVENT_APPEND_PAYLOAD.json"] != expected_append:
        raise ValueError("event append payload is not the exact captured-byte derivation")
    if documents["EVENT_CHAIN.json"] != [*before_events, *expected_append]:
        raise ValueError("event after-image is not the exact append-only list derivation")


def _verify_replay_report(
    revision: Path,
    bundle: Path,
    report: dict[str, Any],
) -> None:
    _require_exact_keys(
        report,
        {"frozen_contract_passed", "frozen_contract_receipt", "replay_mode", "schema_version", "workers"},
        "FRESH_WORKER_PREPARATION_REPORT.json",
    )
    if (
        report["schema_version"] != "bb.rl.phase5.prepared_image_replay_report.v1"
        or report["replay_mode"] != "non_conformance_preparation_replay"
        or report["frozen_contract_passed"] is not False
    ):
        raise ValueError("prepared replay misstates frozen fresh-worker conformance")
    receipt = _require_exact_keys(
        report["frozen_contract_receipt"],
        {"artifact_manifest_sha256", "contract_sha256", "worker_count", "worker_semantic_sha256", "result"},
        "frozen_contract_receipt",
    )
    if (
        receipt["artifact_manifest_sha256"] != ARTIFACT_MANIFEST_SHA256
        or receipt["contract_sha256"] != FRESH_WORKER_CONTRACT_SHA256
        or receipt["worker_count"] != 2
        or receipt["result"] != "non_conformance_preparation_replay"
    ):
        raise ValueError("frozen contract receipt binding/result is invalid")
    workers = report["workers"]
    if not isinstance(workers, list) or len(workers) != 2:
        raise ValueError("prepared replay must contain exactly two worker receipts")
    expected_semantic = derive_semantic(revision, bundle, require_empty_cwd=False)
    semantic_blob = canonical_bytes(expected_semantic)
    semantic_sha256 = sha256_bytes(semantic_blob)
    pids: list[int] = []
    for index, value in enumerate(workers):
        worker = _require_exact_keys(
            value,
            {"pid", "input_hashes", "derived_action", "execution_frontier", "target_execution_allowed", "ambient_inputs_used"},
            f"workers[{index}]",
        )
        pid = worker["pid"]
        if type(pid) is not int or pid <= 0:
            raise ValueError("worker pid is invalid")
        pids.append(pid)
        semantic = {key: child for key, child in worker.items() if key != "pid"}
        if canonical_bytes(semantic) != semantic_blob:
            raise ValueError("worker semantic receipt disagreement")
        ambient = worker["ambient_inputs_used"]
        if not isinstance(ambient, list) or not ambient:
            raise ValueError("prepared-image worker falsely claims no ambient inputs")
        required_ambient = {
            f"prepared/{name}"
            for name in PREPARED_INPUT_FILES
            if (bundle / name).is_file()
        }
        if not required_ambient.issubset(set(ambient)):
            raise ValueError("prepared-image worker omits consumed bundle inputs")
        if worker["target_execution_allowed"] is not False:
            raise ValueError("prepared-image worker claims target authority")
    if len(set(pids)) != 2 or receipt["worker_semantic_sha256"] != semantic_sha256:
        raise ValueError("fresh worker process/hash evidence is invalid")
    validate_zero_authority(report)


def _verify_preparation_report(
    report: dict[str, Any],
    documents: dict[str, Any],
    fresh_report: dict[str, Any],
    spec_decision: dict[str, Any],
    spec_freeze_sha256: str,
) -> dict[str, Any]:
    _require_exact_keys(report, _PREPARATION_REPORT_KEYS, "MIGRATION_PREPARATION_REPORT.json")
    if (
        report["schema_version"] != "bb.rl.phase5.migration_preparation_report.v3"
        or report["program_id"] != PROGRAM_ID
        or report["revision_id"] != REVISION_ID
        or report["artifact_manifest_sha256"] != ARTIFACT_MANIFEST_SHA256
        or report["migration_transaction_sha256"] != MIGRATION_TRANSACTION_SHA256
        or report["spec_freeze_decision_sha256"] != spec_freeze_sha256
        or spec_freeze_sha256 != SPEC_FREEZE_DECISION_SHA256
        or report["spec_freeze_decision"] != spec_decision
    ):
        raise ValueError("preparation report frozen identity bindings mismatch")
    migration_id = _require_string(report["migration_id"], "migration_id")
    if (
        report["authority_decision_sha256"] != spec_freeze_sha256
        or report["commit_results"] != []
        or report["post_commit_hashes"] != []
        or report["consumer_barrier_acquired"] is not False
        or report["consumer_barrier_released"] is not False
        or report["released_lease"] is not False
        or report["cutover_ready"] is not False
        or report["prepared_only"] is not True
        or report["target_execution_allowed"] is not False
        or report["frozen_handoff_contract_passed"] is not False
    ):
        raise ValueError("preparation report claims commit, barrier, cutover, or target authority")
    barrier = _require_exact_keys(
        report["consumer_barrier_feasibility"], _BARRIER_KEYS, "consumer_barrier_feasibility"
    )
    if barrier != {
        "affected_consumer_classes": [
            "raw_root_selector_readers",
            "beads_dolt_sql_readers",
            "omp_cached_rpc_todo_readers",
        ],
        "feasible": False,
        "live_native_binding_available": False,
        "required_remediation": (
            "add native fail-closed bindings to every affected consumer class and "
            "independently verify them"
        ),
        "status": "infeasible_without_native_consumer_bindings",
        "temporary_gate_is_native_evidence": False,
    }:
        raise ValueError("consumer barrier feasibility is not the typed known-negative result")
    native = _require_exact_keys(
        report["native_revision_binding"], _NATIVE_BINDING_KEYS, "native_revision_binding"
    )
    if native != {
        "beads_dolt_native_revision": None,
        "beads_dolt_revision_bound": False,
        "native_revision_bound": False,
        "omp_state_native_revision": None,
        "omp_state_revision_bound": False,
        "revision_type": "file_snapshot_sha256",
    }:
        raise ValueError("preparation report falsely claims a native store revision")
    gate = _require_exact_keys(
        report["gate_exercise"],
        {"blocked_store_ids", "exercised_in_temporary_directory", "live_gate_acquired", "released"},
        "gate_exercise",
    )
    if gate != {
        "blocked_store_ids": list(STORE_IDS),
        "exercised_in_temporary_directory": True,
        "live_gate_acquired": False,
        "released": True,
    }:
        raise ValueError("temporary gate exercise evidence is invalid")
    raw_inputs = documents["__raw_inputs__"]
    before_document = _require_object(documents["BEFORE_IMAGES.json"], "BEFORE_IMAGES.json")
    after_document = _require_object(documents["AFTER_IMAGES.json"], "AFTER_IMAGES.json")
    if (
        report["before_images"] != before_document["images"]
        or report["after_images"] != after_document["images"]
        or report["before_images_sha256"] != sha256_bytes(raw_inputs["BEFORE_IMAGES.json"])
        or report["after_images_sha256"] != sha256_bytes(raw_inputs["AFTER_IMAGES.json"])
    ):
        raise ValueError("preparation report image bindings mismatch")
    expected_hashes = {
        name: sha256_bytes(raw_inputs[name])
        for name in PREPARED_INPUT_FILES
        if name in raw_inputs
    }
    fresh_raw = canonical_bytes(fresh_report)
    expected_hashes["FRESH_WORKER_PREPARATION_REPORT.json"] = sha256_bytes(fresh_raw)
    if report["bundle_artifact_hashes"] != expected_hashes:
        raise ValueError("preparation report bundle artifact hash schema/binding mismatch")
    if report["fresh_worker_preparation_report_sha256"] != sha256_bytes(fresh_raw):
        raise ValueError("preparation report does not bind the replay report")
    for field, name in (
        ("event_append_metadata_ref", "EVENT_APPEND_METADATA.json"),
        ("event_append_payload_ref", "EVENT_APPEND_PAYLOAD.json"),
        ("rollback_descriptors_ref", "ROLLBACK_DESCRIPTORS.json"),
    ):
        reference = _file_ref(report[field], field, expected_path=name)
        if reference != _ref_from_bytes(name, raw_inputs[name]):
            raise ValueError(f"preparation report reference drift: {field}")
    prepared_validation = _require_exact_keys(
        report["prepared_validation"], _PREPARED_VALIDATION_KEYS, "prepared_validation"
    )
    expected_prepared_validation = {
        "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "bundle_input_hashes": {
            name: sha256_bytes(raw_inputs[name])
            for name in PREPARED_INPUT_FILES
            if name in raw_inputs
        },
        "execution_frontier": ["AT0"],
        "migration_id": migration_id,
        "migration_transaction_sha256": MIGRATION_TRANSACTION_SHA256,
        "program_id": PROGRAM_ID,
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.prepared_validation.v1",
        "spec_freeze_sha256": spec_freeze_sha256,
        "target_execution_allowed": False,
        "zero_authority": True,
    }
    if prepared_validation != expected_prepared_validation:
        raise ValueError("prepared validation is not the exact closed derivation")
    if report["prepared_validation_sha256"] != sha256_bytes(canonical_bytes(prepared_validation)):
        raise ValueError("prepared validation digest mismatch")
    validate_zero_authority(report)
    return {"migration_id": migration_id, "prepared_validation": prepared_validation}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    execution_root = _resolved_directory(args.execution_root, "execution-root")
    revision = _resolved_directory(args.revision, "revision")
    bundle = _resolved_directory(args.bundle, "prepared bundle")
    if stat.S_IMODE(os.stat(bundle, follow_symlinks=False).st_mode) & 0o222:
        raise ValueError("prepared bundle directory is writable")
    beads_export = Path(os.path.abspath(os.fspath(args.beads_export)))
    session_state = Path(os.path.abspath(os.fspath(args.session_state)))
    spec_freeze_decision = Path(
        os.path.abspath(os.fspath(args.spec_freeze_decision))
    )
    revision_state = _verify_revision(revision)
    if revision_state["revision"] != revision:
        raise ValueError("revision does not resolve to the exact verified directory")
    if tuple(BUNDLE_FILES) != tuple(COMPLETE_BUNDLE_FILES):
        raise ValueError("prepare/replay complete bundle schemas disagree")
    documents = _bundle_documents(bundle)
    _validate_prepared_bundle(revision_state, documents)
    fresh_report, fresh_raw, fresh_metadata = _read_bundle_object(
        bundle, "FRESH_WORKER_PREPARATION_REPORT.json"
    )
    preparation_report, preparation_raw, preparation_metadata = _read_bundle_object(
        bundle, "MIGRATION_PREPARATION_REPORT.json"
    )
    if (fresh_metadata.st_dev, fresh_metadata.st_ino) == (
        preparation_metadata.st_dev,
        preparation_metadata.st_ino,
    ):
        raise ValueError("bundle reports are inode aliases")
    captures = _capture_live_sources(
        execution_root,
        beads_export,
        session_state,
        spec_freeze_decision,
    )
    _verify_live_before_images(captures, documents)
    decision_value = _require_object(
        _decode_json(
            captures[SPEC_FREEZE_SOURCE_ID]["raw"],
            "captured RC5 SPEC_FREEZE decision",
        ),
        "captured RC5 SPEC_FREEZE decision",
    )
    spec_decision, spec_freeze_sha256 = extract_spec_freeze(
        decision_value,
        payload=captures[SPEC_FREEZE_SOURCE_ID]["raw"],
    )
    _verify_replay_report(revision, bundle, fresh_report)
    report_state = _verify_preparation_report(
        preparation_report,
        documents,
        fresh_report,
        spec_decision,
        spec_freeze_sha256,
    )
    migration_id = report_state["migration_id"]
    _recompute_projections(
        revision_state,
        documents,
        captures,
        migration_id,
        spec_freeze_sha256,
    )
    _assert_retained_modes_unchanged(bundle, documents)
    _assert_sources_unchanged(captures)
    source_hashes = {
        source_id: (
            None
            if captures[source_id]["raw"] is None
            else sha256_bytes(captures[source_id]["raw"])
        )
        for source_id in (*STORE_IDS, SESSION_SOURCE_ID, SPEC_FREEZE_SOURCE_ID)
    }
    validation_report = {
        "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "bundle_file_hashes": {
            **{
                name: sha256_bytes(documents["__raw_inputs__"][name])
                for name in PREPARED_INPUT_FILES
                if name in documents["__raw_inputs__"]
            },
            "FRESH_WORKER_PREPARATION_REPORT.json": sha256_bytes(fresh_raw),
            "MIGRATION_PREPARATION_REPORT.json": sha256_bytes(preparation_raw),
        },
        "commit_count": 0,
        "consumer_barrier_acquired": False,
        "consumer_barrier_feasibility": preparation_report[
            "consumer_barrier_feasibility"
        ],
        "consumer_barrier_released": False,
        "cutover_ready": False,
        "frozen_handoff_contract_passed": False,
        "live_source_hashes": source_hashes,
        "live_source_presence": {
            source_id: captures[source_id]["presence"]
            for source_id in (
                *STORE_IDS,
                SESSION_SOURCE_ID,
                SPEC_FREEZE_SOURCE_ID,
            )
        },
        "migration_id": migration_id,
        "migration_transaction_sha256": MIGRATION_TRANSACTION_SHA256,
        "native_revision_binding": preparation_report["native_revision_binding"],
        "prepared_only": True,
        "prepared_validation_sha256": preparation_report[
            "prepared_validation_sha256"
        ],
        "program_id": PROGRAM_ID,
        "released_lease": False,
        "replay_mode": "non_conformance_preparation_replay",
        "result": "pass",
        "revision_id": REVISION_ID,
        "root_active_status_sha256": V1_ACTIVE_SHA256,
        "schema_version": "bb.rl.phase5.migration_preparation_validation_report.v3",
        "snapshot_only": True,
        "spec_freeze_decision_sha256": spec_freeze_sha256,
        "target_execution_allowed": False,
        "worker_count": 2,
        "zero_authority": True,
    }
    validate_zero_authority(validation_report)
    return validation_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--beads-export", type=Path, required=True)
    parser.add_argument("--session-state", type=Path, required=True)
    parser.add_argument("--spec-freeze-decision", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = validate(args)
    execution_root = _resolved_directory(args.execution_root, "execution-root")
    revision = _resolved_directory(args.revision, "revision")
    bundle = _resolved_directory(args.bundle, "prepared bundle")
    beads_export = Path(os.path.abspath(os.fspath(args.beads_export))).resolve(strict=True)
    session_state = Path(os.path.abspath(os.fspath(args.session_state))).resolve(strict=True)
    spec_freeze_decision = Path(
        os.path.abspath(os.fspath(args.spec_freeze_decision))
    ).resolve(strict=True)
    _write_new_canonical(
        args.output_root,
        args.report,
        report,
        protected=(
            execution_root,
            revision,
            beads_export,
            session_state,
            spec_freeze_decision,
            bundle,
        ),
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
