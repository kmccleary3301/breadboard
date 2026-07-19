from __future__ import annotations

import array
import ctypes
import errno
import fcntl
import hashlib
import hmac
import json
import marshal
import os
import re
import resource
import secrets
import select
import socket
import stat
import sys
import threading
import time
import types
import weakref
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Literal, Mapping, Protocol

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness.materialization import _DirFd
from breadboard.rl.phase5 import g4_source_deletion_helper as _deletion_helper
from breadboard.rl.phase5.rollback_store import (
    DependentOwnershipRecord,
    DependentQuarantineStore,
    ImmutableObjectRef,
)

_DIGEST_PREFIX = "sha256:"
_HMAC_PREFIX = "hmac-sha256:"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MAX_RECORD_BYTES = 4 * 1024 * 1024
_GATE_REF_SCHEMA = "bb.rl.g4.source-deletion-gate-ref.v2"
_REQUEST_SCHEMA = "bb.rl.g4.source-deletion-request.v2"
_PREFLIGHT_SCHEMA = "bb.rl.g4.source-deletion-preflight.v2"
_INTENT_SCHEMA = "bb.rl.g4.source-deletion-intent.v2"
_RECOVERY_SCHEMA = "bb.rl.g4.source-deletion-recovery.v1"
_TRANSITION_SCHEMA = "bb.rl.g4.source-deletion-post-rename-transition.v1"
_TRANSITION_CONSUMED_SCHEMA = (
    "bb.rl.g4.source-deletion-post-rename-transition-consumed.v1"
)
_HELPER_SUCCESS_SCHEMA = "bb.rl.g4.source-deletion-helper-success.v1"
_HELPER_SUCCESS_PREFIX = ".success."
_COMPLETION_SCHEMA = "bb.rl.g4.source-deletion-completion.v2"
_RECEIPT_SCHEMA = "bb.rl.g4.source-deletion-receipt.v2"
_ENTRY_NAME = "owned"
_HELPER_REQUEST_SCHEMA = "bb.rl.g4.source-deletion-helper-request.v2"
_HELPER_RESULT_SCHEMA = "bb.rl.g4.source-deletion-helper-result.v2"
_HELPER_MAX_BYTES = 4096
_HELPER_TIMEOUT_SECONDS = 10.0
_BROKER_CONTROL_SCHEMA = "bb.rl.g4.source-deletion-broker-control.v1"
_THREAD_LOCK_REGISTRY_GUARD = threading.Lock()
_THREAD_CREATION_LOCKS: weakref.WeakValueDictionary[
    tuple[int, int, str], threading.Lock
] = weakref.WeakValueDictionary()
_THREAD_TRANSACTION_LOCKS: weakref.WeakValueDictionary[
    tuple[int, int], threading.Lock
] = weakref.WeakValueDictionary()
_PRIVATE_QUARANTINE_PREFIX = ".bb-g4-private-"

SourceKind = Literal["file", "directory"]
GateKind = Literal[
    "episode_terminal",
    "revocation_published",
    "dependent_quarantined",
    "active_tuple_restored",
    "rerun_recorded",
]
GateOutcome = Literal[
    "closed_cleanup_released",
    "published_active",
    "quarantined_export_blocked",
    "cas_committed",
    "succeeded_recorded",
]

_ALLOWED_GATE_OUTCOME: dict[GateKind, GateOutcome] = {
    "episode_terminal": "closed_cleanup_released",
    "revocation_published": "published_active",
    "dependent_quarantined": "quarantined_export_blocked",
    "active_tuple_restored": "cas_committed",
    "rerun_recorded": "succeeded_recorded",
}

_HELPER_FUNCTION_NAMES = (
    "_canonical",
    "_parse_request",
    "_integer",
    "_unlinkat",
    "_digest",
    "delete_capsule",
)
_HELPER_CONSTANT_NAMES = (
    "_AT_REMOVEDIR",
    "_ENTRY_NAME",
    "_DIGEST_PREFIX",
    "_REQUEST_SCHEMA",
    "_RESULT_SCHEMA",
    "_MAX_BYTES",
    "_DIGEST_RE",
    "_UNLINKAT",
)


def _semantic_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str, bytes}:
        return value
    if type(value) is tuple:
        return ("tuple", tuple(_semantic_value(item) for item in value))
    if type(value) is dict:
        return (
            "dict",
            tuple(
                (str(key), _semantic_value(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ),
        )
    if isinstance(value, types.FunctionType):
        closure = (
            ()
            if value.__closure__ is None
            else tuple(_semantic_value(cell.cell_contents) for cell in value.__closure__)
        )
        return (
            "function",
            id(value),
            id(value.__globals__),
            marshal.dumps(value.__code__),
            _semantic_value(value.__defaults__),
            _semantic_value(value.__kwdefaults__),
            closure,
        )
    if isinstance(value, types.ModuleType):
        return ("module", id(value), value.__name__)
    if isinstance(value, re.Pattern):
        return ("pattern", id(value), value.pattern, value.flags)
    return (
        "object",
        id(value),
        type(value).__module__,
        type(value).__qualname__,
        repr(value),
        repr(getattr(value, "argtypes", None)),
        repr(getattr(value, "restype", None)),
    )


def _helper_semantics_digest() -> bytes:
    functions: list[types.FunctionType] = []
    for name in _HELPER_FUNCTION_NAMES:
        value = vars(_deletion_helper).get(name)
        if not isinstance(value, types.FunctionType):
            raise SourceDeletionError("deletion_helper_capability_invalid")
        if value.__globals__ is not vars(_deletion_helper):
            raise SourceDeletionError("deletion_helper_globals_invalid")
        functions.append(value)
    dependency_names = frozenset(
        name for function in functions for name in function.__code__.co_names
    )
    dependencies: list[object] = []
    for global_name in sorted(dependency_names):
        if global_name not in vars(_deletion_helper):
            continue
        binding = vars(_deletion_helper)[global_name]
        dependencies.append((global_name, _semantic_value(binding)))
        if isinstance(binding, types.ModuleType):
            dependencies.append(
                (
                    global_name + ".__used_attributes__",
                    tuple(
                        (name, _semantic_value(getattr(binding, name)))
                        for name in sorted(dependency_names)
                        if hasattr(binding, name)
                    ),
                )
            )
    constants = tuple(
        (name, _semantic_value(vars(_deletion_helper).get(name)))
        for name in _HELPER_CONSTANT_NAMES
    )
    manifest = (
        tuple((name, _semantic_value(function)) for name, function in zip(
            _HELPER_FUNCTION_NAMES, functions, strict=True
        )),
        constants,
        tuple(dependencies),
    )
    return hashlib.sha256(marshal.dumps(manifest)).digest()


class SourceDeletionError(RuntimeError):
    """A fail-closed source-deletion refusal."""


class SourceDeletionConflict(SourceDeletionError):
    """An immutable operation identity was already bound differently."""


def _require_id(value: str, field: str) -> None:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field}_invalid")


def _require_text(value: str, field: str) -> None:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{field}_invalid")


def _require_digest(value: str, field: str) -> None:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field}_invalid")


def _sha256(value: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value).hexdigest()


