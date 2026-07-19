from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
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
    FileMigrationGate,
    MigrationInProgress,
    build_event,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
    verify_event_chain,
)
from scripts.rl_phase5.replay_phase5_v2_prepared_handoff import (  # noqa: E402
    ARTIFACT_MANIFEST_SHA256,
    MIGRATION_TRANSACTION_SHA256,
    PREPARED_INPUT_FILES,
    PROGRAM_ID,
    REVISION_ID,
    STORE_IDS,
    V1_ACTIVE_SHA256,
    _allowlisted_environment,
    _verify_revision,
)

BEFORE_IMAGE_FILES = {
    "v2_event_log": "BEFORE_IMAGE_v2_event_log.bin",
    "beads_projection": "BEFORE_IMAGE_beads_projection.bin",
    "root_active_selector": "BEFORE_IMAGE_root_active_selector.bin",
}
SESSION_SOURCE_ID = "session_pre_handoff"
SESSION_SOURCE_FILE = "SESSION_PRE_HANDOFF_SNAPSHOT.bin"
SPEC_FREEZE_SOURCE_ID = "rc5_spec_freeze_decision"
SPEC_FREEZE_DECISION_FILE = "SPEC_FREEZE_DECISION.json"
PREPARATION_SUPPORT_FILES = (
    *BEFORE_IMAGE_FILES.values(),
    SESSION_SOURCE_FILE,
    SPEC_FREEZE_DECISION_FILE,
    "ROLLBACK_DESCRIPTORS.json",
    "EVENT_APPEND_PAYLOAD.json",
    "EVENT_APPEND_METADATA.json",
)
BUNDLE_FILES = tuple(
    dict.fromkeys(
        PREPARED_INPUT_FILES
        + PREPARATION_SUPPORT_FILES
        + (
            "FRESH_WORKER_PREPARATION_REPORT.json",
            "MIGRATION_PREPARATION_REPORT.json",
        )
    )
)
SPEC_FREEZE_DECISION_SHA256 = (
    "sha256:e06abb5bf8b0bcbeff6c26721a241eba8961856822b030811df69fd3b8d1da36"
)
SPEC_FREEZE_DECISION_SIZE = 1585
_MIGRATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CapturedSource:
    store_id: str
    source_path: Path
    logical_path: str
    payload: bytes | None
    value: Any
    bytes_sha256: str | None
    size: int | None
    source_mode: int | None
    retained_mode: int | None
    device: int | None
    inode: int | None
    mtime_ns: int | None
    parent_device: int
    parent_inode: int
    presence: str


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _decode_json(raw: bytes, source: str) -> Any:
    try:
        return json.loads(raw, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load JSON from {source}") from exc


def _read_regular_file(path: Path) -> tuple[bytes, os.stat_result]:
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        parent_descriptor = os.open(path.parent, directory_flags)
        try:
            descriptor = os.open(path.name, file_flags, dir_fd=parent_descriptor)
        except BaseException:
            os.close(parent_descriptor)
            raise
    except OSError as exc:
        raise ValueError(f"cannot open confined regular file: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"source is not a regular file: {path}")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(payload) != after.st_size
        ):
            raise ValueError(f"source drifted during capture: {path}")
        return bytes(payload), after
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def load_json(path: Path) -> Any:
    raw, _ = _read_regular_file(path)
    return _decode_json(raw, str(path))


def load_object(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _decode_beads_rows(raw: bytes) -> list[dict[str, Any]]:
    try:
        value = _decode_json(raw, "captured Beads export")
    except ValueError:
        values = [
            _decode_json(line, "captured Beads export JSONL row")
            for line in raw.splitlines()
            if line.strip()
        ]
    else:
        if isinstance(value, dict):
            values = value.get("issues")
        else:
            values = value
    if not isinstance(values, list) or not values:
        raise ValueError("Beads export must contain a non-empty issue list")
    if any(not isinstance(row, dict) for row in values):
        raise ValueError("Beads export issue rows must be objects")
    return values


def _spec_freeze_artifact_sha256(
    decision: dict[str, Any], *, payload: bytes
) -> str:
    digest = sha256_bytes(payload)
    if len(payload) != SPEC_FREEZE_DECISION_SIZE:
        raise ValueError("RC5 SPEC_FREEZE decision artifact size changed")
    if digest != SPEC_FREEZE_DECISION_SHA256:
        raise ValueError("RC5 SPEC_FREEZE decision artifact bytes changed")
    validate_spec_freeze_decision(decision, artifact_sha256=digest)
    return digest


def extract_spec_freeze(
    decision: dict[str, Any], *, payload: bytes
) -> tuple[dict[str, Any], str]:
    digest = _spec_freeze_artifact_sha256(decision, payload=payload)
    return decision, digest


def file_ref(path: Path, logical_path: str) -> dict[str, Any]:
    payload, _ = _read_regular_file(path)
    return {
        "path": logical_path,
        "sha256": sha256_bytes(payload),
        "size": len(payload),
    }


def _retained_mode(source_mode: int) -> int:
    retained_mode = source_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    if not retained_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
        retained_mode |= stat.S_IRUSR
    return retained_mode


def _write_stage_bytes(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"failed to write {path}")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_stage(path: Path, value: Any) -> None:
    _write_stage_bytes(path, canonical_bytes(value))


def _store_contracts(transaction: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stores = transaction.get("stores")
    if not isinstance(stores, list) or len(stores) != 3:
        raise ValueError("migration transaction must declare three stores")
    mapped = {store.get("id"): store for store in stores if isinstance(store, dict)}
    if tuple(mapped) != STORE_IDS:
        raise ValueError("migration transaction store order or IDs changed")
    return mapped


def _capture_source(
    *,
    store_id: str,
    path: Path,
    logical_path: str,
) -> CapturedSource:
    payload, metadata = _read_regular_file(path)
    if store_id == "beads_projection":
        value: Any = _decode_beads_rows(payload)
    else:
        value = _decode_json(payload, logical_path)
    source_mode = stat.S_IMODE(metadata.st_mode)
    parent = os.stat(path.parent, follow_symlinks=False)
    return CapturedSource(
        store_id=store_id,
        source_path=path,
        logical_path=logical_path,
        payload=payload,
        value=value,
        bytes_sha256=sha256_bytes(payload),
        size=len(payload),
        source_mode=source_mode,
        retained_mode=_retained_mode(source_mode),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mtime_ns=metadata.st_mtime_ns,
        parent_device=parent.st_dev,
        parent_inode=parent.st_ino,
        presence="present",
    )
 
 
def _capture_event_source(path: Path, logical_path: str) -> CapturedSource:
    try:
        os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        parent = os.stat(path.parent, follow_symlinks=False)
        if not stat.S_ISDIR(parent.st_mode):
            raise ValueError("event-log parent is not a directory")
        return CapturedSource(
            store_id="v2_event_log",
            source_path=path,
            logical_path=logical_path,
            payload=None,
            value=[],
            bytes_sha256=None,
            size=None,
            source_mode=None,
            retained_mode=None,
            device=None,
            inode=None,
            mtime_ns=None,
            parent_device=parent.st_dev,
            parent_inode=parent.st_ino,
            presence="absent",
        )
    return _capture_source(
        store_id="v2_event_log",
        path=path,
        logical_path=logical_path,
    )


def capture_before_images(
    *,
    execution_root: Path,
    beads_export: Path,
    session_state: Path,
    transaction: dict[str, Any],
    spec_freeze_decision: Path,
) -> dict[str, CapturedSource]:
    _store_contracts(transaction)
    source_paths = {
        "v2_event_log": (
            execution_root / "EVENT_CHAIN.json",
            "execution-root/EVENT_CHAIN.json",
        ),
        "beads_projection": (beads_export, "beads-export"),
        SESSION_SOURCE_ID: (session_state, "session-state"),
        SPEC_FREEZE_SOURCE_ID: (
            spec_freeze_decision,
            "RC5_SPEC_FREEZE_DECISION.json",
        ),
        "root_active_selector": (
            execution_root / "ACTIVE_STATUS.json",
            "execution-root/ACTIVE_STATUS.json",
        ),
    }
    captures = {
        store_id: (
            _capture_event_source(*source_paths[store_id])
            if store_id == "v2_event_log"
            else _capture_source(
                store_id=store_id,
                path=source_paths[store_id][0],
                logical_path=source_paths[store_id][1],
            )
        )
        for store_id in (*STORE_IDS, SESSION_SOURCE_ID, SPEC_FREEZE_SOURCE_ID)
    }
    identities = {
        (item.device, item.inode)
        for item in captures.values()
        if item.presence == "present"
    }
    if len(identities) != sum(
        item.presence == "present" for item in captures.values()
    ):
        raise ValueError("captured migration inputs must be distinct regular files")
    if captures["root_active_selector"].bytes_sha256 != V1_ACTIVE_SHA256:
        raise ValueError("root ACTIVE_STATUS is not the exact frozen v1 selector")
    event_value = captures["v2_event_log"].value
    if not isinstance(event_value, list):
        raise ValueError("live EVENT_CHAIN must be a JSON list")
    verify_event_chain(event_value)
    session_value = captures[SESSION_SOURCE_ID].value
    if not isinstance(session_value, dict):
        raise ValueError("session state must be a JSON object")
    if (
        session_value.get("target_lease") is not None
        or session_value.get("active_packet") is not None
    ):
        raise ValueError("session state contains an active packet or target lease")
    validate_zero_authority(session_value)
    decision_value = captures[SPEC_FREEZE_SOURCE_ID].value
    if not isinstance(decision_value, dict):
        raise ValueError("RC5 SPEC_FREEZE decision must be a JSON object")
    _spec_freeze_artifact_sha256(
        decision_value,
        payload=captures[SPEC_FREEZE_SOURCE_ID].payload,
    )
    return captures


def _rollback_operations(
    captures: dict[str, CapturedSource],
    stores: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for store_id in STORE_IDS:
        source = captures[store_id]
        before_image_ref = (
            {
                "path": BEFORE_IMAGE_FILES[store_id],
                "sha256": source.bytes_sha256,
                "size": source.size,
                "source_mode": source.source_mode,
                "retained_mode": source.retained_mode,
            }
            if source.presence == "present"
            else None
        )
        operation: dict[str, Any] = {
            "before_image_ref": before_image_ref,
            "before_presence": source.presence,
            "destination_logical_path": source.logical_path,
            "native_revision": None,
            "native_revision_bound": False,
            "parent_device": source.parent_device,
            "parent_inode": source.parent_inode,
            "retained_mode": source.retained_mode,
            "rollback_invariant": str(stores[store_id]["rollback_invariant"]),
            "source_mode": source.source_mode,
            "store_id": store_id,
        }
        if store_id == "v2_event_log":
            operation.update(
                {
                    "event_append_metadata_path": "EVENT_APPEND_METADATA.json",
                    "event_type": "MIGRATION_ROLLED_BACK",
                    "operation_type": "append_compensation_event",
                    "required_payload_bindings": [
                        "before_presence",
                        "before_head_sha256",
                        "genesis_created",
                        "committed_event_sha256s",
                        "restored_root_active_selector_sha256",
                        "restored_beads_schema_sha256",
                        "restored_beads_canonical_rows_sha256",
                    ],
                    "required_receipt_bindings": [
                        "compensation_event_sha256",
                        "compensation_head_sha256",
                    ],
                }
            )
        else:
            restore_method = (
                "beads_transaction_import"
                if store_id == "beads_projection"
                else "atomic_regular_file_replace"
            )
            operation.update(
                {
                    "operation_type": "restore_exact_before_image",
                    "restore_method": restore_method,
                    "restore_mode": source.source_mode,
                }
            )
        operations.append(operation)
    return operations


def _snapshot_image(
    *,
    store_id: str,
    logical_path: str,
    payload: bytes,
    snapshot: dict[str, int],
    reversible: bool,
    rollback_invariant: str,
    rollback_operation: dict[str, Any],
    rollback_operation_index: int,
    before_image_ref: dict[str, Any] | None = None,
    source_mode: int | None = None,
    retained_mode: int | None = None,
) -> dict[str, Any]:
    digest = sha256_bytes(payload)
    rollback_digest = sha256_bytes(canonical_bytes(rollback_operation))
    image = {
        "bytes_sha256": digest,
        "native_revision": None,
        "native_revision_bound": False,
        "path": logical_path,
        "reversible": reversible,
        "revision": f"snapshot:{digest}",
        "revision_type": "file_snapshot_sha256",
        "rollback_command_sha256": rollback_digest,
        "rollback_descriptor_ref": {
            "operation_index": rollback_operation_index,
            "path": "ROLLBACK_DESCRIPTORS.json",
            "sha256": rollback_digest,
        },
        "rollback_invariant": rollback_invariant,
        "size": len(payload),
        "snapshot": snapshot,
        "store_id": store_id,
    }
    if before_image_ref is not None:
        image["before_image_ref"] = before_image_ref
    if source_mode is not None and retained_mode is not None:
        image["source_mode"] = source_mode
        image["retained_mode"] = retained_mode
    return image


def _before_image_rows(
    captures: dict[str, CapturedSource],
    stores: dict[str, dict[str, Any]],
    rollback_operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for index, store_id in enumerate(STORE_IDS):
        source = captures[store_id]
        if source.presence == "absent":
            rollback_digest = sha256_bytes(
                canonical_bytes(rollback_operations[index])
            )
            rows.append(
                {
                    "before_image_ref": None,
                    "bytes_sha256": None,
                    "native_revision": None,
                    "native_revision_bound": False,
                    "parent_device": source.parent_device,
                    "parent_inode": source.parent_inode,
                    "path": source.logical_path,
                    "presence": "absent",
                    "retained_mode": None,
                    "reversible": bool(stores[store_id]["reversible"]),
                    "revision": "absent",
                    "revision_type": "absent",
                    "rollback_command_sha256": rollback_digest,
                    "rollback_descriptor_ref": {
                        "operation_index": index,
                        "path": "ROLLBACK_DESCRIPTORS.json",
                        "sha256": rollback_digest,
                    },
                    "rollback_invariant": str(
                        stores[store_id]["rollback_invariant"]
                    ),
                    "size": None,
                    "snapshot": None,
                    "source_mode": None,
                    "store_id": store_id,
                }
            )
            continue
        assert source.payload is not None
        assert source.source_mode is not None
        assert source.retained_mode is not None
        assert source.bytes_sha256 is not None
        assert source.device is not None
        assert source.inode is not None
        assert source.mtime_ns is not None
        rows.append(
            _snapshot_image(
                store_id=store_id,
                logical_path=source.logical_path,
                payload=source.payload,
                snapshot={
                    "device": source.device,
                    "inode": source.inode,
                    "mode": source.source_mode,
                    "mtime_ns": source.mtime_ns,
                },
                reversible=bool(stores[store_id]["reversible"]),
                rollback_invariant=str(stores[store_id]["rollback_invariant"]),
                rollback_operation=rollback_operations[index],
                rollback_operation_index=index,
                source_mode=source.source_mode,
                retained_mode=source.retained_mode,
                before_image_ref={
                    "path": BEFORE_IMAGE_FILES[store_id],
                    "sha256": source.bytes_sha256,
                    "size": source.size,
                    "source_mode": source.source_mode,
                    "retained_mode": source.retained_mode,
                },
            )
        )
    return rows


def _staged_image_row(
    *,
    store_id: str,
    path: Path,
    logical_path: str,
    store: dict[str, Any],
    rollback_operation: dict[str, Any],
    rollback_operation_index: int,
) -> dict[str, Any]:
    payload, metadata = _read_regular_file(path)
    return _snapshot_image(
        store_id=store_id,
        logical_path=logical_path,
        payload=payload,
        snapshot={
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode": stat.S_IMODE(metadata.st_mode),
            "mtime_ns": metadata.st_mtime_ns,
        },
        reversible=bool(store["reversible"]),
        rollback_invariant=str(store["rollback_invariant"]),
        rollback_operation=rollback_operation,
        rollback_operation_index=rollback_operation_index,
    )


def _recheck_captured_sources(captures: dict[str, CapturedSource]) -> None:
    for store_id in sorted(captures):
        captured = captures[store_id]
        if captured.presence == "absent":
            try:
                os.stat(captured.source_path, follow_symlinks=False)
            except FileNotFoundError:
                parent = os.stat(
                    captured.source_path.parent, follow_symlinks=False
                )
                if (
                    parent.st_dev != captured.parent_device
                    or parent.st_ino != captured.parent_inode
                ):
                    raise ValueError(
                        f"live source parent drifted before publication: {store_id}"
                    )
                continue
            raise ValueError(f"live source appeared before publication: {store_id}")
        payload, metadata = _read_regular_file(captured.source_path)
        if (
            metadata.st_dev != captured.device
            or metadata.st_ino != captured.inode
            or stat.S_IMODE(metadata.st_mode) != captured.source_mode
            or len(payload) != captured.size
            or sha256_bytes(payload) != captured.bytes_sha256
        ):
            raise ValueError(f"live source drifted before publication: {store_id}")
def _prepared_validation(
    *,
    staging: Path,
    migration_id: str,
    spec_freeze_sha256: str,
) -> dict[str, Any]:
    return {
        "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "bundle_input_hashes": {
            name: sha256_file(staging / name)
            for name in PREPARED_INPUT_FILES
            if (staging / name).is_file()
        },
        "execution_frontier": ["AT0"],
        "migration_id": migration_id,
        "program_id": PROGRAM_ID,
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.prepared_validation.v1",
        "spec_freeze_sha256": spec_freeze_sha256,
        "target_execution_allowed": False,
        "migration_transaction_sha256": MIGRATION_TRANSACTION_SHA256,
        "zero_authority": True,
    }


def exercise_temporary_gate(migration_id: str) -> dict[str, Any]:
    owner_token = sha256_bytes(canonical_bytes({"migration_id": migration_id, "role": "owner"}))
    verifier_token = sha256_bytes(
        canonical_bytes({"migration_id": migration_id, "role": "verifier"})
    )
    blocked: list[str] = []
    with tempfile.TemporaryDirectory(prefix="phase5-v2-gate-exercise-") as directory:
        gate = FileMigrationGate(Path(directory) / "migration.gate")
        gate.acquire(migration_id, STORE_IDS, owner_token, verifier_token)
        gate.load()
        gate.assert_owner(migration_id, owner_token)
        gate.assert_verifier(migration_id, verifier_token)
        for store_id in STORE_IDS:
            try:
                gate.ordinary_read(store_id)
            except MigrationInProgress:
                blocked.append(store_id)
            else:
                raise ValueError(f"temporary gate did not block {store_id}")
        renewed = gate.renew(migration_id, owner_token)
        if renewed.get("renewal") != 1 or not gate.status().get("acquired"):
            raise ValueError("temporary gate renewal/status exercise failed")
        gate.release(migration_id, owner_token)
        if gate.status().get("acquired"):
            raise ValueError("temporary gate release exercise failed")
    return {
        "blocked_store_ids": blocked,
        "exercised_in_temporary_directory": True,
        "live_gate_acquired": False,
        "released": True,
    }


def _atomic_publish_directory_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        try:
            rename = libc.renamex_np
        except AttributeError as exc:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace directory publication is unsupported",
                destination,
            ) from exc
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError as exc:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace directory publication is unsupported",
                destination,
            ) from exc
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory publication is unsupported",
            destination,
        )
    if result != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(
            error,
            os.strerror(error),
            destination,
        )


def _assert_safe_output(
    output_dir: Path,
    execution_root: Path,
    revision: Path,
    beads_export: Path,
    session_state: Path,
    spec_freeze_decision: Path,
) -> None:
    output = output_dir.resolve()
    if output.exists():
        raise ValueError("output-dir must not already exist")
    if not output.parent.is_dir():
        raise ValueError("output-dir parent must already exist")
    for protected in (
        execution_root.resolve(),
        revision.resolve(),
        beads_export.resolve(),
        session_state.resolve(),
        spec_freeze_decision.resolve(),
    ):
        if output == protected or protected in output.parents or output in protected.parents:
            raise ValueError("output-dir overlaps a live or frozen input")


def resolve_revision(execution_root: Path, value: Path) -> Path:
    if value.name == REVISION_ID and len(value.parts) == 1:
        revision = (
            execution_root
            / "versions"
            / "v2-two-track"
            / REVISION_ID
        ).resolve()
    else:
        revision = value.resolve()
    if revision.name != REVISION_ID or not revision.is_dir():
        raise ValueError("revision must select the exact frozen rc5 revision")
    return revision


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    execution_root = args.execution_root.resolve()
    revision = resolve_revision(execution_root, args.revision)
    beads_export = args.beads_export.absolute()
    session_state_path = args.session_state.absolute()
    output_dir = args.output_dir.resolve()
    spec_freeze_decision_path = args.spec_freeze_decision.absolute()
    migration_id = args.migration_id
    if not _MIGRATION_ID.fullmatch(migration_id):
        raise ValueError("migration-id has an invalid format")
    _assert_safe_output(
        output_dir,
        execution_root,
        revision,
        beads_export,
        session_state_path,
        spec_freeze_decision_path,
    )
    _verify_revision(revision)
    transaction = load_object(revision / "MIGRATION_TRANSACTION.json")
    captures = capture_before_images(
        execution_root=execution_root,
        beads_export=beads_export,
        session_state=session_state_path,
        transaction=transaction,
        spec_freeze_decision=spec_freeze_decision_path,
    )
    live_rows = captures["beads_projection"].value
    if not isinstance(live_rows, list):
        raise ValueError("captured Beads export did not produce issue rows")
    decision_value = captures[SPEC_FREEZE_SOURCE_ID].value
    if not isinstance(decision_value, dict):
        raise ValueError("captured RC5 SPEC_FREEZE decision is not a JSON object")
    spec_freeze_decision, spec_freeze_sha256 = extract_spec_freeze(
        decision_value,
        payload=captures[SPEC_FREEZE_SOURCE_ID].payload,
    )
    session_state = captures[SESSION_SOURCE_ID].value
    draft_status = load_object(revision / "DRAFT_STATUS.json")
    frozen_queue = load_object(revision / "RUN_QUEUE.json")
    beads_migration = load_object(revision / "BEADS_MIGRATION.json")
    stores = _store_contracts(transaction)
    rollback_operations = _rollback_operations(captures, stores)
    before_images = _before_image_rows(captures, stores, rollback_operations)

    active = derive_active_status(
        draft_status,
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    run_queue = derive_run_queue(
        frozen_queue,
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    beads_resolution = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=migration_id,
        spec_freeze_sha256=spec_freeze_sha256,
    )
    session_projection = derive_session_projection(
        session_state,
        active,
        run_queue,
        migration_id=migration_id,
    )
    validate_zero_authority(active, run_queue, beads_resolution, session_projection)

    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.stage-", dir=output_dir.parent)
    )
    installed = False
    try:
        for store_id in STORE_IDS:
            source = captures[store_id]
            if source.presence == "absent":
                continue
            assert source.payload is not None
            assert source.retained_mode is not None
            _write_stage_bytes(
                temporary_dir / BEFORE_IMAGE_FILES[store_id],
                source.payload,
                mode=source.retained_mode,
            )
        session_source = captures[SESSION_SOURCE_ID]
        assert session_source.payload is not None
        assert session_source.retained_mode is not None
        _write_stage_bytes(
            temporary_dir / SESSION_SOURCE_FILE,
            session_source.payload,
            mode=session_source.retained_mode,
        )
        decision_source = captures[SPEC_FREEZE_SOURCE_ID]
        assert decision_source.payload is not None
        assert decision_source.retained_mode is not None
        _write_stage_bytes(
            temporary_dir / SPEC_FREEZE_DECISION_FILE,
            decision_source.payload,
            mode=decision_source.retained_mode,
        )
        rollback_document = {
            "migration_id": migration_id,
            "native_revision_bound": False,
            "operations": rollback_operations,
            "program_id": PROGRAM_ID,
            "revision_id": REVISION_ID,
            "schema_version": "bb.rl.phase5.rollback_descriptors.v1",
        }
        _write_stage(
            temporary_dir / "ROLLBACK_DESCRIPTORS.json", rollback_document
        )
        before_document = {
            "images": before_images,
            "migration_id": migration_id,
            "native_revision_bound": False,
            "program_id": PROGRAM_ID,
            "revision_id": REVISION_ID,
            "revision_type": "file_snapshot_sha256",
            "schema_version": "bb.rl.phase5.before_images.v2",
        }
        _write_stage(temporary_dir / "BEFORE_IMAGES.json", before_document)
        _write_stage(temporary_dir / "BEADS_RESOLUTION.json", beads_resolution)
        _write_stage(temporary_dir / "SESSION_PROJECTION.json", session_projection)
        _write_stage(temporary_dir / "PREPARED_ACTIVE_STATUS.json", active)
        _write_stage(temporary_dir / "PREPARED_RUN_QUEUE.json", run_queue)

        revision_prefix = f"versions/v2-two-track/{REVISION_ID}"
        selector = build_root_selector(
            revision_id=REVISION_ID,
            program_id=PROGRAM_ID,
            generation=active["generation"],
            event_cursor=active["event_cursor"],
            migration_id=migration_id,
            artifact_manifest_ref=file_ref(
                revision / "ARTIFACT_MANIFEST.json",
                f"{revision_prefix}/ARTIFACT_MANIFEST.json",
            ),
            active_status_ref=file_ref(
                temporary_dir / "PREPARED_ACTIVE_STATUS.json",
                f"migrations/{migration_id}/PREPARED_ACTIVE_STATUS.json",
            ),
            evidence_index_ref=file_ref(
                revision / "EVIDENCE_INDEX.json",
                f"{revision_prefix}/EVIDENCE_INDEX.json",
            ),
            authority_policy_ref=file_ref(
                revision / "AUTHORITY_POLICY.json",
                f"{revision_prefix}/AUTHORITY_POLICY.json",
            ),
            run_queue_ref=file_ref(
                temporary_dir / "PREPARED_RUN_QUEUE.json",
                f"migrations/{migration_id}/PREPARED_RUN_QUEUE.json",
            ),
        )
        validate_zero_authority(selector)
        _write_stage(temporary_dir / "PREPARED_ROOT_SELECTOR.json", selector)

        existing_events = captures["v2_event_log"].value
        if not isinstance(existing_events, list):
            raise ValueError("captured live EVENT_CHAIN must be a JSON list")
        before_head = verify_event_chain(existing_events)
        lineage_event = build_event(
            "V1_LINEAGE_IMPORTED",
            {
                "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
                "before_images_sha256": sha256_file(
                    temporary_dir / "BEFORE_IMAGES.json"
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
        activation_event = build_event(
            "V2_ACTIVATED",
            {
                "beads_resolution_ref": file_ref(
                    temporary_dir / "BEADS_RESOLUTION.json", "BEADS_RESOLUTION.json"
                ),
                "migration_id": migration_id,
                "prepared_active_status_ref": file_ref(
                    temporary_dir / "PREPARED_ACTIVE_STATUS.json",
                    "PREPARED_ACTIVE_STATUS.json",
                ),
                "prepared_root_selector_ref": file_ref(
                    temporary_dir / "PREPARED_ROOT_SELECTOR.json",
                    "PREPARED_ROOT_SELECTOR.json",
                ),
                "prepared_run_queue_ref": file_ref(
                    temporary_dir / "PREPARED_RUN_QUEUE.json",
                    "PREPARED_RUN_QUEUE.json",
                ),
                "program_id": PROGRAM_ID,
                "session_projection_ref": file_ref(
                    temporary_dir / "SESSION_PROJECTION.json",
                    "SESSION_PROJECTION.json",
                ),
                "target_execution_allowed": False,
            },
            lineage_event["event_sha256"],
        )
        append_payload = [lineage_event, activation_event]
        staged_events = [*existing_events, *append_payload]
        prepared_head = verify_event_chain(staged_events)
        _write_stage(
            temporary_dir / "EVENT_APPEND_PAYLOAD.json", append_payload
        )
        _write_stage(temporary_dir / "EVENT_CHAIN.json", staged_events)
        event_metadata = {
            "after_event_count": len(staged_events),
            "after_head_sha256": prepared_head,
            "after_image_ref": file_ref(
                temporary_dir / "EVENT_CHAIN.json", "EVENT_CHAIN.json"
            ),
            "append_event_count": len(append_payload),
            "append_payload_ref": file_ref(
                temporary_dir / "EVENT_APPEND_PAYLOAD.json",
                "EVENT_APPEND_PAYLOAD.json",
            ),
            "before_event_count": len(existing_events),
            "before_head_sha256": before_head,
            "committed_event_sha256s": [
                event["event_sha256"] for event in append_payload
            ],
            "migration_id": migration_id,
            "program_id": PROGRAM_ID,
            "revision_id": REVISION_ID,
            "schema_version": "bb.rl.phase5.event_append_metadata.v1",
        }
        _write_stage(
            temporary_dir / "EVENT_APPEND_METADATA.json", event_metadata
        )

        staged_paths = {
            "v2_event_log": temporary_dir / "EVENT_CHAIN.json",
            "beads_projection": temporary_dir / "BEADS_RESOLUTION.json",
            "root_active_selector": temporary_dir / "PREPARED_ROOT_SELECTOR.json",
        }
        after_images = [
            _staged_image_row(
                store_id=store_id,
                path=staged_paths[store_id],
                logical_path=staged_paths[store_id].name,
                store=stores[store_id],
                rollback_operation=rollback_operations[index],
                rollback_operation_index=index,
            )
            for index, store_id in enumerate(STORE_IDS)
        ]
        after_document = {
            "images": after_images,
            "migration_id": migration_id,
            "native_revision_bound": False,
            "program_id": PROGRAM_ID,
            "revision_id": REVISION_ID,
            "revision_type": "file_snapshot_sha256",
            "schema_version": "bb.rl.phase5.after_images.v2",
        }
        _write_stage(temporary_dir / "AFTER_IMAGES.json", after_document)

        prepared_validation = _prepared_validation(
            staging=temporary_dir,
            migration_id=migration_id,
            spec_freeze_sha256=spec_freeze_sha256,
        )
        gate_exercise = exercise_temporary_gate(migration_id)
        replay_script = Path(__file__).with_name(
            "replay_phase5_v2_prepared_handoff.py"
        ).resolve()
        with tempfile.TemporaryDirectory(
            prefix="phase5-v2-replay-output-", dir=output_dir.parent
        ) as replay_output_root, tempfile.TemporaryDirectory(
            prefix="phase5-v2-replay-cwd-"
        ) as empty_cwd:
            replay_report_path = (
                Path(replay_output_root) / "FRESH_WORKER_PREPARATION_REPORT.json"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(replay_script),
                    "--revision",
                    str(revision),
                    "--bundle",
                    str(temporary_dir),
                    "--output-root",
                    replay_output_root,
                    "--report",
                    str(replay_report_path),
                ],
                cwd=empty_cwd,
                env=_allowlisted_environment(),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ValueError(
                    "prepared image replay failed: "
                    f"exit={result.returncode}; stderr={result.stderr.strip()}"
                )
            fresh_report = load_object(replay_report_path)
            receipt = fresh_report.get("frozen_contract_receipt")
            workers = fresh_report.get("workers")
            if (
                fresh_report.get("schema_version")
                != "bb.rl.phase5.prepared_image_replay_report.v1"
                or fresh_report.get("replay_mode")
                != "non_conformance_preparation_replay"
                or fresh_report.get("frozen_contract_passed") is not False
                or not isinstance(receipt, dict)
                or receipt.get("result") != "non_conformance_preparation_replay"
                or receipt.get("worker_count") != 2
                or not isinstance(workers, list)
                or len(workers) != 2
            ):
                raise ValueError(
                    "prepared image replay must truthfully report frozen-contract "
                    "non-conformance from two workers"
                )
            _write_stage(
                temporary_dir / "FRESH_WORKER_PREPARATION_REPORT.json",
                fresh_report,
            )

        bundle_input_names = tuple(
            dict.fromkeys(
                PREPARED_INPUT_FILES
                + PREPARATION_SUPPORT_FILES
                + ("FRESH_WORKER_PREPARATION_REPORT.json",)
            )
        )
        report = {
            "after_images": after_images,
            "after_images_sha256": sha256_file(temporary_dir / "AFTER_IMAGES.json"),
            "artifact_manifest_sha256": ARTIFACT_MANIFEST_SHA256,
            "authority_decision_sha256": spec_freeze_sha256,
            "before_images": before_images,
            "before_images_sha256": sha256_file(temporary_dir / "BEFORE_IMAGES.json"),
            "bundle_artifact_hashes": {
                name: sha256_file(temporary_dir / name)
                for name in bundle_input_names
                if (temporary_dir / name).is_file()
            },
            "commit_results": [],
            "consumer_barrier_acquired": False,
            "consumer_barrier_feasibility": {
                "affected_consumer_classes": [
                    "raw_root_selector_readers",
                    "beads_dolt_sql_readers",
                    "omp_cached_rpc_todo_readers",
                ],
                "feasible": False,
                "live_native_binding_available": False,
                "required_remediation": (
                    "add native fail-closed bindings to every affected consumer "
                    "class and independently verify them"
                ),
                "status": "infeasible_without_native_consumer_bindings",
                "temporary_gate_is_native_evidence": False,
            },
            "consumer_barrier_released": False,
            "cutover_ready": False,
            "event_append_metadata_ref": file_ref(
                temporary_dir / "EVENT_APPEND_METADATA.json",
                "EVENT_APPEND_METADATA.json",
            ),
            "event_append_payload_ref": file_ref(
                temporary_dir / "EVENT_APPEND_PAYLOAD.json",
                "EVENT_APPEND_PAYLOAD.json",
            ),
            "fresh_worker_preparation_report_sha256": sha256_file(
                temporary_dir / "FRESH_WORKER_PREPARATION_REPORT.json"
            ),
            "frozen_handoff_contract_passed": False,
            "gate_exercise": gate_exercise,
            "migration_id": migration_id,
            "migration_transaction_sha256": MIGRATION_TRANSACTION_SHA256,
            "native_revision_binding": {
                "beads_dolt_native_revision": None,
                "beads_dolt_revision_bound": False,
                "native_revision_bound": False,
                "omp_state_revision_bound": False,
                "omp_state_native_revision": None,
                "revision_type": "file_snapshot_sha256",
            },
            "post_commit_hashes": [],
            "prepared_only": True,
            "prepared_validation": prepared_validation,
            "prepared_validation_sha256": sha256_bytes(
                canonical_bytes(prepared_validation)
            ),
            "program_id": PROGRAM_ID,
            "released_lease": False,
            "revision_id": REVISION_ID,
            "rollback_descriptors_ref": file_ref(
                temporary_dir / "ROLLBACK_DESCRIPTORS.json",
                "ROLLBACK_DESCRIPTORS.json",
            ),
            "schema_version": "bb.rl.phase5.migration_preparation_report.v3",
            "spec_freeze_decision": spec_freeze_decision,
            "spec_freeze_decision_sha256": spec_freeze_sha256,
            "target_execution_allowed": False,
        }
        _write_stage(temporary_dir / "MIGRATION_PREPARATION_REPORT.json", report)
        actual_files = sorted(
            path.name for path in temporary_dir.iterdir() if path.is_file()
        )
        expected_files = list(BUNDLE_FILES)
        if captures["v2_event_log"].presence == "absent":
            expected_files.remove(BEFORE_IMAGE_FILES["v2_event_log"])
        if actual_files != sorted(expected_files):
            raise ValueError("prepared bundle file set is not exact")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(temporary_dir, directory_flags)
        try:
            os.fchmod(descriptor, 0o555)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _recheck_captured_sources(captures)
        _atomic_publish_directory_noreplace(temporary_dir, output_dir)
        parent_descriptor = os.open(output_dir.parent, directory_flags)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        installed = True
        print(json.dumps(report, sort_keys=True))
        return report
    finally:
        if not installed and temporary_dir.exists():
            os.chmod(temporary_dir, 0o700)
            for path in temporary_dir.iterdir():
                if path.is_file():
                    os.chmod(path, 0o600)
                    path.unlink()
            temporary_dir.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--beads-export", type=Path, required=True)
    parser.add_argument("--session-state", type=Path, required=True)
    parser.add_argument("--spec-freeze-decision", type=Path, required=True)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    prepare(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
