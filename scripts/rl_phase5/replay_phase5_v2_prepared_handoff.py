from __future__ import annotations

import argparse
import json
import os
import stat
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from breadboard.rl.phase5 import migration_projections, migration_transaction  # noqa: E402
from breadboard.rl.phase5.migration_projections import (  # noqa: E402
    derive_active_status,
    derive_run_queue,
    derive_session_projection,
    validate_beads_projection,
    validate_zero_authority,
    validate_spec_freeze_decision,
)
from breadboard.rl.phase5.migration_transaction import (  # noqa: E402
    canonical_bytes,
    sha256_bytes,
    verify_event_chain,
)

PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
REVISION_ID = "v2.0.0-rc5-20260717"
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
MIGRATION_TRANSACTION_SHA256 = (
    "sha256:792702e6d6abdbc78244c37e6a464de974079aa4820243831dcb81822473673f"
)
FRESH_WORKER_CONTRACT_SHA256 = (
    "sha256:7895b04c7466e1de3caf507e89e54373c6da62bfbf2d1a9395220f67624ce246"
)
V1_ACTIVE_SHA256 = (
    "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
)
STORE_IDS = (
    "v2_event_log",
    "beads_projection",
    "root_active_selector",
)
SESSION_SOURCE_ID = "session_pre_handoff"
FROZEN_ALLOWED_INPUT_FILES = (
    "ARTIFACT_MANIFEST.json",
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
)
BEFORE_IMAGE_FILES = {
    "v2_event_log": "BEFORE_IMAGE_v2_event_log.bin",
    "beads_projection": "BEFORE_IMAGE_beads_projection.bin",
    "root_active_selector": "BEFORE_IMAGE_root_active_selector.bin",
}
SESSION_SOURCE_FILE = "SESSION_PRE_HANDOFF_SNAPSHOT.bin"
SPEC_FREEZE_DECISION_FILE = "SPEC_FREEZE_DECISION.json"
SPEC_FREEZE_DECISION_SHA256 = (
    "sha256:e06abb5bf8b0bcbeff6c26721a241eba8961856822b030811df69fd3b8d1da36"
)
SPEC_FREEZE_DECISION_SIZE = 1585
PREPARED_INPUT_FILES = (
    "BEFORE_IMAGES.json",
    "AFTER_IMAGES.json",
    "EVENT_CHAIN.json",
    "BEADS_RESOLUTION.json",
    "SESSION_PROJECTION.json",
    "PREPARED_ACTIVE_STATUS.json",
    "PREPARED_RUN_QUEUE.json",
    "PREPARED_ROOT_SELECTOR.json",
    *BEFORE_IMAGE_FILES.values(),
    SESSION_SOURCE_FILE,
    SPEC_FREEZE_DECISION_FILE,
    "ROLLBACK_DESCRIPTORS.json",
    "EVENT_APPEND_PAYLOAD.json",
    "EVENT_APPEND_METADATA.json",
)
COMPLETE_BUNDLE_FILES = PREPARED_INPUT_FILES + (
    "FRESH_WORKER_PREPARATION_REPORT.json",
    "MIGRATION_PREPARATION_REPORT.json",
)
AFTER_IMAGE_FILES = {
    "v2_event_log": "EVENT_CHAIN.json",
    "beads_projection": "BEADS_RESOLUTION.json",
    "root_active_selector": "PREPARED_ROOT_SELECTOR.json",
}
_LIST_DOCUMENTS = {"EVENT_CHAIN.json", "EVENT_APPEND_PAYLOAD.json"}
_RAW_DOCUMENTS = {*BEFORE_IMAGE_FILES.values(), SESSION_SOURCE_FILE}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _resolved_directory(path: Path, label: str) -> Path:
    original = _absolute(path)
    if original.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = original.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} does not exist") from exc
    metadata = os.stat(resolved, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} is not a directory")
    return resolved