def _relative_path(value: str) -> str:
    _require_text(value, "relative_path")
    if "\\" in value:
        raise ValueError("relative_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("relative_path_invalid")
    return value


def _absolute_path(value: str, field: str) -> str:
    _require_text(value, field)
    path = Path(value)
    if not path.is_absolute() or os.fspath(path) != os.fspath(path.resolve(strict=False)):
        raise ValueError(f"{field}_invalid")
    return os.fspath(path)


def _identity_projection(identity: SourceOwnershipIdentity) -> dict[str, object]:
    return {
        "ctime_ns": str(identity.ctime_ns),
        "device": str(identity.device),
        "inode": str(identity.inode),
        "kind": identity.kind,
        "relative_path": identity.relative_path,
        "root_authority_id": identity.root_authority_id,
        "root_path": identity.root_path,
        "sha256": identity.sha256,
        "size_bytes": str(identity.size_bytes),
    }


def _source_label(authority_id: str, relative_path: str) -> str:
    return f"{authority_id}:{relative_path}"


@dataclass(frozen=True, slots=True)
class SourceOwnershipIdentity:
    root_authority_id: str
    root_path: str
    relative_path: str
    device: int
    inode: int
    ctime_ns: int
    size_bytes: int
    sha256: str
    kind: SourceKind

    def __post_init__(self) -> None:
        _require_id(self.root_authority_id, "root_authority_id")
        _absolute_path(self.root_path, "root_path")
        _relative_path(self.relative_path)
        for field_name in ("device", "inode", "ctime_ns", "size_bytes"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name}_invalid")
        _require_digest(self.sha256, "sha256")
        if self.kind not in {"file", "directory"}:
            raise ValueError("source_kind_invalid")

    @property
    def key(self) -> str:
        return _source_label(self.root_authority_id, self.relative_path)


@dataclass(frozen=True, slots=True)
class SourceDeletionGateReceipt:
    path: str
    sha256: str
    schema_version: Literal["bb.rl.g4.source-deletion-gate-ref.v2"] = _GATE_REF_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _GATE_REF_SCHEMA:
            raise ValueError("gate_ref_schema_invalid")
        _absolute_path(self.path, "gate_receipt_path")
        _require_digest(self.sha256, "gate_receipt_sha256")

    def projection(self) -> dict[str, str]:
        return {"path": self.path, "schema_version": self.schema_version, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class SourceDeletionGateReceipts:
    episode_terminal_refs: tuple[SourceDeletionGateReceipt, ...]
    revocation_snapshot_ref: SourceDeletionGateReceipt
    dependent_quarantine_refs: tuple[SourceDeletionGateReceipt, ...]
    active_tuple_history_ref: SourceDeletionGateReceipt
    rerun_receipt_ref: SourceDeletionGateReceipt

    def __post_init__(self) -> None:
        if type(self.episode_terminal_refs) is not tuple or not self.episode_terminal_refs:
            raise ValueError("episode_terminal_receipts_missing")
        if type(self.dependent_quarantine_refs) is not tuple or not self.dependent_quarantine_refs:
            raise ValueError("dependent_quarantine_receipts_missing")
        for error, value in (
            ("revocation_snapshot_receipt_missing", self.revocation_snapshot_ref),
            ("active_tuple_history_receipt_missing", self.active_tuple_history_ref),
            ("rerun_receipt_missing", self.rerun_receipt_ref),
        ):
            if type(value) is not SourceDeletionGateReceipt:
                raise ValueError(error)
        for refs in (self.episode_terminal_refs, self.dependent_quarantine_refs):
            if any(type(ref) is not SourceDeletionGateReceipt for ref in refs):
                raise ValueError("gate_receipt_reference_invalid")
        paths = [ref.path for _, refs in self.groups() for ref in refs]
        if len(set(paths)) != len(paths):
            raise ValueError("gate_receipt_reference_reused")

    def groups(self) -> tuple[tuple[GateKind, tuple[SourceDeletionGateReceipt, ...]], ...]:
        return (
            ("episode_terminal", self.episode_terminal_refs),
            ("revocation_published", (self.revocation_snapshot_ref,)),
            ("dependent_quarantined", self.dependent_quarantine_refs),
            ("active_tuple_restored", (self.active_tuple_history_ref,)),
            ("rerun_recorded", (self.rerun_receipt_ref,)),
        )

    def projection(self) -> dict[str, object]:
        return {
            "active_tuple_history_ref": self.active_tuple_history_ref.projection(),
            "dependent_quarantine_refs": [ref.projection() for ref in self.dependent_quarantine_refs],
            "episode_terminal_refs": [ref.projection() for ref in self.episode_terminal_refs],
            "rerun_receipt_ref": self.rerun_receipt_ref.projection(),
            "revocation_snapshot_ref": self.revocation_snapshot_ref.projection(),
        }


@dataclass(frozen=True, slots=True)
class SourceDeletionRequest:
    operation_id: str
    rollback_id: str
    journal_request_digest: str
    owned_sources: tuple[SourceOwnershipIdentity, ...]
    gates: SourceDeletionGateReceipts

    def __post_init__(self) -> None:
        _require_id(self.operation_id, "operation_id")
        _require_id(self.rollback_id, "rollback_id")
        _require_digest(self.journal_request_digest, "journal_request_digest")
        if type(self.owned_sources) is not tuple or not self.owned_sources:
            raise ValueError("owned_sources_missing")
        if any(type(source) is not SourceOwnershipIdentity for source in self.owned_sources):
            raise ValueError("owned_source_invalid")
        if type(self.gates) is not SourceDeletionGateReceipts:
            raise ValueError("source_deletion_gates_invalid")
        keys = tuple(source.key for source in self.owned_sources)
        if len(set(keys)) != len(keys):
            raise ValueError("owned_source_duplicate")
        physical = tuple((source.device, source.inode) for source in self.owned_sources)
        if len(set(physical)) != len(physical):
            raise ValueError("owned_source_physical_identity_duplicate")

    def projection(self) -> dict[str, object]:
        return {
            "gates": self.gates.projection(),
            "journal_request_digest": self.journal_request_digest,
            "operation_id": self.operation_id,
            "owned_sources": [_identity_projection(source) for source in self.owned_sources],
            "rollback_id": self.rollback_id,
            "schema_version": _REQUEST_SCHEMA,
        }

    @property
    def request_digest(self) -> str:
        return _sha256(canonical_json_bytes(self.projection()))


@dataclass(frozen=True, slots=True)
class VerifiedGateOutcome:
    gate: GateKind
    rollback_id: str
    journal_request_digest: str
    subjects: tuple[str, ...]
    receipt_sha256s: tuple[str, ...]
    terminal_outcome: GateOutcome
    authority_generation: int
    inventory_digest: str
    current: Literal[True]

    def __post_init__(self) -> None:
        if self.gate not in _ALLOWED_GATE_OUTCOME:
            raise ValueError("verified_gate_invalid")
        _require_id(self.rollback_id, "verified_rollback_id")
        _require_digest(self.journal_request_digest, "verified_journal_request_digest")
        if type(self.subjects) is not tuple or not self.subjects:
            raise ValueError("verified_subjects_missing")
        for subject in self.subjects:
            _require_text(subject, "verified_subject")
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("verified_subjects_duplicate")
        if type(self.receipt_sha256s) is not tuple or not self.receipt_sha256s:
            raise ValueError("verified_receipts_missing")
        for digest in self.receipt_sha256s:
            _require_digest(digest, "verified_receipt_sha256")
        if type(self.authority_generation) is not int or self.authority_generation < 1:
            raise ValueError("verified_authority_generation_invalid")
        _require_digest(self.inventory_digest, "verified_inventory_digest")
        if self.current is not True:
            raise ValueError("verified_gate_not_current")


class FinalReceiptLease(Protocol):
    @property
    def outcomes(self) -> Mapping[GateKind, VerifiedGateOutcome]: ...

    def assert_current(self) -> None: ...


class FinalReceiptVerifier(Protocol):
    """Acquires authoritative currentness for every final gate as one fence."""

    def acquire(
        self,
        *,
        receipt_sets: Mapping[
            GateKind,
            tuple[tuple[SourceDeletionGateReceipt, bytes], ...],
        ],
        rollback_id: str,
        journal_request_digest: str,
    ) -> AbstractContextManager[FinalReceiptLease]: ...


@dataclass(frozen=True, slots=True)
class BoundSourceOwnership:
    source_key: str
    record: DependentOwnershipRecord

    def __post_init__(self) -> None:
        _require_text(self.source_key, "bound_source_key")
        if type(self.record) is not DependentOwnershipRecord:
            raise ValueError("bound_ownership_record_invalid")


@dataclass(frozen=True, slots=True)
class VerifiedSourceOwnershipFence:
    rollback_id: str
    journal_request_digest: str
    bindings: tuple[BoundSourceOwnership, ...]
    generation_digest: str

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "ownership_rollback_id")
        _require_digest(self.journal_request_digest, "ownership_journal_request_digest")
        if type(self.bindings) is not tuple or not self.bindings:
            raise ValueError("ownership_bindings_missing")
        if any(type(item) is not BoundSourceOwnership for item in self.bindings):
            raise ValueError("ownership_binding_invalid")
        if len({item.source_key for item in self.bindings}) != len(self.bindings):
            raise ValueError("ownership_binding_duplicate")
        _require_digest(self.generation_digest, "ownership_generation_digest")
        expected = _sha256(
            canonical_json_bytes(
                {
                    "bindings": [
                        {"record_digest": item.record.digest, "source_key": item.source_key}
                        for item in self.bindings
                    ],
                    "journal_request_digest": self.journal_request_digest,
                    "rollback_id": self.rollback_id,
                }
            )
        )
        if self.generation_digest != expected:
            raise ValueError("ownership_generation_digest_mismatch")


class SourceOwnershipLease(Protocol):
    @property
    def fence(self) -> VerifiedSourceOwnershipFence: ...

    def assert_current(self) -> None: ...


class SourceOwnershipAuthority(Protocol):
    def acquire(
        self,
        *,
        rollback_id: str,
        journal_request_digest: str,
        sources: tuple[SourceOwnershipIdentity, ...],
    ) -> AbstractContextManager[SourceOwnershipLease]: ...


@dataclass(slots=True)
class _RollbackStoreOwnershipLease:
    fence: VerifiedSourceOwnershipFence
    _active: bool = True

    def assert_current(self) -> None:
        if self._active is not True:
            raise SourceDeletionError("source_ownership_fence_released")


class RollbackStoreSourceOwnershipAuthority:
    """Binds source identities to current signed rollback-store ownership records."""

    def __init__(
        self,
        store: DependentQuarantineStore,
        *,
        object_refs_by_rollback: Mapping[str, Mapping[str, ImmutableObjectRef]],
    ) -> None:
        if not object_refs_by_rollback:
            raise ValueError("source_ownership_bindings_missing")
        bindings: dict[str, dict[str, ImmutableObjectRef]] = {}
        for rollback_id, values in object_refs_by_rollback.items():
            _require_id(rollback_id, "source_ownership_rollback_id")
            if not values:
                raise ValueError("source_ownership_rollback_bindings_missing")
            current: dict[str, ImmutableObjectRef] = {}
            identities: set[str] = set()
            for source_key, object_ref in values.items():
                _require_text(source_key, "source_ownership_source_key")
                if type(object_ref) is not ImmutableObjectRef:
                    raise ValueError("source_ownership_object_ref_invalid")
                if object_ref.identity_digest in identities:
                    raise ValueError("source_ownership_object_ref_duplicate")
                identities.add(object_ref.identity_digest)
                current[source_key] = object_ref
            bindings[rollback_id] = current
        self._store = store
        self._bindings = bindings

    @contextmanager
    def acquire(
        self,
        *,
        rollback_id: str,
        journal_request_digest: str,
        sources: tuple[SourceOwnershipIdentity, ...],
    ) -> Iterator[SourceOwnershipLease]:
        configured = self._bindings.get(rollback_id)
        if configured is None or set(configured) != {source.key for source in sources}:
            raise SourceDeletionError("source_ownership_authoritative_inventory_mismatch")
        with self._store.read_fence() as records:
            by_identity = {
                record.ownership.object_ref.identity_digest: record for record in records
            }
            affected = {
                record.ownership.object_ref.identity_digest
                for record in records
                if any(
                    receipt.rollback_id == rollback_id
                    and receipt.cause_digest == journal_request_digest
                    for receipt in record.quarantine_receipts
                )
            }
            configured_identities = {
                object_ref.identity_digest for object_ref in configured.values()
            }
            if configured_identities != affected:
                raise SourceDeletionError(
                    "source_ownership_authoritative_inventory_mismatch"
                )
            bindings: list[BoundSourceOwnership] = []
            for source in sources:
                object_ref = configured[source.key]
                record = by_identity.get(object_ref.identity_digest)
                if record is None or record.ownership.object_ref != object_ref:
                    raise SourceDeletionError(
                        f"source_ownership_record_missing:{source.key}"
                    )
                bindings.append(BoundSourceOwnership(source.key, record))
            generation_digest = _sha256(
                canonical_json_bytes(
                    {
                        "bindings": [
                            {
                                "record_digest": binding.record.digest,
                                "source_key": binding.source_key,
                            }
                            for binding in bindings
                        ],
                        "journal_request_digest": journal_request_digest,
                        "rollback_id": rollback_id,
                    }
                )
            )
            fence = VerifiedSourceOwnershipFence(
                rollback_id,
                journal_request_digest,
                tuple(bindings),
                generation_digest,
            )
            lease = _RollbackStoreOwnershipLease(fence)
            try:
                yield lease
            finally:
                lease._active = False

@dataclass(frozen=True, slots=True)
class SourceAbsenceProof:
    root_authority_id: str
    root_path: str
    relative_path: str
    prior_device: int
    prior_inode: int
    prior_ctime_ns: int
    prior_size_bytes: int
    prior_sha256: str
    prior_kind: SourceKind
    absence_anchor_relative_path: str
    anchor_device: int
    anchor_inode: int
    observed_at: str

    def __post_init__(self) -> None:
        _require_id(self.root_authority_id, "absence_root_authority_id")
        _absolute_path(self.root_path, "absence_root_path")
        _relative_path(self.relative_path)
        if self.absence_anchor_relative_path:
            _relative_path(self.absence_anchor_relative_path)
        for field_name in (
            "prior_device",
            "prior_inode",
            "prior_ctime_ns",
            "prior_size_bytes",
            "anchor_device",
            "anchor_inode",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name}_invalid")
        _require_digest(self.prior_sha256, "absence_prior_sha256")
        if self.prior_kind not in {"file", "directory"}:
            raise ValueError("absence_prior_kind_invalid")
        _parse_timestamp(self.observed_at)

    @property
    def key(self) -> str:
        return _source_label(self.root_authority_id, self.relative_path)

    def projection(self) -> dict[str, object]:
        return {
            "absence_anchor_relative_path": self.absence_anchor_relative_path,
            "anchor_device": str(self.anchor_device),
            "anchor_inode": str(self.anchor_inode),
            "observed_at": self.observed_at,
            "prior_ctime_ns": str(self.prior_ctime_ns),
            "prior_device": str(self.prior_device),
            "prior_inode": str(self.prior_inode),
            "prior_kind": self.prior_kind,
            "prior_sha256": self.prior_sha256,
            "prior_size_bytes": str(self.prior_size_bytes),
            "relative_path": self.relative_path,
            "root_authority_id": self.root_authority_id,
            "root_path": self.root_path,
        }


@dataclass(frozen=True, slots=True)
class SourceDeletionReceipt:
    operation_id: str
    request_digest: str
    deleted: tuple[str, ...]
    already_absent: tuple[str, ...]
    absence_proofs: tuple[SourceAbsenceProof, ...]
    completed_at: str
    completion_digest: str
    authority_signature: str

    def __post_init__(self) -> None:
        _require_id(self.operation_id, "receipt_operation_id")
        _require_digest(self.request_digest, "receipt_request_digest")
        for field_name in ("deleted", "already_absent"):
            values = getattr(self, field_name)
            if type(values) is not tuple or any(type(value) is not str or not value for value in values):
                raise ValueError(f"receipt_{field_name}_invalid")
            if len(set(values)) != len(values):
                raise ValueError(f"receipt_{field_name}_duplicate")
        if set(self.deleted) & set(self.already_absent):
            raise ValueError("receipt_disposition_overlap")
        if type(self.absence_proofs) is not tuple or not self.absence_proofs:
            raise ValueError("receipt_absence_proofs_missing")
        if any(type(proof) is not SourceAbsenceProof for proof in self.absence_proofs):
            raise ValueError("receipt_absence_proof_invalid")
        if len({proof.key for proof in self.absence_proofs}) != len(self.absence_proofs):
            raise ValueError("receipt_absence_proof_duplicate")
        _parse_timestamp(self.completed_at)
        _require_digest(self.completion_digest, "receipt_completion_digest")
        if (
            type(self.authority_signature) is not str
            or not self.authority_signature.startswith(_HMAC_PREFIX)
            or len(self.authority_signature) != len(_HMAC_PREFIX) + 64
        ):
            raise ValueError("receipt_authority_signature_invalid")

    def unsigned_projection(self) -> dict[str, object]:
        return {
            "absence_proofs": [proof.projection() for proof in self.absence_proofs],
            "already_absent": list(self.already_absent),
            "completed_at": self.completed_at,
            "completion_digest": self.completion_digest,
            "deleted": list(self.deleted),
            "operation_id": self.operation_id,
            "request_digest": self.request_digest,
            "schema_version": _RECEIPT_SCHEMA,
        }

    def projection(self) -> dict[str, object]:
        return {**self.unsigned_projection(), "authority_signature": self.authority_signature}


@dataclass(frozen=True, slots=True)
class _RootAuthority:
    path: str
    identity: tuple[int, int, int, int, int]



@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    device: int
    inode: int
    ctime_ns: int
    mode: int
    uid: int
    gid: int
    link_count: int
    size_bytes: int
    atime_ns: int
    mtime_ns: int
    kind: SourceKind
    sha256: str

    def projection(self) -> dict[str, str]:
        return {
            "atime_ns": str(self.atime_ns),
            "ctime_ns": str(self.ctime_ns),
            "device": str(self.device),
            "gid": str(self.gid),
            "inode": str(self.inode),
            "kind": self.kind,
            "link_count": str(self.link_count),
            "mode": str(self.mode),
            "mtime_ns": str(self.mtime_ns),
            "sha256": self.sha256,
            "size_bytes": str(self.size_bytes),
            "uid": str(self.uid),
        }

    def stable_tuple(self) -> tuple[int, ...]:
        return (
            self.device,
            self.inode,
            self.mode,
            self.uid,
            self.gid,
            self.link_count,
            self.size_bytes,
            self.atime_ns,
            self.mtime_ns,
        )


@dataclass(frozen=True, slots=True)
class _DeletionIntent:
    source: SourceOwnershipIdentity
    quarantine_relative_path: str
    raw: bytes
    pre_rename_snapshot: _SourceSnapshot | None = None


@dataclass(frozen=True, slots=True)
class _PostRenameTransition:
    source_key: str
    private_identity: tuple[int, int]
    capsule_identity: tuple[int, int]
    owned_snapshot: _SourceSnapshot
    raw: bytes


@dataclass(frozen=True, slots=True)
class _HelperSuccess:
    source_key: str
    transition_digest: str
    name: str
    raw: bytes


@dataclass(frozen=True, slots=True)
class _AbsenceAnchor:
    relative_path: str
    metadata: os.stat_result


@dataclass(frozen=True, slots=True)
class _HelperExpectation:
    device: int
    inode: int
    ctime_ns: int
    atime_ns: int
    mode: int
    uid: int
    gid: int
    link_count: int
    mtime_ns: int
    size_bytes: int
    sha256: str
    kind: SourceKind

    def request_bytes(
        self,
        success_record_name: str,
        success_record: Mapping[str, object],
    ) -> bytes:
        return canonical_json_bytes(
            {
                "ctime_ns": str(self.ctime_ns),
                "device": str(self.device),
                "gid": str(self.gid),
                "inode": str(self.inode),
                "kind": self.kind,
                "link_count": str(self.link_count),
                "mode": str(self.mode),
                "mtime_ns": str(self.mtime_ns),
                "schema_version": _HELPER_REQUEST_SCHEMA,
                "sha256": self.sha256,
                "size_bytes": str(self.size_bytes),
                "success_record": success_record,
                "success_record_name": success_record_name,
                "uid": str(self.uid),
            }
        )


class _DeletionBroker:
    def __init__(self, capability_digest: bytes) -> None:
        if threading.active_count() != 1:
            raise SourceDeletionError("deletion_broker_requires_single_threaded_construction")
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        pid = os.fork()
        if pid == 0:
            parent.close()
            self._serve(child, capability_digest)
        child.close()
        parent.settimeout(_HELPER_TIMEOUT_SECONDS)
        self._socket = parent
        self._pid = pid
        self._lock = threading.Lock()
        self._shutdown_request = canonical_json_bytes(
            {
                "action": "shutdown",
                "capability_digest": capability_digest.hex(),
                "schema_version": _BROKER_CONTROL_SCHEMA,
            }
        )
        self._shutdown_ack = canonical_json_bytes(
            {
                "schema_version": _BROKER_CONTROL_SCHEMA,
                "status": "shutdown",
            }
        )

    @staticmethod
    def _serve(channel: socket.socket, capability_digest: bytes) -> None:
        exit_code = 1
        try:
            shutdown_request = canonical_json_bytes(
                {
                    "action": "shutdown",
                    "capability_digest": capability_digest.hex(),
                    "schema_version": _BROKER_CONTROL_SCHEMA,
                }
            )
            shutdown_ack = canonical_json_bytes(
                {
                    "schema_version": _BROKER_CONTROL_SCHEMA,
                    "status": "shutdown",
                }
            )
            broker_fd = channel.detach()
            if broker_fd != 3:
                os.dup2(broker_fd, 3, inheritable=False)
                os.close(broker_fd)
            channel = socket.socket(fileno=3)
            soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
            finite = [
                int(limit)
                for limit in (soft_limit, hard_limit)
                if limit != resource.RLIM_INFINITY
            ]
            os.closerange(4, max(4, int(os.sysconf("SC_OPEN_MAX")), *finite))
            for descriptor in (0, 1, 2):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            while True:
                raw, ancillary, _, _ = channel.recvmsg(
                    _HELPER_MAX_BYTES,
                    socket.CMSG_SPACE(array.array("i").itemsize),
                )
                if not raw:
                    exit_code = 0
                    break
                if raw == shutdown_request and not ancillary:
                    channel.send(shutdown_ack)
                    exit_code = 0
                    break
                passed = array.array("i")
                for level, kind, data in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        passed.frombytes(data[: passed.itemsize])
                if len(passed) != 1:
                    raise RuntimeError("broker_capsule_fd_invalid")
                capsule_fd = passed[0]
                start_read, start_write = os.pipe()
                result_read, result_write = os.pipe()
                pid = os.fork()
                if pid == 0:
                    channel.close()
                    os.close(start_write)
                    os.close(result_read)
                    SourceDeletionGuard._forked_helper_child(
                        capsule_fd,
                        start_read,
                        result_write,
                        raw,
                        capability_digest,
                    )
                os.close(capsule_fd)
                os.close(start_read)
                os.close(result_write)
                channel.send(b"R")
                if channel.recv(1) != b"S":
                    SourceDeletionGuard._kill_and_reap(pid)
                    raise RuntimeError("broker_start_protocol_invalid")
                os.write(start_write, b"\x01")
                os.close(start_write)
                child_raw, child_status = SourceDeletionGuard._read_fork_result(


                    pid,
                    result_read,
                )
                os.close(result_read)
                channel.send(
                    child_status.to_bytes(4, "big", signed=False) + child_raw
                )
        except BaseException:
            pass
        finally:
            try:
                channel.close()
            except BaseException:
                pass
            os._exit(exit_code)

    def prepare(self, capsule_fd: int, request_raw: bytes) -> None:
        self._lock.acquire()
        try:
            rights = array.array("i", [capsule_fd])
            self._socket.sendmsg(
                [request_raw],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)],
            )
            if self._socket.recv(1) != b"R":
                raise SourceDeletionError("deletion_broker_prepare_failed")
        except BaseException:
            self._lock.release()
            raise

    def start(self) -> tuple[bytes, int]:
        try:
            self._socket.send(b"S")
            packet = self._socket.recv(_HELPER_MAX_BYTES + 4)
            if len(packet) < 4:
                raise SourceDeletionError("deletion_broker_result_invalid")
            return packet[4:], int.from_bytes(packet[:4], "big", signed=False)
        finally:
            self._lock.release()

    def abort(self) -> None:
        if self._lock.locked():
            self._lock.release()

    def close(self) -> None:
        channel = getattr(self, "_socket", None)
        if channel is None:
            return
        self._socket = None
        pid = getattr(self, "_pid", -1)
        try:
            try:
                channel.send(self._shutdown_request)
                if channel.recv(_HELPER_MAX_BYTES) != self._shutdown_ack:
                    raise SourceDeletionError("deletion_broker_shutdown_invalid")
            except OSError:
                pass
        finally:
            channel.close()
        if pid > 0:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    return
                if waited == pid:
                    return
                time.sleep(0.01)
            SourceDeletionGuard._kill_and_reap(pid)

    def __del__(self) -> None:
        self.close()


def _parse_timestamp(value: str) -> datetime:
    _require_text(value, "timestamp")
    if not value.endswith("Z"):
        raise ValueError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_invalid")
    return parsed


