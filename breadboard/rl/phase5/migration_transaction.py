from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator


_GATE_SCHEMA_VERSION = "bb.rl.phase5.file-migration-gate.v1"
_GATE_FIELDS = {
    "schema_version",
    "migration_id",
    "store_ids",
    "owner_token",
    "verifier_token",
    "renewal",
}
_EVENT_FIELDS = {
    "predecessor_sha256",
    "event_type",
    "payload",
    "event_sha256",
}
_REQUIRED_STORE_COUNT = 3


class MigrationInProgress(RuntimeError):
    """Raised when an ordinary reader reaches a gated migration store."""

    def __init__(self, store_id: str) -> None:
        super().__init__("MIGRATION_IN_PROGRESS")
        self.store_id = store_id


class GateOwnershipError(PermissionError):
    """Raised when a caller cannot prove the required gate ownership."""


def canonical_bytes(value: object) -> bytes:
    """Return the Phase 5 canonical JSON representation of *value*."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("value is not valid strict JSON") from None
    return (encoded + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Hash bytes using the Phase 5 prefixed SHA-256 representation."""

    if not isinstance(value, bytes):
        raise TypeError("value must be bytes")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Hash the exact bytes read from *path* without interpreting them."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return value[7:] == value[7:].lower()


def _require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class StoreImage:
    store_id: str
    revision: str
    bytes_sha256: str
    size: int
    path: str
    reversible: bool
    rollback_command_sha256: str
    rollback_invariant: str

    def __post_init__(self) -> None:
        _require_nonempty_string(self.store_id, "store_id")
        _require_nonempty_string(self.revision, "revision")
        if not _is_sha256(self.bytes_sha256):
            raise ValueError("bytes_sha256 must be a sha256:<hex> digest")
        if type(self.size) is not int or self.size < 0:
            raise ValueError("size must be a non-negative integer")
        _require_nonempty_string(self.path, "path")
        if type(self.reversible) is not bool:
            raise ValueError("reversible must be a bool")
        if not _is_sha256(self.rollback_command_sha256):
            raise ValueError("rollback_command_sha256 must be a sha256:<hex> digest")
        _require_nonempty_string(self.rollback_invariant, "rollback_invariant")

    def as_dict(self) -> dict[str, object]:
        return {
            "store_id": self.store_id,
            "revision": self.revision,
            "bytes_sha256": self.bytes_sha256,
            "size": self.size,
            "path": self.path,
            "reversible": self.reversible,
            "rollback_command_sha256": self.rollback_command_sha256,
            "rollback_invariant": self.rollback_invariant,
        }


def capture_store_image(
    store_id: str,
    revision: str,
    path: str | os.PathLike[str],
    rollback_command: Mapping[str, object],
    reversible: bool,
    rollback_invariant: str,
) -> StoreImage:
    """Capture byte and rollback-command digests for one immutable store image."""

    _require_nonempty_string(store_id, "store_id")
    _require_nonempty_string(revision, "revision")
    if not isinstance(rollback_command, Mapping):
        raise TypeError("rollback_command must be a JSON object")
    if type(reversible) is not bool:
        raise ValueError("reversible must be a bool")
    _require_nonempty_string(rollback_invariant, "rollback_invariant")

    image_path = Path(path)
    digest = hashlib.sha256()
    size = 0
    with image_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)

    rollback_object = dict(rollback_command)
    return StoreImage(
        store_id=store_id,
        revision=revision,
        bytes_sha256="sha256:" + digest.hexdigest(),
        size=size,
        path=str(image_path),
        reversible=reversible,
        rollback_command_sha256=sha256_bytes(canonical_bytes(rollback_object)),
        rollback_invariant=rollback_invariant,
    )


def build_event(
    event_type: str,
    payload: Mapping[str, object],
    predecessor_sha256: str | None = None,
) -> dict[str, object]:
    """Build a hash-linked event whose digest excludes only its own hash field."""

    _require_nonempty_string(event_type, "event_type")
    if predecessor_sha256 is not None and not _is_sha256(predecessor_sha256):
        raise ValueError("predecessor_sha256 must be None or a sha256:<hex> digest")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a JSON object")

    # The JSON round trip detaches nested caller-owned objects and rejects values
    # that cannot participate in the canonical event representation.
    payload_object = json.loads(canonical_bytes(dict(payload)))
    body: dict[str, object] = {
        "predecessor_sha256": predecessor_sha256,
        "event_type": event_type,
        "payload": payload_object,
    }
    return {**body, "event_sha256": sha256_bytes(canonical_bytes(body))}


