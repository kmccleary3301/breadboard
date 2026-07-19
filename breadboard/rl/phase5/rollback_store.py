from __future__ import annotations

import base64
import binascii
import ctypes
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import threading
import sys
from typing import Any, Iterator, Mapping, Protocol, Sequence
import uuid


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ROLE_RE = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_MAX_RECORD_BYTES = 4 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_RECEIPT_PAYLOADS = 64
_MAX_AGGREGATE_RECEIPT_PAYLOAD_BYTES = 2 * 1024 * 1024
_MAX_ROLLBACK_QUARANTINE_PAIRS = 256
_MAX_ROLLBACK_QUARANTINE_BYTES = 64 * 1024 * 1024
_MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES = 64 * 1024
_MAX_ROLLBACK_QUARANTINE_ARTIFACTS = 2 * _MAX_ROLLBACK_QUARANTINE_PAIRS
_MAX_ROOT_ENTRIES = 2048
_MAX_ROOT_NAME_BYTES = 64 * 1024 * 1024
_MAX_ABANDONED_TEMPS = 128
_MAX_ABANDONED_TEMP_NAME_BYTES = 32 * 1024
_MAX_ABANDONED_TEMP_BYTES = 64 * 1024 * 1024
_MAX_CLEANUP_MANIFEST_BYTES = 2 * 1024 * 1024
_CLEANUP_PREPARING_NAME = "preparing"
_CLEANUP_COMMITTED_NAME = "committed"
_CLEANUP_PREPARING_TEMP_NAME = ".preparing.tmp"
_CLEANUP_COMMITTED_TEMP_NAME = ".committed.tmp"
_CLEANUP_RECEIPT_NAME = "receipt"
_CLEANUP_RECEIPT_TEMP_NAME = ".receipt.tmp"
_TEST_CLEANUP_FAULT_HOOK: Any = None


class _CleanupInjectedCrash(BaseException):
    pass


_ROLLBACK_TERMINAL_DIRECTORY = ".terminal-rollback"
_ROLLBACK_TERMINAL_ANCHOR_INDEX = ".terminal-rollback-anchors"
_REQUEST_KEYS = frozenset(
    (
        "affected_episode_ids",
        "approved_tuple",
        "dependent_root_refs",
        "evidence_invalidations",
        "failed_rerun_invalidations",
        "frozen_active_generation",
        "rerun_authoring_input",
        "rerun_source_identities",
        "rerun_input_path",
        "revocation_publish_request",
        "rollback_id",
        "schema_version",
        "source_deletion_plan",
    )
)
_OBSERVATION_KEYS = frozenset(
    (
        "evidence_id",
        "exit_code",
        "graph_alias",
        "kind",
        "observed_bytes_base64",
        "observed_identity",
        "observed_target_node_id",
        "schema_version",
    )
)
_OBSERVATION_KINDS = frozenset(("active_status", "artifact", "identity", "rerun"))


class RollbackStoreError(RuntimeError):
    pass


class RollbackValidationError(RollbackStoreError, ValueError):
    pass


class RollbackConflictError(RollbackStoreError):
    pass


class RollbackIdempotencyConflict(RollbackConflictError):
    pass


class RollbackCorruptionError(RollbackStoreError):
    pass


class DependentIneligibleError(RollbackStoreError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("value is not canonical JSON") from error


def canonical_digest(value: bytes) -> str:
    if type(value) is not bytes:
        raise RollbackValidationError("digest input must be exact bytes")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _require_digest(value: object, name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise RollbackValidationError(f"{name} must be a lowercase sha256 digest")
    return value


def _require_id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise RollbackValidationError(f"{name} has an invalid identity")
    return value


def _require_role(value: object) -> str:
    if type(value) is not str or _ROLE_RE.fullmatch(value) is None:
        raise RollbackValidationError("tuple reference role is invalid")
    return value


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RollbackValidationError(f"{name} must be an integer >= {minimum}")
    return value


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise RollbackValidationError(f"{name} must be an exact boolean")
    return value


def _require_object(
    value: object, keys: frozenset[str], name: str
) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise RollbackValidationError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _require_tuple(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise RollbackValidationError(f"{name} must be a canonical array")
    return value


def _decode_canonical_payload(raw: bytes, name: str) -> Mapping[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_PAYLOAD_BYTES:
        raise RollbackValidationError(
            f"{name} must be exact non-empty bytes within the size bound"
        )
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RollbackValidationError(f"{name} must be canonical JSON") from error
    if type(decoded) is not dict or raw != canonical_json_bytes(decoded):
        raise RollbackValidationError(f"{name} must be a canonical JSON object")
    return decoded


def _require_sorted_unique_array(value: object, name: str) -> list[Any]:
    items = _require_tuple(value, name)
    canonical_items = tuple(canonical_json_bytes(item) for item in items)
    if canonical_items != tuple(sorted(canonical_items)) or len(
        set(canonical_items)
    ) != len(canonical_items):
        raise RollbackValidationError(f"{name} must be unique and sorted")
    return items


def _validate_exact_model(value: object, model_type: Any, name: str) -> Any:
    if type(value) is not dict:
        raise RollbackValidationError(f"{name} must be an exact object")
    try:
        model = model_type.model_validate_json(canonical_json_bytes(value), strict=True)
    except (TypeError, ValueError) as error:
        raise RollbackValidationError(f"{name} is invalid") from error
    if model.model_dump(mode="json") != value:
        raise RollbackValidationError(f"{name} projection is not exact")
    return model


def _validate_absolute_normalized_path(value: object, name: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
        or len(value) > 4096
    ):
        raise RollbackValidationError(f"{name} must be an absolute normalized path")
    return value


@dataclass(frozen=True, slots=True)
class _ImmutableFileIdentity:
    device: int
    inode: int
    size_bytes: int
    mtime_ns: str
    ctime_ns: str
    owner_uid: int
    mode: int
    nlink: int

    @classmethod
    def from_object(cls, value: object, name: str) -> "_ImmutableFileIdentity":
        item = _require_object(
            value,
            frozenset(
                (
                    "ctime_ns",
                    "device",
                    "inode",
                    "mode",
                    "mtime_ns",
                    "nlink",
                    "owner_uid",
                    "size_bytes",
                )
            ),
            name,
        )
        for field_name in (
            "device",
            "inode",
            "mode",
            "nlink",
            "owner_uid",
            "size_bytes",
        ):
            _require_int(item[field_name], f"{name} {field_name}")
        for field_name in ("ctime_ns", "mtime_ns"):
            value = item[field_name]
            if type(value) is not str or not value.isascii() or not value.isdecimal():
                raise RollbackValidationError(
                    f"{name} {field_name} must be decimal nanoseconds"
                )
        identity = cls(
            device=item["device"],
            inode=item["inode"],
            size_bytes=item["size_bytes"],
            mtime_ns=item["mtime_ns"],
            ctime_ns=item["ctime_ns"],
            owner_uid=item["owner_uid"],
            mode=item["mode"],
            nlink=item["nlink"],
        )
        if (
            identity.inode < 1
            or identity.size_bytes < 1
            or identity.nlink != 1
            or identity.mode & 0o222
        ):
            raise RollbackValidationError(
                f"{name} must bind a non-writable, single-link regular file"
            )
        return identity

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "_ImmutableFileIdentity":
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            size_bytes=value.st_size,
            mtime_ns=str(value.st_mtime_ns),
            ctime_ns=str(value.st_ctime_ns),
            owner_uid=value.st_uid,
            mode=stat.S_IMODE(value.st_mode),
            nlink=value.st_nlink,
        )

    def canonical_object(self) -> dict[str, object]:
        return {
            "ctime_ns": self.ctime_ns,
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "mtime_ns": self.mtime_ns,
            "nlink": self.nlink,
            "owner_uid": self.owner_uid,
            "size_bytes": self.size_bytes,
        }


def _open_pinned_parent(path: str, name: str) -> tuple[int, str]:
    normalized = _validate_absolute_normalized_path(path, f"{name} path")
    parts = normalized.split("/")
    leaf = parts[-1]
    if not leaf:
        raise RollbackValidationError(f"{name} path must name a file")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open("/", flags)
    try:
        for component in parts[1:-1]:
            next_descriptor = os.open(
                component,
                flags,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, leaf
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(slots=True)
class _PinnedImmutableSource:
    path: str
    name: str
    parent_fd: int
    file_fd: int
    parent_identity: tuple[int, int, int, int]
    identity: _ImmutableFileIdentity
    raw: bytes
    digest: str

    @classmethod
    def capture(
        cls,
        path: str,
        name: str,
        expected_digest: str,
        expected_identity: _ImmutableFileIdentity,
    ) -> "_PinnedImmutableSource":
        try:
            parent_fd, leaf = _open_pinned_parent(path, name)
        except OSError as error:
            raise RollbackValidationError(
                f"{name} parent authority is not securely readable"
            ) from error
        file_fd = -1
        try:
            file_fd = os.open(
                leaf,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            before = os.fstat(file_fd)
            observed = _ImmutableFileIdentity.from_stat(before)
            if (
                not stat.S_ISREG(before.st_mode)
                or observed != expected_identity
                or observed.nlink != 1
                or observed.mode & 0o222
                or observed.size_bytes > _MAX_RECORD_BYTES
            ):
                raise RollbackValidationError(
                    f"{name} immutable file identity mismatch"
                )
            chunks: list[bytes] = []
            remaining = observed.size_bytes
            while remaining:
                chunk = os.read(file_fd, min(65536, remaining))
                if not chunk:
                    raise RollbackValidationError(f"{name} changed during pinned read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(file_fd, 1):
                raise RollbackValidationError(f"{name} grew during pinned read")
            raw = b"".join(chunks)
            after = os.fstat(file_fd)
            path_state = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                _ImmutableFileIdentity.from_stat(after) != observed
                or _ImmutableFileIdentity.from_stat(path_state) != observed
                or canonical_digest(raw) != expected_digest
            ):
                raise RollbackValidationError(
                    f"{name} pinned bytes or identity changed"
                )
            parent = os.fstat(parent_fd)
            return cls(
                path=path,
                name=name,
                parent_fd=parent_fd,
                file_fd=file_fd,
                parent_identity=(
                    parent.st_dev,
                    parent.st_ino,
                    parent.st_uid,
                    stat.S_IMODE(parent.st_mode),
                ),
                identity=observed,
                raw=raw,
                digest=expected_digest,
            )
        except OSError as error:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(parent_fd)
            raise RollbackValidationError(f"{name} is not securely readable") from error
        except BaseException:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(parent_fd)
            raise

    def revalidate(self) -> None:
        fresh_parent_fd, leaf = _open_pinned_parent(self.path, self.name)
        try:
            fresh_parent = os.fstat(fresh_parent_fd)
            path_state = os.stat(leaf, dir_fd=fresh_parent_fd, follow_symlinks=False)
            if (
                (
                    fresh_parent.st_dev,
                    fresh_parent.st_ino,
                    fresh_parent.st_uid,
                    stat.S_IMODE(fresh_parent.st_mode),
                )
                != self.parent_identity
                or _ImmutableFileIdentity.from_stat(path_state) != self.identity
                or _ImmutableFileIdentity.from_stat(os.fstat(self.file_fd))
                != self.identity
            ):
                raise RollbackValidationError(f"{self.name} pinned authority changed")
        finally:
            os.close(fresh_parent_fd)

    def close(self) -> None:
        os.close(self.file_fd)
        os.close(self.parent_fd)


def _revalidate_source_capsules(
    capsules: Sequence[_PinnedImmutableSource],
) -> None:
    for capsule in capsules:
        capsule.revalidate()


def _source_identity_from_projection(value: object) -> Any:
    from breadboard.rl.phase5.g4_source_deletion import (
        SourceOwnershipIdentity,
    )

    item = _require_object(
        value,
        frozenset(
            (
                "ctime_ns",
                "device",
                "inode",
                "kind",
                "relative_path",
                "root_authority_id",
                "root_path",
                "sha256",
                "size_bytes",
            )
        ),
        "owned source identity",
    )
    numbers: dict[str, int] = {}
    for field in ("ctime_ns", "device", "inode", "size_bytes"):
        raw = item[field]
        if type(raw) is not str or not raw.isdigit() or str(int(raw)) != raw:
            raise RollbackValidationError(
                f"owned source {field} must be a canonical unsigned integer"
            )
        numbers[field] = int(raw)
    try:
        source = SourceOwnershipIdentity(
            root_authority_id=item["root_authority_id"],
            root_path=item["root_path"],
            relative_path=item["relative_path"],
            device=numbers["device"],
            inode=numbers["inode"],
            ctime_ns=numbers["ctime_ns"],
            size_bytes=numbers["size_bytes"],
            sha256=item["sha256"],
            kind=item["kind"],
        )
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("owned source identity is invalid") from error
    return source


def _validate_observation(value: object) -> Mapping[str, Any]:
    item = _require_object(value, _OBSERVATION_KEYS, "evidence observation")
    if (
        item["schema_version"] != "bb.rl.phase5.g4-evidence-observation.v1"
        or item["kind"] not in _OBSERVATION_KINDS
        or type(item["graph_alias"]) is not str
        or not item["graph_alias"]
    ):
        raise RollbackValidationError("evidence observation identity is invalid")
    kind = item["kind"]
    evidence_id = item["evidence_id"]
    if kind != "active_status":
        _require_id(evidence_id, "evidence observation id")
    elif evidence_id is not None:
        raise RollbackValidationError(
            "active-status observation cannot carry evidence id"
        )
    expected_non_null = {
        "artifact": frozenset(("evidence_id",)),
        "rerun": frozenset(("evidence_id", "exit_code")),
        "identity": frozenset(("evidence_id", "observed_identity")),
        "active_status": frozenset(("observed_target_node_id",)),
    }[kind]
    nullable = (
        "evidence_id",
        "exit_code",
        "observed_identity",
        "observed_target_node_id",
    )
    for field in nullable:
        if (field in expected_non_null) != (item[field] is not None):
            raise RollbackValidationError(
                "evidence observation kind fields are incoherent"
            )
    if kind == "artifact":
        encoded = item["observed_bytes_base64"]
        if encoded is not None:
            if type(encoded) is not str:
                raise RollbackValidationError(
                    "artifact observation base64 must be exact"
                )
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise RollbackValidationError(
                    "artifact observation base64 is invalid"
                ) from error
            if base64.b64encode(decoded).decode("ascii") != encoded:
                raise RollbackValidationError(
                    "artifact observation base64 is not canonical"
                )
    elif item["observed_bytes_base64"] is not None:
        raise RollbackValidationError(
            "non-artifact observation cannot carry observed bytes"
        )
    if kind == "rerun" and type(item["exit_code"]) is not int:
        raise RollbackValidationError(
            "rerun observation exit code must be an exact integer"
        )
    if kind == "identity":
        identity = _require_object(
            item["observed_identity"],
            frozenset(
                (
                    "config_digest",
                    "model_digest",
                    "run_id",
                    "source_head",
                    "task_digest",
                    "threshold_digest",
                )
            ),
            "observed evidence identity",
        )
        _require_id(identity["run_id"], "observed evidence run id")
        for field in (
            "config_digest",
            "model_digest",
            "source_head",
            "task_digest",
            "threshold_digest",
        ):
            _require_digest(identity[field], f"observed evidence {field}")
    if kind == "active_status":
        _require_id(
            item["observed_target_node_id"],
            "active-status target node id",
        )
    return item


def _validate_f6_input_and_sources(
    authoring: Any,
    rerun_path: str,
    affected_episode_ids: Sequence[str],
    identity_projection: object,
    capsules: list[_PinnedImmutableSource],
) -> tuple[bytes, Any]:
    from scripts.rl_phase5.run_f6_restart_replay import F6RestartReplayInput

    identity_root = _require_object(
        identity_projection,
        frozenset(
            (
                "authority_bundle",
                "composition_descriptor",
                "composition_manifest",
                "original_request",
                "rerun_input",
                "secret_files",
            )
        ),
        "F6 rerun source identities",
    )
    secret_identity_items = _require_object(
        identity_root["secret_files"],
        frozenset(authoring.secret_files),
        "F6 secret source identities",
    )

    def binding(value: object, name: str) -> tuple[str, _ImmutableFileIdentity]:
        item = _require_object(
            value,
            frozenset(("identity", "sha256")),
            f"{name} binding",
        )
        return (
            _require_digest(item["sha256"], f"{name} digest"),
            _ImmutableFileIdentity.from_object(item["identity"], f"{name} identity"),
        )

    source_specs = (
        (
            "composition descriptor",
            authoring.composition_descriptor,
            binding(
                identity_root["composition_descriptor"],
                "composition descriptor",
            ),
        ),
        (
            "composition manifest",
            authoring.composition_manifest,
            binding(
                identity_root["composition_manifest"],
                "composition manifest",
            ),
        ),
        (
            "authority bundle",
            authoring.authority_bundle,
            binding(identity_root["authority_bundle"], "authority bundle"),
        ),
        (
            "original request",
            authoring.original_request,
            binding(identity_root["original_request"], "original request"),
        ),
        *(
            (
                f"secret file {handle_id}",
                source,
                binding(
                    secret_identity_items[handle_id],
                    f"secret file {handle_id}",
                ),
            )
            for handle_id, source in sorted(authoring.secret_files.items())
        ),
    )
    source_payloads: dict[str, bytes] = {}
    for source_name, source, (expected_digest, expected_identity) in source_specs:
        if expected_digest != source.sha256:
            raise RollbackValidationError(
                f"{source_name} request digest binding mismatch"
            )
        capsule = _PinnedImmutableSource.capture(
            source.path,
            source_name,
            expected_digest,
            expected_identity,
        )
        capsules.append(capsule)
        source_payloads[source_name] = capsule.raw

    rerun_digest, rerun_identity = binding(identity_root["rerun_input"], "rerun input")
    rerun_capsule = _PinnedImmutableSource.capture(
        rerun_path,
        "rerun input",
        rerun_digest,
        rerun_identity,
    )
    capsules.append(rerun_capsule)
    input_raw = rerun_capsule.raw
    try:
        input_model = F6RestartReplayInput.model_validate_json(input_raw, strict=True)
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("immutable F6 rerun input is invalid") from error
    if canonical_json_bytes(input_model.model_dump(mode="json")) != input_raw:
        raise RollbackValidationError("immutable F6 rerun input is not canonical")

    production = input_model.production
    input_secrets = {
        handle_id: {"path": source.path, "sha256": source.sha256}
        for handle_id, source in production.secret_files.items()
    }
    authoring_secrets = {
        handle_id: {"path": source.path, "sha256": source.sha256}
        for handle_id, source in authoring.secret_files.items()
    }
    input_secret_identities = {
        handle_id: source.identity.model_dump(mode="json")
        for handle_id, source in production.secret_files.items()
    }
    supplied_secret_identities = {
        handle_id: binding(
            secret_identity_items[handle_id],
            f"secret file {handle_id}",
        )[1].canonical_object()
        for handle_id in sorted(secret_identity_items)
    }
    original_projection = canonical_json_bytes(
        input_model.original_request.model_dump(mode="json")
    )
    if (
        source_payloads["original request"] != original_projection
        or input_model.original_request.episode_id not in affected_episode_ids
        or input_model.fresh_live_request.episode_id != authoring.fresh_episode_id
        or input_model.target != authoring.target
        or input_model.task_input != authoring.task_input
        or input_model.run_context != authoring.run_context
        or input_model.report_path != authoring.report_path
        or production.composition_ref_path != authoring.composition_descriptor.path
        or production.composition_descriptor_ref.digest
        != authoring.composition_descriptor.sha256
        or production.composition_manifest_ref.digest
        != authoring.composition_manifest.sha256
        or production.authority_bundle_ref.digest != authoring.authority_bundle.sha256
        or input_secrets != authoring_secrets
        or supplied_secret_identities != input_secret_identities
    ):
        raise RollbackValidationError("F6 rerun authoring source binding mismatch")
    return input_raw, input_model


def _validate_request_payload_with_capsules(
    raw: bytes,
    rollback_id: str,
    request_digest: str,
    source_capsules: list[_PinnedImmutableSource],
) -> Mapping[str, Any]:
    from breadboard.rl.phase5.f6_restart_replay_authoring import (
        F6RestartReplayAuthoringInput,
    )

    value = _require_object(
        _decode_canonical_payload(raw, "rollback request payload"),
        _REQUEST_KEYS,
        "rollback request payload",
    )
    if (
        value["schema_version"] != "bb.rl.phase5.g4-rollback-request.v1"
        or value["rollback_id"] != rollback_id
        or canonical_digest(raw) != request_digest
    ):
        raise RollbackValidationError(
            "rollback request payload identity or digest mismatch"
        )
    _require_int(
        value["frozen_active_generation"],
        "frozen active generation",
        minimum=1,
    )
    episodes = _require_tuple(value["affected_episode_ids"], "affected episode ids")
    if not episodes or len(set(episodes)) != len(episodes):
        raise RollbackValidationError(
            "affected episode ids must be nonempty and unique"
        )
    for episode_id in episodes:
        _require_id(episode_id, "affected episode id")
    _active_tuple_from_object(value["approved_tuple"])

    revocation = _require_object(
        value["revocation_publish_request"],
        frozenset(
            (
                "binding",
                "expected_epoch",
                "expected_generation",
                "operation_id",
                "scope_digest",
            )
        ),
        "revocation publish request",
    )
    if revocation["operation_id"] != f"{rollback_id}.revocation":
        raise RollbackValidationError(
            "revocation publication operation does not bind rollback"
        )
    _require_digest(revocation["scope_digest"], "revocation scope digest")
    binding = _require_object(
        revocation["binding"],
        frozenset(("epoch", "scope_digest", "state_digest")),
        "revocation binding",
    )
    _require_int(binding["epoch"], "revocation binding epoch")
    _require_digest(binding["scope_digest"], "revocation binding scope digest")
    _require_digest(binding["state_digest"], "revocation binding state digest")
    if binding["scope_digest"] != revocation["scope_digest"]:
        raise RollbackValidationError("revocation scope binding drifted")
    expected_generation = revocation["expected_generation"]
    expected_epoch = revocation["expected_epoch"]
    if (expected_generation is None) != (expected_epoch is None):
        raise RollbackValidationError("revocation expectations must be paired")
    if expected_generation is not None:
        _require_int(
            expected_generation,
            "expected revocation generation",
            minimum=1,
        )
        _require_int(expected_epoch, "expected revocation epoch")

    authoring = _validate_exact_model(
        value["rerun_authoring_input"],
        F6RestartReplayAuthoringInput,
        "F6 rerun authoring input",
    )
    if authoring.fresh_episode_id in episodes:
        raise RollbackValidationError(
            "fresh rerun episode id overlaps affected episode"
        )
    rerun_path = _validate_absolute_normalized_path(
        value["rerun_input_path"], "rerun input path"
    )
    source_paths = {
        authoring.composition_descriptor.path,
        authoring.composition_manifest.path,
        authoring.authority_bundle.path,
        authoring.original_request.path,
        authoring.report_path,
        *(source.path for source in authoring.secret_files.values()),
    }
    if rerun_path in source_paths:
        raise RollbackValidationError(
            "rerun input path must be exclusive from source/report paths"
        )
    _validate_f6_input_and_sources(
        authoring,
        rerun_path,
        episodes,
        value["rerun_source_identities"],
        source_capsules,
    )

    root_items = _require_tuple(value["dependent_root_refs"], "dependent root refs")
    if not root_items:
        raise RollbackValidationError("dependent root refs must be nonempty")
    roots = tuple(_immutable_ref_from_object(item) for item in root_items)
    if roots != tuple(sorted(roots, key=lambda item: item.identity_digest)) or len(
        {root.identity_digest for root in roots}
    ) != len(roots):
        raise RollbackValidationError(
            "dependent root refs must be identity-sorted and unique"
        )

    observations_by_field: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for field in ("evidence_invalidations", "failed_rerun_invalidations"):
        items = _require_tuple(value[field], field)
        observations = tuple(_validate_observation(item) for item in items)
        identities = tuple(
            (
                item["graph_alias"],
                item["kind"],
                item["evidence_id"],
                item["observed_target_node_id"],
            )
            for item in observations
        )
        if len(set(identities)) != len(identities):
            raise RollbackValidationError(f"{field} observations must be unique")
        observations_by_field[field] = observations
    for item in observations_by_field["failed_rerun_invalidations"]:
        if item["kind"] != "rerun" or item["exit_code"] == 0:
            raise RollbackValidationError(
                "failed rerun invalidations require nonzero rerun observations"
            )

    deletion = _require_object(
        value["source_deletion_plan"],
        frozenset(("operation_id", "owned_sources", "schema_version")),
        "source deletion plan",
    )
    if (
        deletion["schema_version"] != "bb.rl.phase5.g4-source-deletion-plan.v1"
        or deletion["operation_id"] != f"{rollback_id}.source-deletion"
    ):
        raise RollbackValidationError("source deletion plan identity is invalid")
    source_items = _require_tuple(
        deletion["owned_sources"], "source deletion owned sources"
    )
    if not source_items:
        raise RollbackValidationError("source deletion sources must be nonempty")
    sources = tuple(_source_identity_from_projection(item) for item in source_items)
    keys = tuple(source.key for source in sources)
    physical = tuple((source.device, source.inode) for source in sources)
    if (
        keys != tuple(sorted(keys))
        or len(set(keys)) != len(keys)
        or len(set(physical)) != len(physical)
    ):
        raise RollbackValidationError(
            "source deletion ownership must be sorted and unique"
        )
    return value


def _validate_request_payload(
    raw: bytes, rollback_id: str, request_digest: str
) -> Mapping[str, Any]:
    source_capsules: list[_PinnedImmutableSource] = []
    try:
        return _validate_request_payload_with_capsules(
            raw,
            rollback_id,
            request_digest,
            source_capsules,
        )
    finally:
        for capsule in reversed(source_capsules):
            capsule.close()


@dataclass(frozen=True, slots=True)
class ImmutableObjectRef:
    reference: str
    digest: str

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not str
            or not self.reference
            or len(self.reference) > 4096
            or any(character.isspace() for character in self.reference)
        ):
            raise RollbackValidationError("immutable reference is invalid")
        _require_digest(self.digest, "immutable reference digest")

    def canonical_object(self) -> dict[str, str]:
        return {"digest": self.digest, "reference": self.reference}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class ApprovedTupleRef:
    role: str
    object_ref: ImmutableObjectRef

    def __post_init__(self) -> None:
        _require_role(self.role)
        if type(self.object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError("tuple object reference must be exact")

    def canonical_object(self) -> dict[str, Any]:
        return {"object_ref": self.object_ref.canonical_object(), "role": self.role}


@dataclass(frozen=True, slots=True)
class ActiveApprovedTuple:
    immutable_refs: tuple[ApprovedTupleRef, ...]
    tuple_digest: str
    schema_version: str = "bb.rl.phase5.active-approved-tuple.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "bb.rl.phase5.active-approved-tuple.v1":
            raise RollbackValidationError("active tuple schema is invalid")
        if type(self.immutable_refs) is not tuple or not self.immutable_refs:
            raise RollbackValidationError("active tuple requires immutable references")
        if any(type(item) is not ApprovedTupleRef for item in self.immutable_refs):
            raise RollbackValidationError("active tuple references must be exact")
        roles = tuple(item.role for item in self.immutable_refs)
        if roles != tuple(sorted(roles)) or len(set(roles)) != len(roles):
            raise RollbackValidationError(
                "active tuple roles must be unique and sorted"
            )
        _require_digest(self.tuple_digest, "active tuple digest")
        if self.tuple_digest != canonical_digest(
            canonical_json_bytes(
                {
                    "immutable_refs": [
                        item.canonical_object() for item in self.immutable_refs
                    ],
                    "schema_version": self.schema_version,
                }
            )
        ):
            raise RollbackValidationError("active tuple digest does not match its refs")

    @classmethod
    def from_refs(
        cls, immutable_refs: Sequence[ApprovedTupleRef]
    ) -> ActiveApprovedTuple:
        refs = tuple(immutable_refs)
        payload = {
            "immutable_refs": [item.canonical_object() for item in refs],
            "schema_version": "bb.rl.phase5.active-approved-tuple.v1",
        }
        return cls(refs, canonical_digest(canonical_json_bytes(payload)))

    def canonical_object(self) -> dict[str, Any]:
        return {
            "immutable_refs": [item.canonical_object() for item in self.immutable_refs],
            "schema_version": self.schema_version,
            "tuple_digest": self.tuple_digest,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())


class RollbackPhase(str, Enum):
    PREPARED = "prepared"
    EPISODES_CLOSED_OR_QUARANTINED = "episodes_closed_or_quarantined"
    REVOCATION_PUBLISHED = "revocation_published"
    DEPENDENTS_QUARANTINED = "dependents_quarantined"
    ACTIVE_TUPLE_RESTORED = "active_tuple_restored"
    RERUN_RECORDED = "rerun_recorded"
    SOURCE_DELETED = "source_deleted"
    COMPLETE = "complete"
    QUARANTINED = "quarantined"


_PHASE_ORDER = (
    RollbackPhase.PREPARED,
    RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    RollbackPhase.REVOCATION_PUBLISHED,
    RollbackPhase.DEPENDENTS_QUARANTINED,
    RollbackPhase.ACTIVE_TUPLE_RESTORED,
    RollbackPhase.RERUN_RECORDED,
    RollbackPhase.SOURCE_DELETED,
    RollbackPhase.COMPLETE,
)
_TERMINAL_PHASES = frozenset((RollbackPhase.COMPLETE, RollbackPhase.QUARANTINED))
_MAX_ROLLBACK_HISTORY_GENERATIONS = (
    len(_PHASE_ORDER) + 2 * _MAX_ROLLBACK_QUARANTINE_PAIRS
)
_MAX_ROLLBACK_HISTORY_BYTES = _MAX_ROLLBACK_HISTORY_GENERATIONS * _MAX_RECORD_BYTES

_PHASE_RECEIPT_KEYS = frozenset(
    (
        "body",
        "journal_generation",
        "journal_revision",
        "phase",
        "request_digest",
        "rollback_id",
        "schema_version",
    )
)
_PHASE_BODY_KEYS = {
    RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED: frozenset(
        ("episode_receipts", "reconcile_receipts")
    ),
    RollbackPhase.REVOCATION_PUBLISHED: frozenset(("revocation_receipt",)),
    RollbackPhase.DEPENDENTS_QUARANTINED: frozenset(
        ("dependent_quarantine_receipts", "evidence_invalidations")
    ),
    RollbackPhase.ACTIVE_TUPLE_RESTORED: frozenset(("active_tuple_state",)),
    RollbackPhase.RERUN_RECORDED: frozenset(("rerun_report",)),
    RollbackPhase.SOURCE_DELETED: frozenset(
        ("source_deletion_receipt", "source_deletion_request")
    ),
    RollbackPhase.COMPLETE: frozenset(("prior_phase_receipt_digests",)),
    RollbackPhase.QUARANTINED: frozenset(
        ("cleanup_receipts", "failed_phase", "leaf_errors")
    ),
}


def _validate_cleanup_receipt(value: object) -> None:
    from breadboard.rl.harness.materialization import (
        CleanupState,
        CleanupStepReceipt,
        SandboxCleanupReceipt,
    )

    item = _require_object(
        value,
        frozenset(("lease_id", "state", "steps")),
        "reconcile receipt",
    )
    _require_id(item["lease_id"], "reconcile lease id")
    steps: list[CleanupStepReceipt] = []
    for raw_step in _require_tuple(item["steps"], "reconcile cleanup steps"):
        step = _require_object(
            raw_step,
            frozenset(("detail", "resource", "state")),
            "reconcile cleanup step",
        )
        if (
            type(step["detail"]) is not str
            or type(step["resource"]) is not str
            or not step["resource"]
        ):
            raise RollbackValidationError("reconcile cleanup step is invalid")
        try:
            state = CleanupState(step["state"])
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "reconcile cleanup state is invalid"
            ) from error
        steps.append(CleanupStepReceipt(step["resource"], state, step["detail"]))
    expected = SandboxCleanupReceipt.from_steps(item["lease_id"], tuple(steps))
    if item["state"] != expected.state.value or expected.state is CleanupState.FAILED:
        raise RollbackValidationError("reconcile aggregate cleanup state is invalid")


def _validate_episode_receipts(
    body: Mapping[str, Any], request: Mapping[str, Any]
) -> None:
    from breadboard.rl.harness.contracts import ArtifactRef

    receipts = _require_tuple(body["episode_receipts"], "episode receipts")
    if [
        item.get("episode_id") if type(item) is dict else None for item in receipts
    ] != request["affected_episode_ids"]:
        raise RollbackValidationError(
            "episode receipts must bind affected episodes in request order"
        )
    for raw_receipt in receipts:
        receipt = _require_object(
            raw_receipt,
            frozenset(
                (
                    "cancellation_reason",
                    "cancellation_requested",
                    "cleanup_disposition",
                    "closed_envelope_ref",
                    "episode_id",
                    "terminal_state",
                    "transition_head_digest",
                    "transition_sequence",
                )
            ),
            "episode rollback receipt",
        )
        if (
            type(receipt["cancellation_reason"]) is not str
            or not receipt["cancellation_reason"]
        ):
            raise RollbackValidationError("episode cancellation reason is invalid")
        _require_bool(
            receipt["cancellation_requested"],
            "episode cancellation requested",
        )
        _require_digest(
            receipt["transition_head_digest"],
            "episode transition head digest",
        )
        _require_int(
            receipt["transition_sequence"],
            "episode transition sequence",
        )
        terminal = receipt["terminal_state"]
        disposition = receipt["cleanup_disposition"]
        closed_ref = receipt["closed_envelope_ref"]
        if terminal == "closed":
            if disposition != "released" or closed_ref is None:
                raise RollbackValidationError(
                    "closed episode receipt lacks released envelope"
                )
            _validate_exact_model(
                closed_ref, ArtifactRef, "closed episode envelope ref"
            )
        elif terminal == "quarantined":
            if disposition != "quarantined" or closed_ref is not None:
                raise RollbackValidationError(
                    "quarantined episode receipt is incoherent"
                )
        else:
            raise RollbackValidationError("episode terminal state is invalid")
    reconcile = _require_tuple(body["reconcile_receipts"], "reconcile receipts")
    lease_ids: list[str] = []
    for receipt in reconcile:
        _validate_cleanup_receipt(receipt)
        lease_ids.append(receipt["lease_id"])
    if len(set(lease_ids)) != len(lease_ids):
        raise RollbackValidationError("reconcile lease ids must be unique")


def _validate_evidence_invalidation(value: object, request: Mapping[str, Any]) -> None:
    from breadboard.rl.phase5.evidence_graph import EvidenceState

    item = _require_object(
        value,
        frozenset(
            (
                "affected_node_ids",
                "award_allowed",
                "effective_states",
                "graph_alias",
                "graph_root",
                "observation_digest",
                "promotion_allowed",
                "rejection_code",
                "root_node_id",
                "schema_version",
            )
        ),
        "evidence invalidation receipt",
    )
    if (
        item["schema_version"] != "bb.rl.phase5.g4-evidence-invalidation-receipt.v1"
        or item["award_allowed"] is not False
        or item["promotion_allowed"] is not False
        or type(item["graph_alias"]) is not str
        or not item["graph_alias"]
        or type(item["rejection_code"]) is not str
        or not item["rejection_code"]
    ):
        raise RollbackValidationError("evidence invalidation receipt flags are invalid")
    _require_digest(item["graph_root"], "evidence graph root")
    _require_id(item["root_node_id"], "evidence root node id")
    _require_digest(item["observation_digest"], "evidence observation digest")
    observations = (
        request["evidence_invalidations"] + request["failed_rerun_invalidations"]
    )
    matching = [
        observation
        for observation in observations
        if observation["graph_alias"] == item["graph_alias"]
        and canonical_digest(canonical_json_bytes(observation))
        == item["observation_digest"]
    ]
    if len(matching) != 1:
        raise RollbackValidationError(
            "evidence receipt does not bind one request observation"
        )
    affected = _require_tuple(item["affected_node_ids"], "affected evidence node ids")
    if (
        not affected
        or any(type(node) is not str or not node for node in affected)
        or affected != sorted(set(affected))
    ):
        raise RollbackValidationError(
            "affected evidence node ids must be sorted and unique"
        )
    effective = _require_tuple(item["effective_states"], "effective evidence states")
    pairs: list[tuple[str, str]] = []
    for raw_pair in effective:
        pair = _require_tuple(raw_pair, "effective evidence state pair")
        if len(pair) != 2 or type(pair[0]) is not str:
            raise RollbackValidationError("effective evidence state pair is invalid")
        try:
            EvidenceState(pair[1])
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "effective evidence state is invalid"
            ) from error
        pairs.append((pair[0], pair[1]))
    if pairs != sorted(set(pairs)) or not set(affected) <= {node for node, _ in pairs}:
        raise RollbackValidationError(
            "effective evidence states are incomplete or unordered"
        )


def _validate_revocation_receipt(value: object, request: Mapping[str, Any]) -> None:
    from breadboard.rl.phase5.revocation_publication import (
        RevocationSnapshotPublishReceipt,
    )

    receipt = _validate_exact_model(
        value,
        RevocationSnapshotPublishReceipt,
        "revocation publication receipt",
    )
    publish_request = request["revocation_publish_request"]
    expected_generation = publish_request["expected_generation"]
    if expected_generation is None:
        expected_generation = 0
    if (
        receipt.operation_id != publish_request["operation_id"]
        or receipt.request_digest
        != canonical_digest(canonical_json_bytes(publish_request))
        or receipt.generation != expected_generation + 1
    ):
        raise RollbackValidationError("revocation publication receipt binding mismatch")


def _validate_dependent_receipts(
    body: Mapping[str, Any],
    request: Mapping[str, Any],
    ref: RollbackPayloadRef,
) -> None:
    root_digests = {
        _immutable_ref_from_object(item).identity_digest
        for item in request["dependent_root_refs"]
    }
    receipts = _require_tuple(
        body["dependent_quarantine_receipts"],
        "dependent quarantine receipts",
    )
    if not receipts:
        raise RollbackValidationError("dependent quarantine receipts must be nonempty")
    object_digests: list[str] = []
    for raw_receipt in receipts:
        receipt = _quarantine_receipt_from_object(raw_receipt)
        if (
            receipt.rollback_id != ref.rollback_id
            or receipt.cause_digest != ref.request_digest
            or not set(receipt.causal_root_digests) <= root_digests
        ):
            raise RollbackValidationError(
                "dependent quarantine receipt binding mismatch"
            )
        object_digests.append(receipt.object_ref.identity_digest)
    if len(set(object_digests)) != len(object_digests):
        raise RollbackValidationError("dependent quarantine object refs must be unique")
    invalidations = _require_tuple(
        body["evidence_invalidations"], "evidence invalidation receipts"
    )
    expected_observations = len(request["evidence_invalidations"]) + len(
        request["failed_rerun_invalidations"]
    )
    if len(invalidations) != expected_observations:
        raise RollbackValidationError(
            "evidence invalidation receipt coverage is incomplete"
        )
    for invalidation in invalidations:
        _validate_evidence_invalidation(invalidation, request)


def _validate_active_tuple_receipt(
    value: object, request: Mapping[str, Any], rollback_id: str
) -> None:
    state = _active_state_from_object(value)
    if (
        state.approved_tuple.canonical_object() != request["approved_tuple"]
        or state.operation_id != f"{rollback_id}.active-tuple"
        or state.generation != request["frozen_active_generation"] + 1
        or state.previous_state_digest is None
    ):
        raise RollbackValidationError("active tuple rollback state binding mismatch")


def _validate_rerun_receipt(value: object, request: Mapping[str, Any]) -> None:
    from breadboard.rl.phase5.f6_restart_replay_authoring import (
        F6RestartReplayAuthoringInput,
    )
    from scripts.rl_phase5.run_f6_restart_replay import (
        F6RestartReplayReport,
    )

    report = _validate_exact_model(value, F6RestartReplayReport, "F6 rerun report")
    authoring_model = _validate_exact_model(
        request["rerun_authoring_input"],
        F6RestartReplayAuthoringInput,
        "F6 rerun authoring input",
    )
    receipt_capsules: list[_PinnedImmutableSource] = []
    try:
        input_raw, input_model = _validate_f6_input_and_sources(
            authoring_model,
            request["rerun_input_path"],
            request["affected_episode_ids"],
            request["rerun_source_identities"],
            receipt_capsules,
        )
    finally:
        for capsule in reversed(receipt_capsules):
            capsule.close()
    authoring = request["rerun_authoring_input"]
    input_production = input_model.production
    original_episode_id = input_model.original_request.episode_id
    original_request_digest = canonical_digest(
        canonical_json_bytes(input_model.original_request.model_dump(mode="json"))
    )
    input_secret_sources = {
        handle_id: {"path": source.path, "sha256": source.sha256}
        for handle_id, source in input_production.secret_files.items()
    }
    normalized_request = input_model.original_request.model_dump(mode="json")
    normalized_request["episode_id"] = "<episode-id>"
    immutable_digest = canonical_digest(
        canonical_json_bytes(
            {
                "immutable_identity": input_model.immutable_identity.model_dump(
                    mode="json"
                ),
                "request": normalized_request,
                "run_context": input_model.run_context,
                "schema_version": "bb.rl.phase5-f6-immutable-input.v1",
                "task_input": input_model.task_input,
            }
        )
    )
    if (
        report.input_digest != canonical_digest(input_raw)
        or report.immutable_input_digest != immutable_digest
        or report.immutable_identity != input_model.immutable_identity
        or report.target != input_model.target
        or report.original.episode_id != original_episode_id
        or report.cached.episode_id != original_episode_id
        or report.fresh_live.episode_id != input_model.fresh_live_request.episode_id
        or input_model.target.model_dump(mode="json") != authoring["target"]
        or input_model.task_input != authoring["task_input"]
        or input_model.run_context != authoring["run_context"]
        or input_model.report_path != authoring["report_path"]
        or report.fresh_live.episode_id != authoring["fresh_episode_id"]
        or input_model.fresh_live_request.episode_id != authoring["fresh_episode_id"]
        or input_model.original_request.episode_id
        not in request["affected_episode_ids"]
        or authoring["original_request"]["sha256"] != original_request_digest
        or input_production.composition_ref_path
        != authoring["composition_descriptor"]["path"]
        or input_production.composition_descriptor_ref.digest
        != authoring["composition_descriptor"]["sha256"]
        or input_production.composition_manifest_ref.digest
        != authoring["composition_manifest"]["sha256"]
        or input_production.authority_bundle_ref.digest
        != authoring["authority_bundle"]["sha256"]
        or input_secret_sources != authoring["secret_files"]
        or report.production.composition_descriptor_digest
        != authoring["composition_descriptor"]["sha256"]
        or report.production.composition_manifest_digest
        != authoring["composition_manifest"]["sha256"]
        or report.production.authority_bundle_digest
        != authoring["authority_bundle"]["sha256"]
        or report.production.composition_descriptor_digest
        != input_production.composition_descriptor_ref.digest
        or report.production.composition_manifest_digest
        != input_production.composition_manifest_ref.digest
        or report.production.authority_bundle_digest
        != input_production.authority_bundle_ref.digest
    ):
        raise RollbackValidationError("F6 rerun report binding mismatch")


def _source_deletion_request_from_projection(value: object) -> Any:
    from breadboard.rl.phase5.g4_source_deletion import (
        SourceDeletionGateReceipt,
        SourceDeletionGateReceipts,
        SourceDeletionRequest,
    )

    item = _require_object(
        value,
        frozenset(
            (
                "gates",
                "journal_request_digest",
                "operation_id",
                "owned_sources",
                "rollback_id",
                "schema_version",
            )
        ),
        "source deletion request",
    )
    if item["schema_version"] != "bb.rl.g4.source-deletion-request.v2":
        raise RollbackValidationError("source deletion request schema is invalid")
    gates_value = _require_object(
        item["gates"],
        frozenset(
            (
                "active_tuple_history_ref",
                "dependent_quarantine_refs",
                "episode_terminal_refs",
                "rerun_receipt_ref",
                "revocation_snapshot_ref",
            )
        ),
        "source deletion gates",
    )

    def gate_ref(raw: object) -> SourceDeletionGateReceipt:
        gate = _require_object(
            raw,
            frozenset(("path", "schema_version", "sha256")),
            "source deletion gate ref",
        )
        try:
            receipt = SourceDeletionGateReceipt(
                gate["path"], gate["sha256"], gate["schema_version"]
            )
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "source deletion gate ref is invalid"
            ) from error
        if receipt.projection() != gate:
            raise RollbackValidationError(
                "source deletion gate ref projection is not exact"
            )
        return receipt

    try:
        gates = SourceDeletionGateReceipts(
            tuple(
                gate_ref(raw)
                for raw in _require_tuple(
                    gates_value["episode_terminal_refs"],
                    "episode terminal gate refs",
                )
            ),
            gate_ref(gates_value["revocation_snapshot_ref"]),
            tuple(
                gate_ref(raw)
                for raw in _require_tuple(
                    gates_value["dependent_quarantine_refs"],
                    "dependent quarantine gate refs",
                )
            ),
            gate_ref(gates_value["active_tuple_history_ref"]),
            gate_ref(gates_value["rerun_receipt_ref"]),
        )
        request = SourceDeletionRequest(
            operation_id=item["operation_id"],
            rollback_id=item["rollback_id"],
            journal_request_digest=item["journal_request_digest"],
            owned_sources=tuple(
                _source_identity_from_projection(source)
                for source in _require_tuple(
                    item["owned_sources"], "source deletion request sources"
                )
            ),
            gates=gates,
        )
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("source deletion request is invalid") from error
    if request.projection() != item:
        raise RollbackValidationError("source deletion request projection is not exact")
    return request


def _source_deletion_receipt_from_projection(value: object) -> Any:
    from breadboard.rl.phase5.g4_source_deletion import (
        SourceAbsenceProof,
        SourceDeletionReceipt,
    )

    item = _require_object(
        value,
        frozenset(
            (
                "absence_proofs",
                "already_absent",
                "authority_signature",
                "completed_at",
                "completion_digest",
                "deleted",
                "operation_id",
                "request_digest",
                "schema_version",
            )
        ),
        "source deletion receipt",
    )
    if item["schema_version"] != "bb.rl.g4.source-deletion-receipt.v2":
        raise RollbackValidationError("source deletion receipt schema is invalid")
    proofs = []
    for raw in _require_tuple(item["absence_proofs"], "source absence proofs"):
        proof = _require_object(
            raw,
            frozenset(
                (
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
                )
            ),
            "source absence proof",
        )
        numbers: dict[str, int] = {}
        for field in (
            "anchor_device",
            "anchor_inode",
            "prior_ctime_ns",
            "prior_device",
            "prior_inode",
            "prior_size_bytes",
        ):
            raw_number = proof[field]
            if (
                type(raw_number) is not str
                or not raw_number.isdigit()
                or str(int(raw_number)) != raw_number
            ):
                raise RollbackValidationError(
                    f"source absence proof {field} is not canonical"
                )
            numbers[field] = int(raw_number)
        try:
            parsed = SourceAbsenceProof(
                root_authority_id=proof["root_authority_id"],
                root_path=proof["root_path"],
                relative_path=proof["relative_path"],
                prior_device=numbers["prior_device"],
                prior_inode=numbers["prior_inode"],
                prior_ctime_ns=numbers["prior_ctime_ns"],
                prior_size_bytes=numbers["prior_size_bytes"],
                prior_sha256=proof["prior_sha256"],
                prior_kind=proof["prior_kind"],
                observed_at=proof["observed_at"],
                absence_anchor_relative_path=proof["absence_anchor_relative_path"],
                anchor_device=numbers["anchor_device"],
                anchor_inode=numbers["anchor_inode"],
            )
        except (TypeError, ValueError) as error:
            raise RollbackValidationError("source absence proof is invalid") from error
        if parsed.projection() != proof:
            raise RollbackValidationError(
                "source absence proof projection is not exact"
            )
        proofs.append(parsed)
    try:
        receipt = SourceDeletionReceipt(
            operation_id=item["operation_id"],
            request_digest=item["request_digest"],
            deleted=tuple(_require_tuple(item["deleted"], "deleted source keys")),
            already_absent=tuple(
                _require_tuple(item["already_absent"], "already absent source keys")
            ),
            absence_proofs=tuple(proofs),
            completed_at=item["completed_at"],
            completion_digest=item["completion_digest"],
            authority_signature=item["authority_signature"],
        )
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("source deletion receipt is invalid") from error
    if receipt.projection() != item:
        raise RollbackValidationError("source deletion receipt projection is not exact")
    return receipt


def _validate_source_deletion_body(
    body: Mapping[str, Any],
    request: Mapping[str, Any],
    ref: RollbackPayloadRef,
    prior_receipt_refs: tuple[RollbackPayloadRef, ...],
    store_root: Path,
) -> None:
    deletion_request = _source_deletion_request_from_projection(
        body["source_deletion_request"]
    )
    plan = request["source_deletion_plan"]
    if (
        deletion_request.operation_id != plan["operation_id"]
        or deletion_request.rollback_id != ref.rollback_id
        or deletion_request.journal_request_digest != ref.request_digest
        or deletion_request.projection()["owned_sources"] != plan["owned_sources"]
    ):
        raise RollbackValidationError(
            "source deletion request does not bind rollback plan"
        )
    by_phase: dict[RollbackPhase, list[RollbackPayloadRef]] = {}
    for prior_ref in prior_receipt_refs:
        by_phase.setdefault(prior_ref.phase, []).append(prior_ref)

    def gate_projection(payload_ref: RollbackPayloadRef) -> dict[str, str]:
        return {
            "path": str(store_root / payload_ref.relative_path),
            "schema_version": "bb.rl.g4.source-deletion-gate-ref.v2",
            "sha256": payload_ref.payload_digest,
        }

    expected_gates = {
        "active_tuple_history_ref": gate_projection(
            by_phase[RollbackPhase.ACTIVE_TUPLE_RESTORED][0]
        ),
        "dependent_quarantine_refs": [
            gate_projection(item)
            for item in by_phase[RollbackPhase.DEPENDENTS_QUARANTINED]
        ],
        "episode_terminal_refs": [
            gate_projection(item)
            for item in by_phase[RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED]
        ],
        "rerun_receipt_ref": gate_projection(by_phase[RollbackPhase.RERUN_RECORDED][0]),
        "revocation_snapshot_ref": gate_projection(
            by_phase[RollbackPhase.REVOCATION_PUBLISHED][0]
        ),
    }
    if deletion_request.projection()["gates"] != expected_gates:
        raise RollbackValidationError(
            "source deletion gates do not bind authoritative payload refs"
        )
    receipt = _source_deletion_receipt_from_projection(body["source_deletion_receipt"])
    source_keys = {source.key for source in deletion_request.owned_sources}
    proof_by_key = {proof.key: proof for proof in receipt.absence_proofs}
    if (
        receipt.operation_id != deletion_request.operation_id
        or receipt.request_digest != deletion_request.request_digest
        or set(receipt.deleted) | set(receipt.already_absent) != source_keys
        or set(proof_by_key) != source_keys
    ):
        raise RollbackValidationError(
            "source deletion receipt coverage or request binding mismatch"
        )
    source_by_key = {source.key: source for source in deletion_request.owned_sources}
    for key, proof in proof_by_key.items():
        source = source_by_key[key]
        if (
            proof.root_path != source.root_path
            or proof.relative_path != source.relative_path
            or proof.prior_device != source.device
            or proof.prior_inode != source.inode
            or proof.prior_ctime_ns != source.ctime_ns
            or proof.prior_size_bytes != source.size_bytes
            or proof.prior_sha256 != source.sha256
            or proof.prior_kind != source.kind
        ):
            raise RollbackValidationError(
                "source absence proof does not bind owned source identity"
            )


def _validate_receipt_payload(
    raw: bytes,
    *,
    ref: RollbackPayloadRef,
    leaf_errors: tuple[RollbackLeafError, ...],
    prior_receipt_digests: tuple[str, ...],
    prior_receipt_refs: tuple[RollbackPayloadRef, ...],
    request: Mapping[str, Any],
    store_root: Path,
) -> Mapping[str, Any]:
    value = _require_object(
        _decode_canonical_payload(raw, "rollback phase receipt payload"),
        _PHASE_RECEIPT_KEYS,
        "rollback phase receipt payload",
    )
    if (
        value["schema_version"] != "bb.rl.phase5.g4-phase-receipt.v1"
        or value["rollback_id"] != ref.rollback_id
        or value["request_digest"] != ref.request_digest
        or value["phase"] != ref.phase.value
        or value["journal_generation"] != ref.journal_generation
        or value["journal_revision"] != ref.journal_revision
        or canonical_digest(raw) != ref.payload_digest
    ):
        raise RollbackValidationError("rollback phase receipt payload binding mismatch")
    body = _require_object(
        value["body"],
        _PHASE_BODY_KEYS[ref.phase],
        "rollback phase receipt body",
    )
    if ref.phase is not RollbackPhase.QUARANTINED and leaf_errors:
        raise RollbackValidationError(
            "non-quarantine receipt cannot carry journal leaf errors"
        )
    if ref.phase is RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED:
        _validate_episode_receipts(body, request)
    elif ref.phase is RollbackPhase.REVOCATION_PUBLISHED:
        _validate_revocation_receipt(body["revocation_receipt"], request)
    elif ref.phase is RollbackPhase.DEPENDENTS_QUARANTINED:
        _validate_dependent_receipts(body, request, ref)
    elif ref.phase is RollbackPhase.ACTIVE_TUPLE_RESTORED:
        _validate_active_tuple_receipt(
            body["active_tuple_state"], request, ref.rollback_id
        )
    elif ref.phase is RollbackPhase.RERUN_RECORDED:
        _validate_rerun_receipt(body["rerun_report"], request)
    elif ref.phase is RollbackPhase.SOURCE_DELETED:
        _validate_source_deletion_body(
            body,
            request,
            ref,
            prior_receipt_refs,
            store_root,
        )
    elif ref.phase is RollbackPhase.COMPLETE:
        digests = _require_tuple(
            body["prior_phase_receipt_digests"],
            "complete prior phase receipt digests",
        )
        required_phases = _PHASE_ORDER[1:-1]
        if (
            tuple(digests) != prior_receipt_digests
            or len(digests) != len(required_phases)
            or tuple(item.phase for item in prior_receipt_refs) != required_phases
        ):
            raise RollbackValidationError(
                "complete receipt does not bind exactly six prior phases"
            )
        for digest in digests:
            _require_digest(digest, "complete prior phase receipt digest")
    else:
        if not leaf_errors or body["leaf_errors"] != [
            error.canonical_object() for error in leaf_errors
        ]:
            raise RollbackValidationError(
                "quarantine receipt leaf errors do not match journal"
            )
        try:
            failed_phase = RollbackPhase(body["failed_phase"])
        except (TypeError, ValueError) as error:
            raise RollbackValidationError(
                "quarantine failed phase is invalid"
            ) from error
        prior_phase = (
            RollbackPhase.PREPARED
            if not prior_receipt_refs
            else prior_receipt_refs[-1].phase
        )
        if prior_phase in _TERMINAL_PHASES:
            raise RollbackValidationError("quarantine cannot follow a terminal phase")
        expected_failed = _PHASE_ORDER[_PHASE_ORDER.index(prior_phase) + 1]
        if failed_phase is not expected_failed or failed_phase in _TERMINAL_PHASES:
            raise RollbackValidationError(
                "quarantine failed phase does not match attempted phase"
            )
        cleanup_receipts = _require_tuple(
            body["cleanup_receipts"], "quarantine cleanup receipts"
        )
        canonical_cleanups = tuple(
            canonical_json_bytes(item) for item in cleanup_receipts
        )
        if len(set(canonical_cleanups)) != len(canonical_cleanups):
            raise RollbackValidationError("quarantine cleanup receipts must be unique")
        for cleanup in cleanup_receipts:
            if type(cleanup) is not dict:
                raise RollbackValidationError(
                    "quarantine cleanup receipt must be typed"
                )
            if set(cleanup) == {"lease_id", "state", "steps"}:
                _validate_cleanup_receipt(cleanup)
            else:
                _validate_evidence_invalidation(cleanup, request)
    return value


class RollbackPayloadKind(str, Enum):
    REQUEST = "request"
    PHASE_RECEIPT = "phase_receipt"


def _payload_relative_path(
    rollback_id: str,
    kind: RollbackPayloadKind,
    phase: RollbackPhase,
    generation: int,
    revision: int,
    payload_digest: str,
) -> str:
    return (
        f"payload.{rollback_id}.g{generation:020d}.r{revision:020d}."
        f"{phase.value}.{kind.value}.{payload_digest[7:]}.json"
    )


@dataclass(frozen=True, slots=True)
class RollbackPayloadRef:
    rollback_id: str
    request_digest: str
    payload_digest: str
    kind: RollbackPayloadKind
    phase: RollbackPhase
    journal_generation: int
    journal_revision: int
    relative_path: str
    schema_version: str = "bb.rl.phase5.rollback-payload-ref.v1"

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "rollback payload rollback id")
        _require_digest(self.request_digest, "rollback payload request digest")
        _require_digest(self.payload_digest, "rollback payload digest")
        if type(self.kind) is not RollbackPayloadKind:
            raise RollbackValidationError("rollback payload kind must be exact")
        if type(self.phase) is not RollbackPhase:
            raise RollbackValidationError("rollback payload phase must be exact")
        _require_int(
            self.journal_generation,
            "rollback payload journal generation",
            minimum=1,
        )
        _require_int(self.journal_revision, "rollback payload journal revision")
        lineage_offset = self.journal_generation - self.journal_revision - 1
        if (
            lineage_offset < 0
            or lineage_offset % 2 != 0
            or lineage_offset > 2 * _MAX_ROLLBACK_QUARANTINE_PAIRS
        ):
            raise RollbackValidationError(
                "rollback payload generation/revision lineage is incoherent"
            )
        if self.kind is RollbackPayloadKind.REQUEST and (
            self.phase is not RollbackPhase.PREPARED
            or self.journal_generation != 1
            or self.journal_revision != 0
            or self.payload_digest != self.request_digest
        ):
            raise RollbackValidationError("rollback request payload ref is incoherent")
        if self.kind is RollbackPayloadKind.PHASE_RECEIPT and (
            self.phase is RollbackPhase.PREPARED
            or self.journal_generation < 2
            or self.journal_revision < 1
        ):
            raise RollbackValidationError("rollback receipt payload ref is incoherent")
        expected_path = _payload_relative_path(
            self.rollback_id,
            self.kind,
            self.phase,
            self.journal_generation,
            self.journal_revision,
            self.payload_digest,
        )
        if self.relative_path != expected_path:
            raise RollbackValidationError(
                "rollback payload authoritative path mismatch"
            )
        if self.schema_version != "bb.rl.phase5.rollback-payload-ref.v1":
            raise RollbackValidationError("rollback payload ref schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "journal_generation": self.journal_generation,
            "journal_revision": self.journal_revision,
            "kind": self.kind.value,
            "payload_digest": self.payload_digest,
            "phase": self.phase.value,
            "relative_path": self.relative_path,
            "request_digest": self.request_digest,
            "rollback_id": self.rollback_id,
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackLeafError:
    adapter: str
    object_ref: str
    error_code: str
    error_digest: str

    def __post_init__(self) -> None:
        _require_id(self.adapter, "leaf error adapter")
        if (
            type(self.object_ref) is not str
            or not self.object_ref
            or len(self.object_ref) > 4096
        ):
            raise RollbackValidationError("leaf error object reference is invalid")
        _require_id(self.error_code, "leaf error code")
        _require_digest(self.error_digest, "leaf error digest")

    def canonical_object(self) -> dict[str, str]:
        return {
            "adapter": self.adapter,
            "error_code": self.error_code,
            "error_digest": self.error_digest,
            "object_ref": self.object_ref,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackPhaseReceipt:
    phase: RollbackPhase
    receipt_digests: tuple[str, ...]
    receipt_refs: tuple[RollbackPayloadRef, ...]
    leaf_errors: tuple[RollbackLeafError, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not RollbackPhase
            or self.phase is RollbackPhase.PREPARED
        ):
            raise RollbackValidationError("phase receipt has an invalid phase")
        if type(self.receipt_digests) is not tuple or not self.receipt_digests:
            raise RollbackValidationError(
                "phase receipt requires exact receipt digests"
            )
        for digest in self.receipt_digests:
            _require_digest(digest, "phase receipt digest")
        if len(set(self.receipt_digests)) != len(self.receipt_digests):
            raise RollbackValidationError("phase receipt digests must be unique")
        if (
            type(self.receipt_refs) is not tuple
            or len(self.receipt_refs) != len(self.receipt_digests)
            or any(type(ref) is not RollbackPayloadRef for ref in self.receipt_refs)
        ):
            raise RollbackValidationError(
                "phase receipt requires one exact authoritative ref per digest"
            )
        if any(
            ref.kind is not RollbackPayloadKind.PHASE_RECEIPT
            or ref.phase is not self.phase
            or ref.payload_digest != digest
            for ref, digest in zip(self.receipt_refs, self.receipt_digests, strict=True)
        ):
            raise RollbackValidationError(
                "phase receipt authoritative refs do not match digests"
            )
        if type(self.leaf_errors) is not tuple or any(
            type(error) is not RollbackLeafError for error in self.leaf_errors
        ):
            raise RollbackValidationError("phase leaf errors must be exact")
        if len({error.error_digest for error in self.leaf_errors}) != len(
            self.leaf_errors
        ):
            raise RollbackValidationError("phase leaf errors must be unique")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "leaf_errors": [error.canonical_object() for error in self.leaf_errors],
            "phase": self.phase.value,
            "receipt_digests": list(self.receipt_digests),
            "receipt_refs": [ref.canonical_object() for ref in self.receipt_refs],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackTerminalQuarantineRef:
    transaction_id: str
    rollback_id: str
    predecessor_generation: int
    predecessor_record_digest: str
    successor_generation: int
    successor_record_digest: str
    successor_raw_digest: str
    successor_name: str
    tombstone_name: str
    tombstone_raw_digest: str
    schema_version: str = "bb.rl.phase5.rollback-terminal-quarantine-ref.v1"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", self.transaction_id):
            raise RollbackValidationError(
                "terminal quarantine transaction id is invalid"
            )
        _require_id(self.rollback_id, "terminal quarantine rollback id")
        _require_int(
            self.predecessor_generation,
            "terminal quarantine predecessor generation",
            minimum=1,
        )
        _require_int(
            self.successor_generation,
            "terminal quarantine successor generation",
            minimum=2,
        )
        if self.successor_generation != self.predecessor_generation + 1:
            raise RollbackValidationError(
                "terminal quarantine generations are not adjacent"
            )
        for value, name in (
            (self.predecessor_record_digest, "predecessor record digest"),
            (self.successor_record_digest, "successor record digest"),
            (self.successor_raw_digest, "successor raw digest"),
            (self.tombstone_raw_digest, "tombstone raw digest"),
        ):
            _require_digest(value, f"terminal quarantine {name}")
        expected_successor, expected_tombstone = (
            _PinnedSignedDirectory._rollback_quarantine_names(
                self.transaction_id,
                self.rollback_id,
                self.successor_record_digest,
            )
        )
        if (
            self.successor_name != expected_successor
            or self.tombstone_name != expected_tombstone
        ):
            raise RollbackValidationError(
                "terminal quarantine artifact names are invalid"
            )
        if self.schema_version != "bb.rl.phase5.rollback-terminal-quarantine-ref.v1":
            raise RollbackValidationError("terminal quarantine ref schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "predecessor_generation": self.predecessor_generation,
            "predecessor_record_digest": self.predecessor_record_digest,
            "rollback_id": self.rollback_id,
            "schema_version": self.schema_version,
            "successor_generation": self.successor_generation,
            "successor_name": self.successor_name,
            "successor_raw_digest": self.successor_raw_digest,
            "successor_record_digest": self.successor_record_digest,
            "tombstone_name": self.tombstone_name,
            "tombstone_raw_digest": self.tombstone_raw_digest,
            "transaction_id": self.transaction_id,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class RollbackJournalRecord:
    rollback_id: str
    request_digest: str
    request_payload_ref: RollbackPayloadRef
    generation: int
    revision: int
    phase: RollbackPhase
    phase_receipts: tuple[RollbackPhaseReceipt, ...]
    previous_record_digest: str | None
    terminal_quarantine_refs: tuple[RollbackTerminalQuarantineRef, ...] = ()
    schema_version: str = "bb.rl.phase5.rollback-journal.v3"

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "rollback id")
        _require_digest(self.request_digest, "rollback request digest")
        if (
            type(self.request_payload_ref) is not RollbackPayloadRef
            or self.request_payload_ref.kind is not RollbackPayloadKind.REQUEST
            or self.request_payload_ref.rollback_id != self.rollback_id
            or self.request_payload_ref.request_digest != self.request_digest
        ):
            raise RollbackValidationError(
                "rollback journal request payload ref mismatch"
            )
        _require_int(self.generation, "journal generation", minimum=1)
        _require_int(self.revision, "journal revision")
        if type(self.phase) is not RollbackPhase:
            raise RollbackValidationError("journal phase must be exact")
        if type(self.phase_receipts) is not tuple or any(
            type(item) is not RollbackPhaseReceipt for item in self.phase_receipts
        ):
            raise RollbackValidationError("journal phase receipts must be exact")
        if self.revision != len(self.phase_receipts):
            raise RollbackValidationError("journal revision must match receipt count")
        if type(self.terminal_quarantine_refs) is not tuple or any(
            type(item) is not RollbackTerminalQuarantineRef
            for item in self.terminal_quarantine_refs
        ):
            raise RollbackValidationError(
                "journal terminal quarantine refs must be exact"
            )
        if any(
            item.rollback_id != self.rollback_id
            for item in self.terminal_quarantine_refs
        ):
            raise RollbackValidationError(
                "journal terminal quarantine rollback binding mismatch"
            )
        if len({item.transaction_id for item in self.terminal_quarantine_refs}) != len(
            self.terminal_quarantine_refs
        ):
            raise RollbackValidationError(
                "journal terminal quarantine refs must be unique"
            )
        if self.generation != (
            self.revision + 1 + 2 * len(self.terminal_quarantine_refs)
        ):
            raise RollbackValidationError(
                "journal generation/revision lineage is incoherent"
            )
        if len(self.terminal_quarantine_refs) > _MAX_ROLLBACK_QUARANTINE_PAIRS:
            raise RollbackValidationError(
                "journal terminal quarantine ref count exceeds fixed bound"
            )
        successor_generations = tuple(
            item.successor_generation for item in self.terminal_quarantine_refs
        )
        if (
            successor_generations != tuple(sorted(successor_generations))
            or len(set(successor_generations)) != len(successor_generations)
            or any(
                generation >= self.generation for generation in successor_generations
            )
        ):
            raise RollbackValidationError(
                "journal terminal quarantine chronology is invalid"
            )
        previous_receipt_generation = 1
        for index, receipt in enumerate(self.phase_receipts):
            expected_revision = index + 1
            receipt_generations = {
                ref.journal_generation for ref in receipt.receipt_refs
            }
            if (
                len(receipt_generations) != 1
                or next(iter(receipt_generations)) <= previous_receipt_generation
                or next(iter(receipt_generations)) > self.generation
                or any(
                    ref.rollback_id != self.rollback_id
                    or ref.request_digest != self.request_digest
                    or ref.journal_revision != expected_revision
                    for ref in receipt.receipt_refs
                )
            ):
                raise RollbackValidationError(
                    "journal receipt authoritative ref binding mismatch"
                )
            previous_receipt_generation = next(iter(receipt_generations))
            receipt_generation = next(iter(receipt_generations))
            restoration_count = sum(
                1
                for ref in self.terminal_quarantine_refs
                if ref.successor_generation < receipt_generation
            )
            if receipt_generation != (expected_revision + 1 + 2 * restoration_count):
                raise RollbackValidationError(
                    "journal receipt generation lineage is incoherent"
                )
            is_last = index == len(self.phase_receipts) - 1
            if receipt.phase is RollbackPhase.QUARANTINED:
                if not is_last:
                    raise RollbackValidationError(
                        "terminal quarantine must be the final journal receipt"
                    )
            elif receipt.phase is not _PHASE_ORDER[index + 1]:
                raise RollbackValidationError(
                    "journal phase receipt sequence is not monotonic"
                )
        all_receipt_digests = tuple(
            digest
            for receipt in self.phase_receipts
            for digest in receipt.receipt_digests
        )
        if len(set(all_receipt_digests)) != len(all_receipt_digests):
            raise RollbackValidationError(
                "journal receipt payload digests must be globally unique"
            )
        if self.generation == 1:
            if (
                self.phase is not RollbackPhase.PREPARED
                or self.phase_receipts
                or self.previous_record_digest is not None
                or self.terminal_quarantine_refs
            ):
                raise RollbackValidationError("initial journal record is invalid")
        else:
            _require_digest(
                self.previous_record_digest,
                "previous journal record digest",
            )
            if self.phase is RollbackPhase.PREPARED:
                if self.phase_receipts or not self.terminal_quarantine_refs:
                    raise RollbackValidationError(
                        "restored prepared journal is invalid"
                    )
            elif (
                not self.phase_receipts
                or self.phase_receipts[-1].phase is not self.phase
            ):
                raise RollbackValidationError(
                    "journal phase must match its last receipt"
                )
        if self.schema_version != "bb.rl.phase5.rollback-journal.v3":
            raise RollbackValidationError("rollback journal schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "phase": self.phase.value,
            "phase_receipts": [item.canonical_object() for item in self.phase_receipts],
            "previous_record_digest": self.previous_record_digest,
            "request_digest": self.request_digest,
            "request_payload_ref": self.request_payload_ref.canonical_object(),
            "revision": self.revision,
            "rollback_id": self.rollback_id,
            "terminal_quarantine_refs": [
                item.canonical_object() for item in self.terminal_quarantine_refs
            ],
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class ActiveApprovedTupleState:
    generation: int
    approved_tuple: ActiveApprovedTuple
    operation_id: str
    previous_state_digest: str | None
    schema_version: str = "bb.rl.phase5.active-approved-tuple-state.v1"

    def __post_init__(self) -> None:
        _require_int(self.generation, "active tuple generation", minimum=1)
        if type(self.approved_tuple) is not ActiveApprovedTuple:
            raise RollbackValidationError(
                "approved tuple state requires an exact tuple"
            )
        _require_id(self.operation_id, "active tuple operation id")
        if self.generation == 1:
            if self.previous_state_digest is not None:
                raise RollbackValidationError(
                    "initial active tuple cannot have a predecessor"
                )
        else:
            _require_digest(
                self.previous_state_digest, "previous active tuple state digest"
            )
        if self.schema_version != "bb.rl.phase5.active-approved-tuple-state.v1":
            raise RollbackValidationError("active tuple state schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "approved_tuple": self.approved_tuple.canonical_object(),
            "generation": self.generation,
            "operation_id": self.operation_id,
            "previous_state_digest": self.previous_state_digest,
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class ActiveApprovedTupleHistoryEntry:
    state: ActiveApprovedTupleState
    state_digest: str

    def __post_init__(self) -> None:
        if type(self.state) is not ActiveApprovedTupleState:
            raise RollbackValidationError("history entry state must be exact")
        _require_digest(self.state_digest, "active tuple history digest")
        if self.state_digest != self.state.digest:
            raise RollbackValidationError("active tuple history digest mismatch")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "state": self.state.canonical_object(),
            "state_digest": self.state_digest,
        }


class DependentObjectKind(str, Enum):
    REWARD = "reward"
    CHECKPOINT = "checkpoint"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class DependentOwnership:
    registration_id: str
    approved_tuple_digest: str
    episode_id: str
    run_id: str
    object_kind: DependentObjectKind
    object_ref: ImmutableObjectRef
    parent_refs: tuple[ImmutableObjectRef, ...] = ()
    schema_version: str = "bb.rl.phase5.dependent-ownership.v1"

    def __post_init__(self) -> None:
        _require_id(self.registration_id, "dependent registration id")
        _require_digest(self.approved_tuple_digest, "dependent approved tuple digest")
        _require_id(self.episode_id, "dependent episode id")
        _require_id(self.run_id, "dependent run id")
        if type(self.object_kind) is not DependentObjectKind:
            raise RollbackValidationError("dependent object kind must be exact")
        if type(self.object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError("dependent object ref must be exact")
        if type(self.parent_refs) is not tuple or any(
            type(item) is not ImmutableObjectRef for item in self.parent_refs
        ):
            raise RollbackValidationError("dependent parents must be exact")
        identities = tuple(item.identity_digest for item in self.parent_refs)
        if identities != tuple(sorted(identities)) or len(set(identities)) != len(
            identities
        ):
            raise RollbackValidationError("dependent parents must be unique and sorted")
        if self.object_ref.identity_digest in identities:
            raise RollbackValidationError("dependent object cannot own itself")
        if self.schema_version != "bb.rl.phase5.dependent-ownership.v1":
            raise RollbackValidationError("dependent ownership schema is invalid")

    def canonical_object(self) -> dict[str, Any]:
        return {
            "approved_tuple_digest": self.approved_tuple_digest,
            "episode_id": self.episode_id,
            "object_kind": self.object_kind.value,
            "object_ref": self.object_ref.canonical_object(),
            "parent_refs": [item.canonical_object() for item in self.parent_refs],
            "registration_id": self.registration_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(canonical_json_bytes(self.canonical_object()))


@dataclass(frozen=True, slots=True)
class DependentQuarantineReceipt:
    rollback_id: str
    cause_digest: str
    object_ref: ImmutableObjectRef
    ownership_digest: str
    causal_root_digests: tuple[str, ...]
    generation: int
    schema_version: str = "bb.rl.phase5.dependent-quarantine-receipt.v1"

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "dependent quarantine rollback id")
        _require_digest(self.cause_digest, "dependent quarantine cause digest")
        if type(self.object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError(
                "dependent quarantine object ref must be exact"
            )
        _require_digest(self.ownership_digest, "dependent ownership digest")
        if type(self.causal_root_digests) is not tuple or not self.causal_root_digests:
            raise RollbackValidationError("dependent quarantine requires causal roots")
        for digest in self.causal_root_digests:
            _require_digest(digest, "dependent quarantine causal root digest")
        if self.causal_root_digests != tuple(sorted(set(self.causal_root_digests))):
            raise RollbackValidationError(
                "dependent causal roots must be unique and sorted"
            )
        _require_int(self.generation, "dependent quarantine generation", minimum=2)
        if self.schema_version != "bb.rl.phase5.dependent-quarantine-receipt.v1":
            raise RollbackValidationError(
                "dependent quarantine receipt schema is invalid"
            )

    def canonical_object(self) -> dict[str, Any]:
        return {
            "causal_root_digests": list(self.causal_root_digests),
            "cause_digest": self.cause_digest,
            "generation": self.generation,
            "object_ref": self.object_ref.canonical_object(),
            "ownership_digest": self.ownership_digest,
            "rollback_id": self.rollback_id,
            "schema_version": self.schema_version,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(canonical_json_bytes(self.canonical_object()))


@dataclass(frozen=True, slots=True)
class DependentOwnershipRecord:
    generation: int
    ownership: DependentOwnership
    promotion_eligible: bool
    export_eligible: bool
    quarantine_receipts: tuple[DependentQuarantineReceipt, ...]
    previous_record_digest: str | None
    schema_version: str = "bb.rl.phase5.dependent-ownership-record.v1"

    def __post_init__(self) -> None:
        _require_int(self.generation, "dependent generation", minimum=1)
        if type(self.ownership) is not DependentOwnership:
            raise RollbackValidationError("dependent ownership record must be exact")
        _require_bool(self.promotion_eligible, "promotion eligibility")
        _require_bool(self.export_eligible, "export eligibility")
        if self.promotion_eligible != self.export_eligible:
            raise RollbackValidationError(
                "promotion/export eligibility must fail closed together"
            )
        if type(self.quarantine_receipts) is not tuple or any(
            type(item) is not DependentQuarantineReceipt
            for item in self.quarantine_receipts
        ):
            raise RollbackValidationError("dependent quarantine receipts must be exact")
        if self.generation != len(self.quarantine_receipts) + 1:
            raise RollbackValidationError(
                "dependent generation must match quarantine history"
            )
        if self.generation == 1:
            if (
                not self.promotion_eligible
                or self.quarantine_receipts
                or self.previous_record_digest is not None
            ):
                raise RollbackValidationError("new dependent must begin eligible")
        else:
            if self.promotion_eligible or not self.quarantine_receipts:
                raise RollbackValidationError(
                    "quarantined dependent cannot be eligible"
                )
            _require_digest(
                self.previous_record_digest, "previous dependent record digest"
            )
        event_keys = tuple(
            (item.rollback_id, item.cause_digest) for item in self.quarantine_receipts
        )
        if len(set(event_keys)) != len(event_keys):
            raise RollbackValidationError("dependent quarantine event must be unique")
        if any(
            item.object_ref != self.ownership.object_ref
            or item.ownership_digest != self.ownership.digest
            for item in self.quarantine_receipts
        ):
            raise RollbackValidationError(
                "dependent quarantine receipt ownership mismatch"
            )
        if self.schema_version != "bb.rl.phase5.dependent-ownership-record.v1":
            raise RollbackValidationError(
                "dependent ownership record schema is invalid"
            )

    def canonical_object(self) -> dict[str, Any]:
        return {
            "export_eligible": self.export_eligible,
            "generation": self.generation,
            "ownership": self.ownership.canonical_object(),
            "previous_record_digest": self.previous_record_digest,
            "promotion_eligible": self.promotion_eligible,
            "quarantine_receipts": [
                item.canonical_object() for item in self.quarantine_receipts
            ],
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_object())

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical_bytes())


class RollbackJournalStore(Protocol):
    def prepare(
        self, rollback_id: str, request_digest: str, request_payload: bytes
    ) -> RollbackJournalRecord: ...

    def get(self, rollback_id: str) -> RollbackJournalRecord | None: ...
    def get_request(self, rollback_id: str) -> bytes: ...
    def get_request_ref(self, rollback_id: str) -> RollbackPayloadRef: ...

    def advance(
        self,
        rollback_id: str,
        *,
        expected_generation: int,
        expected_revision: int,
        phase: RollbackPhase,
        receipt_digests: tuple[str, ...],
        receipt_payloads: tuple[bytes, ...],
        leaf_errors: tuple[RollbackLeafError, ...] = (),
    ) -> RollbackJournalRecord: ...

    def get_receipt_payload(self, rollback_id: str, receipt_digest: str) -> bytes: ...
    def get_receipt_ref(
        self, rollback_id: str, receipt_digest: str
    ) -> RollbackPayloadRef: ...

    def history(self, rollback_id: str) -> tuple[RollbackJournalRecord, ...]: ...


class ActiveApprovedTupleStore(Protocol):
    def get(self) -> ActiveApprovedTupleState | None: ...

    def compare_and_swap(
        self,
        expected_generation: int | None,
        approved_tuple: ActiveApprovedTuple,
        operation_id: str,
    ) -> ActiveApprovedTupleState: ...

    def history(self) -> tuple[ActiveApprovedTupleHistoryEntry, ...]: ...


class DependentQuarantineStore(Protocol):
    def register(self, ownership: DependentOwnership) -> DependentOwnershipRecord: ...

    def get(
        self, object_ref: ImmutableObjectRef
    ) -> DependentOwnershipRecord | None: ...

    def quarantine_causal(
        self,
        rollback_id: str,
        cause_digest: str,
        root_refs: tuple[ImmutableObjectRef, ...],
    ) -> tuple[DependentQuarantineReceipt, ...]: ...

    def list_owned(
        self,
        *,
        approved_tuple_digest: str | None = None,
        episode_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[DependentOwnershipRecord, ...]: ...

    def assert_promotion_eligible(self, object_ref: ImmutableObjectRef) -> None: ...

    def assert_export_eligible(self, object_ref: ImmutableObjectRef) -> None: ...
    def read_fence(self) -> Iterator[tuple[DependentOwnershipRecord, ...]]: ...


def _rename_noreplace(
    source: str,
    destination: str,
    directory_fd: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = libc.renameatx_np
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            directory_fd,
            source_bytes,
            directory_fd,
            destination_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            directory_fd,
            source_bytes,
            directory_fd,
            destination_bytes,
            0x00000001,
        )
    else:
        raise RollbackCorruptionError("atomic no-replace rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination,
        )


def _rename_noreplace_between(
    source: str,
    destination: str,
    source_directory_fd: int,
    destination_directory_fd: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = libc.renameatx_np
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            source_directory_fd,
            source_bytes,
            destination_directory_fd,
            destination_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            source_directory_fd,
            source_bytes,
            destination_directory_fd,
            destination_bytes,
            0x00000001,
        )
    else:
        raise RollbackCorruptionError("atomic no-replace rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination,
        )


@dataclass(slots=True)
class _HeldStoreFile:
    name: str
    fd: int
    path_directory_fd: int
    identity: tuple[int, int, int, int, int, int, int, int]
    raw: bytes

    @staticmethod
    def _identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            stat.S_IMODE(value.st_mode),
            value.st_nlink,
            value.st_size,
            value.st_ctime_ns,
        )

    @classmethod
    def capture(
        cls,
        store: "_PinnedSignedDirectory",
        name: str,
        *,
        directory_fd: int | None = None,
    ) -> "_HeldStoreFile":
        path_directory_fd = (
            store._path_directory_fd(name) if directory_fd is None else directory_fd
        )
        if directory_fd is None:
            fd = store._open_regular(name, os.O_RDONLY)
        else:
            fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        try:
            value = os.fstat(fd)
            if (
                not stat.S_ISREG(value.st_mode)
                or stat.S_IMODE(value.st_mode) != 0o600
                or value.st_nlink != 1
                or (value.st_uid, value.st_gid) != store._owner
                or value.st_size > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError("recovery authority file is not exact")
            raw = os.pread(fd, value.st_size + 1, 0)
            if len(raw) != value.st_size:
                raise RollbackCorruptionError(
                    "recovery authority file changed during capture"
                )
            return cls(
                name,
                fd,
                path_directory_fd,
                cls._identity(value),
                raw,
            )
        except BaseException:
            os.close(fd)
            raise

    def revalidate(
        self,
        store: "_PinnedSignedDirectory",
        *,
        path_name: str | None = None,
    ) -> None:
        expected_name = self.name if path_name is None else path_name
        descriptor_value = os.fstat(self.fd)
        path_value = os.stat(
            expected_name,
            dir_fd=self.path_directory_fd,
            follow_symlinks=False,
        )
        if (
            self._identity(descriptor_value) != self.identity
            or self._identity(path_value) != self.identity
            or os.pread(self.fd, len(self.raw) + 1, 0) != self.raw
        ):
            raise RollbackCorruptionError("recovery authority identity changed")

    def refresh_path_identity(
        self,
        store: "_PinnedSignedDirectory",
        path_name: str,
    ) -> None:
        descriptor_value = os.fstat(self.fd)
        path_value = os.stat(
            path_name,
            dir_fd=self.path_directory_fd,
            follow_symlinks=False,
        )
        descriptor_identity = self._identity(descriptor_value)
        path_identity = self._identity(path_value)
        if (
            descriptor_identity[:7] != self.identity[:7]
            or path_identity != descriptor_identity
            or os.pread(self.fd, len(self.raw) + 1, 0) != self.raw
        ):
            raise RollbackCorruptionError("renamed recovery authority identity changed")
        self.identity = descriptor_identity

    def close(self) -> None:
        os.close(self.fd)


@dataclass(slots=True)
class _RollbackRecoveryCapsule:
    transaction_id: str
    intent: _HeldStoreFile
    predecessor: _HeldStoreFile
    predecessor_commit: _HeldStoreFile
    successor: _HeldStoreFile | None
    head_name: str
    displaced_name: str
    candidate_name: str
    quarantine_name: str
    tombstone_name: str
    successor_history_name: str
    successor_commit_name: str
    state: str
    candidate: _HeldStoreFile | None = None
    installed_head: _HeldStoreFile | None = None

    def close(self) -> None:
        if self.successor is not None:
            self.successor.close()
        if self.candidate is not None:
            self.candidate.close()
        if self.installed_head is not None:
            self.installed_head.close()
        self.predecessor_commit.close()
        self.predecessor.close()
        self.intent.close()


class _PublicationTransaction:
    def __init__(
        self,
        store: "_PinnedSignedDirectory",
        revalidate: Any,
    ) -> None:
        self.store = store
        self.revalidate = revalidate
        self.created: set[str] = set()
        self.replaced: dict[str, bytes | None] = {}
        self.mutated_replacements: set[str] = set()
        self.temps: set[str] = set()
        self.transaction_id = uuid.uuid4().hex

    def capture_replaced(self, name: str, old_payload: bytes | None) -> None:
        self.replaced.setdefault(name, old_payload)

    def mark_replaced(self, name: str) -> None:
        self.mutated_replacements.add(name)

    def rollback(self) -> None:
        failures: list[BaseException] = []
        for name in sorted(self.created):
            try:
                os.unlink(name, dir_fd=self.store._root_fd)
            except FileNotFoundError:
                pass
            except BaseException as error:
                failures.append(error)
        for name in sorted(self.mutated_replacements):
            old_payload = self.replaced[name]
            try:
                if old_payload is None:
                    try:
                        os.unlink(name, dir_fd=self.store._root_fd)
                    except FileNotFoundError:
                        pass
                else:
                    self.store._rollback_replaced_head(
                        name,
                        old_payload,
                        self.transaction_id,
                    )
            except BaseException as error:
                failures.append(error)
        for name in sorted(self.temps):
            try:
                os.unlink(name, dir_fd=self.store._root_fd)
            except FileNotFoundError:
                pass
            except BaseException as error:
                failures.append(error)
        try:
            os.fsync(self.store._root_fd)
        except BaseException as error:
            failures.append(error)
        if failures:
            raise RollbackCorruptionError(
                "rollback publication transaction could not restore prior state"
            ) from failures[0]


class _PinnedSignedDirectory:
    @contextmanager
    def _publication_transaction(
        self,
        revalidate: Any,
    ) -> Iterator[_PublicationTransaction]:
        if self._publication_tx is not None:
            raise RollbackCorruptionError(
                "nested rollback publication transaction is forbidden"
            )
        transaction = _PublicationTransaction(self, revalidate)
        self._publication_tx = transaction
        try:
            yield transaction
        except BaseException as operation_error:
            try:
                transaction.rollback()
            except BaseException as rollback_error:
                raise rollback_error from operation_error
            raise
        finally:
            self._publication_tx = None

    def __init__(
        self,
        root: str | Path,
        *,
        authority_key: bytes,
        domain: str,
        root_fd: int | None = None,
    ) -> None:
        if type(authority_key) is not bytes or len(authority_key) < 32:
            raise RollbackValidationError(
                "rollback authority key must be at least 32 bytes"
            )
        _require_id(domain, "rollback store domain")
        requested = Path(root)
        if root_fd is None:
            if requested.exists() and requested.is_symlink():
                raise RollbackCorruptionError("rollback store root cannot be a symlink")
            requested.mkdir(mode=0o700, parents=True, exist_ok=True)
            resolved = requested.resolve(strict=True)
            if requested.absolute() != resolved:
                raise RollbackCorruptionError(
                    "rollback store root cannot use a path alias"
                )
            self.root = resolved
        else:
            self.root = requested.absolute()
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        self._root_fd = (
            os.dup(root_fd) if root_fd is not None else os.open(self.root, flags)
        )
        self._root_stat = os.fstat(self._root_fd)
        if (
            self._root_stat.st_uid != os.geteuid()
            or self._root_stat.st_gid != os.getegid()
        ):
            os.close(self._root_fd)
            raise RollbackCorruptionError(
                "rollback store root owner is not the effective owner"
            )
        self._owner = (self._root_stat.st_uid, self._root_stat.st_gid)
        self._quarantine_fd = -1
        self._terminal_fd = -1
        self._terminal_stat: os.stat_result | None = None
        self._quarantine_stat: os.stat_result | None = None
        self._lock_fd = -1
        self._authority_key = authority_key
        self._domain = domain
        self._thread_lock = threading.RLock()
        self._publication_tx: _PublicationTransaction | None = None
        self._cleanup_forward_active = False
        self._cleanup_pending_checkpoint_factory: Any | None = None
        self._cleanup_recovery_replace_boundary: str | None = None
        self._cleanup_recovery_replace_temp: str | None = None
        self._cleanup_recovery_replace_destination: str | None = None
        self._cleanup_recovery_replace_proof: dict[str, object] | None = None
        self._cleanup_recovery_checkpoint: Any | None = None
        self._cleanup_resumed_forward = False
        self._closed = False
        try:
            self._validate_dir_mode(self._root_stat, "rollback store root")
            try:
                os.mkdir(".quarantine", mode=0o700, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            except FileExistsError:
                pass
            self._quarantine_fd = os.open(".quarantine", flags, dir_fd=self._root_fd)
            self._quarantine_stat = os.fstat(self._quarantine_fd)
            self._validate_dir_mode(
                self._quarantine_stat,
                "rollback quarantine directory",
            )
            if self._domain == "rollback-journal":
                try:
                    os.mkdir(
                        _ROLLBACK_TERMINAL_DIRECTORY,
                        mode=0o700,
                        dir_fd=self._root_fd,
                    )
                    os.fsync(self._root_fd)
                except FileExistsError:
                    pass
                self._terminal_fd = os.open(
                    _ROLLBACK_TERMINAL_DIRECTORY,
                    flags,
                    dir_fd=self._root_fd,
                )
                self._terminal_stat = os.fstat(self._terminal_fd)
                self._validate_dir_mode(
                    self._terminal_stat,
                    "rollback terminal directory",
                )
            self._lock_fd = self._open_regular(
                ".store.lock", os.O_RDWR | os.O_CREAT, 0o600
            )
            fcntl.flock(self._root_fd, fcntl.LOCK_EX)
            try:
                self._validate_root()
                self._cleanup_abandoned_temps()
            finally:
                fcntl.flock(self._root_fd, fcntl.LOCK_UN)
        except BaseException:
            for descriptor in (
                self._lock_fd,
                self._terminal_fd,
                self._quarantine_fd,
                self._root_fd,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise

    def _validate_dir_mode(
        self,
        value: os.stat_result,
        name: str,
    ) -> None:
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o700
            or (value.st_uid, value.st_gid) != self._owner
        ):
            raise RollbackCorruptionError(
                f"{name} must be a trusted-owner 0700 directory"
            )

    @staticmethod
    def _journal_version_name(
        rollback_id: str,
        generation: int,
        record_digest: str,
        suffix: str,
    ) -> str:
        return f"journal.{rollback_id}.g{generation:020d}.{record_digest[7:]}.{suffix}"

    @staticmethod
    def _rollback_quarantine_names(
        transaction_id: str,
        rollback_id: str,
        successor_record_digest: str,
    ) -> tuple[str, str]:
        rollback_identity_digest = canonical_digest(
            _require_id(rollback_id, "rollback id").encode()
        )[7:]
        base = (
            f"rollback-quarantine.{rollback_identity_digest}."
            f"{transaction_id}.{successor_record_digest[7:]}"
        )
        return f"{base}.successor", f"{base}.tombstone"

    @staticmethod
    def _decode_recovery_identity(
        value: object,
        name: str,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        if (
            type(value) is not list
            or len(value) != 8
            or any(type(item) is not int or item < 0 for item in value)
        ):
            raise RollbackCorruptionError(f"{name} recovery identity is invalid")
        return tuple(value)

    def _rollback_intent_bytes(
        self,
        transaction_id: str,
        previous_raw: bytes,
        successor_raw: bytes,
    ) -> bytes:
        previous = _journal_from_object(
            self._verify_signed(previous_raw, "journal-record")
        )
        successor = _journal_from_object(
            self._verify_signed(successor_raw, "journal-record")
        )
        if (
            successor.rollback_id != previous.rollback_id
            or successor.generation != previous.generation + 1
            or successor.previous_record_digest != previous.digest
        ):
            raise RollbackCorruptionError(
                "rollback transaction records are not exact successors"
            )
        return self._signed_bytes(
            "publication-rollback-intent",
            {
                "domain": self._domain,
                "prior_generation": previous.generation,
                "prior_raw_sha256": canonical_digest(previous_raw),
                "prior_record_digest": previous.digest,
                "relationship": "exact-successor",
                "rollback_id": previous.rollback_id,
                "schema_version": ("bb.rl.phase5.publication-rollback-intent.v1"),
                "successor_generation": successor.generation,
                "successor_raw_sha256": canonical_digest(successor_raw),
                "successor_record_digest": successor.digest,
                "transaction_id": transaction_id,
                "state": "active",
                "prior_commit_identity": None,
                "prior_history_identity": None,
                "quarantine_name": None,
                "successor_quarantine_identity": None,
            },
        )

    def _rollback_replaced_head(
        self,
        head_name: str,
        previous_raw: bytes,
        transaction_id: str,
    ) -> None:
        successor_raw = self._read(head_name)
        if successor_raw is None:
            raise RollbackCorruptionError(
                "rollback transaction successor head is missing"
            )
        if successor_raw == previous_raw:
            return
        intent_name = f".{self._domain}.{transaction_id}.transaction-rollback"
        intent_raw = self._rollback_intent_bytes(
            transaction_id,
            previous_raw,
            successor_raw,
        )
        self._create_immutable(intent_name, intent_raw)
        capsule = self._preflight_transaction_rollback_intent(intent_name)
        try:
            self._recover_transaction_rollback(capsule)
        finally:
            capsule.close()

    def _preflight_transaction_rollback_intent(
        self,
        name: str,
        *,
        recovery_directory_fd: int | None = None,
    ) -> _RollbackRecoveryCapsule:
        if self._domain != "rollback-journal":
            raise RollbackCorruptionError(
                "transaction rollback intent has no recovery authority"
            )
        match = re.fullmatch(
            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
            r"transaction-rollback",
            name,
        )
        if match is None:
            raise RollbackCorruptionError("transaction rollback intent name is invalid")
        transaction_id = match.group(1)
        held: list[_HeldStoreFile] = []

        def recovery_location(candidate_name: str) -> int | None:
            root_exists = self._read(candidate_name) is not None
            if recovery_directory_fd is None:
                return None if root_exists else -1
            try:
                os.stat(
                    candidate_name,
                    dir_fd=recovery_directory_fd,
                    follow_symlinks=False,
                )
                staged_exists = True
            except FileNotFoundError:
                staged_exists = False
            if root_exists and staged_exists:
                raise RollbackCorruptionError(
                    "rollback recovery authority is duplicated"
                )
            if staged_exists:
                return recovery_directory_fd
            return None if root_exists else -1

        def recovery_exists(candidate_name: str) -> bool:
            return recovery_location(candidate_name) != -1

        try:
            intent_file = _HeldStoreFile.capture(
                self,
                name,
                directory_fd=recovery_directory_fd,
            )
            held.append(intent_file)
            intent = _require_object(
                self._verify_signed(
                    intent_file.raw,
                    "publication-rollback-intent",
                ),
                frozenset(
                    (
                        "domain",
                        "prior_generation",
                        "prior_raw_sha256",
                        "prior_record_digest",
                        "relationship",
                        "rollback_id",
                        "schema_version",
                        "prior_commit_identity",
                        "prior_history_identity",
                        "quarantine_name",
                        "successor_generation",
                        "successor_raw_sha256",
                        "successor_record_digest",
                        "successor_quarantine_identity",
                        "transaction_id",
                        "state",
                    )
                ),
                "publication rollback intent",
            )
            if (
                intent["schema_version"]
                != "bb.rl.phase5.publication-rollback-intent.v1"
                or intent["domain"] != self._domain
                or intent["transaction_id"] != transaction_id
                or intent["relationship"] != "exact-successor"
            ):
                raise RollbackCorruptionError(
                    "publication rollback intent binding is invalid"
                )
            intent_state = intent["state"]
            if intent_state not in (
                "active",
                "cleanup_pending",
                "quarantined",
            ):
                raise RollbackCorruptionError(
                    "publication rollback intent state is invalid"
                )
            rollback_id = intent["rollback_id"]
            _require_id(rollback_id, "rollback id")
            prior_generation = intent["prior_generation"]
            successor_generation = intent["successor_generation"]
            _require_int(prior_generation, "prior generation", minimum=1)
            _require_int(
                successor_generation,
                "successor generation",
                minimum=2,
            )
            if successor_generation != prior_generation + 1:
                raise RollbackCorruptionError(
                    "publication rollback generations are not adjacent"
                )
            for field_name in (
                "prior_raw_sha256",
                "prior_record_digest",
                "successor_raw_sha256",
                "successor_record_digest",
            ):
                _require_digest(intent[field_name], field_name)
            quarantine_name, tombstone_name = self._rollback_quarantine_names(
                transaction_id,
                rollback_id,
                intent["successor_record_digest"],
            )
            terminal_identities: (
                tuple[
                    tuple[int, int, int, int, int, int, int, int],
                    tuple[int, int, int, int, int, int, int, int],
                    tuple[int, int, int, int, int, int, int, int],
                ]
                | None
            ) = None
            if intent_state == "quarantined":
                if intent["quarantine_name"] != quarantine_name:
                    raise RollbackCorruptionError(
                        "rollback quarantine name binding is invalid"
                    )
                terminal_identities = (
                    self._decode_recovery_identity(
                        intent["prior_history_identity"],
                        "prior history",
                    ),
                    self._decode_recovery_identity(
                        intent["prior_commit_identity"],
                        "prior commit",
                    ),
                    self._decode_recovery_identity(
                        intent["successor_quarantine_identity"],
                        "successor quarantine",
                    ),
                )
            elif any(
                intent[field_name] is not None
                for field_name in (
                    "prior_commit_identity",
                    "prior_history_identity",
                    "quarantine_name",
                    "successor_quarantine_identity",
                )
            ):
                raise RollbackCorruptionError(
                    "non-terminal rollback intent has quarantine bindings"
                )
            prior_history_name = self._journal_version_name(
                rollback_id,
                prior_generation,
                intent["prior_record_digest"],
                "history",
            )
            predecessor = _HeldStoreFile.capture(
                self,
                prior_history_name,
            )
            held.append(predecessor)
            if canonical_digest(predecessor.raw) != intent["prior_raw_sha256"]:
                raise RollbackCorruptionError(
                    "publication rollback predecessor history is invalid"
                )
            prior = _journal_from_object(
                self._verify_signed(
                    predecessor.raw,
                    "journal-record",
                )
            )
            if (
                prior.rollback_id != rollback_id
                or prior.generation != prior_generation
                or prior.digest != intent["prior_record_digest"]
            ):
                raise RollbackCorruptionError(
                    "publication rollback predecessor binding is invalid"
                )
            prior_commit_name = self._journal_version_name(
                rollback_id,
                prior_generation,
                prior.digest,
                "commit",
            )
            predecessor_commit = _HeldStoreFile.capture(
                self,
                prior_commit_name,
            )
            held.append(predecessor_commit)
            self._verify_commit(
                predecessor_commit.raw,
                identity=rollback_id,
                generation=prior_generation,
                record_digest=prior.digest,
            )
            if terminal_identities is not None and (
                predecessor.identity != terminal_identities[0]
                or predecessor_commit.identity != terminal_identities[1]
            ):
                raise RollbackCorruptionError(
                    "rollback quarantine predecessor identity changed"
                )
            successor_history_name = self._journal_version_name(
                rollback_id,
                successor_generation,
                intent["successor_record_digest"],
                "history",
            )
            successor_commit_name = self._journal_version_name(
                rollback_id,
                successor_generation,
                intent["successor_record_digest"],
                "commit",
            )
            if (
                self._read(successor_history_name) is not None
                or self._read(successor_commit_name) is not None
            ):
                raise RollbackCorruptionError(
                    "publication rollback successor is committed or staged"
                )
            head_name = f"journal.{rollback_id}.head"
            displaced_name = f".{self._domain}.{transaction_id}.displaced-head"
            candidate_name = f".{self._domain}.{transaction_id}.prior-candidate"
            head_exists = self._read(head_name) is not None
            displaced_exists = recovery_exists(displaced_name)
            quarantine_exists = self._read(quarantine_name) is not None
            if self._read(tombstone_name) is not None:
                raise RollbackCorruptionError(
                    "terminal rollback tombstone conflicts with active intent"
                )
            successor: _HeldStoreFile | None = None
            installed_head: _HeldStoreFile | None = None
            if intent_state == "active":
                if quarantine_exists:
                    raise RollbackCorruptionError(
                        "active rollback has terminal quarantine authority"
                    )
                if head_exists and not displaced_exists:
                    head_file = _HeldStoreFile.capture(self, head_name)
                    held.append(head_file)
                    if (
                        canonical_digest(head_file.raw)
                        != intent["successor_raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "publication rollback head state conflicts"
                        )
                    successor = head_file
                    state = "successor_at_head"
                elif displaced_exists:
                    successor_location = recovery_location(displaced_name)
                    assert successor_location != -1
                    successor = _HeldStoreFile.capture(
                        self,
                        displaced_name,
                        directory_fd=successor_location,
                    )
                    held.append(successor)
                    if not head_exists:
                        state = "successor_displaced"
                    else:
                        installed_head = _HeldStoreFile.capture(
                            self,
                            head_name,
                        )
                        held.append(installed_head)
                        if installed_head.raw != predecessor.raw:
                            raise RollbackCorruptionError(
                                "publication rollback head state conflicts"
                            )
                        state = "prior_installed"
                else:
                    raise RollbackCorruptionError(
                        "publication rollback target head is missing"
                    )
            else:
                if not head_exists:
                    raise RollbackCorruptionError(
                        "terminal rollback prior head is missing"
                    )
                installed_head = _HeldStoreFile.capture(
                    self,
                    head_name,
                )
                held.append(installed_head)
                if installed_head.raw != predecessor.raw:
                    raise RollbackCorruptionError(
                        "terminal rollback prior head is invalid"
                    )
                if displaced_exists == quarantine_exists:
                    raise RollbackCorruptionError(
                        "terminal rollback successor authority is ambiguous"
                    )
                successor_path = displaced_name if displaced_exists else quarantine_name
                successor_directory_fd = (
                    recovery_location(displaced_name) if displaced_exists else None
                )
                assert successor_directory_fd != -1
                successor = _HeldStoreFile.capture(
                    self,
                    successor_path,
                    directory_fd=successor_directory_fd,
                )
                held.append(successor)
                if intent_state == "quarantined":
                    if displaced_exists or terminal_identities is None:
                        raise RollbackCorruptionError(
                            "quarantined rollback physical state is invalid"
                        )
                    if successor.identity != terminal_identities[2]:
                        raise RollbackCorruptionError(
                            "quarantined successor identity changed"
                        )
                    state = "quarantined_pending_move"
                else:
                    state = (
                        "cleanup_pending_with_displaced"
                        if displaced_exists
                        else "cleanup_pending_with_quarantine"
                    )
            if successor is not None:
                if canonical_digest(successor.raw) != intent["successor_raw_sha256"]:
                    raise RollbackCorruptionError(
                        "publication rollback successor bytes mismatch"
                    )
                successor_record = _journal_from_object(
                    self._verify_signed(
                        successor.raw,
                        "journal-record",
                    )
                )
                if (
                    successor_record.rollback_id != rollback_id
                    or successor_record.generation != successor_generation
                    or successor_record.digest != intent["successor_record_digest"]
                    or successor_record.previous_record_digest != prior.digest
                ):
                    raise RollbackCorruptionError(
                        "publication rollback target is not exact successor"
                    )
            candidate = None
            if recovery_exists(candidate_name):
                if state != "successor_displaced":
                    raise RollbackCorruptionError(
                        "publication rollback candidate conflicts"
                    )
                candidate_location = recovery_location(candidate_name)
                assert candidate_location != -1
                candidate = _HeldStoreFile.capture(
                    self,
                    candidate_name,
                    directory_fd=candidate_location,
                )
                held.append(candidate)
                if candidate.raw != predecessor.raw:
                    raise RollbackCorruptionError(
                        "publication rollback candidate is invalid"
                    )
            return _RollbackRecoveryCapsule(
                transaction_id=transaction_id,
                intent=intent_file,
                predecessor=predecessor,
                predecessor_commit=predecessor_commit,
                successor=successor,
                installed_head=installed_head,
                head_name=head_name,
                displaced_name=displaced_name,
                candidate_name=candidate_name,
                quarantine_name=quarantine_name,
                tombstone_name=tombstone_name,
                successor_history_name=successor_history_name,
                successor_commit_name=successor_commit_name,
                state=state,
                candidate=candidate,
            )
        except BaseException as error:
            for item in reversed(held):
                item.close()
            raise RollbackCorruptionError(
                "abandoned transaction rollback intent is invalid"
            ) from error

    def _revalidate_recovery_capsule(
        self,
        capsule: _RollbackRecoveryCapsule,
        *,
        successor_path: str | None,
        candidate_path: str | None = None,
    ) -> None:
        capsule.intent.revalidate(self)
        capsule.predecessor.revalidate(self)
        capsule.predecessor_commit.revalidate(self)
        if capsule.successor is not None:
            assert successor_path is not None
            capsule.successor.revalidate(
                self,
                path_name=successor_path,
            )
        if capsule.candidate is not None:
            capsule.candidate.revalidate(
                self,
                path_name=(
                    capsule.candidate_name if candidate_path is None else candidate_path
                ),
            )
        if capsule.installed_head is not None:
            capsule.installed_head.revalidate(
                self,
                path_name=capsule.head_name,
            )
        self._verify_signed(
            capsule.intent.raw,
            "publication-rollback-intent",
        )
        predecessor = _journal_from_object(
            self._verify_signed(
                capsule.predecessor.raw,
                "journal-record",
            )
        )
        self._verify_commit(
            capsule.predecessor_commit.raw,
            identity=predecessor.rollback_id,
            generation=predecessor.generation,
            record_digest=predecessor.digest,
        )
        if (
            self._read(capsule.successor_history_name) is not None
            or self._read(capsule.successor_commit_name) is not None
        ):
            raise RollbackCorruptionError(
                "publication rollback successor authority appeared"
            )

    def _cleanup_pending_intent_bytes(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> bytes:
        payload = dict(
            _require_object(
                self._verify_signed(
                    capsule.intent.raw,
                    "publication-rollback-intent",
                ),
                frozenset(
                    (
                        "domain",
                        "prior_generation",
                        "prior_raw_sha256",
                        "prior_record_digest",
                        "relationship",
                        "rollback_id",
                        "schema_version",
                        "prior_commit_identity",
                        "prior_history_identity",
                        "quarantine_name",
                        "state",
                        "successor_generation",
                        "successor_raw_sha256",
                        "successor_record_digest",
                        "successor_quarantine_identity",
                        "transaction_id",
                    )
                ),
                "publication rollback intent",
            )
        )
        if payload["state"] != "active":
            raise RollbackCorruptionError(
                "rollback cleanup transition requires active intent"
            )
        payload["state"] = "cleanup_pending"
        return self._signed_bytes(
            "publication-rollback-intent",
            payload,
        )

    def _quarantined_intent_bytes(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> bytes:
        payload = dict(
            self._verify_signed(
                capsule.intent.raw,
                "publication-rollback-intent",
            )
        )
        if payload["state"] != "cleanup_pending":
            raise RollbackCorruptionError(
                "terminal quarantine requires cleanup-pending intent"
            )
        assert capsule.successor is not None
        payload.update(
            {
                "prior_commit_identity": list(capsule.predecessor_commit.identity),
                "prior_history_identity": list(capsule.predecessor.identity),
                "quarantine_name": capsule.quarantine_name,
                "state": "quarantined",
                "successor_quarantine_identity": list(capsule.successor.identity),
            }
        )
        return self._signed_bytes(
            "publication-rollback-intent",
            payload,
        )

    def _publish_terminal_restoration(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> RollbackJournalRecord:
        assert capsule.successor is not None
        predecessor = _journal_from_object(
            self._verify_signed(capsule.predecessor.raw, "journal-record")
        )
        successor = _journal_from_object(
            self._verify_signed(capsule.successor.raw, "journal-record")
        )
        ref = RollbackTerminalQuarantineRef(
            capsule.transaction_id,
            predecessor.rollback_id,
            predecessor.generation,
            predecessor.digest,
            successor.generation,
            successor.digest,
            canonical_digest(capsule.successor.raw),
            capsule.quarantine_name,
            capsule.tombstone_name,
            canonical_digest(capsule.intent.raw),
        )
        restoration = RollbackJournalRecord(
            predecessor.rollback_id,
            predecessor.request_digest,
            predecessor.request_payload_ref,
            successor.generation + 1,
            predecessor.revision,
            predecessor.phase,
            predecessor.phase_receipts,
            successor.digest,
            (*predecessor.terminal_quarantine_refs, ref),
        )
        signed_restoration = self._signed_bytes(
            "journal-record",
            restoration.canonical_object(),
        )
        current_head = self._read(capsule.head_name)
        anchor_key = (
            f"{canonical_digest(ref.rollback_id.encode())[7:]}."
            f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
        )
        indexed = anchor_key in self._terminal_quarantine_anchors()
        pending = self._terminal_anchor_pending(ref)
        if not indexed and pending is None:
            if current_head == signed_restoration:
                raise RollbackCorruptionError(
                    "terminal restoration has no pending-forward authority"
                )
            self._cleanup_recovery_fault("pending_anchor.before_publish")
            self._ensure_terminal_anchor_pending(ref)
            self._cleanup_recovery_fault("pending_anchor.after_publish")
        if current_head == signed_restoration:
            if self._read(self._history_name(restoration)) != signed_restoration:
                raise RollbackCorruptionError(
                    "terminal restoration history is incomplete"
                )
            expected_commit = self._commit_bytes(
                restoration.rollback_id,
                restoration.generation,
                restoration.digest,
            )
            current_commit = self._read(self._commit_name(restoration))
            anchors = self._terminal_quarantine_anchors()
            if current_commit is None:
                if anchor_key in anchors:
                    raise RollbackCorruptionError(
                        "terminal restoration committed authority disappeared"
                    )
                self._cleanup_recovery_fault("restoration_commit.before_publish")
                self._create_immutable(
                    self._commit_name(restoration),
                    expected_commit,
                )
                os.fsync(self._root_fd)
                self._cleanup_recovery_fault("restoration_commit.after_durable")
            elif current_commit != expected_commit:
                raise RollbackCorruptionError("terminal restoration commit is invalid")
            self._cleanup_recovery_fault("terminal_anchor.before_publish")
            self._publish_terminal_anchor(ref)
            self._cleanup_recovery_fault("terminal_anchor.after_publish")
            return restoration
        if current_head not in (
            capsule.predecessor.raw,
            capsule.successor.raw,
        ):
            raise RollbackCorruptionError(
                "terminal restoration canonical head conflicts"
            )
        publication_transaction = self._publication_tx
        self._publication_tx = None
        try:
            self._cleanup_recovery_fault("successor_history.before_publish")
            self._create_immutable(
                capsule.successor_history_name,
                capsule.successor.raw,
            )
            self._cleanup_recovery_fault("successor_history.after_publish")
            self._cleanup_recovery_fault("successor_commit.before_publish")
            self._create_immutable(
                capsule.successor_commit_name,
                self._commit_bytes(
                    successor.rollback_id,
                    successor.generation,
                    successor.digest,
                ),
            )
            self._cleanup_recovery_fault("successor_commit.after_publish")
            self._cleanup_recovery_fault("restoration_head.before_publish")
            self._cleanup_recovery_replace_boundary = "restoration_head"
            try:
                self._publish_versioned(
                    head_name=capsule.head_name,
                    history_name=self._history_name(restoration),
                    commit_name=self._commit_name(restoration),
                    identity=restoration.rollback_id,
                    generation=restoration.generation,
                    record_digest=restoration.digest,
                    signed_record=signed_restoration,
                    old_head=current_head,
                )
            finally:
                self._cleanup_recovery_replace_boundary = None
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
            self._cleanup_recovery_fault("restoration_head.after_publish")
            self._cleanup_recovery_fault("terminal_anchor.before_publish")
            self._publish_terminal_anchor(ref)
            self._cleanup_recovery_fault("terminal_anchor.after_publish")
        finally:
            self._publication_tx = publication_transaction
        return restoration

    def _mark_rollback_cleanup_pending(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> None:
        cleanup_raw = self._cleanup_pending_intent_bytes(capsule)
        intent_directory_fd = capsule.intent.path_directory_fd
        self._replace_at(
            intent_directory_fd,
            capsule.intent.name,
            cleanup_raw,
            capsule.intent.raw,
            capsule.intent,
        )
        capsule.state = "cleanup_pending_with_displaced"
        replacement = _HeldStoreFile.capture(
            self,
            capsule.intent.name,
            directory_fd=intent_directory_fd,
        )
        prior_intent = capsule.intent
        capsule.intent = replacement
        prior_intent.close()

    def _finish_rollback_cleanup(
        self,
        capsule: _RollbackRecoveryCapsule,
        *,
        successor_path: str,
        candidate_path: str | None = None,
    ) -> None:
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=successor_path,
            candidate_path=candidate_path,
        )
        if self._read(capsule.head_name) != capsule.predecessor.raw:
            raise RollbackCorruptionError(
                "cleanup-pending rollback predecessor is invalid"
            )
        assert capsule.successor is not None
        if successor_path == capsule.displaced_name:
            self._assert_rollback_quarantine_capacity(
                capsule.quarantine_name,
                len(capsule.successor.raw),
            )
            successor_directory_fd = capsule.successor.path_directory_fd
            self._cleanup_recovery_fault("successor_quarantine.before_move")
            _rename_noreplace_between(
                capsule.displaced_name,
                capsule.quarantine_name,
                successor_directory_fd,
                self._terminal_fd,
            )
            os.fsync(successor_directory_fd)
            os.fsync(self._terminal_fd)
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault("successor_quarantine.after_durable")
            capsule.successor.path_directory_fd = self._terminal_fd
            capsule.successor.refresh_path_identity(
                self,
                capsule.quarantine_name,
            )
            capsule.state = "cleanup_pending_with_quarantine"
        elif successor_path != capsule.quarantine_name:
            raise RollbackCorruptionError("cleanup-pending successor path is invalid")
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=capsule.quarantine_name,
            candidate_path=candidate_path,
        )
        terminal_raw = self._quarantined_intent_bytes(capsule)
        intent_directory_fd = capsule.intent.path_directory_fd
        self._replace_at(
            intent_directory_fd,
            capsule.intent.name,
            terminal_raw,
            capsule.intent.raw,
            capsule.intent,
        )
        capsule.state = "quarantined_pending_move"
        replacement = _HeldStoreFile.capture(
            self,
            capsule.intent.name,
            directory_fd=intent_directory_fd,
        )
        prior_intent = capsule.intent
        capsule.intent = replacement
        prior_intent.close()
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=capsule.quarantine_name,
            candidate_path=candidate_path,
        )
        self._cleanup_recovery_fault("terminal_tombstone.before_move")
        _rename_noreplace_between(
            capsule.intent.name,
            capsule.tombstone_name,
            capsule.intent.path_directory_fd,
            self._terminal_fd,
        )
        os.fsync(self._terminal_fd)
        os.fsync(self._root_fd)
        self._cleanup_recovery_fault("terminal_tombstone.after_durable")
        capsule.intent.path_directory_fd = self._terminal_fd
        capsule.intent.refresh_path_identity(
            self,
            capsule.tombstone_name,
        )
        capsule.intent.name = capsule.tombstone_name
        capsule.state = "terminal_complete"
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=capsule.quarantine_name,
            candidate_path=candidate_path,
        )
        self._cleanup_recovery_fault("restoration.before_publish")
        self._publish_terminal_restoration(capsule)
        self._cleanup_recovery_fault("restoration.after_publish")

    def _recover_transaction_rollback(
        self,
        capsule: _RollbackRecoveryCapsule,
    ) -> None:
        if capsule.state == "successor_at_head":
            successor_path = capsule.head_name
        elif capsule.state in (
            "cleanup_pending_with_quarantine",
            "quarantined_pending_move",
            "terminal_complete",
        ):
            successor_path = capsule.quarantine_name
        else:
            successor_path = capsule.displaced_name
        self._revalidate_recovery_capsule(
            capsule,
            successor_path=successor_path,
        )
        if capsule.state in (
            "cleanup_pending_with_displaced",
            "cleanup_pending_with_quarantine",
        ):
            self._finish_rollback_cleanup(
                capsule,
                successor_path=successor_path,
            )
            return
        if capsule.state == "quarantined_pending_move":
            self._cleanup_recovery_fault("terminal_tombstone.before_move")
            _rename_noreplace_between(
                capsule.intent.name,
                capsule.tombstone_name,
                capsule.intent.path_directory_fd,
                self._terminal_fd,
            )
            os.fsync(self._terminal_fd)
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault("terminal_tombstone.after_durable")
            capsule.intent.path_directory_fd = self._terminal_fd
            capsule.intent.refresh_path_identity(
                self,
                capsule.tombstone_name,
            )
            capsule.intent.name = capsule.tombstone_name
            capsule.state = "terminal_complete"
            self._revalidate_recovery_capsule(
                capsule,
                successor_path=capsule.quarantine_name,
            )
            self._cleanup_recovery_fault("restoration.before_publish")
            self._publish_terminal_restoration(capsule)
            self._cleanup_recovery_fault("restoration.after_publish")
            return
        if capsule.state == "prior_installed":
            self._mark_rollback_cleanup_pending(capsule)
            self._finish_rollback_cleanup(
                capsule,
                successor_path=capsule.displaced_name,
            )
            return
        displaced_by_operation = False
        installed_prior = False
        candidate_created = False
        candidate_source_fd = self._root_fd
        try:
            if capsule.state == "successor_at_head":
                self._cleanup_recovery_fault("successor_displacement.before_move")
                _rename_noreplace(
                    capsule.head_name,
                    capsule.displaced_name,
                    self._root_fd,
                )
                os.fsync(self._root_fd)
                self._cleanup_recovery_fault("successor_displacement.after_durable")
                displaced_by_operation = True
                successor_path = capsule.displaced_name
                capsule.successor.refresh_path_identity(
                    self,
                    capsule.displaced_name,
                )
            self._revalidate_recovery_capsule(
                capsule,
                successor_path=successor_path,
            )
            if capsule.candidate is None:
                self._cleanup_recovery_fault("prior_candidate.before_publish")
                self._create_immutable(
                    capsule.candidate_name,
                    capsule.predecessor.raw,
                )
                self._cleanup_recovery_fault("prior_candidate.after_publish")
                capsule.candidate = _HeldStoreFile.capture(
                    self,
                    capsule.candidate_name,
                )
                candidate_created = True
            assert capsule.candidate is not None
            candidate_source_fd = capsule.candidate.path_directory_fd
            self._cleanup_recovery_fault("prior_head.before_publish")
            if candidate_source_fd == self._root_fd:
                _rename_noreplace(
                    capsule.candidate_name,
                    capsule.head_name,
                    self._root_fd,
                )
            else:
                _rename_noreplace_between(
                    capsule.candidate_name,
                    capsule.head_name,
                    candidate_source_fd,
                    self._root_fd,
                )
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault("prior_head.after_durable")
            assert capsule.candidate is not None
            capsule.candidate.path_directory_fd = self._root_fd
            capsule.candidate.refresh_path_identity(
                self,
                capsule.head_name,
            )
            installed_prior = True
            self._revalidate_recovery_capsule(
                capsule,
                successor_path=capsule.displaced_name,
                candidate_path=capsule.head_name,
            )
            if self._read(capsule.head_name) != capsule.predecessor.raw:
                raise RollbackCorruptionError(
                    "installed rollback predecessor is invalid"
                )
            self._mark_rollback_cleanup_pending(capsule)
            self._finish_rollback_cleanup(
                capsule,
                successor_path=capsule.displaced_name,
                candidate_path=capsule.head_name,
            )
        except _CleanupInjectedCrash:
            raise
        except BaseException:
            if capsule.state.startswith(("cleanup_pending", "quarantined", "terminal")):
                raise
            if installed_prior:
                assert capsule.candidate is not None
                _rename_noreplace_between(
                    capsule.head_name,
                    capsule.candidate_name,
                    self._root_fd,
                    candidate_source_fd,
                )
                assert capsule.successor is not None
                _rename_noreplace_between(
                    capsule.displaced_name,
                    capsule.head_name,
                    capsule.successor.path_directory_fd,
                    self._root_fd,
                )
                capsule.candidate.path_directory_fd = candidate_source_fd
                os.fsync(self._root_fd)
                if candidate_created:
                    os.unlink(
                        capsule.candidate_name,
                        dir_fd=self._root_fd,
                    )
                    os.fsync(self._root_fd)
            elif displaced_by_operation:
                _rename_noreplace(
                    capsule.displaced_name,
                    capsule.head_name,
                    self._root_fd,
                )
                os.fsync(self._root_fd)
                if candidate_created:
                    try:
                        os.unlink(
                            capsule.candidate_name,
                            dir_fd=self._root_fd,
                        )
                    except FileNotFoundError:
                        pass
                    os.fsync(self._root_fd)
            raise

    def _rollback_quarantine_inventory(
        self,
    ) -> dict[str, dict[str, tuple[str, int]]]:
        pattern = re.compile(
            r"^rollback-quarantine\.([0-9a-f]{64})\."
            r"([0-9a-f]{32})\.([0-9a-f]{64})\."
            r"(successor|tombstone)$"
        )
        inventory: dict[str, dict[str, tuple[str, int]]] = {}
        aggregate_bytes = 0
        artifact_count = 0
        with os.scandir(self._terminal_fd) as entries:
            for entry in entries:
                artifact_count += 1
                if artifact_count > _MAX_ROLLBACK_QUARANTINE_ARTIFACTS:
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact bound is exhausted"
                    )
                name = entry.name
                match = pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact name is invalid"
                    )
                value = entry.stat(follow_symlinks=False)
                suffix = match.group(4)
                size_limit = (
                    _MAX_RECORD_BYTES
                    if suffix == "successor"
                    else _MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES
                )
                if (
                    not stat.S_ISREG(value.st_mode)
                    or stat.S_IMODE(value.st_mode) != 0o600
                    or value.st_nlink != 1
                    or (value.st_uid, value.st_gid) != self._owner
                    or value.st_size <= 0
                    or value.st_size > size_limit
                ):
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact is invalid"
                    )
                base = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
                artifacts = inventory.setdefault(base, {})
                if len(inventory) > _MAX_ROLLBACK_QUARANTINE_PAIRS:
                    raise RollbackCorruptionError(
                        "rollback quarantine pair bound is exhausted"
                    )
                if suffix in artifacts:
                    raise RollbackCorruptionError(
                        "rollback quarantine artifact is duplicated"
                    )
                artifacts[suffix] = (name, value.st_size)
                aggregate_bytes += value.st_size
                if aggregate_bytes > _MAX_ROLLBACK_QUARANTINE_BYTES:
                    raise RollbackCorruptionError(
                        "rollback quarantine byte bound is exhausted"
                    )
        return inventory

    def _assert_rollback_quarantine_capacity(
        self,
        quarantine_name: str,
        successor_size: int,
    ) -> None:
        inventory = self._rollback_quarantine_inventory()
        match = re.fullmatch(
            r"rollback-quarantine\.([0-9a-f]{64})\."
            r"([0-9a-f]{32})\.([0-9a-f]{64})\.successor",
            quarantine_name,
        )
        if match is None:
            raise RollbackCorruptionError(
                "rollback quarantine capacity binding is invalid"
            )
        current_base = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
        aggregate_bytes = sum(
            size for artifacts in inventory.values() for _, size in artifacts.values()
        )
        for base, artifacts in inventory.items():
            expected = (
                {"successor"} if base == current_base else {"successor", "tombstone"}
            )
            if set(artifacts) != expected:
                raise RollbackCorruptionError("rollback quarantine pair is incomplete")
        projected_pairs = len(inventory)
        projected_bytes = aggregate_bytes
        if current_base not in inventory:
            projected_pairs += 1
            projected_bytes += successor_size
        projected_bytes += _MAX_ROLLBACK_QUARANTINE_TOMBSTONE_BYTES
        if (
            projected_pairs > _MAX_ROLLBACK_QUARANTINE_PAIRS
            or projected_bytes > _MAX_ROLLBACK_QUARANTINE_BYTES
        ):
            raise RollbackCorruptionError(
                "rollback quarantine retention bound is exhausted"
            )

    def _assert_generation_not_quarantined(
        self,
        identity: str,
        generation: int,
        record_digest: str,
    ) -> None:
        inventory = self._rollback_quarantine_inventory()
        anchors = self._terminal_quarantine_anchors()
        for base, artifacts in inventory.items():
            anchor = anchors.get(base)
            if anchor is None:
                anchor = self._terminal_anchor_pending_for_base(base)
                if anchor is None:
                    raise RollbackCorruptionError(
                        "rollback quarantine pair has no signed anchor"
                    )
                continue
            if self._rollback_id_blocked(anchor.rollback_id):
                continue
            tombstone = artifacts.get("tombstone")
            if tombstone is None:
                raise RollbackCorruptionError("rollback quarantine pair is incomplete")
            raw = self._read(tombstone[0])
            if raw is None:
                raise RollbackCorruptionError(
                    "rollback quarantine tombstone disappeared"
                )
            payload = self._verify_signed(
                raw,
                "publication-rollback-intent",
            )
            if (
                payload.get("rollback_id") == identity
                and payload.get("successor_generation") == generation
                and payload.get("successor_record_digest") == record_digest
            ):
                raise RollbackConflictError(
                    "journal generation is terminally quarantined"
                )

    def _terminal_quarantine_anchors(
        self,
    ) -> dict[str, RollbackTerminalQuarantineRef]:
        raw = self._read(_ROLLBACK_TERMINAL_ANCHOR_INDEX)
        if raw is None:
            return {}
        try:
            payload = _require_object(
                self._verify_signed(raw, "terminal-quarantine-anchor-index"),
                frozenset(("entries", "schema_version")),
                "terminal quarantine anchor index",
            )
            if (
                payload["schema_version"]
                != "bb.rl.phase5.rollback-terminal-anchor-index.v1"
            ):
                raise RollbackCorruptionError(
                    "terminal quarantine anchor index schema is invalid"
                )
            entries = _require_tuple(
                payload["entries"],
                "terminal quarantine anchor index entries",
            )
            if len(entries) > _MAX_ROLLBACK_QUARANTINE_PAIRS:
                raise RollbackCorruptionError(
                    "terminal quarantine anchor index bound is exhausted"
                )
            refs = tuple(
                _terminal_quarantine_ref_from_object(entry) for entry in entries
            )
        except (RollbackValidationError, RollbackCorruptionError) as error:
            raise RollbackCorruptionError(
                "terminal quarantine anchor index is invalid"
            ) from error
        anchor_keys = tuple(
            f"{canonical_digest(ref.rollback_id.encode())[7:]}."
            f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
            for ref in refs
        )
        if anchor_keys != tuple(sorted(anchor_keys)) or len(set(anchor_keys)) != len(
            anchor_keys
        ):
            raise RollbackCorruptionError(
                "terminal quarantine anchor index order is invalid"
            )
        return dict(zip(anchor_keys, refs, strict=True))

    def _terminal_anchor_index_bytes(
        self,
        anchors: Mapping[str, RollbackTerminalQuarantineRef],
    ) -> bytes:
        return self._signed_bytes(
            "terminal-quarantine-anchor-index",
            {
                "entries": [
                    anchors[anchor_key].canonical_object()
                    for anchor_key in sorted(anchors)
                ],
                "schema_version": ("bb.rl.phase5.rollback-terminal-anchor-index.v1"),
            },
        )

    @staticmethod
    def _terminal_anchor_key(
        ref: RollbackTerminalQuarantineRef,
    ) -> str:
        return (
            f"{canonical_digest(ref.rollback_id.encode())[7:]}."
            f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
        )

    def _terminal_anchor_pending_name(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> str:
        return f".terminal-anchor-pending.{self._terminal_anchor_key(ref)}"

    def _terminal_anchor_pending_for_base(
        self,
        base: str,
    ) -> RollbackTerminalQuarantineRef | None:
        raw = self._read(f".terminal-anchor-pending.{base}")
        if raw is None:
            return None
        try:
            payload = _require_object(
                self._verify_signed(
                    raw,
                    "terminal-quarantine-anchor-pending",
                ),
                frozenset(("ref", "schema_version")),
                "terminal quarantine pending anchor",
            )
            if (
                payload["schema_version"]
                != "bb.rl.phase5.rollback-terminal-anchor-pending.v1"
            ):
                raise RollbackCorruptionError(
                    "terminal quarantine pending anchor schema is invalid"
                )
            ref = _terminal_quarantine_ref_from_object(payload["ref"])
        except (RollbackValidationError, RollbackCorruptionError) as error:
            raise RollbackCorruptionError(
                "terminal quarantine pending anchor is invalid"
            ) from error
        if self._terminal_anchor_key(ref) != base:
            raise RollbackCorruptionError(
                "terminal quarantine pending anchor binding is invalid"
            )
        return ref

    def _terminal_anchor_pending(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> bytes | None:
        raw = self._read(self._terminal_anchor_pending_name(ref))
        if raw is None:
            return None
        expected = self._signed_bytes(
            "terminal-quarantine-anchor-pending",
            {
                "ref": ref.canonical_object(),
                "schema_version": ("bb.rl.phase5.rollback-terminal-anchor-pending.v1"),
            },
        )
        if raw != expected:
            raise RollbackCorruptionError(
                "terminal quarantine pending anchor is invalid"
            )
        return raw

    def _ensure_terminal_anchor_pending(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> None:
        if self._terminal_anchor_pending(ref) is not None:
            return
        self._create_immutable(
            self._terminal_anchor_pending_name(ref),
            self._signed_bytes(
                "terminal-quarantine-anchor-pending",
                {
                    "ref": ref.canonical_object(),
                    "schema_version": (
                        "bb.rl.phase5.rollback-terminal-anchor-pending.v1"
                    ),
                },
            ),
        )
        os.fsync(self._root_fd)

    def _clear_terminal_anchor_pending(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> None:
        name = self._terminal_anchor_pending_name(ref)
        if self._read(name) is None:
            return
        os.unlink(name, dir_fd=self._root_fd)
        os.fsync(self._root_fd)

    def _publish_terminal_anchor(
        self,
        ref: RollbackTerminalQuarantineRef,
    ) -> None:
        old_raw = self._read(_ROLLBACK_TERMINAL_ANCHOR_INDEX)
        anchors = self._terminal_quarantine_anchors()
        anchor_key = self._terminal_anchor_key(ref)
        existing = anchors.get(anchor_key)
        if existing is not None:
            if existing != ref:
                raise RollbackCorruptionError("terminal quarantine anchor conflicts")
            self._clear_terminal_anchor_pending(ref)
            return
        if len(anchors) >= _MAX_ROLLBACK_QUARANTINE_PAIRS:
            raise RollbackCorruptionError(
                "terminal quarantine anchor index bound is exhausted"
            )
        anchors[anchor_key] = ref
        previous_boundary = self._cleanup_recovery_replace_boundary
        if (
            self._cleanup_forward_active
            or self._cleanup_recovery_checkpoint is not None
        ):
            self._cleanup_recovery_replace_boundary = "terminal_anchor"
        try:
            self._replace(
                _ROLLBACK_TERMINAL_ANCHOR_INDEX,
                self._terminal_anchor_index_bytes(anchors),
                old_raw,
            )
        finally:
            self._cleanup_recovery_replace_boundary = previous_boundary
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
        os.fsync(self._root_fd)
        self._clear_terminal_anchor_pending(ref)

    def _recover_pending_terminal_restorations(self) -> None:
        inventory = self._rollback_quarantine_inventory()
        anchors = self._terminal_quarantine_anchors()
        anchored_by_base = {
            self._terminal_anchor_key(ref): ref for ref in anchors.values()
        }
        for base, artifacts in inventory.items():
            if base in anchored_by_base:
                self._clear_terminal_anchor_pending(anchored_by_base[base])
                continue
            if set(artifacts) != {"successor", "tombstone"}:
                raise RollbackCorruptionError("rollback quarantine pair is incomplete")
            identity_digest, transaction_id, digest_hex = base.split(".", 2)
            successor_name = artifacts["successor"][0]
            tombstone_name = artifacts["tombstone"][0]
            held: list[_HeldStoreFile] = []
            try:
                successor = _HeldStoreFile.capture(self, successor_name)
                held.append(successor)
                tombstone = _HeldStoreFile.capture(self, tombstone_name)
                held.append(tombstone)
                payload = _require_object(
                    self._verify_signed(
                        tombstone.raw,
                        "publication-rollback-intent",
                    ),
                    frozenset(
                        (
                            "domain",
                            "prior_commit_identity",
                            "prior_generation",
                            "prior_history_identity",
                            "prior_raw_sha256",
                            "prior_record_digest",
                            "quarantine_name",
                            "relationship",
                            "rollback_id",
                            "schema_version",
                            "state",
                            "successor_generation",
                            "successor_quarantine_identity",
                            "successor_raw_sha256",
                            "successor_record_digest",
                            "transaction_id",
                        )
                    ),
                    "pending terminal rollback restoration",
                )
                if (
                    payload["schema_version"]
                    != "bb.rl.phase5.publication-rollback-intent.v1"
                    or payload["domain"] != self._domain
                    or payload["transaction_id"] != transaction_id
                    or payload["relationship"] != "exact-successor"
                    or payload["state"] != "quarantined"
                    or payload["quarantine_name"] != successor_name
                    or payload["successor_record_digest"] != f"sha256:{digest_hex}"
                    or identity_digest
                    != canonical_digest(str(payload["rollback_id"]).encode())[7:]
                ):
                    raise RollbackCorruptionError(
                        "pending terminal restoration binding is invalid"
                    )
                rollback_id = _require_id(
                    payload["rollback_id"],
                    "rollback id",
                )
                prior_generation = _require_int(
                    payload["prior_generation"],
                    "prior generation",
                    minimum=1,
                )
                successor_generation = _require_int(
                    payload["successor_generation"],
                    "successor generation",
                    minimum=2,
                )
                if successor_generation != prior_generation + 1:
                    raise RollbackCorruptionError(
                        "pending terminal restoration generations diverged"
                    )
                predecessor_name = self._journal_version_name(
                    rollback_id,
                    prior_generation,
                    payload["prior_record_digest"],
                    "history",
                )
                predecessor_commit_name = self._journal_version_name(
                    rollback_id,
                    prior_generation,
                    payload["prior_record_digest"],
                    "commit",
                )
                predecessor = _HeldStoreFile.capture(
                    self,
                    predecessor_name,
                )
                held.append(predecessor)
                predecessor_commit = _HeldStoreFile.capture(
                    self,
                    predecessor_commit_name,
                )
                held.append(predecessor_commit)
                predecessor_record = _journal_from_object(
                    self._verify_signed(
                        predecessor.raw,
                        "journal-record",
                    )
                )
                successor_record = _journal_from_object(
                    self._verify_signed(
                        successor.raw,
                        "journal-record",
                    )
                )
                if (
                    predecessor_record.rollback_id != rollback_id
                    or predecessor_record.generation != prior_generation
                    or predecessor_record.digest != payload["prior_record_digest"]
                    or successor_record.rollback_id != rollback_id
                    or successor_record.generation != successor_generation
                    or successor_record.digest != payload["successor_record_digest"]
                    or successor_record.previous_record_digest
                    != predecessor_record.digest
                    or canonical_digest(predecessor.raw) != payload["prior_raw_sha256"]
                    or canonical_digest(successor.raw)
                    != payload["successor_raw_sha256"]
                    or predecessor.identity
                    != self._decode_recovery_identity(
                        payload["prior_history_identity"],
                        "prior history",
                    )
                    or predecessor_commit.identity
                    != self._decode_recovery_identity(
                        payload["prior_commit_identity"],
                        "prior commit",
                    )
                    or successor.identity
                    != self._decode_recovery_identity(
                        payload["successor_quarantine_identity"],
                        "successor quarantine",
                    )
                ):
                    raise RollbackCorruptionError(
                        "pending terminal restoration authority diverged"
                    )
                self._verify_commit(
                    predecessor_commit.raw,
                    identity=rollback_id,
                    generation=prior_generation,
                    record_digest=predecessor_record.digest,
                )
                capsule = _RollbackRecoveryCapsule(
                    transaction_id=transaction_id,
                    intent=tombstone,
                    predecessor=predecessor,
                    predecessor_commit=predecessor_commit,
                    successor=successor,
                    head_name=self._head_name(rollback_id),
                    displaced_name=(f".{self._domain}.{transaction_id}.displaced-head"),
                    candidate_name=(
                        f".{self._domain}.{transaction_id}.prior-candidate"
                    ),
                    quarantine_name=successor_name,
                    tombstone_name=tombstone_name,
                    successor_history_name=self._journal_version_name(
                        rollback_id,
                        successor_generation,
                        successor_record.digest,
                        "history",
                    ),
                    successor_commit_name=self._journal_version_name(
                        rollback_id,
                        successor_generation,
                        successor_record.digest,
                        "commit",
                    ),
                    state="terminal_complete",
                )
                try:
                    checkpoint_factory = self._cleanup_pending_checkpoint_factory
                    if checkpoint_factory is not None:
                        self._cleanup_recovery_checkpoint = checkpoint_factory(capsule)
                    self._publish_terminal_restoration(capsule)
                except BaseException:
                    predecessor_for_ref = _journal_from_object(
                        self._verify_signed(
                            capsule.predecessor.raw,
                            "journal-record",
                        )
                    )
                    successor_for_ref = _journal_from_object(
                        self._verify_signed(
                            capsule.successor.raw,
                            "journal-record",
                        )
                    )
                    self._publish_terminal_anchor(
                        RollbackTerminalQuarantineRef(
                            capsule.transaction_id,
                            rollback_id,
                            predecessor_for_ref.generation,
                            predecessor_for_ref.digest,
                            successor_for_ref.generation,
                            successor_for_ref.digest,
                            canonical_digest(capsule.successor.raw),
                            successor_name,
                            tombstone_name,
                            canonical_digest(capsule.intent.raw),
                        )
                    )
                    self._block_rollback_id(rollback_id)
                    raise
                held.clear()
                capsule.close()
            finally:
                for item in reversed(held):
                    item.close()

        if self._read(_ROLLBACK_TERMINAL_ANCHOR_INDEX) is None:
            self._replace(
                _ROLLBACK_TERMINAL_ANCHOR_INDEX,
                self._terminal_anchor_index_bytes({}),
                None,
            )
            os.fsync(self._root_fd)

    def _terminal_pair_evidence(
        self,
        anchor: RollbackTerminalQuarantineRef,
        artifacts: Mapping[str, tuple[str, int]],
    ) -> tuple[Mapping[str, Any], RollbackJournalRecord, bytes]:
        if set(artifacts) != {"successor", "tombstone"}:
            raise RollbackCorruptionError("rollback quarantine pair is incomplete")
        successor_name = artifacts["successor"][0]
        tombstone_name = artifacts["tombstone"][0]
        if (
            successor_name != anchor.successor_name
            or tombstone_name != anchor.tombstone_name
        ):
            raise RollbackCorruptionError(
                "terminal rollback pair name binding is invalid"
            )
        successor = _HeldStoreFile.capture(self, successor_name)
        try:
            tombstone = _HeldStoreFile.capture(self, tombstone_name)
            try:
                payload = _require_object(
                    self._verify_signed(
                        tombstone.raw,
                        "publication-rollback-intent",
                    ),
                    frozenset(
                        (
                            "domain",
                            "prior_commit_identity",
                            "prior_generation",
                            "prior_history_identity",
                            "prior_raw_sha256",
                            "prior_record_digest",
                            "quarantine_name",
                            "relationship",
                            "rollback_id",
                            "schema_version",
                            "state",
                            "successor_generation",
                            "successor_quarantine_identity",
                            "successor_raw_sha256",
                            "successor_record_digest",
                            "transaction_id",
                        )
                    ),
                    "terminal rollback quarantine",
                )
                if (
                    payload["schema_version"]
                    != "bb.rl.phase5.publication-rollback-intent.v1"
                    or payload["domain"] != self._domain
                    or payload["transaction_id"] != anchor.transaction_id
                    or payload["rollback_id"] != anchor.rollback_id
                    or payload["relationship"] != "exact-successor"
                    or payload["state"] != "quarantined"
                    or payload["quarantine_name"] != successor_name
                    or payload["prior_generation"] != anchor.predecessor_generation
                    or payload["prior_record_digest"]
                    != anchor.predecessor_record_digest
                    or payload["successor_generation"] != anchor.successor_generation
                    or payload["successor_record_digest"]
                    != anchor.successor_record_digest
                    or payload["successor_raw_sha256"] != anchor.successor_raw_digest
                    or canonical_digest(tombstone.raw) != anchor.tombstone_raw_digest
                    or successor.identity
                    != self._decode_recovery_identity(
                        payload["successor_quarantine_identity"],
                        "successor quarantine",
                    )
                    or canonical_digest(successor.raw) != anchor.successor_raw_digest
                ):
                    raise RollbackCorruptionError(
                        "terminal rollback quarantine binding is invalid"
                    )
                successor_record = _journal_from_object(
                    self._verify_signed(
                        successor.raw,
                        "journal-record",
                    )
                )
                if (
                    successor_record.rollback_id != anchor.rollback_id
                    or successor_record.generation != anchor.successor_generation
                    or successor_record.digest != anchor.successor_record_digest
                    or successor_record.previous_record_digest
                    != anchor.predecessor_record_digest
                ):
                    raise RollbackCorruptionError(
                        "terminal rollback successor model is invalid"
                    )
                successor.revalidate(self)
                tombstone.revalidate(self)
                return payload, successor_record, successor.raw
            finally:
                tombstone.close()
        finally:
            successor.close()

    def _validate_live_terminal_anchor(
        self,
        anchor: RollbackTerminalQuarantineRef,
        payload: Mapping[str, Any],
        successor_record: RollbackJournalRecord,
        successor_raw: bytes,
        *,
        block_on_failure: bool = True,
    ) -> None:
        rollback_id = anchor.rollback_id
        predecessor_name = self._journal_version_name(
            rollback_id,
            anchor.predecessor_generation,
            anchor.predecessor_record_digest,
            "history",
        )
        predecessor_commit_name = self._journal_version_name(
            rollback_id,
            anchor.predecessor_generation,
            anchor.predecessor_record_digest,
            "commit",
        )
        successor_history_name = self._journal_version_name(
            rollback_id,
            anchor.successor_generation,
            anchor.successor_record_digest,
            "history",
        )
        successor_commit_name = self._journal_version_name(
            rollback_id,
            anchor.successor_generation,
            anchor.successor_record_digest,
            "commit",
        )
        marker = self._marker_name(rollback_id)
        if self._read(successor_history_name) is None:
            self._quarantine(
                successor_commit_name,
                marker,
                rollback_id,
            )
            raise RollbackCorruptionError(
                "terminal rollback successor history disappeared"
            )
        if self._read(successor_commit_name) is None:
            self._quarantine(
                self._head_name(rollback_id),
                marker,
                rollback_id,
            )
            raise RollbackCorruptionError(
                "terminal rollback successor commit disappeared"
            )
        held: list[_HeldStoreFile] = []
        try:
            predecessor = _HeldStoreFile.capture(self, predecessor_name)
            held.append(predecessor)
            predecessor_commit = _HeldStoreFile.capture(
                self,
                predecessor_commit_name,
            )
            held.append(predecessor_commit)
            successor_history = _HeldStoreFile.capture(
                self,
                successor_history_name,
            )
            held.append(successor_history)
            successor_commit = _HeldStoreFile.capture(
                self,
                successor_commit_name,
            )
            held.append(successor_commit)
            predecessor_record = _journal_from_object(
                self._verify_signed(predecessor.raw, "journal-record")
            )
            if (
                predecessor_record.rollback_id != rollback_id
                or predecessor_record.generation != anchor.predecessor_generation
                or predecessor_record.digest != anchor.predecessor_record_digest
                or canonical_digest(predecessor.raw) != payload["prior_raw_sha256"]
                or predecessor.identity
                != self._decode_recovery_identity(
                    payload["prior_history_identity"],
                    "prior history",
                )
                or predecessor_commit.identity
                != self._decode_recovery_identity(
                    payload["prior_commit_identity"],
                    "prior commit",
                )
                or successor_history.raw != successor_raw
            ):
                raise RollbackCorruptionError(
                    "terminal rollback root authority diverged"
                )
            self._verify_commit(
                predecessor_commit.raw,
                identity=rollback_id,
                generation=predecessor_record.generation,
                record_digest=predecessor_record.digest,
            )
            self._verify_commit(
                successor_commit.raw,
                identity=rollback_id,
                generation=successor_record.generation,
                record_digest=successor_record.digest,
            )
            restoration = RollbackJournalRecord(
                rollback_id,
                predecessor_record.request_digest,
                predecessor_record.request_payload_ref,
                successor_record.generation + 1,
                predecessor_record.revision,
                predecessor_record.phase,
                predecessor_record.phase_receipts,
                successor_record.digest,
                (*predecessor_record.terminal_quarantine_refs, anchor),
            )
            restoration_raw = self._signed_bytes(
                "journal-record",
                restoration.canonical_object(),
            )
            restoration_history = _HeldStoreFile.capture(
                self,
                self._history_name(restoration),
            )
            held.append(restoration_history)
            restoration_commit = _HeldStoreFile.capture(
                self,
                self._commit_name(restoration),
            )
            held.append(restoration_commit)
            if restoration_history.raw != restoration_raw:
                raise RollbackCorruptionError(
                    "terminal rollback restoration history diverged"
                )
            self._verify_commit(
                restoration_commit.raw,
                identity=rollback_id,
                generation=restoration.generation,
                record_digest=restoration.digest,
            )
            head = _HeldStoreFile.capture(
                self,
                self._head_name(rollback_id),
            )
            held.append(head)
            current = _journal_from_object(
                self._verify_signed(head.raw, "journal-record")
            )
            current_history = _HeldStoreFile.capture(
                self,
                self._history_name(current),
            )
            held.append(current_history)
            current_commit = _HeldStoreFile.capture(
                self,
                self._commit_name(current),
            )
            held.append(current_commit)
            if (
                current.rollback_id != rollback_id
                or anchor not in current.terminal_quarantine_refs
                or current_history.raw != head.raw
            ):
                raise RollbackCorruptionError(
                    "terminal rollback canonical head diverged"
                )
            self._verify_commit(
                current_commit.raw,
                identity=rollback_id,
                generation=current.generation,
                record_digest=current.digest,
            )
            for item in held:
                item.revalidate(self)
        except (OSError, RollbackValidationError, RollbackCorruptionError):
            if block_on_failure:
                self._block_rollback_id(rollback_id)
            raise
        finally:
            for item in reversed(held):
                item.close()

    def _validate_terminal_rollback_quarantines(self) -> None:
        inventory = self._rollback_quarantine_inventory()
        anchors = self._terminal_quarantine_anchors()
        expected_bases = {
            (
                f"{canonical_digest(ref.rollback_id.encode())[7:]}."
                f"{ref.transaction_id}.{ref.successor_record_digest[7:]}"
            ): ref
            for ref in anchors.values()
        }
        if set(inventory) - set(expected_bases):
            raise RollbackCorruptionError(
                "rollback terminal anchor and pair inventory diverged"
            )
        for base, anchor in expected_bases.items():
            blocked = self._rollback_id_blocked(anchor.rollback_id)
            artifacts = inventory.get(base)
            if artifacts is None:
                if blocked:
                    continue
                raise RollbackCorruptionError(
                    "rollback terminal anchor and pair inventory diverged"
                )
            if blocked:
                try:
                    self._terminal_pair_evidence(anchor, artifacts)
                except (
                    OSError,
                    RollbackValidationError,
                    RollbackCorruptionError,
                ):
                    continue
                continue
            payload, successor_record, successor_raw = self._terminal_pair_evidence(
                anchor, artifacts
            )
            self._validate_live_terminal_anchor(
                anchor,
                payload,
                successor_record,
                successor_raw,
            )

    @staticmethod
    def _temp_identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_ctime_ns,
        )

    def _scan_abandoned_temp_names(
        self,
        pattern: re.Pattern[str],
        *,
        collect: bool,
    ) -> tuple[
        tuple[int, int, int, int, int, int],
        list[str],
        list[str],
    ]:
        entry_count = 0
        aggregate_name_bytes = 0
        name_digest_sum = 0
        name_digest_xor = 0
        owned_count = 0
        owned_name_bytes = 0
        names: list[str] = []
        root_names: list[str] = []
        owned_prefix = f".{self._domain}."
        owned_suffixes = (
            ".immutable",
            ".rollback",
            ".tmp",
            ".transaction-rollback",
            ".displaced-head",
            ".prior-candidate",
        )
        with os.scandir(self._root_fd) as entries:
            for entry in entries:
                name = entry.name
                if type(name) is not str:
                    raise RollbackCorruptionError(
                        "rollback store root entry name is invalid"
                    )
                try:
                    encoded_name = name.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise RollbackCorruptionError(
                        "rollback store root entry name is not UTF-8"
                    ) from error
                entry_count += 1
                aggregate_name_bytes += len(encoded_name)
                if (
                    entry_count > _MAX_ROOT_ENTRIES
                    or aggregate_name_bytes > _MAX_ROOT_NAME_BYTES
                ):
                    raise RollbackCorruptionError(
                        "rollback store root enumeration bound is exhausted"
                    )
                name_digest = int.from_bytes(
                    hashlib.sha256(encoded_name).digest(),
                    "big",
                )
                name_digest_sum = (name_digest_sum + name_digest) % (1 << 256)
                name_digest_xor ^= name_digest
                if collect:
                    root_names.append(name)
                match = pattern.fullmatch(name)
                looks_owned = name.startswith(owned_prefix) and name.endswith(
                    owned_suffixes
                )
                if looks_owned and match is None:
                    raise RollbackCorruptionError(
                        "abandoned rollback temp name is invalid"
                    )
                if match is None:
                    continue
                owned_count += 1
                owned_name_bytes += len(encoded_name)
                if (
                    owned_count > _MAX_ABANDONED_TEMPS
                    or owned_name_bytes > _MAX_ABANDONED_TEMP_NAME_BYTES
                ):
                    raise RollbackCorruptionError(
                        "abandoned rollback temp bound is exhausted"
                    )
                if collect:
                    names.append(name)
        return (
            (
                entry_count,
                aggregate_name_bytes,
                name_digest_sum,
                name_digest_xor,
                owned_count,
                owned_name_bytes,
            ),
            names,
            root_names,
        )

    def _bounded_root_names(self) -> tuple[str, ...]:
        def scan(
            *, collect: bool
        ) -> tuple[
            tuple[int, int, int, int],
            list[str],
        ]:
            entry_count = 0
            aggregate_name_bytes = 0
            name_digest_sum = 0
            name_digest_xor = 0
            names: list[str] = []
            with os.scandir(self._root_fd) as entries:
                for entry in entries:
                    name = entry.name
                    if type(name) is not str:
                        raise RollbackCorruptionError(
                            "rollback store root entry name is invalid"
                        )
                    try:
                        encoded_name = name.encode("utf-8")
                    except UnicodeEncodeError as error:
                        raise RollbackCorruptionError(
                            "rollback store root entry name is not UTF-8"
                        ) from error
                    entry_count += 1
                    aggregate_name_bytes += len(encoded_name)
                    if (
                        entry_count > _MAX_ROOT_ENTRIES
                        or aggregate_name_bytes > _MAX_ROOT_NAME_BYTES
                    ):
                        raise RollbackCorruptionError(
                            "rollback store root enumeration bound is exhausted"
                        )
                    name_digest = int.from_bytes(
                        hashlib.sha256(encoded_name).digest(),
                        "big",
                    )
                    name_digest_sum = (name_digest_sum + name_digest) % (1 << 256)
                    name_digest_xor ^= name_digest
                    if collect:
                        names.append(name)
            return (
                (
                    entry_count,
                    aggregate_name_bytes,
                    name_digest_sum,
                    name_digest_xor,
                ),
                names,
            )

        expected_scan, _ = scan(collect=False)
        observed_scan, names = scan(collect=True)
        if observed_scan != expected_scan or len(set(names)) != len(names):
            raise RollbackCorruptionError(
                "rollback store root changed during bounded enumeration"
            )
        return tuple(sorted(names))

    @property
    def _cleanup_staging_name(self) -> str:
        return f".{self._domain}.cleanup-staging"

    def _cleanup_fault(self, boundary: str) -> None:
        hook = _TEST_CLEANUP_FAULT_HOOK
        if hook is None:
            return
        try:
            hook(boundary)
        except _CleanupInjectedCrash:
            raise
        except BaseException as error:
            raise _CleanupInjectedCrash(boundary) from error

    def _cleanup_recovery_fault(self, boundary: str) -> None:
        checkpoint = self._cleanup_recovery_checkpoint
        if not self._cleanup_forward_active and checkpoint is None:
            return
        is_after = boundary.rsplit(".", 1)[-1].startswith("after")
        if checkpoint is not None:
            checkpoint(boundary, True)
        self._cleanup_fault(f"forward.recovery.{boundary}")
        if not is_after and checkpoint is not None:
            checkpoint(boundary, False)

    @staticmethod
    def _cleanup_authority_temp_name(name: str) -> str:
        if name == _CLEANUP_PREPARING_NAME:
            return _CLEANUP_PREPARING_TEMP_NAME
        if name == _CLEANUP_COMMITTED_NAME:
            return _CLEANUP_COMMITTED_TEMP_NAME
        if name == _CLEANUP_RECEIPT_NAME:
            return _CLEANUP_RECEIPT_TEMP_NAME
        raise RollbackCorruptionError("cleanup authority name is invalid")

    @staticmethod
    def _cleanup_stage_identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            stat.S_IMODE(value.st_mode),
            value.st_nlink,
        )

    def _cleanup_transaction_id(
        self,
        stage_identity: Sequence[int],
        root_identity: Sequence[int],
        root_names: Sequence[str],
    ) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "domain": self._domain,
                    "root_identity": list(root_identity),
                    "root_names": list(root_names),
                    "stage_identity": list(stage_identity),
                }
            )
        ).hexdigest()

    def _rejoin_cleanup_stage(
        self,
        directory_fd: int,
        stage_identity: Sequence[int],
        *,
        expected_names: set[str] | None = None,
    ) -> None:
        descriptor_value = os.fstat(directory_fd)
        path_value = os.stat(
            self._cleanup_staging_name,
            dir_fd=self._root_fd,
            follow_symlinks=False,
        )
        expected = tuple(stage_identity)
        if (
            len(expected) != 6
            or any(type(part) is not int or part < 0 for part in expected)
            or not stat.S_ISDIR(descriptor_value.st_mode)
            or not stat.S_ISDIR(path_value.st_mode)
            or self._cleanup_stage_identity(descriptor_value)[:5] != expected[:5]
            or self._cleanup_stage_identity(path_value)[:5] != expected[:5]
            or descriptor_value.st_nlink != path_value.st_nlink
            or descriptor_value.st_nlink < expected[5]
            or (
                expected_names is not None
                and descriptor_value.st_nlink
                not in {expected[5], expected[5] + len(expected_names)}
            )
            or expected[2:4] != tuple(self._owner)
            or expected[4] != 0o700
            or expected[5] < 2
            or expected[0] != self._root_stat.st_dev
        ):
            raise RollbackCorruptionError(
                "abandoned cleanup staging directory binding changed: "
                f"expected={expected!r}, "
                f"descriptor={self._cleanup_stage_identity(descriptor_value)!r}, "
                f"path={self._cleanup_stage_identity(path_value)!r}"
            )
        if (
            expected_names is not None
            and set(self._bounded_cleanup_staging_names(directory_fd)) != expected_names
        ):
            raise RollbackCorruptionError(
                "cleanup staging inventory binding is invalid"
            )

    def _dispose_cleanup_temp(
        self,
        directory_fd: int,
        name: str,
        stage_identity: Sequence[int],
        *,
        expected_names: set[str],
        prefix: str,
    ) -> None:
        temp = self._validate_cleanup_authority_file(directory_fd, name)
        try:
            if len(temp.raw) > _MAX_CLEANUP_MANIFEST_BYTES:
                raise RollbackCorruptionError(
                    "cleanup temporary authority exceeds bound"
                )
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=expected_names,
            )
            temp.revalidate(self)
            self._remove_cleanup_authority(
                directory_fd,
                name,
                prefix=prefix,
                stage_identity=stage_identity,
                expected_names=expected_names,
                target=temp,
            )
            self._sync_cleanup_stage(directory_fd, prefix=prefix)
        finally:
            temp.close()

    def _cleanup_write_all(self, fd: int, payload: bytes, *, prefix: str) -> None:
        view = memoryview(payload)
        chunk_index = 0
        while view:
            self._cleanup_fault(f"{prefix}.before_write_chunk.{chunk_index}")
            before = len(view)
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("rollback store write made no progress")
            view = view[written:]
            self._cleanup_fault(f"{prefix}.after_write_chunk.{chunk_index}")
            if written < before:
                self._cleanup_fault(f"{prefix}.after_short_write.{chunk_index}")
            chunk_index += 1

    def _cleanup_stage_identity_now(self, directory_fd: int) -> tuple[int, ...]:
        return self._cleanup_stage_identity(os.fstat(directory_fd))

    def _remove_cleanup_stage(
        self,
        directory_fd: int,
        *,
        prefix: str,
        stage_identity: Sequence[int],
    ) -> None:
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=set(),
        )
        self._cleanup_fault(f"{prefix}.before_stage_rmdir")
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=set(),
        )
        os.rmdir(self._cleanup_staging_name, dir_fd=self._root_fd)
        self._cleanup_fault(f"{prefix}.after_stage_rmdir")
        self._cleanup_fault(f"{prefix}.before_parent_fsync")
        os.fsync(self._root_fd)
        self._cleanup_fault(f"{prefix}.after_parent_fsync")

    def _remove_cleanup_authority(
        self,
        directory_fd: int,
        name: str,
        *,
        prefix: str,
        stage_identity: Sequence[int] | None = None,
        expected_names: set[str] | None = None,
        target: _HeldStoreFile | None = None,
        hook_name: str | None = None,
    ) -> None:
        if stage_identity is not None and expected_names is None:
            raise RollbackCorruptionError(
                "cleanup mutation inventory is unavailable"
            )
        owns_target = target is None
        if target is None:
            try:
                target = _HeldStoreFile.capture(
                    self,
                    name,
                    directory_fd=directory_fd,
                )
            except (OSError, RollbackCorruptionError) as error:
                raise RollbackCorruptionError(
                    "cleanup mutation target could not be held"
                ) from error
        try:
            boundary_name = name if hook_name is None else hook_name
            assert target is not None
            self._cleanup_fault(f"{prefix}.before_unlink.{boundary_name}")
            if stage_identity is not None:
                assert expected_names is not None
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=expected_names,
                )
            target.revalidate(self)
            os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            self._cleanup_fault(f"{prefix}.after_unlink.{boundary_name}")
        finally:
            if owns_target:
                assert target is not None
                target.close()

    def _sync_cleanup_stage(self, directory_fd: int, *, prefix: str) -> None:
        self._cleanup_fault(f"{prefix}.before_stage_fsync")
        os.fsync(directory_fd)
        self._cleanup_fault(f"{prefix}.after_stage_fsync")

    def _sync_cleanup_root(self, *, prefix: str) -> None:
        self._cleanup_fault(f"{prefix}.before_root_fsync")
        os.fsync(self._root_fd)
        self._cleanup_fault(f"{prefix}.after_root_fsync")

    def _validate_cleanup_root_inventory(
        self,
        preparing: Mapping[str, object],
        *,
        staged_names: set[str],
        permitted_additions: set[str] | None = None,
    ) -> None:
        expected = set(preparing["root_names"]) - staged_names | {
            self._cleanup_staging_name
        }
        if permitted_additions is not None:
            expected |= permitted_additions
        if set(self._bounded_root_names()) != expected:
            raise RollbackCorruptionError("cleanup root inventory binding is invalid")

    def _validate_cleanup_candidate_name(self, name: object) -> str:
        if type(name) is not str or name in (
            "",
            ".",
            "..",
            _CLEANUP_PREPARING_NAME,
            _CLEANUP_COMMITTED_NAME,
            _CLEANUP_PREPARING_TEMP_NAME,
            _CLEANUP_COMMITTED_TEMP_NAME,
            _CLEANUP_RECEIPT_NAME,
            _CLEANUP_RECEIPT_TEMP_NAME,
            self._cleanup_staging_name,
        ):
            raise RollbackCorruptionError("cleanup candidate name is invalid")
        if (
            os.path.isabs(name)
            or "/" in name
            or (os.altsep is not None and os.altsep in name)
            or os.path.normpath(name) != name
        ):
            raise RollbackCorruptionError("cleanup candidate name is invalid")
        return name

    def _validate_cleanup_authority_file(
        self,
        directory_fd: int,
        name: str,
    ) -> _HeldStoreFile:
        try:
            return _HeldStoreFile.capture(self, name, directory_fd=directory_fd)
        except (OSError, RollbackCorruptionError) as error:
            raise RollbackCorruptionError(
                "cleanup staging authority file is not exact"
            ) from error

    def _validate_preparing_candidate_positions(
        self,
        directory_fd: int,
        preparing: Mapping[str, object],
    ) -> tuple[list[_HeldStoreFile], dict[str, int]]:
        candidates = tuple(preparing["candidates"])
        held: list[_HeldStoreFile] = []
        locations: dict[str, int] = {}
        try:
            for expected in candidates:
                name = self._validate_cleanup_candidate_name(expected["name"])
                root_exists = self._path_exists_at(self._root_fd, name)
                staged_exists = self._path_exists_at(directory_fd, name)
                if root_exists == staged_exists:
                    raise RollbackCorruptionError(
                        "cleanup preparing candidate location is ambiguous"
                    )
                location = self._root_fd if root_exists else directory_fd
                candidate = _HeldStoreFile.capture(
                    self,
                    name,
                    directory_fd=location,
                )
                if not self._cleanup_candidate_survives_rename(candidate, expected):
                    raise RollbackCorruptionError(
                        "cleanup preparing candidate identity changed"
                    )
                held.append(candidate)
                locations[name] = location
            staged_names = {
                name for name, location in locations.items() if location == directory_fd
            }
            self._validate_cleanup_root_inventory(
                preparing,
                staged_names=staged_names,
            )
            for candidate in held:
                candidate.revalidate(self)
            return held, locations
        except BaseException:
            for candidate in held:
                candidate.close()
            raise

    @staticmethod
    def _path_exists_at(directory_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def _open_cleanup_staging(self, *, create: bool) -> tuple[int, bool] | None:
        created = False
        if create:
            try:
                self._cleanup_fault("stage_dir.before_create")
                os.mkdir(self._cleanup_staging_name, 0o700, dir_fd=self._root_fd)
                self._cleanup_fault("stage_dir.after_create")
                self._sync_cleanup_root(prefix="stage_dir")
                created = True
            except FileExistsError:
                pass
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(self._cleanup_staging_name, flags, dir_fd=self._root_fd)
        except FileNotFoundError:
            return None
        try:
            value = os.fstat(fd)
            path_value = os.stat(
                self._cleanup_staging_name,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(value.st_mode)
                or stat.S_IMODE(value.st_mode) != 0o700
                or (value.st_uid, value.st_gid) != self._owner
                or (value.st_dev, value.st_ino)
                != (path_value.st_dev, path_value.st_ino)
                or value.st_dev != self._root_stat.st_dev
                or value.st_nlink < 2
            ):
                raise RollbackCorruptionError(
                    "abandoned cleanup staging directory is not exact"
                )
            return fd, created
        except BaseException:
            os.close(fd)
            raise

    def _write_cleanup_authority(
        self,
        directory_fd: int,
        name: str,
        raw: bytes,
        *,
        stage_identity: Sequence[int] | None = None,
        expected_names: set[str] | None = None,
        replace: bool = False,
        boundary_prefix: str | None = None,
    ) -> None:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup staging authority exceeds bound")
        prefix = boundary_prefix or f"authority.{name}"
        temp_name = self._cleanup_authority_temp_name(name)
        if stage_identity is None:
            stage_identity = self._cleanup_stage_identity_now(directory_fd)
        if expected_names is None:
            expected_names = set(self._bounded_cleanup_staging_names(directory_fd))
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=expected_names,
        )
        self._cleanup_fault(f"{prefix}.before_temp_create")
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        self._cleanup_fault(f"{prefix}.after_temp_create")
        try:
            created = os.fstat(fd)
            if (
                not stat.S_ISREG(created.st_mode)
                or stat.S_IMODE(created.st_mode) != 0o600
                or created.st_nlink != 1
                or (created.st_uid, created.st_gid) != self._owner
                or created.st_size != 0
            ):
                raise RollbackCorruptionError(
                    "cleanup temporary authority file is not exact"
                )
            self._cleanup_fault(f"{prefix}.before_temp_write")
            self._cleanup_write_all(fd, raw, prefix=prefix)
            self._cleanup_fault(f"{prefix}.after_temp_write")
            self._cleanup_fault(f"{prefix}.before_temp_fsync")
            os.fsync(fd)
            self._cleanup_fault(f"{prefix}.after_temp_fsync")
        finally:
            os.close(fd)
        names_with_temp = {*expected_names, temp_name}
        self._rejoin_cleanup_stage(
            directory_fd,
            stage_identity,
            expected_names=names_with_temp,
        )
        temp = self._validate_cleanup_authority_file(directory_fd, temp_name)
        replacement_target: _HeldStoreFile | None = None
        try:
            if temp.raw != raw:
                raise RollbackCorruptionError(
                    "cleanup temporary authority write is incomplete"
                )
            temp.revalidate(self)
            replacement_target = (
                _HeldStoreFile.capture(
                    self,
                    name,
                    directory_fd=directory_fd,
                )
                if replace
                else None
            )
            self._cleanup_fault(f"{prefix}.before_rename")
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=names_with_temp,
            )
            temp.revalidate(self)
            if replacement_target is not None:
                replacement_target.revalidate(self)
            elif self._path_exists_at(directory_fd, name):
                raise RollbackCorruptionError(
                    "cleanup authority destination appeared"
                )
            if replace:
                os.replace(
                    temp_name,
                    name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            else:
                _rename_noreplace_between(
                    temp_name,
                    name,
                    directory_fd,
                    directory_fd,
                )
            self._cleanup_fault(f"{prefix}.after_rename")
        finally:
            temp.close()
            if replacement_target is not None:
                replacement_target.close()
        self._sync_cleanup_stage(directory_fd, prefix=prefix)

    def _bounded_cleanup_staging_names(self, directory_fd: int) -> tuple[str, ...]:
        def scan(*, collect: bool) -> tuple[tuple[int, int, int, int], list[str]]:
            count = 0
            name_bytes = 0
            digest_sum = 0
            digest_xor = 0
            names: list[str] = []
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    name = entry.name
                    if type(name) is not str:
                        raise RollbackCorruptionError(
                            "cleanup staging entry name is invalid"
                        )
                    try:
                        encoded = name.encode("utf-8")
                    except UnicodeEncodeError as error:
                        raise RollbackCorruptionError(
                            "cleanup staging entry name is not UTF-8"
                        ) from error
                    count += 1
                    name_bytes += len(encoded)
                    if (
                        count > _MAX_ABANDONED_TEMPS + 3
                        or name_bytes
                        > _MAX_ABANDONED_TEMP_NAME_BYTES
                        + len(_CLEANUP_PREPARING_NAME)
                        + len(_CLEANUP_COMMITTED_NAME)
                        + len(_CLEANUP_RECEIPT_NAME)
                        + len(_CLEANUP_RECEIPT_TEMP_NAME)
                    ):
                        raise RollbackCorruptionError(
                            "cleanup staging enumeration bound is exhausted"
                        )
                    digest = int.from_bytes(hashlib.sha256(encoded).digest(), "big")
                    digest_sum = (digest_sum + digest) % (1 << 256)
                    digest_xor ^= digest
                    if collect:
                        names.append(name)
            return (count, name_bytes, digest_sum, digest_xor), names

        expected, _ = scan(collect=False)
        observed, names = scan(collect=True)
        if expected != observed or len(set(names)) != len(names):
            raise RollbackCorruptionError(
                "cleanup staging changed during bounded enumeration"
            )
        return tuple(sorted(names))

    def _cleanup_preparing_payload(
        self,
        raw: bytes,
        directory_fd: int,
    ) -> dict[str, object]:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup preparing authority exceeds bound")
        payload = _require_object(
            self._verify_signed(raw, "abandoned-cleanup-preparing"),
            frozenset(
                (
                    "candidates",
                    "domain",
                    "root_identity",
                    "root_names",
                    "schema_version",
                    "stage_identity",
                    "state",
                    "transaction_id",
                )
            ),
            "abandoned cleanup preparing authority",
        )
        if (
            payload["schema_version"] != "bb.rl.phase5.abandoned-cleanup-preparing.v2"
            or payload["domain"] != self._domain
            or payload["state"] != "preparing"
        ):
            raise RollbackCorruptionError("cleanup preparing binding is invalid")
        root_identity = _require_tuple(
            payload["root_identity"],
            "cleanup preparing root identity",
        )
        if (
            len(root_identity) != 4
            or any(type(item) is not int for item in root_identity)
            or tuple(root_identity)
            != (
                self._root_stat.st_dev,
                self._root_stat.st_ino,
                self._owner[0],
                self._owner[1],
            )
        ):
            raise RollbackCorruptionError("cleanup preparing root binding is invalid")
        root_names = _require_tuple(
            payload["root_names"],
            "cleanup preparing root names",
        )
        if len(root_names) > _MAX_ROOT_ENTRIES:
            raise RollbackCorruptionError("cleanup preparing root inventory is invalid")
        root_name_bytes = 0
        for name in root_names:
            if (
                type(name) is not str
                or not name
                or os.path.isabs(name)
                or "/" in name
                or (os.altsep is not None and os.altsep in name)
                or os.path.normpath(name) != name
            ):
                raise RollbackCorruptionError(
                    "cleanup preparing root inventory is invalid"
                )
            try:
                root_name_bytes += len(name.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise RollbackCorruptionError(
                    "cleanup preparing root inventory is invalid"
                ) from error
        if (
            sorted(root_names) != root_names
            or len(set(root_names)) != len(root_names)
            or root_name_bytes > _MAX_ROOT_NAME_BYTES
            or self._cleanup_staging_name in root_names
        ):
            raise RollbackCorruptionError("cleanup preparing root inventory is invalid")
        stage_identity = _require_tuple(
            payload["stage_identity"],
            "cleanup preparing stage identity",
        )
        self._rejoin_cleanup_stage(directory_fd, stage_identity)
        transaction_id = payload["transaction_id"]
        if type(
            transaction_id
        ) is not str or transaction_id != self._cleanup_transaction_id(
            stage_identity,
            root_identity,
            root_names,
        ):
            raise RollbackCorruptionError(
                "cleanup preparing transaction binding is invalid"
            )
        candidates = _require_tuple(
            payload["candidates"],
            "cleanup preparing candidates",
        )
        if len(candidates) > _MAX_ABANDONED_TEMPS:
            raise RollbackCorruptionError(
                "cleanup preparing candidate bound is exhausted"
            )
        candidate_names: list[str] = []
        total_bytes = 0
        for candidate in candidates:
            item = _require_object(
                candidate,
                frozenset(("identity", "name", "raw_sha256")),
                "cleanup preparing candidate",
            )
            name = item["name"]
            identity = _require_tuple(
                item["identity"],
                "cleanup preparing candidate identity",
            )
            name = self._validate_cleanup_candidate_name(name)
            if (
                len(identity) != 8
                or any(type(part) is not int or part < 0 for part in identity)
                or identity[2:4] != [self._owner[0], self._owner[1]]
                or identity[4] != 0o600
                or identity[5] != 1
                or identity[6] > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError(
                    "cleanup preparing candidate identity is invalid"
                )
            _require_digest(item["raw_sha256"], "cleanup candidate raw digest")
            candidate_names.append(name)
            total_bytes += identity[6]
        if (
            tuple(sorted(candidate_names)) != tuple(candidate_names)
            or len(set(candidate_names)) != len(candidate_names)
            or sum(len(name.encode("utf-8")) for name in candidate_names)
            > _MAX_ABANDONED_TEMP_NAME_BYTES
            or total_bytes > _MAX_ABANDONED_TEMP_BYTES
        ):
            raise RollbackCorruptionError(
                "cleanup preparing candidate inventory is invalid"
            )
        return payload

    def _cleanup_object_proof(
        self,
        candidate: _HeldStoreFile,
        *,
        location: str,
        path: str,
    ) -> dict[str, object]:
        if location not in {"root", "stage", "terminal"}:
            raise RollbackCorruptionError("cleanup proof location is invalid")
        self._validate_cleanup_candidate_name(path)
        return {
            "identity": list(candidate.identity),
            "location": location,
            "path": path,
            "raw_sha256": canonical_digest(candidate.raw),
        }

    def _validate_cleanup_object_proof(
        self,
        value: object,
        *,
        label: str,
    ) -> dict[str, object]:
        proof = _require_object(
            value,
            frozenset(("identity", "location", "path", "raw_sha256")),
            label,
        )
        identity = _require_tuple(proof["identity"], f"{label} identity")
        location = proof["location"]
        path = proof["path"]
        if (
            len(identity) != 8
            or any(type(part) is not int or part < 0 for part in identity)
            or identity[2:4] != [self._owner[0], self._owner[1]]
            or identity[4] != 0o600
            or identity[5] != 1
            or identity[6] > _MAX_RECORD_BYTES
            or location not in {"root", "stage", "terminal"}
        ):
            raise RollbackCorruptionError(f"{label} identity is invalid")
        self._validate_cleanup_candidate_name(path)
        _require_digest(proof["raw_sha256"], f"{label} raw digest")
        return proof

    def _cleanup_replacement_temp_location(self, temp: object) -> str:
        if type(temp) is not str:
            raise RollbackCorruptionError(
                "cleanup replacement temporary name is invalid"
            )
        if re.fullmatch(
            rf"\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\.tmp",
            temp,
        ):
            return "root"
        if re.fullmatch(r"\.intent-replace-[0-9a-f]{32}", temp):
            return "stage"
        raise RollbackCorruptionError(
            "cleanup replacement temporary name is invalid"
        )


    def _validate_cleanup_replacement_proof(
        self,
        value: object,
        *,
        label: str,
    ) -> dict[str, object]:
        proof = _require_object(
            value,
            frozenset(
                (
                    "destination",
                    "destination_digest",
                    "destination_identity",
                    "expected_digest",
                    "expected_size",
                    "expected_payload",
                    "identity",
                    "observed_digest",
                    "state",
                    "temp",
                )
            ),
            label,
        )
        state = proof["state"]
        temp = proof["temp"]
        destination = proof["destination"]
        expected_size = proof["expected_size"]
        temp_location = self._cleanup_replacement_temp_location(temp)
        if (
            state not in {"preparing", "created", "ready", "post"}
            or temp_location not in {"root", "stage"}
            or type(destination) is not str
            or self._validate_cleanup_candidate_name(destination) != destination
            or type(expected_size) is not int
            or not 0 <= expected_size <= _MAX_RECORD_BYTES
        ):
            raise RollbackCorruptionError(f"{label} binding is invalid")
        expected_payload = proof["expected_payload"]
        if type(expected_payload) is not str:
            raise RollbackCorruptionError(f"{label} expected payload is invalid")
        expected_raw = expected_payload.encode("utf-8")
        if (
            len(expected_raw) != expected_size
            or canonical_digest(expected_raw) != proof["expected_digest"]
        ):
            raise RollbackCorruptionError(f"{label} expected payload is invalid")
        _require_digest(proof["expected_digest"], f"{label} expected digest")
        destination_identity = proof["destination_identity"]
        destination_digest = proof["destination_digest"]
        if destination_identity is None:
            if destination_digest is not None:
                raise RollbackCorruptionError(
                    f"{label} destination binding is invalid"
                )
        else:
            identity = _require_tuple(
                destination_identity,
                f"{label} destination identity",
            )
            if (
                len(identity) != 8
                or any(type(part) is not int or part < 0 for part in identity)
                or identity[2:4] != [self._owner[0], self._owner[1]]
                or identity[4] != 0o600
                or identity[5] != 1
                or identity[6] > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError(
                    f"{label} destination identity is invalid"
                )
            _require_digest(
                destination_digest,
                f"{label} destination digest",
            )
        if temp_location == "stage":
            if destination_identity is None:
                raise RollbackCorruptionError(
                    f"{label} stage destination binding is invalid"
                )
            expected_token = canonical_digest(
                canonical_json_bytes(
                    {
                        "name": destination,
                        "old_identity": list(destination_identity),
                        "payload_digest": proof["expected_digest"],
                    }
                )
            )[7:39]
            if temp != f".intent-replace-{expected_token}":
                raise RollbackCorruptionError(
                    f"{label} stage temporary binding is invalid"
                )
        replacement_identity = proof["identity"]
        observed_digest = proof["observed_digest"]
        if state == "preparing":
            if replacement_identity is not None or observed_digest is not None:
                raise RollbackCorruptionError(f"{label} preparing state is invalid")
        else:
            identity = _require_tuple(
                replacement_identity,
                f"{label} identity",
            )
            if (
                len(identity) != 8
                or any(type(part) is not int or part < 0 for part in identity)
                or identity[2:4] != [self._owner[0], self._owner[1]]
                or identity[4] != 0o600
                or identity[5] != 1
                or identity[6] > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError(f"{label} identity is invalid")
            _require_digest(observed_digest, f"{label} observed digest")
            if state in {"ready", "post"} and (
                identity[6] != expected_size
                or observed_digest != proof["expected_digest"]
            ):
                raise RollbackCorruptionError(f"{label} final state is invalid")
        return proof

    def _validate_terminal_cleanup_replacement(
        self,
        recovery_proof: Mapping[str, object],
    ) -> tuple[dict[str, object], dict[str, object] | None]:
        replacement_value = recovery_proof.get("replacement")
        if replacement_value is None:
            return dict(recovery_proof), None
        replacement = self._validate_cleanup_replacement_proof(
            replacement_value,
            label="terminal cleanup replacement proof",
        )
        if replacement["state"] != "post":
            raise RollbackCorruptionError(
                "terminal cleanup replacement proof is not poststate"
            )
        if self._path_exists_at(self._root_fd, str(replacement["temp"])):
            raise RollbackCorruptionError(
                "terminal cleanup replacement temporary survived"
            )
        objects = _require_tuple(
            recovery_proof.get("objects"),
            "terminal cleanup recovery proof objects",
        )
        destination_matches: list[dict[str, object]] = []
        for value in objects:
            proof = self._validate_cleanup_object_proof(
                value,
                label="terminal cleanup recovery object proof",
            )
            if (
                proof["location"] == "root"
                and proof["path"] == replacement["destination"]
            ):
                destination_matches.append(proof)
        if len(destination_matches) != 1:
            raise RollbackCorruptionError(
                "terminal cleanup replacement destination proof is not unique"
            )
        destination_proof = destination_matches[0]
        expected_raw = str(replacement["expected_payload"]).encode("utf-8")
        if (
            destination_proof["identity"] != replacement["identity"]
            or destination_proof["raw_sha256"]
            != replacement["expected_digest"]
        ):
            raise RollbackCorruptionError(
                "terminal cleanup replacement destination proof diverged"
            )
        installed = _HeldStoreFile.capture(
            self,
            str(replacement["destination"]),
        )
        try:
            if (
                installed.identity != tuple(replacement["identity"])
                or installed.raw != expected_raw
                or canonical_digest(installed.raw)
                != replacement["expected_digest"]
            ):
                raise RollbackCorruptionError(
                    "terminal cleanup replacement destination changed"
                )
            installed.revalidate(self)
        finally:
            installed.close()
        active = self._cleanup_recovery_replace_proof
        if active is not None and active != replacement:
            raise RollbackCorruptionError(
                "active cleanup replacement proof diverged"
            )
        self._cleanup_recovery_replace_proof = None
        self._cleanup_recovery_replace_temp = None
        self._cleanup_recovery_replace_destination = None
        cleaned = dict(recovery_proof)
        del cleaned["replacement"]
        return cleaned, dict(replacement)

    def _cleanup_committed_payload(
        self,
        raw: bytes,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
    ) -> dict[str, object]:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup committed authority exceeds bound")
        payload = _require_object(
            self._verify_signed(raw, "abandoned-cleanup-committed"),
            frozenset(
                (
                    "candidate_states",
                    "domain",
                    "preparing_digest",
                    "progress_generation",
                    "recovery_proof",
                    "schema_version",
                    "stage_identity",
                    "state",
                    "tombstone_proofs",
                    "transaction_id",
                )
            ),
            "abandoned cleanup committed authority",
        )
        candidate_states = _require_tuple(
            payload["candidate_states"],
            "cleanup committed candidate states",
        )
        expected_names = tuple(item["name"] for item in tuple(preparing["candidates"]))
        observed_names: list[str] = []
        for candidate_state in candidate_states:
            item = _require_object(
                candidate_state,
                frozenset(("name", "state")),
                "cleanup committed candidate state",
            )
            observed_names.append(self._validate_cleanup_candidate_name(item["name"]))
            if item["state"] not in ("pending", "processing", "processed"):
                raise RollbackCorruptionError(
                    "cleanup committed candidate progress is invalid"
                )
        tombstone_proofs = _require_tuple(
            payload["tombstone_proofs"],
            "cleanup committed tombstone proofs",
        )
        tombstone_names: list[str] = []
        for tombstone_proof in tombstone_proofs:
            item = _require_object(
                tombstone_proof,
                frozenset(("candidate_name", "proof", "status")),
                "cleanup committed tombstone proof",
            )
            candidate_name = self._validate_cleanup_candidate_name(
                item["candidate_name"]
            )
            proof = self._validate_cleanup_object_proof(
                item["proof"],
                label="cleanup committed tombstone proof",
            )
            if item["status"] not in {"moving", "processed"}:
                raise RollbackCorruptionError(
                    "cleanup committed tombstone status is invalid"
                )
            if (
                proof["location"] != "stage"
                or not str(proof["path"]).endswith(".cleanup-tombstone")
            ):
                raise RollbackCorruptionError(
                    "cleanup committed tombstone binding is invalid"
                )
            tombstone_names.append(candidate_name)
        recovery_proof = payload["recovery_proof"]
        if recovery_proof is not None:
            recovery_keys = (
                frozenset(recovery_proof)
                if type(recovery_proof) is dict
                else frozenset()
            )
            expected_recovery_keys = frozenset(("objects", "substate"))
            if recovery_keys not in {
                expected_recovery_keys,
                expected_recovery_keys | {"replacement"},
            }:
                raise RollbackCorruptionError(
                    "cleanup committed recovery proof has invalid keys"
                )
            recovery = _require_object(
                recovery_proof,
                recovery_keys,
                "cleanup committed recovery proof",
            )
            if "replacement" in recovery:
                self._validate_cleanup_replacement_proof(
                    recovery["replacement"],
                    label="cleanup committed replacement proof",
                )
            if type(recovery["substate"]) is not str or not recovery["substate"]:
                raise RollbackCorruptionError(
                    "cleanup committed recovery substate is invalid"
                )
            objects = _require_tuple(
                recovery["objects"],
                "cleanup committed recovery proof objects",
            )
            if not objects or len(objects) > 16:
                raise RollbackCorruptionError(
                    "cleanup committed recovery proof bound is invalid"
                )
            proof_keys: list[tuple[str, str]] = []
            for proof_value in objects:
                proof = self._validate_cleanup_object_proof(
                    proof_value,
                    label="cleanup committed recovery object proof",
                )
                proof_keys.append((proof["location"], proof["path"]))
            if proof_keys != sorted(proof_keys) or len(set(proof_keys)) != len(
                proof_keys
            ):
                raise RollbackCorruptionError(
                    "cleanup committed recovery proof ordering is invalid"
                )
        stage_identity = _require_tuple(
            payload["stage_identity"],
            "cleanup committed stage identity",
        )
        generation = payload["progress_generation"]
        if (
            payload["schema_version"] != "bb.rl.phase5.abandoned-cleanup-committed.v3"
            or payload["domain"] != self._domain
            or payload["state"] != "committed"
            or payload["preparing_digest"] != canonical_digest(preparing_raw)
            or tuple(observed_names) != expected_names
            or type(generation) is not int
            or generation < 0
            or generation > 2 * len(expected_names) + 2
            or tombstone_names != sorted(tombstone_names)
            or len(set(tombstone_names)) != len(tombstone_names)
            or any(name not in expected_names for name in tombstone_names)
            or list(stage_identity) != list(preparing["stage_identity"])
            or payload["transaction_id"] != preparing["transaction_id"]
        ):
            raise RollbackCorruptionError("cleanup committed binding is invalid")
        return payload

    def _cleanup_committed_bytes(
        self,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        candidate_states: Sequence[Mapping[str, object]],
        progress_generation: int,
        *,
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
    ) -> bytes:
        return self._signed_bytes(
            "abandoned-cleanup-committed",
            {
                "candidate_states": [
                    {"name": item["name"], "state": item["state"]}
                    for item in candidate_states
                ],
                "domain": self._domain,
                "preparing_digest": canonical_digest(preparing_raw),
                "progress_generation": progress_generation,
                "recovery_proof": recovery_proof,
                "schema_version": "bb.rl.phase5.abandoned-cleanup-committed.v3",
                "stage_identity": list(preparing["stage_identity"]),
                "state": "committed",
                "tombstone_proofs": list(tombstone_proofs),
                "transaction_id": preparing["transaction_id"],
            },
        )

    def _persist_cleanup_progress(
        self,
        directory_fd: int,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
        candidate_states: list[dict[str, object]],
        progress_generation: int,
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
        *,
        expected_names: set[str],
    ) -> tuple[bytes, int]:
        current = self._validate_cleanup_authority_file(
            directory_fd,
            _CLEANUP_COMMITTED_NAME,
        )
        try:
            if current.raw != committed_raw:
                raise RollbackCorruptionError(
                    "cleanup committed authority changed during progress"
                )
            current.revalidate(self)
            self._rejoin_cleanup_stage(
                directory_fd,
                preparing["stage_identity"],
                expected_names=expected_names,
            )
            next_generation = progress_generation + 1
            next_raw = self._cleanup_committed_bytes(
                preparing_raw,
                preparing,
                candidate_states,
                next_generation,
                tombstone_proofs=tombstone_proofs,
                recovery_proof=recovery_proof,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_COMMITTED_NAME,
                next_raw,
                stage_identity=preparing["stage_identity"],
                expected_names=expected_names,
                replace=True,
                boundary_prefix=f"authority.committed.g{next_generation}",
            )
            return next_raw, next_generation
        finally:
            current.close()

    def _persist_cleanup_recovery_checkpoint(
        self,
        directory_fd: int,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
        candidate_states: list[dict[str, object]],
        progress_generation: int,
        *,
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
        expected_names: set[str],
        boundary: str,
    ) -> bytes:
        current = self._validate_cleanup_authority_file(
            directory_fd,
            _CLEANUP_COMMITTED_NAME,
        )
        try:
            if current.raw != committed_raw:
                raise RollbackCorruptionError(
                    "cleanup committed authority changed during recovery checkpoint"
                )
            next_raw = self._cleanup_committed_bytes(
                preparing_raw,
                preparing,
                candidate_states,
                progress_generation,
                tombstone_proofs=tombstone_proofs,
                recovery_proof=recovery_proof,
            )
            current.revalidate(self)
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_COMMITTED_NAME,
                next_raw,
                stage_identity=preparing["stage_identity"],
                expected_names=expected_names,
                replace=True,
                boundary_prefix=f"authority.recovery_checkpoint.{boundary}",
            )
            return next_raw
        finally:
            current.close()

    def _cleanup_receipt_bytes(
        self,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
        candidate_names: tuple[str, ...],
        tombstone_proofs: Sequence[Mapping[str, object]],
        recovery_proof: Mapping[str, object] | None,
        terminal_replacement_proof: Mapping[str, object] | None,
    ) -> bytes:
        return self._signed_bytes(
            "abandoned-cleanup-receipt",
            {
                "candidate_names": list(candidate_names),
                "committed_digest": canonical_digest(committed_raw),
                "domain": self._domain,
                "preparing_digest": canonical_digest(preparing_raw),
                "recovery_proof": recovery_proof,
                "schema_version": "bb.rl.phase5.abandoned-cleanup-receipt.v4",
                "stage_identity": list(preparing["stage_identity"]),
                "state": "complete",
                "terminal_removal_intent": True,
                "tombstone_proofs": list(tombstone_proofs),
                "terminal_replacement_proof": terminal_replacement_proof,
                "transaction_id": preparing["transaction_id"],
            },
        )

    def _cleanup_receipt_payload(
        self,
        raw: bytes,
    ) -> Mapping[str, object]:
        if len(raw) > _MAX_CLEANUP_MANIFEST_BYTES:
            raise RollbackCorruptionError("cleanup receipt authority exceeds bound")
        payload = _require_object(
            self._verify_signed(raw, "abandoned-cleanup-receipt"),
            frozenset(
                (
                    "candidate_names",
                    "committed_digest",
                    "domain",
                    "preparing_digest",
                    "recovery_proof",
                    "schema_version",
                    "stage_identity",
                    "state",
                    "terminal_removal_intent",
                    "terminal_replacement_proof",
                    "tombstone_proofs",
                    "transaction_id",
                )
            ),
            "abandoned cleanup receipt",
        )
        candidate_names = _require_tuple(
            payload["candidate_names"],
            "cleanup receipt candidate names",
        )
        stage_identity = _require_tuple(
            payload["stage_identity"],
            "cleanup receipt stage identity",
        )
        if (
            payload["schema_version"] != "bb.rl.phase5.abandoned-cleanup-receipt.v4"
            or payload["domain"] != self._domain
            or payload["state"] != "complete"
            or payload["terminal_removal_intent"] is not True
            or type(payload["transaction_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", payload["transaction_id"]) is None
            or len(stage_identity) != 6
            or any(type(part) is not int or part < 0 for part in stage_identity)
            or len(candidate_names) > _MAX_ABANDONED_TEMPS
            or any(type(name) is not str for name in candidate_names)
            or candidate_names != sorted(candidate_names)
            or len(set(candidate_names)) != len(candidate_names)
        ):
            raise RollbackCorruptionError("cleanup receipt binding is invalid")
        _require_digest(payload["preparing_digest"], "cleanup preparing digest")
        _require_digest(payload["committed_digest"], "cleanup committed digest")
        for name in candidate_names:
            self._validate_cleanup_candidate_name(name)
        _require_tuple(
            payload["tombstone_proofs"],
            "cleanup receipt tombstone proofs",
        )
        if payload["recovery_proof"] is not None:
            _require_object(
                payload["recovery_proof"],
                frozenset(("objects", "substate")),
                "cleanup receipt recovery proof",
            )
        terminal_replacement = payload["terminal_replacement_proof"]
        if terminal_replacement is not None:
            if payload["recovery_proof"] is None:
                raise RollbackCorruptionError(
                    "cleanup receipt replacement has no recovery proof"
                )
            replacement = self._validate_cleanup_replacement_proof(
                terminal_replacement,
                label="cleanup receipt terminal replacement proof",
            )
            if replacement["state"] != "post":
                raise RollbackCorruptionError(
                    "cleanup receipt terminal replacement is not poststate"
                )
        return payload

    def _resume_cleanup_receipt(
        self,
        directory_fd: int,
        names: tuple[str, ...],
    ) -> None:
        receipt_file = self._validate_cleanup_authority_file(
            directory_fd,
            _CLEANUP_RECEIPT_NAME,
        )
        try:
            receipt = self._cleanup_receipt_payload(receipt_file.raw)
            stage_identity = receipt["stage_identity"]
            candidate_names = tuple(receipt["candidate_names"])
            tombstone_proofs = tuple(receipt["tombstone_proofs"])
            tombstone_paths: set[str] = set()
            tombstone_candidates: list[str] = []
            for value in tombstone_proofs:
                item = _require_object(
                    value,
                    frozenset(("candidate_name", "proof", "status")),
                    "cleanup receipt tombstone proof",
                )
                if item["status"] != "processed":
                    raise RollbackCorruptionError(
                        "cleanup receipt tombstone is not terminal"
                    )
                candidate_name = self._validate_cleanup_candidate_name(
                    item["candidate_name"]
                )
                proof = self._validate_cleanup_object_proof(
                    item["proof"],
                    label="cleanup receipt tombstone proof",
                )
                if (
                    proof["location"] != "stage"
                    or not str(proof["path"]).endswith(".cleanup-tombstone")
                ):
                    raise RollbackCorruptionError(
                        "cleanup receipt tombstone binding is invalid"
                    )
                tombstone_candidates.append(candidate_name)
                tombstone_paths.add(str(proof["path"]))
            if (
                tombstone_candidates != sorted(tombstone_candidates)
                or len(tombstone_paths) != len(tombstone_proofs)
                or any(name not in candidate_names for name in tombstone_candidates)
            ):
                raise RollbackCorruptionError(
                    "cleanup receipt tombstone ordering is invalid"
                )
            recovery_proof = receipt["recovery_proof"]
            recovery_objects: tuple[object, ...] = ()
            if recovery_proof is not None:
                recovery = _require_object(
                    recovery_proof,
                    frozenset(("objects", "substate")),
                    "cleanup receipt recovery proof",
                )
                recovery_objects = _require_tuple(
                    recovery["objects"],
                    "cleanup receipt recovery proof objects",
                )
            terminal_replacement_proof = receipt[
                "terminal_replacement_proof"
            ]
            if terminal_replacement_proof is not None:
                assert recovery_proof is not None
                terminal_recovery = dict(recovery_proof)
                terminal_recovery["replacement"] = terminal_replacement_proof
                (
                    terminal_recovery,
                    validated_terminal_replacement,
                ) = self._validate_terminal_cleanup_replacement(
                    terminal_recovery
                )
                if (
                    terminal_recovery != recovery_proof
                    or validated_terminal_replacement
                    != terminal_replacement_proof
                ):
                    raise RollbackCorruptionError(
                        "cleanup receipt terminal replacement binding changed"
                    )
            allowed = {
                _CLEANUP_PREPARING_NAME,
                _CLEANUP_COMMITTED_NAME,
                _CLEANUP_RECEIPT_NAME,
                *tombstone_paths,
            }
            stage_names = set(names)
            if stage_names - allowed:
                raise RollbackCorruptionError(
                    "cleanup receipt staging inventory is invalid"
                )
            if (
                _CLEANUP_COMMITTED_NAME in stage_names
                and _CLEANUP_PREPARING_NAME not in stage_names
            ):
                raise RollbackCorruptionError(
                    "cleanup receipt committed authority has no preparing authority"
                )
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=stage_names,
            )
            preparing_payload: Mapping[str, object] | None = None
            preparing_raw: bytes | None = None
            if _CLEANUP_PREPARING_NAME in stage_names:
                preparing = self._validate_cleanup_authority_file(
                    directory_fd,
                    _CLEANUP_PREPARING_NAME,
                )
                try:
                    if canonical_digest(preparing.raw) != receipt["preparing_digest"]:
                        raise RollbackCorruptionError(
                            "cleanup receipt preparing binding is invalid"
                        )
                    preparing_payload = self._cleanup_preparing_payload(
                        preparing.raw,
                        directory_fd,
                    )
                    preparing_raw = preparing.raw
                    if (
                        preparing_payload["stage_identity"] != stage_identity
                        or preparing_payload["transaction_id"]
                        != receipt["transaction_id"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt stage binding is invalid"
                        )
                finally:
                    preparing.close()
            if _CLEANUP_COMMITTED_NAME in stage_names:
                assert preparing_payload is not None and preparing_raw is not None
                committed = self._validate_cleanup_authority_file(
                    directory_fd,
                    _CLEANUP_COMMITTED_NAME,
                )
                try:
                    if canonical_digest(committed.raw) != receipt["committed_digest"]:
                        raise RollbackCorruptionError(
                            "cleanup receipt committed binding is invalid"
                        )
                    committed_payload = self._cleanup_committed_payload(
                        committed.raw,
                        preparing_raw,
                        preparing_payload,
                    )
                    if any(
                        item["state"] != "processed"
                        for item in committed_payload["candidate_states"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt progress is incomplete"
                        )
                    committed_recovery_proof = committed_payload[
                        "recovery_proof"
                    ]
                    if terminal_replacement_proof is not None:
                        if (
                            type(committed_recovery_proof) is not dict
                            or committed_recovery_proof.get("replacement")
                            != terminal_replacement_proof
                        ):
                            raise RollbackCorruptionError(
                                "cleanup receipt terminal replacement history "
                                "diverged"
                            )
                        committed_recovery_proof = dict(
                            committed_recovery_proof
                        )
                        del committed_recovery_proof["replacement"]
                    if (
                        list(committed_payload["tombstone_proofs"])
                        != list(tombstone_proofs)
                        or committed_recovery_proof != recovery_proof
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt terminal proof binding is invalid"
                        )
                finally:
                    committed.close()
            intents = tuple(
                name
                for name in candidate_names
                if name.endswith(".transaction-rollback")
            )
            ordinary_names = tuple(
                name
                for name in candidate_names
                if name not in intents
                and not name.endswith((".displaced-head", ".prior-candidate"))
            )
            if tuple(tombstone_candidates) != ordinary_names:
                raise RollbackCorruptionError(
                    "cleanup receipt ordinary candidate proof is incomplete"
                )
            for name in candidate_names:
                if self._path_exists_at(directory_fd, name) or self._path_exists_at(
                    self._root_fd,
                    name,
                ):
                    raise RollbackCorruptionError("cleanup receipt candidate survived")
            if intents and (
                len(intents) != 1
                or not self._committed_recovery_is_complete(intents[0])
            ):
                raise RollbackCorruptionError(
                    "cleanup receipt recovery proof is incomplete"
                )
            for proof_value in recovery_objects:
                proof = self._validate_cleanup_object_proof(
                    proof_value,
                    label="cleanup receipt recovery object proof",
                )
                location_fd = {
                    "root": self._root_fd,
                    "stage": directory_fd,
                    "terminal": self._terminal_fd,
                }[proof["location"]]
                recovery_object = _HeldStoreFile.capture(
                    self,
                    str(proof["path"]),
                    directory_fd=location_fd,
                )
                try:
                    if (
                        recovery_object.identity != tuple(proof["identity"])
                        or canonical_digest(recovery_object.raw)
                        != proof["raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt recovery object changed"
                        )
                    recovery_object.revalidate(self)
                finally:
                    recovery_object.close()
            for tombstone_index, value in enumerate(tombstone_proofs):
                proof = value["proof"]
                path = str(proof["path"])
                if not self._path_exists_at(directory_fd, path):
                    stage_names.discard(path)
                    continue
                tombstone = _HeldStoreFile.capture(
                    self,
                    path,
                    directory_fd=directory_fd,
                )
                try:
                    if (
                        tombstone.identity != tuple(proof["identity"])
                        or canonical_digest(tombstone.raw) != proof["raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup receipt tombstone changed"
                        )
                    prefix = f"receipt.remove.tombstone.{tombstone_index}"
                    self._remove_cleanup_authority(
                        directory_fd,
                        path,
                        prefix=prefix,
                        stage_identity=stage_identity,
                        expected_names=stage_names,
                        target=tombstone,
                        hook_name=str(tombstone_index),
                    )
                    stage_names.remove(path)
                    self._sync_cleanup_stage(directory_fd, prefix=prefix)
                finally:
                    tombstone.close()
            for name in (_CLEANUP_COMMITTED_NAME, _CLEANUP_PREPARING_NAME):
                if name not in stage_names:
                    continue
                prefix = f"receipt.remove.{name}"
                self._remove_cleanup_authority(
                    directory_fd,
                    name,
                    prefix=prefix,
                    stage_identity=stage_identity,
                    expected_names=stage_names,
                )
                stage_names.remove(name)
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
            self._remove_cleanup_authority(
                directory_fd,
                _CLEANUP_RECEIPT_NAME,
                prefix="receipt.remove.receipt",
                stage_identity=stage_identity,
                expected_names=stage_names,
                target=receipt_file,
            )
            stage_names.remove(_CLEANUP_RECEIPT_NAME)
            self._sync_cleanup_stage(
                directory_fd,
                prefix="receipt.remove.receipt",
            )
            self._remove_cleanup_stage(
                directory_fd,
                prefix="receipt.terminal",
                stage_identity=stage_identity,
            )
        finally:
            receipt_file.close()

    @staticmethod
    def _cleanup_candidate_matches(
        candidate: _HeldStoreFile,
        expected: Mapping[str, object],
    ) -> bool:
        return (
            candidate.identity == tuple(expected["identity"])
            and canonical_digest(candidate.raw) == expected["raw_sha256"]
        )

    @staticmethod
    def _cleanup_candidate_survives_rename(
        candidate: _HeldStoreFile,
        expected: Mapping[str, object],
    ) -> bool:
        identity = tuple(expected["identity"])
        return (
            candidate.identity[:7] == identity[:7]
            and canonical_digest(candidate.raw) == expected["raw_sha256"]
        )

    def _rollback_cleanup_staging(
        self,
        directory_fd: int,
        preparing: Mapping[str, object],
        *,
        authority_name: str = _CLEANUP_PREPARING_NAME,
        discard_names: tuple[str, ...] = (),
    ) -> None:
        candidates = tuple(preparing["candidates"])
        stage_identity = preparing["stage_identity"]
        held: list[_HeldStoreFile] = []
        try:
            held, locations = self._validate_preparing_candidate_positions(
                directory_fd,
                preparing,
            )
            staged_candidates = {
                name for name, location in locations.items() if location == directory_fd
            }
            stage_names = {
                *staged_candidates,
                authority_name,
                *discard_names,
            }
            self._rejoin_cleanup_stage(
                directory_fd,
                stage_identity,
                expected_names=stage_names,
            )
            for candidate in reversed(held):
                if locations[candidate.name] == self._root_fd:
                    continue
                prefix = f"rollback.move.{candidate.name}"
                candidate.revalidate(self)
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                self._cleanup_fault(f"{prefix}.before_move")
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                candidate.revalidate(self)
                _rename_noreplace_between(
                    candidate.name,
                    candidate.name,
                    directory_fd,
                    self._root_fd,
                )
                stage_names.remove(candidate.name)
                self._cleanup_fault(f"{prefix}.after_move")
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
                self._sync_cleanup_root(prefix=prefix)
                candidate.path_directory_fd = self._root_fd
                candidate.refresh_path_identity(self, candidate.name)
            if set(self._bounded_root_names()) != {
                *preparing["root_names"],
                self._cleanup_staging_name,
            }:
                raise RollbackCorruptionError(
                    "cleanup preparing rollback root inventory diverged"
                )
            for candidate, expected in zip(held, candidates, strict=True):
                candidate.revalidate(self)
                if not self._cleanup_candidate_survives_rename(candidate, expected):
                    raise RollbackCorruptionError(
                        "cleanup preparing rollback identity diverged"
                    )
            for name in (*discard_names, authority_name):
                prefix = f"rollback.remove.{name}"
                self._remove_cleanup_authority(
                    directory_fd,
                    name,
                    prefix=prefix,
                    stage_identity=stage_identity,
                    expected_names=stage_names,
                )
                stage_names.remove(name)
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
            self._remove_cleanup_stage(
                directory_fd,
                prefix="rollback.terminal",
                stage_identity=stage_identity,
            )
            if self._bounded_root_names() != tuple(preparing["root_names"]):
                raise RollbackCorruptionError(
                    "cleanup preparing rollback root inventory diverged"
                )
        except _CleanupInjectedCrash:
            raise
        except BaseException as error:
            raise RollbackCorruptionError(
                "abandoned cleanup staging rollback failed"
            ) from error
        finally:
            for candidate in held:
                candidate.close()

    def _committed_recovery_rollback_id(
        self,
        intent_name: str,
    ) -> str | None:
        match = re.fullmatch(
            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
            r"transaction-rollback",
            intent_name,
        )
        if match is None or self._domain != "rollback-journal":
            return None
        transaction_id = match.group(1)
        matching = tuple(
            artifacts
            for base, artifacts in self._rollback_quarantine_inventory().items()
            if base.split(".", 2)[1] == transaction_id
        )
        if len(matching) != 1 or set(matching[0]) != {"successor", "tombstone"}:
            return None
        tombstone = _HeldStoreFile.capture(self, matching[0]["tombstone"][0])
        try:
            payload = self._verify_signed(
                tombstone.raw,
                "publication-rollback-intent",
            )
            if (
                payload.get("transaction_id") != transaction_id
                or payload.get("state") != "quarantined"
            ):
                return None
            rollback_id = _require_id(payload.get("rollback_id"), "rollback id")
            tombstone.revalidate(self)
            return rollback_id
        finally:
            tombstone.close()

    def _committed_recovery_is_complete(self, intent_name: str) -> bool:
        match = re.fullmatch(
            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
            r"transaction-rollback",
            intent_name,
        )
        if match is None or self._domain != "rollback-journal":
            return False
        transaction_id = match.group(1)
        anchors = self._terminal_quarantine_anchors()
        refs = tuple(
            ref for ref in anchors.values() if ref.transaction_id == transaction_id
        )
        if len(refs) != 1:
            return False
        ref = refs[0]
        base = self._terminal_anchor_key(ref)
        inventory = self._rollback_quarantine_inventory()
        artifacts = inventory.get(base)
        if artifacts is None or set(inventory) - set(anchors):
            return False
        payload, successor_record, successor_raw = self._terminal_pair_evidence(
            ref,
            artifacts,
        )
        self._validate_live_terminal_anchor(
            ref,
            payload,
            successor_record,
            successor_raw,
            block_on_failure=False,
        )
        return True

    def _resume_committed_cleanup(
        self,
        directory_fd: int,
        preparing_raw: bytes,
        preparing: Mapping[str, object],
        committed_raw: bytes,
    ) -> None:
        candidates = tuple(preparing["candidates"])
        candidate_names = tuple(item["name"] for item in candidates)
        committed = self._cleanup_committed_payload(
            committed_raw,
            preparing_raw,
            preparing,
        )
        candidate_states = [
            {"name": item["name"], "state": item["state"]}
            for item in committed["candidate_states"]
        ]
        progress_generation = committed["progress_generation"]
        tombstone_proofs = [
            {
                "candidate_name": item["candidate_name"],
                "proof": dict(item["proof"]),
                "status": item["status"],
            }
            for item in committed["tombstone_proofs"]
        ]
        recovery_proof = (
            None
            if committed["recovery_proof"] is None
            else {
                "objects": [
                    dict(item) for item in committed["recovery_proof"]["objects"]
                ],
                "substate": committed["recovery_proof"]["substate"],
            }
        )
        if (
            recovery_proof is not None
            and "replacement" in committed["recovery_proof"]
        ):
            recovery_proof["replacement"] = dict(
                committed["recovery_proof"]["replacement"]
            )
            self._cleanup_recovery_replace_proof = dict(
                committed["recovery_proof"]["replacement"]
            )
        stage_identity = preparing["stage_identity"]
        pattern = re.compile(
            rf"^\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\."
            r"(?:immutable|rollback|tmp|transaction-rollback|"
            r"displaced-head|prior-candidate)$"
        )
        expected_by_name = {item["name"]: item for item in candidates}
        recovery_names = {
            name
            for name in candidate_names
            if name.endswith(
                (".transaction-rollback", ".displaced-head", ".prior-candidate")
            )
        }
        intents = tuple(
            name for name in candidate_names if name.endswith(".transaction-rollback")
        )
        if len(intents) > 1:
            raise RollbackCorruptionError(
                "committed cleanup has multiple recovery intents"
            )
        if recovery_names and not intents:
            raise RollbackCorruptionError(
                "committed cleanup recovery artifacts have no intent"
            )
        for name in candidate_names:
            if pattern.fullmatch(name) is None:
                raise RollbackCorruptionError(
                    "committed cleanup candidate name is invalid"
                )

        def close_held(held: Mapping[str, _HeldStoreFile]) -> None:
            for candidate in held.values():
                candidate.close()

        def capture_stage() -> tuple[dict[str, _HeldStoreFile], set[str]]:
            nonlocal committed_raw, recovery_proof
            held: dict[str, _HeldStoreFile] = {}
            stage_names = {
                _CLEANUP_PREPARING_NAME,
                _CLEANUP_COMMITTED_NAME,
            }
            actual_stage_names = set(
                self._bounded_cleanup_staging_names(directory_fd)
            )
            intent_temps = {
                name
                for name in actual_stage_names
                if name.startswith(".intent-replace-")
            }
            for intent_temp in intent_temps:
                if self._cleanup_replacement_temp_location(intent_temp) != "stage":
                    raise RollbackCorruptionError(
                        "recovery replacement temporary name is invalid"
                    )
            if len(intent_temps) > 1:
                raise RollbackCorruptionError(
                    "multiple recovery replacement temporaries survived"
                )
            replacement = (
                None
                if recovery_proof is None
                else recovery_proof.get("replacement")
            )
            if replacement is not None:
                replacement = self._validate_cleanup_replacement_proof(
                    replacement,
                    label="cleanup stage replacement proof",
                )
                if (
                    self._cleanup_replacement_temp_location(replacement["temp"])
                    == "stage"
                ):
                    replacement_temp = str(replacement["temp"])
                    replacement_state = str(replacement["state"])
                    if replacement_state == "post":
                        expected_temps: set[str] = set()
                    elif replacement_state == "preparing":
                        expected_temps = intent_temps & {replacement_temp}
                    else:
                        expected_temps = {replacement_temp}
                    if intent_temps != expected_temps:
                        raise RollbackCorruptionError(
                            "cleanup stage replacement temporary state changed"
                        )
                    if intent_temps:
                        replacement_file = _HeldStoreFile.capture(
                            self,
                            replacement_temp,
                            directory_fd=directory_fd,
                        )
                        try:
                            expected_payload = str(
                                replacement["expected_payload"]
                            ).encode("utf-8")
                            if replacement_state == "preparing":
                                if replacement_file.raw:
                                    raise RollbackCorruptionError(
                                        "unsigned stage replacement temporary changed"
                                    )
                            else:
                                signed_identity = tuple(replacement["identity"])
                                observed_digest = canonical_digest(
                                    replacement_file.raw
                                )
                                if (
                                    replacement_state == "created"
                                    and (
                                        replacement_file.identity
                                        != signed_identity
                                        or observed_digest
                                        != replacement["observed_digest"]
                                    )
                                ) or (
                                    replacement_state == "ready"
                                    and (
                                        replacement_file.identity
                                        != signed_identity
                                        or observed_digest
                                        != replacement["observed_digest"]
                                        or replacement_file.raw != expected_payload
                                    )
                                ):
                                    raise RollbackCorruptionError(
                                        "signed stage replacement temporary changed"
                                    )
                            replacement_file.revalidate(self)
                        finally:
                            replacement_file.close()
            elif intent_temps:
                raise RollbackCorruptionError(
                    "unsigned recovery replacement temporary survived"
                )
            stage_names.update(intent_temps)
            proof_by_candidate = {
                item["candidate_name"]: item for item in tombstone_proofs
            }

            def capture_proof(
                key: str,
                proof: Mapping[str, object],
            ) -> _HeldStoreFile:
                location = proof["location"]
                location_fd = {
                    "root": self._root_fd,
                    "stage": directory_fd,
                    "terminal": self._terminal_fd,
                }[location]
                path = str(proof["path"])
                try:
                    candidate = _HeldStoreFile.capture(
                        self,
                        path,
                        directory_fd=location_fd,
                    )
                except (OSError, RollbackCorruptionError) as error:
                    raise RollbackCorruptionError(
                        "committed cleanup proof object could not be held"
                    ) from error
                if (
                    candidate.identity != tuple(proof["identity"])
                    or canonical_digest(candidate.raw) != proof["raw_sha256"]
                ):
                    candidate.close()
                    raise RollbackCorruptionError(
                        "committed cleanup proof object identity changed"
                    )
                held[key] = candidate
                if location == "stage":
                    stage_names.add(path)
                return candidate

            try:
                recovery_stage_paths: set[str] = set()
                recovery_objects = (
                    []
                    if recovery_proof is None
                    else [dict(value) for value in recovery_proof["objects"]]
                )
                resolvable_move = (
                    None if recovery_proof is None else recovery_proof["substate"]
                )
                if resolvable_move in {
                    "successor_displacement.before_move",
                    "successor_quarantine.before_move",
                    "terminal_tombstone.before_move",
                    "prior_head.before_publish",
                    "cleanup_intent.before_publish",
                    "terminal_intent.before_publish",
                } and intents:
                    if resolvable_move == "terminal_tombstone.before_move":
                        transaction_match = re.fullmatch(
                            rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
                            r"transaction-rollback",
                            intents[0],
                        )
                        if transaction_match is None:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone source is invalid"
                            )
                        transaction_id = transaction_match.group(1)
                        planned_transaction_id = transaction_id
                        successor_proof = next(
                            (
                                value
                                for value in recovery_objects
                                if value["location"] == "terminal"
                                and f".{transaction_id}." in str(value["path"])
                                and str(value["path"]).endswith(".successor")
                            ),
                            None,
                        )
                        if successor_proof is None:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone destination is unbound"
                            )
                        successor_path = str(successor_proof["path"])
                        signed_terminal_destination = (
                            f"{successor_path[:-len('.successor')]}.tombstone"
                        )
                        source_exists = self._path_exists_at(
                            directory_fd,
                            intents[0],
                        )
                        destination_exists = self._path_exists_at(
                            self._terminal_fd,
                            signed_terminal_destination,
                        )
                        if source_exists == destination_exists:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone transition is ambiguous"
                            )
                        intent_authority = _HeldStoreFile.capture(
                            self,
                            intents[0] if source_exists else signed_terminal_destination,
                            directory_fd=(
                                directory_fd if source_exists else self._terminal_fd
                            ),
                        )
                    else:
                        intent_location_fd = (
                            directory_fd
                            if self._path_exists_at(directory_fd, intents[0])
                            else self._root_fd
                        )
                        intent_authority = _HeldStoreFile.capture(
                            self,
                            intents[0],
                            directory_fd=intent_location_fd,
                        )
                    try:
                        intent_payload = self._verify_signed(
                            intent_authority.raw,
                            "publication-rollback-intent",
                        )
                        rollback_id = str(intent_payload["rollback_id"])
                        transaction_id = str(intent_payload["transaction_id"])
                        if (
                            resolvable_move == "terminal_tombstone.before_move"
                            and transaction_id != planned_transaction_id
                        ):
                            raise RollbackCorruptionError(
                                "planned terminal tombstone transaction changed"
                            )
                    finally:
                        intent_authority.close()
                    source_location = "root"
                    destination_location = "root"
                    if resolvable_move in {
                        "cleanup_intent.before_publish",
                        "terminal_intent.before_publish",
                    }:
                        source_location = "stage"
                        destination_location = "stage"
                        ready_proofs = [
                            value
                            for value in recovery_objects
                            if value["location"] == "stage"
                            and str(value["path"]).startswith(".intent-replace-")
                        ]
                        if len(ready_proofs) != 1:
                            raise RollbackCorruptionError(
                                "recovery replacement READY proof is invalid"
                            )
                        source_path = str(ready_proofs[0]["path"])
                        destination_path = intents[0]
                    elif resolvable_move == "successor_displacement.before_move":
                        source_path = f"journal.{rollback_id}.head"
                        destination_path = (
                            f".{self._domain}.{transaction_id}.displaced-head"
                        )
                    elif resolvable_move == "successor_quarantine.before_move":
                        source_path = (
                            f".{self._domain}.{transaction_id}.displaced-head"
                        )
                        destination_path, _ = self._rollback_quarantine_names(
                            transaction_id,
                            rollback_id,
                            str(intent_payload["successor_record_digest"]),
                        )
                        destination_location = "terminal"
                    elif resolvable_move == "terminal_tombstone.before_move":
                        source_path = intents[0]
                        source_location = "stage"
                        _, destination_path = self._rollback_quarantine_names(
                            transaction_id,
                            rollback_id,
                            str(intent_payload["successor_record_digest"]),
                        )
                        if destination_path != signed_terminal_destination:
                            raise RollbackCorruptionError(
                                "planned terminal tombstone name changed"
                            )
                        destination_location = "terminal"
                    else:
                        source_path = (
                            f".{self._domain}.{transaction_id}.prior-candidate"
                        )
                        destination_path = f"journal.{rollback_id}.head"
                    location_fds = {
                        "root": self._root_fd,
                        "stage": directory_fd,
                        "terminal": self._terminal_fd,
                    }
                    source_proof = next(
                        (
                            value
                            for value in recovery_objects
                            if value["location"] == source_location
                            and value["path"] == source_path
                        ),
                        None,
                    )
                    if (
                        source_proof is not None
                        and not self._path_exists_at(
                            location_fds[source_location],
                            source_path,
                        )
                        and self._path_exists_at(
                            location_fds[destination_location],
                            destination_path,
                        )
                    ):
                        installed = _HeldStoreFile.capture(
                            self,
                            destination_path,
                            directory_fd=location_fds[destination_location],
                        )
                        try:
                            if resolvable_move in {
                                "cleanup_intent.before_publish",
                                "terminal_intent.before_publish",
                            }:
                                installed_payload = self._verify_signed(
                                    installed.raw,
                                    "publication-rollback-intent",
                                )
                                expected_state = (
                                    "cleanup_pending"
                                    if resolvable_move
                                    == "cleanup_intent.before_publish"
                                    else "quarantined"
                                )
                                if installed_payload["state"] != expected_state:
                                    raise RollbackCorruptionError(
                                        "recovery replacement destination "
                                        "state is invalid"
                                    )
                            if (
                                installed.identity[:7]
                                != tuple(source_proof["identity"])[:7]
                                or canonical_digest(installed.raw)
                                != source_proof["raw_sha256"]
                            ):
                                raise RollbackCorruptionError(
                                    "planned recovery move transition is invalid"
                                )
                            replacement_proof = self._cleanup_object_proof(
                                installed,
                                location=destination_location,
                                path=destination_path,
                            )
                        finally:
                            installed.close()
                        recovery_objects.remove(source_proof)
                        recovery_objects = [
                            value
                            for value in recovery_objects
                            if not (
                                value["location"] == destination_location
                                and value["path"] == destination_path
                            )
                        ]
                        recovery_objects.append(replacement_proof)
                        recovery_objects.sort(
                            key=lambda item: (item["location"], item["path"])
                        )
                        boundary = f"{resolvable_move}.after_resolved"
                        next_proof = {
                            "objects": recovery_objects,
                            "substate": boundary,
                        }
                        committed_raw = self._persist_cleanup_recovery_checkpoint(
                            directory_fd,
                            preparing_raw,
                            preparing,
                            committed_raw,
                            candidate_states,
                            progress_generation,
                            tombstone_proofs=tombstone_proofs,
                            recovery_proof=next_proof,
                            expected_names=set(
                                self._bounded_cleanup_staging_names(directory_fd)
                            ),
                            boundary=boundary,
                        )
                        recovery_proof = next_proof
                if (
                    recovery_proof is not None
                    and "replacement" in recovery_proof
                    and recovery_proof["replacement"]["state"] == "ready"
                ):
                    replacement = recovery_proof["replacement"]
                    replacement_temp = str(replacement["temp"])
                    replacement_destination = str(replacement["destination"])
                    replacement_location = self._cleanup_replacement_temp_location(
                        replacement_temp
                    )
                    replacement_fd = {
                        "root": self._root_fd,
                        "stage": directory_fd,
                    }[replacement_location]
                    temp_exists = self._path_exists_at(
                        replacement_fd,
                        replacement_temp,
                    )
                    if not temp_exists:
                        if not self._path_exists_at(
                            replacement_fd,
                            replacement_destination,
                        ):
                            raise RollbackCorruptionError(
                                "ready recovery replacement disappeared"
                            )
                        installed = _HeldStoreFile.capture(
                            self,
                            replacement_destination,
                            directory_fd=replacement_fd,
                        )
                        try:
                            if (
                                installed.identity[:7]
                                != tuple(replacement["identity"])[:7]
                                or canonical_digest(installed.raw)
                                != replacement["expected_digest"]
                            ):
                                raise RollbackCorruptionError(
                                    "ready recovery replacement poststate changed"
                                )
                            installed.revalidate(self)
                            installed_proof = self._cleanup_object_proof(
                                installed,
                                location=replacement_location,
                                path=replacement_destination,
                            )
                        finally:
                            installed.close()
                        recovery_objects = [
                            value
                            for value in recovery_proof["objects"]
                            if not (
                                value["location"] == replacement_location
                                and value["path"]
                                in {
                                    replacement_temp,
                                    replacement_destination,
                                }
                            )
                        ]
                        recovery_objects.append(installed_proof)
                        recovery_objects.sort(
                            key=lambda item: (item["location"], item["path"])
                        )
                        boundary = (
                            f"{recovery_proof['substate']}.replacement_post"
                        )
                        next_replacement = {
                            **replacement,
                            "identity": list(installed_proof["identity"]),
                            "observed_digest": installed_proof["raw_sha256"],
                            "state": "post",
                        }
                        next_proof = {
                            "objects": recovery_objects,
                            "replacement": next_replacement,
                            "substate": boundary,
                        }
                        committed_raw = self._persist_cleanup_recovery_checkpoint(
                            directory_fd,
                            preparing_raw,
                            preparing,
                            committed_raw,
                            candidate_states,
                            progress_generation,
                            tombstone_proofs=tombstone_proofs,
                            recovery_proof=next_proof,
                            expected_names=set(
                                self._bounded_cleanup_staging_names(directory_fd)
                            ),
                            boundary=boundary,
                        )
                        recovery_proof = next_proof
                        self._cleanup_recovery_replace_proof = dict(
                            next_replacement
                        )
                if recovery_proof is not None:
                    recovery_objects = [
                        dict(value) for value in recovery_proof["objects"]
                    ]
                for proof in recovery_objects:
                    key = f"proof:{proof['location']}:{proof['path']}"
                    capture_proof(key, proof)
                    if proof["location"] == "stage":
                        recovery_stage_paths.add(str(proof["path"]))
                if recovery_proof is not None and "replacement" in recovery_proof:
                    completed_replacement = recovery_proof["replacement"]
                    if (
                        completed_replacement["state"] == "post"
                        and self._cleanup_replacement_temp_location(
                            completed_replacement["temp"]
                        )
                        == "stage"
                    ):
                        self._cleanup_recovery_replace_proof = None
                        self._cleanup_recovery_replace_temp = None
                        self._cleanup_recovery_replace_destination = None
                for name in candidate_names:
                    state = next(
                        item["state"] for item in candidate_states if item["name"] == name
                    )
                    if name in recovery_names and recovery_proof is not None:
                        staged = self._path_exists_at(directory_fd, name)
                        if staged != (name in recovery_stage_paths):
                            raise RollbackCorruptionError(
                                "committed cleanup recovery path proof changed"
                            )
                        proof_key = f"proof:stage:{name}"
                        if proof_key in held:
                            held[name] = held.pop(proof_key)
                        elif state != "processed" and name == intents[0]:
                            terminal_intent = any(
                                proof["location"] == "terminal"
                                and str(proof["path"]).endswith(".tombstone")
                                for proof in recovery_proof["objects"]
                            )
                            if not terminal_intent:
                                raise RollbackCorruptionError(
                                    "unfinished cleanup recovery intent disappeared"
                                )
                        continue
                    proof_item = proof_by_candidate.get(name)
                    if state == "processed":
                        if proof_item is None or proof_item["status"] != "processed":
                            raise RollbackCorruptionError(
                                "processed cleanup candidate has no tombstone proof"
                            )
                        capture_proof(name, proof_item["proof"])
                        if self._path_exists_at(directory_fd, name):
                            raise RollbackCorruptionError(
                                "processed cleanup candidate was replaced"
                            )
                        continue
                    if proof_item is not None:
                        if state != "processing" or proof_item["status"] != "moving":
                            raise RollbackCorruptionError(
                                "unfinished cleanup tombstone proof is invalid"
                            )
                        proof = proof_item["proof"]
                        source_exists = self._path_exists_at(directory_fd, name)
                        tombstone_exists = self._path_exists_at(
                            directory_fd,
                            str(proof["path"]),
                        )
                        if source_exists == tombstone_exists:
                            raise RollbackCorruptionError(
                                "cleanup tombstone move location is ambiguous"
                            )
                        if source_exists:
                            stage_names.add(name)
                            candidate = _HeldStoreFile.capture(
                                self,
                                name,
                                directory_fd=directory_fd,
                            )
                            held[name] = candidate
                            if not self._cleanup_candidate_matches(
                                candidate,
                                expected_by_name[name],
                            ):
                                raise RollbackCorruptionError(
                                    "cleanup tombstone source identity changed"
                                )
                        else:
                            path = str(proof["path"])
                            stage_names.add(path)
                            candidate = _HeldStoreFile.capture(
                                self,
                                path,
                                directory_fd=directory_fd,
                            )
                            held[name] = candidate
                            if (
                                candidate.identity[:7]
                                != tuple(proof["identity"])[:7]
                                or canonical_digest(candidate.raw)
                                != proof["raw_sha256"]
                            ):
                                raise RollbackCorruptionError(
                                    "cleanup tombstone transition identity changed"
                                )
                        continue
                    if self._path_exists_at(self._root_fd, name):
                        raise RollbackCorruptionError(
                            "committed cleanup candidate replayed"
                        )
                    if not self._path_exists_at(directory_fd, name):
                        raise RollbackCorruptionError(
                            "unfinished cleanup candidate disappeared"
                        )
                    stage_names.add(name)
                    try:
                        candidate = _HeldStoreFile.capture(
                            self,
                            name,
                            directory_fd=directory_fd,
                        )
                    except OSError as error:
                        raise RollbackCorruptionError(
                            "committed cleanup stage candidate could not be held: "
                            f"name={name!r}, errno={error.errno!r}"
                        ) from error
                    held[name] = candidate
                    if not self._cleanup_candidate_matches(
                        candidate,
                        expected_by_name[name],
                    ):
                        raise RollbackCorruptionError(
                            "committed cleanup candidate identity changed: "
                            f"{name!r}, state={state!r}, "
                            f"expected={tuple(expected_by_name[name]['identity'])!r}, "
                            f"actual={candidate.identity!r}"
                        )
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                for candidate in held.values():
                    candidate.revalidate(self)
                return held, stage_names
            except BaseException:
                close_held(held)
                raise

        def validate_root(
            intent_capsule: _RollbackRecoveryCapsule | None,
        ) -> None:
            actual_root = set(self._bounded_root_names())
            baseline_root = set(preparing["root_names"]) - set(candidate_names) | {
                self._cleanup_staging_name
            }
            additions = actual_root - baseline_root
            permitted_additions = {
                name
                for name in additions
                if (
                    name == _ROLLBACK_TERMINAL_ANCHOR_INDEX
                    or name.startswith(".terminal-anchor-pending.")
                )
            }
            rollback_id: str | None = None
            if intent_capsule is not None:
                rollback_id = _journal_from_object(
                    self._verify_signed(
                        intent_capsule.predecessor.raw,
                        "journal-record",
                    )
                ).rollback_id
                permitted_additions.update(
                    name
                    for name in additions
                    if name.startswith(f"journal.{rollback_id}.")
                    and name.endswith((".history", ".commit"))
                )
                permitted_additions.update(
                    additions
                    & {
                        intent_capsule.displaced_name,
                        intent_capsule.candidate_name,
                    }
                )
            elif intents:
                rollback_id = self._committed_recovery_rollback_id(intents[0])
                if rollback_id is not None:
                    permitted_additions.update(
                        name
                        for name in additions
                        if name.startswith(f"journal.{rollback_id}.")
                        and name.endswith((".history", ".commit"))
                    )
                transaction_match = re.fullmatch(
                    rf"\.{re.escape(self._domain)}\.([0-9a-f]{{32}})\."
                    r"transaction-rollback",
                    intents[0],
                )
                assert transaction_match is not None
                recovery_temp = f".{self._domain}.{transaction_match.group(1)}.tmp"
                if recovery_temp in additions:
                    permitted_additions.add(recovery_temp)
            if recovery_proof is not None:
                permitted_additions.update(
                    str(value["path"])
                    for value in recovery_proof["objects"]
                    if value["location"] == "root"
                    and str(value["path"]) in additions
                )
                replacement = recovery_proof.get("replacement")
                if replacement is not None:
                    replacement = self._validate_cleanup_replacement_proof(
                        replacement,
                        label="cleanup committed replacement proof",
                    )
                    replacement_temp = str(replacement["temp"])
                    replacement_location = self._cleanup_replacement_temp_location(
                        replacement_temp
                    )
                    replacement_state = str(replacement["state"])
                    if replacement_location == "root" and replacement_state == "preparing":
                        if replacement_temp in actual_root:
                            raise RollbackCorruptionError(
                                "uncreated recovery replacement temporary appeared"
                            )
                    elif replacement_location == "root" and replacement_state == "post":
                        if replacement_temp in actual_root:
                            raise RollbackCorruptionError(
                                "post recovery replacement temporary survived"
                            )
                        replacement_file = _HeldStoreFile.capture(
                            self,
                            str(replacement["destination"]),
                        )
                        try:
                            if (
                                replacement_file.identity
                                != tuple(replacement["identity"])
                                or canonical_digest(replacement_file.raw)
                                != replacement["observed_digest"]
                            ):
                                raise RollbackCorruptionError(
                                    "post recovery replacement destination changed"
                                )
                            replacement_file.revalidate(self)
                        finally:
                            replacement_file.close()
                    elif replacement_location == "root":
                        if replacement_temp not in additions:
                            raise RollbackCorruptionError(
                                "signed recovery replacement temporary disappeared"
                            )
                        replacement_file = _HeldStoreFile.capture(
                            self,
                            replacement_temp,
                        )
                        try:
                            signed_identity = tuple(replacement["identity"])
                            expected_payload = str(
                                replacement["expected_payload"]
                            ).encode("utf-8")
                            if (
                                replacement_file.identity[:6]
                                != signed_identity[:6]
                                or (
                                    replacement_state == "created"
                                    and not expected_payload.startswith(
                                        replacement_file.raw
                                    )
                                )
                                or (
                                    replacement_state == "ready"
                                    and (
                                        replacement_file.identity
                                        != signed_identity
                                        or canonical_digest(replacement_file.raw)
                                        != replacement["observed_digest"]
                                    )
                                )
                            ):
                                raise RollbackCorruptionError(
                                    "signed recovery replacement temporary changed"
                                )
                            replacement_file.revalidate(self)
                        finally:
                            replacement_file.close()
                        permitted_additions.add(replacement_temp)
            missing_root = baseline_root - actual_root
            permitted_missing = (
                {intent_capsule.head_name}
                if intent_capsule is not None
                and intent_capsule.head_name in missing_root
                and intent_capsule.displaced_name in additions
                else set()
            )
            if additions != permitted_additions or missing_root != permitted_missing:
                raise RollbackCorruptionError(
                    "committed cleanup root inventory is invalid: "
                    f"additions={sorted(additions)!r}, "
                    f"permitted={sorted(permitted_additions)!r}, "
                    f"missing={sorted(missing_root)!r}, "
                    f"permitted_missing={sorted(permitted_missing)!r}"
                )

        def capture_recovery_checkpoint(
            capsule: _RollbackRecoveryCapsule,
            boundary: str,
            *,
            persist_checkpoint: bool,
        ) -> None:
            nonlocal committed_raw, recovery_proof
            specifications = {
                *(
                    ("stage", name)
                    for name in {
                        *recovery_names,
                        capsule.displaced_name,
                        capsule.candidate_name,
                    }
                ),
                ("root", capsule.displaced_name),
                ("root", capsule.candidate_name),
                ("root", capsule.head_name),
                ("terminal", capsule.quarantine_name),
                ("terminal", capsule.tombstone_name),
            }
            replacement_temp = self._cleanup_recovery_replace_temp
            replacement_destination = self._cleanup_recovery_replace_destination
            replacement_location = (
                "root"
                if replacement_temp is None
                else self._cleanup_replacement_temp_location(replacement_temp)
            )
            replacement_poststate = (
                boundary.rsplit(".", 1)[-1] in {"after_replace", "after_publish"}
                and replacement_temp is not None
                and replacement_destination is not None
            )
            if (
                replacement_temp is not None
                and not replacement_poststate
                and self._cleanup_recovery_replace_proof is None
            ):
                specifications.add((replacement_location, replacement_temp))
            if recovery_proof is not None:
                specifications.update(
                    (str(item["location"]), str(item["path"]))
                    for item in recovery_proof["objects"]
                    if not (
                        item["location"] == replacement_location
                        and item["path"] == replacement_temp
                        and (
                            replacement_poststate
                            or self._cleanup_recovery_replace_proof is not None
                        )
                    )
                )
            if replacement_poststate:
                specifications.add(
                    (replacement_location, replacement_destination)
                )
            objects: list[dict[str, object]] = []
            captured: list[_HeldStoreFile] = []
            try:
                for location, path in sorted(specifications):
                    location_fd = {
                        "root": self._root_fd,
                        "stage": directory_fd,
                        "terminal": self._terminal_fd,
                    }[location]
                    if not self._path_exists_at(location_fd, path):
                        continue
                    candidate = _HeldStoreFile.capture(
                        self,
                        path,
                        directory_fd=location_fd,
                    )
                    captured.append(candidate)
                    candidate.revalidate(self)
                    objects.append(
                        self._cleanup_object_proof(
                            candidate,
                            location=location,
                            path=path,
                        )
                    )
                objects.sort(key=lambda item: (item["location"], item["path"]))
                if replacement_poststate:
                    assert replacement_temp is not None
                    assert replacement_destination is not None
                    previous_temp = next(
                        (
                            item
                            for item in (
                                () if recovery_proof is None else recovery_proof["objects"]
                            )
                            if item["location"] == replacement_location
                            and item["path"] == replacement_temp
                        ),
                        None,
                    )
                    if (
                        previous_temp is None
                        and self._cleanup_recovery_replace_proof is not None
                        and self._cleanup_recovery_replace_proof["state"] == "ready"
                    ):
                        previous_temp = {
                            "identity": self._cleanup_recovery_replace_proof[
                                "identity"
                            ],
                            "raw_sha256": self._cleanup_recovery_replace_proof[
                                "observed_digest"
                            ],
                        }
                    replacement_object = next(
                        (
                            item
                            for item in objects
                            if item["location"] == replacement_location
                            and item["path"] == replacement_destination
                        ),
                        None,
                    )
                    if (
                        previous_temp is None
                        or replacement_object is None
                        or replacement_object["identity"][:7]
                        != previous_temp["identity"][:7]
                        or replacement_object["raw_sha256"]
                        != previous_temp["raw_sha256"]
                    ):
                        raise RollbackCorruptionError(
                            "cleanup recovery replacement poststate is invalid"
                        )
                    if self._cleanup_recovery_replace_proof is not None:
                        self._cleanup_recovery_replace_proof = {
                            **self._cleanup_recovery_replace_proof,
                            "identity": list(replacement_object["identity"]),
                            "observed_digest": replacement_object["raw_sha256"],
                            "state": "post",
                        }
                current_objects = (
                    None if recovery_proof is None else recovery_proof["objects"]
                )
                if not persist_checkpoint:
                    if current_objects is not None and objects != current_objects:
                        raise RollbackCorruptionError(
                            "cleanup recovery objects changed after signed checkpoint"
                        )
                    validate_root(capsule)
                    return
                if not any(
                    item["path"] == capsule.intent.name
                    or item["path"] == capsule.tombstone_name
                    for item in objects
                ):
                    raise RollbackCorruptionError(
                        "cleanup recovery checkpoint has no intent authority"
                    )
                next_proof: dict[str, object] = {
                    "objects": objects,
                    "substate": boundary,
                }
                if self._cleanup_recovery_replace_proof is not None:
                    next_proof["replacement"] = dict(
                        self._cleanup_recovery_replace_proof
                    )
                stage_names = {
                    _CLEANUP_PREPARING_NAME,
                    _CLEANUP_COMMITTED_NAME,
                    *(
                        str(item["proof"]["path"])
                        for item in tombstone_proofs
                        if item["proof"]["location"] == "stage"
                    ),
                    *(
                        item["name"]
                        for item in candidate_states
                        if item["name"] not in recovery_names
                        and item["state"] != "processed"
                    ),
                    *(
                        str(item["path"])
                        for item in objects
                        if item["location"] == "stage"
                    ),
                    *(
                        (replacement_temp,)
                        if replacement_temp is not None
                        and replacement_location == "stage"
                        and not replacement_poststate
                        and self._path_exists_at(directory_fd, replacement_temp)
                        else ()
                    ),
                }
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                committed_raw = self._persist_cleanup_recovery_checkpoint(
                    directory_fd,
                    preparing_raw,
                    preparing,
                    committed_raw,
                    candidate_states,
                    progress_generation,
                    tombstone_proofs=tombstone_proofs,
                    recovery_proof=next_proof,
                    expected_names=stage_names,
                    boundary=boundary,
                )
                recovery_proof = next_proof
            finally:
                for candidate in captured:
                    candidate.close()

        def preflight_exact_intent(
            held: Mapping[str, _HeldStoreFile],
        ) -> _RollbackRecoveryCapsule | None:
            if not intents or intents[0] not in held:
                return None
            intent = held[intents[0]]
            intent_state = next(
                item["state"] for item in candidate_states if item["name"] == intents[0]
            )
            if intent_state == "pending" and not self._cleanup_candidate_matches(
                intent,
                expected_by_name[intents[0]],
            ):
                raise RollbackCorruptionError(
                    "committed cleanup recovery intent identity changed"
                )
            if intent_state == "processing" and self._committed_recovery_is_complete(
                intents[0]
            ):
                raise RollbackCorruptionError(
                    "completed cleanup recovery intent was replaced"
                )
            capsule = self._preflight_transaction_rollback_intent(
                intents[0],
                recovery_directory_fd=directory_fd,
            )
            if (
                capsule.intent.identity != intent.identity
                or capsule.intent.raw != intent.raw
            ):
                capsule.close()
                raise RollbackCorruptionError(
                    "committed cleanup recovery intent was not rejoined"
                )
            intent.revalidate(self)
            capsule.intent.revalidate(self)
            return capsule

        def persist(
            stage_names: set[str],
        ) -> None:
            nonlocal committed_raw, progress_generation
            committed_raw, progress_generation = self._persist_cleanup_progress(
                directory_fd,
                preparing_raw,
                preparing,
                committed_raw,
                candidate_states,
                progress_generation,
                tombstone_proofs,
                recovery_proof,
                expected_names=stage_names,
            )


        recovery_states = {
            item["state"] for item in candidate_states if item["name"] in recovery_names
        }
        if len(recovery_states) > 1:
            raise RollbackCorruptionError("cleanup recovery progress is not atomic")
        if recovery_names:
            recovery_state = next(iter(recovery_states))
            held, stage_names = capture_stage()
            intent_capsule: _RollbackRecoveryCapsule | None = None
            try:
                intent_capsule = preflight_exact_intent(held)
                validate_root(intent_capsule)
                if recovery_state == "pending":
                    for item in candidate_states:
                        if item["name"] in recovery_names:
                            item["state"] = "processing"
                    persist(stage_names)
                    recovery_state = "processing"
            finally:
                if intent_capsule is not None:
                    intent_capsule.close()
                close_held(held)

            if recovery_state == "processing":
                held, stage_names = capture_stage()
                intent_capsule = None
                try:
                    intent_capsule = preflight_exact_intent(held)
                    validate_root(intent_capsule)
                    self._cleanup_forward_active = True
                    self._cleanup_recovery_checkpoint = (
                        None
                        if intent_capsule is None
                        else lambda boundary, should_persist: (
                            capture_recovery_checkpoint(
                                intent_capsule,
                                boundary,
                                persist_checkpoint=should_persist,
                            )
                        )
                    )
                    try:
                        if intent_capsule is not None:
                            self._cleanup_fault(
                                "forward.recovery.before."
                                f"{intent_capsule.transaction_id}"
                            )
                            validate_root(intent_capsule)
                            self._rejoin_cleanup_stage(
                                directory_fd,
                                stage_identity,
                                expected_names=stage_names,
                            )
                            held[intents[0]].revalidate(self)
                            intent_capsule.intent.revalidate(self)
                            self._recover_transaction_rollback(intent_capsule)
                            self._cleanup_fault(
                                "forward.recovery.after."
                                f"{intent_capsule.transaction_id}"
                            )
                        elif not self._committed_recovery_is_complete(intents[0]):
                            self._cleanup_pending_checkpoint_factory = (
                                lambda capsule: (
                                    lambda boundary, should_persist: (
                                        capture_recovery_checkpoint(
                                            capsule,
                                            boundary,
                                            persist_checkpoint=should_persist,
                                        )
                                    )
                                )
                            )
                            try:
                                self._cleanup_fault(
                                    f"forward.pending_recovery.before.{intents[0]}"
                                )
                                self._recover_pending_terminal_restorations()
                                self._cleanup_fault(
                                    f"forward.pending_recovery.after.{intents[0]}"
                                )
                            finally:
                                self._cleanup_pending_checkpoint_factory = None
                    finally:
                        self._cleanup_recovery_checkpoint = None
                        self._cleanup_forward_active = False
                finally:
                    if intent_capsule is not None:
                        intent_capsule.close()
                    close_held(held)
                held, stage_names = capture_stage()
                try:
                    if any(name in held for name in recovery_names):
                        raise RollbackCorruptionError(
                            "cleanup recovery candidate replacement survived"
                        )
                    if not self._committed_recovery_is_complete(intents[0]):
                        raise RollbackCorruptionError(
                            "committed cleanup recovery evidence is incomplete"
                        )
                    validate_root(None)
                    for item in candidate_states:
                        if item["name"] in recovery_names:
                            item["state"] = "processed"
                    persist(stage_names)
                finally:
                    close_held(held)
            else:
                if recovery_state != "processed":
                    raise RollbackCorruptionError(
                        "cleanup recovery progress is invalid"
                    )
                held, stage_names = capture_stage()
                try:
                    if any(name in held for name in recovery_names):
                        raise RollbackCorruptionError(
                            "processed cleanup recovery candidate survived"
                        )
                    if not self._committed_recovery_is_complete(intents[0]):
                        raise RollbackCorruptionError(
                            "committed cleanup recovery evidence is incomplete"
                        )
                    validate_root(None)
                finally:
                    close_held(held)

        for state_item in candidate_states:
            name = state_item["name"]
            if name in recovery_names:
                continue
            held, stage_names = capture_stage()
            try:
                validate_root(None)
                if state_item["state"] == "pending":
                    state_item["state"] = "processing"
                    persist(stage_names)
            finally:
                close_held(held)
            if state_item["state"] == "processing":
                tombstone_index = candidate_names.index(name)
                tombstone_name = (
                    f".{self._domain}.{preparing['transaction_id']}."
                    f"{tombstone_index:04x}.cleanup-tombstone"
                )
                proof_item = next(
                    (
                        item
                        for item in tombstone_proofs
                        if item["candidate_name"] == name
                    ),
                    None,
                )
                if proof_item is None:
                    held, stage_names = capture_stage()
                    try:
                        validate_root(None)
                        candidate = held[name]
                        candidate.revalidate(self)
                        if not self._cleanup_candidate_matches(
                            candidate,
                            expected_by_name[name],
                        ):
                            raise RollbackCorruptionError(
                                "ordinary cleanup candidate identity changed"
                            )
                        proof_item = {
                            "candidate_name": name,
                            "proof": self._cleanup_object_proof(
                                candidate,
                                location="stage",
                                path=tombstone_name,
                            ),
                            "status": "moving",
                        }
                        tombstone_proofs.append(proof_item)
                        tombstone_proofs.sort(
                            key=lambda item: str(item["candidate_name"])
                        )
                        committed_raw = self._persist_cleanup_recovery_checkpoint(
                            directory_fd,
                            preparing_raw,
                            preparing,
                            committed_raw,
                            candidate_states,
                            progress_generation,
                            tombstone_proofs=tombstone_proofs,
                            recovery_proof=recovery_proof,
                            expected_names=stage_names,
                            boundary=f"tombstone_plan.{tombstone_index}",
                        )
                    finally:
                        close_held(held)
                held, stage_names = capture_stage()
                try:
                    validate_root(None)
                    candidate = held[name]
                    if candidate.name == name:
                        prefix = f"forward.tombstone.{name}"
                        self._cleanup_fault(f"{prefix}.before_move")
                        self._rejoin_cleanup_stage(
                            directory_fd,
                            stage_identity,
                            expected_names=stage_names,
                        )
                        candidate.revalidate(self)
                        _rename_noreplace_between(
                            name,
                            tombstone_name,
                            directory_fd,
                            directory_fd,
                        )
                        stage_names.remove(name)
                        stage_names.add(tombstone_name)
                        candidate.name = tombstone_name
                        candidate.refresh_path_identity(self, tombstone_name)
                    else:
                        prefix = f"forward.tombstone.{name}"
                    self._cleanup_fault(f"{prefix}.before_stage_fsync")
                    self._rejoin_cleanup_stage(
                        directory_fd,
                        stage_identity,
                        expected_names=stage_names,
                    )
                    candidate.revalidate(self)
                    os.fsync(directory_fd)
                    candidate.refresh_path_identity(self, tombstone_name)
                    assert proof_item is not None
                    proof_item["proof"] = self._cleanup_object_proof(
                        candidate,
                        location="stage",
                        path=tombstone_name,
                    )
                    proof_item["status"] = "processed"
                    state_item["state"] = "processed"
                    persist(stage_names)
                    self._cleanup_fault(f"{prefix}.after_stage_fsync")
                    self._cleanup_fault(f"{prefix}.after_move")
                finally:
                    close_held(held)
            elif state_item["state"] != "processed":
                raise RollbackCorruptionError("ordinary cleanup progress is invalid")

        held, stage_names = capture_stage()
        try:
            validate_root(None)
            if any(item["state"] != "processed" for item in candidate_states):
                raise RollbackCorruptionError(
                    "cleanup completion inventory is incomplete"
                )
            if intents and not self._committed_recovery_is_complete(intents[0]):
                raise RollbackCorruptionError(
                    "cleanup completion recovery proof is incomplete"
                )
            terminal_replacement_proof = None
            if recovery_proof is not None:
                (
                    recovery_proof,
                    terminal_replacement_proof,
                ) = self._validate_terminal_cleanup_replacement(
                    recovery_proof
                )
            expected_receipt_raw = self._cleanup_receipt_bytes(
                preparing_raw,
                preparing,
                committed_raw,
                candidate_names,
                tombstone_proofs,
                recovery_proof,
                terminal_replacement_proof,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_RECEIPT_NAME,
                expected_receipt_raw,
                stage_identity=stage_identity,
                expected_names=stage_names,
                boundary_prefix="authority.receipt",
            )
        finally:
            close_held(held)
        self._resume_cleanup_receipt(
            directory_fd,
            self._bounded_cleanup_staging_names(directory_fd),
        )
        if self._path_exists_at(self._root_fd, self._cleanup_staging_name):
            raise RollbackCorruptionError("cleanup staging directory survived removal")

    def _resume_cleanup_staging(self) -> bool:
        opened = self._open_cleanup_staging(create=False)
        if opened is None:
            return False
        directory_fd, _ = opened
        try:
            stage_identity: Sequence[int] = self._cleanup_stage_identity_now(
                directory_fd
            )
            names = self._bounded_cleanup_staging_names(directory_fd)
            if not names:
                self._remove_cleanup_stage(
                    directory_fd,
                    prefix="resume.empty",
                    stage_identity=stage_identity,
                )
                return True
            temp_names = set(names) & {
                _CLEANUP_PREPARING_TEMP_NAME,
                _CLEANUP_COMMITTED_TEMP_NAME,
                _CLEANUP_RECEIPT_TEMP_NAME,
            }
            if len(temp_names) > 1:
                raise RollbackCorruptionError(
                    "cleanup staging has multiple temporary authorities"
                )
            if _CLEANUP_RECEIPT_NAME in names:
                if temp_names:
                    raise RollbackCorruptionError(
                        "cleanup receipt has an unsafe temporary authority"
                    )
                self._resume_cleanup_receipt(directory_fd, names)
                self._cleanup_resumed_forward = True
                return True
            if _CLEANUP_PREPARING_NAME not in names:
                if names != (_CLEANUP_PREPARING_TEMP_NAME,):
                    raise RollbackCorruptionError(
                        "cleanup staging has no preparing authority"
                    )
                self._dispose_cleanup_temp(
                    directory_fd,
                    _CLEANUP_PREPARING_TEMP_NAME,
                    stage_identity,
                    expected_names={_CLEANUP_PREPARING_TEMP_NAME},
                    prefix="resume.dispose.preparing",
                )
                stage_identity = self._cleanup_stage_identity_now(directory_fd)
                self._remove_cleanup_stage(
                    directory_fd,
                    prefix="resume.empty",
                    stage_identity=stage_identity,
                )
                return True
            preparing_file = self._validate_cleanup_authority_file(
                directory_fd,
                _CLEANUP_PREPARING_NAME,
            )
            try:
                preparing = self._cleanup_preparing_payload(
                    preparing_file.raw,
                    directory_fd,
                )
                stage_identity = preparing["stage_identity"]
                if _CLEANUP_PREPARING_TEMP_NAME in names:
                    if (
                        _CLEANUP_COMMITTED_NAME in names
                        or _CLEANUP_RECEIPT_TEMP_NAME in names
                    ):
                        raise RollbackCorruptionError(
                            "cleanup preparing temporary authority is unsafe"
                        )
                    self._dispose_cleanup_temp(
                        directory_fd,
                        _CLEANUP_PREPARING_TEMP_NAME,
                        stage_identity,
                        expected_names=set(names),
                        prefix="resume.dispose.preparing",
                    )
                    names = self._bounded_cleanup_staging_names(directory_fd)
                if _CLEANUP_COMMITTED_NAME not in names:
                    if _CLEANUP_RECEIPT_TEMP_NAME in names:
                        raise RollbackCorruptionError(
                            "cleanup receipt temporary authority has no commit"
                        )
                    discard_names: tuple[str, ...] = ()
                    if _CLEANUP_COMMITTED_TEMP_NAME in names:
                        self._dispose_cleanup_temp(
                            directory_fd,
                            _CLEANUP_COMMITTED_TEMP_NAME,
                            stage_identity,
                            expected_names=set(names),
                            prefix="resume.dispose.committed",
                        )
                        names = self._bounded_cleanup_staging_names(directory_fd)
                    self._rollback_cleanup_staging(
                        directory_fd,
                        preparing,
                        discard_names=discard_names,
                    )
                    return True
                committed_file = self._validate_cleanup_authority_file(
                    directory_fd,
                    _CLEANUP_COMMITTED_NAME,
                )
                try:
                    committed_payload = self._cleanup_committed_payload(
                        committed_file.raw,
                        preparing_file.raw,
                        preparing,
                    )
                    recovery = committed_payload["recovery_proof"]
                    if recovery is not None and "replacement" in recovery:
                        replacement = self._validate_cleanup_replacement_proof(
                            recovery["replacement"],
                            label="resumed cleanup replacement proof",
                        )
                        replacement_state = str(replacement["state"])
                        replacement_temp = str(replacement["temp"])
                        replacement_location = (
                            self._cleanup_replacement_temp_location(
                                replacement_temp
                            )
                        )
                        replacement_fd = {
                            "root": self._root_fd,
                            "stage": directory_fd,
                        }[replacement_location]
                        temp_exists = self._path_exists_at(
                            replacement_fd,
                            replacement_temp,
                        )
                        if replacement_state == "preparing":
                            if temp_exists:
                                if replacement_location != "stage":
                                    raise RollbackCorruptionError(
                                        "recovery replacement temporary state changed"
                                    )
                                replacement_file = _HeldStoreFile.capture(
                                    self,
                                    replacement_temp,
                                    directory_fd=replacement_fd,
                                )
                                try:
                                    if replacement_file.raw:
                                        raise RollbackCorruptionError(
                                            "unsigned stage replacement changed"
                                        )
                                    replacement_file.revalidate(self)
                                finally:
                                    replacement_file.close()
                        elif replacement_state == "post":
                            if temp_exists:
                                raise RollbackCorruptionError(
                                    "recovery replacement temporary state changed"
                                )
                            destination = _HeldStoreFile.capture(
                                self,
                                str(replacement["destination"]),
                                directory_fd=replacement_fd,
                            )
                            try:
                                if (
                                    destination.identity
                                    != tuple(replacement["identity"])
                                    or canonical_digest(destination.raw)
                                    != replacement["observed_digest"]
                                ):
                                    raise RollbackCorruptionError(
                                        "post recovery replacement changed"
                                    )
                                destination.revalidate(self)
                            finally:
                                destination.close()
                        else:
                            if not temp_exists:
                                raise RollbackCorruptionError(
                                    "signed recovery replacement disappeared"
                                )
                            replacement_file = _HeldStoreFile.capture(
                                self,
                                replacement_temp,
                                directory_fd=replacement_fd,
                            )
                            try:
                                signed_identity = tuple(replacement["identity"])
                                expected_payload = str(
                                    replacement["expected_payload"]
                                ).encode("utf-8")
                                observed_digest = canonical_digest(
                                    replacement_file.raw
                                )
                                if replacement_state in {"created", "ready"} and (
                                    replacement_file.identity != signed_identity
                                    or observed_digest
                                    != replacement["observed_digest"]
                                    or (
                                        replacement_state == "ready"
                                        and replacement_file.raw
                                        != expected_payload
                                    )
                                ):
                                    raise RollbackCorruptionError(
                                        "signed recovery replacement changed"
                                    )
                                replacement_file.revalidate(self)
                            finally:
                                replacement_file.close()
                    if _CLEANUP_COMMITTED_TEMP_NAME in names:
                        self._dispose_cleanup_temp(
                            directory_fd,
                            _CLEANUP_COMMITTED_TEMP_NAME,
                            stage_identity,
                            expected_names=set(names),
                            prefix="resume.dispose.committed",
                        )
                        names = self._bounded_cleanup_staging_names(directory_fd)
                    if _CLEANUP_RECEIPT_TEMP_NAME in names:
                        self._dispose_cleanup_temp(
                            directory_fd,
                            _CLEANUP_RECEIPT_TEMP_NAME,
                            stage_identity,
                            expected_names=set(names),
                            prefix="resume.dispose.receipt",
                        )
                    self._resume_committed_cleanup(
                        directory_fd,
                        preparing_file.raw,
                        preparing,
                        committed_file.raw,
                    )
                    self._cleanup_resumed_forward = True
                finally:
                    committed_file.close()
                return True
            finally:
                preparing_file.close()
        finally:
            os.close(directory_fd)

    def _cleanup_abandoned_temps(self) -> None:
        if self._resume_cleanup_staging() and not self._cleanup_resumed_forward:
            self._cleanup_resumed_forward = False
            return
        self._cleanup_resumed_forward = False
        pattern = re.compile(
            rf"^\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\."
            r"(?:immutable|rollback|tmp|transaction-rollback|"
            r"displaced-head|prior-candidate)$"
        )
        expected_scan, _, _ = self._scan_abandoned_temp_names(
            pattern,
            collect=False,
        )
        observed_scan, names, root_names = self._scan_abandoned_temp_names(
            pattern,
            collect=True,
        )
        if observed_scan != expected_scan:
            raise RollbackCorruptionError(
                "rollback store root changed during abandoned temp scan"
            )
        names = sorted(names)
        root_names = sorted(root_names)
        if len(set(names)) != len(names):
            raise RollbackCorruptionError("abandoned rollback temp name is duplicated")
        if not names:
            if self._domain == "rollback-journal":
                self._recover_pending_terminal_restorations()
                self._validate_terminal_rollback_quarantines()
            return
        intents = tuple(
            name for name in names if name.endswith(".transaction-rollback")
        )
        if len(intents) > 1:
            raise RollbackCorruptionError(
                "multiple transaction rollback intents are forbidden"
            )
        recovery_artifacts = tuple(
            name
            for name in names
            if name.endswith((".displaced-head", ".prior-candidate"))
        )
        if recovery_artifacts and not intents:
            raise RollbackCorruptionError("rollback recovery artifact has no intent")
        held: dict[str, _HeldStoreFile] = {}
        recovery: _RollbackRecoveryCapsule | None = None
        directory_fd = -1
        preparing_raw: bytes | None = None
        preparing: dict[str, object] | None = None
        committed = False
        moved_any = False
        try:
            total_bytes = 0
            for name in names:
                try:
                    candidate = _HeldStoreFile.capture(self, name)
                except (OSError, RollbackCorruptionError) as error:
                    raise RollbackCorruptionError(
                        "abandoned rollback temp could not be held"
                    ) from error
                total_bytes += len(candidate.raw)
                if total_bytes > _MAX_ABANDONED_TEMP_BYTES:
                    candidate.close()
                    raise RollbackCorruptionError(
                        "abandoned rollback temp byte bound is exhausted"
                    )
                held[name] = candidate
            recovery = (
                self._preflight_transaction_rollback_intent(intents[0])
                if intents
                else None
            )
            try:
                for candidate in held.values():
                    candidate.revalidate(self)
            except (OSError, RollbackCorruptionError) as error:
                raise RollbackCorruptionError(
                    "abandoned rollback temp identity changed"
                ) from error
            if tuple(root_names) != self._bounded_root_names():
                raise RollbackCorruptionError(
                    "rollback store root changed before cleanup staging"
                )
            estimated_manifest_bytes = (
                sum(len(name.encode("utf-8")) for name in root_names)
                + sum(len(name.encode("utf-8")) + 256 for name in names)
                + 1024
            )
            if estimated_manifest_bytes > _MAX_CLEANUP_MANIFEST_BYTES:
                raise RollbackCorruptionError(
                    "cleanup preparing manifest bound is exhausted"
                )
            opened = self._open_cleanup_staging(create=True)
            assert opened is not None
            directory_fd, created = opened
            if not created or self._bounded_cleanup_staging_names(directory_fd):
                raise RollbackCorruptionError(
                    "exclusive cleanup staging directory already exists"
                )
            stage_identity = list(self._cleanup_stage_identity_now(directory_fd))
            root_identity = [
                self._root_stat.st_dev,
                self._root_stat.st_ino,
                self._owner[0],
                self._owner[1],
            ]
            preparing = {
                "candidates": [
                    {
                        "identity": list(held[name].identity),
                        "name": name,
                        "raw_sha256": canonical_digest(held[name].raw),
                    }
                    for name in names
                ],
                "domain": self._domain,
                "root_identity": root_identity,
                "root_names": root_names,
                "schema_version": "bb.rl.phase5.abandoned-cleanup-preparing.v2",
                "stage_identity": stage_identity,
                "state": "preparing",
                "transaction_id": self._cleanup_transaction_id(
                    stage_identity,
                    root_identity,
                    root_names,
                ),
            }
            preparing_raw = self._signed_bytes(
                "abandoned-cleanup-preparing",
                preparing,
            )
            if len(preparing_raw) > _MAX_CLEANUP_MANIFEST_BYTES:
                raise RollbackCorruptionError(
                    "cleanup preparing authority exceeds bound"
                )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_PREPARING_NAME,
                preparing_raw,
                stage_identity=stage_identity,
                expected_names=set(),
                boundary_prefix="authority.preparing.initial",
            )
            stage_names = {_CLEANUP_PREPARING_NAME}
            for index, name in enumerate(names):
                candidate = held[name]
                prefix = f"stage.move.{index}.{name}"
                candidate.revalidate(self)
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                self._cleanup_fault(f"{prefix}.before_move")
                candidate.revalidate(self)
                self._rejoin_cleanup_stage(
                    directory_fd,
                    stage_identity,
                    expected_names=stage_names,
                )
                _rename_noreplace_between(
                    name,
                    name,
                    self._root_fd,
                    directory_fd,
                )
                moved_any = True
                stage_names.add(name)
                self._cleanup_fault(f"{prefix}.after_move")
                self._sync_cleanup_stage(directory_fd, prefix=prefix)
                self._sync_cleanup_root(prefix=prefix)
                candidate.path_directory_fd = directory_fd
                candidate.refresh_path_identity(self, name)
            self._cleanup_fault("stage.all_moved")
            preparing["candidates"] = [
                {
                    "identity": list(held[name].identity),
                    "name": name,
                    "raw_sha256": canonical_digest(held[name].raw),
                }
                for name in names
            ]
            preparing_raw = self._signed_bytes(
                "abandoned-cleanup-preparing",
                preparing,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_PREPARING_NAME,
                preparing_raw,
                stage_identity=stage_identity,
                expected_names=stage_names,
                replace=True,
                boundary_prefix="authority.preparing.staged",
            )
            candidate_states = [{"name": name, "state": "pending"} for name in names]
            committed_raw = self._cleanup_committed_bytes(
                preparing_raw,
                preparing,
                candidate_states,
                0,
                tombstone_proofs=(),
                recovery_proof=None,
            )
            self._write_cleanup_authority(
                directory_fd,
                _CLEANUP_COMMITTED_NAME,
                committed_raw,
                stage_identity=stage_identity,
                expected_names=stage_names,
                boundary_prefix="authority.committed.g0",
            )
            self._sync_cleanup_root(prefix="authority.committed.g0")
            committed = True
            if recovery is not None:
                recovery.close()
                recovery = None
            for candidate in held.values():
                candidate.close()
            held.clear()
            self._resume_committed_cleanup(
                directory_fd,
                preparing_raw,
                preparing,
                committed_raw,
            )
        except _CleanupInjectedCrash:
            raise
        except BaseException:
            if committed:
                raise
            if directory_fd >= 0:
                stage_names = self._bounded_cleanup_staging_names(directory_fd)
                current_stage_identity = self._cleanup_stage_identity_now(directory_fd)
                if (
                    _CLEANUP_COMMITTED_TEMP_NAME in stage_names
                    and _CLEANUP_COMMITTED_NAME not in stage_names
                ):
                    self._dispose_cleanup_temp(
                        directory_fd,
                        _CLEANUP_COMMITTED_TEMP_NAME,
                        current_stage_identity,
                        expected_names=set(stage_names),
                        prefix="exception.dispose.committed",
                    )
                    stage_names = self._bounded_cleanup_staging_names(directory_fd)
                if (
                    _CLEANUP_PREPARING_TEMP_NAME in stage_names
                    and _CLEANUP_PREPARING_NAME not in stage_names
                ):
                    self._dispose_cleanup_temp(
                        directory_fd,
                        _CLEANUP_PREPARING_TEMP_NAME,
                        current_stage_identity,
                        expected_names=set(stage_names),
                        prefix="exception.dispose.preparing",
                    )
                    stage_names = self._bounded_cleanup_staging_names(directory_fd)
                if not moved_any and stage_names == (_CLEANUP_PREPARING_NAME,):
                    self._remove_cleanup_authority(
                        directory_fd,
                        _CLEANUP_PREPARING_NAME,
                        prefix="exception.remove.preparing",
                        stage_identity=current_stage_identity,
                        expected_names=set(stage_names),
                    )
                    self._sync_cleanup_stage(
                        directory_fd,
                        prefix="exception.remove.preparing",
                    )
                    stage_names = ()
                    next_stage_identity = (
                        *current_stage_identity[:5],
                        current_stage_identity[5] - 1,
                    )
                    self._rejoin_cleanup_stage(
                        directory_fd,
                        next_stage_identity,
                        expected_names=set(),
                    )
                    current_stage_identity = next_stage_identity
                if stage_names:
                    assert preparing is not None
                    self._rollback_cleanup_staging(directory_fd, preparing)
                else:
                    self._remove_cleanup_stage(
                        directory_fd,
                        prefix="exception.terminal",
                        stage_identity=current_stage_identity,
                    )
            raise
        finally:
            if recovery is not None:
                recovery.close()
            for candidate in held.values():
                candidate.close()
            if directory_fd >= 0:
                os.close(directory_fd)
        if self._domain == "rollback-journal":
            self._recover_pending_terminal_restorations()
            self._validate_terminal_rollback_quarantines()

    def close(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
            for descriptor in (
                self._lock_fd,
                self._terminal_fd,
                self._quarantine_fd,
                self._root_fd,
            ):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _validate_root(self) -> None:
        if self._closed:
            raise RollbackCorruptionError("rollback store is closed")
        current = os.stat(self.root, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o700
            or (current.st_dev, current.st_ino)
            != (self._root_stat.st_dev, self._root_stat.st_ino)
            or (current.st_uid, current.st_gid) != self._owner
        ):
            raise RollbackCorruptionError("rollback store root identity changed")
        if self._quarantine_stat is None or self._quarantine_fd < 0:
            raise RollbackCorruptionError(
                "rollback quarantine directory is unavailable"
            )
        quarantine = os.stat(
            ".quarantine",
            dir_fd=self._root_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(quarantine.st_mode)
            or stat.S_IMODE(quarantine.st_mode) != 0o700
            or (quarantine.st_dev, quarantine.st_ino)
            != (
                self._quarantine_stat.st_dev,
                self._quarantine_stat.st_ino,
            )
            or (quarantine.st_uid, quarantine.st_gid) != self._owner
        ):
            raise RollbackCorruptionError(
                "rollback quarantine directory identity changed"
            )
        if self._domain == "rollback-journal":
            if self._terminal_stat is None or self._terminal_fd < 0:
                raise RollbackCorruptionError(
                    "rollback terminal directory is unavailable"
                )
            terminal = os.stat(
                _ROLLBACK_TERMINAL_DIRECTORY,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(terminal.st_mode)
                or stat.S_IMODE(terminal.st_mode) != 0o700
                or (terminal.st_dev, terminal.st_ino)
                != (
                    self._terminal_stat.st_dev,
                    self._terminal_stat.st_ino,
                )
                or (terminal.st_uid, terminal.st_gid) != self._owner
            ):
                raise RollbackCorruptionError(
                    "rollback terminal directory identity changed"
                )

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._thread_lock:
            self._validate_root()
            fcntl.flock(self._root_fd, fcntl.LOCK_EX)
            try:
                self._validate_root()
                if self._domain == "rollback-journal":
                    self._validate_terminal_rollback_quarantines()
                yield
                self._validate_root()
                if self._domain == "rollback-journal":
                    self._validate_terminal_rollback_quarantines()
            finally:
                fcntl.flock(self._root_fd, fcntl.LOCK_UN)

    def _path_directory_fd(self, name: str) -> int:
        if name.startswith("rollback-quarantine."):
            if self._domain != "rollback-journal" or self._terminal_fd < 0:
                raise RollbackCorruptionError("rollback terminal path is unavailable")
            return self._terminal_fd
        return self._root_fd

    def _open_regular(self, name: str, flags: int, mode: int | None = None) -> int:
        open_flags = flags | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = self._path_directory_fd(name)
        if mode is None:
            fd = os.open(name, open_flags, dir_fd=directory_fd)
        else:
            fd = os.open(name, open_flags, mode, dir_fd=directory_fd)
        value = os.fstat(fd)
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_nlink != 1
            or (value.st_uid, value.st_gid) != self._owner
        ):
            os.close(fd)
            raise RollbackCorruptionError(
                "rollback store file must be trusted-owner, regular, "
                "single-link, and 0600"
            )
        return fd

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("rollback store write made no progress")
            view = view[written:]

    def _read(self, name: str) -> bytes | None:
        self._validate_root()
        try:
            fd = self._open_regular(name, os.O_RDONLY)
        except FileNotFoundError:
            return None
        try:
            value = os.fstat(fd)
            if value.st_size > _MAX_RECORD_BYTES:
                raise RollbackCorruptionError(
                    "rollback store record exceeds size bound"
                )
            remaining = value.st_size + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != value.st_size:
                raise RollbackCorruptionError(
                    "rollback store record changed during read"
                )
            return payload
        finally:
            os.close(fd)

    def _write_temp(self, name: str, payload: bytes) -> None:
        if len(payload) > _MAX_RECORD_BYTES:
            raise RollbackValidationError("rollback store record exceeds size bound")
        if self._publication_tx is not None:
            self._publication_tx.temps.add(name)
        fd = self._open_regular(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            self._write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        if (
            self._cleanup_recovery_replace_boundary is not None
            and name == self._cleanup_recovery_replace_temp
        ):
            self._cleanup_recovery_fault(
                f"{self._cleanup_recovery_replace_boundary}.after_temp_ready"
            )

    def _create_immutable(self, name: str, payload: bytes) -> None:
        temp = f".{self._domain}.{uuid.uuid4().hex}.immutable"
        linked = False
        try:
            self._write_temp(temp, payload)
            try:
                os.link(
                    temp,
                    name,
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                    follow_symlinks=False,
                )
                linked = True
                if self._publication_tx is not None:
                    self._publication_tx.created.add(name)
            except FileExistsError:
                existing = self._read(name)
                if existing != payload:
                    raise RollbackCorruptionError(
                        "immutable rollback history conflicts"
                    )
            try:
                os.unlink(temp, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass
            os.fsync(self._root_fd)
        except BaseException:
            if linked:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                except FileNotFoundError:
                    pass
            raise
        finally:
            try:
                os.unlink(temp, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass

    def _replace_at(
        self,
        directory_fd: int,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
        old_file: _HeldStoreFile,
    ) -> None:
        replacement_state = self._verify_signed(
            payload,
            "publication-rollback-intent",
        )["state"]
        boundary_prefix = {
            "cleanup_pending": "cleanup_intent",
            "quarantined": "terminal_intent",
        }.get(replacement_state)
        if boundary_prefix is None:
            raise RollbackCorruptionError(
                "recovery replacement state is invalid"
            )
        if directory_fd == self._root_fd:
            if old_payload is None or old_file.raw != old_payload:
                raise RollbackCorruptionError(
                    "recovery replacement old payload binding is invalid"
                )
            old_file.revalidate(self)
            self._replace(name, payload, old_payload)
            self._cleanup_recovery_fault(f"{boundary_prefix}.after_publish")
            return
        if len(payload) > _MAX_RECORD_BYTES:
            raise RollbackValidationError("rollback store record exceeds size bound")

        resumed_proof = self._cleanup_recovery_replace_proof
        if resumed_proof is not None and resumed_proof["state"] == "post":
            self._validate_cleanup_replacement_proof(
                resumed_proof,
                label="post stage replacement proof",
            )
            temp = str(resumed_proof["temp"])
            if (
                self._cleanup_replacement_temp_location(temp) != "stage"
                or resumed_proof["destination"] != name
                or resumed_proof["expected_digest"] != canonical_digest(payload)
                or resumed_proof["expected_payload"] != payload.decode("utf-8")
                or self._path_exists_at(directory_fd, temp)
                or old_file.identity != tuple(resumed_proof["identity"])
                or old_file.raw != payload
            ):
                raise RollbackCorruptionError(
                    "post stage replacement binding changed"
                )
            old_file.revalidate(self)
            self._cleanup_recovery_replace_proof = None
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
            return

        if old_payload is None or old_file.raw != old_payload:
            raise RollbackCorruptionError(
                "recovery replacement old payload binding is invalid"
            )
        old_file.revalidate(self)
        token = canonical_digest(
            canonical_json_bytes(
                {
                    "name": name,
                    "old_identity": list(old_file.identity),
                    "payload_digest": canonical_digest(payload),
                }
            )
        )[7:39]
        temp = f".intent-replace-{token}"
        intent_temps = {
            candidate
            for candidate in self._bounded_cleanup_staging_names(directory_fd)
            if candidate.startswith(".intent-replace-")
        }
        for candidate in intent_temps:
            if self._cleanup_replacement_temp_location(candidate) != "stage":
                raise RollbackCorruptionError(
                    "recovery replacement temporary name changed"
                )
        if intent_temps - {temp}:
            raise RollbackCorruptionError(
                "recovery replacement temporary name changed"
            )

        base_proof: dict[str, object] = {
            "destination": name,
            "destination_digest": canonical_digest(old_file.raw),
            "destination_identity": list(old_file.identity),
            "expected_digest": canonical_digest(payload),
            "expected_payload": payload.decode("utf-8"),
            "expected_size": len(payload),
            "temp": temp,
        }
        proof = self._cleanup_recovery_replace_proof
        self._cleanup_recovery_replace_temp = temp
        self._cleanup_recovery_replace_destination = name
        temp_file: _HeldStoreFile | None = None
        write_fd = -1
        try:
            if proof is None:
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.before_temp_create"
                )
            else:
                self._validate_cleanup_replacement_proof(
                    proof,
                    label="active stage replacement proof",
                )
                if (
                    self._cleanup_replacement_temp_location(proof["temp"])
                    != "stage"
                    or any(proof.get(key) != value for key, value in base_proof.items())
                ):
                    raise RollbackCorruptionError(
                        "active stage replacement binding changed"
                    )

            for _ in range(2):
                state = str(proof["state"])
                temp_exists = self._path_exists_at(directory_fd, temp)
                if state == "preparing":
                    if temp_exists:
                        temp_file = _HeldStoreFile.capture(
                            self,
                            temp,
                            directory_fd=directory_fd,
                        )
                        if temp_file.raw:
                            raise RollbackCorruptionError(
                                "unsigned stage replacement temporary changed"
                            )
                        self._cleanup_recovery_fault(
                            f"{boundary_prefix}.before_temp_restart_unlink"
                        )
                        temp_file.revalidate(self)
                        os.unlink(temp, dir_fd=directory_fd)
                        os.fsync(directory_fd)
                        self._cleanup_recovery_fault(
                            f"{boundary_prefix}.after_temp_restart_unlink"
                        )
                        temp_file.close()
                        temp_file = None
                        self._cleanup_recovery_fault(
                            f"{boundary_prefix}.before_temp_create"
                        )
                    write_fd = os.open(
                        temp,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=directory_fd,
                    )
                    temp_file = _HeldStoreFile.capture(
                        self,
                        temp,
                        directory_fd=directory_fd,
                    )
                    if temp_file.raw or temp_file.identity[6] != 0:
                        raise RollbackCorruptionError(
                            "created stage replacement temporary is not empty"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "created",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_create"
                    )
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.before_temp_write"
                    )
                    self._write_all(write_fd, payload)
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_write"
                    )
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.before_temp_fsync"
                    )
                    os.fsync(write_fd)
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_fsync"
                    )
                    os.close(write_fd)
                    write_fd = -1
                    created_identity = temp_file.identity
                    temp_file.close()
                    temp_file = _HeldStoreFile.capture(
                        self,
                        temp,
                        directory_fd=directory_fd,
                    )
                    if (
                        temp_file.identity[:6] != created_identity[:6]
                        or temp_file.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "stage replacement temporary changed during write"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "ready",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(
                        f"{boundary_prefix}.after_temp_ready"
                    )
                    break

                if not temp_exists:
                    raise RollbackCorruptionError(
                        "signed stage replacement temporary disappeared"
                    )
                temp_file = _HeldStoreFile.capture(
                    self,
                    temp,
                    directory_fd=directory_fd,
                )
                signed_identity = tuple(proof["identity"])
                if (
                    temp_file.identity != signed_identity
                    or canonical_digest(temp_file.raw) != proof["observed_digest"]
                ):
                    raise RollbackCorruptionError(
                        "created stage replacement temporary changed"
                    )
                if state == "ready":
                    if temp_file.raw != payload:
                        raise RollbackCorruptionError(
                            "ready stage replacement temporary changed"
                        )
                    break
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.before_temp_restart_unlink"
                )
                temp_file.revalidate(self)
                os.unlink(temp, dir_fd=directory_fd)
                os.fsync(directory_fd)
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.after_temp_restart_unlink"
                )
                temp_file.close()
                temp_file = None
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(
                    f"{boundary_prefix}.before_temp_create"
                )
            else:
                raise RollbackCorruptionError(
                    "stage replacement temporary restart bound exhausted"
                )

            assert temp_file is not None
            self._cleanup_recovery_fault(f"{boundary_prefix}.before_publish")
            old_file.revalidate(self)
            temp_file.revalidate(self)
            os.replace(
                temp,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
            self._cleanup_recovery_fault(f"{boundary_prefix}.after_publish")
            self._cleanup_recovery_replace_proof = None
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            if temp_file is not None:
                temp_file.close()

    def _replace_recovery_root(
        self,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
        *,
        boundary: str,
        temp: str,
    ) -> None:
        old_file: _HeldStoreFile | None = None
        temp_file: _HeldStoreFile | None = None
        write_fd = -1
        try:
            resumed_proof = self._cleanup_recovery_replace_proof
            if (
                resumed_proof is not None
                and resumed_proof["state"] == "post"
            ):
                self._validate_cleanup_replacement_proof(
                    resumed_proof,
                    label="post cleanup replacement proof",
                )
                if (
                    resumed_proof["destination"] != name
                    or resumed_proof["temp"] != temp
                    or resumed_proof["expected_digest"]
                    != canonical_digest(payload)
                    or self._path_exists_at(self._root_fd, temp)
                ):
                    raise RollbackCorruptionError(
                        "post cleanup replacement binding changed"
                    )
                installed = _HeldStoreFile.capture(self, name)
                try:
                    if (
                        installed.identity != tuple(resumed_proof["identity"])
                        or installed.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "post cleanup replacement destination changed"
                        )
                    installed.revalidate(self)
                finally:
                    installed.close()
                self._cleanup_recovery_replace_proof = None
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
                return
            if old_payload is not None:
                old_file = _HeldStoreFile.capture(self, name)
                if old_file.raw != old_payload:
                    raise RollbackCorruptionError(
                        "recovery root replacement old payload changed"
                    )
                old_file.revalidate(self)
            elif self._path_exists_at(self._root_fd, name):
                raise RollbackCorruptionError(
                    "recovery root replacement destination appeared"
                )
            base_proof: dict[str, object] = {
                "destination": name,
                "destination_digest": (
                    None
                    if old_file is None
                    else canonical_digest(old_file.raw)
                ),
                "destination_identity": (
                    None if old_file is None else list(old_file.identity)
                ),
                "expected_digest": canonical_digest(payload),
                "expected_payload": payload.decode("utf-8"),
                "expected_size": len(payload),
                "temp": temp,
            }
            proof = self._cleanup_recovery_replace_proof
            if proof is None:
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(f"{boundary}.before_temp_create")
            else:
                self._validate_cleanup_replacement_proof(
                    proof,
                    label="active cleanup replacement proof",
                )
                if any(proof.get(key) != value for key, value in base_proof.items()):
                    raise RollbackCorruptionError(
                        "active cleanup replacement binding changed"
                    )
            for _ in range(2):
                state = str(proof["state"])
                temp_exists = self._path_exists_at(self._root_fd, temp)
                if state == "preparing":
                    if temp_exists:
                        raise RollbackCorruptionError(
                            "uncreated recovery replacement temporary appeared"
                        )
                    write_fd = os.open(
                        temp,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=self._root_fd,
                    )
                    temp_file = _HeldStoreFile.capture(self, temp)
                    if temp_file.raw or temp_file.identity[6] != 0:
                        raise RollbackCorruptionError(
                            "created recovery replacement temporary is not empty"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "created",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_create")
                    self._cleanup_recovery_fault(f"{boundary}.before_temp_write")
                    self._write_all(write_fd, payload)
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_write")
                    self._cleanup_recovery_fault(f"{boundary}.before_temp_fsync")
                    os.fsync(write_fd)
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_fsync")
                    os.close(write_fd)
                    write_fd = -1
                    created_identity = temp_file.identity
                    temp_file.close()
                    temp_file = _HeldStoreFile.capture(self, temp)
                    if (
                        temp_file.identity[:6] != created_identity[:6]
                        or temp_file.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "recovery replacement temporary changed during write"
                        )
                    proof = {
                        **base_proof,
                        "identity": list(temp_file.identity),
                        "observed_digest": canonical_digest(temp_file.raw),
                        "state": "ready",
                    }
                    self._cleanup_recovery_replace_proof = proof
                    self._cleanup_recovery_fault(f"{boundary}.after_temp_ready")
                    break
                if not temp_exists:
                    raise RollbackCorruptionError(
                        "signed recovery replacement temporary disappeared"
                    )
                temp_file = _HeldStoreFile.capture(self, temp)
                signed_identity = tuple(proof["identity"])
                if (
                    temp_file.identity != signed_identity
                    or canonical_digest(temp_file.raw) != proof["observed_digest"]
                ):
                    raise RollbackCorruptionError(
                        "created recovery replacement temporary changed"
                    )
                if state == "ready":
                    if (
                        temp_file.identity != signed_identity
                        or temp_file.raw != payload
                    ):
                        raise RollbackCorruptionError(
                            "ready recovery replacement temporary changed"
                        )
                    break
                self._cleanup_recovery_fault(
                    f"{boundary}.before_temp_restart_unlink"
                )
                temp_file.revalidate(self)
                os.unlink(temp, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
                self._cleanup_recovery_fault(
                    f"{boundary}.after_temp_restart_unlink"
                )
                temp_file.close()
                temp_file = None
                proof = {
                    **base_proof,
                    "identity": None,
                    "observed_digest": None,
                    "state": "preparing",
                }
                self._cleanup_recovery_replace_proof = proof
                self._cleanup_recovery_fault(f"{boundary}.before_temp_create")
            else:
                raise RollbackCorruptionError(
                    "recovery replacement temporary restart bound exhausted"
                )
            assert temp_file is not None
            self._cleanup_recovery_fault(f"{boundary}.before_replace")
            if old_file is not None:
                old_file.revalidate(self)
            elif self._path_exists_at(self._root_fd, name):
                raise RollbackCorruptionError(
                    "recovery root replacement destination appeared"
                )
            temp_file.revalidate(self)
            os.replace(
                temp,
                name,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
            os.fsync(self._root_fd)
            self._cleanup_recovery_fault(f"{boundary}.after_replace")
            self._cleanup_recovery_replace_proof = None
            self._cleanup_recovery_replace_temp = None
            self._cleanup_recovery_replace_destination = None
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            if temp_file is not None:
                temp_file.close()
            if old_file is not None:
                old_file.close()

    def _replace(self, name: str, payload: bytes, old_payload: bytes | None) -> None:
        if self._publication_tx is not None:
            self._publication_tx.capture_replaced(name, old_payload)
        recovery_boundary = self._cleanup_recovery_replace_boundary
        if recovery_boundary is None:
            temp = f".{self._domain}.{uuid.uuid4().hex}.tmp"
        else:
            resumed_proof = self._cleanup_recovery_replace_proof
            if (
                resumed_proof is not None
                and resumed_proof["state"] == "post"
                and (
                    resumed_proof["destination"] != name
                    or resumed_proof["expected_digest"]
                    != canonical_digest(payload)
                )
            ):
                self._validate_cleanup_replacement_proof(
                    resumed_proof,
                    label="completed cleanup replacement proof",
                )
                completed_temp = str(resumed_proof["temp"])
                if self._path_exists_at(self._root_fd, completed_temp):
                    raise RollbackCorruptionError(
                        "completed cleanup replacement temporary survived"
                    )
                completed = _HeldStoreFile.capture(
                    self,
                    str(resumed_proof["destination"]),
                )
                try:
                    expected_payload = str(
                        resumed_proof["expected_payload"]
                    ).encode("utf-8")
                    if (
                        completed.identity
                        != tuple(resumed_proof["identity"])
                        or completed.raw != expected_payload
                        or canonical_digest(completed.raw)
                        != resumed_proof["expected_digest"]
                    ):
                        raise RollbackCorruptionError(
                            "completed cleanup replacement destination changed"
                        )
                    completed.revalidate(self)
                finally:
                    completed.close()
                self._cleanup_recovery_replace_proof = None
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
                resumed_proof = None
            if (
                resumed_proof is not None
                and resumed_proof["state"] == "post"
            ):
                temp = str(resumed_proof["temp"])
            else:
                token = canonical_digest(
                    canonical_json_bytes(
                        {
                            "name": name,
                            "old_digest": (
                                None
                                if old_payload is None
                                else canonical_digest(old_payload)
                            ),
                            "payload_digest": canonical_digest(payload),
                        }
                    )
                )[7:39]
                temp = f".{self._domain}.{token}.tmp"
            self._cleanup_recovery_replace_temp = temp
            self._cleanup_recovery_replace_destination = name
            ready_root_temps = {
                candidate
                for candidate in self._bounded_root_names()
                if re.fullmatch(
                    rf"\.{re.escape(self._domain)}\.[0-9a-f]{{32}}\.tmp",
                    candidate,
                )
                is not None
            }
            if ready_root_temps - {temp}:
                raise RollbackCorruptionError(
                    "recovery root replacement temporary name changed"
                )
            self._replace_recovery_root(
                name,
                payload,
                old_payload,
                boundary=recovery_boundary,
                temp=temp,
            )
            return
        replaced = False
        old_file: _HeldStoreFile | None = None
        temp_file: _HeldStoreFile | None = None
        try:
            if not self._path_exists_at(self._root_fd, temp):
                self._write_temp(temp, payload)
            temp_file = _HeldStoreFile.capture(self, temp)
            if temp_file.raw != payload:
                raise RollbackCorruptionError(
                    "recovery root replacement temporary payload changed"
                )
            if old_payload is not None:
                old_file = _HeldStoreFile.capture(self, name)
                if old_file.raw != old_payload:
                    raise RollbackCorruptionError(
                        "recovery root replacement old payload changed"
                    )
            if old_file is not None:
                old_file.revalidate(self)
            elif self._path_exists_at(self._root_fd, name):
                raise RollbackCorruptionError(
                    "recovery root replacement destination appeared"
                )
            temp_file.revalidate(self)
            os.replace(
                temp,
                name,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._root_fd,
            )
            replaced = True
            if self._publication_tx is not None:
                self._publication_tx.mark_replaced(name)
            os.fsync(self._root_fd)
            if recovery_boundary is not None:
                self._cleanup_recovery_fault(
                    f"{recovery_boundary}.after_replace"
                )
                self._cleanup_recovery_replace_temp = None
                self._cleanup_recovery_replace_destination = None
        except BaseException:
            if replaced and recovery_boundary is None:
                if old_payload is None:
                    os.unlink(name, dir_fd=self._root_fd)
                else:
                    rollback = f".{self._domain}.{uuid.uuid4().hex}.rollback"
                    try:
                        self._write_temp(rollback, old_payload)
                        os.replace(
                            rollback,
                            name,
                            src_dir_fd=self._root_fd,
                            dst_dir_fd=self._root_fd,
                        )
                    finally:
                        try:
                            os.unlink(rollback, dir_fd=self._root_fd)
                        except FileNotFoundError:
                            pass
                os.fsync(self._root_fd)
            raise
        finally:
            if old_file is not None:
                old_file.close()
            if temp_file is not None:
                temp_file.close()
            if recovery_boundary is None:
                try:
                    os.unlink(temp, dir_fd=self._root_fd)
                except FileNotFoundError:
                    pass

    def _commit_bytes(
        self, identity: str, generation: int, record_digest: str
    ) -> bytes:
        return self._signed_bytes(
            "generation-commit",
            {
                "generation": generation,
                "identity": identity,
                "record_digest": record_digest,
                "schema_version": "bb.rl.phase5.rollback-generation-commit.v1",
            },
        )

    def _verify_commit(
        self,
        raw: bytes,
        *,
        identity: str,
        generation: int,
        record_digest: str,
    ) -> None:
        payload = _require_object(
            self._verify_signed(raw, "generation-commit"),
            frozenset(("generation", "identity", "record_digest", "schema_version")),
            "rollback generation commit",
        )
        if (
            payload["schema_version"] != "bb.rl.phase5.rollback-generation-commit.v1"
            or payload["identity"] != identity
            or payload["generation"] != generation
            or payload["record_digest"] != record_digest
        ):
            raise RollbackCorruptionError("rollback generation commit mismatch")

    def _publish_versioned(
        self,
        *,
        head_name: str,
        history_name: str,
        commit_name: str,
        identity: str,
        generation: int,
        record_digest: str,
        signed_record: bytes,
        old_head: bytes | None,
    ) -> None:
        if self._domain == "rollback-journal":
            self._assert_generation_not_quarantined(
                identity,
                generation,
                record_digest,
            )
        self._create_immutable(history_name, signed_record)
        if self._publication_tx is not None:
            self._publication_tx.revalidate()
        self._replace(head_name, signed_record, old_head)
        try:
            self._create_immutable(
                commit_name,
                self._commit_bytes(identity, generation, record_digest),
            )
        except BaseException:
            if old_head is None:
                try:
                    os.unlink(head_name, dir_fd=self._root_fd)
                except FileNotFoundError:
                    pass
                os.fsync(self._root_fd)
            else:
                self._replace(head_name, old_head, signed_record)
            raise

    def _signed_bytes(self, kind: str, payload: Mapping[str, Any]) -> bytes:
        payload_bytes = canonical_json_bytes(payload)
        payload_digest = canonical_digest(payload_bytes)
        mac_input = (
            canonical_json_bytes(
                {
                    "domain": self._domain,
                    "kind": kind,
                    "payload_digest": payload_digest,
                }
            )
            + payload_bytes
        )
        authority_hmac = hmac.new(
            self._authority_key, mac_input, hashlib.sha256
        ).hexdigest()
        return canonical_json_bytes(
            {
                "authority_hmac": authority_hmac,
                "domain": self._domain,
                "kind": kind,
                "payload": payload,
                "payload_digest": payload_digest,
                "schema_version": "bb.rl.phase5.rollback-signed-record.v1",
            }
        )

    def _verify_signed(self, raw: bytes, kind: str) -> Mapping[str, Any]:
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RollbackCorruptionError(
                "rollback store record is not canonical JSON"
            ) from error
        outer = _require_object(
            decoded,
            frozenset(
                (
                    "authority_hmac",
                    "domain",
                    "kind",
                    "payload",
                    "payload_digest",
                    "schema_version",
                )
            ),
            "signed rollback record",
        )
        if raw != canonical_json_bytes(decoded):
            raise RollbackCorruptionError(
                "rollback store record is not canonically encoded"
            )
        if (
            outer["schema_version"] != "bb.rl.phase5.rollback-signed-record.v1"
            or outer["domain"] != self._domain
            or outer["kind"] != kind
            or type(outer["authority_hmac"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", outer["authority_hmac"])
        ):
            raise RollbackCorruptionError("rollback store signed identity is invalid")
        payload = outer["payload"]
        if type(payload) is not dict:
            raise RollbackCorruptionError("rollback store signed payload is invalid")
        payload_bytes = canonical_json_bytes(payload)
        expected_digest = canonical_digest(payload_bytes)
        if outer["payload_digest"] != expected_digest:
            raise RollbackCorruptionError("rollback store payload digest mismatch")
        mac_input = (
            canonical_json_bytes(
                {
                    "domain": self._domain,
                    "kind": kind,
                    "payload_digest": expected_digest,
                }
            )
            + payload_bytes
        )
        expected_hmac = hmac.new(
            self._authority_key, mac_input, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_hmac, outer["authority_hmac"]):
            raise RollbackCorruptionError("rollback store authority HMAC mismatch")
        return payload

    def _block_identity(
        self,
        marker: str,
        identity: str,
    ) -> None:
        marker_payload = self._signed_bytes(
            "corruption-marker",
            {"domain": self._domain, "identity": identity},
        )
        old_marker = self._read(marker)
        if old_marker is None:
            self._replace(marker, marker_payload, None)
        elif old_marker != marker_payload:
            raise RollbackCorruptionError(
                "rollback corruption marker identity diverged"
            )
        os.fsync(self._root_fd)

    def _block_rollback_id(self, rollback_id: str) -> None:
        rollback_id = _require_id(rollback_id, "rollback id")
        self._block_identity(
            f"journal.{rollback_id}.blocked",
            rollback_id,
        )

    def _rollback_id_blocked(self, rollback_id: str) -> bool:
        rollback_id = _require_id(rollback_id, "rollback id")
        raw = self._read(f"journal.{rollback_id}.blocked")
        if raw is None:
            return False
        payload = _require_object(
            self._verify_signed(raw, "corruption-marker"),
            frozenset(("domain", "identity")),
            "rollback corruption marker",
        )
        if payload != {
            "domain": "rollback-journal",
            "identity": rollback_id,
        }:
            raise RollbackCorruptionError(
                "rollback corruption marker identity is invalid"
            )
        return True

    def _blocked(self, marker: str) -> bool:
        raw = self._read(marker)
        if raw is None:
            return False
        self._verify_signed(raw, "corruption-marker")
        return True

    def _quarantine(self, name: str, marker: str, identity: str) -> None:
        self._block_identity(marker, identity)
        destination = f"{self._domain}.{canonical_digest(identity.encode())[7:]}.{uuid.uuid4().hex}.corrupt"
        try:
            os.rename(
                name,
                destination,
                src_dir_fd=self._root_fd,
                dst_dir_fd=self._quarantine_fd,
            )
        except FileNotFoundError:
            pass
        os.fsync(self._quarantine_fd)
        os.fsync(self._root_fd)


class FilesystemRollbackJournalStore(_PinnedSignedDirectory):
    def __init__(
        self,
        root: str | Path,
        *,
        authority_key: bytes,
        root_fd: int | None = None,
    ) -> None:
        super().__init__(
            root,
            authority_key=authority_key,
            domain="rollback-journal",
            root_fd=root_fd,
        )

    @staticmethod
    def _head_name(rollback_id: str) -> str:
        return f"journal.{_require_id(rollback_id, 'rollback id')}.head"

    @staticmethod
    def _marker_name(rollback_id: str) -> str:
        return f"journal.{_require_id(rollback_id, 'rollback id')}.blocked"

    @staticmethod
    def _request_name(rollback_id: str) -> str:
        return f"journal.{_require_id(rollback_id, 'rollback id')}.request"

    def _request_binding_bytes(self, rollback_id: str, request_digest: str) -> bytes:
        return self._signed_bytes(
            "journal-request-binding",
            {
                "request_digest": request_digest,
                "rollback_id": rollback_id,
                "schema_version": "bb.rl.phase5.rollback-request-binding.v1",
            },
        )

    def _verify_request_binding_locked(
        self, rollback_id: str, request_digest: str
    ) -> bytes:
        name = self._request_name(rollback_id)
        raw = self._read(name)
        expected = self._request_binding_bytes(rollback_id, request_digest)
        if raw is None:
            self._quarantine(name, self._marker_name(rollback_id), rollback_id)
            raise RollbackCorruptionError("rollback journal request binding is missing")
        try:
            self._verify_signed(raw, "journal-request-binding")
        except (RollbackValidationError, RollbackCorruptionError) as error:
            self._quarantine(name, self._marker_name(rollback_id), rollback_id)
            raise RollbackCorruptionError(
                "rollback journal request binding was quarantined"
            ) from error
        if raw != expected:
            raise RollbackIdempotencyConflict(
                "rollback id is already bound to a different request digest"
            )
        return raw

    def _payload_bytes(self, ref: RollbackPayloadRef, payload: bytes) -> bytes:
        return self._signed_bytes(
            "journal-payload",
            {
                "payload_base64": base64.b64encode(payload).decode("ascii"),
                "payload_ref": ref.canonical_object(),
                "schema_version": "bb.rl.phase5.rollback-payload-object.v1",
            },
        )

    def _decode_payload_locked(
        self,
        ref: RollbackPayloadRef,
        *,
        leaf_errors: tuple[RollbackLeafError, ...] = (),
        prior_receipt_digests: tuple[str, ...] = (),
        prior_receipt_refs: tuple[RollbackPayloadRef, ...] = (),
        request: Mapping[str, Any] | None = None,
    ) -> bytes:
        raw = self._read(ref.relative_path)
        try:
            if raw is None:
                raise RollbackCorruptionError(
                    "authoritative rollback payload is missing"
                )
            value = _require_object(
                self._verify_signed(raw, "journal-payload"),
                frozenset(("payload_base64", "payload_ref", "schema_version")),
                "rollback payload object",
            )
            if (
                value["schema_version"] != "bb.rl.phase5.rollback-payload-object.v1"
                or _payload_ref_from_object(value["payload_ref"]) != ref
                or type(value["payload_base64"]) is not str
            ):
                raise RollbackCorruptionError(
                    "authoritative rollback payload binding mismatch"
                )
            try:
                payload = base64.b64decode(value["payload_base64"], validate=True)
            except (ValueError, binascii.Error) as error:
                raise RollbackCorruptionError(
                    "authoritative rollback payload encoding is invalid"
                ) from error
            if ref.kind is RollbackPayloadKind.REQUEST:
                _validate_request_payload(payload, ref.rollback_id, ref.request_digest)
            else:
                _validate_receipt_payload(
                    payload,
                    ref=ref,
                    leaf_errors=leaf_errors,
                    prior_receipt_digests=prior_receipt_digests,
                    prior_receipt_refs=prior_receipt_refs,
                    request=(
                        request
                        if request is not None
                        else (_ for _ in ()).throw(
                            RollbackCorruptionError(
                                "receipt payload request context is missing"
                            )
                        )
                    ),
                    store_root=self.root,
                )
            return payload
        except (
            RollbackValidationError,
            RollbackCorruptionError,
            OSError,
        ) as error:
            self._quarantine(
                ref.relative_path,
                self._marker_name(ref.rollback_id),
                ref.rollback_id,
            )
            raise RollbackCorruptionError(
                "authoritative rollback payload was quarantined"
            ) from error

    def _store_payload_locked(
        self,
        ref: RollbackPayloadRef,
        payload: bytes,
        *,
        leaf_errors: tuple[RollbackLeafError, ...] = (),
        prior_receipt_digests: tuple[str, ...] = (),
        prior_receipt_refs: tuple[RollbackPayloadRef, ...] = (),
        request: Mapping[str, Any] | None = None,
    ) -> None:
        if ref.kind is RollbackPayloadKind.REQUEST:
            _validate_request_payload(payload, ref.rollback_id, ref.request_digest)
        else:
            _validate_receipt_payload(
                payload,
                ref=ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_receipt_digests,
                prior_receipt_refs=prior_receipt_refs,
                request=(
                    request
                    if request is not None
                    else (_ for _ in ()).throw(
                        RollbackValidationError(
                            "receipt payload request context is missing"
                        )
                    )
                ),
                store_root=self.root,
            )
        existing = self._read(ref.relative_path)
        expected = self._payload_bytes(ref, payload)
        if existing is None:
            self._create_immutable(ref.relative_path, expected)
        elif existing != expected:
            self._decode_payload_locked(
                ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_receipt_digests,
                prior_receipt_refs=prior_receipt_refs,
                request=request,
            )
            raise RollbackIdempotencyConflict(
                "authoritative rollback payload bytes diverge"
            )
        else:
            self._decode_payload_locked(
                ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_receipt_digests,
                prior_receipt_refs=prior_receipt_refs,
                request=request,
            )

    def _validate_payload_joins_locked(self, record: RollbackJournalRecord) -> None:
        request_raw = self._decode_payload_locked(record.request_payload_ref)
        request = _validate_request_payload(
            request_raw, record.rollback_id, record.request_digest
        )
        prior_digests: list[str] = []
        prior_refs: list[RollbackPayloadRef] = []
        for receipt in record.phase_receipts:
            for ref in receipt.receipt_refs:
                self._decode_payload_locked(
                    ref,
                    leaf_errors=receipt.leaf_errors,
                    prior_receipt_digests=tuple(prior_digests),
                    prior_receipt_refs=tuple(prior_refs),
                    request=request,
                )
            prior_digests.extend(receipt.receipt_digests)
            prior_refs.extend(receipt.receipt_refs)

    @staticmethod
    def _history_name(record: RollbackJournalRecord) -> str:
        return f"journal.{record.rollback_id}.g{record.generation:020d}.{record.digest[7:]}.history"

    @staticmethod
    def _commit_name(record: RollbackJournalRecord) -> str:
        return (
            f"journal.{record.rollback_id}.g{record.generation:020d}."
            f"{record.digest[7:]}.commit"
        )

    @staticmethod
    def _validate_journal_transition(
        chain: tuple[tuple[RollbackJournalRecord, bytes], ...],
        index: int,
    ) -> None:
        record = chain[index][0]
        if index == 0:
            return
        previous = chain[index - 1][0]
        if record.generation != previous.generation + 1:
            raise RollbackCorruptionError(
                "rollback journal publication generations are not adjacent"
            )
        if record.terminal_quarantine_refs == previous.terminal_quarantine_refs:
            if (
                record.revision != previous.revision + 1
                or record.phase_receipts[:-1] != previous.phase_receipts
            ):
                raise RollbackCorruptionError(
                    "rollback journal semantic transition is invalid"
                )
            return
        if (
            len(record.terminal_quarantine_refs)
            != len(previous.terminal_quarantine_refs) + 1
            or record.terminal_quarantine_refs[:-1] != previous.terminal_quarantine_refs
        ):
            raise RollbackCorruptionError(
                "rollback journal terminal quarantine chain diverged"
            )
        ref = record.terminal_quarantine_refs[-1]
        if (
            ref.successor_generation != previous.generation
            or ref.successor_record_digest != previous.digest
            or ref.predecessor_generation >= ref.successor_generation
        ):
            raise RollbackCorruptionError(
                "rollback journal terminal quarantine successor binding is invalid"
            )
        predecessor = chain[ref.predecessor_generation - 1][0]
        if (
            predecessor.digest != ref.predecessor_record_digest
            or record.rollback_id != predecessor.rollback_id
            or record.request_digest != predecessor.request_digest
            or record.request_payload_ref != predecessor.request_payload_ref
            or record.revision != predecessor.revision
            or record.phase is not predecessor.phase
            or record.phase_receipts != predecessor.phase_receipts
            or record.generation != ref.successor_generation + 1
        ):
            raise RollbackCorruptionError(
                "rollback journal terminal restoration payload is invalid"
            )

    def _read_exact_authority(self, name: str) -> bytes | None:
        self._validate_root()
        try:
            held = _HeldStoreFile.capture(self, name)
        except FileNotFoundError:
            return None
        try:
            held.revalidate(self)
            return held.raw
        finally:
            held.close()

    def _committed_history_locked(
        self, rollback_id: str
    ) -> tuple[tuple[RollbackJournalRecord, bytes], ...]:
        marker = self._marker_name(rollback_id)
        if self._blocked(marker):
            raise RollbackCorruptionError("rollback journal is quarantined")
        head_name = self._head_name(rollback_id)
        suspect = head_name
        try:
            head_raw = self._read_exact_authority(head_name)
            if head_raw is None:
                return ()
            head_record = self._decode(head_raw)
            if (
                head_record.rollback_id != rollback_id
                or head_record.generation > _MAX_ROLLBACK_HISTORY_GENERATIONS
            ):
                raise RollbackCorruptionError(
                    "rollback journal head authority is invalid"
                )
            suspect = self._history_name(head_record)
            history_raw = self._read_exact_authority(suspect)
            if history_raw != head_raw:
                raise RollbackCorruptionError(
                    "rollback journal head and history diverged"
                )
            commit_name = self._commit_name(head_record)
            commit_raw = self._read_exact_authority(commit_name)
            head_is_committed = commit_raw is not None
            if commit_raw is not None:
                suspect = commit_name
                self._verify_commit(
                    commit_raw,
                    identity=rollback_id,
                    generation=head_record.generation,
                    record_digest=head_record.digest,
                )

            reverse_chain: list[tuple[RollbackJournalRecord, bytes]] = []
            aggregate_bytes = 0

            def append_record(
                record: RollbackJournalRecord,
                raw: bytes,
            ) -> None:
                nonlocal aggregate_bytes
                if (
                    len(reverse_chain) >= _MAX_ROLLBACK_HISTORY_GENERATIONS
                    or aggregate_bytes + len(raw) > _MAX_ROLLBACK_HISTORY_BYTES
                ):
                    raise RollbackCorruptionError(
                        "rollback journal committed history bound is exhausted"
                    )
                aggregate_bytes += len(raw)
                reverse_chain.append((record, raw))

            current = head_record
            if head_is_committed:
                append_record(head_record, head_raw)
            while current.generation > 1:
                generation = current.generation - 1
                digest = current.previous_record_digest
                if digest is None:
                    raise RollbackCorruptionError(
                        "rollback journal predecessor authority is missing"
                    )
                suspect = self._journal_version_name(
                    rollback_id,
                    generation,
                    digest,
                    "history",
                )
                raw = self._read_exact_authority(suspect)
                if raw is None:
                    raise RollbackCorruptionError(
                        "rollback journal history disappeared"
                    )
                record = self._decode(raw)
                if (
                    record.rollback_id != rollback_id
                    or record.generation != generation
                    or record.digest != digest
                ):
                    raise RollbackCorruptionError(
                        "rollback journal history identity mismatch"
                    )
                commit_name = self._journal_version_name(
                    rollback_id,
                    generation,
                    digest,
                    "commit",
                )
                suspect = commit_name
                commit_raw = self._read_exact_authority(commit_name)
                if commit_raw is None:
                    raise RollbackCorruptionError("rollback journal commit disappeared")
                self._verify_commit(
                    commit_raw,
                    identity=rollback_id,
                    generation=generation,
                    record_digest=digest,
                )
                append_record(record, raw)
                current = record
            if current.previous_record_digest is not None:
                raise RollbackCorruptionError(
                    "rollback journal initial predecessor is invalid"
                )
            chain = tuple(reversed(reverse_chain))
            for index, (record, _) in enumerate(chain):
                expected_previous = None if index == 0 else chain[index - 1][0].digest
                if record.previous_record_digest != expected_previous:
                    suspect = self._history_name(record)
                    raise RollbackCorruptionError(
                        "rollback journal committed chain diverged"
                    )
                self._validate_journal_transition(chain, index)
            return chain
        except (
            OSError,
            RollbackValidationError,
            RollbackCorruptionError,
        ) as error:
            self._quarantine(suspect, marker, rollback_id)
            if suspect.endswith(".history"):
                message = "rollback journal history was quarantined"
            elif suspect.endswith(".commit"):
                message = "rollback journal commit was quarantined"
            else:
                message = "rollback journal committed authority was quarantined"
            raise RollbackCorruptionError(message) from error

    def _validate_terminal_ref_join_locked(
        self,
        record: RollbackJournalRecord,
    ) -> None:
        rollback_id = record.rollback_id
        try:
            anchors = self._terminal_quarantine_anchors()
            indexed_refs = tuple(
                ref for ref in anchors.values() if ref.rollback_id == rollback_id
            )
            expected_indexed_refs = tuple(
                sorted(
                    record.terminal_quarantine_refs,
                    key=self._terminal_anchor_key,
                )
            )
            if indexed_refs != expected_indexed_refs:
                raise RollbackCorruptionError(
                    "terminal rollback journal and anchor index diverged"
                )
            for ref in record.terminal_quarantine_refs:
                successor_name, tombstone_name = self._rollback_quarantine_names(
                    ref.transaction_id,
                    rollback_id,
                    ref.successor_record_digest,
                )
                if (
                    ref.successor_name != successor_name
                    or ref.tombstone_name != tombstone_name
                ):
                    raise RollbackCorruptionError(
                        "terminal rollback pair name binding is invalid"
                    )
                payload, successor_record, successor_raw = self._terminal_pair_evidence(
                    ref,
                    {
                        "successor": (successor_name, 0),
                        "tombstone": (tombstone_name, 0),
                    },
                )
                self._validate_live_terminal_anchor(
                    ref,
                    payload,
                    successor_record,
                    successor_raw,
                )
        except (
            OSError,
            RollbackValidationError,
            RollbackCorruptionError,
        ) as error:
            self._block_rollback_id(rollback_id)
            raise RollbackCorruptionError(
                "terminal rollback authority was quarantined"
            ) from error

    def _decode(self, raw: bytes) -> RollbackJournalRecord:
        return _journal_from_object(self._verify_signed(raw, "journal-record"))

    def _load_locked(
        self, rollback_id: str
    ) -> tuple[RollbackJournalRecord | None, bytes | None]:
        marker = self._marker_name(rollback_id)
        history = self._committed_history_locked(rollback_id)
        head = self._head_name(rollback_id)
        try:
            raw = self._read_exact_authority(head)
        except (RollbackCorruptionError, OSError) as error:
            self._quarantine(head, marker, rollback_id)
            raise RollbackCorruptionError("rollback journal was quarantined") from error
        if not history:
            if raw is not None:
                try:
                    record = self._decode(raw)
                    if (
                        record.rollback_id != rollback_id
                        or self._read_exact_authority(self._history_name(record)) != raw
                    ):
                        raise RollbackCorruptionError(
                            "uncommitted rollback journal head is invalid"
                        )
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                    OSError,
                ) as error:
                    self._quarantine(head, marker, rollback_id)
                    raise RollbackCorruptionError(
                        "rollback journal was quarantined"
                    ) from error
                os.unlink(head, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return None, None
        current, committed_raw = history[-1]
        self._verify_request_binding_locked(rollback_id, current.request_digest)
        if raw is None:
            self._quarantine(head, marker, rollback_id)
            raise RollbackCorruptionError("rollback journal committed head is missing")
        try:
            head_record = self._decode(raw)
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(head, marker, rollback_id)
            raise RollbackCorruptionError("rollback journal was quarantined") from error
        if head_record == current and raw == committed_raw:
            self._validate_terminal_ref_join_locked(current)
            self._validate_payload_joins_locked(current)
            return current, raw
        if (
            head_record.generation == current.generation + 1
            and head_record.previous_record_digest == current.digest
            and self._read_exact_authority(self._history_name(head_record)) == raw
            and self._read_exact_authority(self._commit_name(head_record)) is None
        ):
            self._validate_terminal_ref_join_locked(current)
            self._validate_payload_joins_locked(current)
            self._replace(head, committed_raw, raw)
            return current, committed_raw
        self._quarantine(head, marker, rollback_id)
        raise RollbackCorruptionError(
            "rollback journal signed head replay was quarantined"
        )

    def _persist_locked(
        self, record: RollbackJournalRecord, old_payload: bytes | None
    ) -> None:
        signed = self._signed_bytes("journal-record", record.canonical_object())
        self._publish_versioned(
            head_name=self._head_name(record.rollback_id),
            history_name=self._history_name(record),
            commit_name=self._commit_name(record),
            identity=record.rollback_id,
            generation=record.generation,
            record_digest=record.digest,
            signed_record=signed,
            old_head=old_payload,
        )

    def prepare(
        self,
        rollback_id: str,
        request_digest: str,
        request_payload: bytes,
    ) -> RollbackJournalRecord:
        source_capsules: list[_PinnedImmutableSource] = []
        try:
            _validate_request_payload_with_capsules(
                request_payload,
                rollback_id,
                request_digest,
                source_capsules,
            )
            return self._prepare_captured(
                rollback_id,
                request_digest,
                request_payload,
                source_capsules,
            )
        finally:
            for capsule in reversed(source_capsules):
                capsule.close()

    def _prepare_captured(
        self,
        rollback_id: str,
        request_digest: str,
        request_payload: bytes,
        source_capsules: Sequence[_PinnedImmutableSource],
    ) -> RollbackJournalRecord:
        _require_id(rollback_id, "rollback id")
        _require_digest(request_digest, "rollback request digest")
        request_ref = RollbackPayloadRef(
            rollback_id,
            request_digest,
            request_digest,
            RollbackPayloadKind.REQUEST,
            RollbackPhase.PREPARED,
            1,
            0,
            _payload_relative_path(
                rollback_id,
                RollbackPayloadKind.REQUEST,
                RollbackPhase.PREPARED,
                1,
                0,
                request_digest,
            ),
        )
        expected_binding = self._request_binding_bytes(rollback_id, request_digest)
        with self._exclusive():
            current, old_payload = self._load_locked(rollback_id)
            binding_name = self._request_name(rollback_id)
            existing_binding = self._read(binding_name)
            if existing_binding is not None:
                try:
                    self._verify_signed(existing_binding, "journal-request-binding")
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                ) as error:
                    self._quarantine(
                        binding_name,
                        self._marker_name(rollback_id),
                        rollback_id,
                    )
                    raise RollbackCorruptionError(
                        "rollback journal request binding was quarantined"
                    ) from error
                if existing_binding != expected_binding:
                    raise RollbackIdempotencyConflict(
                        "rollback id is already bound to a different request digest"
                    )
            _revalidate_source_capsules(source_capsules)
            if current is not None:
                if (
                    current.request_payload_ref != request_ref
                    or self._decode_payload_locked(request_ref) != request_payload
                ):
                    raise RollbackIdempotencyConflict(
                        "rollback id is already bound to different request bytes"
                    )
                return current
            with self._publication_transaction(
                lambda: _revalidate_source_capsules(source_capsules)
            ):
                if existing_binding is None:
                    self._create_immutable(binding_name, expected_binding)
                self._store_payload_locked(request_ref, request_payload)
                record = RollbackJournalRecord(
                    rollback_id,
                    request_digest,
                    request_ref,
                    1,
                    0,
                    RollbackPhase.PREPARED,
                    (),
                    None,
                )
                self._persist_locked(record, old_payload)
                return record

    def get(self, rollback_id: str) -> RollbackJournalRecord | None:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            return self._load_locked(rollback_id)[0]

    def get_request_ref(self, rollback_id: str) -> RollbackPayloadRef:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            return current.request_payload_ref

    def get_request(self, rollback_id: str) -> bytes:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            return self._decode_payload_locked(current.request_payload_ref)

    def advance(
        self,
        rollback_id: str,
        *,
        expected_generation: int,
        expected_revision: int,
        phase: RollbackPhase,
        receipt_digests: tuple[str, ...],
        receipt_payloads: tuple[bytes, ...],
        leaf_errors: tuple[RollbackLeafError, ...] = (),
    ) -> RollbackJournalRecord:
        source_capsules: list[_PinnedImmutableSource] = []
        try:
            return self._advance_captured(
                rollback_id,
                expected_generation=expected_generation,
                expected_revision=expected_revision,
                phase=phase,
                receipt_digests=receipt_digests,
                receipt_payloads=receipt_payloads,
                leaf_errors=leaf_errors,
                source_capsules=source_capsules,
            )
        finally:
            for capsule in reversed(source_capsules):
                capsule.close()

    def _advance_captured(
        self,
        rollback_id: str,
        *,
        expected_generation: int,
        expected_revision: int,
        phase: RollbackPhase,
        receipt_digests: tuple[str, ...],
        receipt_payloads: tuple[bytes, ...],
        leaf_errors: tuple[RollbackLeafError, ...] = (),
        source_capsules: list[_PinnedImmutableSource],
    ) -> RollbackJournalRecord:
        _require_id(rollback_id, "rollback id")
        _require_int(expected_generation, "expected journal generation", minimum=1)
        _require_int(expected_revision, "expected journal revision")
        if type(receipt_digests) is not tuple or type(receipt_payloads) is not tuple:
            raise RollbackValidationError(
                "receipt digests and payloads must be exact tuples"
            )
        if (
            not receipt_digests
            or len(receipt_digests) != len(receipt_payloads)
            or any(type(payload) is not bytes for payload in receipt_payloads)
        ):
            raise RollbackValidationError(
                "advance requires one exact payload per receipt digest"
            )
        if len(receipt_payloads) > _MAX_RECEIPT_PAYLOADS:
            raise RollbackValidationError(
                "phase receipt payload count exceeds fixed bound"
            )
        if (
            sum(len(payload) for payload in receipt_payloads)
            > _MAX_AGGREGATE_RECEIPT_PAYLOAD_BYTES
        ):
            raise RollbackValidationError(
                "aggregate phase receipt payload bytes exceed fixed bound"
            )
        for digest, payload in zip(receipt_digests, receipt_payloads, strict=True):
            _require_digest(digest, "phase receipt digest")
            if canonical_digest(payload) != digest:
                raise RollbackValidationError("phase receipt payload digest mismatch")
        with self._exclusive():
            current, old_payload = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            replay = (
                current.generation == expected_generation + 1
                and current.revision == expected_revision + 1
                and current.phase is phase
                and bool(current.phase_receipts)
            )
            if not replay and (
                current.generation != expected_generation
                or current.revision != expected_revision
            ):
                raise RollbackConflictError(
                    "rollback journal generation/revision compare-and-swap failed"
                )
            prior_receipts = (
                current.phase_receipts[:-1] if replay else current.phase_receipts
            )
            prior_digests = tuple(
                digest
                for prior_receipt in prior_receipts
                for digest in prior_receipt.receipt_digests
            )
            prior_refs = tuple(
                ref
                for prior_receipt in prior_receipts
                for ref in prior_receipt.receipt_refs
            )
            request_raw = self._decode_payload_locked(current.request_payload_ref)
            request = _validate_request_payload_with_capsules(
                request_raw,
                current.rollback_id,
                current.request_digest,
                source_capsules,
            )
            refs = tuple(
                RollbackPayloadRef(
                    current.rollback_id,
                    current.request_digest,
                    digest,
                    RollbackPayloadKind.PHASE_RECEIPT,
                    phase,
                    expected_generation + 1,
                    expected_revision + 1,
                    _payload_relative_path(
                        current.rollback_id,
                        RollbackPayloadKind.PHASE_RECEIPT,
                        phase,
                        expected_generation + 1,
                        expected_revision + 1,
                        digest,
                    ),
                )
                for digest in receipt_digests
            )
            receipt = RollbackPhaseReceipt(phase, receipt_digests, refs, leaf_errors)
            for ref, payload in zip(refs, receipt_payloads, strict=True):
                _validate_receipt_payload(
                    payload,
                    ref=ref,
                    leaf_errors=leaf_errors,
                    prior_receipt_digests=prior_digests,
                    prior_receipt_refs=prior_refs,
                    request=request,
                    store_root=self.root,
                )
            if replay:
                if current.phase_receipts[-1] != receipt:
                    raise RollbackIdempotencyConflict(
                        "rollback phase replay has divergent receipt bindings"
                    )
                for ref, payload in zip(refs, receipt_payloads, strict=True):
                    if (
                        self._decode_payload_locked(
                            ref,
                            leaf_errors=leaf_errors,
                            prior_receipt_digests=prior_digests,
                            prior_receipt_refs=prior_refs,
                            request=request,
                        )
                        != payload
                    ):
                        raise RollbackIdempotencyConflict(
                            "rollback phase replay has divergent receipt bytes"
                        )
                return current
            if current.phase in _TERMINAL_PHASES:
                raise RollbackConflictError("terminal rollback journal is absorbing")
            if phase is RollbackPhase.QUARANTINED:
                if not leaf_errors:
                    raise RollbackValidationError(
                        "terminal quarantine requires exact leaf errors"
                    )
            else:
                if leaf_errors:
                    raise RollbackValidationError(
                        "only terminal quarantine may persist leaf errors"
                    )
                expected_phase = _PHASE_ORDER[_PHASE_ORDER.index(current.phase) + 1]
                if phase is not expected_phase:
                    raise RollbackConflictError(
                        "rollback journal phase advance is not monotonic"
                    )
            if set(receipt_digests) & set(prior_digests):
                raise RollbackIdempotencyConflict(
                    "receipt payload digest is already committed"
                )
            record = RollbackJournalRecord(
                current.rollback_id,
                current.request_digest,
                current.request_payload_ref,
                current.generation + 1,
                current.revision + 1,
                phase,
                (*current.phase_receipts, receipt),
                current.digest,
                current.terminal_quarantine_refs,
            )
            projected_signed_record = self._signed_bytes(
                "journal-record", record.canonical_object()
            )
            if len(projected_signed_record) > _MAX_RECORD_BYTES:
                raise RollbackValidationError(
                    "projected rollback journal exceeds fixed size bound"
                )
            _revalidate_source_capsules(source_capsules)
            with self._publication_transaction(
                lambda: _revalidate_source_capsules(source_capsules)
            ):
                for ref, payload in zip(refs, receipt_payloads, strict=True):
                    self._store_payload_locked(
                        ref,
                        payload,
                        leaf_errors=leaf_errors,
                        prior_receipt_digests=prior_digests,
                        prior_receipt_refs=prior_refs,
                        request=request,
                    )
                self._persist_locked(record, old_payload)
                return record

    def _receipt_context_locked(
        self, record: RollbackJournalRecord, receipt_digest: str
    ) -> tuple[
        RollbackPayloadRef,
        tuple[RollbackLeafError, ...],
        tuple[str, ...],
        tuple[RollbackPayloadRef, ...],
    ]:
        _require_digest(receipt_digest, "phase receipt digest")
        prior: list[str] = []
        prior_refs: list[RollbackPayloadRef] = []
        for receipt in record.phase_receipts:
            for ref in receipt.receipt_refs:
                if ref.payload_digest == receipt_digest:
                    return (
                        ref,
                        receipt.leaf_errors,
                        tuple(prior),
                        tuple(prior_refs),
                    )
            prior.extend(receipt.receipt_digests)
            prior_refs.extend(receipt.receipt_refs)
        raise RollbackConflictError(
            "receipt payload is not committed by this rollback journal"
        )

    def get_receipt_ref(
        self, rollback_id: str, receipt_digest: str
    ) -> RollbackPayloadRef:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            ref, _, _, _ = self._receipt_context_locked(current, receipt_digest)
            return ref

    def get_receipt_payload(self, rollback_id: str, receipt_digest: str) -> bytes:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                raise RollbackConflictError("rollback journal is not prepared")
            ref, leaf_errors, prior_digests, prior_refs = self._receipt_context_locked(
                current, receipt_digest
            )
            request_raw = self._decode_payload_locked(current.request_payload_ref)
            request = _validate_request_payload(
                request_raw, current.rollback_id, current.request_digest
            )
            return self._decode_payload_locked(
                ref,
                leaf_errors=leaf_errors,
                prior_receipt_digests=prior_digests,
                prior_receipt_refs=prior_refs,
                request=request,
            )

    def history(self, rollback_id: str) -> tuple[RollbackJournalRecord, ...]:
        _require_id(rollback_id, "rollback id")
        with self._exclusive():
            current, _ = self._load_locked(rollback_id)
            if current is None:
                return ()
            return tuple(
                record for record, _ in self._committed_history_locked(rollback_id)
            )


class FilesystemActiveApprovedTupleStore(_PinnedSignedDirectory):
    _HEAD = "active-approved.head"
    _MARKER = "active-approved.blocked"

    def __init__(
        self,
        root: str | Path,
        *,
        authority_key: bytes,
        root_fd: int | None = None,
    ) -> None:
        super().__init__(
            root,
            authority_key=authority_key,
            domain="active-approved-tuple",
            root_fd=root_fd,
        )

    @staticmethod
    def _history_name(state: ActiveApprovedTupleState) -> str:
        return f"active-approved.g{state.generation:020d}.{state.digest[7:]}.history"

    @staticmethod
    def _commit_name(state: ActiveApprovedTupleState) -> str:
        return f"active-approved.g{state.generation:020d}.{state.digest[7:]}.commit"

    @staticmethod
    def _operation_name(operation_id: str) -> str:
        return f"active-operation.{_require_id(operation_id, 'active tuple operation id')}.request"

    def _committed_history_locked(
        self,
    ) -> tuple[tuple[ActiveApprovedTupleState, bytes], ...]:
        if self._blocked(self._MARKER):
            raise RollbackCorruptionError("active approved tuple is quarantined")
        history_pattern = re.compile(
            r"^active-approved\.g(\d{20})\.([0-9a-f]{64})\.history$"
        )
        commit_pattern = re.compile(
            r"^active-approved\.g(\d{20})\.([0-9a-f]{64})\.commit$"
        )
        histories: dict[
            tuple[int, str], tuple[ActiveApprovedTupleState, bytes, str]
        ] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith("active-approved.g") and item.endswith(".history")
        ):
            try:
                match = history_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError(
                        "active tuple history name is invalid"
                    )
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("active tuple history disappeared")
                state = self._decode(raw)
                key = (int(match.group(1)), "sha256:" + match.group(2))
                if (
                    state.generation != key[0]
                    or state.digest != key[1]
                    or key in histories
                ):
                    raise RollbackCorruptionError(
                        "active tuple history identity mismatch"
                    )
                histories[key] = (state, raw, name)
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, self._MARKER, "active-approved")
                raise RollbackCorruptionError(
                    "active tuple history was quarantined"
                ) from error
        committed: dict[int, tuple[ActiveApprovedTupleState, bytes]] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith("active-approved.g") and item.endswith(".commit")
        ):
            try:
                match = commit_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError("active tuple commit name is invalid")
                generation = int(match.group(1))
                digest = "sha256:" + match.group(2)
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("active tuple commit disappeared")
                self._verify_commit(
                    raw,
                    identity="active-approved",
                    generation=generation,
                    record_digest=digest,
                )
                history = histories.get((generation, digest))
                if history is None or generation in committed:
                    raise RollbackCorruptionError(
                        "active tuple committed tip is invalid"
                    )
                committed[generation] = (history[0], history[1])
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, self._MARKER, "active-approved")
                raise RollbackCorruptionError(
                    "active tuple commit was quarantined"
                ) from error
        if not committed:
            return ()
        generations = tuple(sorted(committed))
        if generations != tuple(range(1, generations[-1] + 1)):
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError("active tuple committed history has a gap")
        chain = tuple(committed[generation] for generation in generations)
        for index, (state, _) in enumerate(chain):
            expected_previous = None if index == 0 else chain[index - 1][0].digest
            if state.previous_state_digest != expected_previous:
                self._quarantine(
                    self._history_name(state), self._MARKER, "active-approved"
                )
                raise RollbackCorruptionError("active tuple committed chain diverged")
            binding_name = self._operation_name(state.operation_id)
            expected_binding = self._signed_bytes(
                "active-operation-binding",
                {
                    "approved_tuple_digest": state.approved_tuple.tuple_digest,
                    "expected_generation": state.generation - 1
                    if state.generation > 1
                    else None,
                    "operation_id": state.operation_id,
                    "schema_version": "bb.rl.phase5.active-operation-binding.v1",
                },
            )
            try:
                binding = self._read(binding_name)
                if binding is None:
                    raise RollbackCorruptionError("active operation binding is missing")
                self._verify_signed(binding, "active-operation-binding")
                if binding != expected_binding:
                    raise RollbackCorruptionError("active operation binding diverged")
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(binding_name, self._MARKER, "active-approved")
                raise RollbackCorruptionError(
                    "active operation binding was quarantined"
                ) from error
        return chain

    def _decode(self, raw: bytes) -> ActiveApprovedTupleState:
        return _active_state_from_object(self._verify_signed(raw, "active-state"))

    def _load_locked(self) -> tuple[ActiveApprovedTupleState | None, bytes | None]:
        history = self._committed_history_locked()
        try:
            raw = self._read(self._HEAD)
        except (RollbackCorruptionError, OSError) as error:
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError(
                "active approved tuple was quarantined"
            ) from error
        if not history:
            if raw is not None:
                try:
                    state = self._decode(raw)
                    if self._read(self._history_name(state)) != raw:
                        raise RollbackCorruptionError(
                            "uncommitted active tuple head is invalid"
                        )
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                    OSError,
                ) as error:
                    self._quarantine(self._HEAD, self._MARKER, "active-approved")
                    raise RollbackCorruptionError(
                        "active approved tuple was quarantined"
                    ) from error
                os.unlink(self._HEAD, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return None, None
        current, committed_raw = history[-1]
        if raw is None:
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError(
                "active approved tuple committed head is missing"
            )
        try:
            head_state = self._decode(raw)
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(self._HEAD, self._MARKER, "active-approved")
            raise RollbackCorruptionError(
                "active approved tuple was quarantined"
            ) from error
        if head_state == current and raw == committed_raw:
            return current, raw
        if (
            head_state.generation == current.generation + 1
            and head_state.previous_state_digest == current.digest
            and self._read(self._history_name(head_state)) == raw
            and self._read(self._commit_name(head_state)) is None
        ):
            self._replace(self._HEAD, committed_raw, raw)
            return current, committed_raw
        self._quarantine(self._HEAD, self._MARKER, "active-approved")
        raise RollbackCorruptionError("active tuple signed head replay was quarantined")

    def get(self) -> ActiveApprovedTupleState | None:
        with self._exclusive():
            return self._load_locked()[0]

    def compare_and_swap(
        self,
        expected_generation: int | None,
        approved_tuple: ActiveApprovedTuple,
        operation_id: str,
    ) -> ActiveApprovedTupleState:
        if expected_generation is not None:
            _require_int(expected_generation, "expected active generation", minimum=1)
        if type(approved_tuple) is not ActiveApprovedTuple:
            raise RollbackValidationError("active approved tuple must be exact")
        _require_id(operation_id, "active tuple operation id")
        binding = self._signed_bytes(
            "active-operation-binding",
            {
                "approved_tuple_digest": approved_tuple.tuple_digest,
                "expected_generation": expected_generation,
                "operation_id": operation_id,
                "schema_version": "bb.rl.phase5.active-operation-binding.v1",
            },
        )
        with self._exclusive():
            current, old_payload = self._load_locked()
            binding_name = self._operation_name(operation_id)
            existing_binding = self._read(binding_name)
            if existing_binding is not None:
                try:
                    self._verify_signed(existing_binding, "active-operation-binding")
                except (RollbackValidationError, RollbackCorruptionError) as error:
                    self._quarantine(binding_name, self._MARKER, "active-approved")
                    raise RollbackCorruptionError(
                        "active operation binding was quarantined"
                    ) from error
                if existing_binding != binding:
                    raise RollbackIdempotencyConflict(
                        "active tuple operation id is bound to a different request"
                    )
                for state, _ in self._committed_history_locked():
                    if state.operation_id == operation_id:
                        return state
            actual_generation = current.generation if current is not None else None
            if actual_generation != expected_generation:
                raise RollbackConflictError(
                    "active approved tuple generation compare-and-swap failed"
                )
            if existing_binding is None:
                self._create_immutable(binding_name, binding)
            state = ActiveApprovedTupleState(
                generation=1 if current is None else current.generation + 1,
                approved_tuple=approved_tuple,
                operation_id=operation_id,
                previous_state_digest=current.digest if current is not None else None,
            )
            signed = self._signed_bytes("active-state", state.canonical_object())
            self._publish_versioned(
                head_name=self._HEAD,
                history_name=self._history_name(state),
                commit_name=self._commit_name(state),
                identity="active-approved",
                generation=state.generation,
                record_digest=state.digest,
                signed_record=signed,
                old_head=old_payload,
            )
            return state

    def history(self) -> tuple[ActiveApprovedTupleHistoryEntry, ...]:
        with self._exclusive():
            current, _ = self._load_locked()
            if current is None:
                return ()
            return tuple(
                ActiveApprovedTupleHistoryEntry(state, state.digest)
                for state, _ in self._committed_history_locked()
            )


@dataclass(frozen=True, slots=True)
class _QuarantineOperation:
    rollback_id: str
    cause_digest: str
    root_digests: tuple[str, ...]
    affected_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id(self.rollback_id, "rollback id")
        _require_digest(self.cause_digest, "quarantine cause digest")
        for name, values in (
            ("quarantine roots", self.root_digests),
            ("quarantine affected objects", self.affected_digests),
        ):
            if (
                type(values) is not tuple
                or not values
                or values != tuple(sorted(set(values)))
            ):
                raise RollbackValidationError(f"{name} must be unique and sorted")
            for digest in values:
                _require_digest(digest, name)
        if not set(self.root_digests).issubset(self.affected_digests):
            raise RollbackValidationError(
                "quarantine affected objects must include every root"
            )

    def canonical_object(self) -> dict[str, Any]:
        return {
            "affected_digests": list(self.affected_digests),
            "cause_digest": self.cause_digest,
            "rollback_id": self.rollback_id,
            "root_digests": list(self.root_digests),
            "schema_version": "bb.rl.phase5.dependent-quarantine-operation.v2",
        }

    @property
    def digest(self) -> str:
        return canonical_digest(canonical_json_bytes(self.canonical_object()))


class FilesystemDependentQuarantineStore(_PinnedSignedDirectory):
    _GLOBAL_MARKER = "dependent-index.blocked"

    def __init__(
        self,
        root: str | Path,
        *,
        authority_key: bytes,
        root_fd: int | None = None,
    ) -> None:
        super().__init__(
            root,
            authority_key=authority_key,
            domain="dependent-quarantine",
            root_fd=root_fd,
        )

    @staticmethod
    def _key(object_ref: ImmutableObjectRef) -> str:
        if type(object_ref) is not ImmutableObjectRef:
            raise RollbackValidationError("dependent object ref must be exact")
        return object_ref.identity_digest[7:]

    @classmethod
    def _head_name(cls, object_ref: ImmutableObjectRef) -> str:
        return f"dependent.{cls._key(object_ref)}.head"

    @classmethod
    def _marker_name(cls, object_ref: ImmutableObjectRef) -> str:
        return f"dependent.{cls._key(object_ref)}.blocked"

    @classmethod
    def _history_name(cls, record: DependentOwnershipRecord) -> str:
        return (
            f"dependent.{cls._key(record.ownership.object_ref)}."
            f"g{record.generation:020d}.{record.digest[7:]}.history"
        )

    @classmethod
    def _commit_name(cls, record: DependentOwnershipRecord) -> str:
        return (
            f"dependent.{cls._key(record.ownership.object_ref)}."
            f"g{record.generation:020d}.{record.digest[7:]}.commit"
        )

    @staticmethod
    def _operation_name(rollback_id: str) -> str:
        return f"quarantine.{_require_id(rollback_id, 'rollback id')}.request"

    @staticmethod
    def _operation_marker(rollback_id: str) -> str:
        return f"quarantine.{_require_id(rollback_id, 'rollback id')}.blocked"

    @staticmethod
    def _operation_complete_name(rollback_id: str) -> str:
        return f"quarantine.{_require_id(rollback_id, 'rollback id')}.complete"

    @staticmethod
    def _registration_name(registration_id: str) -> str:
        return (
            f"registration."
            f"{_require_id(registration_id, 'dependent registration id')}.request"
        )

    def _registration_binding_bytes(self, ownership: DependentOwnership) -> bytes:
        return self._signed_bytes(
            "dependent-registration-binding",
            {
                "object_identity_digest": ownership.object_ref.identity_digest,
                "ownership_digest": ownership.digest,
                "registration_id": ownership.registration_id,
                "schema_version": "bb.rl.phase5.dependent-registration-binding.v1",
            },
        )

    def _decode(self, raw: bytes) -> DependentOwnershipRecord:
        return _dependent_record_from_object(
            self._verify_signed(raw, "dependent-record")
        )

    def _assert_global_unblocked(self) -> None:
        if self._blocked(self._GLOBAL_MARKER):
            raise RollbackCorruptionError("dependent ownership index is quarantined")

    def _committed_history_locked(
        self, object_ref: ImmutableObjectRef
    ) -> tuple[tuple[DependentOwnershipRecord, bytes], ...]:
        self._assert_global_unblocked()
        marker = self._marker_name(object_ref)
        if self._blocked(marker):
            raise RollbackCorruptionError("dependent ownership record is quarantined")
        key = self._key(object_ref)
        prefix = f"dependent.{key}.g"
        history_pattern = re.compile(
            rf"^dependent\.{key}\.g(\d{{20}})\.([0-9a-f]{{64}})\.history$"
        )
        commit_pattern = re.compile(
            rf"^dependent\.{key}\.g(\d{{20}})\.([0-9a-f]{{64}})\.commit$"
        )
        histories: dict[
            tuple[int, str], tuple[DependentOwnershipRecord, bytes, str]
        ] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith(prefix) and item.endswith(".history")
        ):
            try:
                match = history_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError("dependent history name is invalid")
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("dependent history disappeared")
                record = self._decode(raw)
                record_key = (int(match.group(1)), "sha256:" + match.group(2))
                if (
                    record.ownership.object_ref != object_ref
                    or record.generation != record_key[0]
                    or record.digest != record_key[1]
                    or record_key in histories
                ):
                    raise RollbackCorruptionError("dependent history identity mismatch")
                histories[record_key] = (record, raw, name)
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, marker, object_ref.identity_digest)
                raise RollbackCorruptionError(
                    "dependent history was quarantined"
                ) from error
        committed: dict[int, tuple[DependentOwnershipRecord, bytes]] = {}
        for name in sorted(
            item
            for item in self._bounded_root_names()
            if item.startswith(prefix) and item.endswith(".commit")
        ):
            try:
                match = commit_pattern.fullmatch(name)
                if match is None:
                    raise RollbackCorruptionError("dependent commit name is invalid")
                generation = int(match.group(1))
                digest = "sha256:" + match.group(2)
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError("dependent commit disappeared")
                self._verify_commit(
                    raw,
                    identity=object_ref.identity_digest,
                    generation=generation,
                    record_digest=digest,
                )
                history = histories.get((generation, digest))
                if history is None or generation in committed:
                    raise RollbackCorruptionError("dependent committed tip is invalid")
                committed[generation] = (history[0], history[1])
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, marker, object_ref.identity_digest)
                raise RollbackCorruptionError(
                    "dependent commit was quarantined"
                ) from error
        if not committed:
            return ()
        latest_generation = max(committed)
        latest = committed[latest_generation]
        chain: list[tuple[DependentOwnershipRecord, bytes]] = [latest]
        cursor = latest[0]
        while cursor.previous_record_digest is not None:
            predecessor = histories.get(
                (cursor.generation - 1, cursor.previous_record_digest)
            )
            if predecessor is None:
                self._quarantine(
                    self._history_name(cursor), marker, object_ref.identity_digest
                )
                raise RollbackCorruptionError(
                    "dependent committed predecessor is missing"
                )
            chain.append((predecessor[0], predecessor[1]))
            cursor = predecessor[0]
        chain.reverse()
        if (
            chain[0][0].generation != 1
            or chain[-1][0].generation != latest_generation
            or any(record.ownership != chain[0][0].ownership for record, _ in chain)
        ):
            self._quarantine(
                self._history_name(latest[0]), marker, object_ref.identity_digest
            )
            raise RollbackCorruptionError("dependent committed chain diverged")
        by_generation = {record.generation: record for record, _ in chain}
        if any(
            generation not in by_generation or by_generation[generation] != record
            for generation, (record, _) in committed.items()
        ):
            self._quarantine(
                self._history_name(latest[0]), marker, object_ref.identity_digest
            )
            raise RollbackCorruptionError("dependent committed history forked")
        ownership = chain[0][0].ownership
        binding_name = self._registration_name(ownership.registration_id)
        expected_binding = self._registration_binding_bytes(ownership)
        try:
            binding = self._read(binding_name)
            if binding is None:
                raise RollbackCorruptionError(
                    "dependent registration binding is missing"
                )
            self._verify_signed(binding, "dependent-registration-binding")
            if binding != expected_binding:
                raise RollbackCorruptionError("dependent registration binding diverged")
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(binding_name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError(
                "dependent registration binding was quarantined"
            ) from error
        return tuple(chain)

    def _load_locked(
        self, object_ref: ImmutableObjectRef
    ) -> tuple[DependentOwnershipRecord | None, bytes | None]:
        marker = self._marker_name(object_ref)
        history = self._committed_history_locked(object_ref)
        name = self._head_name(object_ref)
        try:
            raw = self._read(name)
        except (RollbackCorruptionError, OSError) as error:
            self._quarantine(name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError(
                "dependent ownership record was quarantined"
            ) from error
        if not history:
            if raw is not None:
                try:
                    record = self._decode(raw)
                    if (
                        record.ownership.object_ref != object_ref
                        or self._read(self._history_name(record)) != raw
                    ):
                        raise RollbackCorruptionError(
                            "uncommitted dependent head is invalid"
                        )
                except (
                    RollbackValidationError,
                    RollbackCorruptionError,
                    OSError,
                ) as error:
                    self._quarantine(name, marker, object_ref.identity_digest)
                    raise RollbackCorruptionError(
                        "dependent ownership record was quarantined"
                    ) from error
                os.unlink(name, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            return None, None
        current, committed_raw = history[-1]
        if raw is None:
            self._quarantine(name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError("dependent committed head is missing")
        try:
            head_record = self._decode(raw)
        except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
            self._quarantine(name, marker, object_ref.identity_digest)
            raise RollbackCorruptionError(
                "dependent ownership record was quarantined"
            ) from error
        if head_record == current and raw == committed_raw:
            return current, raw
        if (
            head_record.generation > current.generation
            and head_record.ownership == current.ownership
            and self._read(self._history_name(head_record)) == raw
            and self._read(self._commit_name(head_record)) is None
        ):
            self._replace(name, committed_raw, raw)
            return current, committed_raw
        self._quarantine(name, marker, object_ref.identity_digest)
        raise RollbackCorruptionError("dependent signed head replay was quarantined")

    def _publish_records_locked(
        self,
        records: Sequence[DependentOwnershipRecord],
        old_payload: bytes | None,
    ) -> DependentOwnershipRecord:
        if not records:
            raise RollbackValidationError("dependent publication requires records")
        signed_by_digest: dict[str, bytes] = {}
        for record in records:
            signed = self._signed_bytes("dependent-record", record.canonical_object())
            signed_by_digest[record.digest] = signed
            self._create_immutable(self._history_name(record), signed)
        final = records[-1]
        self._publish_versioned(
            head_name=self._head_name(final.ownership.object_ref),
            history_name=self._history_name(final),
            commit_name=self._commit_name(final),
            identity=final.ownership.object_ref.identity_digest,
            generation=final.generation,
            record_digest=final.digest,
            signed_record=signed_by_digest[final.digest],
            old_head=old_payload,
        )
        return final

    def _all_locked(self) -> tuple[DependentOwnershipRecord, ...]:
        self._assert_global_unblocked()
        names = sorted(self._bounded_root_names())
        for name in names:
            if name.startswith("dependent.") and name.endswith(".blocked"):
                if self._blocked(name):
                    raise RollbackCorruptionError(
                        "dependent ownership index contains a quarantined identity"
                    )
        records: list[DependentOwnershipRecord] = []
        head_pattern = re.compile(r"^dependent\.([0-9a-f]{64})\.head$")
        for name in (
            item
            for item in names
            if item.startswith("dependent.") and item.endswith(".head")
        ):
            match = head_pattern.fullmatch(name)
            if match is None:
                self._quarantine(name, self._GLOBAL_MARKER, "dependent-index")
                raise RollbackCorruptionError(
                    "dependent ownership filename was quarantined"
                )
            try:
                raw = self._read(name)
                if raw is None:
                    continue
                decoded = self._decode(raw)
                if self._key(decoded.ownership.object_ref) != match.group(1):
                    raise RollbackCorruptionError(
                        "dependent filename identity mismatch"
                    )
                record, _ = self._load_locked(decoded.ownership.object_ref)
                if record is None:
                    raise RollbackCorruptionError(
                        "dependent committed record disappeared"
                    )
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                identity = "sha256:" + match.group(1)
                marker = f"dependent.{match.group(1)}.blocked"
                self._quarantine(name, marker, identity)
                raise RollbackCorruptionError(
                    "dependent ownership index was quarantined"
                ) from error
            records.append(record)
        return tuple(records)

    def _operation_from_raw(self, raw: bytes, rollback_id: str) -> _QuarantineOperation:
        payload = _require_object(
            self._verify_signed(raw, "quarantine-operation"),
            frozenset(
                (
                    "affected_digests",
                    "cause_digest",
                    "rollback_id",
                    "root_digests",
                    "schema_version",
                )
            ),
            "dependent quarantine operation",
        )
        if (
            payload["schema_version"]
            != "bb.rl.phase5.dependent-quarantine-operation.v2"
            or payload["rollback_id"] != rollback_id
        ):
            raise RollbackCorruptionError(
                "dependent quarantine operation identity mismatch"
            )
        return _QuarantineOperation(
            payload["rollback_id"],
            payload["cause_digest"],
            tuple(_require_tuple(payload["root_digests"], "quarantine roots")),
            tuple(
                _require_tuple(
                    payload["affected_digests"], "quarantine affected objects"
                )
            ),
        )

    def _operations_locked(
        self,
    ) -> tuple[tuple[_QuarantineOperation, bool], ...]:
        names = sorted(self._bounded_root_names())
        blocked_pattern = re.compile(
            r"^quarantine\.([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.blocked$"
        )
        for name in names:
            match = blocked_pattern.fullmatch(name)
            if match is not None and self._blocked(name):
                raise RollbackCorruptionError(
                    "dependent quarantine operation is blocked"
                )
        request_pattern = re.compile(
            r"^quarantine\.([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.request$"
        )
        operations: list[tuple[_QuarantineOperation, bool]] = []
        for name in (
            item
            for item in names
            if item.startswith("quarantine.") and item.endswith(".request")
        ):
            match = request_pattern.fullmatch(name)
            if match is None:
                self._quarantine(name, self._GLOBAL_MARKER, "dependent-index")
                raise RollbackCorruptionError(
                    "dependent quarantine request filename is invalid"
                )
            rollback_id = match.group(1)
            try:
                raw = self._read(name)
                if raw is None:
                    raise RollbackCorruptionError(
                        "dependent quarantine request disappeared"
                    )
                operation = self._operation_from_raw(raw, rollback_id)
                complete_name = self._operation_complete_name(rollback_id)
                complete_raw = self._read(complete_name)
                complete = complete_raw is not None
                if complete_raw is not None:
                    payload = _require_object(
                        self._verify_signed(
                            complete_raw, "quarantine-operation-complete"
                        ),
                        frozenset(
                            ("operation_digest", "rollback_id", "schema_version")
                        ),
                        "dependent quarantine completion",
                    )
                    if payload != {
                        "operation_digest": operation.digest,
                        "rollback_id": rollback_id,
                        "schema_version": "bb.rl.phase5.dependent-quarantine-complete.v1",
                    }:
                        raise RollbackCorruptionError(
                            "dependent quarantine completion mismatch"
                        )
            except (RollbackValidationError, RollbackCorruptionError, OSError) as error:
                self._quarantine(name, self._operation_marker(rollback_id), rollback_id)
                raise RollbackCorruptionError(
                    "dependent quarantine operation was quarantined"
                ) from error
            operations.append((operation, complete))
        return tuple(operations)

    def register(self, ownership: DependentOwnership) -> DependentOwnershipRecord:
        if type(ownership) is not DependentOwnership:
            raise RollbackValidationError("dependent ownership must be exact")
        binding_name = self._registration_name(ownership.registration_id)
        binding = self._registration_binding_bytes(ownership)
        with self._exclusive():
            self._assert_global_unblocked()
            existing_binding = self._read(binding_name)
            if existing_binding is not None:
                try:
                    self._verify_signed(
                        existing_binding, "dependent-registration-binding"
                    )
                except (RollbackValidationError, RollbackCorruptionError) as error:
                    self._quarantine(
                        binding_name, self._GLOBAL_MARKER, "dependent-index"
                    )
                    raise RollbackCorruptionError(
                        "dependent registration binding was quarantined"
                    ) from error
                if existing_binding != binding:
                    raise RollbackIdempotencyConflict(
                        "dependent registration id is bound to different ownership"
                    )
            current, old_payload = self._load_locked(ownership.object_ref)
            if current is not None and current.ownership != ownership:
                raise RollbackConflictError(
                    "immutable object is already bound to different ownership"
                )
            all_records = self._all_locked()
            by_identity = {
                item.ownership.object_ref.identity_digest: item for item in all_records
            }
            operations = self._operations_locked()
            inherited: dict[tuple[str, str], tuple[str, ...]] = {}
            for parent in ownership.parent_refs:
                parent_state = by_identity.get(parent.identity_digest)
                if parent_state is None:
                    raise RollbackConflictError(
                        "dependent parent must be registered before its child"
                    )
                for receipt in parent_state.quarantine_receipts:
                    inherited[(receipt.rollback_id, receipt.cause_digest)] = (
                        receipt.causal_root_digests
                    )
                for operation, _ in operations:
                    if parent.identity_digest in operation.affected_digests:
                        inherited[(operation.rollback_id, operation.cause_digest)] = (
                            operation.root_digests
                        )
            if existing_binding is None:
                self._create_immutable(binding_name, binding)
            record = (
                current
                if current is not None
                else DependentOwnershipRecord(1, ownership, True, True, (), None)
            )
            publications: list[DependentOwnershipRecord] = []
            if current is None:
                publications.append(record)
            for rollback_id, cause_digest in sorted(inherited):
                if any(
                    item.rollback_id == rollback_id
                    and item.cause_digest == cause_digest
                    for item in record.quarantine_receipts
                ):
                    continue
                receipt = DependentQuarantineReceipt(
                    rollback_id,
                    cause_digest,
                    ownership.object_ref,
                    ownership.digest,
                    inherited[(rollback_id, cause_digest)],
                    record.generation + 1,
                )
                record = DependentOwnershipRecord(
                    record.generation + 1,
                    ownership,
                    False,
                    False,
                    (*record.quarantine_receipts, receipt),
                    record.digest,
                )
                publications.append(record)
            if not publications:
                return record
            return self._publish_records_locked(publications, old_payload)

    def get(self, object_ref: ImmutableObjectRef) -> DependentOwnershipRecord | None:
        with self._exclusive():
            return self._load_locked(object_ref)[0]

    def list_owned(
        self,
        *,
        approved_tuple_digest: str | None = None,
        episode_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[DependentOwnershipRecord, ...]:
        if approved_tuple_digest is not None:
            _require_digest(approved_tuple_digest, "approved tuple digest")
        if episode_id is not None:
            _require_id(episode_id, "episode id")
        if run_id is not None:
            _require_id(run_id, "run id")
        with self._exclusive():
            self._operations_locked()
            return tuple(
                record
                for record in self._all_locked()
                if (
                    approved_tuple_digest is None
                    or record.ownership.approved_tuple_digest == approved_tuple_digest
                )
                and (episode_id is None or record.ownership.episode_id == episode_id)
                and (run_id is None or record.ownership.run_id == run_id)
            )

    def quarantine_causal(
        self,
        rollback_id: str,
        cause_digest: str,
        root_refs: tuple[ImmutableObjectRef, ...],
    ) -> tuple[DependentQuarantineReceipt, ...]:
        _require_id(rollback_id, "rollback id")
        _require_digest(cause_digest, "dependent quarantine cause digest")
        if (
            type(root_refs) is not tuple
            or not root_refs
            or any(type(item) is not ImmutableObjectRef for item in root_refs)
        ):
            raise RollbackValidationError("causal quarantine roots must be exact")
        root_digests = tuple(sorted({item.identity_digest for item in root_refs}))
        if len(root_digests) != len(root_refs):
            raise RollbackValidationError("causal quarantine roots must be unique")
        with self._exclusive():
            if self._blocked(self._operation_marker(rollback_id)):
                raise RollbackCorruptionError(
                    "dependent quarantine operation is blocked"
                )
            records = self._all_locked()
            by_identity = {
                record.ownership.object_ref.identity_digest: record
                for record in records
            }
            missing = set(root_digests) - set(by_identity)
            if missing:
                raise RollbackConflictError("causal quarantine root is not registered")
            affected = set(root_digests)
            changed = True
            while changed:
                changed = False
                for identity, record in by_identity.items():
                    if identity in affected:
                        continue
                    if {
                        parent.identity_digest
                        for parent in record.ownership.parent_refs
                    } & affected:
                        affected.add(identity)
                        changed = True
            computed_operation = _QuarantineOperation(
                rollback_id,
                cause_digest,
                root_digests,
                tuple(sorted(affected)),
            )
            operation_name = self._operation_name(rollback_id)
            existing_operation = self._read(operation_name)
            if existing_operation is None:
                operation = computed_operation
                self._create_immutable(
                    operation_name,
                    self._signed_bytes(
                        "quarantine-operation", operation.canonical_object()
                    ),
                )
            else:
                try:
                    operation = self._operation_from_raw(
                        existing_operation, rollback_id
                    )
                except (RollbackValidationError, RollbackCorruptionError) as error:
                    self._quarantine(
                        operation_name,
                        self._operation_marker(rollback_id),
                        rollback_id,
                    )
                    raise RollbackCorruptionError(
                        "dependent quarantine operation was quarantined"
                    ) from error
                if (
                    operation.cause_digest != cause_digest
                    or operation.root_digests != root_digests
                ):
                    raise RollbackIdempotencyConflict(
                        "rollback id is bound to a different dependent quarantine request"
                    )
                if set(operation.affected_digests) - set(by_identity):
                    self._quarantine(
                        operation_name,
                        self._operation_marker(rollback_id),
                        rollback_id,
                    )
                    raise RollbackCorruptionError(
                        "dependent quarantine affected ownership is missing"
                    )
            receipts: list[DependentQuarantineReceipt] = []
            for identity in operation.affected_digests:
                current = by_identity[identity]
                prior = next(
                    (
                        item
                        for item in current.quarantine_receipts
                        if item.rollback_id == rollback_id
                        and item.cause_digest == cause_digest
                    ),
                    None,
                )
                if prior is not None:
                    receipts.append(prior)
                    continue
                receipt = DependentQuarantineReceipt(
                    rollback_id,
                    cause_digest,
                    current.ownership.object_ref,
                    current.ownership.digest,
                    root_digests,
                    current.generation + 1,
                )
                updated = DependentOwnershipRecord(
                    current.generation + 1,
                    current.ownership,
                    False,
                    False,
                    (*current.quarantine_receipts, receipt),
                    current.digest,
                )
                old_payload = self._read(self._head_name(current.ownership.object_ref))
                if old_payload is None:
                    raise RollbackCorruptionError(
                        "dependent ownership disappeared during quarantine"
                    )
                self._publish_records_locked((updated,), old_payload)
                by_identity[identity] = updated
                receipts.append(receipt)
            complete_payload = self._signed_bytes(
                "quarantine-operation-complete",
                {
                    "operation_digest": operation.digest,
                    "rollback_id": rollback_id,
                    "schema_version": "bb.rl.phase5.dependent-quarantine-complete.v1",
                },
            )
            self._create_immutable(
                self._operation_complete_name(rollback_id),
                complete_payload,
            )
            return tuple(receipts)

    def _assert_eligible(self, object_ref: ImmutableObjectRef, *, export: bool) -> None:
        with self._exclusive():
            record, _ = self._load_locked(object_ref)
            if record is None:
                raise DependentIneligibleError("dependent object is not registered")
            identity = object_ref.identity_digest
            if any(
                not complete and identity in operation.affected_digests
                for operation, complete in self._operations_locked()
            ):
                raise DependentIneligibleError(
                    "dependent object has an incomplete quarantine intent"
                )
            eligible = record.export_eligible if export else record.promotion_eligible
            if not eligible:
                purpose = "export" if export else "promotion"
                raise DependentIneligibleError(
                    f"dependent object is quarantined for {purpose}"
                )

    def assert_promotion_eligible(self, object_ref: ImmutableObjectRef) -> None:
        self._assert_eligible(object_ref, export=False)

    def assert_export_eligible(self, object_ref: ImmutableObjectRef) -> None:
        self._assert_eligible(object_ref, export=True)

    @contextmanager
    def read_fence(
        self,
    ) -> Iterator[tuple[DependentOwnershipRecord, ...]]:
        with self._exclusive():
            operations = self._operations_locked()
            if any(not complete for _, complete in operations):
                raise DependentIneligibleError(
                    "dependent quarantine operation is incomplete"
                )
            yield self._all_locked()


def _immutable_ref_from_object(value: object) -> ImmutableObjectRef:
    item = _require_object(
        value, frozenset(("digest", "reference")), "immutable object reference"
    )
    return ImmutableObjectRef(item["reference"], item["digest"])


def _approved_tuple_ref_from_object(value: object) -> ApprovedTupleRef:
    item = _require_object(value, frozenset(("object_ref", "role")), "tuple reference")
    return ApprovedTupleRef(
        item["role"], _immutable_ref_from_object(item["object_ref"])
    )


def _active_tuple_from_object(value: object) -> ActiveApprovedTuple:
    item = _require_object(
        value,
        frozenset(("immutable_refs", "schema_version", "tuple_digest")),
        "active approved tuple",
    )
    return ActiveApprovedTuple(
        tuple(
            _approved_tuple_ref_from_object(ref)
            for ref in _require_tuple(item["immutable_refs"], "active tuple refs")
        ),
        item["tuple_digest"],
        item["schema_version"],
    )


def _payload_ref_from_object(value: object) -> RollbackPayloadRef:
    item = _require_object(
        value,
        frozenset(
            (
                "journal_generation",
                "journal_revision",
                "kind",
                "payload_digest",
                "phase",
                "relative_path",
                "request_digest",
                "rollback_id",
                "schema_version",
            )
        ),
        "rollback payload ref",
    )
    try:
        kind = RollbackPayloadKind(item["kind"])
        phase = RollbackPhase(item["phase"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("rollback payload ref enum is invalid") from error
    return RollbackPayloadRef(
        item["rollback_id"],
        item["request_digest"],
        item["payload_digest"],
        kind,
        phase,
        item["journal_generation"],
        item["journal_revision"],
        item["relative_path"],
        item["schema_version"],
    )


def _leaf_error_from_object(value: object) -> RollbackLeafError:
    item = _require_object(
        value,
        frozenset(("adapter", "error_code", "error_digest", "object_ref")),
        "rollback leaf error",
    )
    return RollbackLeafError(
        item["adapter"], item["object_ref"], item["error_code"], item["error_digest"]
    )


def _phase_receipt_from_object(value: object) -> RollbackPhaseReceipt:
    item = _require_object(
        value,
        frozenset(("leaf_errors", "phase", "receipt_digests", "receipt_refs")),
        "rollback phase receipt",
    )
    try:
        phase = RollbackPhase(item["phase"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("rollback phase is invalid") from error
    return RollbackPhaseReceipt(
        phase,
        tuple(_require_tuple(item["receipt_digests"], "phase receipt digests")),
        tuple(
            _payload_ref_from_object(ref)
            for ref in _require_tuple(item["receipt_refs"], "phase receipt refs")
        ),
        tuple(
            _leaf_error_from_object(error)
            for error in _require_tuple(item["leaf_errors"], "phase leaf errors")
        ),
    )


def _terminal_quarantine_ref_from_object(
    value: object,
) -> RollbackTerminalQuarantineRef:
    item = _require_object(
        value,
        frozenset(
            (
                "predecessor_generation",
                "predecessor_record_digest",
                "rollback_id",
                "schema_version",
                "successor_generation",
                "successor_name",
                "successor_raw_digest",
                "successor_record_digest",
                "tombstone_name",
                "tombstone_raw_digest",
                "transaction_id",
            )
        ),
        "rollback terminal quarantine ref",
    )
    return RollbackTerminalQuarantineRef(
        item["transaction_id"],
        item["rollback_id"],
        item["predecessor_generation"],
        item["predecessor_record_digest"],
        item["successor_generation"],
        item["successor_record_digest"],
        item["successor_raw_digest"],
        item["successor_name"],
        item["tombstone_name"],
        item["tombstone_raw_digest"],
        item["schema_version"],
    )


def _journal_from_object(value: object) -> RollbackJournalRecord:
    item = _require_object(
        value,
        frozenset(
            (
                "generation",
                "phase",
                "phase_receipts",
                "previous_record_digest",
                "request_digest",
                "request_payload_ref",
                "revision",
                "rollback_id",
                "terminal_quarantine_refs",
                "schema_version",
            )
        ),
        "rollback journal",
    )
    try:
        phase = RollbackPhase(item["phase"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("rollback journal phase is invalid") from error
    return RollbackJournalRecord(
        item["rollback_id"],
        item["request_digest"],
        _payload_ref_from_object(item["request_payload_ref"]),
        item["generation"],
        item["revision"],
        phase,
        tuple(
            _phase_receipt_from_object(receipt)
            for receipt in _require_tuple(
                item["phase_receipts"], "journal phase receipts"
            )
        ),
        item["previous_record_digest"],
        tuple(
            _terminal_quarantine_ref_from_object(ref)
            for ref in _require_tuple(
                item["terminal_quarantine_refs"],
                "journal terminal quarantine refs",
            )
        ),
        item["schema_version"],
    )


def _active_state_from_object(value: object) -> ActiveApprovedTupleState:
    item = _require_object(
        value,
        frozenset(
            (
                "approved_tuple",
                "generation",
                "operation_id",
                "previous_state_digest",
                "schema_version",
            )
        ),
        "active tuple state",
    )
    return ActiveApprovedTupleState(
        item["generation"],
        _active_tuple_from_object(item["approved_tuple"]),
        item["operation_id"],
        item["previous_state_digest"],
        item["schema_version"],
    )


def _ownership_from_object(value: object) -> DependentOwnership:
    item = _require_object(
        value,
        frozenset(
            (
                "approved_tuple_digest",
                "episode_id",
                "object_kind",
                "object_ref",
                "parent_refs",
                "registration_id",
                "run_id",
                "schema_version",
            )
        ),
        "dependent ownership",
    )
    try:
        kind = DependentObjectKind(item["object_kind"])
    except (TypeError, ValueError) as error:
        raise RollbackValidationError("dependent object kind is invalid") from error
    return DependentOwnership(
        item["registration_id"],
        item["approved_tuple_digest"],
        item["episode_id"],
        item["run_id"],
        kind,
        _immutable_ref_from_object(item["object_ref"]),
        tuple(
            _immutable_ref_from_object(parent)
            for parent in _require_tuple(item["parent_refs"], "dependent parent refs")
        ),
        item["schema_version"],
    )


def _quarantine_receipt_from_object(value: object) -> DependentQuarantineReceipt:
    item = _require_object(
        value,
        frozenset(
            (
                "causal_root_digests",
                "cause_digest",
                "generation",
                "object_ref",
                "ownership_digest",
                "rollback_id",
                "schema_version",
            )
        ),
        "dependent quarantine receipt",
    )
    return DependentQuarantineReceipt(
        item["rollback_id"],
        item["cause_digest"],
        _immutable_ref_from_object(item["object_ref"]),
        item["ownership_digest"],
        tuple(_require_tuple(item["causal_root_digests"], "dependent causal roots")),
        item["generation"],
        item["schema_version"],
    )


def _dependent_record_from_object(value: object) -> DependentOwnershipRecord:
    item = _require_object(
        value,
        frozenset(
            (
                "export_eligible",
                "generation",
                "ownership",
                "previous_record_digest",
                "promotion_eligible",
                "quarantine_receipts",
                "schema_version",
            )
        ),
        "dependent ownership record",
    )
    return DependentOwnershipRecord(
        item["generation"],
        _ownership_from_object(item["ownership"]),
        item["promotion_eligible"],
        item["export_eligible"],
        tuple(
            _quarantine_receipt_from_object(receipt)
            for receipt in _require_tuple(
                item["quarantine_receipts"], "dependent quarantine receipts"
            )
        ),
        item["previous_record_digest"],
        item["schema_version"],
    )


__all__ = [
    "ActiveApprovedTuple",
    "ActiveApprovedTupleHistoryEntry",
    "ActiveApprovedTupleState",
    "ActiveApprovedTupleStore",
    "ApprovedTupleRef",
    "DependentIneligibleError",
    "DependentObjectKind",
    "DependentOwnership",
    "DependentOwnershipRecord",
    "DependentQuarantineReceipt",
    "DependentQuarantineStore",
    "FilesystemActiveApprovedTupleStore",
    "FilesystemDependentQuarantineStore",
    "FilesystemRollbackJournalStore",
    "ImmutableObjectRef",
    "RollbackConflictError",
    "RollbackCorruptionError",
    "RollbackIdempotencyConflict",
    "RollbackJournalRecord",
    "RollbackJournalStore",
    "RollbackLeafError",
    "RollbackPayloadKind",
    "RollbackPayloadRef",
    "RollbackPhase",
    "RollbackPhaseReceipt",
    "RollbackStoreError",
    "RollbackValidationError",
    "canonical_digest",
    "canonical_json_bytes",
]
