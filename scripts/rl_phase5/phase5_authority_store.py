from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _make_store_gate():
    token = object()
    deployment_store_ids: set[int] = set()

    def is_store_token(candidate: object) -> bool:
        return candidate is token

    def is_deployment_store(candidate: object) -> bool:
        return (
            isinstance(candidate, FileTrustStore)
            and id(candidate) in deployment_store_ids
        )

    def open_deployment_store(
        *,
        root: Path,
        deployment_key: bytes,
        expected_public_key_digest: str,
    ) -> FileTrustStore:
        store = FileTrustStore(
            _token=token,
            root=root,
            deployment_key=deployment_key,
            expected_public_key_digest=expected_public_key_digest,
        )
        deployment_store_ids.add(id(store))
        return store

    return is_store_token, is_deployment_store, open_deployment_store


(
    _is_store_token,
    _is_deployment_store,
    _open_deployment_store,
) = _make_store_gate()
del _make_store_gate


@dataclass(frozen=True)
class StoredArtifact:
    object_id: str
    artifact_uri: str
    artifact_bytes: bytes
    store_id: str
    authority_hmac: str


class FileTrustStore:
    _phase5_private_store_marker = True
    """Deployment-anchored, inode-checked, fsync-backed Phase 5 state."""

    _KEY = "authority.key"
    _ANCHOR = "anchor.json"
    _ARTIFACTS = "artifacts"
    _STATE = "state"
    _STATE_HEAD = "state.head.json"
    _EVENTS = "events.jsonl"

    def __init__(
        self,
        *,
        _token: object | None = None,
        root: Path | None = None,
        deployment_key: bytes | None = None,
        expected_public_key_digest: str | None = None,
    ) -> None:
        if (
            not _is_store_token(_token)
            or root is None
            or deployment_key is None
            or expected_public_key_digest is None
        ):
            raise ValueError(
                "Phase 5 trust stores are opened only by server composition"
            )
        if len(deployment_key) < 32:
            raise ValueError(
                "Phase 5 deployment signing key must contain at least 32 bytes"
            )
        public_key_digest = self.public_key_digest_for(deployment_key)
        if not hmac.compare_digest(public_key_digest, expected_public_key_digest):
            raise ValueError("Phase 5 deployment public-key digest mismatch")
        self._root = root.absolute()
        self._key = bytes(deployment_key)
        self._public_key_digest = public_key_digest
        self._store_id = "store:" + public_key_digest.removeprefix("sha256:")
        if not self._root.exists():
            self._bootstrap()
        self._open_and_lock()

    @staticmethod
    def public_key_for(deployment_key: bytes) -> bytes:
        if not isinstance(deployment_key, bytes) or len(deployment_key) != 32:
            raise ValueError("Phase 5 deployment signing key must contain 32 bytes")
        return (
            Ed25519PrivateKey.from_private_bytes(deployment_key)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )

    @staticmethod
    def public_key_digest_for(deployment_key: bytes) -> str:
        return "sha256:" + hashlib.sha256(
            FileTrustStore.public_key_for(deployment_key)
        ).hexdigest()

    @property
    def store_id(self) -> str:
        return self._store_id

    @property
    def public_key_digest(self) -> str:
        return self._public_key_digest

    def record_artifact(
        self,
        *,
        object_id: str,
        artifact_uri: str,
        artifact_payload: Mapping[str, Any],
    ) -> StoredArtifact:
        self._check_layout()
        if not object_id or not artifact_uri:
            raise ValueError("production evidence object requires identity and URI")
        artifact_bytes = self._json_bytes(artifact_payload)
        authority_hmac = self._mac(
            self._json_bytes(
                {
                    "artifact_sha256": self._sha256(artifact_bytes),
                    "artifact_size": len(artifact_bytes),
                    "artifact_uri": artifact_uri,
                    "object_id": object_id,
                    "store_id": self._store_id,
                }
            )
        )
        record = self._json_bytes(
            {
                "artifact": artifact_payload,
                "artifact_uri": artifact_uri,
                "authority_hmac": authority_hmac,
                "object_id": object_id,
                "schema": "bb.rl.phase5.signed-artifact.v2",
                "store_id": self._store_id,
            }
        )
        path = self._artifact_path(object_id)
        if path.exists():
            if self._read_nofollow(path) != record:
                raise ValueError("production evidence records are immutable")
        else:
            self._create_exclusive(path, record, 0o600)
        return StoredArtifact(
            object_id=object_id,
            artifact_uri=artifact_uri,
            artifact_bytes=artifact_bytes,
            store_id=self._store_id,
            authority_hmac=authority_hmac,
        )

    def verify_artifact(self, artifact: StoredArtifact) -> bool:
        self._check_layout()
        if artifact.store_id != self._store_id:
            return False
        expected = self._mac(
            self._json_bytes(
                {
                    "artifact_sha256": self._sha256(artifact.artifact_bytes),
                    "artifact_size": len(artifact.artifact_bytes),
                    "artifact_uri": artifact.artifact_uri,
                    "object_id": artifact.object_id,
                    "store_id": artifact.store_id,
                }
            )
        )
        if not hmac.compare_digest(expected, artifact.authority_hmac):
            return False
        try:
            record = json.loads(
                self._read_nofollow(self._artifact_path(artifact.object_id))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return record == {
            "artifact": json.loads(artifact.artifact_bytes),
            "artifact_uri": artifact.artifact_uri,
            "authority_hmac": artifact.authority_hmac,
            "object_id": artifact.object_id,
            "schema": "bb.rl.phase5.signed-artifact.v2",
            "store_id": artifact.store_id,
        }

    def load_artifacts(self) -> dict[str, StoredArtifact]:
        self._check_layout()
        artifacts: dict[str, StoredArtifact] = {}
        with os.scandir(self._root / self._ARTIFACTS) as entries:
            for entry in sorted(entries, key=lambda value: value.name):
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError(
                        "Phase 5 artifact storage contains a foreign object"
                    )
                try:
                    record = json.loads(self._read_nofollow(Path(entry.path)))
                    if set(record) != {
                        "artifact",
                        "artifact_uri",
                        "authority_hmac",
                        "object_id",
                        "schema",
                        "store_id",
                    }:
                        raise ValueError
                    object_id = record["object_id"]
                    if (
                        not isinstance(object_id, str)
                        or Path(entry.path) != self._artifact_path(object_id)
                        or record["schema"] != "bb.rl.phase5.signed-artifact.v2"
                    ):
                        raise ValueError
                    artifact = StoredArtifact(
                        object_id=object_id,
                        artifact_uri=record["artifact_uri"],
                        artifact_bytes=self._json_bytes(record["artifact"]),
                        store_id=record["store_id"],
                        authority_hmac=record["authority_hmac"],
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ) as error:
                    raise ValueError(
                        "persisted production evidence artifact is invalid"
                    ) from error
                if object_id in artifacts or not self.verify_artifact(artifact):
                    raise ValueError(
                        "persisted production evidence artifact is invalid"
                    )
                artifacts[object_id] = artifact
        return artifacts

    def append_event(self, event: Mapping[str, Any]) -> None:
        self._check_layout()
        unsigned = dict(event)
        unsigned["store_id"] = self._store_id
        line = (
            self._json_bytes(
                {
                    "event": unsigned,
                    "hmac": self._mac(self._json_bytes(unsigned)),
                    "schema": "bb.rl.phase5.signed-event.v2",
                }
            )
            + b"\n"
        )
        path = self._root / self._EVENTS
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            if self._identity_fd(fd) != self._events_identity:
                raise ValueError("Phase 5 event log inode changed")
            self._write_all(fd, line)
            os.fsync(fd)
            if self._identity_fd(fd) != self._events_identity:
                raise ValueError("Phase 5 event log inode changed while writing")
        finally:
            os.close(fd)
        self._fsync_dir(self._root)

    def events(self) -> tuple[dict[str, Any], ...]:
        self._check_layout()
        raw = self._read_nofollow(self._root / self._EVENTS)
        if raw and not raw.endswith(b"\n"):
            raise ValueError("Phase 5 event log is truncated")
        events: list[dict[str, Any]] = []
        for sequence, line in enumerate(raw.splitlines(), 1):
            try:
                envelope = json.loads(line)
                if (
                    set(envelope) != {"event", "hmac", "schema"}
                    or envelope["schema"] != "bb.rl.phase5.signed-event.v2"
                ):
                    raise ValueError
                event = envelope["event"]
                if (
                    not isinstance(event, dict)
                    or event.get("store_id") != self._store_id
                ):
                    raise ValueError
                if event.get("sequence") != sequence:
                    raise ValueError
                if not hmac.compare_digest(
                    self._mac(self._json_bytes(event)), envelope["hmac"]
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    "Phase 5 event log signature or sequence mismatch"
                ) from error
            events.append(
                {key: value for key, value in event.items() if key != "store_id"}
            )
        return tuple(events)

    def commit_state(self, payload: Mapping[str, Any]) -> None:
        self._check_layout()
        sequence = self._last_state_sequence + 1
        previous_digest = self._last_state_digest
        state = {
            "payload": payload,
            "previous_state_sha256": previous_digest,
            "sequence": sequence,
            "store_id": self._store_id,
        }
        state_bytes = self._json_bytes(state)
        digest = self._sha256(state_bytes)
        envelope = self._json_bytes(
            {
                "hmac": self._mac(state_bytes),
                "schema": "bb.rl.phase5.signed-state.v2",
                "state": state,
                "state_sha256": digest,
            }
        )
        path = self._root / self._STATE / f"{sequence:020d}.json"
        self._create_exclusive(path, envelope, 0o600)
        head = self._json_bytes(
            {
                "hmac": self._mac(
                    self._json_bytes({"sequence": sequence, "state_sha256": digest})
                ),
                "schema": "bb.rl.phase5.state-head.v2",
                "sequence": sequence,
                "state_sha256": digest,
            }
        )
        self._replace_file(self._root / self._STATE_HEAD, head, 0o600)
        self._last_state_sequence = sequence
        self._last_state_digest = digest

    def load_state(self) -> tuple[int, str, dict[str, Any]] | None:
        self._check_layout(check_head=False)
        directory = self._root / self._STATE
        names: list[str] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError(
                        "Phase 5 signed-state directory contains a foreign object"
                    )
                names.append(entry.name)
        names.sort()
        previous_digest: str | None = None
        latest_payload: dict[str, Any] | None = None
        for sequence, name in enumerate(names, 1):
            if name != f"{sequence:020d}.json":
                raise ValueError("Phase 5 signed-state generations are truncated")
            try:
                envelope = json.loads(self._read_nofollow(directory / name))
                if set(envelope) != {"hmac", "schema", "state", "state_sha256"}:
                    raise ValueError
                if envelope["schema"] != "bb.rl.phase5.signed-state.v2":
                    raise ValueError
                state = envelope["state"]
                if set(state) != {
                    "payload",
                    "previous_state_sha256",
                    "sequence",
                    "store_id",
                }:
                    raise ValueError
                if (
                    state["sequence"] != sequence
                    or state["store_id"] != self._store_id
                    or state["previous_state_sha256"] != previous_digest
                    or not isinstance(state["payload"], dict)
                ):
                    raise ValueError
                state_bytes = self._json_bytes(state)
                digest = self._sha256(state_bytes)
                if digest != envelope["state_sha256"] or not hmac.compare_digest(
                    self._mac(state_bytes), envelope["hmac"]
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("Phase 5 signed state is corrupt") from error
            previous_digest = digest
            latest_payload = state["payload"]
        head_path = self._root / self._STATE_HEAD
        if latest_payload is None:
            if head_path.exists():
                raise ValueError("Phase 5 state head exists without signed state")
            return None
        try:
            head = json.loads(self._read_nofollow(head_path))
            head_payload = {
                "sequence": len(names),
                "state_sha256": previous_digest,
            }
            if head != {
                "hmac": self._mac(self._json_bytes(head_payload)),
                "schema": "bb.rl.phase5.state-head.v2",
                **head_payload,
            }:
                raise ValueError
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Phase 5 state head is missing or corrupt") from error
        return len(names), previous_digest, latest_payload

    def _bootstrap(self) -> None:
        parent = self._root.parent
        if not parent.exists() or parent.is_symlink() or not parent.is_dir():
            raise ValueError("Phase 5 deployment state parent must already exist")
        self._root.mkdir(mode=0o700, exist_ok=False)
        self._fsync_dir(parent)
        self._create_exclusive(self._root / self._KEY, self._key, 0o600)
        (self._root / self._ARTIFACTS).mkdir(mode=0o700)
        self._fsync_dir(self._root)
        (self._root / self._STATE).mkdir(mode=0o700)
        self._fsync_dir(self._root)
        self._create_exclusive(self._root / self._EVENTS, b"", 0o600)
        anchor = {
            "artifacts_identity": self._identity_payload(self._root / self._ARTIFACTS),
            "events_identity": self._identity_payload(self._root / self._EVENTS),
            "key_identity": self._identity_payload(self._root / self._KEY),
            "public_key_digest": self._public_key_digest,
            "root_identity": self._identity_payload(self._root),
            "schema": "bb.rl.phase5.deployment-anchor.v2",
            "state_identity": self._identity_payload(self._root / self._STATE),
            "store_id": self._store_id,
        }
        unsigned = self._json_bytes(anchor)
        self._create_exclusive(
            self._root / self._ANCHOR,
            self._json_bytes({"anchor": anchor, "hmac": self._mac(unsigned)}),
            0o600,
        )

    def _open_and_lock(self) -> None:
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("Phase 5 trust-store root must be a real directory")
        key = self._read_nofollow(self._root / self._KEY)
        if not hmac.compare_digest(key, self._key):
            raise ValueError("Phase 5 deployment signing key identity mismatch")
        try:
            envelope = json.loads(self._read_nofollow(self._root / self._ANCHOR))
            anchor = envelope["anchor"]
            if set(envelope) != {"anchor", "hmac"} or not hmac.compare_digest(
                self._mac(self._json_bytes(anchor)), envelope["hmac"]
            ):
                raise ValueError
            expected = {
                "artifacts_identity": self._identity_payload(
                    self._root / self._ARTIFACTS
                ),
                "events_identity": self._identity_payload(self._root / self._EVENTS),
                "key_identity": self._identity_payload(self._root / self._KEY),
                "public_key_digest": self._public_key_digest,
                "root_identity": self._identity_payload(self._root),
                "schema": "bb.rl.phase5.deployment-anchor.v2",
                "state_identity": self._identity_payload(self._root / self._STATE),
                "store_id": self._store_id,
            }
            if anchor != expected:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Phase 5 deployment trust anchor mismatch") from error
        self._root_identity = self._identity(self._root)
        self._key_identity = self._identity(self._root / self._KEY)
        self._anchor_identity = self._identity(self._root / self._ANCHOR)
        self._artifacts_identity = self._identity(self._root / self._ARTIFACTS)
        self._state_identity = self._identity(self._root / self._STATE)
        self._events_identity = self._identity(self._root / self._EVENTS)
        self._check_layout()
        latest = self.load_state()
        self._last_state_sequence = 0 if latest is None else latest[0]
        self._last_state_digest = None if latest is None else latest[1]

    def _check_layout(self, *, check_head: bool = True) -> None:
        expected = (
            (self._root, self._root_identity),
            (self._root / self._KEY, self._key_identity),
            (self._root / self._ANCHOR, self._anchor_identity),
            (self._root / self._ARTIFACTS, self._artifacts_identity),
            (self._root / self._STATE, self._state_identity),
            (self._root / self._EVENTS, self._events_identity),
        )
        for path, identity in expected:
            if path.is_symlink() or self._identity(path) != identity:
                raise ValueError("Phase 5 trust-store inode identity changed")
        if check_head:
            head = self._root / self._STATE_HEAD
            if head.exists() and head.is_symlink():
                raise ValueError("Phase 5 state head must not be a symlink")

    def _artifact_path(self, object_id: str) -> Path:
        return (
            self._root
            / self._ARTIFACTS
            / (hashlib.sha256(object_id.encode("utf-8")).hexdigest() + ".json")
        )

    def _mac(self, value: bytes) -> str:
        return "hmac-sha256:" + hmac.new(self._key, value, hashlib.sha256).hexdigest()

    @staticmethod
    def _sha256(value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        value = path.stat(follow_symlinks=False)
        return value.st_dev, value.st_ino

    @classmethod
    def _identity_payload(cls, path: Path) -> list[int]:
        return list(cls._identity(path))

    @staticmethod
    def _identity_fd(fd: int) -> tuple[int, int]:
        value = os.fstat(fd)
        return value.st_dev, value.st_ino

    @classmethod
    def _read_nofollow(cls, path: Path) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Phase 5 state path is not a regular file")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(fd, min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("Phase 5 state file is truncated")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                raise ValueError("Phase 5 state file changed while reading")
            after = os.fstat(fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("Phase 5 state file changed while reading")
            if cls._identity(path) != (before.st_dev, before.st_ino):
                raise ValueError("Phase 5 state file inode changed while reading")
            return b"".join(chunks)
        finally:
            os.close(fd)

    @classmethod
    def _create_exclusive(cls, path: Path, content: bytes, mode: int) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, mode)
        try:
            cls._write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        cls._fsync_dir(path.parent)

    @classmethod
    def _replace_file(cls, path: Path, content: bytes, mode: int) -> None:
        temp = path.with_name(path.name + ".new")
        try:
            cls._create_exclusive(temp, content, mode)
            os.replace(temp, path)
            cls._fsync_dir(path.parent)
        finally:
            if temp.exists():
                temp.unlink()
                cls._fsync_dir(path.parent)

    @staticmethod
    def _write_all(fd: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise OSError("short write while persisting Phase 5 authority state")
            offset += written

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)




__all__ = ["FileTrustStore", "StoredArtifact"]