def verify_event_chain(
    events: Sequence[Mapping[str, object]],
    initial_predecessor_sha256: str | None = None,
) -> str | None:
    """Verify an ordered append-only chain and return its final head digest."""

    if initial_predecessor_sha256 is not None and not _is_sha256(
        initial_predecessor_sha256
    ):
        raise ValueError(
            "initial_predecessor_sha256 must be None or a sha256:<hex> digest"
        )

    expected_predecessor = initial_predecessor_sha256
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"event {index} must be an object")
        if set(event) != _EVENT_FIELDS:
            raise ValueError(f"event {index} fields do not match the event contract")

        predecessor = event["predecessor_sha256"]
        if predecessor != expected_predecessor:
            raise ValueError(f"event {index} predecessor does not match the chain head")
        event_type = _require_nonempty_string(event["event_type"], "event_type")
        payload = event["payload"]
        if not isinstance(payload, Mapping):
            raise ValueError(f"event {index} payload must be an object")
        event_sha256 = event["event_sha256"]
        if not _is_sha256(event_sha256):
            raise ValueError(f"event {index} has an invalid event_sha256")

        body = {
            "predecessor_sha256": predecessor,
            "event_type": event_type,
            "payload": dict(payload),
        }
        expected_hash = sha256_bytes(canonical_bytes(body))
        if event_sha256 != expected_hash:
            raise ValueError(f"event {index} hash does not match its contents")
        expected_predecessor = event_sha256

    return expected_predecessor