def _read_regular_nofollow(
    path: Path,
    *,
    label: str,
    reject_hardlinks: bool = False,
) -> tuple[bytes, os.stat_result]:
    original = _absolute(path)
    if original.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(original, flags)
    except OSError as exc:
        raise ValueError(f"cannot open {label} without following links") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} is not a regular file")
        if reject_hardlinks and before.st_nlink != 1:
            raise ValueError(f"{label} must not be a hardlink alias")
        chunks = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or len(chunks) != after.st_size:
            raise ValueError(f"{label} drifted during its single-open read")
        entry = os.stat(original, follow_symlinks=False)
        if (entry.st_dev, entry.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(f"{label} changed identity during its single-open read")
        return bytes(chunks), after
    finally:
        os.close(descriptor)


def _decode_json(raw: bytes, label: str) -> Any:
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid strict JSON: {label}") from exc
    if canonical_bytes(value) != raw:
        raise ValueError(f"non-canonical JSON: {label}")
    return value


def _load_json(path: Path) -> Any:
    raw, _ = _read_regular_nofollow(path, label=str(path))
    return _decode_json(raw, str(path))


def _load_object(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    document = _require_object(value, label)
    if set(document) != keys:
        raise ValueError(f"{label} fields are not the closed schema")
    return document


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid {field}")
    return value


def _require_digest(value: Any, field: str) -> str:
    digest = _require_string(value, field)
    if (
        len(digest) != 71
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(f"{field} is not a SHA-256 digest")
    return digest


def _require_mode(value: Any, field: str) -> int:
    if type(value) is not int or value < 0 or value > 0o7777:
        raise ValueError(f"{field} is invalid")
    return value


def _expected_retained_mode(source_mode: int) -> int:
    retained_mode = source_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    if retained_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH) == 0:
        retained_mode |= stat.S_IRUSR
    return retained_mode


def _file_ref(
    value: Any,
    label: str,
    *,
    expected_path: str | None = None,
    include_modes: bool = False,
) -> dict[str, Any]:
    keys = {"path", "sha256", "size"}
    if include_modes:
        keys.update({"retained_mode", "source_mode"})
    reference = _require_exact_keys(value, keys, label)
    path = _require_string(reference["path"], f"{label}.path")
    if expected_path is not None and path != expected_path:
        raise ValueError(f"{label}.path is not the declared confined path")
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"{label}.path escapes its declared namespace")
    _require_digest(reference["sha256"], f"{label}.sha256")
    if type(reference["size"]) is not int or reference["size"] < 0:
        raise ValueError(f"{label}.size is invalid")
    if include_modes:
        source_mode = _require_mode(reference["source_mode"], f"{label}.source_mode")
        retained_mode = _require_mode(reference["retained_mode"], f"{label}.retained_mode")
        if retained_mode != _expected_retained_mode(source_mode):
            raise ValueError(f"{label}.retained_mode does not preserve source readability")
    return reference


def _manifest_rows(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest files must be a non-empty list")
    mapped: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(rows):
        row = _require_exact_keys(
            value,
            {"media_type", "mode", "path", "sha256", "size"},
            f"manifest.files[{index}]",
        )
        name = _require_string(row["path"], f"manifest.files[{index}].path")
        if name in mapped or Path(name).name != name:
            raise ValueError("manifest contains a duplicate or non-local path")
        _require_digest(row["sha256"], f"manifest.files[{index}].sha256")
        if row["mode"] != "0444" or type(row["size"]) is not int or row["size"] < 0:
            raise ValueError("manifest file metadata is invalid")
        mapped[name] = row
    return mapped


def _verify_revision(revision: Path) -> dict[str, Any]:
    revision = _resolved_directory(revision, "revision")
    manifest_raw, manifest_stat = _read_regular_nofollow(
        revision / "ARTIFACT_MANIFEST.json", label="ARTIFACT_MANIFEST.json"
    )
    if sha256_bytes(manifest_raw) != ARTIFACT_MANIFEST_SHA256:
        raise ValueError("wrong rc5 ARTIFACT_MANIFEST bytes")
    if stat.S_IMODE(manifest_stat.st_mode) != 0o444:
        raise ValueError("ARTIFACT_MANIFEST mode drift")
    manifest = _require_object(
        _decode_json(manifest_raw, "ARTIFACT_MANIFEST.json"), "ARTIFACT_MANIFEST.json"
    )
    if (
        manifest.get("program_id") != PROGRAM_ID
        or manifest.get("revision_id") != REVISION_ID
        or manifest.get("v1_active_status_sha256") != V1_ACTIVE_SHA256
    ):
        raise ValueError("wrong rc5 program, revision, or v1 selector identity")
    rows = _manifest_rows(manifest)
    input_hashes = {"ARTIFACT_MANIFEST.json": ARTIFACT_MANIFEST_SHA256}
    input_bytes = {"ARTIFACT_MANIFEST.json": manifest_raw}
    input_values: dict[str, Any] = {"ARTIFACT_MANIFEST.json": manifest}
    for name in FROZEN_ALLOWED_INPUT_FILES[1:]:
        row = rows.get(name)
        if row is None:
            raise ValueError(f"frozen handoff input is absent from manifest: {name}")
        raw, metadata = _read_regular_nofollow(revision / name, label=name)
        digest = sha256_bytes(raw)
        if (
            digest != row["sha256"]
            or len(raw) != row["size"]
            or stat.S_IMODE(metadata.st_mode) != 0o444
        ):
            raise ValueError(f"frozen handoff input drift: {name}")
        input_hashes[name] = digest
        input_bytes[name] = raw
        if name.endswith(".json"):
            input_values[name] = _decode_json(raw, name)
    if input_hashes["MIGRATION_TRANSACTION.json"] != MIGRATION_TRANSACTION_SHA256:
        raise ValueError("wrong rc5 MIGRATION_TRANSACTION bytes")
    contract = _require_exact_keys(
        input_values["FRESH_WORKER_HANDOFF_CONTRACT.json"],
        {
            "allowed_inputs",
            "contract_kind",
            "derivation",
            "distinct_from",
            "isolation",
            "nonclaims",
            "program_id",
            "receipt",
            "revision_id",
            "schema_version",
        },
        "FRESH_WORKER_HANDOFF_CONTRACT.json",
    )
    if (
        contract["allowed_inputs"] != list(FROZEN_ALLOWED_INPUT_FILES)
        or contract["program_id"] != PROGRAM_ID
        or contract["revision_id"] != REVISION_ID
        or contract["schema_version"] != "bb.rl.phase5.fresh_worker_handoff_contract.v2"
    ):
        raise ValueError("fresh-worker frozen input allowlist or identity drift")
    receipt = _require_exact_keys(
        contract["receipt"],
        {"additional_fields_allowed", "each_worker_fields", "pass", "top_level_fields"},
        "fresh_worker_contract.receipt",
    )
    if receipt["additional_fields_allowed"] is not False or receipt["each_worker_fields"] != [
        "pid",
        "input_hashes",
        "derived_action",
        "execution_frontier",
        "target_execution_allowed",
        "ambient_inputs_used",
    ] or receipt["top_level_fields"] != [
        "artifact_manifest_sha256",
        "contract_sha256",
        "worker_count",
        "worker_semantic_sha256",
        "result",
    ]:
        raise ValueError("fresh-worker receipt field contract drift")
    return {
        "input_bytes": input_bytes,
        "input_hashes": input_hashes,
        "input_values": input_values,
        "manifest": manifest,
        "manifest_rows": rows,
        "revision": revision,
    }


def _bundle_documents(bundle: Path) -> dict[str, Any]:
    bundle = _resolved_directory(bundle, "prepared bundle")
    actual = sorted(os.listdir(bundle))
    event_before_file = BEFORE_IMAGE_FILES["v2_event_log"]
    absent_prepared_files = tuple(
        name for name in PREPARED_INPUT_FILES if name != event_before_file
    )
    absent_complete_files = tuple(
        name for name in COMPLETE_BUNDLE_FILES if name != event_before_file
    )
    allowed_sets = {
        tuple(sorted(PREPARED_INPUT_FILES)),
        tuple(sorted(COMPLETE_BUNDLE_FILES)),
        tuple(sorted(absent_prepared_files)),
        tuple(sorted(absent_complete_files)),
    }
    if tuple(actual) not in allowed_sets:
        raise ValueError("prepared bundle file set is incomplete or contains extra files")
    documents: dict[str, Any] = {}
    raw_inputs: dict[str, bytes] = {}
    metadata_inputs: dict[str, os.stat_result] = {}
    identities: set[tuple[int, int]] = set()
    for name in PREPARED_INPUT_FILES:
        if name not in actual:
            continue
        raw, metadata = _read_regular_nofollow(
            bundle / name, label=f"prepared bundle artifact {name}", reject_hardlinks=True
        )
        if stat.S_IMODE(metadata.st_mode) & 0o222:
            raise ValueError(f"prepared bundle artifact is writable: {name}")
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in identities:
            raise ValueError("prepared bundle artifacts contain an inode alias")
        identities.add(identity)
        raw_inputs[name] = raw
        metadata_inputs[name] = metadata
        if name in _RAW_DOCUMENTS:
            documents[name] = raw
        else:
            value = _decode_json(raw, name)
            if name in _LIST_DOCUMENTS:
                if not isinstance(value, list):
                    raise ValueError(f"{name} must be a top-level JSON list")
            elif not isinstance(value, dict):
                raise ValueError(f"{name} must be a JSON object")
            documents[name] = value
    documents["__raw_inputs__"] = raw_inputs
    documents["__metadata_inputs__"] = metadata_inputs
    documents["__bundle_path__"] = bundle
    return documents


def _snapshot(value: Any, label: str) -> dict[str, Any]:
    snapshot = _require_exact_keys(value, {"device", "inode", "mode", "mtime_ns"}, label)
    for field in ("device", "inode", "mode", "mtime_ns"):
        if type(snapshot[field]) is not int or snapshot[field] < 0:
            raise ValueError(f"{label}.{field} is invalid")
    _require_mode(snapshot["mode"], f"{label}.mode")
    return snapshot


def _rollback_descriptor_ref(value: Any, label: str, index: int) -> dict[str, Any]:
    reference = _require_exact_keys(
        value, {"operation_index", "path", "sha256"}, label
    )
    if reference["operation_index"] != index or reference["path"] != "ROLLBACK_DESCRIPTORS.json":
        raise ValueError(f"{label} does not bind its rollback operation")
    _require_digest(reference["sha256"], f"{label}.sha256")
    return reference


def _image_map(document: dict[str, Any], label: str, *, before: bool) -> dict[str, dict[str, Any]]:
    _require_exact_keys(
        document,
        {
            "images",
            "migration_id",
            "native_revision_bound",
            "program_id",
            "revision_id",
            "revision_type",
            "schema_version",
        },
        label,
    )
    images = document["images"]
    if not isinstance(images, list) or len(images) != len(STORE_IDS):
        raise ValueError(f"{label} must contain exactly three images")
    base_keys = {
        "bytes_sha256",
        "native_revision",
        "native_revision_bound",
        "path",
        "reversible",
        "revision",
        "revision_type",
        "rollback_command_sha256",
        "rollback_descriptor_ref",
        "rollback_invariant",
        "size",
        "snapshot",
        "store_id",
    }
    mapped: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(images):
        if not isinstance(value, dict):
            raise ValueError(f"{label}.images[{index}] must be a JSON object")
        store_id = value.get("store_id")
        absent = (
            before
            and store_id == "v2_event_log"
            and value.get("presence") == "absent"
        )
        keys = set(base_keys)
        if before:
            keys.update({"before_image_ref", "retained_mode", "source_mode"})
        if absent:
            keys.update({"parent_device", "parent_inode", "presence"})
        image = _require_exact_keys(value, keys, f"{label}.images[{index}]")
        if store_id != STORE_IDS[index] or store_id in mapped:
            raise ValueError(f"{label} image order or store identity changed")
        descriptor_ref = _rollback_descriptor_ref(
            image["rollback_descriptor_ref"],
            f"{label}.{store_id}.rollback_descriptor_ref",
            index,
        )
        if image["rollback_command_sha256"] != descriptor_ref["sha256"]:
            raise ValueError(f"{label}.{store_id} rollback digest disagreement")
        if absent:
            if (
                image["before_image_ref"] is not None
                or image["bytes_sha256"] is not None
                or image["native_revision"] is not None
                or image["native_revision_bound"] is not False
                or image["retained_mode"] is not None
                or image["revision"] != "absent"
                or image["revision_type"] != "absent"
                or image["size"] is not None
                or image["snapshot"] is not None
                or image["source_mode"] is not None
                or type(image["parent_device"]) is not int
                or image["parent_device"] < 0
                or type(image["parent_inode"]) is not int
                or image["parent_inode"] < 0
            ):
                raise ValueError("event before-image absence binding is invalid")
            mapped[store_id] = image
            continue
        digest = _require_digest(
            image["bytes_sha256"], f"{label}.{store_id}.bytes_sha256"
        )
        if (
            image["revision"] != f"snapshot:{digest}"
            or image["revision_type"] != "file_snapshot_sha256"
            or image["native_revision_bound"] is not False
            or image["native_revision"] is not None
        ):
            raise ValueError(f"{label}.{store_id} falsely claims a native revision")
        if type(image["size"]) is not int or image["size"] < 0:
            raise ValueError(f"{label}.{store_id}.size is invalid")
        if type(image["reversible"]) is not bool:
            raise ValueError(f"{label}.{store_id}.reversible is invalid")
        _require_string(image["path"], f"{label}.{store_id}.path")
        _require_string(
            image["rollback_invariant"], f"{label}.{store_id}.rollback_invariant"
        )
        snapshot = _snapshot(image["snapshot"], f"{label}.{store_id}.snapshot")
        if before:
            source_mode = _require_mode(
                image["source_mode"], f"{label}.{store_id}.source_mode"
            )
            retained_mode = _require_mode(
                image["retained_mode"], f"{label}.{store_id}.retained_mode"
            )
            if (
                retained_mode != _expected_retained_mode(source_mode)
                or snapshot["mode"] != source_mode
            ):
                raise ValueError(f"{label}.{store_id} mode binding is invalid")
            before_ref = _file_ref(
                image["before_image_ref"],
                f"{label}.{store_id}.before_image_ref",
                expected_path=BEFORE_IMAGE_FILES[store_id],
                include_modes=True,
            )
            if (
                before_ref["source_mode"] != source_mode
                or before_ref["retained_mode"] != retained_mode
            ):
                raise ValueError(
                    f"{label}.{store_id} before-image mode binding changed"
                )
        mapped[store_id] = image
    return mapped


def _validate_rollback_descriptors(
    document: dict[str, Any], before_images: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    _require_exact_keys(
        document,
        {
            "migration_id",
            "native_revision_bound",
            "operations",
            "program_id",
            "revision_id",
            "schema_version",
        },
        "ROLLBACK_DESCRIPTORS.json",
    )
    if (
        document["schema_version"] != "bb.rl.phase5.rollback_descriptors.v1"
        or document["program_id"] != PROGRAM_ID
        or document["revision_id"] != REVISION_ID
        or document["native_revision_bound"] is not False
    ):
        raise ValueError(
            "ROLLBACK_DESCRIPTORS.json identity or native revision truth changed"
        )
    operations = document["operations"]
    if not isinstance(operations, list) or len(operations) != len(STORE_IDS):
        raise ValueError(
            "ROLLBACK_DESCRIPTORS.json must contain exactly three operations"
        )
    validated: list[dict[str, Any]] = []
    common = {
        "before_image_ref",
        "before_presence",
        "destination_logical_path",
        "native_revision",
        "native_revision_bound",
        "operation_type",
        "parent_device",
        "parent_inode",
        "retained_mode",
        "rollback_invariant",
        "source_mode",
        "store_id",
    }
    for index, value in enumerate(operations):
        store_id = STORE_IDS[index]
        keys = set(common)
        if store_id == "v2_event_log":
            keys.update(
                {
                    "event_append_metadata_path",
                    "event_type",
                    "required_payload_bindings",
                    "required_receipt_bindings",
                }
            )
        else:
            keys.update({"restore_method", "restore_mode"})
        operation = _require_exact_keys(
            value, keys, f"rollback.operations[{index}]"
        )
        if (
            operation["store_id"] != store_id
            or operation["native_revision_bound"] is not False
            or operation["native_revision"] is not None
            or operation["before_presence"] not in {"absent", "present"}
            or type(operation["parent_device"]) is not int
            or operation["parent_device"] < 0
            or type(operation["parent_inode"]) is not int
            or operation["parent_inode"] < 0
        ):
            raise ValueError(
                f"rollback operation {index} identity or revision truth changed"
            )
        before_image = before_images[store_id]
        if operation["destination_logical_path"] != before_image["path"]:
            raise ValueError(f"rollback operation {index} destination changed")
        if operation["rollback_invariant"] != before_image["rollback_invariant"]:
            raise ValueError(f"rollback operation {index} invariant changed")
        absent = operation["before_presence"] == "absent"
        if absent:
            if (
                store_id != "v2_event_log"
                or operation["before_image_ref"] is not None
                or operation["source_mode"] is not None
                or operation["retained_mode"] is not None
                or before_image.get("presence") != "absent"
                or operation["parent_device"] != before_image["parent_device"]
                or operation["parent_inode"] != before_image["parent_inode"]
            ):
                raise ValueError(
                    "absent event rollback operation binding changed"
                )
            source_mode = None
        else:
            before_ref = _file_ref(
                operation["before_image_ref"],
                f"rollback.operations[{index}].before_image_ref",
                expected_path=BEFORE_IMAGE_FILES[store_id],
                include_modes=True,
            )
            if before_ref != before_image["before_image_ref"]:
                raise ValueError(
                    f"rollback operation {index} before-image binding changed"
                )
            source_mode = _require_mode(
                operation["source_mode"],
                f"rollback.operations[{index}].source_mode",
            )
            retained_mode = _require_mode(
                operation["retained_mode"],
                f"rollback.operations[{index}].retained_mode",
            )
            if (
                source_mode != before_image["source_mode"]
                or retained_mode != before_image["retained_mode"]
                or retained_mode != _expected_retained_mode(source_mode)
            ):
                raise ValueError(
                    f"rollback operation {index} mode binding changed"
                )
        if store_id == "v2_event_log":
            if (
                operation["operation_type"] != "append_compensation_event"
                or operation["event_append_metadata_path"]
                != "EVENT_APPEND_METADATA.json"
                or operation["event_type"] != "MIGRATION_ROLLED_BACK"
                or operation["required_payload_bindings"]
                != [
                    "before_presence",
                    "before_head_sha256",
                    "genesis_created",
                    "committed_event_sha256s",
                    "restored_root_active_selector_sha256",
                    "restored_beads_schema_sha256",
                    "restored_beads_canonical_rows_sha256",
                ]
                or operation["required_receipt_bindings"]
                != [
                    "compensation_event_sha256",
                    "compensation_head_sha256",
                ]
            ):
                raise ValueError(
                    "event rollback descriptor is not the typed append compensation"
                )
        elif (
            operation["operation_type"] != "restore_exact_before_image"
            or operation["restore_mode"] != source_mode
        ):
            raise ValueError(
                f"rollback operation {index} is not exact restoration"
            )
        digest = sha256_bytes(canonical_bytes(operation))
        if (
            before_image["rollback_command_sha256"] != digest
            or before_image["rollback_descriptor_ref"]["sha256"] != digest
        ):
            raise ValueError(f"rollback operation {index} digest mismatch")
        validated.append(operation)
    return validated


def _validate_event_append(
    documents: dict[str, Any], before_images: dict[str, dict[str, Any]], after_images: dict[str, dict[str, Any]]
) -> None:
    events = documents["EVENT_CHAIN.json"]
    append_payload = documents["EVENT_APPEND_PAYLOAD.json"]
    metadata = _require_exact_keys(
        documents["EVENT_APPEND_METADATA.json"],
        {
            "after_event_count",
            "after_head_sha256",
            "after_image_ref",
            "append_event_count",
            "append_payload_ref",
            "before_event_count",
            "before_head_sha256",
            "committed_event_sha256s",
            "migration_id",
            "program_id",
            "revision_id",
            "schema_version",
        },
        "EVENT_APPEND_METADATA.json",
    )
    event_before = before_images["v2_event_log"]
    event_before_file = BEFORE_IMAGE_FILES["v2_event_log"]
    if event_before.get("presence") == "absent":
        if event_before_file in documents["__raw_inputs__"]:
            raise ValueError("absent event before-image has retained bytes")
        before_events = []
    else:
        before_raw = documents[event_before_file]
        before_events = _decode_json(before_raw, event_before_file)
        if not isinstance(before_events, list):
            raise ValueError("retained event before-image is not a JSON list")
    if not isinstance(append_payload, list) or len(append_payload) != 2:
        raise ValueError("event append payload must contain exactly two events")
    if events != [*before_events, *append_payload]:
        raise ValueError("EVENT_CHAIN.json is not the exact append-only list after-image")
    if [event.get("event_type") for event in append_payload if isinstance(event, dict)] != [
        "V1_LINEAGE_IMPORTED",
        "V2_ACTIVATED",
    ]:
        raise ValueError("event append payload has the wrong event types")
    committed_event_sha256s = [
        _require_digest(event.get("event_sha256"), "appended event digest")
        for event in append_payload
        if isinstance(event, dict)
    ]
    if len(committed_event_sha256s) != 2:
        raise ValueError("event append payload rows must be objects")
    before_head = verify_event_chain(before_events)
    after_head = verify_event_chain(events)
    if (
        metadata["schema_version"] != "bb.rl.phase5.event_append_metadata.v1"
        or metadata["program_id"] != PROGRAM_ID
        or metadata["revision_id"] != REVISION_ID
        or metadata["before_event_count"] != len(before_events)
        or metadata["append_event_count"] != len(append_payload)
        or metadata["after_event_count"] != len(events)
        or metadata["before_head_sha256"] != before_head
        or metadata["after_head_sha256"] != after_head
        or metadata["committed_event_sha256s"] != committed_event_sha256s
    ):
        raise ValueError("event append count/head metadata mismatch")
    raw_inputs = documents["__raw_inputs__"]
    append_ref = _file_ref(
        metadata["append_payload_ref"],
        "event_append.append_payload_ref",
        expected_path="EVENT_APPEND_PAYLOAD.json",
    )
    after_ref = _file_ref(
        metadata["after_image_ref"],
        "event_append.after_image_ref",
        expected_path="EVENT_CHAIN.json",
    )
    for reference, name in (
        (append_ref, "EVENT_APPEND_PAYLOAD.json"),
        (after_ref, "EVENT_CHAIN.json"),
    ):
        if reference["sha256"] != sha256_bytes(raw_inputs[name]) or reference["size"] != len(raw_inputs[name]):
            raise ValueError(f"event append reference drift: {name}")
    event_after = after_images["v2_event_log"]
    if event_after["bytes_sha256"] != after_ref["sha256"] or event_after["size"] != after_ref["size"]:
        raise ValueError("event after-image does not bind the exact append-only list")
    if event_before.get("presence") == "absent":
        if event_before["bytes_sha256"] is not None:
            raise ValueError("absent event before-image claims retained bytes")
    elif event_before["bytes_sha256"] != sha256_bytes(before_raw):
        raise ValueError("event before-image descriptor does not bind retained bytes")


def _validate_prepared_bundle(
    revision_state: dict[str, Any], documents: dict[str, Any]
) -> tuple[str, str]:
    before_document = _require_object(documents["BEFORE_IMAGES.json"], "BEFORE_IMAGES.json")
    after_document = _require_object(documents["AFTER_IMAGES.json"], "AFTER_IMAGES.json")
    before_images = _image_map(before_document, "BEFORE_IMAGES.json", before=True)
    after_images = _image_map(after_document, "AFTER_IMAGES.json", before=False)
    migration_ids = {
        document.get("migration_id")
        for document in (
            before_document,
            after_document,
            documents["ROLLBACK_DESCRIPTORS.json"],
            documents["EVENT_APPEND_METADATA.json"],
            documents["BEADS_RESOLUTION.json"],
            documents["SESSION_PROJECTION.json"],
            documents["PREPARED_ACTIVE_STATUS.json"],
            documents["PREPARED_RUN_QUEUE.json"],
            documents["PREPARED_ROOT_SELECTOR.json"],
        )
        if isinstance(document, dict)
    }
    if len(migration_ids) != 1:
        raise ValueError("prepared documents disagree on migration_id")
    migration_id = _require_string(next(iter(migration_ids)), "migration_id")
    for label, document in (("before", before_document), ("after", after_document)):
        if (
            document["program_id"] != PROGRAM_ID
            or document["revision_id"] != REVISION_ID
            or document["schema_version"] != f"bb.rl.phase5.{label}_images.v2"
            or document["native_revision_bound"] is not False
            or document["revision_type"] != "file_snapshot_sha256"
        ):
            raise ValueError(f"{label} image document identity changed")
    raw_inputs = documents["__raw_inputs__"]
    metadata_inputs = documents["__metadata_inputs__"]
    for store_id, filename in BEFORE_IMAGE_FILES.items():
        image = before_images[store_id]
        if image.get("presence") == "absent":
            if filename in raw_inputs or image["before_image_ref"] is not None:
                raise ValueError(
                    "absent event before-image unexpectedly retained bytes"
                )
            continue
        payload = raw_inputs[filename]
        reference = image["before_image_ref"]
        retained_mode = stat.S_IMODE(metadata_inputs[filename].st_mode)
        if image["bytes_sha256"] != sha256_bytes(payload) or image["size"] != len(payload):
            raise ValueError(f"retained before-image bytes drift: {store_id}")
        if reference["sha256"] != image["bytes_sha256"] or reference["size"] != image["size"]:
            raise ValueError(f"retained before-image reference drift: {store_id}")
        if retained_mode != image["retained_mode"]:
            raise ValueError(f"retained before-image mode drift: {store_id}")
    decision_raw = raw_inputs[SPEC_FREEZE_DECISION_FILE]
    if (
        len(decision_raw) != SPEC_FREEZE_DECISION_SIZE
        or sha256_bytes(decision_raw) != SPEC_FREEZE_DECISION_SHA256
    ):
        raise ValueError("RC5 SPEC_FREEZE decision artifact bytes changed")
    validate_spec_freeze_decision(
        _require_object(
            documents[SPEC_FREEZE_DECISION_FILE],
            SPEC_FREEZE_DECISION_FILE,
        ),
        artifact_sha256=SPEC_FREEZE_DECISION_SHA256,
    )
    for store_id, filename in AFTER_IMAGE_FILES.items():
        payload = raw_inputs[filename]
        image = after_images[store_id]
        if image["bytes_sha256"] != sha256_bytes(payload) or image["size"] != len(payload):
            raise ValueError(f"after-image bytes drift: {store_id}")
    _validate_rollback_descriptors(documents["ROLLBACK_DESCRIPTORS.json"], before_images)
    _validate_event_append(documents, before_images, after_images)

    active = _require_object(documents["PREPARED_ACTIVE_STATUS.json"], "PREPARED_ACTIVE_STATUS.json")
    queue = _require_object(documents["PREPARED_RUN_QUEUE.json"], "PREPARED_RUN_QUEUE.json")
    beads = _require_object(documents["BEADS_RESOLUTION.json"], "BEADS_RESOLUTION.json")
    session = _require_object(documents["SESSION_PROJECTION.json"], "SESSION_PROJECTION.json")
    selector = _require_exact_keys(
        documents["PREPARED_ROOT_SELECTOR.json"],
        {"artifacts", "event_cursor", "generation", "migration_id", "program_id", "revision_id", "schema_version"},
        "PREPARED_ROOT_SELECTOR.json",
    )
    spec_hashes = {active.get("spec_freeze_sha256"), queue.get("spec_freeze_sha256"), beads.get("spec_freeze_sha256")}
    if len(spec_hashes) != 1:
        raise ValueError("prepared projections disagree on SPEC_FREEZE")
    spec_freeze_sha256 = _require_digest(next(iter(spec_hashes)), "spec_freeze_sha256")
    if spec_freeze_sha256 != SPEC_FREEZE_DECISION_SHA256:
        raise ValueError("prepared projections do not bind the exact RC5 SPEC_FREEZE decision")
    expected_active = derive_active_status(
        _require_object(revision_state["input_values"]["DRAFT_STATUS.json"], "DRAFT_STATUS.json"),
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    expected_queue = derive_run_queue(
        _require_object(revision_state["input_values"]["RUN_QUEUE.json"], "RUN_QUEUE.json"),
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    if active != expected_active or queue != expected_queue:
        raise ValueError("prepared active status or run queue is not the exact frozen derivation")
    before_session = _decode_json(
        raw_inputs[SESSION_SOURCE_FILE],
        SESSION_SOURCE_FILE,
    )
    expected_session = derive_session_projection(
        _require_object(before_session, "retained session before-image"),
        active,
        queue,
        migration_id=migration_id,
    )
    if session != expected_session:
        raise ValueError("session projection is not derived from retained before-image bytes")
    validate_beads_projection(beads)
    artifacts = _require_exact_keys(
        selector["artifacts"],
        {"active_status", "artifact_manifest", "authority_policy", "evidence_index", "run_queue"},
        "PREPARED_ROOT_SELECTOR.artifacts",
    )
    reference_files = {
        "active_status": "PREPARED_ACTIVE_STATUS.json",
        "run_queue": "PREPARED_RUN_QUEUE.json",
    }
    for key, filename in reference_files.items():
        reference = _file_ref(artifacts[key], f"selector.artifacts.{key}")
        payload = raw_inputs[filename]
        if reference["sha256"] != sha256_bytes(payload) or reference["size"] != len(payload):
            raise ValueError(f"selector reference drift: {key}")
    manifest_reference = _file_ref(artifacts["artifact_manifest"], "selector.artifacts.artifact_manifest")
    if manifest_reference["sha256"] != ARTIFACT_MANIFEST_SHA256:
        raise ValueError("selector artifact manifest reference drift")
    for key in ("authority_policy", "evidence_index"):
        _file_ref(artifacts[key], f"selector.artifacts.{key}")
    if (
        selector["program_id"] != PROGRAM_ID
        or selector["revision_id"] != REVISION_ID
        or selector["migration_id"] != migration_id
        or selector["schema_version"] != "bb.rl.phase5.root_active_selector.v1"
    ):
        raise ValueError("prepared selector identity changed")
    frontier = _eligible_frontier(queue)
    if frontier != ["AT0"]:
        raise ValueError("post-cutover execution frontier is not exactly AT0")
    accepted_documents = [
        before_document,
        after_document,
        documents["ROLLBACK_DESCRIPTORS.json"],
        documents["EVENT_APPEND_METADATA.json"],
        beads,
        session,
        active,
        queue,
        selector,
        {"events": documents["EVENT_CHAIN.json"]},
        {"events": documents["EVENT_APPEND_PAYLOAD.json"]},
    ]
    validate_zero_authority(*accepted_documents)
    return migration_id, spec_freeze_sha256


def _eligible_frontier(run_queue: dict[str, Any]) -> list[str]:
    eligible = run_queue.get("eligible")
    if not isinstance(eligible, list):
        raise ValueError("prepared run queue eligible field is not a list")
    frontier: list[str] = []
    for entry in eligible:
        if isinstance(entry, str):
            frontier.append(entry)
        elif isinstance(entry, dict):
            frontier.append(_require_string(entry.get("packet_key"), "eligible packet_key"))
        else:
            raise ValueError("invalid prepared eligible entry")
    return frontier


def _implementation_inputs() -> dict[str, str]:
    paths = {
        "implementation/replay_phase5_v2_prepared_handoff.py": Path(__file__).resolve(),
        "implementation/breadboard.rl.phase5.migration_projections": Path(migration_projections.__file__).resolve(),
        "implementation/breadboard.rl.phase5.migration_transaction": Path(migration_transaction.__file__).resolve(),
        "runtime/python_executable": Path(sys.executable).resolve(),
    }
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        raw, _ = _read_regular_nofollow(path, label=name)
        hashes[name] = sha256_bytes(raw)
    environment = _allowlisted_environment()
    for key in ("LANG", "LC_ALL", "PATH", "PYTHONHASHSEED"):
        name = f"environment/{key}"
        hashes[name] = sha256_bytes(canonical_bytes(environment[key]))
    return hashes


def derive_semantic(revision: Path, bundle: Path, *, require_empty_cwd: bool) -> dict[str, Any]:
    if require_empty_cwd and any(Path.cwd().iterdir()):
        raise ValueError("fresh-worker cwd is not empty")
    revision_state = _verify_revision(revision)
    documents = _bundle_documents(bundle)
    migration_id, _ = _validate_prepared_bundle(revision_state, documents)
    queue = _require_object(documents["PREPARED_RUN_QUEUE.json"], "PREPARED_RUN_QUEUE.json")
    active = _require_object(documents["PREPARED_ACTIVE_STATUS.json"], "PREPARED_ACTIVE_STATUS.json")
    input_hashes = dict(revision_state["input_hashes"])
    for name, payload in documents["__raw_inputs__"].items():
        input_hashes[f"prepared/{name}"] = sha256_bytes(payload)
    input_hashes.update(_implementation_inputs())
    ambient_inputs_used = [
        *(
            f"prepared/{name}"
            for name in PREPARED_INPUT_FILES
            if name in documents["__raw_inputs__"]
        ),
        "implementation/replay_phase5_v2_prepared_handoff.py",
        "implementation/breadboard.rl.phase5.migration_projections",
        "implementation/breadboard.rl.phase5.migration_transaction",
        "runtime/python_executable",
        "runtime/python_standard_library",
        "environment/LANG",
        "environment/LC_ALL",
        "environment/PATH",
        "environment/PYTHONHASHSEED",
    ]
    return {
        "ambient_inputs_used": ambient_inputs_used,
        "derived_action": active.get("allowed_next"),
        "execution_frontier": _eligible_frontier(queue),
        "input_hashes": input_hashes,
        "target_execution_allowed": False,
    }


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _safe_output_destination(
    output_root: Path,
    output: Path,
    *,
    protected: tuple[Path, ...],
) -> tuple[Path, Path, os.stat_result]:
    root = _resolved_directory(output_root, "output-root")
    if _absolute(output_root).is_symlink():
        raise ValueError("output-root must not be a symlink")
    destination = _absolute(output)
    if destination.exists() or destination.is_symlink():
        raise ValueError("output file must be new and must not be a symlink")
    if destination.parent.resolve(strict=True) != root:
        raise ValueError("output file must be a direct child of output-root")
    resolved_protected = tuple(_absolute(path).resolve(strict=True) for path in protected)
    for item in resolved_protected:
        if _paths_overlap(root, item) or _paths_overlap(destination, item):
            raise ValueError("output-root/output overlaps a protected input")
    root_stat = os.stat(root, follow_symlinks=False)
    for item in resolved_protected:
        item_stat = os.stat(item, follow_symlinks=False)
        if (root_stat.st_dev, root_stat.st_ino) == (item_stat.st_dev, item_stat.st_ino):
            raise ValueError("output-root is an inode alias of a protected input")
    return root, destination, root_stat


def _write_new_canonical(
    output_root: Path,
    output: Path,
    value: Any,
    *,
    protected: tuple[Path, ...],
) -> None:
    payload = canonical_bytes(value)
    root, destination, expected_root = _safe_output_destination(
        output_root, output, protected=protected
    )
    root_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd = os.open(root, root_flags)
    temp_name: str | None = None
    try:
        opened_root = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or (opened_root.st_dev, opened_root.st_ino)
            != (expected_root.st_dev, expected_root.st_ino)
        ):
            raise ValueError("output-root changed identity before publication")
        temp_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _ in range(128):
            candidate = f".phase5-receipt-{os.getpid()}-{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(candidate, temp_flags, 0o600, dir_fd=root_fd)
            except FileExistsError:
                continue
            temp_name = candidate
            break
        else:
            raise FileExistsError("could not allocate a unique receipt temp file")
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while publishing replay receipt")
                view = view[written:]
            os.fchmod(descriptor, 0o444)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(
            temp_name,
            destination.name,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
            follow_symlinks=False,
        )
        os.fsync(root_fd)
        os.unlink(temp_name, dir_fd=root_fd)
        temp_name = None
        os.fsync(root_fd)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            else:
                os.fsync(root_fd)
        os.close(root_fd)


def _worker(args: argparse.Namespace) -> int:
    revision = _resolved_directory(args.revision, "revision")
    bundle = _resolved_directory(args.bundle, "prepared bundle")
    semantic = derive_semantic(revision, bundle, require_empty_cwd=True)
    receipt = {"pid": os.getpid(), **semantic}
    _require_exact_keys(
        receipt,
        {"pid", "input_hashes", "derived_action", "execution_frontier", "target_execution_allowed", "ambient_inputs_used"},
        "worker receipt",
    )
    validate_zero_authority(receipt)
    _write_new_canonical(
        args.output_root,
        args.worker_output,
        receipt,
        protected=(revision, bundle),
    )
    return 0


def _allowlisted_environment() -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONHASHSEED": "0",
    }


def _parent(args: argparse.Namespace) -> int:
    revision = _resolved_directory(args.revision, "revision")
    bundle = _resolved_directory(args.bundle, "prepared bundle")
    script = Path(__file__).resolve()
    workers: list[dict[str, Any]] = []
    semantic_bytes: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="phase5-v2-prepared-worker-a-") as first, tempfile.TemporaryDirectory(prefix="phase5-v2-prepared-worker-b-") as second:
        for directory_name in (first, second):
            directory = Path(directory_name).resolve()
            output = directory / "worker-output.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--revision",
                    str(revision),
                    "--bundle",
                    str(bundle),
                    "--output-root",
                    str(directory),
                    "--worker-output",
                    str(output),
                ],
                cwd=directory,
                env=_allowlisted_environment(),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ValueError(
                    "prepared fresh worker failed: "
                    f"exit={result.returncode}; stderr={result.stderr.strip()}"
                )
            worker = _load_object(output)
            _require_exact_keys(
                worker,
                {"pid", "input_hashes", "derived_action", "execution_frontier", "target_execution_allowed", "ambient_inputs_used"},
                "worker receipt",
            )
            if type(worker["pid"]) is not int or worker["pid"] <= 0:
                raise ValueError("worker receipt pid is invalid")
            semantic = {key: value for key, value in worker.items() if key != "pid"}
            semantic_blob = canonical_bytes(semantic)
            workers.append(worker)
            semantic_bytes.append(semantic_blob)
    if len({worker["pid"] for worker in workers}) != 2:
        raise ValueError("prepared workers did not run in distinct processes")
    if semantic_bytes[0] != semantic_bytes[1]:
        raise ValueError("prepared workers derived different semantic results")
    semantic_sha256 = sha256_bytes(semantic_bytes[0])
    frozen_receipt = {
        "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "contract_sha256": FRESH_WORKER_CONTRACT_SHA256,
        "result": "non_conformance_preparation_replay",
        "worker_count": 2,
        "worker_semantic_sha256": semantic_sha256,
    }
    _require_exact_keys(
        frozen_receipt,
        {"artifact_manifest_sha256", "contract_sha256", "worker_count", "worker_semantic_sha256", "result"},
        "frozen contract receipt",
    )
    report = {
        "frozen_contract_passed": False,
        "frozen_contract_receipt": frozen_receipt,
        "replay_mode": "non_conformance_preparation_replay",
        "schema_version": "bb.rl.phase5.prepared_image_replay_report.v1",
        "workers": workers,
    }
    validate_zero_authority(report)
    _write_new_canonical(
        args.output_root,
        args.report,
        report,
        protected=(revision, bundle),
    )
    print(json.dumps(report, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    outputs = parser.add_mutually_exclusive_group(required=True)
    outputs.add_argument("--worker-output", type=Path)
    outputs.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.worker_output is not None:
        return _worker(args)
    return _parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