class SourceDeletionGuard:
    """Final receipt-gated deletion for an exact, fenced source inventory."""

    def __init__(
        self,
        *,
        receipt_root: Path,
        receipt_authority_key: bytes,
        root_authorities: Mapping[str, Path],
        final_receipt_verifier: FinalReceiptVerifier,
        source_ownership_authority: SourceOwnershipAuthority,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(receipt_authority_key) is not bytes or len(receipt_authority_key) < 32:
            raise ValueError("receipt_authority_key_invalid")
        if not root_authorities:
            raise ValueError("root_authorities_missing")
        self._receipt_key = receipt_authority_key
        self._verifier = final_receipt_verifier
        self._ownership_authority = source_ownership_authority
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._receipt_root = Path(_absolute_path(os.fspath(receipt_root), "receipt_root"))
        self._receipt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        receipt_metadata = os.stat(self._receipt_root, follow_symlinks=False)
        if (
            not stat.S_ISDIR(receipt_metadata.st_mode)
            or stat.S_IMODE(receipt_metadata.st_mode) != 0o700
            or receipt_metadata.st_uid != os.geteuid()
            or receipt_metadata.st_gid != os.getegid()
        ):
            raise ValueError("receipt_root_authority_invalid")
        self._receipt_identity = self._directory_identity(receipt_metadata)
        authorities: dict[str, _RootAuthority] = {}
        physical_roots: set[tuple[int, int]] = set()
        for authority_id, path_value in root_authorities.items():
            _require_id(authority_id, "root_authority_id")
            path = _absolute_path(os.fspath(path_value), "root_authority_path")
            descriptor = self._open_directory(path)
            try:
                metadata = os.fstat(descriptor)
                identity = self._directory_identity(metadata)
            finally:
                os.close(descriptor)
            physical = (metadata.st_dev, metadata.st_ino)
            if physical in physical_roots:
                raise ValueError("root_authority_physical_alias")
            physical_roots.add(physical)
            for prior in authorities.values():
                if self._paths_overlap(path, prior.path):
                    raise ValueError("root_authorities_overlap")
            if self._paths_overlap(path, os.fspath(self._receipt_root)):
                raise ValueError("receipt_and_source_roots_overlap")
            authorities[authority_id] = _RootAuthority(path=path, identity=identity)
        self._authorities = authorities
        self._helper_capability_digest = _helper_semantics_digest()
        self._broker = _DeletionBroker(self._helper_capability_digest)

    def _assert_helper_capability(self) -> None:
        try:
            observed = _helper_semantics_digest()
        except SourceDeletionError as exc:
            raise SourceDeletionError("deletion_helper_capability_changed") from exc
        if not hmac.compare_digest(self._helper_capability_digest, observed):
            raise SourceDeletionError("deletion_helper_capability_changed")

    def delete(self, request: SourceDeletionRequest) -> SourceDeletionReceipt:
        if type(request) is not SourceDeletionRequest:
            raise TypeError("source deletion requires SourceDeletionRequest")
        self._assert_helper_capability()
        request_bytes = canonical_json_bytes(request.projection())
        request_digest = _sha256(request_bytes)
        operation_key = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
        receipt_owner = self._open_receipt_root()
        lock_fd = -1
        creation_lock: threading.Lock | None = None
        creation_lock_held = False
        root_lock_held = False
        thread_lock: threading.Lock | None = None
        try:
            creation_key = (
                self._receipt_identity[0],
                self._receipt_identity[1],
                operation_key,
            )
            with _THREAD_LOCK_REGISTRY_GUARD:
                creation_lock = _THREAD_CREATION_LOCKS.get(creation_key)
                if creation_lock is None:
                    creation_lock = threading.Lock()
                    _THREAD_CREATION_LOCKS[creation_key] = creation_lock
            creation_lock.acquire()
            creation_lock_held = True
            fcntl.flock(receipt_owner.fd, fcntl.LOCK_EX)
            root_lock_held = True
            creation_lock_held = True
            lock_fd = receipt_owner.open_file(
                f"{operation_key}.lock",
                os.O_RDWR | os.O_CREAT,
                0o600,
            )
            lock_metadata = os.fstat(lock_fd)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600
                or lock_metadata.st_nlink != 1
                or lock_metadata.st_uid != os.geteuid()
                or lock_metadata.st_gid != os.getegid()
            ):
                raise SourceDeletionError("deletion_lock_invalid")
            named_lock = os.stat(
                f"{operation_key}.lock",
                dir_fd=receipt_owner.fd,
                follow_symlinks=False,
            )
            if (
                named_lock.st_dev,
                named_lock.st_ino,
                named_lock.st_mode,
                named_lock.st_nlink,
                named_lock.st_uid,
                named_lock.st_gid,
            ) != (
                lock_metadata.st_dev,
                lock_metadata.st_ino,
                lock_metadata.st_mode,
                lock_metadata.st_nlink,
                lock_metadata.st_uid,
                lock_metadata.st_gid,
            ):
                raise SourceDeletionError("deletion_lock_path_substituted")
            lock_key = (lock_metadata.st_dev, lock_metadata.st_ino)
            with _THREAD_LOCK_REGISTRY_GUARD:
                thread_lock = _THREAD_TRANSACTION_LOCKS.get(lock_key)
                if thread_lock is None:
                    thread_lock = threading.Lock()
                    _THREAD_TRANSACTION_LOCKS[lock_key] = thread_lock
            thread_lock.acquire()
            creation_lock.release()
            creation_lock_held = False
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked_metadata = os.fstat(lock_fd)
            locked_named = os.stat(
                f"{operation_key}.lock",
                dir_fd=receipt_owner.fd,
                follow_symlinks=False,
            )
            if (
                locked_metadata.st_dev,
                locked_metadata.st_ino,
                locked_metadata.st_mode,
                locked_metadata.st_nlink,
                locked_metadata.st_uid,
                locked_metadata.st_gid,
            ) != (
                locked_named.st_dev,
                locked_named.st_ino,
                locked_named.st_mode,
                locked_named.st_nlink,
                locked_named.st_uid,
                locked_named.st_gid,
            ):
                raise SourceDeletionError("deletion_lock_path_substituted")
            self._cleanup_operation_temps(receipt_owner, operation_key)
            blocked = self._read_optional(receipt_owner, f"{operation_key}.blocked.json")
            if blocked is not None:
                raise SourceDeletionError("source_deletion_operation_blocked")
            self._write_once(receipt_owner, f"{operation_key}.request.json", request_bytes)
            receipt_sets = self._gate_receipt_sets(request)
            ownership_context = self._ownership_authority.acquire(
                rollback_id=request.rollback_id,
                journal_request_digest=request.journal_request_digest,
                sources=request.owned_sources,
            )
            gate_context = self._verifier.acquire(
                receipt_sets=receipt_sets,
                rollback_id=request.rollback_id,
                journal_request_digest=request.journal_request_digest,
            )
            with ownership_context as lease, gate_context as gate_lease:
                fence = self._validate_ownership_fence(request, lease)
                gate_outcomes = self._validate_gate_lease(
                    request,
                    receipt_sets,
                    gate_lease,
                )
                self._validate_authority_joins(request, fence, gate_outcomes)
                return self._delete_under_fence(
                    request,
                    request_digest,
                    operation_key,
                    receipt_owner,
                    lease,
                    gate_lease,
                )
        finally:
            if creation_lock_held and creation_lock is not None:
                creation_lock.release()
            if lock_fd >= 0:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            if thread_lock is not None:
                thread_lock.release()
            if root_lock_held:
                fcntl.flock(receipt_owner.fd, fcntl.LOCK_UN)
            receipt_owner.close()


    def _delete_under_fence(
        self,
        request: SourceDeletionRequest,
        request_digest: str,
        operation_key: str,
        receipt_owner: _DirFd,
        lease: SourceOwnershipLease,
        gate_lease: FinalReceiptLease,
    ) -> SourceDeletionReceipt:
        roots = self._open_request_roots(request)
        quarantines: dict[str, _DirFd] = {}
        try:
            receipt_name = f"{operation_key}.receipt.json"
            completion_name = f"{operation_key}.completion.json"
            prior_receipt = self._read_optional(receipt_owner, receipt_name)
            prior_completion = self._read_optional(receipt_owner, completion_name)
            if prior_receipt is not None:
                receipt = self._parse_receipt(prior_receipt)
                if prior_completion is None:
                    raise SourceDeletionError("deletion_receipt_without_completion")
                if receipt is not None:
                    assert prior_completion is not None
                    self._validate_receipt(
                        request,
                        request_digest,
                        receipt,
                        roots,
                        prior_completion,
                    )
                    quarantines = self._open_private_quarantines(
                        request,
                        roots,
                        operation_key,
                    )
                    ordered = self._ordered_sources(request)
                    expected_intents = {
                        source.key: self._intent_for(
                            request,
                            request_digest,
                            operation_key,
                            index,
                            source,
                        )
                        for index, source in enumerate(ordered)
                    }
                    intents = self._load_intents(
                        receipt_owner,
                        operation_key,
                        request,
                        expected_intents,
                    )
                    if set(intents) != set(expected_intents):
                        raise SourceDeletionError(
                            "deletion_receipt_intent_inventory_incomplete"
                        )
                    self._validate_inventory_with_private_access(
                        request,
                        roots,
                        quarantines,
                        intents,
                        frozenset(source.key for source in request.owned_sources),
                        frozenset(),
                    )
                    lease.assert_current()
                    gate_lease.assert_current()
                    self._write_once(receipt_owner, receipt_name, prior_receipt)
                    lease.assert_current()
                    gate_lease.assert_current()
                    return receipt
            if self._read_optional(
                receipt_owner,
                f"{operation_key}.blocked.json",
            ) is not None:
                raise SourceDeletionError("source_deletion_previously_blocked")
            quarantines = self._open_private_quarantines(request, roots, operation_key)
            ordered = self._ordered_sources(request)
            expected_intents = {
                source.key: self._intent_for(request, request_digest, operation_key, index, source)
                for index, source in enumerate(ordered)
            }
            intents = self._load_intents(receipt_owner, operation_key, request, expected_intents)
            preflight_name = f"{operation_key}.preflight.json"
            preflight = canonical_json_bytes(
                {
                    "owned_source_digests": [
                        _sha256(canonical_json_bytes(_identity_projection(source)))
                        for source in request.owned_sources
                    ],
                    "operation_id": request.operation_id,
                    "request_digest": request_digest,
                    "schema_version": _PREFLIGHT_SCHEMA,
                }
            )
            prior_preflight = self._read_optional(receipt_owner, preflight_name)
            if prior_preflight is None:
                if intents:
                    raise SourceDeletionError("deletion_intent_without_preflight")
                self._validate_inventory_with_private_access(
                    request,
                    roots,
                    quarantines,
                    {},
                    frozenset(),
                    frozenset(),
                )
                lease.assert_current()
                self._write_once(receipt_owner, preflight_name, preflight)
            elif prior_preflight != preflight:
                raise SourceDeletionConflict("deletion_preflight_conflict")
            else:
                self._resync_existing(receipt_owner, preflight_name)
            missing_before = frozenset(
                source.key
                for source in request.owned_sources
                if not self._exists(roots[source.root_authority_id], source.relative_path)
            )
            if any(key not in intents for key in missing_before):
                raise SourceDeletionError("owned_source_absent_without_durable_intent")
            recovered_sources = self._load_recovered_sources(
                receipt_owner,
                operation_key,
                request,
                intents,
            )
            transitions, transitions_by_digest, consumed_transitions = (
                self._load_transitions(
                receipt_owner,
                operation_key,
                request_digest,
                request,
                intents,
                quarantines,
                )
            )
            helper_successes = self._load_helper_successes(
                request,
                request_digest,
                operation_key,
                roots,
                quarantines,
                intents,
                transitions_by_digest,
            )
            if any(key not in helper_successes for key in missing_before):
                raise SourceDeletionError(
                    "owned_source_absent_without_helper_success"
                )
            self._validate_inventory_with_private_access(
                request,
                roots,
                quarantines,
                intents,
                missing_before,
                recovered_sources,
            )
            if prior_completion is not None:
                self._validate_completion(
                    request,
                    request_digest,
                    prior_completion,
                    roots,
                )
            deleted: list[str] = []
            already_absent: list[str] = []
            for source in ordered:
                lease.assert_current()
                gate_lease.assert_current()
                owner = roots[source.root_authority_id]
                quarantine = quarantines[source.root_authority_id]
                intent = intents.get(source.key, expected_intents[source.key])
                original_exists = self._exists(owner, source.relative_path)
                capsule_exists = self._exists(
                    quarantine,
                    intent.quarantine_relative_path,
                )
                if capsule_exists and source.key not in intents:
                    raise SourceDeletionError(
                        f"quarantine_without_durable_intent:{source.key}"
                    )
                quarantined_exists = False
                if capsule_exists:
                    capsule = self._open_capsule(quarantine, intent, create=False)
                    try:
                        quarantined_exists = self._capsule_contains_owned(capsule)
                    finally:
                        capsule.close()
                if original_exists and quarantined_exists:
                    raise SourceDeletionError(
                        f"source_and_quarantine_both_present:{source.key}"
                    )
                if quarantined_exists:
                    transition = transitions.get(source.key)
                    if transition is None:
                        raise SourceDeletionError(
                            f"quarantine_without_post_rename_transition:{source.key}"
                        )
                    self._delete_quarantined(
                        request,
                        roots,
                        quarantines,
                        quarantine,
                        source,
                        intent,
                        receipt_owner,
                        operation_key,
                        gate_lease,
                        lease,
                        transition,
                    )
                    deleted.append(source.key)
                    continue
                if not original_exists:
                    success_pair = helper_successes.get(source.key)
                    if (
                        source.key not in intents
                        or not capsule_exists
                        or success_pair is None
                    ):
                        raise SourceDeletionError(
                            f"owned_source_disappeared_without_helper_success:{source.key}"
                        )
                    success, success_transition = success_pair
                    if (
                        success.transition_digest not in consumed_transitions
                        and transitions.get(source.key) != success_transition
                    ):
                        raise SourceDeletionError(
                            f"helper_success_transition_not_active:{source.key}"
                        )
                    if success.transition_digest not in consumed_transitions:
                        consumed_name, consumed_raw = (
                            self._transition_consumed_record(
                                operation_key,
                                source,
                                success_transition,
                            )
                        )
                        self._write_once(
                            receipt_owner,
                            consumed_name,
                            consumed_raw,
                        )
                    already_absent.append(source.key)
                    continue
                if source.key not in intents:
                    try:
                        snapshot = self._capture_source_snapshot(owner, source)
                    except BlockingIOError as exc:
                        raise SourceDeletionError(
                            f"source_exclusive_lease_conflict:{source.key}"
                        ) from exc
                    intent = self._intent_for(
                        request,
                        request_digest,
                        operation_key,
                        ordered.index(source),
                        source,
                        snapshot,
                    )
                    expected_intents[source.key] = intent
                    self._write_once(
                        receipt_owner,
                        self._intent_name(operation_key, ordered.index(source)),
                        intent.raw,
                    )
                    intents[source.key] = intent
                recovered_from_intent = source.key in recovered_sources
                if recovered_from_intent:
                    consumed_name, consumed_raw = self._recovery_record(
                        operation_key,
                        source,
                        intent,
                        consumed=True,
                    )
                    self._write_once(receipt_owner, consumed_name, consumed_raw)
                    prior_transition = transitions.get(source.key)
                    if prior_transition is not None:
                        transition_consumed_name, transition_consumed_raw = (
                            self._transition_consumed_record(
                                operation_key,
                                source,
                                prior_transition,
                            )
                        )
                        self._write_once(
                            receipt_owner,
                            transition_consumed_name,
                            transition_consumed_raw,
                        )
                self._quarantine_and_delete(
                    request,
                    roots,
                    quarantines,
                    owner,
                    quarantine,
                    source,
                    intent,
                    receipt_owner,
                    operation_key,
                    gate_lease,
                    lease,
                    recovered_from_intent,
                )
                deleted.append(source.key)
            lease.assert_current()
            gate_lease.assert_current()
            completion_raw = self._read_optional(receipt_owner, completion_name)
            if completion_raw is None:
                completed_at = self._timestamp()
                proofs = tuple(
                    self._absence_proof(source, roots[source.root_authority_id], completed_at)
                    for source in request.owned_sources
                )
                completion_document = {
                    "absence_proofs": [proof.projection() for proof in proofs],
                    "already_absent": already_absent,
                    "completed_at": completed_at,
                    "deleted": deleted,
                    "operation_id": request.operation_id,
                    "request_digest": request_digest,
                    "schema_version": _COMPLETION_SCHEMA,
                }
                completion_raw = canonical_json_bytes(completion_document)
                lease.assert_current()
                gate_lease.assert_current()
                self._write_once(receipt_owner, completion_name, completion_raw)
                lease.assert_current()
                gate_lease.assert_current()
            completion = self._parse_completion(completion_raw)
            unsigned = {
                key: value for key, value in completion.items() if key != "schema_version"
            }
            unsigned["schema_version"] = _RECEIPT_SCHEMA
            unsigned["completion_digest"] = _sha256(completion_raw)
            signature = self._sign_receipt(unsigned)
            receipt_document = {**unsigned, "authority_signature": signature}
            receipt_raw = canonical_json_bytes(receipt_document)
            receipt = self._parse_receipt(receipt_raw)
            self._validate_receipt(
                request,
                request_digest,
                receipt,
                roots,
                completion_raw,
            )
            lease.assert_current()
            gate_lease.assert_current()
            self._write_once(receipt_owner, receipt_name, receipt_raw)
            gate_lease.assert_current()
            lease.assert_current()
            return receipt
        finally:
            for owner in quarantines.values():
                try:
                    os.fchmod(owner.fd, 0)
                    os.fsync(owner.fd)
                finally:
                    owner.close()
            for owner in roots.values():
                owner.close()

    @staticmethod
    def _ordered_sources(request: SourceDeletionRequest) -> tuple[SourceOwnershipIdentity, ...]:
        return tuple(
            sorted(
                request.owned_sources,
                key=lambda source: (
                    -len(PurePosixPath(source.relative_path).parts),
                    1 if source.kind == "directory" else 0,
                    source.root_authority_id,
                    source.relative_path,
                ),
            )
        )

    def _validate_ownership_fence(
        self, request: SourceDeletionRequest, lease: SourceOwnershipLease
    ) -> VerifiedSourceOwnershipFence:
        try:
            fence = lease.fence
            lease.assert_current()
        except Exception as exc:
            raise SourceDeletionError("source_ownership_fence_invalid") from exc
        if type(fence) is not VerifiedSourceOwnershipFence:
            raise SourceDeletionError("source_ownership_fence_unverified")
        if (
            fence.rollback_id != request.rollback_id
            or fence.journal_request_digest != request.journal_request_digest
            or tuple(item.source_key for item in fence.bindings)
            != tuple(source.key for source in request.owned_sources)
        ):
            raise SourceDeletionError("source_ownership_fence_join_invalid")
        by_key = {source.key: source for source in request.owned_sources}
        for binding in fence.bindings:
            source = by_key[binding.source_key]
            record = binding.record
            if record.promotion_eligible is not False or record.export_eligible is not False:
                raise SourceDeletionError(f"source_dependent_still_eligible:{source.key}")
            if record.ownership.object_ref.digest != source.sha256:
                raise SourceDeletionError(f"source_ownership_digest_mismatch:{source.key}")
            matching = tuple(
                receipt
                for receipt in record.quarantine_receipts
                if receipt.rollback_id == request.rollback_id
                and receipt.cause_digest == request.journal_request_digest
            )
            if len(matching) != 1:
                raise SourceDeletionError(f"source_quarantine_receipt_join_invalid:{source.key}")
        return fence

    def _gate_receipt_sets(
        self,
        request: SourceDeletionRequest,
    ) -> dict[GateKind, tuple[tuple[SourceDeletionGateReceipt, bytes], ...]]:
        return {
            gate: tuple((ref, self._read_immutable_receipt(ref)) for ref in refs)
            for gate, refs in request.gates.groups()
        }

    @staticmethod
    def _validate_gate_lease(
        request: SourceDeletionRequest,
        receipt_sets: Mapping[
            GateKind,
            tuple[tuple[SourceDeletionGateReceipt, bytes], ...],
        ],
        lease: FinalReceiptLease,
    ) -> dict[GateKind, VerifiedGateOutcome]:
        lease.assert_current()
        outcomes = dict(lease.outcomes)
        if set(outcomes) != {gate for gate, _ in request.gates.groups()}:
            raise SourceDeletionError("authoritative_gate_fence_incomplete")
        for gate, refs in request.gates.groups():
            outcome = outcomes.get(gate)
            if type(outcome) is not VerifiedGateOutcome:
                raise SourceDeletionError(f"{gate}_unverified_outcome")
            supplied = receipt_sets.get(gate)
            if supplied is None or tuple(item[0] for item in supplied) != refs:
                raise SourceDeletionError(f"{gate}_receipt_fence_join_invalid")
            expected_inventory_digest = _sha256(
                canonical_json_bytes(
                    {
                        "authority_generation": outcome.authority_generation,
                        "gate": gate,
                        "journal_request_digest": request.journal_request_digest,
                        "receipt_sha256s": [ref.sha256 for ref in refs],
                        "rollback_id": request.rollback_id,
                        "subjects": list(outcome.subjects),

                    }
                )
            )
            if (
                outcome.gate != gate
                or outcome.rollback_id != request.rollback_id
                or outcome.journal_request_digest != request.journal_request_digest
                or outcome.receipt_sha256s != tuple(ref.sha256 for ref in refs)
                or outcome.terminal_outcome != _ALLOWED_GATE_OUTCOME[gate]
                or outcome.inventory_digest != expected_inventory_digest
                or outcome.current is not True
            ):
                raise SourceDeletionError(f"{gate}_receipt_outcome_invalid")
        lease.assert_current()
        return outcomes



    @staticmethod
    def _validate_authority_joins(
        request: SourceDeletionRequest,
        fence: VerifiedSourceOwnershipFence,
        outcomes: Mapping[GateKind, VerifiedGateOutcome],
    ) -> None:
        dependent_subjects = outcomes["dependent_quarantined"].subjects
        expected_dependents = tuple(
            binding.record.ownership.object_ref.reference for binding in fence.bindings
        )
        if dependent_subjects != expected_dependents:
            raise SourceDeletionError("dependent_gate_inventory_ownership_mismatch")
        expected_episodes = tuple(
            dict.fromkeys(
                binding.record.ownership.episode_id
                for binding in fence.bindings
            )
        )
        if outcomes["episode_terminal"].subjects != expected_episodes:
            raise SourceDeletionError("episode_gate_inventory_ownership_mismatch")

    def _open_request_roots(self, request: SourceDeletionRequest) -> dict[str, _DirFd]:
        roots: dict[str, _DirFd] = {}
        for source in request.owned_sources:
            authority = self._authorities.get(source.root_authority_id)
            if authority is None or source.root_path != authority.path:
                raise SourceDeletionError("source_root_authority_mismatch")
            source_absolute = os.path.join(source.root_path, *PurePosixPath(source.relative_path).parts)
            for other_id, other in self._authorities.items():
                if other_id != source.root_authority_id and (
                    source_absolute == other.path or self._is_beneath(other.path, source_absolute)
                ):
                    raise SourceDeletionError("source_contains_root_authority")
            if self._paths_overlap(source_absolute, os.fspath(self._receipt_root)):
                raise SourceDeletionError("source_contains_receipt_authority")
            if source.root_authority_id in roots:
                continue
            descriptor = self._open_directory(authority.path)
            metadata = os.fstat(descriptor)
            if self._directory_identity(metadata) != authority.identity:
                os.close(descriptor)
                raise SourceDeletionError("source_root_substituted")
            roots[source.root_authority_id] = _DirFd(descriptor, duplicate=False)
        return roots

    def _open_private_quarantines(
        self,
        request: SourceDeletionRequest,
        roots: Mapping[str, _DirFd],
        operation_key: str,
    ) -> dict[str, _DirFd]:
        quarantines: dict[str, _DirFd] = {}
        try:
            for authority_id, root in roots.items():
                private_name = self._private_quarantine_name(operation_key, authority_id)
                if any(
                    PurePosixPath(source.relative_path).parts[0] == private_name
                    for source in request.owned_sources
                    if source.root_authority_id == authority_id
                ):
                    raise SourceDeletionError("source_overlaps_private_quarantine")
                try:
                    os.mkdir(private_name, mode=0o700, dir_fd=root.fd)
                    root.fsync_dir()
                except FileExistsError:
                    metadata = os.stat(private_name, dir_fd=root.fd, follow_symlinks=False)
                    if (
                        not stat.S_ISDIR(metadata.st_mode)
                        or stat.S_ISLNK(metadata.st_mode)
                        or metadata.st_uid != os.geteuid()
                        or metadata.st_nlink < 2
                    ):
                        raise SourceDeletionError("private_quarantine_substituted")
                    os.chmod(
                        private_name,
                        0o700,
                        dir_fd=root.fd,
                        follow_symlinks=False,
                    )
                descriptor = os.open(
                    private_name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=root.fd,
                )
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_nlink < 2
                ):
                    os.close(descriptor)
                    raise SourceDeletionError("private_quarantine_substituted")
                os.fchmod(descriptor, 0)
                os.fsync(descriptor)
                root.fsync_dir()
                quarantines[authority_id] = _DirFd(descriptor, duplicate=False)
            return quarantines
        except Exception:
            for owner in quarantines.values():
                owner.close()
            raise

    def _private_quarantine_name(self, operation_key: str, authority_id: str) -> str:
        token = hmac.new(
            self._receipt_key,
            canonical_json_bytes(
                {
                    "authority_id": authority_id,
                    "operation_key": operation_key,
                    "purpose": "g4-private-quarantine",
                }
            ),
            hashlib.sha256,
        ).hexdigest()
        return _PRIVATE_QUARANTINE_PREFIX + token


    def _validate_inventory_with_private_access(
        self,
        request: SourceDeletionRequest,
        roots: Mapping[str, _DirFd],
        quarantines: Mapping[str, _DirFd],
        intents: Mapping[str, _DeletionIntent],
        admitted_missing: frozenset[str],
        admitted_recovered: frozenset[str],
    ) -> None:
        try:
            for owner in quarantines.values():
                os.fchmod(owner.fd, 0o700)
            self._validate_inventory(
                request,
                roots,
                quarantines,
                intents,
                admitted_missing,
                admitted_recovered,
            )
        finally:
            for owner in quarantines.values():
                os.fchmod(owner.fd, 0o300)

    def _validate_inventory(
        self,
        request: SourceDeletionRequest,
        roots: Mapping[str, _DirFd],
        quarantines: Mapping[str, _DirFd],
        intents: Mapping[str, _DeletionIntent],
        admitted_missing: frozenset[str],
        admitted_recovered: frozenset[str],
    ) -> None:
        by_authority = {
            authority_id: {
                source.relative_path: source
                for source in request.owned_sources
                if source.root_authority_id == authority_id
            }
            for authority_id in roots
        }
        missing_by_authority = {
            authority_id: {
                source.relative_path
                for source in request.owned_sources
                if source.root_authority_id == authority_id and source.key in admitted_missing
            }
            for authority_id in roots
        }
        expected_quarantines = {
            authority_id: {
                intent.quarantine_relative_path
                for key, intent in intents.items()
                if key.startswith(authority_id + ":")
            }
            for authority_id in quarantines
        }
        for authority_id, quarantine in quarantines.items():
            directory = quarantine.open_dir()
            try:
                actual_quarantines = set(os.listdir(directory))
            finally:
                os.close(directory)
            if not actual_quarantines.issubset(expected_quarantines[authority_id]):
                raise SourceDeletionError("private_quarantine_inventory_mismatch")
        for source in request.owned_sources:
            owner = roots[source.root_authority_id]
            if source.key in admitted_missing:
                intent = intents.get(source.key)
                if (
                    intent is not None
                    and self._exists(
                        quarantines[source.root_authority_id],
                        intent.quarantine_relative_path,
                    )
                ):
                    self._verify_quarantine(
                        quarantines[source.root_authority_id],
                        source,
                        intent,
                    )
                continue
            intent = intents.get(source.key)
            recovery_snapshot = (
                intent.pre_rename_snapshot
                if intent is not None and source.key in admitted_recovered
                else None
            )
            relaxed = source.kind == "directory" and any(
                path.startswith(source.relative_path + "/")
                for path in missing_by_authority[source.root_authority_id]
            )
            self._verify_source(
                owner,
                source,
                relaxed_directory=relaxed,
                recovery_snapshot=recovery_snapshot,
            )
            if source.kind != "directory":
                continue
            directory = owner.open_dir(source.relative_path)
            try:
                actual_children = set(os.listdir(directory))
            finally:
                os.close(directory)
            expected_children = {
                PurePosixPath(path).name
                for path in by_authority[source.root_authority_id]
                if PurePosixPath(path).parent == PurePosixPath(source.relative_path)
                and _source_label(source.root_authority_id, path) not in admitted_missing
            }
            if actual_children != expected_children:
                raise SourceDeletionError(f"unowned_or_missing_descendant:{source.key}")

    def _intent_for(
        self,
        request: SourceDeletionRequest,
        request_digest: str,
        operation_key: str,
        index: int,
        source: SourceOwnershipIdentity,
        snapshot: _SourceSnapshot | None = None,
    ) -> _DeletionIntent:
        quarantine = (
            hmac.new(
                self._receipt_key,
                canonical_json_bytes(
                    {
                        "index": index,
                        "operation_key": operation_key,
                        "source_key": source.key,
                    }
                ),
                hashlib.sha256,
            ).hexdigest()
            + ".capsule"
        )
        unsigned: dict[str, object] = {
            "operation_id": request.operation_id,
            "quarantine_relative_path": quarantine,
            "request_digest": request_digest,
            "schema_version": _INTENT_SCHEMA,
            "source": _identity_projection(source),
        }
        if snapshot is None:
            return _DeletionIntent(source, quarantine, canonical_json_bytes(unsigned))
        unsigned["pre_rename_snapshot"] = snapshot.projection()
        signature = _HMAC_PREFIX + hmac.new(
            self._receipt_key,
            canonical_json_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        raw = canonical_json_bytes({**unsigned, "authority_signature": signature})
        return _DeletionIntent(source, quarantine, raw, snapshot)

    def _capture_source_snapshot(
        self,
        owner: _DirFd,
        source: SourceOwnershipIdentity,
    ) -> _SourceSnapshot:
        parent, name = self._open_parent(owner, source.relative_path)
        descriptor = -1
        try:
            descriptor = self._open_source_at(parent, name, source.kind)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._verify_open_descriptor(
                descriptor,
                source,
                relaxed_directory=source.kind == "directory",
            )
            opened = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if self._file_identity(opened) != self._file_identity(named):
                raise SourceDeletionError(f"source_snapshot_race:{source.key}")
            return _SourceSnapshot(
                device=opened.st_dev,
                inode=opened.st_ino,
                ctime_ns=opened.st_ctime_ns,
                mode=opened.st_mode,
                uid=opened.st_uid,
                gid=opened.st_gid,
                link_count=opened.st_nlink,
                size_bytes=opened.st_size,
                atime_ns=opened.st_atime_ns,
                mtime_ns=opened.st_mtime_ns,
                kind=source.kind,
                sha256=source.sha256,
            )
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            os.close(parent)

    @staticmethod
    def _intent_name(operation_key: str, index: int) -> str:
        return f"{operation_key}.intent.{index:08d}.json"

    def _recovery_record(
        self,
        operation_key: str,
        source: SourceOwnershipIdentity,
        intent: _DeletionIntent,
        *,
        consumed: bool,
    ) -> tuple[str, bytes]:
        snapshot = intent.pre_rename_snapshot
        if snapshot is None:
            raise SourceDeletionError("deletion_intent_snapshot_missing")
        unsigned: dict[str, object] = {
            "capsule_relative_path": intent.quarantine_relative_path,
            "intent_digest": _sha256(intent.raw),
            "operation_key": operation_key,
            "schema_version": _RECOVERY_SCHEMA,
            "source_key": source.key,
            "stable_source_snapshot": snapshot.projection(),
            "state": "recovery_consumed" if consumed else "source_restored_capsule_empty",
        }
        signature = _HMAC_PREFIX + hmac.new(
            self._receipt_key,
            canonical_json_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        raw = canonical_json_bytes({**unsigned, "authority_signature": signature})
        digest = hashlib.sha256(raw).hexdigest()
        phase = "recovery-consumed" if consumed else "recovery"
        return f"{operation_key}.{phase}.{digest}.json", raw

    def _load_recovered_sources(
        self,
        owner: _DirFd,
        operation_key: str,
        request: SourceDeletionRequest,
        intents: Mapping[str, _DeletionIntent],
    ) -> frozenset[str]:
        expected: dict[str, tuple[str, str]] = {}
        records: dict[str, bytes] = {}
        for source in request.owned_sources:
            intent = intents.get(source.key)
            if intent is None:
                continue
            recovery_name, recovery_raw = self._recovery_record(
                operation_key,
                source,
                intent,
                consumed=False,
            )
            consumed_name, consumed_raw = self._recovery_record(
                operation_key,
                source,
                intent,
                consumed=True,
            )
            expected[source.key] = (recovery_name, consumed_name)
            records[recovery_name] = recovery_raw
            records[consumed_name] = consumed_raw
        directory = owner.open_dir()
        try:
            actual = {
                name
                for name in os.listdir(directory)
                if name.startswith(operation_key + ".recovery")
            }
        finally:
            os.close(directory)
        if not actual.issubset(records):
            raise SourceDeletionError("source_recovery_marker_unrecognized")
        for name in actual:
            if self._read_required(owner, name) != records[name]:
                raise SourceDeletionError("source_recovery_marker_invalid")
        recovered: set[str] = set()
        for source_key, (recovery_name, consumed_name) in expected.items():
            if recovery_name in actual and consumed_name not in actual:
                recovered.add(source_key)
        return frozenset(recovered)

    def _transition_record(
        self,
        operation_key: str,
        request_digest: str,
        source: SourceOwnershipIdentity,
        intent: _DeletionIntent,
        quarantine: _DirFd,
        capsule: _DirFd,
        snapshot: _SourceSnapshot,
    ) -> tuple[str, bytes]:
        private_metadata = os.fstat(quarantine.fd)
        capsule_metadata = os.fstat(capsule.fd)
        unsigned: dict[str, object] = {
            "capsule_identity": {
                "device": str(capsule_metadata.st_dev),
                "inode": str(capsule_metadata.st_ino),
            },
            "intent_digest": _sha256(intent.raw),
            "operation_key": operation_key,
            "owned_entry_snapshot": snapshot.projection(),
            "private_root_identity": {
                "device": str(private_metadata.st_dev),
                "inode": str(private_metadata.st_ino),
            },
            "request_digest": request_digest,
            "schema_version": _TRANSITION_SCHEMA,
            "source_key": source.key,
        }
        signature = _HMAC_PREFIX + hmac.new(
            self._receipt_key,
            canonical_json_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        raw = canonical_json_bytes({**unsigned, "authority_signature": signature})
        digest = hashlib.sha256(raw).hexdigest()
        return f"{operation_key}.transition.{digest}.json", raw

    def _transition_consumed_record(
        self,
        operation_key: str,
        source: SourceOwnershipIdentity,
        transition: _PostRenameTransition,
    ) -> tuple[str, bytes]:
        unsigned = {
            "operation_key": operation_key,
            "schema_version": _TRANSITION_CONSUMED_SCHEMA,
            "source_key": source.key,
            "transition_digest": _sha256(transition.raw),
        }
        signature = _HMAC_PREFIX + hmac.new(
            self._receipt_key,
            canonical_json_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        raw = canonical_json_bytes({**unsigned, "authority_signature": signature})
        digest = hashlib.sha256(raw).hexdigest()
        return f"{operation_key}.transition-consumed.{digest}.json", raw

    def _helper_success_for(
        self,
        request: SourceDeletionRequest,
        request_digest: str,
        operation_key: str,
        source: SourceOwnershipIdentity,
        root: _DirFd,
        quarantine: _DirFd,
        capsule: _DirFd,
        intent: _DeletionIntent,
        transition: _PostRenameTransition,
        helper_semantic_digest: str | None = None,
    ) -> tuple[_HelperSuccess, dict[str, object]]:
        if helper_semantic_digest is None:
            helper_semantic_digest = (
                _DIGEST_PREFIX + self._helper_capability_digest.hex()
            )
        _require_digest(helper_semantic_digest, "helper_semantic_digest")
        root_metadata = os.fstat(root.fd)
        private_metadata = os.fstat(quarantine.fd)
        capsule_metadata = os.fstat(capsule.fd)
        unsigned: dict[str, object] = {
            "capsule_identity": {
                "device": str(capsule_metadata.st_dev),
                "inode": str(capsule_metadata.st_ino),
            },
            "helper_semantic_digest": helper_semantic_digest,
            "intent_digest": _sha256(intent.raw),
            "operation_id": request.operation_id,
            "operation_key": operation_key,
            "owned_entry_snapshot": transition.owned_snapshot.projection(),
            "postconditions": {
                "capsule_entries": [],
                "parent_name_absent": True,
                "retained_inode_terminal": True,
            },
            "private_root_identity": {
                "device": str(private_metadata.st_dev),
                "inode": str(private_metadata.st_ino),
            },
            "request_digest": request_digest,
            "root_identity": {
                "device": str(root_metadata.st_dev),
                "inode": str(root_metadata.st_ino),
            },
            "schema_version": _HELPER_SUCCESS_SCHEMA,
            "source_key": source.key,
            "transition_digest": _sha256(transition.raw),
        }
        signature = _HMAC_PREFIX + hmac.new(
            self._receipt_key,
            canonical_json_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        document = {**unsigned, "authority_signature": signature}
        raw = canonical_json_bytes(document)
        name = (
            _HELPER_SUCCESS_PREFIX
            + hashlib.sha256(raw).hexdigest()
            + ".json"
        )
        return (
            _HelperSuccess(
                source.key,
                _sha256(transition.raw),
                name,
                raw,
            ),
            document,
        )

    @staticmethod
    def _read_helper_success(
        capsule: _DirFd,
        expected: _HelperSuccess,
    ) -> _HelperSuccess | None:
        os.fchmod(capsule.fd, 0o500)
        descriptor = -1
        try:
            entries = os.listdir(capsule.fd)
            if entries == []:
                return None
            if entries != [expected.name]:
                return None
            no_follow = getattr(os, "O_NOFOLLOW", None)
            if type(no_follow) is not int or no_follow == 0:
                raise SourceDeletionError("helper_success_nofollow_unavailable")
            descriptor = os.open(
                expected.name,
                os.O_RDONLY | no_follow,
                dir_fd=capsule.fd,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()
                or metadata.st_gid != os.getegid()
                or metadata.st_nlink != 1
                or metadata.st_size != len(expected.raw)
            ):
                raise SourceDeletionError("helper_success_authority_invalid")
            raw = bytearray()
            while len(raw) <= len(expected.raw):
                chunk = os.read(descriptor, len(expected.raw) + 1 - len(raw))
                if not chunk:
                    break
                raw.extend(chunk)
            if bytes(raw) != expected.raw:
                raise SourceDeletionError("helper_success_invalid")
            return expected
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.fchmod(capsule.fd, 0)

    @staticmethod
    def _installed_helper_semantic_digest(capsule: _DirFd) -> str | None:
        os.fchmod(capsule.fd, 0o500)
        descriptor = -1
        try:
            entries = os.listdir(capsule.fd)
            if entries == []:
                return None
            if (
                len(entries) != 1
                or re.fullmatch(r"\.success\.[0-9a-f]{64}\.json", entries[0])
                is None
            ):
                raise SourceDeletionError("helper_success_unrecognized")
            no_follow = getattr(os, "O_NOFOLLOW", None)
            if type(no_follow) is not int or no_follow == 0:
                raise SourceDeletionError("helper_success_nofollow_unavailable")
            descriptor = os.open(
                entries[0],
                os.O_RDONLY | no_follow,
                dir_fd=capsule.fd,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()
                or metadata.st_gid != os.getegid()
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
                or metadata.st_size > _HELPER_MAX_BYTES
            ):
                raise SourceDeletionError("helper_success_authority_invalid")
            raw = bytearray()
            while len(raw) <= _HELPER_MAX_BYTES:
                chunk = os.read(
                    descriptor,
                    min(1024, _HELPER_MAX_BYTES + 1 - len(raw)),
                )
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) != metadata.st_size:
                raise SourceDeletionError("helper_success_invalid")
            try:
                document = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SourceDeletionError("helper_success_invalid") from exc
            if (
                type(document) is not dict
                or canonical_json_bytes(document) != raw
                or type(document.get("helper_semantic_digest")) is not str
            ):
                raise SourceDeletionError("helper_success_invalid")
            _require_digest(
                document["helper_semantic_digest"],
                "helper_semantic_digest",
            )
            return document["helper_semantic_digest"]
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.fchmod(capsule.fd, 0)


    def _load_helper_successes(
        self,
        request: SourceDeletionRequest,
        request_digest: str,
        operation_key: str,
        roots: Mapping[str, _DirFd],
        quarantines: Mapping[str, _DirFd],
        intents: Mapping[str, _DeletionIntent],
        transitions_by_digest: Mapping[str, _PostRenameTransition],
    ) -> dict[str, tuple[_HelperSuccess, _PostRenameTransition]]:
        successes: dict[str, tuple[_HelperSuccess, _PostRenameTransition]] = {}
        for source in request.owned_sources:
            intent = intents.get(source.key)
            if intent is None:
                continue
            quarantine = quarantines[source.root_authority_id]
            os.fchmod(quarantine.fd, 0o700)
            if not self._exists(
                quarantine,
                intent.quarantine_relative_path,
            ):
                os.fchmod(quarantine.fd, 0)
                continue
            capsule = self._open_capsule(quarantine, intent, create=False)
            try:
                if self._capsule_contains_owned(capsule):
                    continue
                helper_semantic_digest = (
                    self._installed_helper_semantic_digest(capsule)
                )
                matched = False
                for transition in transitions_by_digest.values():
                    if transition.source_key != source.key:
                        continue
                    expected, _ = self._helper_success_for(
                        request,
                        request_digest,
                        operation_key,
                        source,
                        roots[source.root_authority_id],
                        quarantine,
                        capsule,
                        intent,
                        transition,
                        helper_semantic_digest,
                    )
                    success = self._read_helper_success(capsule, expected)
                    if success is None:
                        continue
                    if matched:
                        raise SourceDeletionError("helper_success_duplicate")
                    successes[source.key] = (success, transition)
                    matched = True
                if not matched:
                    os.fchmod(capsule.fd, 0o500)
                    try:
                        if os.listdir(capsule.fd):
                            raise SourceDeletionError("helper_success_unrecognized")
                    finally:
                        os.fchmod(capsule.fd, 0)
            finally:
                capsule.close()
                os.fchmod(quarantine.fd, 0)
        return successes

    @staticmethod
    def _snapshot_from_projection(value: object) -> _SourceSnapshot:
        expected_keys = {
            "atime_ns",
            "ctime_ns",
            "device",
            "gid",
            "inode",
            "kind",
            "link_count",
            "mode",
            "mtime_ns",
            "sha256",
            "size_bytes",
            "uid",
        }
        if type(value) is not dict or set(value) != expected_keys:
            raise SourceDeletionError("source_transition_snapshot_invalid")
        assert isinstance(value, dict)
        integer_names = expected_keys - {"kind", "sha256"}
        if any(
            type(value[name]) is not str
            or not value[name].isascii()
            or not value[name].isdecimal()
            or str(int(value[name])) != value[name]
            for name in integer_names
        ):
            raise SourceDeletionError("source_transition_snapshot_invalid")
        if value["kind"] not in {"file", "directory"}:
            raise SourceDeletionError("source_transition_snapshot_invalid")
        try:
            _require_digest(value["sha256"], "transition_snapshot_sha256")
            return _SourceSnapshot(
                device=int(value["device"]),
                inode=int(value["inode"]),
                ctime_ns=int(value["ctime_ns"]),
                mode=int(value["mode"]),
                uid=int(value["uid"]),
                gid=int(value["gid"]),
                link_count=int(value["link_count"]),
                size_bytes=int(value["size_bytes"]),
                atime_ns=int(value["atime_ns"]),
                mtime_ns=int(value["mtime_ns"]),
                kind=value["kind"],
                sha256=value["sha256"],
            )
        except (TypeError, ValueError) as exc:
            raise SourceDeletionError("source_transition_snapshot_invalid") from exc

    def _load_transitions(
        self,
        owner: _DirFd,
        operation_key: str,
        request_digest: str,
        request: SourceDeletionRequest,
        intents: Mapping[str, _DeletionIntent],
        quarantines: Mapping[str, _DirFd],
    ) -> tuple[
        dict[str, _PostRenameTransition],
        dict[str, _PostRenameTransition],
        frozenset[str],
    ]:
        directory = owner.open_dir()
        try:
            names = tuple(
                name
                for name in os.listdir(directory)
                if name.startswith(operation_key + ".transition")
            )
        finally:
            os.close(directory)
        sources = {source.key: source for source in request.owned_sources}
        transitions_by_digest: dict[str, _PostRenameTransition] = {}
        consumed: dict[str, str] = {}
        for name in names:
            raw = self._read_required(owner, name)
            try:
                document = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SourceDeletionError("source_transition_corrupt") from exc
            if type(document) is not dict or canonical_json_bytes(document) != raw:
                raise SourceDeletionError("source_transition_corrupt")
            unsigned = {
                key: value
                for key, value in document.items()
                if key != "authority_signature"
            }
            expected_signature = _HMAC_PREFIX + hmac.new(
                self._receipt_key,
                canonical_json_bytes(unsigned),
                hashlib.sha256,
            ).hexdigest()
            if (
                type(document.get("authority_signature")) is not str
                or not hmac.compare_digest(
                    document["authority_signature"],
                    expected_signature,
                )
            ):
                raise SourceDeletionError("source_transition_signature_invalid")
            raw_digest = hashlib.sha256(raw).hexdigest()
            if document.get("schema_version") == _TRANSITION_CONSUMED_SCHEMA:
                if (
                    set(document)
                    != {
                        "authority_signature",
                        "operation_key",
                        "schema_version",
                        "source_key",
                        "transition_digest",
                    }
                    or name
                    != f"{operation_key}.transition-consumed.{raw_digest}.json"
                    or document["operation_key"] != operation_key
                    or document["source_key"] not in sources
                ):
                    raise SourceDeletionError("source_transition_consumed_invalid")
                _require_digest(
                    document["transition_digest"],
                    "transition_consumed_digest",
                )
                transition_digest = document["transition_digest"]
                if transition_digest in consumed:
                    raise SourceDeletionError("source_transition_consumed_duplicate")
                consumed[transition_digest] = document["source_key"]
                continue
            if (
                set(document)
                != {
                    "authority_signature",
                    "capsule_identity",
                    "intent_digest",
                    "operation_key",
                    "owned_entry_snapshot",
                    "private_root_identity",
                    "request_digest",
                    "schema_version",
                    "source_key",
                }
                or document["schema_version"] != _TRANSITION_SCHEMA
                or name != f"{operation_key}.transition.{raw_digest}.json"
                or document["operation_key"] != operation_key
                or document["request_digest"] != request_digest
                or document["source_key"] not in sources
            ):
                raise SourceDeletionError("source_transition_invalid")
            source = sources[document["source_key"]]
            intent = intents.get(source.key)
            if (
                intent is None
                or document["intent_digest"] != _sha256(intent.raw)
                or type(document["private_root_identity"]) is not dict
                or set(document["private_root_identity"]) != {"device", "inode"}
                or type(document["capsule_identity"]) is not dict
                or set(document["capsule_identity"]) != {"device", "inode"}
            ):
                raise SourceDeletionError("source_transition_invalid")
            snapshot = self._snapshot_from_projection(
                document["owned_entry_snapshot"]
            )
            pre_rename = intent.pre_rename_snapshot
            if (
                pre_rename is None
                or snapshot.stable_tuple() != pre_rename.stable_tuple()
                or snapshot.ctime_ns < pre_rename.ctime_ns
                or snapshot.kind != source.kind
                or snapshot.sha256 != source.sha256
            ):
                raise SourceDeletionError("source_transition_snapshot_invalid")
            try:
                private_identity = tuple(
                    int(document["private_root_identity"][field])
                    for field in ("device", "inode")
                )
                capsule_identity = tuple(
                    int(document["capsule_identity"][field])
                    for field in ("device", "inode")
                )
            except (TypeError, ValueError) as exc:
                raise SourceDeletionError("source_transition_invalid") from exc
            transition = _PostRenameTransition(
                source.key,
                private_identity,
                capsule_identity,
                snapshot,
                raw,
            )
            transition_digest = _sha256(raw)
            if transition_digest in transitions_by_digest:
                raise SourceDeletionError("source_transition_duplicate")
            transitions_by_digest[transition_digest] = transition
        if any(
            digest not in transitions_by_digest
            or transitions_by_digest[digest].source_key != source_key
            for digest, source_key in consumed.items()
        ):
            raise SourceDeletionError("source_transition_consumed_orphan")
        active: dict[str, _PostRenameTransition] = {}
        for transition_digest, transition in transitions_by_digest.items():
            if transition_digest in consumed:
                continue
            if transition.source_key in active:
                raise SourceDeletionError("source_transition_duplicate")
            active[transition.source_key] = transition
        for source_key, transition in active.items():
            source = sources[source_key]
            quarantine = quarantines[source.root_authority_id]
            private_metadata = os.fstat(quarantine.fd)
            if (
                private_metadata.st_dev,
                private_metadata.st_ino,
            ) != transition.private_identity:
                raise SourceDeletionError("source_transition_private_root_changed")
            intent = intents[source_key]
            os.fchmod(quarantine.fd, 0o700)
            try:
                capsule = self._open_capsule(quarantine, intent, create=False)
            except BaseException:
                os.fchmod(quarantine.fd, 0)
                raise
            descriptor = -1
            try:
                capsule_metadata = os.fstat(capsule.fd)
                if (
                    capsule_metadata.st_dev,
                    capsule_metadata.st_ino,
                ) != transition.capsule_identity:
                    raise SourceDeletionError("source_transition_capsule_changed")
                if self._capsule_contains_owned(capsule):
                    os.fchmod(capsule.fd, 0o700)
                    descriptor = self._open_source_at(
                        capsule.fd,
                        _ENTRY_NAME,
                        source.kind,
                    )
                    metadata = os.fstat(descriptor)
                    digest = (
                        self._file_digest(descriptor)
                        if source.kind == "file"
                        else self._directory_digest(descriptor)
                    )
                    observed = _SourceSnapshot(
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_ctime_ns,
                        metadata.st_mode,
                        metadata.st_uid,
                        metadata.st_gid,
                        metadata.st_nlink,
                        metadata.st_size,
                        metadata.st_atime_ns,
                        metadata.st_mtime_ns,
                        source.kind,
                        digest,
                    )
                    if observed != transition.owned_snapshot:
                        raise SourceDeletionError(
                            "source_transition_owned_entry_changed"
                        )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                os.fchmod(capsule.fd, 0)
                capsule.close()
                os.fchmod(quarantine.fd, 0)
        return active, transitions_by_digest, frozenset(consumed)

    def _load_intents(
        self,
        owner: _DirFd,
        operation_key: str,
        request: SourceDeletionRequest,
        expected: Mapping[str, _DeletionIntent],
    ) -> dict[str, _DeletionIntent]:
        directory = owner.open_dir()
        try:
            names = tuple(os.listdir(directory))
        finally:
            os.close(directory)
        expected_by_name = {
            self._intent_name(operation_key, index): expected[source.key]
            for index, source in enumerate(self._ordered_sources(request))
        }
        intents: dict[str, _DeletionIntent] = {}
        prefix = f"{operation_key}.intent."
        for name in names:
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            intent = expected_by_name.get(name)
            if intent is None:
                raise SourceDeletionError("deletion_intent_inventory_mismatch")
            raw = self._read_required(owner, name)
            try:
                document = json.loads(raw)
                expected_document = json.loads(intent.raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SourceDeletionError("deletion_intent_corrupt") from exc
            if (
                type(document) is not dict
                or canonical_json_bytes(document) != raw
                or set(document)
                != {
                    "authority_signature",
                    "operation_id",
                    "pre_rename_snapshot",
                    "quarantine_relative_path",
                    "request_digest",
                    "schema_version",
                    "source",
                }
                or {
                    key: document[key]
                    for key in expected_document
                }
                != expected_document
                or type(document["pre_rename_snapshot"]) is not dict
                or set(document["pre_rename_snapshot"])
                != {
                    "atime_ns",
                    "ctime_ns",
                    "device",
                    "gid",
                    "inode",
                    "kind",
                    "link_count",
                    "mode",
                    "mtime_ns",
                    "sha256",
                    "size_bytes",
                    "uid",
                }
            ):
                raise SourceDeletionError("deletion_intent_corrupt")
            unsigned = {
                key: value
                for key, value in document.items()
                if key != "authority_signature"
            }
            expected_signature = _HMAC_PREFIX + hmac.new(
                self._receipt_key,
                canonical_json_bytes(unsigned),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(
                document["authority_signature"],
                expected_signature,
            ):
                raise SourceDeletionError("deletion_intent_signature_invalid")
            values = document["pre_rename_snapshot"]
            assert isinstance(values, dict)
            try:
                snapshot = _SourceSnapshot(
                    device=int(values["device"]),
                    inode=int(values["inode"]),
                    ctime_ns=int(values["ctime_ns"]),
                    mode=int(values["mode"]),
                    uid=int(values["uid"]),
                    gid=int(values["gid"]),
                    link_count=int(values["link_count"]),
                    size_bytes=int(values["size_bytes"]),
                    atime_ns=int(values["atime_ns"]),
                    mtime_ns=int(values["mtime_ns"]),
                    kind=values["kind"],
                    sha256=values["sha256"],
                )
            except (TypeError, ValueError) as exc:
                raise SourceDeletionError("deletion_intent_snapshot_invalid") from exc
            if (
                snapshot.device != intent.source.device
                or snapshot.inode != intent.source.inode
                or snapshot.kind != intent.source.kind
                or snapshot.sha256 != intent.source.sha256
            ):
                raise SourceDeletionError("deletion_intent_snapshot_invalid")
            bound = _DeletionIntent(
                intent.source,
                intent.quarantine_relative_path,
                raw,
                snapshot,
            )
            intents[intent.source.key] = bound
        return intents

    def _open_capsule(
        self,
        quarantine: _DirFd,
        intent: _DeletionIntent,
        *,
        create: bool,
    ) -> _DirFd:
        name = intent.quarantine_relative_path
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=quarantine.fd)
                quarantine.fsync_dir()
            except FileExistsError:
                pass
        before = os.stat(name, dir_fd=quarantine.fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
        ):
            raise SourceDeletionError("deletion_capsule_authority_invalid")
        os.chmod(name, 0o700, dir_fd=quarantine.fd, follow_symlinks=False)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=quarantine.fd,
        )
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_gid != os.getegid()
        ):
            os.close(descriptor)
            raise SourceDeletionError("deletion_capsule_substituted")
        os.fchmod(descriptor, 0)
        os.fsync(descriptor)
        quarantine.fsync_dir()
        return _DirFd(descriptor, duplicate=False)

    @staticmethod
    def _capsule_contains_owned(capsule: _DirFd) -> bool:
        try:
            os.fchmod(capsule.fd, 0o700)
            return SourceDeletionGuard._exists(capsule, _ENTRY_NAME)
        finally:
            os.fchmod(capsule.fd, 0)

    def _quarantine_and_delete(
        self,
        request: SourceDeletionRequest,
        roots: dict[str, _DirFd],
        quarantines: dict[str, _DirFd],
        owner: _DirFd,
        quarantine: _DirFd,
        source: SourceOwnershipIdentity,
        intent: _DeletionIntent,
        receipt_owner: _DirFd,
        operation_key: str,
        gate_lease: FinalReceiptLease,
        ownership_lease: SourceOwnershipLease,
        recovered_from_intent: bool,
    ) -> None:
        parent, name = self._open_parent(owner, source.relative_path)
        capsule = self._open_capsule(quarantine, intent, create=True)
        descriptor = -1
        handed_off = False
        try:
            descriptor = self._open_source_at(parent, name, source.kind)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._verify_open_descriptor(
                descriptor,
                source,
                relaxed_directory=source.kind == "directory",
                recovery_snapshot=(
                    intent.pre_rename_snapshot if recovered_from_intent else None
                ),
            )
            snapshot = intent.pre_rename_snapshot
            if snapshot is None:
                raise SourceDeletionError("deletion_intent_snapshot_missing")
            before_opened = os.fstat(descriptor)
            before_named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            before_tuple = (
                before_opened.st_dev,
                before_opened.st_ino,
                before_opened.st_ctime_ns,
                before_opened.st_mode,
                before_opened.st_uid,
                before_opened.st_gid,
                before_opened.st_nlink,
                before_opened.st_size,
                before_opened.st_atime_ns,
                before_opened.st_mtime_ns,
            )
            expected_before = (
                snapshot.device,
                snapshot.inode,
                snapshot.ctime_ns,
                snapshot.mode,
                snapshot.uid,
                snapshot.gid,
                snapshot.link_count,
                snapshot.size_bytes,
                snapshot.atime_ns,
                snapshot.mtime_ns,
            )
            named_before = (
                before_named.st_dev,
                before_named.st_ino,
                before_named.st_ctime_ns,
                before_named.st_mode,
                before_named.st_uid,
                before_named.st_gid,
                before_named.st_nlink,
                before_named.st_size,
                before_named.st_atime_ns,
                before_named.st_mtime_ns,
            )
            if recovered_from_intent:
                before_stable = before_tuple[:2] + before_tuple[3:]
                named_stable = named_before[:2] + named_before[3:]
                expected_stable = expected_before[:2] + expected_before[3:]
                invalid_before = (
                    before_stable != expected_stable
                    or named_stable != expected_stable
                    or before_tuple[2] != named_before[2]
                    or before_tuple[2] < expected_before[2]
                )
            else:
                invalid_before = (
                    before_tuple != expected_before
                    or named_before != expected_before
                )
            if invalid_before:
                raise SourceDeletionError(
                    f"source_pre_rename_metadata_drift:{source.key}"
                )
            os.fchmod(capsule.fd, 0o300)
            self._rename_noreplace(parent, name, capsule.fd, _ENTRY_NAME)
            after_opened = os.fstat(descriptor)
            after_named = os.stat(
                _ENTRY_NAME,
                dir_fd=capsule.fd,
                follow_symlinks=False,
            )
            after_stable = (
                after_opened.st_dev,
                after_opened.st_ino,
                after_opened.st_mode,
                after_opened.st_uid,
                after_opened.st_gid,
                after_opened.st_nlink,
                after_opened.st_size,
                after_opened.st_atime_ns,
                after_opened.st_mtime_ns,
            )
            named_stable = (
                after_named.st_dev,
                after_named.st_ino,
                after_named.st_mode,
                after_named.st_uid,
                after_named.st_gid,
                after_named.st_nlink,
                after_named.st_size,
                after_named.st_atime_ns,
                after_named.st_mtime_ns,
            )
            if (
                after_stable != snapshot.stable_tuple()
                or named_stable != snapshot.stable_tuple()
                or after_opened.st_ctime_ns < snapshot.ctime_ns
                or after_named.st_ctime_ns != after_opened.st_ctime_ns
            ):
                raise SourceDeletionError(
                    f"source_rename_transition_invalid:{source.key}"
                )
            os.fsync(parent)
            os.fsync(capsule.fd)
            self._verify_quarantined_descriptor(
                capsule.fd,
                _ENTRY_NAME,
                descriptor,
                source,
            )
            post_rename_snapshot = _SourceSnapshot(
                after_opened.st_dev,
                after_opened.st_ino,
                after_opened.st_ctime_ns,
                after_opened.st_mode,
                after_opened.st_uid,
                after_opened.st_gid,
                after_opened.st_nlink,
                after_opened.st_size,
                after_opened.st_atime_ns,
                after_opened.st_mtime_ns,
                source.kind,
                source.sha256,
            )
            transition_name, transition_raw = self._transition_record(
                operation_key,
                _sha256(canonical_json_bytes(request.projection())),
                source,
                intent,
                quarantine,
                capsule,
                post_rename_snapshot,
            )
            self._write_once(receipt_owner, transition_name, transition_raw)
            private_metadata = os.fstat(quarantine.fd)
            capsule_metadata = os.fstat(capsule.fd)
            transition = _PostRenameTransition(
                source.key,
                (private_metadata.st_dev, private_metadata.st_ino),
                (capsule_metadata.st_dev, capsule_metadata.st_ino),
                post_rename_snapshot,
                transition_raw,
            )
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            descriptor = -1
            handed_off = True
            self._run_delete_helper(
                request,
                roots,
                quarantines,
                capsule,
                quarantine,
                intent,
                source,
                receipt_owner,
                operation_key,
                ownership_lease,
                gate_lease,
                transition,
            )
        except BlockingIOError as exc:
            raise SourceDeletionError(
                f"source_exclusive_lease_conflict:{source.key}"
            ) from exc
        except Exception:
            if not handed_off:
                os.fchmod(capsule.fd, 0o700)
                if self._exists(capsule, _ENTRY_NAME):
                    self._restore_quarantine(capsule.fd, _ENTRY_NAME, parent, name)
            raise
        finally:
            if not handed_off:
                capsule.close()
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            os.close(parent)

    def _delete_quarantined(
        self,
        request: SourceDeletionRequest,
        roots: dict[str, _DirFd],
        quarantines: dict[str, _DirFd],
        quarantine: _DirFd,
        source: SourceOwnershipIdentity,
        intent: _DeletionIntent,
        receipt_owner: _DirFd,
        operation_key: str,
        gate_lease: FinalReceiptLease,
        ownership_lease: SourceOwnershipLease,
        transition: _PostRenameTransition,
    ) -> None:
        capsule = self._open_capsule(quarantine, intent, create=False)
        descriptor = -1
        handed_off = False
        try:
            os.fchmod(capsule.fd, 0o300)
            descriptor = self._open_source_at(capsule.fd, _ENTRY_NAME, source.kind)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._verify_quarantined_descriptor(
                capsule.fd,
                _ENTRY_NAME,
                descriptor,
                source,
            )
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            descriptor = -1
            handed_off = True
            self._run_delete_helper(
                request,
                roots,
                quarantines,
                capsule,
                quarantine,
                intent,
                source,
                receipt_owner,
                operation_key,
                ownership_lease,
                gate_lease,
                transition,
            )
        except BlockingIOError as exc:
            raise SourceDeletionError(
                f"source_exclusive_lease_conflict:{source.key}"
            ) from exc
        finally:
            if not handed_off:
                capsule.close()
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @staticmethod
    def _descriptor_path(descriptor: int) -> bytes | None:
        if sys.platform != "darwin":
            return None
        command = getattr(fcntl, "F_GETPATH", None)
        if command is None:
            raise SourceDeletionError("descriptor_path_authority_unavailable")
        raw = fcntl.fcntl(descriptor, command, b"\0" * 1024)
        if type(raw) is not bytes:
            raise SourceDeletionError("descriptor_path_authority_invalid")
        path = raw.split(b"\0", 1)[0]
        if not path or not path.startswith(b"/"):
            raise SourceDeletionError("descriptor_path_authority_invalid")
        return path

    @staticmethod
    def _verify_retained_after_helper(
        descriptor: int,
        descriptor_path: bytes | None,
        expectation: _HelperExpectation,
    ) -> None:
        metadata = os.fstat(descriptor)
        if (
            metadata.st_dev != expectation.device
            or metadata.st_ino != expectation.inode
            or metadata.st_mode != expectation.mode
            or metadata.st_uid != expectation.uid
            or metadata.st_gid != expectation.gid
            or metadata.st_ctime_ns < expectation.ctime_ns
        ):
            raise SourceDeletionError("retained_source_metadata_changed")
        if expectation.kind == "file" or sys.platform.startswith("linux"):
            if metadata.st_nlink != 0:
                raise SourceDeletionError("retained_source_link_survived")
            return
        if sys.platform != "darwin":
            raise SourceDeletionError("directory_link_semantics_unsupported")
        if (
            descriptor_path is None
            or SourceDeletionGuard._descriptor_path(descriptor) != descriptor_path
        ):
            raise SourceDeletionError("retained_directory_path_changed")


    def _capture_helper_expectation(
        self,
        capsule: _DirFd,
        source: SourceOwnershipIdentity,
    ) -> tuple[_HelperExpectation, int, bytes | None]:
        descriptor = -1
        locked = False
        retained_path: bytes | None = None
        try:
            os.fchmod(capsule.fd, 0o300)
            descriptor = self._open_source_at(capsule.fd, _ENTRY_NAME, source.kind)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            self._verify_quarantined_descriptor(
                capsule.fd,
                _ENTRY_NAME,
                descriptor,
                source,
            )
            opened = os.fstat(descriptor)
            named = os.stat(_ENTRY_NAME, dir_fd=capsule.fd, follow_symlinks=False)
            exact_fields = (
                "st_dev",
                "st_ino",
                "st_ctime_ns",
                "st_atime_ns",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_mtime_ns",
            )
            if any(getattr(opened, field) != getattr(named, field) for field in exact_fields):
                raise SourceDeletionError(f"quarantine_metadata_mismatch:{source.key}")
            expectation = _HelperExpectation(
                device=opened.st_dev,
                inode=opened.st_ino,
                ctime_ns=opened.st_ctime_ns,
                atime_ns=opened.st_atime_ns,
                mode=opened.st_mode,
                uid=opened.st_uid,
                gid=opened.st_gid,
                link_count=opened.st_nlink,
                mtime_ns=opened.st_mtime_ns,
                size_bytes=opened.st_size,
                sha256=source.sha256,
                kind=source.kind,
            )
            retained_path = self._descriptor_path(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            locked = False
            retained_descriptor = descriptor
            descriptor = -1
            return expectation, retained_descriptor, retained_path
        finally:
            if descriptor >= 0:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            os.fchmod(capsule.fd, 0)
            os.fsync(capsule.fd)

    def _run_delete_helper(
        self,
        request: SourceDeletionRequest,
        roots: dict[str, _DirFd],
        quarantines: dict[str, _DirFd],
        capsule: _DirFd,
        quarantine: _DirFd,
        intent: _DeletionIntent,
        source: SourceOwnershipIdentity,
        receipt_owner: _DirFd,
        operation_key: str,
        ownership_lease: SourceOwnershipLease,
        gate_lease: FinalReceiptLease,
        transition: _PostRenameTransition,
    ) -> None:
        expectation, retained_descriptor, retained_path = (
            self._capture_helper_expectation(capsule, source)
        )
        capsule_metadata = os.fstat(capsule.fd)
        capsule_identity = (capsule_metadata.st_dev, capsule_metadata.st_ino)
        request_digest = _sha256(canonical_json_bytes(request.projection()))
        success, success_document = self._helper_success_for(
            request,
            request_digest,
            operation_key,
            source,
            roots[source.root_authority_id],
            quarantine,
            capsule,
            intent,
            transition,
        )
        request_raw = expectation.request_bytes(
            success.name,
            success_document,
        )
        if len(request_raw) > _HELPER_MAX_BYTES:
            raise SourceDeletionError("deletion_helper_request_too_large")

        namespace_suspended = False
        broker_prepared = False
        child_raw = b""
        child_status = -1
        retained_error: SourceDeletionError | None = None
        try:
            ownership_lease.assert_current()
            gate_lease.assert_current()
            self._assert_helper_capability()
            self._broker.prepare(capsule.fd, request_raw)
            broker_prepared = True
            capsule.close()
            self._suspend_namespace_authorities(roots, quarantines)
            namespace_suspended = True
            if any(owner.fd >= 0 for owner in (*roots.values(), *quarantines.values())):
                raise SourceDeletionError("deletion_namespace_authority_retained")
            child_raw, child_status = self._broker.start()
            broker_prepared = False
        except SourceDeletionError as exc:
            self._block_operation(
                receipt_owner,
                operation_key,
                source,
                str(exc),
            )
        except BaseException:
            self._block_operation(
                receipt_owner,
                operation_key,
                source,
                "isolated_delete_helper_handoff_failed",
            )
        finally:
            try:
                if broker_prepared:
                    self._broker.abort()
                capsule.close()
                if namespace_suspended:
                    try:
                        self._restore_namespace_authorities(
                            request,
                            roots,
                            quarantines,
                            operation_key,
                        )
                    except BaseException:
                        self._block_operation(
                            receipt_owner,
                            operation_key,
                            source,
                            "deletion_namespace_authority_reopen_failed",
                        )
            finally:
                if retained_descriptor >= 0:
                    try:
                        if (
                            os.WIFEXITED(child_status)
                            and os.WEXITSTATUS(child_status) == 0
                        ):
                            self._validate_helper_result(
                                child_raw,
                                expectation,
                                success,
                            )
                            self._verify_retained_after_helper(
                                retained_descriptor,
                                retained_path,
                                expectation,
                            )
                    except SourceDeletionError as exc:
                        retained_error = exc
                    finally:
                        os.close(retained_descriptor)
                        retained_descriptor = -1

        if retained_error is not None:
            self._block_operation(
                receipt_owner,
                operation_key,
                source,
                str(retained_error),
            )

        ownership_lease.assert_current()
        gate_lease.assert_current()
        if not os.WIFEXITED(child_status) or os.WEXITSTATUS(child_status) != 0:
            reopened = self._open_capsule(quarantine, intent, create=False)
            try:
                reopened_metadata = os.fstat(reopened.fd)
                if (
                    reopened_metadata.st_dev,
                    reopened_metadata.st_ino,
                ) != capsule_identity:
                    self._block_operation(
                        receipt_owner,
                        operation_key,
                        source,
                        "isolated_delete_helper_capsule_substituted",
                    )
                success_installed = (
                    self._read_helper_success(reopened, success) is not None
                )
                os.fchmod(reopened.fd, 0o500)
                entries = os.listdir(reopened.fd)
                if not success_installed and entries == [_ENTRY_NAME]:
                    root = roots[source.root_authority_id]
                    if not self._exists(root, source.relative_path):
                        parent, name = self._open_parent(root, source.relative_path)
                        try:
                            transition_consumed_name, transition_consumed_raw = (
                                self._transition_consumed_record(
                                    operation_key,
                                    source,
                                    transition,
                                )
                            )
                            self._write_once(
                                receipt_owner,
                                transition_consumed_name,
                                transition_consumed_raw,
                            )
                            recovery_name, recovery_raw = self._recovery_record(
                                operation_key,
                                source,
                                intent,
                                consumed=False,
                            )
                            self._write_once(
                                receipt_owner,
                                recovery_name,
                                recovery_raw,
                            )
                            os.fchmod(reopened.fd, 0o300)
                            self._restore_quarantine(
                                reopened.fd,
                                _ENTRY_NAME,
                                parent,
                                name,
                            )
                        finally:
                            os.close(parent)
                    self._block_operation(
                        receipt_owner,
                        operation_key,
                        source,
                        "isolated_delete_helper_failed",
                    )
                if not success_installed:
                    reason = (
                        "isolated_delete_helper_success_missing_after_delete"
                        if entries == []
                        else "isolated_delete_helper_capsule_invalid"
                    )
                    self._block_operation(
                        receipt_owner,
                        operation_key,
                        source,
                        reason,
                    )
            finally:
                os.fchmod(reopened.fd, 0)
                reopened.close()
            if self._exists(
                roots[source.root_authority_id],
                source.relative_path,
            ):
                self._block_operation(
                    receipt_owner,
                    operation_key,
                    source,
                    "isolated_delete_helper_failed",
                )
            raise SourceDeletionError(
                f"deletion_helper_result_lost_after_delete:{source.key}"
            )
        try:
            self._validate_helper_result(child_raw, expectation, success)
        except SourceDeletionError:
            self._block_operation(
                receipt_owner,
                operation_key,
                source,
                "isolated_delete_helper_protocol_invalid",
            )

        reopened = self._open_capsule(quarantine, intent, create=False)
        try:
            reopened_metadata = os.fstat(reopened.fd)
            if (
                reopened_metadata.st_dev,
                reopened_metadata.st_ino,
            ) != capsule_identity:
                raise SourceDeletionError("deletion_capsule_substituted")
            os.fchmod(reopened.fd, 0o500)
            try:
                if self._read_helper_success(reopened, success) is None:
                    self._block_operation(
                        receipt_owner,
                        operation_key,
                        source,
                        "isolated_delete_helper_success_missing",
                    )
            finally:
                os.fchmod(reopened.fd, 0)
            os.fsync(reopened.fd)
            quarantine.fsync_dir()
            ownership_lease.assert_current()
            gate_lease.assert_current()
            consumed_name, consumed_raw = self._transition_consumed_record(
                operation_key,
                source,
                transition,
            )
            self._write_once(receipt_owner, consumed_name, consumed_raw)
        finally:
            os.fchmod(reopened.fd, 0)
            reopened.close()

    @staticmethod
    def _suspend_namespace_authorities(
        roots: Mapping[str, _DirFd],
        quarantines: Mapping[str, _DirFd],
    ) -> None:
        for owner in (*quarantines.values(), *roots.values()):
            owner.close()


    def _restore_namespace_authorities(
        self,
        request: SourceDeletionRequest,
        roots: dict[str, _DirFd],
        quarantines: dict[str, _DirFd],
        operation_key: str,
    ) -> None:
        replacements = self._open_request_roots(request)
        replacement_quarantines: dict[str, _DirFd] = {}
        try:
            replacement_quarantines = self._open_private_quarantines(
                request,
                replacements,
                operation_key,
            )
            if set(replacements) != set(roots) or set(replacement_quarantines) != set(
                quarantines
            ):
                raise SourceDeletionError("deletion_namespace_inventory_changed")
            for authority_id, owner in roots.items():
                replacement = replacements[authority_id]
                owner.fd = replacement.fd
                replacement.fd = -1
            for authority_id, owner in quarantines.items():
                replacement = replacement_quarantines[authority_id]
                owner.fd = replacement.fd
                os.fchmod(owner.fd, 0o300)
                replacement.fd = -1
        finally:
            for owner in (*replacement_quarantines.values(), *replacements.values()):
                owner.close()

    @staticmethod
    def _forked_helper_child(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        expected_capability_digest: bytes,
    ) -> None:
        exit_code = 1
        try:
            copied = [
                fcntl.fcntl(
                    descriptor,
                    getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD),
                    6,
                )
                for descriptor in (capsule_fd, start_fd, result_fd)
            ]
            for copied_fd, target_fd in zip(copied, (3, 4, 5), strict=True):
                os.dup2(copied_fd, target_fd, inheritable=False)
            soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
            open_max = int(os.sysconf("SC_OPEN_MAX"))
            finite_limits = [
                int(limit)
                for limit in (soft_limit, hard_limit)
                if limit != resource.RLIM_INFINITY
            ]
            child_fd_limit = max(6, open_max, *finite_limits)
            os.closerange(6, child_fd_limit)
            for descriptor in (0, 1, 2):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if os.read(4, 2) != b"\x01" or os.read(4, 1) != b"":
                raise RuntimeError("helper_start_protocol_invalid")
            os.close(4)
            if (
                type(expected_capability_digest) is not bytes
                or not hmac.compare_digest(
                    expected_capability_digest,
                    _helper_semantics_digest(),
                )
            ):
                raise RuntimeError("helper_capability_changed")
            result = _deletion_helper.delete_capsule(3, request_raw)
            if type(result) is not bytes or not result or len(result) > _HELPER_MAX_BYTES:
                raise RuntimeError("helper_result_size_invalid")
            view = memoryview(result)
            while view:
                written = os.write(5, view)
                if written <= 0:
                    raise RuntimeError("helper_result_write_failed")
                view = view[written:]
            exit_code = 0
        except BaseException:
            pass
        finally:
            for descriptor in (3, 4, 5):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            os._exit(exit_code)

    @staticmethod
    def _write_child_result(descriptor: int, raw: bytes) -> None:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("helper_result_write_failed")
            view = view[written:]

    @classmethod
    def _read_fork_result(cls, pid: int, descriptor: int) -> tuple[bytes, int]:
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + _HELPER_TIMEOUT_SECONDS
        result = bytearray()
        status: int | None = None
        eof = False
        while status is None or not eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cls._kill_and_reap(pid)
                raise SourceDeletionError("isolated_delete_helper_timeout")
            readable, _, _ = select.select(
                [descriptor] if not eof else [],
                [],
                [],
                min(remaining, 0.05),
            )
            if readable:
                try:
                    chunk = os.read(
                        descriptor,
                        min(1024, _HELPER_MAX_BYTES + 1 - len(result)),
                    )
                except BlockingIOError:
                    chunk = None
                if chunk == b"":
                    eof = True
                elif chunk:
                    result.extend(chunk)
                    if len(result) > _HELPER_MAX_BYTES:
                        cls._kill_and_reap(pid)
                        raise SourceDeletionError(
                            "isolated_delete_helper_output_too_large"
                        )
            if status is None:
                waited, observed_status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    status = observed_status
        assert status is not None
        return bytes(result), status

    @staticmethod
    def _kill_and_reap(pid: int) -> None:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    def _validate_helper_result(
        self,
        raw: bytes,
        expectation: _HelperExpectation,
        success: _HelperSuccess,
    ) -> None:
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceDeletionError("isolated_delete_helper_protocol_invalid") from exc
        if (
            type(document) is not dict
            or set(document)
            != {
                "capsule_entries",
                "device",
                "gid",
                "inode",
                "kind",
                "link_count",
                "observed_inode_link_count",
                "mode",
                "parent_name_absent",
                "prior_ctime_ns",
                "prior_link_count",
                "schema_version",
                "status",
                "success_record_digest",
                "success_record_name",
                "uid",
            }
            or canonical_json_bytes(document) != raw
            or document["schema_version"] != _HELPER_RESULT_SCHEMA
            or document["status"] != "deleted"
            or document["capsule_entries"] != []
            or document["parent_name_absent"] is not True
            or document["device"] != str(expectation.device)
            or document["inode"] != str(expectation.inode)
            or document["kind"] != expectation.kind
            or document["mode"] != str(expectation.mode)
            or document["uid"] != str(expectation.uid)
            or document["gid"] != str(expectation.gid)
            or document["prior_ctime_ns"] != str(expectation.ctime_ns)
            or document["prior_link_count"] != str(expectation.link_count)
            or document["success_record_name"] != success.name
            or document["success_record_digest"] != _sha256(success.raw)
            or document["link_count"] != "0"
            or type(document["observed_inode_link_count"]) is not str
            or not document["observed_inode_link_count"].isascii()
            or not document["observed_inode_link_count"].isdecimal()
            or (
                expectation.kind == "file"
                and document["observed_inode_link_count"] != "0"
            )
            or (
                expectation.kind == "directory"
                and sys.platform.startswith("linux")
                and document["observed_inode_link_count"] != "0"
            )
        ):
            raise SourceDeletionError("isolated_delete_helper_protocol_invalid")

    def _verify_quarantine(
        self, owner: _DirFd, source: SourceOwnershipIdentity, intent: _DeletionIntent
    ) -> None:
        capsule = self._open_capsule(owner, intent, create=False)
        descriptor = -1
        try:
            if not self._capsule_contains_owned(capsule):
                return
            os.fchmod(capsule.fd, 0o700)
            descriptor = self._open_source_at(capsule.fd, _ENTRY_NAME, source.kind)
            self._verify_quarantined_descriptor(
                capsule.fd,
                _ENTRY_NAME,
                descriptor,
                source,
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.fchmod(capsule.fd, 0)
            capsule.close()

    def _verify_quarantined_descriptor(
        self, parent: int, name: str, descriptor: int, source: SourceOwnershipIdentity
    ) -> None:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        exact_fields = (
            "st_dev",
            "st_ino",
            "st_ctime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
        )
        if (
            opened.st_dev != source.device
            or opened.st_ino != source.inode
            or named.st_dev != source.device
            or named.st_ino != source.inode
            or any(getattr(opened, field) != getattr(named, field) for field in exact_fields)
        ):
            raise SourceDeletionError(f"quarantine_identity_mismatch:{source.key}")
        expected_kind = (
            stat.S_ISREG(opened.st_mode)
            if source.kind == "file"
            else stat.S_ISDIR(opened.st_mode)
        )
        named_kind = (
            stat.S_ISREG(named.st_mode)
            if source.kind == "file"
            else stat.S_ISDIR(named.st_mode)
        )
        if (
            not expected_kind
            or not named_kind
            or stat.S_ISLNK(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
        ):
            raise SourceDeletionError(f"source_kind_or_symlink_drift:{source.key}")
        if source.kind == "file":
            if opened.st_nlink != 1:
                raise SourceDeletionError(f"source_hardlink_drift:{source.key}")
            if (
                opened.st_size != source.size_bytes
                or self._file_digest(descriptor) != source.sha256
            ):
                raise SourceDeletionError(
                    f"source_identity_or_digest_drift:{source.key}"
                )
        elif opened.st_nlink < 2 or os.listdir(descriptor):
            raise SourceDeletionError(f"owned_directory_not_empty:{source.key}")


    def _block_operation(
        self,
        owner: _DirFd,
        operation_key: str,
        source: SourceOwnershipIdentity,
        reason: str,
    ) -> None:
        raw = canonical_json_bytes(
            {
                "reason": reason,
                "schema_version": "bb.rl.g4.source-deletion-blocked.v1",
                "source": _identity_projection(source),
            }
        )
        self._write_once(owner, f"{operation_key}.blocked.json", raw)
        raise SourceDeletionError(f"source_deletion_blocked:{reason}:{source.key}")

    def _restore_quarantine(
        self,
        quarantine_parent: int,
        quarantine: str,
        source_parent: int,
        original: str,
    ) -> None:
        try:
            self._rename_noreplace(
                quarantine_parent,
                quarantine,
                source_parent,
                original,
            )
            os.fsync(quarantine_parent)
            os.fsync(source_parent)
        except FileExistsError as exc:
            raise SourceDeletionError("quarantine_restore_destination_occupied") from exc

    def _verify_source(
        self,
        owner: _DirFd,
        source: SourceOwnershipIdentity,
        *,
        relaxed_directory: bool,
        recovery_snapshot: _SourceSnapshot | None = None,
    ) -> None:
        parent, name = self._open_parent(owner, source.relative_path)
        descriptor = -1
        try:
            descriptor = self._open_source_at(parent, name, source.kind)
            self._verify_open_descriptor(
                descriptor,
                source,
                relaxed_directory=relaxed_directory,
                recovery_snapshot=recovery_snapshot,
            )
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(descriptor)
            if named.st_dev != opened.st_dev or named.st_ino != opened.st_ino:
                raise SourceDeletionError(f"source_namespace_identity_drift:{source.key}")
        except FileNotFoundError as exc:
            raise SourceDeletionError(f"owned_source_missing:{source.key}") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise SourceDeletionError(
                    f"source_kind_or_symlink_drift:{source.key}"
                ) from exc
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def _verify_open_descriptor(
        self,
        descriptor: int,
        source: SourceOwnershipIdentity,
        *,
        relaxed_directory: bool,
        recovery_snapshot: _SourceSnapshot | None = None,
    ) -> None:
        metadata = os.fstat(descriptor)
        expected_kind = (
            stat.S_ISREG(metadata.st_mode)
            if source.kind == "file"
            else stat.S_ISDIR(metadata.st_mode)
        )
        if not expected_kind or stat.S_ISLNK(metadata.st_mode):
            raise SourceDeletionError(f"source_kind_or_symlink_drift:{source.key}")
        if metadata.st_dev != source.device or metadata.st_ino != source.inode:
            raise SourceDeletionError(f"source_inode_drift:{source.key}")
        if source.kind == "file":
            if metadata.st_nlink != 1:
                raise SourceDeletionError(f"source_hardlink_drift:{source.key}")
            digest = self._file_digest(descriptor)
        else:
            digest = self._directory_digest(descriptor)
        if recovery_snapshot is not None and (
            metadata.st_dev != recovery_snapshot.device
            or metadata.st_ino != recovery_snapshot.inode
            or metadata.st_ctime_ns < recovery_snapshot.ctime_ns
            or metadata.st_mode != recovery_snapshot.mode
            or metadata.st_uid != recovery_snapshot.uid
            or metadata.st_gid != recovery_snapshot.gid
            or metadata.st_nlink != recovery_snapshot.link_count
            or metadata.st_size != recovery_snapshot.size_bytes
            or metadata.st_atime_ns != recovery_snapshot.atime_ns
            or metadata.st_mtime_ns != recovery_snapshot.mtime_ns
            or digest != recovery_snapshot.sha256
        ):
            raise SourceDeletionError(f"source_recovery_identity_drift:{source.key}")
        if (
            recovery_snapshot is None
            and not relaxed_directory
            and (
                metadata.st_ctime_ns != source.ctime_ns
                or metadata.st_size != source.size_bytes
                or digest != source.sha256
            )
        ):
            raise SourceDeletionError(f"source_identity_or_digest_drift:{source.key}")

    @staticmethod
    def _open_source_at(parent: int, name: str, kind: SourceKind) -> int:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if kind == "directory":
            flags |= getattr(os, "O_DIRECTORY", 0)
        return os.open(name, flags, dir_fd=parent)

    def _absence_proof(
        self, source: SourceOwnershipIdentity, owner: _DirFd, observed_at: str
    ) -> SourceAbsenceProof:
        anchor = self._absence_anchor(owner, source.relative_path, source.key)
        return SourceAbsenceProof(
            root_authority_id=source.root_authority_id,
            root_path=source.root_path,
            relative_path=source.relative_path,
            prior_device=source.device,
            prior_inode=source.inode,
            prior_ctime_ns=source.ctime_ns,
            prior_size_bytes=source.size_bytes,
            prior_sha256=source.sha256,
            prior_kind=source.kind,
            absence_anchor_relative_path=anchor.relative_path,
            anchor_device=anchor.metadata.st_dev,
            anchor_inode=anchor.metadata.st_ino,
            observed_at=observed_at,
        )

    def _validate_receipt(
        self,
        request: SourceDeletionRequest,
        request_digest: str,
        receipt: SourceDeletionReceipt,
        roots: Mapping[str, _DirFd],
        completion_raw: bytes,
    ) -> None:
        expected_keys = tuple(source.key for source in request.owned_sources)
        disposition = (*receipt.deleted, *receipt.already_absent)
        if (
            receipt.operation_id != request.operation_id
            or receipt.request_digest != request_digest
            or receipt.completion_digest != _sha256(completion_raw)
            or len(disposition) != len(expected_keys)
            or set(disposition) != set(expected_keys)
            or len(receipt.absence_proofs) != len(expected_keys)
            or tuple(proof.key for proof in receipt.absence_proofs) != expected_keys
            or not hmac.compare_digest(
                receipt.authority_signature,
                self._sign_receipt(receipt.unsigned_projection()),
            )
        ):
            raise SourceDeletionError("deletion_receipt_schema_or_partition_invalid")
        completion = self._parse_completion(completion_raw)
        if (
            tuple(completion["deleted"]) != receipt.deleted
            or tuple(completion["already_absent"]) != receipt.already_absent
            or completion["completed_at"] != receipt.completed_at
            or tuple(
                self._proof_from_object(item) for item in completion["absence_proofs"]
            )
            != receipt.absence_proofs
        ):
            raise SourceDeletionError("deletion_receipt_completion_mismatch")
        by_key = {source.key: source for source in request.owned_sources}
        for proof in receipt.absence_proofs:
            source = by_key[proof.key]
            if (
                proof.root_path != source.root_path
                or proof.prior_device != source.device
                or proof.prior_inode != source.inode
                or proof.prior_ctime_ns != source.ctime_ns
                or proof.prior_size_bytes != source.size_bytes
                or proof.prior_sha256 != source.sha256
                or proof.prior_kind != source.kind
                or proof.observed_at != receipt.completed_at
            ):
                raise SourceDeletionError("deletion_receipt_absence_identity_mismatch")
            anchor = self._absence_anchor(roots[source.root_authority_id], source.relative_path, source.key)
            if (
                anchor.relative_path != proof.absence_anchor_relative_path
                or anchor.metadata.st_dev != proof.anchor_device
                or anchor.metadata.st_ino != proof.anchor_inode
            ):
                raise SourceDeletionError(f"absence_anchor_substituted:{source.key}")

    def _validate_completion(
        self,
        request: SourceDeletionRequest,
        request_digest: str,
        raw: bytes,
        roots: Mapping[str, _DirFd],
    ) -> dict[str, object]:
        completion = self._parse_completion(raw)
        expected_keys = tuple(source.key for source in request.owned_sources)
        deleted = tuple(completion["deleted"])
        already_absent = tuple(completion["already_absent"])
        proofs = tuple(
            self._proof_from_object(item) for item in completion["absence_proofs"]
        )
        if (
            completion["operation_id"] != request.operation_id
            or completion["request_digest"] != request_digest
            or len((*deleted, *already_absent)) != len(expected_keys)
            or set((*deleted, *already_absent)) != set(expected_keys)
            or tuple(proof.key for proof in proofs) != expected_keys
        ):
            raise SourceDeletionError("deletion_completion_schema_or_partition_invalid")
        by_key = {source.key: source for source in request.owned_sources}
        for proof in proofs:
            source = by_key[proof.key]
            if (
                proof.root_path != source.root_path
                or proof.prior_device != source.device
                or proof.prior_inode != source.inode
                or proof.prior_ctime_ns != source.ctime_ns
                or proof.prior_size_bytes != source.size_bytes
                or proof.prior_sha256 != source.sha256
                or proof.prior_kind != source.kind
                or proof.observed_at != completion["completed_at"]
            ):
                raise SourceDeletionError("deletion_completion_absence_identity_mismatch")
            anchor = self._absence_anchor(
                roots[source.root_authority_id],
                source.relative_path,
                source.key,
            )
            if (
                anchor.relative_path != proof.absence_anchor_relative_path
                or anchor.metadata.st_dev != proof.anchor_device
                or anchor.metadata.st_ino != proof.anchor_inode
            ):
                raise SourceDeletionError(f"absence_anchor_substituted:{source.key}")
        return completion

    def _parse_receipt(self, raw: bytes) -> SourceDeletionReceipt:
        try:
            document = json.loads(raw)
            keys = {
                "absence_proofs",
                "already_absent",
                "authority_signature",
                "completed_at",
                "completion_digest",
                "deleted",
                "operation_id",
                "request_digest",
                "schema_version",
            }
            if type(document) is not dict or set(document) != keys or document["schema_version"] != _RECEIPT_SCHEMA:
                raise ValueError
            if canonical_json_bytes(document) != raw:
                raise ValueError
            proofs = tuple(self._proof_from_object(item) for item in document["absence_proofs"])
            return SourceDeletionReceipt(
                operation_id=document["operation_id"],
                request_digest=document["request_digest"],
                deleted=tuple(document["deleted"]),
                already_absent=tuple(document["already_absent"]),
                completion_digest=document["completion_digest"],
                absence_proofs=proofs,
                completed_at=document["completed_at"],
                authority_signature=document["authority_signature"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceDeletionError("deletion_receipt_corrupt") from exc

    def _parse_completion(self, raw: bytes) -> dict[str, object]:
        try:
            document = json.loads(raw)
            keys = {
                "absence_proofs",
                "already_absent",
                "completed_at",
                "deleted",
                "operation_id",
                "request_digest",
                "schema_version",
            }
            if type(document) is not dict or set(document) != keys or document["schema_version"] != _COMPLETION_SCHEMA:
                raise ValueError
            if canonical_json_bytes(document) != raw:
                raise ValueError
            tuple(self._proof_from_object(item) for item in document["absence_proofs"])
            return document
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceDeletionError("deletion_completion_corrupt") from exc

    @staticmethod
    def _proof_from_object(item: object) -> SourceAbsenceProof:
        keys = {
            "absence_anchor_relative_path",
            "anchor_device",
            "anchor_inode",
            "observed_at",
            "prior_ctime_ns",
            "prior_device",
            "prior_inode",
            "prior_kind",
            "prior_sha256",
            "prior_size_bytes",
            "relative_path",
            "root_authority_id",
            "root_path",
        }
        if type(item) is not dict or set(item) != keys:
            raise ValueError("absence_proof_schema_invalid")
        return SourceAbsenceProof(
            root_authority_id=item["root_authority_id"],
            root_path=item["root_path"],
            relative_path=item["relative_path"],
            prior_device=int(item["prior_device"]),
            prior_inode=int(item["prior_inode"]),
            prior_ctime_ns=int(item["prior_ctime_ns"]),
            prior_size_bytes=int(item["prior_size_bytes"]),
            prior_sha256=item["prior_sha256"],
            prior_kind=item["prior_kind"],
            absence_anchor_relative_path=item["absence_anchor_relative_path"],
            anchor_device=int(item["anchor_device"]),
            anchor_inode=int(item["anchor_inode"]),
            observed_at=item["observed_at"],
        )

    def _sign_receipt(self, unsigned: Mapping[str, object]) -> str:
        return _HMAC_PREFIX + hmac.new(
            self._receipt_key, canonical_json_bytes(dict(unsigned)), hashlib.sha256
        ).hexdigest()

    def _read_immutable_receipt(self, ref: SourceDeletionGateReceipt) -> bytes:
        try:
            descriptor = self._open_file(ref.path)
        except OSError as exc:
            raise SourceDeletionError("gate_receipt_unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > _MAX_RECORD_BYTES:
                raise SourceDeletionError("gate_receipt_storage_invalid")
            raw = self._read_all(descriptor, _MAX_RECORD_BYTES)
            if self._file_identity(metadata) != self._file_identity(os.fstat(descriptor)):
                raise SourceDeletionError("gate_receipt_changed_while_reading")
        finally:
            os.close(descriptor)
        if _sha256(raw) != ref.sha256:
            raise SourceDeletionError("gate_receipt_digest_mismatch")
        try:
            document = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SourceDeletionError("gate_receipt_not_canonical_json") from exc
        if type(document) is not dict or canonical_json_bytes(document) != raw:
            raise SourceDeletionError("gate_receipt_not_canonical_json")
        return raw

    def _open_receipt_root(self) -> _DirFd:
        descriptor = self._open_directory(os.fspath(self._receipt_root))
        metadata = os.fstat(descriptor)
        if (
            self._directory_identity(metadata) != self._receipt_identity
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
        ):
            os.close(descriptor)
            raise SourceDeletionError("receipt_root_substituted")
        return _DirFd(descriptor, duplicate=False)

    @staticmethod
    def _cleanup_operation_temps(owner: _DirFd, operation_key: str) -> None:
        grammar = re.compile(
            r"^\."
            + re.escape(operation_key)
            + r"\.(?:request|preflight|completion|receipt|blocked|intent\.\d{8})"
            + r"\.json\.[0-9a-f]{32}\.tmp$"
        )
        directory = owner.open_dir()
        try:
            names = tuple(os.listdir(directory))
        finally:
            os.close(directory)
        removed = False
        for name in names:
            if grammar.fullmatch(name) is None:
                continue
            metadata = os.stat(name, dir_fd=owner.fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or metadata.st_gid != os.getegid()
            ):
                raise SourceDeletionError("durable_deletion_temp_invalid")
            os.unlink(name, dir_fd=owner.fd)
            removed = True
        if removed:
            owner.fsync_dir()

    @classmethod
    def _write_once(cls, owner: _DirFd, name: str, raw: bytes) -> None:
        existing = cls._read_optional(owner, name)
        if existing is not None:
            if existing == raw:
                cls._resync_existing(owner, name)
                return
            raise SourceDeletionConflict("durable_deletion_record_conflict")
        temp = f".{name}.{secrets.token_hex(16)}.tmp"
        descriptor = -1
        try:
            descriptor = owner.open_file(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise SourceDeletionError("durable_deletion_record_write_failed")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                cls._rename_noreplace(owner.fd, temp, owner.fd, name)
            except FileExistsError:
                prior = cls._read_required(owner, name)
                if prior != raw:
                    raise SourceDeletionConflict("durable_deletion_record_conflict")
            owner.fsync_dir()
            cls._resync_existing(owner, name)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temp, dir_fd=owner.fd)
            except FileNotFoundError:
                pass
            else:
                owner.fsync_dir()

    @classmethod
    def _resync_existing(cls, owner: _DirFd, name: str) -> None:
        descriptor = owner.open_file(name, os.O_RDONLY)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SourceDeletionError("durable_deletion_record_invalid")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        owner.fsync_dir()

    @staticmethod
    def _read_optional(owner: _DirFd, name: str) -> bytes | None:
        try:
            return SourceDeletionGuard._read_required(owner, name)
        except FileNotFoundError:
            return None

    @staticmethod
    def _read_required(owner: _DirFd, name: str) -> bytes:
        descriptor = owner.open_file(name, os.O_RDONLY)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > _MAX_RECORD_BYTES:
                raise SourceDeletionError("durable_deletion_record_invalid")
            raw = SourceDeletionGuard._read_all(descriptor, _MAX_RECORD_BYTES)
            if SourceDeletionGuard._file_identity(metadata) != SourceDeletionGuard._file_identity(os.fstat(descriptor)):
                raise SourceDeletionError("durable_deletion_record_changed")
            return raw
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_all(descriptor: int, maximum: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - size))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise SourceDeletionError("record_too_large")

    @staticmethod
    def _file_digest(descriptor: int) -> str:
        os.lseek(descriptor, 0, os.SEEK_SET)
        hasher = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                return _DIGEST_PREFIX + hasher.hexdigest()
            hasher.update(chunk)

    @classmethod
    def _directory_digest(cls, descriptor: int) -> str:
        entries: list[dict[str, object]] = []
        for name in sorted(os.listdir(descriptor)):
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise SourceDeletionError("source_directory_contains_symlink")
            if stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise SourceDeletionError("source_directory_contains_hardlink")
                child = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
                try:
                    digest = cls._file_digest(child)
                finally:
                    os.close(child)
                kind = "file"
            elif stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
                try:
                    digest = cls._directory_digest(child)
                finally:
                    os.close(child)
                kind = "directory"
            else:
                raise SourceDeletionError("source_directory_contains_special_file")
            entries.append(
                {"kind": kind, "name": name, "sha256": digest, "size_bytes": metadata.st_size}
            )
        return _sha256(canonical_json_bytes(entries))

    @staticmethod
    def _exists(owner: _DirFd, relative: str) -> bool:
        try:
            return owner.exists(relative)
        except FileNotFoundError:
            return False
        except (NotADirectoryError, OSError) as exc:
            raise SourceDeletionError("source_parent_path_invalid") from exc

    @staticmethod
    def _absence_anchor(owner: _DirFd, relative: str, source_key: str) -> _AbsenceAnchor:
        current = os.dup(owner.fd)
        traversed: list[str] = []
        try:
            for index, part in enumerate(_DirFd.parts(relative)):
                try:
                    metadata = os.stat(part, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    return _AbsenceAnchor("/".join(traversed), os.fstat(current))
                if index == len(_DirFd.parts(relative)) - 1:
                    raise SourceDeletionError(f"deleted_source_reappeared:{source_key}")
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise SourceDeletionError(f"absence_path_substituted:{source_key}")
                child = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
                os.close(current)
                current = child
                traversed.append(part)
        finally:
            os.close(current)

    @staticmethod
    def _open_parent(owner: _DirFd, relative: str) -> tuple[int, str]:
        parts = _DirFd.parts(relative)
        parent = owner.open_dir("/".join(parts[:-1])) if len(parts) > 1 else os.dup(owner.fd)
        return parent, parts[-1]

    @staticmethod
    def _open_directory(path: str) -> int:
        return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))

    @staticmethod
    def _open_file(path: str) -> int:
        return os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))

    @staticmethod
    def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
        return metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid, metadata.st_gid

    @staticmethod
    def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    @staticmethod
    def _paths_overlap(first: str, second: str) -> bool:
        try:
            common = os.path.commonpath((first, second))
        except ValueError:
            return False
        return common in {first, second}

    @staticmethod
    def _is_beneath(candidate: str, parent: str) -> bool:
        try:
            return os.path.commonpath((candidate, parent)) == parent and candidate != parent
        except ValueError:
            return False


    @staticmethod
    def _rename_noreplace(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        source_bytes = os.fsencode(source)
        destination_bytes = os.fsencode(destination)
        if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
            result = libc.renameatx_np(
                source_fd,
                ctypes.c_char_p(source_bytes),
                destination_fd,
                ctypes.c_char_p(destination_bytes),
                0x00000004,
            )
        elif hasattr(libc, "renameat2"):
            result = libc.renameat2(
                source_fd,
                ctypes.c_char_p(source_bytes),
                destination_fd,
                ctypes.c_char_p(destination_bytes),
                1,
            )
        else:
            raise SourceDeletionError("atomic_noreplace_rename_unavailable")
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(error, os.strerror(error), destination)
            if error == errno.ENOENT:
                raise FileNotFoundError(error, os.strerror(error), source)
            raise OSError(error, os.strerror(error), source)

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SourceDeletionError("deletion_clock_invalid")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "BoundSourceOwnership",
    "FinalReceiptLease",
    "FinalReceiptVerifier",
    "GateKind",
    "RollbackStoreSourceOwnershipAuthority",
    "SourceAbsenceProof",
    "SourceDeletionConflict",
    "SourceDeletionError",
    "SourceDeletionGateReceipt",
    "SourceDeletionGateReceipts",
    "SourceDeletionGuard",
    "SourceDeletionReceipt",
    "SourceDeletionRequest",
    "SourceOwnershipAuthority",
    "SourceOwnershipIdentity",
    "SourceOwnershipLease",
    "VerifiedGateOutcome",
    "VerifiedSourceOwnershipFence",
]