class FileMigrationGate:
    """A caller-located, single-file migration writer and read barrier gate."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        if not self.path.name:
            raise ValueError("gate path must name a file")

    @staticmethod
    def _validate_store_ids(store_ids: Iterable[str]) -> list[str]:
        if isinstance(store_ids, (str, bytes)):
            raise ValueError("store_ids must be an iterable of store identifiers")
        scope = list(store_ids)
        if len(scope) != _REQUIRED_STORE_COUNT:
            raise ValueError("migration gate scope must contain exactly three store IDs")
        for store_id in scope:
            _require_nonempty_string(store_id, "store_id")
        if len(set(scope)) != len(scope):
            raise ValueError("migration gate store IDs must be unique")
        return scope

    @staticmethod
    def _validate_state(value: object) -> dict[str, object]:
        if not isinstance(value, dict) or set(value) != _GATE_FIELDS:
            raise ValueError("migration gate fields do not match the gate contract")
        if value["schema_version"] != _GATE_SCHEMA_VERSION:
            raise ValueError("migration gate schema version does not match")
        migration_id = _require_nonempty_string(value["migration_id"], "migration_id")
        store_ids = FileMigrationGate._validate_store_ids(value["store_ids"])
        owner_token = _require_nonempty_string(value["owner_token"], "owner_token")
        verifier_token = _require_nonempty_string(
            value["verifier_token"], "verifier_token"
        )
        renewal = value["renewal"]
        if type(renewal) is not int or renewal < 0:
            raise ValueError("migration gate renewal must be a non-negative integer")
        return {
            "schema_version": _GATE_SCHEMA_VERSION,
            "migration_id": migration_id,
            "store_ids": store_ids,
            "owner_token": owner_token,
            "verifier_token": verifier_token,
            "renewal": renewal,
        }

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("failed to write migration gate")
            view = view[written:]

    def _same_open_file(self, descriptor: int) -> bool:
        opened = os.fstat(descriptor)
        current = os.stat(self.path, follow_symlinks=False)
        return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)

    def _sync_parent(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _locked_state(self, *, exclusive: bool) -> Iterator[tuple[int, dict[str, object]]]:
        flags = (os.O_RDWR if exclusive else os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            if not self._same_open_file(descriptor):
                raise GateOwnershipError("migration gate changed while it was being opened")
            with os.fdopen(os.dup(descriptor), "rb") as source:
                raw = source.read()
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("migration gate is not valid UTF-8 JSON") from exc
            state = self._validate_state(decoded)
            if raw != canonical_bytes(state):
                raise ValueError("migration gate is not canonically encoded")
            yield descriptor, state
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _assert_identity(
        state: Mapping[str, object],
        migration_id: str,
        token: str,
        token_field: str,
    ) -> None:
        _require_nonempty_string(migration_id, "migration_id")
        _require_nonempty_string(token, token_field)
        if (
            state["migration_id"] != migration_id
            or state[token_field] != token
        ):
            raise GateOwnershipError("migration gate ownership does not match")

    def acquire(
        self,
        migration_id: str,
        store_ids: Iterable[str],
        owner_token: str,
        verifier_token: str,
    ) -> dict[str, object]:
        """Atomically acquire this exact gate path for a three-store scope."""

        migration_id = _require_nonempty_string(migration_id, "migration_id")
        owner_token = _require_nonempty_string(owner_token, "owner_token")
        verifier_token = _require_nonempty_string(verifier_token, "verifier_token")
        scope = self._validate_store_ids(store_ids)
        state: dict[str, object] = {
            "schema_version": _GATE_SCHEMA_VERSION,
            "migration_id": migration_id,
            "store_ids": scope,
            "owner_token": owner_token,
            "verifier_token": verifier_token,
            "renewal": 0,
        }
        payload = canonical_bytes(state)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(self.path, flags, 0o600)
        try:
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
        except BaseException:
            try:
                if self._same_open_file(descriptor):
                    os.unlink(self.path)
            finally:
                os.close(descriptor)
            raise
        else:
            os.close(descriptor)
        self._sync_parent()
        return dict(state)

    def load(self) -> dict[str, object]:
        """Load and strictly validate the acquired gate."""

        with self._locked_state(exclusive=False) as (_descriptor, state):
            return state

    def assert_owner(self, migration_id: str, owner_token: str) -> dict[str, object]:
        with self._locked_state(exclusive=False) as (_descriptor, state):
            self._assert_identity(state, migration_id, owner_token, "owner_token")
            return state

    def assert_verifier(
        self, migration_id: str, verifier_token: str
    ) -> dict[str, object]:
        with self._locked_state(exclusive=False) as (_descriptor, state):
            self._assert_identity(
                state, migration_id, verifier_token, "verifier_token"
            )
            return state

    def ordinary_read(self, store_id: str) -> None:
        """Fail closed for ordinary reads of stores covered by an active gate."""

        store_id = _require_nonempty_string(store_id, "store_id")
        try:
            with self._locked_state(exclusive=False) as (_descriptor, state):
                if store_id in state["store_ids"]:
                    raise MigrationInProgress(store_id)
        except FileNotFoundError:
            return

    def renew(self, migration_id: str, owner_token: str) -> dict[str, object]:
        """Advance the owner-authenticated renewal counter monotonically."""

        with self._locked_state(exclusive=True) as (descriptor, state):
            self._assert_identity(state, migration_id, owner_token, "owner_token")
            renewed = dict(state)
            renewed["renewal"] = int(state["renewal"]) + 1
            payload = canonical_bytes(renewed)
            os.lseek(descriptor, 0, os.SEEK_SET)
            self._write_all(descriptor, payload)
            os.ftruncate(descriptor, len(payload))
            os.fsync(descriptor)
        return renewed

    def release(self, migration_id: str, owner_token: str) -> dict[str, object]:
        """Remove the gate only after proving writer ownership."""

        with self._locked_state(exclusive=True) as (descriptor, state):
            self._assert_identity(state, migration_id, owner_token, "owner_token")
            if not self._same_open_file(descriptor):
                raise GateOwnershipError("migration gate changed before release")
            os.unlink(self.path)
            self._sync_parent()
            return state

    def status(self) -> dict[str, object]:
        """Return a validated status without creating or acquiring a gate."""

        try:
            state = self.load()
        except FileNotFoundError:
            return {"acquired": False, "path": str(self.path)}
        return {"acquired": True, "path": str(self.path), **state}


__all__ = [
    "canonical_bytes",
    "sha256_bytes",
    "sha256_file",
    "StoreImage",
    "capture_store_image",
    "build_event",
    "verify_event_chain",
    "MigrationInProgress",
    "GateOwnershipError",
    "FileMigrationGate",
]
