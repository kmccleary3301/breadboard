"""High level orchestration of session lifecycle for the CLI bridge."""

from __future__ import annotations
import asyncio
import hashlib
import inspect
import json
import logging
import os
import shutil
import stat
import time
import uuid
import weakref
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
from threading import RLock
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Protocol, Sequence
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import (
    AnchoredStorage,
    ArtifactStore,
    ReplayError,
    Session as ProductSession,
)
from breadboard.product.runtime.events import (
    GenerationAdoptionError,
    JsonlEventSink,
    ProcessLock,
)
from breadboard.product.runtime.children import (
    DurableChildReconciler,
    ExpectedRevisionConflict,
    LateResultRejected,
    ProcessExecutionAdapter,
    RayJobAdapter,
)
from breadboard.product.coordination.work_items import WorkItem, WorkItemRepository
from breadboard.product.runtime.session_store import (
    authorize_session_artifact_manifest,
    event_from_record,
    load_session,
    mutate_session,
    session_directory_identity,
    session_event_path,
)
from fastapi import HTTPException, UploadFile, status
from breadboard.product.harness.default_profile import (
    DefaultProfileResolution,
    resolve_default_profile,
)
from breadboard.product.harness.templates import load_daily_driver_model_roles
from .events import (
    EventType,
    SessionEvent,
    REPLAY_RETENTION_MAX_EVENTS,
    replay_retention_facts,
)
from .models import (
    ATPReplBatchRequest,
    ATPReplBatchResponse,
    ATPReplError,
    ATPReplMetrics,
    ATPReplRequest,
    ATPReplResponse,
    ATPReplSorry,
    AttachmentUploadResponse,
    ModelCatalogResponse,
    SkillCatalogResponse,
    CTreeSnapshotResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionFileContent,
    SessionFileInfo,
    SessionInputRequest,
    SessionInputResponse,
    SessionStatus,
    BeginControlDrainRequest,
    BootstrapChallengeRequest,
    BootstrapChallengeResponse,
    ClientLeaseRequest,
    ClientRegisterRequest,
    ClientRegistrationResponse,
    DrainControlRequest,
    DrainControlResponse,
    GracefulControlResultRequest,
    HardSignalCommitRequest,
    HardSignalPreparationResponse,
    HardSignalPermitResponse,
    HardSignalOutcomeRequest,
    HardSignalPrepareRequest,
    OwnerAcquireRequest,
    OwnerLeaseRequest,
    OwnerLeaseResponse,
    SessionTurnCancelRequest,
    SessionTurnCancelResponse,
)
from .atp_diagnostics import build_atp_harness_diagnostic
from .registry import (
    CancellationRecord,
    SessionRecord,
    SessionRegistry,
    SubscriberState,
    TurnRecord,
    cancellation_body_digest,
    identity_digest,
    submission_body_digest,
)
from .engine_identity_config import (
    P30_SESSION_REPLAY_CONTRACT_DIGEST,
    P30_SESSION_SCHEMA_SHA256,
    get_engine_process_identity,
    get_launch_bootstrap_verifier,
    p30_session_schema_sha256,
)
from .session_runner import SessionRunner
from .tail_index import _TAIL_LINE_INDEX_CACHE
from .model_catalog import build_model_catalog
from .runtime_emission import (
    DEFAULT_INTERACTIVE_SESSION_TITLE,
    _sanitize_persisted_runtime_config,
    ManagedStatePaths,
    ManagedStateRootError,
    compile_runtime_effective_config_graph,
    default_runtime_record_root,
    emit_session_start_records,
    managed_state_paths,
    prepare_managed_state,
    retained_runtime_overrides,
    primitive_emission_enabled,
)
from ...orchestration import MultiAgentOrchestrator, TeamConfig
from ...compilation.v2_loader import load_agent_config
from ...compilation.effective_operation_policy import policy_pack_for_config_authority
from ...model_roles import (
    ModelRoleResolutionError,
    compile_model_roles,
    embed_model_role_lock,
    restore_model_role_lock,
)
from ...provider.routing import provider_router
from ...provider_broker import get_provider_broker
from ...provider import runtime_codex as runtime_codex_module

logger = logging.getLogger(__name__)
MODEL_ROLES_METADATA_KEY = "bb.model_roles.v1"


def _load_bridge_chaos_metadata() -> dict[str, float] | None:
    latency, jitter = (
        max(0, int(os.environ.get(name, "0")))
        for name in ("BREADBOARD_CLI_LATENCY_MS", "BREADBOARD_CLI_JITTER_MS")
    )
    try:
        drop = float(os.environ.get("BREADBOARD_CLI_DROP_RATE", "0"))
    except ValueError:
        drop = 0.0
    drop = max(0.0, min(1.0, drop))
    if latency == jitter == drop == 0:
        return None
    return {"latencyMs": float(latency), "jitterMs": float(jitter), "dropRate": drop}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


_START_PENDING, _START_COMMITTED, _START_OWNER = (
    ".start.pending",
    ".start.committed",
    ".start.owner",
)
_SESSION_EVENT_ROOT_METADATA_KEY = "session_event_root"
_SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY = "durable_product_workspace"
_MAX_RETAINED_EVENT_JOURNAL_BYTES = 64 * 1024 * 1024


def _read_bounded_event_journal(stream: Any, size: int) -> bytes:
    if size > _MAX_RETAINED_EVENT_JOURNAL_BYTES:
        raise OSError("retained event journal exceeds byte limit")
    payload = stream.read(_MAX_RETAINED_EVENT_JOURNAL_BYTES + 1)
    if len(payload) > _MAX_RETAINED_EVENT_JOURNAL_BYTES:
        raise OSError("retained event journal exceeds byte limit")
    return payload

def _validate_retained_event_journal_stat(file_stat: os.stat_result) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise OSError("retained event journal is not a regular file")
    if file_stat.st_nlink != 1:
        raise OSError("retained event journal must have exactly one hard link")




def _event_root(state_paths: ManagedStatePaths | None = None) -> Path:
    managed = state_paths if state_paths is not None else managed_state_paths()
    if managed is not None:
        return managed.session_events
    return Path(
        os.environ.get(
            "BREADBOARD_SESSION_EVENT_ROOT",
            Path.home() / ".breadboard" / "session_events",
        )
    ).resolve()

def _read_retained_event_journal(
    event_root: Path,
    session_id: str,
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    event_path = event_root / session_id / "session_events.jsonl"
    if os.name == "nt":
        handles: list[int] = []
        try:
            handles.append(
                AnchoredStorage.windows_handle(
                    event_root,
                    directory=True,
                    create=False,
                )
            )
            handles.append(
                AnchoredStorage.windows_handle(
                    event_path.parent,
                    directory=True,
                    create=False,
                )
            )
            handles.append(
                AnchoredStorage.windows_handle(
                    event_path,
                    directory=False,
                    create=False,
                )
            )
            directory_stat = event_path.parent.stat(follow_symlinks=False)
            file_stat = event_path.stat(follow_symlinks=False)
            _validate_retained_event_journal_stat(file_stat)
            with event_path.open("rb") as stream:
                payload = _read_bounded_event_journal(stream, file_stat.st_size)
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
    else:
        root_descriptor = os.open(
            event_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        session_descriptor: int | None = None
        event_descriptor: int | None = None
        try:
            session_descriptor = AnchoredStorage.open_directory(
                root_descriptor,
                session_id,
                create=False,
            )
            directory_stat = os.fstat(session_descriptor)
            event_descriptor = os.open(
                "session_events.jsonl",
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=session_descriptor,
            )
            file_stat = os.fstat(event_descriptor)
            _validate_retained_event_journal_stat(file_stat)
            with os.fdopen(event_descriptor, "rb") as stream:
                event_descriptor = None
                payload = _read_bounded_event_journal(stream, file_stat.st_size)
        finally:
            if event_descriptor is not None:
                os.close(event_descriptor)
            if session_descriptor is not None:
                os.close(session_descriptor)
            os.close(root_descriptor)
    return (
        payload,
        (directory_stat.st_dev, directory_stat.st_ino),
        (file_stat.st_dev, file_stat.st_ino),
    )

def _retained_event_journal_identity(
    event_root: Path,
    session_id: str,
) -> tuple[tuple[int, int], tuple[int, int]]:
    event_path = event_root / session_id / "session_events.jsonl"
    if os.name == "nt":
        handles: list[int] = []
        try:
            handles.append(
                AnchoredStorage.windows_handle(
                    event_root,
                    directory=True,
                    create=False,
                )
            )
            handles.append(
                AnchoredStorage.windows_handle(
                    event_path.parent,
                    directory=True,
                    create=False,
                )
            )
            handles.append(
                AnchoredStorage.windows_handle(
                    event_path,
                    directory=False,
                    create=False,
                )
            )
            directory_stat = event_path.parent.stat(follow_symlinks=False)
            file_stat = event_path.stat(follow_symlinks=False)
            _validate_retained_event_journal_stat(file_stat)
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
    else:
        root_descriptor = os.open(
            event_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        session_descriptor: int | None = None
        event_descriptor: int | None = None
        try:
            session_descriptor = AnchoredStorage.open_directory(
                root_descriptor,
                session_id,
                create=False,
            )
            directory_stat = os.fstat(session_descriptor)
            event_descriptor = os.open(
                "session_events.jsonl",
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=session_descriptor,
            )
            file_stat = os.fstat(event_descriptor)
            _validate_retained_event_journal_stat(file_stat)
        finally:
            if event_descriptor is not None:
                os.close(event_descriptor)
            if session_descriptor is not None:
                os.close(session_descriptor)
            os.close(root_descriptor)
    return (
        (directory_stat.st_dev, directory_stat.st_ino),
        (file_stat.st_dev, file_stat.st_ino),
    )
def _retained_event_lock_path(event_root: Path, session_id: str) -> Path:
    return event_root / f".{session_id}.session_events.lock"



class _RetainedProcessLock:
    def __init__(self, path: Path, *, lock_path: Path | None = None) -> None:
        self._path = Path(path)
        self._lock_path = (
            Path(lock_path)
            if lock_path is not None
            else self._path.with_name(f".{self._path.name}.lock")
        )
        self._generic: ProcessLock | None = None
        self._stream: Any | None = None

    def __enter__(self) -> "_RetainedProcessLock":
        if os.name != "nt":
            self._generic = ProcessLock(self._path)
            self._generic.__enter__()
            return self
        descriptor = AnchoredStorage.windows_file_descriptor(
            self._lock_path,
            create=True,
        )
        stream: Any | None = None
        try:
            stream = os.fdopen(descriptor, "a+b", buffering=0)
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError("unsafe retained event process lock")
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        except BaseException:
            if stream is None:
                os.close(descriptor)
            else:
                stream.close()
            raise
        self._stream = stream
        return self

    def __exit__(self, *exc: object) -> None:
        if self._generic is not None:
            self._generic.__exit__(*exc)
            self._generic = None
            return
        stream = self._stream
        if stream is None:
            return
        try:
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            stream.close()
            self._stream = None


class _RetainedEventSink:
    _locks = tuple(RLock() for _ in range(64))

    def __init__(
        self,
        delegate: JsonlEventSink | None,
        event_root: Path,
        session_id: str,
        expected_identity: tuple[tuple[int, int], tuple[int, int]],
    ) -> None:
        self.path = (
            delegate.path
            if delegate is not None
            else (event_root / session_id / "session_events.jsonl").absolute()
        )
        self._delegate = delegate
        self._event_root = event_root
        self._session_id = session_id
        self._expected_identity = expected_identity
        self._lock = self._locks[hash(expected_identity) % len(self._locks)]
        self._expected_size: int | None = None

    def _verify_identity(
        self,
        directory_stat: os.stat_result,
        file_stat: os.stat_result,
    ) -> None:
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise RuntimeError("retained event journal identity changed")
        current_identity = (
            (directory_stat.st_dev, directory_stat.st_ino),
            (file_stat.st_dev, file_stat.st_ino),
        )
        if current_identity != self._expected_identity:
            raise RuntimeError("retained event journal identity changed")

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short retained event journal write")
            offset += written

    @staticmethod
    def _unlink_regular_at(directory_descriptor: int, name: str) -> None:
        try:
            entry_stat = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if not stat.S_ISREG(entry_stat.st_mode):
            raise OSError(f"unsafe retained event transaction entry: {name}")
        os.unlink(name, dir_fd=directory_descriptor)

    def _recover_transaction_posix(
        self,
        session_descriptor: int,
        event_descriptor: int,
    ) -> None:
        transaction_name = ".session_events.jsonl.txn"
        temporary_name = f"{transaction_name}.tmp"
        self._unlink_regular_at(session_descriptor, temporary_name)
        try:
            transaction_descriptor = os.open(
                transaction_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=session_descriptor,
            )
        except FileNotFoundError:
            return
        try:
            transaction_stat = os.fstat(transaction_descriptor)
            if not stat.S_ISREG(transaction_stat.st_mode):
                raise OSError("unsafe retained event transaction")
            raw_offset = os.read(transaction_descriptor, 64)
            if os.read(transaction_descriptor, 1):
                raise OSError("oversized retained event transaction")
            recovered_offset = int(raw_offset.decode("ascii"))
        finally:
            os.close(transaction_descriptor)
        event_stat = os.fstat(event_descriptor)
        current_size = event_stat.st_size
        if recovered_offset < 0 or recovered_offset > current_size:
            raise OSError("invalid retained event transaction offset")
        retained_tail = os.pread(
            event_descriptor,
            current_size - recovered_offset,
            recovered_offset,
        )
        _validate_retained_event_journal_stat(event_stat)
        truncated = False
        try:
            os.ftruncate(event_descriptor, recovered_offset)
            truncated = True
            os.fsync(event_descriptor)
            _validate_retained_event_journal_stat(os.fstat(event_descriptor))
        except BaseException:
            if truncated:
                os.lseek(event_descriptor, recovered_offset, os.SEEK_SET)
                self._write_all(event_descriptor, retained_tail)
                os.fsync(event_descriptor)
            raise
        self._unlink_regular_at(session_descriptor, transaction_name)
        os.fsync(session_descriptor)

    def _append_posix(self, event: object) -> None:
        payload = (
            json.dumps(
                event.as_dict(),  # type: ignore[attr-defined]
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        root_descriptor = os.open(
            self._event_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        session_descriptor: int | None = None
        event_descriptor: int | None = None
        process_lock_descriptor: int | None = None
        try:
            session_descriptor = AnchoredStorage.open_directory(
                root_descriptor,
                self._session_id,
                create=False,
            )
            process_lock_descriptor = os.open(
                ".session_events.jsonl.lock",
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=session_descriptor,
            )
            process_lock_stat = os.fstat(process_lock_descriptor)
            if (
                not stat.S_ISREG(process_lock_stat.st_mode)
                or process_lock_stat.st_nlink != 1
            ):
                raise OSError("unsafe retained event process lock")
            import fcntl

            fcntl.flock(process_lock_descriptor, fcntl.LOCK_EX)
            event_descriptor = os.open(
                "session_events.jsonl",
                os.O_RDWR
                | os.O_APPEND
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=session_descriptor,
            )
            self._verify_identity(
                os.fstat(session_descriptor),
                os.fstat(event_descriptor),
            )
            fcntl.flock(event_descriptor, fcntl.LOCK_EX)
            self._recover_transaction_posix(
                session_descriptor,
                event_descriptor,
            )
            transaction_name = ".session_events.jsonl.txn"
            temporary_name = f"{transaction_name}.tmp"
            original_offset = os.lseek(event_descriptor, 0, os.SEEK_END)
            if (
                self._expected_size is not None
                and original_offset != self._expected_size
            ):
                raise RuntimeError(
                    "retained event journal advanced since session recovery"
                )
            if original_offset + len(payload) > _MAX_RETAINED_EVENT_JOURNAL_BYTES:
                raise RuntimeError("retained event journal exceeds byte limit")
            transaction_descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=session_descriptor,
            )
            try:
                self._write_all(
                    transaction_descriptor,
                    str(original_offset).encode("ascii"),
                )
                os.fsync(transaction_descriptor)
            finally:
                os.close(transaction_descriptor)
            os.replace(
                temporary_name,
                transaction_name,
                src_dir_fd=session_descriptor,
                dst_dir_fd=session_descriptor,
            )
            os.fsync(session_descriptor)
            remove_transaction = False
            try:
                self._write_all(event_descriptor, payload)
                os.fsync(event_descriptor)
                self._verify_identity(
                    os.fstat(session_descriptor),
                    os.fstat(event_descriptor),
                )
                path_stat = os.stat(
                    "session_events.jsonl",
                    dir_fd=session_descriptor,
                    follow_symlinks=False,
                )
                expected_file = self._expected_identity[1]
                if (path_stat.st_dev, path_stat.st_ino) != expected_file:
                    raise RuntimeError("retained event journal identity changed")
                remove_transaction = True
            except BaseException:
                try:
                    os.ftruncate(event_descriptor, original_offset)
                    os.fsync(event_descriptor)
                except BaseException:
                    remove_transaction = False
                    raise
                else:
                    remove_transaction = True
                    raise
            finally:
                if remove_transaction:
                    self._unlink_regular_at(
                        session_descriptor,
                        transaction_name,
                    )
                self._unlink_regular_at(session_descriptor, temporary_name)
                os.fsync(session_descriptor)
            self._expected_size = original_offset + len(payload)
        finally:
            if event_descriptor is not None:
                os.close(event_descriptor)
            if process_lock_descriptor is not None:
                os.close(process_lock_descriptor)
            if session_descriptor is not None:
                os.close(session_descriptor)
            os.close(root_descriptor)

    def recover(self) -> bytes | None:
        event_path = (
            self._event_root / self._session_id / "session_events.jsonl"
        )
        if os.name == "nt":
            handles: list[int] = []
            try:
                handles.append(
                    AnchoredStorage.windows_handle(
                        self._event_root,
                        directory=True,
                        create=False,
                    )
                )
                handles.append(
                    AnchoredStorage.windows_handle(
                        event_path.parent,
                        directory=True,
                        create=False,
                    )
                )
                handles.append(
                    AnchoredStorage.windows_handle(
                        event_path,
                        directory=False,
                        create=False,
                    )
                )
                self._verify_identity(
                    event_path.parent.stat(follow_symlinks=False),
                    event_path.stat(follow_symlinks=False),
                )
                with _RetainedProcessLock(
                    event_path,
                    lock_path=_retained_event_lock_path(
                        self._event_root,
                        self._session_id,
                    ),
                ):
                    retained = _read_retained_event_journal(
                        self._event_root,
                        self._session_id,
                    )
                    self._verify_identity(
                        event_path.parent.stat(follow_symlinks=False),
                        event_path.stat(follow_symlinks=False),
                    )
                    delegate = JsonlEventSink._for_existing_path(
                        event_path,
                        max_bytes=_MAX_RETAINED_EVENT_JOURNAL_BYTES,
                    )
                self._delegate = delegate
                self.path = delegate.path
                self._expected_size = len(retained[0])
                return retained[0]
            finally:
                for handle in reversed(handles):
                    AnchoredStorage.close_windows_handle(handle)
            raise RuntimeError("unreachable retained event recovery state")

        root_descriptor = os.open(
            self._event_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        session_descriptor: int | None = None
        event_descriptor: int | None = None
        process_lock_descriptor: int | None = None
        try:
            session_descriptor = AnchoredStorage.open_directory(
                root_descriptor,
                self._session_id,
                create=False,
            )
            process_lock_descriptor = os.open(
                ".session_events.jsonl.lock",
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=session_descriptor,
            )
            process_lock_stat = os.fstat(process_lock_descriptor)
            if not stat.S_ISREG(process_lock_stat.st_mode):
                raise OSError("unsafe retained event process lock")
            import fcntl

            fcntl.flock(process_lock_descriptor, fcntl.LOCK_EX)
            event_descriptor = os.open(
                "session_events.jsonl",
                os.O_RDWR
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=session_descriptor,
            )
            self._verify_identity(
                os.fstat(session_descriptor),
                os.fstat(event_descriptor),
            )
            fcntl.flock(event_descriptor, fcntl.LOCK_EX)
            self._recover_transaction_posix(
                session_descriptor,
                event_descriptor,
            )
            self._verify_identity(
                os.fstat(session_descriptor),
                os.fstat(event_descriptor),
            )
            path_stat = os.stat(
                "session_events.jsonl",
                dir_fd=session_descriptor,
                follow_symlinks=False,
            )
            if (path_stat.st_dev, path_stat.st_ino) != self._expected_identity[1]:
                raise RuntimeError("retained event journal identity changed")
            current_identity = _retained_event_journal_identity(
                self._event_root,
                self._session_id,
            )
            if current_identity != self._expected_identity:
                raise RuntimeError("retained event journal identity changed")
            event_stat = os.fstat(event_descriptor)
            with os.fdopen(os.dup(event_descriptor), "rb") as stream:
                retained_payload = _read_bounded_event_journal(
                    stream,
                    event_stat.st_size,
                )
            self._expected_size = event_stat.st_size
            return retained_payload
        finally:
            if event_descriptor is not None:
                os.close(event_descriptor)
            if process_lock_descriptor is not None:
                os.close(process_lock_descriptor)
            if session_descriptor is not None:
                os.close(session_descriptor)
            os.close(root_descriptor)

    def _append_windows(self, event: object) -> None:
        event_path = (
            self._event_root / self._session_id / "session_events.jsonl"
        )
        handles: list[int] = []
        try:
            handles.append(
                AnchoredStorage.windows_handle(
                    self._event_root,
                    directory=True,
                    create=False,
                )
            )
            handles.append(
                AnchoredStorage.windows_handle(
                    event_path.parent,
                    directory=True,
                    create=False,
                )
            )
            handles.append(
                AnchoredStorage.windows_handle(
                    event_path,
                    directory=False,
                    create=False,
                )
            )
            with _RetainedProcessLock(
                event_path,
                lock_path=_retained_event_lock_path(
                    self._event_root,
                    self._session_id,
                ),
            ):
                self._verify_identity(
                    event_path.parent.stat(follow_symlinks=False),
                    event_path.stat(follow_symlinks=False),
                )
                if self._delegate is None:
                    raise RuntimeError("retained event sink was not recovered")
                current_size = event_path.stat(follow_symlinks=False).st_size
                if (
                    self._expected_size is not None
                    and current_size != self._expected_size
                ):
                    raise RuntimeError(
                        "retained event journal advanced since session recovery"
                    )
                self._delegate._append_with_process_lock(event)
                self._expected_size = event_path.stat(
                    follow_symlinks=False
                ).st_size
        finally:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)

    def append(self, event: object) -> None:
        try:
            with self._lock:
                if os.name == "nt":
                    self._append_windows(event)
                else:
                    self._append_posix(event)
        except OSError as exc:
            raise RuntimeError("retained event journal identity changed") from exc



def _unsafe_retained_journal(session_id: str, cause: OSError | None = None) -> ReplayError:
    error = ReplayError(
        "unsafe_event_journal",
        f"retained session {session_id!r} has an unsafe logical event journal",
    )
    if cause is not None:
        error.__cause__ = cause
    return error




def _restore_product_session(
    session_id: str,
    state_paths: ManagedStatePaths | None = None,
    *,
    event_root: Path | None = None,
) -> ProductSession:
    selected_event_root = (
        event_root if event_root is not None else _event_root(state_paths)
    )
    event_path = selected_event_root / session_id / "session_events.jsonl"
    initial_journal: tuple[bytes, tuple[int, int], tuple[int, int]] | None
    try:
        initial_journal = _read_retained_event_journal(
            selected_event_root,
            session_id,
        )
    except FileNotFoundError:
        initial_journal = None
    except OSError as exc:
        raise _unsafe_retained_journal(session_id, exc)
    try:
        if initial_journal is None:
            JsonlEventSink(event_path)
            raise FileNotFoundError(event_path)
        protected_sink = _RetainedEventSink(
            None,
            selected_event_root,
            session_id,
            initial_journal[1:],
        )
        try:
            recovered_payload = protected_sink.recover()
            if recovered_payload is None:
                retained_journal = _read_retained_event_journal(
                    selected_event_root,
                    session_id,
                )
                retained_payload = retained_journal[0]
                if retained_journal[1:] != initial_journal[1:]:
                    raise _unsafe_retained_journal(session_id)
            else:
                retained_payload = recovered_payload
        except OSError as exc:
            raise _unsafe_retained_journal(session_id, exc)
        except RuntimeError as exc:
            if "identity changed" in str(exc):
                raise _unsafe_retained_journal(session_id) from exc
            raise
        events = [
            event_from_record(record)
            for line in retained_payload.decode("utf-8").splitlines()
            if line.strip()
            for record in (json.loads(line),)
        ]
        restored = ProductSession.restore(events, sink=protected_sink)
    except ReplayError:
        raise
    except FileNotFoundError as exc:
        raise ReplayError(
            "missing_event_stream",
            f"retained session {session_id!r} has no logical event journal",
        ) from exc
    except RuntimeError as exc:
        recovery_cause = exc.__cause__ or exc.__context__
        if not isinstance(recovery_cause, (UnicodeError, ValueError)):
            raise
        raise ReplayError(
            "invalid_event_record",
            f"retained session {session_id!r} has an invalid logical event journal",
        ) from exc
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ReplayError(
            "invalid_event_record",
            f"retained session {session_id!r} has an invalid logical event journal",
        ) from exc
    if restored.read_model.session_id != session_id:
        raise ReplayError(
            "event_identity_mismatch",
            f"retained session {session_id!r} logical event identity mismatch",
        )
    return restored


def _sync_tree(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted(
        (root, *(item for item in root.rglob("*") if item.is_dir())),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        AnchoredStorage.sync_directory(path)




def _start_active(path: Path) -> bool:
    try:
        pid = int((path / _START_OWNER).read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def _create_owned_stage(path: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(16).hex()}.start-owner")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.mkdir(mode=0o700)
        (temporary / _START_OWNER).write_text(str(os.getpid()), encoding="utf-8")
        _sync_tree(temporary)
        temporary.replace(path)
        AnchoredStorage.sync_directory(path.parent)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _cleanup_incomplete_starts(
    record_root: Path | None = None,
    event_root: Path | None = None,
    *,
    state_paths: ManagedStatePaths | None = None,
) -> None:
    managed = state_paths if state_paths is not None else managed_state_paths()
    if managed is not None:
        record_root = record_root or managed.runtime_records
        event_root = event_root or managed.session_events
    else:
        record_root = record_root or default_runtime_record_root()
        event_root = event_root or _event_root()
    for root in (record_root, event_root):
        for staged in root.glob(".*.start-owner") if root.is_dir() else ():
            if not _start_active(staged):
                shutil.rmtree(staged, ignore_errors=True)
    for staged in (
        record_root.glob(".*.records.starting") if record_root.is_dir() else ()
    ):
        session_id = staged.name[1 : -len(".records.starting")]
        if _start_active(staged):
            continue
        if not (record_root / session_id / _START_COMMITTED).is_file():
            shutil.rmtree(event_root / session_id, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)
    for bundle in record_root.iterdir() if record_root.is_dir() else ():
        if (
            bundle.is_dir()
            and (bundle / _START_PENDING).exists()
            and not (bundle / _START_COMMITTED).exists()
            and not _start_active(bundle)
        ):
            shutil.rmtree(bundle, ignore_errors=True)
            shutil.rmtree(event_root / bundle.name, ignore_errors=True)
    for staged in event_root.glob(".*.events.starting") if event_root.is_dir() else ():
        if _start_active(staged):
            continue
        session_id = staged.name[1 : -len(".events.starting")]
        authority, target = (
            record_root / session_id / _START_COMMITTED,
            event_root / session_id,
        )
        if authority.is_file():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            for path in staged.iterdir():
                (target / path.name).exists() or path.replace(target / path.name)
            (target / _START_OWNER).unlink(missing_ok=True)
            shutil.rmtree(staged, ignore_errors=True)
            _sync_tree(target)
        else:
            shutil.rmtree(staged, ignore_errors=True)
    for root in (record_root, event_root):
        AnchoredStorage.sync_directory(root) if root.is_dir() else None


def _prepare_start_stages(
    record_root: Path,
    event_root: Path,
    runtime_record_dir: Path,
    event_dir: Path,
    active_stages: set[Path],
    publish_records: bool,
) -> list[Path]:
    record_root.parent.mkdir(parents=True, exist_ok=True)
    with ProcessLock(record_root.parent / f"{record_root.name}.start-staging"):
        _cleanup_incomplete_starts(record_root, event_root)
        if event_dir.exists() or publish_records and runtime_record_dir.exists():
            raise RuntimeError(f"session bundle already exists: {event_dir.name}")
        created: list[Path] = []
        try:
            for path in active_stages:
                _create_owned_stage(path)
                created.append(path)
            return created
        except BaseException:
            for path in created:
                shutil.rmtree(path, ignore_errors=True)
                if path.parent.is_dir():
                    AnchoredStorage.sync_directory(path.parent)
            raise


@dataclass(frozen=True)
class SessionContractReadiness:
    ready: bool
    reason: str


@dataclass(frozen=True)
class PreparedEventStream:
    record: SessionRecord
    queue: "asyncio.Queue[Optional[SessionEvent]]"


class DurableChildReconcilerProtocol(Protocol):
    """Cancellation-capable restart boundary for retained child sessions."""

    def __call__(self, recovery_ref: str) -> Any: ...

    def cancel(self, recovery_ref: str, *, reason: str = "operator request") -> Any: ...

    def cancel_tree(
        self, parent_session_id: str, *, reason: str = "operator request"
    ) -> Any: ...


class SessionService:
    """Facade that coordinates the registry, runners, and FastAPI endpoints."""

    def __init__(
        self,
        registry: SessionRegistry | None = None,
        *,
        state_root: str | Path | None = None,
        subscriber_queue_maxsize: int | None = None,
        durable_child_reconciler: DurableChildReconcilerProtocol | None = None,
        durable_child_repository: WorkItemRepository | None = None,
    ) -> None:
        self._managed_state_paths = prepare_managed_state()
        if self._managed_state_paths is not None:
            configured_state_root = self._managed_state_paths.session_state
            if (
                state_root is not None
                and Path(state_root).resolve() != configured_state_root
            ):
                raise ManagedStateRootError()
            if registry is not None:
                registry_root = getattr(registry, "_state_root", None)
                if (
                    registry_root is None
                    or Path(registry_root).resolve() != configured_state_root
                ):
                    raise ManagedStateRootError()
        else:
            configured_state_root = (
                state_root
                or os.environ.get("BREADBOARD_SESSION_STATE_ROOT")
                or (default_runtime_record_root() / "session_state")
            )
        self.registry = registry or SessionRegistry(
            configured_state_root,
            process_identity=get_engine_process_identity(),
            bootstrap_verifier=get_launch_bootstrap_verifier(),
        )
        child_repository = durable_child_repository
        if durable_child_reconciler is None and child_repository is not None:
            child_orchestrator = MultiAgentOrchestrator(TeamConfig("durable-child-runtime"))
            durable_child_reconciler = DurableChildReconciler(
                registry=self.registry,
                repository=child_repository,
                adapter_factories=(ProcessExecutionAdapter,),
                adapters=(RayJobAdapter(child_orchestrator),),
            )
        if durable_child_reconciler is not None and any(
            not callable(getattr(durable_child_reconciler, method, None))
            for method in ("__call__", "cancel", "cancel_tree")
        ):
            raise TypeError(
                "durable_child_reconciler must implement __call__, cancel, and cancel_tree"
            )
        self._durable_child_repository = child_repository
        self._durable_child_reconciler = durable_child_reconciler
        self._subscriber_queue_maxsize = max(
            1,
            subscriber_queue_maxsize
            if subscriber_queue_maxsize is not None
            else REPLAY_RETENTION_MAX_EVENTS + 2,
        )
        self._bridge_chaos = _load_bridge_chaos_metadata()
        self._atp_repl_enabled = _env_flag("ATP_REPL_ENABLE") or _env_flag(
            "ATP_REPL_ROUTE"
        )
        self._atp_repl_service: Any | None = None
        self._atp_service_initialized = False
        self._atp_runtime_capabilities: dict[str, Any] = {}
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._workspace_upload_locks: weakref.WeakValueDictionary[
            str, asyncio.Lock
        ] = weakref.WeakValueDictionary()
        _cleanup_incomplete_starts(state_paths=self._managed_state_paths)

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id.casefold(), asyncio.Lock())

    def _workspace_upload_lock(self, workspace_dir: Path) -> asyncio.Lock:
        key = os.path.normcase(str(workspace_dir.resolve()))
        return self._workspace_upload_locks.setdefault(key, asyncio.Lock())

    @staticmethod
    def _runtime_lock(
        session_id: str, runtime_config: dict[str, Any], source_ref: str
    ) -> EffectiveHarnessLock:
        graph = compile_runtime_effective_config_graph(
            session_id, runtime_config, source_ref
        )
        role_lock = runtime_config.get("model_role_lock")
        if isinstance(role_lock, Mapping):
            graph = embed_model_role_lock(
                graph,
                restore_model_role_lock(
                    role_lock,
                    broker=get_provider_broker(),
                    session_id=session_id,
                ),
            )
        return EffectiveHarnessLock._from_record(graph)

    @staticmethod
    def _configured_model_catalog(
        runtime_config: dict[str, Any],
        *,
        session_id: str,
    ) -> dict[str, Any]:
        providers = (
            runtime_config.get("providers") if isinstance(runtime_config, dict) else {}
        )
        providers = providers if isinstance(providers, dict) else {}
        configured = providers.get("models") or []
        default_model = providers.get("default_model") or runtime_config.get("model")
        if not configured and default_model:
            configured = [{"id": default_model}]
        entries, issues = build_model_catalog(
            configured,
            credential_origin=lambda route: provider_router.get_credential_origin(
                str(route), session_id=session_id
            ),
        )
        return {
            "models": [
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else dict(item)
                for item in entries
            ],
            "issues": [
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else dict(item)
                for item in issues
            ],
        }

    @classmethod
    def _compile_session_role_lock(
        cls,
        runtime_config: dict[str, Any],
        *,
        session_id: str,
        role_document: Any | None = None,
    ):
        if role_document is None:
            role_document = runtime_config.get("model_roles")
        if role_document is None:
            return None
        catalog = cls._configured_model_catalog(runtime_config, session_id=session_id)
        return compile_model_roles(
            role_document,
            broker=get_provider_broker(),
            session_id=session_id,
            bind_session_accounts=True,
            catalog=catalog,
        )

    def _publication_boundary(self, _name: str) -> None:
        pass

    def _publish_start_bundle(
        self,
        session_id: str,
        staged_record_dir: Path,
        staging_record_root: Path,
        runtime_record_dir: Path,
        staged_event_dir: Path,
        event_dir: Path,
        publish_records: bool,
    ) -> None:
        bundle = staged_record_dir if publish_records else staged_event_dir
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / _START_OWNER).write_text(str(os.getpid()), encoding="utf-8")
        if publish_records:
            bundle.mkdir(parents=True, exist_ok=True)
            (bundle / _START_PENDING).write_text(session_id + "\n", encoding="utf-8")
            _sync_tree(bundle)
            _sync_tree(staged_event_dir)
            self._publication_boundary("records")
            if runtime_record_dir == event_dir:
                for path in staged_event_dir.iterdir():
                    path.replace(bundle / path.name)
                shutil.rmtree(staged_event_dir)
                _sync_tree(bundle)
            self._publication_boundary("events")
        else:
            _sync_tree(bundle)
            self._publication_boundary("records")
            self._publication_boundary("events")
        temporary, marker = (
            bundle / f"{_START_COMMITTED}.tmp",
            bundle / _START_COMMITTED,
        )
        with temporary.open("xb") as stream:
            stream.write((session_id + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        AnchoredStorage.sync_directory(bundle)
        self._publication_boundary("commit")
        target = runtime_record_dir if publish_records else event_dir
        bundle.replace(target)
        AnchoredStorage.sync_directory(target.parent)
        self._publication_boundary("authority")
        if publish_records:
            shutil.rmtree(staging_record_root, ignore_errors=True)
        if publish_records and runtime_record_dir != event_dir:
            staged_event_dir.replace(event_dir)
            AnchoredStorage.sync_directory(event_dir.parent)
        for owner in {target / _START_OWNER, event_dir / _START_OWNER}:
            owner.unlink(missing_ok=True)
        for root in {target, event_dir}:
            AnchoredStorage.sync_directory(root) if root.is_dir() else None

    def p30_session_contract_readiness(
        self,
        contract_descriptor: dict[str, Any],
        *,
        session_replay_contract_digest: str,
    ) -> SessionContractReadiness:
        http_contract = contract_descriptor.get("http")
        if not isinstance(http_contract, dict) or http_contract.get("missing_routes"):
            return SessionContractReadiness(
                ready=False, reason="session_contract_missing"
            )
        if (
            p30_session_schema_sha256(contract_descriptor) != P30_SESSION_SCHEMA_SHA256
            or session_replay_contract_digest != P30_SESSION_REPLAY_CONTRACT_DIGEST
        ):
            return SessionContractReadiness(
                ready=False, reason="session_contract_mismatch"
            )
        return SessionContractReadiness(ready=True, reason="ready")

    async def issue_bootstrap_challenge(
        self,
        request: BootstrapChallengeRequest,
    ) -> BootstrapChallengeResponse:
        return await self.registry.issue_bootstrap_challenge(request)

    async def acquire_owner(
        self,
        request: OwnerAcquireRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        return await self.registry.acquire_owner(
            request, owner_credential=owner_credential
        )

    async def renew_owner(
        self,
        request: OwnerLeaseRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        return await self.registry.renew_owner(
            request, owner_credential=owner_credential
        )

    async def release_owner(
        self,
        request: OwnerLeaseRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        return await self.registry.release_owner(
            request, owner_credential=owner_credential
        )

    async def register_client(
        self,
        request: ClientRegisterRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        return await self.registry.register_client(
            request, registration_credential=registration_credential
        )

    async def renew_client(
        self,
        request: ClientLeaseRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        return await self.registry.renew_client(
            request, registration_credential=registration_credential
        )

    async def detach_client(
        self,
        request: ClientLeaseRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        return await self.registry.detach_client(
            request, registration_credential=registration_credential
        )

    async def begin_control_drain(
        self,
        request: BeginControlDrainRequest,
        *,
        owner_credential: bytearray,
        registration_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.begin_control_drain(
            request,
            owner_credential=owner_credential,
            registration_credential=registration_credential,
        )

    async def record_graceful_control(
        self,
        request: GracefulControlResultRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.record_graceful_control(
            request, owner_credential=owner_credential
        )

    async def prepare_hard_signal(
        self,
        request: HardSignalPrepareRequest,
        *,
        owner_credential: bytearray,
    ) -> HardSignalPreparationResponse:
        return await self.registry.prepare_hard_signal(
            request, owner_credential=owner_credential
        )

    async def commit_hard_signal(
        self,
        request: HardSignalCommitRequest,
        *,
        owner_credential: bytearray,
    ) -> HardSignalPermitResponse:
        return await self.registry.commit_hard_signal(
            request, owner_credential=owner_credential
        )

    async def record_hard_signal_outcome(
        self,
        request: HardSignalOutcomeRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.record_hard_signal_outcome(
            request, owner_credential=owner_credential
        )

    async def rollback_control_drain(
        self,
        request: DrainControlRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.rollback_control_drain(
            request, owner_credential=owner_credential
        )

    async def create_session(
        self,
        request: SessionCreateRequest,
        *,
        session_id: str | None = None,
        event_root: Path | None = None,
        runtime_root: Path | None = None,
        effective_lock: EffectiveHarnessLock | None = None,
    ) -> SessionCreateResponse:
        selected_session_id = session_id or str(uuid.uuid4())
        async with self._session_lock(selected_session_id):
            collision = await self.registry.resolve_session_id(selected_session_id)
            if collision is not None:
                raise ValueError(f"session already exists: {selected_session_id}")
            return await self._create_session(
                request,
                session_id=selected_session_id,
                event_root=event_root,
                runtime_root=runtime_root,
                effective_lock=effective_lock,
            )


    async def _create_session(
        self,
        request: SessionCreateRequest,
        *,
        session_id: str | None = None,
        event_root: Path | None = None,
        runtime_root: Path | None = None,
        effective_lock: EffectiveHarnessLock | None = None,
    ) -> SessionCreateResponse:
        if effective_lock is not None and not isinstance(
            effective_lock, EffectiveHarnessLock
        ):
            raise TypeError("effective_lock must be an EffectiveHarnessLock")
        await self.registry.ensure_session_admission_open()
        session_id = session_id or str(uuid.uuid4())
        if await self.registry.get(session_id) is not None:
            raise ValueError(f"session already exists: {session_id}")
        durable_product_workspace: Path | None = None
        default_profile: DefaultProfileResolution | None = None
        if request.config_path is None:
            default_profile = resolve_default_profile()
            default_lock = default_profile.compilation.lock
            if (
                effective_lock is not None
                and effective_lock.as_dict() != default_lock.as_dict()
            ):
                raise ValueError(
                    "supplied effective lock conflicts with packaged default profile"
                )
            request = request.model_copy(
                update={"config_path": str(default_profile.source_path)}
            )
        else:
            request = request.model_copy(
                update={
                    "config_path": str(
                        Path(str(request.config_path)).expanduser().resolve()
                    )
                }
            )
        retained_request_overrides = retained_runtime_overrides(
            request.overrides, reject_unsupported=True
        )
        default_profile_overridden = any(
            key != "workspace.root" for key in (request.overrides or {})
        )
        bundled_role_bindings_overridden = any(
            key in {"model", "model_roles", "providers"}
            or key.startswith(("model_roles.", "providers."))
            for key in (request.overrides or {})
        )
        request_metadata = dict(request.metadata or {})
        role_document = request_metadata.pop(MODEL_ROLES_METADATA_KEY, None)
        if (
            role_document is None
            and default_profile is not None
            and not bundled_role_bindings_overridden
        ):
            role_document = load_daily_driver_model_roles()
        for reserved_key in (
            "config_path",
            "default_profile",
            "profile_id",
            "definition_ref",
            "schema_version",
            "source_sha256",
            "profile_hash",
            "effective_lock_schema_version",
            "effective_lock_hash",
            "workspace",
            _SESSION_EVENT_ROOT_METADATA_KEY,
            _SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY,
            "artifact_manifest_ref",
            "runtime_overrides",
            "skills_selection",
        ):
            request_metadata.pop(reserved_key, None)
        if default_profile is not None:
            default_identity = default_profile.public_identity()
            request_metadata["config_path"] = str(default_identity["definition_ref"])
            request_metadata["default_profile"] = default_identity
        else:
            request_metadata["config_path"] = str(request.config_path)
        request = request.model_copy(update={"metadata": request_metadata})
        metadata = dict(request.metadata or {})
        if self._bridge_chaos:
            metadata.setdefault("bridgeChaos", self._bridge_chaos)
        session_title = (
            request.task if request.task.strip() else DEFAULT_INTERACTIVE_SESSION_TITLE
        )
        record = SessionRecord(
            session_id=session_id, status=SessionStatus.STARTING, metadata=metadata
        )
        runner = SessionRunner(session=record, registry=self.registry, request=request)
        runtime_config = runner.prepare_runtime_config()
        if retained_request_overrides:
            metadata["runtime_overrides"] = retained_request_overrides
        if runner.request.workspace:
            metadata["workspace"] = str(
                Path(runner.request.workspace).expanduser().resolve()
            )
        if runner.request.workspace is not None:
            candidate_workspace = Path(runner.request.workspace).expanduser().resolve()
            requested_event_root = (event_root or _event_root()).expanduser().resolve()
            durable_event_root = (
                session_event_path(candidate_workspace, session_id).parent.parent.resolve()
            )
            if requested_event_root == durable_event_root:
                durable_product_workspace = candidate_workspace
                metadata[_SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY] = str(
                    candidate_workspace
                )
        runtime_providers = (
            runtime_config.get("providers")
            if isinstance(runtime_config, dict)
            else None
        )
        if not metadata.get("model"):
            selected_model = (
                runtime_providers.get("default_model")
                if isinstance(runtime_providers, dict)
                else None
            ) or runtime_config.get("model")
            if selected_model:
                metadata["model"] = str(selected_model)
        if (
            not metadata.get("mode")
            and isinstance(runtime_config, dict)
            and runtime_config.get("mode")
        ):
            metadata["mode"] = str(runtime_config["mode"])
        role_lock = self._compile_session_role_lock(
            runtime_config,
            session_id=session_id,
            role_document=role_document,
        )
        if role_lock is not None:
            runtime_config = runner.install_model_role_lock(role_lock)
            persisted_runtime_config = _sanitize_persisted_runtime_config(
                runtime_config
            )
        else:
            persisted_runtime_config = _sanitize_persisted_runtime_config(
                runtime_config
            )
        runtime_graph = compile_runtime_effective_config_graph(
            session_id, persisted_runtime_config, request.config_path
        )
        if role_lock is not None:
            runtime_graph = embed_model_role_lock(runtime_graph, role_lock)
            metadata["model_role_lock_hash"] = role_lock.lock_hash
            metadata["model_role_lock"] = role_lock.as_dict()
            metadata["active_model_role"] = role_lock["defaults"]["role"]
            metadata["model_role_default"] = role_lock["defaults"]["role"]
        if (
            default_profile is not None
            and not default_profile_overridden
            and role_lock is None
        ):
            runtime_lock = default_profile.compilation.lock
        elif effective_lock is not None:
            selected_graph = effective_lock.as_dict()
            if role_lock is not None:
                selected_graph = embed_model_role_lock(selected_graph, role_lock)
            runtime_lock = EffectiveHarnessLock._from_record(selected_graph)
        else:
            runtime_lock = EffectiveHarnessLock._from_record(runtime_graph)
        emit_primitives = primitive_emission_enabled()
        if self._managed_state_paths is not None:
            runtime_base = self._managed_state_paths.runtime_records
            event_base = self._managed_state_paths.session_events
        else:
            runtime_base = runtime_root or default_runtime_record_root()
            event_base = event_root or _event_root()
        event_base = event_base.expanduser().resolve()
        metadata[_SESSION_EVENT_ROOT_METADATA_KEY] = str(event_base)
        runtime_record_dir, event_dir = (
            runtime_base / session_id,
            event_base / session_id,
        )
        staging_record_root = (
            runtime_record_dir.parent / f".{session_id}.records.starting"
        )
        staged_record_dir, staged_event_dir = (
            staging_record_root / session_id,
            event_dir.with_name(f".{session_id}.events.starting"),
        )
        active_stages = {
            staged_event_dir,
            *({staging_record_root} if emit_primitives else set()),
        }
        created_stages = await asyncio.to_thread(
            _prepare_start_stages,
            runtime_record_dir.parent,
            event_dir.parent,
            runtime_record_dir,
            event_dir,
            active_stages,
            emit_primitives,
        )
        if emit_primitives:
            metadata.setdefault("runtime_record_dir", str(runtime_record_dir))
        record.runner, published = runner, False
        retained_refresh_failed = False
        try:
            if emit_primitives:
                staged_paths = emit_session_start_records(
                    session_id=session_id,
                    request=request,
                    title=session_title,
                    output_root=staging_record_root,
                    effective_runtime_config=runtime_config,
                    model_role_lock=role_lock,
                )
                metadata.setdefault(
                    "runtime_records",
                    {
                        name: str(
                            runtime_record_dir
                            / Path(path).relative_to(staged_record_dir)
                        )
                        for name, path in staged_paths.items()
                    },
                )
            event_sink = JsonlEventSink(
                staged_event_dir / "session_events.jsonl",
                max_bytes=_MAX_RETAINED_EVENT_JOURNAL_BYTES,
            )
            product_session = ProductSession.start(
                runtime_lock, session_title, session_id=session_id, sink=event_sink
            )
            initial_event_journal_size = (
                staged_event_dir / "session_events.jsonl"
            ).stat(follow_symlinks=False).st_size
            record.product_session = product_session
            async with self.registry.publish_session(record, runner):
                with _RetainedProcessLock(
                    staged_event_dir / "session_events.jsonl",
                    lock_path=_retained_event_lock_path(
                        event_base,
                        session_id,
                    ),
                ):
                    runner.schedule_start()
                    self._publish_start_bundle(
                        session_id,
                        staged_record_dir,
                        staging_record_root,
                        runtime_record_dir,
                        staged_event_dir,
                        event_dir,
                        emit_primitives,
                    )
                    event_sink.path = event_dir / "session_events.jsonl"
                    live_sink = _RetainedEventSink(
                        event_sink,
                        event_base,
                        session_id,
                        _retained_event_journal_identity(
                            event_base,
                            session_id,
                        ),
                    )
                    live_sink._expected_size = initial_event_journal_size
                    if event_sink.path.stat(
                        follow_symlinks=False
                    ).st_size != initial_event_journal_size:
                        raise RuntimeError(
                            "retained event journal advanced before live sink binding"
                        )
                    product_session._sink = live_sink
                    published = True
            if durable_product_workspace is not None:
                runner.bind_durable_product_session(
                    durable_product_workspace,
                    session_directory_identity(
                        durable_product_workspace,
                        create=True,
                    ),
                )
            await self._ensure_dispatcher(record)
            await self._maybe_prewarm_request_runtime(request, metadata, runtime_config)
            final_event_path = event_dir / "session_events.jsonl"
            final_lock_path = _retained_event_lock_path(event_base, session_id)
            for _ in range(2):
                with _RetainedProcessLock(
                    final_event_path,
                    lock_path=final_lock_path,
                ):
                    bound_sink = getattr(product_session, "_sink", None)
                    expected_identity = getattr(
                        bound_sink,
                        "_expected_identity",
                        None,
                    )
                    current_identity = _retained_event_journal_identity(
                        event_base,
                        session_id,
                    )
                    expected_size = getattr(bound_sink, "_expected_size", None)
                    current_size = final_event_path.stat(
                        follow_symlinks=False
                    ).st_size
                    if (
                        expected_identity == current_identity
                        and expected_size == current_size
                    ):
                        runner.authorize_start()
                        break
                try:
                    product_session = _restore_product_session(
                        session_id,
                        event_root=event_base,
                    )
                except BaseException:
                    retained_refresh_failed = True
                    record.product_session = None
                    raise
                record.product_session = product_session
            else:
                retained_refresh_failed = True
                record.product_session = None
                raise RuntimeError(
                    "retained event journal advanced before start authorization"
                )
        except BaseException:
            published = (
                published
                or (
                    (runtime_record_dir if emit_primitives else event_dir)
                    / _START_COMMITTED
                ).is_file()
            )
            if published and "event_sink" in locals():
                event_sink.path = (
                    event_dir
                    if (event_dir / "session_events.jsonl").is_file()
                    else staged_event_dir
                ) / "session_events.jsonl"
            if published:
                (staged_event_dir / _START_OWNER).unlink(missing_ok=True)
            if not retained_refresh_failed:
                try:
                    runner.transition_product_session(
                        "fail", "session_setup_failed", "session setup failed"
                    )
                except Exception:
                    logger.exception(
                        "Failed to terminalize session %s after setup failure",
                        session_id,
                    )
            try:
                await runner.stop()
            except Exception:
                logger.exception(
                    "Failed to stop session %s after setup failure", session_id
                )
            if record.dispatcher_task and not record.dispatcher_task.done():
                await record.event_queue.put(None)
                await asyncio.gather(record.dispatcher_task, return_exceptions=True)
            if not published:
                for path in (
                    runtime_record_dir,
                    staging_record_root,
                    event_dir,
                    staged_event_dir,
                ):
                    shutil.rmtree(path, ignore_errors=True)
                for root in (runtime_record_dir.parent, event_dir.parent):
                    if root.exists():
                        AnchoredStorage.sync_directory(root)
            else:
                await self.registry.update_status(session_id, SessionStatus.FAILED)
            raise
        logger.info("Session %s created", session_id)
        return SessionCreateResponse(
            session_id=session_id,
            status=record.projected_status(),
            created_at=record.created_at,
            logging_dir=record.logging_dir,
        )

    async def _maybe_prewarm_request_runtime(
        self,
        request: SessionCreateRequest,
        metadata: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> None:
        if not self._should_prewarm_request_runtime(metadata):
            return
        try:
            await asyncio.to_thread(
                self._prewarm_request_runtime_sync, request, metadata, runtime_config
            )
        except Exception as exc:
            logger.debug("Codex prewarm skipped: %s", exc)

    def _should_prewarm_request_runtime(self, metadata: dict[str, Any]) -> bool:
        return bool(
            metadata.get("non_interactive_cli_session")
            or str(metadata.get("cli_session_kind") or "").strip().lower()
            in {"oneshot", "interactive", "repl"}
        )

    def _prewarm_request_runtime_sync(
        self,
        request: SessionCreateRequest,
        metadata: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        providers = config.get("providers", {}) if isinstance(config, dict) else {}
        selected_model = (
            metadata.get("model")
            or (request.overrides or {}).get("providers.default_model")
            or providers.get("default_model")
            or (config.get("model") if isinstance(config, dict) else None)
        )
        if not selected_model:
            return
        model_ref = str(selected_model).strip()
        if not model_ref:
            return
        descriptor, routed_model = provider_router.get_runtime_descriptor(model_ref)
        if descriptor.runtime_id != "codex_app_server":
            return
        workspace = str(request.workspace or os.getcwd()).strip() or os.getcwd()
        runtime_codex_module.prewarm_codex_app_server(model=routed_model, cwd=workspace)

    @staticmethod
    def _stream_open_event(record: SessionRecord) -> SessionEvent:
        return SessionEvent(
            type=EventType.STREAM_OPEN,
            session_id=record.session_id,
            payload=replay_retention_facts(
                record.event_log,
                head_sequence=record.event_seq,
                retained_history_partial=record.replay_history_partial,
                persisted_head_event_id=record.replay_head_event_id,
            ),
            stable_cursor=False,
        )

    async def prepare_event_stream(
        self,
        session_id: str,
        *,
        replay: bool = False,
        limit: int | None = None,
        from_id: str | None = None,
    ) -> PreparedEventStream:
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)
        queue: "asyncio.Queue[Optional[SessionEvent]]" = asyncio.Queue()
        queue.put_nowait(self._stream_open_event(record))
        await self._register_subscriber(
            record, queue, replay=replay, limit=limit, from_id=from_id, validated=True
        )
        return PreparedEventStream(record=record, queue=queue)

    async def prepared_event_stream(
        self,
        prepared: PreparedEventStream,
    ) -> AsyncIterator[SessionEvent]:
        try:
            while True:
                event = await prepared.queue.get()
                if event is None:
                    break
                yield event
        finally:
            await self._unregister_subscriber(prepared.record, prepared.queue)

    async def cancel_turn(
        self,
        session_id: str,
        turn_id: str,
        payload: SessionTurnCancelRequest,
    ) -> SessionTurnCancelResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="session not active",
            )
        key = payload.cancellation_request_key
        key_digest = identity_digest(key)
        body_digest = cancellation_body_digest(turn_id, payload.reason)
        retry_queued_turn: TurnRecord | None = None
        response: SessionTurnCancelResponse | None = None
        existing: CancellationRecord | None = None
        turn: TurnRecord | None = None
        cancellation: CancellationRecord | None = None
        disposition: str | None = None
        async with record.admission_lock:
            existing = record.cancellations_by_key.get(key)
            if existing is None:
                existing = record.cancellations_by_key_digest.get(key_digest)
            if existing is not None:
                if existing.body_digest != body_digest:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "cancellation_idempotency_conflict",
                            "turn_id": existing.turn_id,
                        },
                    )
                response = SessionTurnCancelResponse(
                    cancellation_request_id=existing.cancellation_request_id,
                    cancellation_request_key=key,
                    input_id=existing.input_id,
                    turn_id=existing.turn_id,
                    disposition="deduplicated",
                    original_disposition=existing.original_disposition,
                )
                retained_turn = record.turns_by_id.get(existing.turn_id)
                if (
                    existing.original_disposition == "queued_cancelled"
                    and retained_turn is not None
                    and retained_turn.terminal_outcome is None
                ):
                    retry_queued_turn = retained_turn
                else:
                    return response
            else:
                turn = record.turns_by_id.get(turn_id)
                if turn is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="turn not found",
                    )
                if turn.terminal_outcome is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="turn is already terminal",
                    )
                queued_index: int | None = None
                if record.active_turn_id == turn_id:
                    disposition = "cancellation_requested"
                elif turn.state == "queued":
                    disposition = "queued_cancelled"
                    try:
                        queued_index = record.queued_turn_ids.index(turn_id)
                    except ValueError:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="turn is not cancellable",
                        ) from None
                else:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="turn is not cancellable",
                    )
                previous_cancellation_requested = turn.cancellation_requested
                previous_cancellation_reason = turn.cancellation_reason
                turn.cancellation_requested = True
                turn.cancellation_reason = payload.reason
                cancellation = CancellationRecord(
                    cancellation_request_id=uuid.uuid4().hex,
                    cancellation_request_key=key,
                    turn_id=turn_id,
                    input_id=turn.input_id,
                    reason=payload.reason,
                    original_disposition=disposition,
                    body_digest=body_digest,
                )
                record.cancellations_by_key[key] = cancellation
                record.cancellations_by_key_digest[key_digest] = cancellation
                if queued_index is not None:
                    record.queued_turn_ids.remove(turn_id)
                try:
                    await self.registry.persist(record)
                except Exception:
                    turn.cancellation_requested = previous_cancellation_requested
                    turn.cancellation_reason = previous_cancellation_reason
                    record.cancellations_by_key.pop(key, None)
                    record.cancellations_by_key_digest.pop(key_digest, None)
                    if queued_index is not None:
                        record.queued_turn_ids.insert(queued_index, turn_id)
                    raise
                response = SessionTurnCancelResponse(
                    cancellation_request_id=cancellation.cancellation_request_id,
                    cancellation_request_key=key,
                    input_id=turn.input_id,
                    turn_id=turn_id,
                    disposition=disposition,
                    original_disposition=disposition,
                )
        if retry_queued_turn is not None:
            assert existing is not None
            assert response is not None
            await runner.finish_queued_turn_cancellation(
                retry_queued_turn,
                existing.reason,
            )
            return response
        assert turn is not None
        assert disposition is not None
        assert response is not None
        if disposition == "queued_cancelled":
            await runner.finish_queued_turn_cancellation(turn, payload.reason)
        elif not runner.request_turn_cancellation(turn_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="turn is no longer active",
            )
        return response

    async def ensure_session(self, session_id: str) -> SessionRecord:
        record = await self.registry.get(session_id)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session not found",
            )
        if not record.loaded_from_retained_state:
            return record
        async with self._session_lock(session_id):
            return await self._ensure_session_locked(session_id)

    async def _ensure_session_locked(self, session_id: str) -> SessionRecord:
        record = await self.registry.get(session_id)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session not found",
            )
        if record.loaded_from_retained_state:
            await self._resume_retained_session(record)
        return record
    @staticmethod
    def _bind_restored_durable_product_session(
        record: SessionRecord,
        runner: SessionRunner,
    ) -> None:
        workspace_value = record.metadata.get(
            _SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY
        )
        if not isinstance(workspace_value, str) or not workspace_value.strip():
            return
        workspace = Path(workspace_value).expanduser().resolve()
        runner.bind_durable_product_session(
            workspace,
            session_directory_identity(workspace, create=True),
        )

    @staticmethod
    def _restore_retained_workspace_attachments(
        record: SessionRecord,
        runner: SessionRunner,
    ) -> None:
        if record.metadata.get(_SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY):
            return
        workspace_value = record.metadata.get("workspace")
        if not isinstance(workspace_value, str) or not workspace_value.strip():
            return
        runner.artifacts.restore_manifest(
            Path(workspace_value).expanduser().resolve()
        )

    async def _resume_retained_session(self, record: SessionRecord) -> None:
        metadata = dict(record.metadata or {})
        durable_child = metadata.get("durable_child")
        if isinstance(durable_child, Mapping):
            recovery_ref = str(durable_child.get("recovery_ref") or "").strip()
            reconciler = self._durable_child_reconciler
            if not recovery_ref:
                raise RuntimeError("durable child retained state has no recovery reference")
            if not callable(reconciler):
                raise RuntimeError("durable child recovery requires an authoritative reconciler")
            reconciled = reconciler(recovery_ref)
            if inspect.isawaitable(reconciled):
                reconciled = await reconciled
            if (
                not getattr(reconciled, "terminal_count", 0)
                and not isinstance(metadata.get("durable_parent_cancellation"), Mapping)
                and (
                    getattr(reconciled, "status", None) == "starting"
                    or getattr(reconciled, "cancellation_requested", False)
                )
            ):
                record.runner = None
                return
            recorded_workspace = metadata.get("workspace")
            workspace = (
                str(recorded_workspace).strip()
                if isinstance(recorded_workspace, str) and recorded_workspace.strip()
                else None
            )
            if workspace is None:
                raise RuntimeError("durable child retained state has no workspace")
            try:
                product, _ = load_session(workspace, record.session_id)
            except FileNotFoundError:
                if record.status not in {
                    SessionStatus.COMPLETED,
                    SessionStatus.FAILED,
                    SessionStatus.STOPPED,
                }:
                    raise
            else:
                record.product_session = product
            record.runner = None
            if not isinstance(metadata.get("durable_parent_cancellation"), Mapping):
                record.loaded_from_retained_state = False
                return
        parent_cancellation = metadata.get("durable_parent_cancellation")
        if record.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        } and not isinstance(parent_cancellation, Mapping):
            record.loaded_from_retained_state = False
            return
        repository = self._durable_child_repository
        if isinstance(parent_cancellation, Mapping) and self._durable_child_repository is None:
            raise RuntimeError(
                "durable parent cancellation requires an authoritative WorkItemRepository"
            )
        if isinstance(parent_cancellation, Mapping) and repository is not None:
            work_item_id = str(parent_cancellation.get("work_item_id") or "").strip()
            reason = str(parent_cancellation.get("reason") or "operator request")
            workspace = str(metadata.get("workspace") or "").strip()
            if work_item_id and workspace:
                child_refs = parent_cancellation.get("child_recovery_refs") or ()
                if not isinstance(child_refs, (list, tuple)) or any(not isinstance(ref, str) or not ref.strip() for ref in child_refs):
                    raise RuntimeError("durable parent cancellation child references are invalid")
                cancel_child = getattr(self._durable_child_reconciler, "cancel", None)
                reconcile_child = getattr(self._durable_child_reconciler, "__call__", None)
                for child_ref in child_refs:
                    if not callable(cancel_child):
                        if not callable(reconcile_child):
                            raise RuntimeError("durable parent cancellation cannot replay child cancellation")
                        repaired = reconcile_child(child_ref)
                        if inspect.isawaitable(repaired):
                            repaired = await repaired
                        if getattr(repaired, "terminal_count", 0) != 1:
                            raise RuntimeError("durable parent cancellation replay did not settle child")
                        continue
                    try:
                        result = cancel_child(child_ref, reason=reason)
                        if inspect.isawaitable(result):
                            result = await result
                        if getattr(result, "terminal_count", 0) != 1:
                            raise RuntimeError(
                                "durable parent cancellation replay did not settle child"
                            )
                    except (ExpectedRevisionConflict, LateResultRejected):
                        if not callable(reconcile_child):
                            raise
                        repaired = reconcile_child(child_ref)
                        if inspect.isawaitable(repaired):
                            repaired = await repaired
                        if getattr(repaired, "terminal_count", 0) != 1:
                            raise RuntimeError(
                                "durable parent cancellation replay did not settle child"
                            )
                parent_product, _ = load_session(workspace, record.session_id)
                parent_work = WorkItem.restore(repository, work_item_id)
                product_status = parent_product.read_model.status
                work_status = parent_work.read_model.status
                try:
                    if work_status in {"canceled", "completed", "failed"} and product_status not in {
                        "canceled",
                        "completed",
                        "failed",
                    }:
                        if work_status == "completed":
                            mutate_session(
                                workspace,
                                record.session_id,
                                lambda current: current.complete("replayed Work Item completion"),
                            )
                        elif work_status == "failed":
                            mutate_session(
                                workspace,
                                record.session_id,
                                lambda current: current.fail("work_item", "replayed Work Item failure"),
                            )
                        else:
                            mutate_session(
                                workspace,
                                record.session_id,
                                lambda current: current.cancel(reason),
                            )
                    elif product_status in {"canceled", "completed", "failed"} and work_status not in {
                        "canceled",
                        "completed",
                        "failed",
                    }:
                        if product_status == "completed":
                            attempt = parent_work.read_model.current_attempt
                            if attempt is None:
                                raise RuntimeError(
                                    "completed Product Session has no active Work Item attempt"
                                )
                            parent_work.complete(
                                "replayed Product Session completion",
                                attempt_id=attempt.attempt_id,
                            )
                        elif product_status == "failed":
                            parent_work.fail("product_session", "replayed Product Session failure")
                        else:
                            parent_work.cancel("operator", reason)
                    elif product_status not in {
                        "canceled",
                        "completed",
                        "failed",
                    }:
                        mutate_session(
                            workspace,
                            record.session_id,
                            lambda current: current.cancel(reason),
                        )
                        parent_work.cancel("operator", reason)
                except (RuntimeError, ValueError) as error:
                    raise RuntimeError(
                        "durable parent cancellation cannot reconcile owners"
                    ) from error
                parent_product, _ = load_session(workspace, record.session_id)
                parent_work = WorkItem.restore(repository, work_item_id)
                if (
                    parent_product.read_model.status
                    not in {"canceled", "completed", "failed"}
                    or parent_work.read_model.status
                    not in {"canceled", "completed", "failed"}
                    or parent_product.read_model.status != parent_work.read_model.status
                ):
                    raise RuntimeError("durable parent cancellation did not settle owners")
                bridge_status = {
                    "completed": SessionStatus.COMPLETED,
                    "failed": SessionStatus.FAILED,
                    "canceled": SessionStatus.STOPPED,
                }[parent_product.read_model.status]
                record.product_session = parent_product
                await self.registry.update_status(record.session_id, bridge_status)
                metadata["durable_parent_cancellation"] = None
                await self.registry.update_metadata(record.session_id, metadata=metadata)
                record.loaded_from_retained_state = False
                return
        metadata = dict(record.metadata or {})
        recorded_event_root = metadata.get(_SESSION_EVENT_ROOT_METADATA_KEY)
        recorded_workspace = metadata.get("workspace")
        retained_workspace = (
            Path(recorded_workspace).expanduser().absolute()
            if isinstance(recorded_workspace, str) and recorded_workspace.strip()
            else None
        )
        discovered_workspace_journal = False
        if (
            not isinstance(recorded_event_root, str)
            or not recorded_event_root.strip()
        ) and retained_workspace is not None:
            managed_event_root = _event_root(self._managed_state_paths)
            workspace_event_root = session_event_path(
                retained_workspace,
                record.session_id,
            ).parent.parent
            workspace_event_journal = (
                workspace_event_root
                / record.session_id
                / "session_events.jsonl"
            )
            if (
                workspace_event_journal.is_file()
                and workspace_event_journal.resolve() == workspace_event_journal
            ):
                retained_event_root = workspace_event_root
                discovered_workspace_journal = True
            elif (
                managed_event_root / record.session_id / "session_events.jsonl"
            ).is_file():
                retained_event_root = managed_event_root
            else:
                retained_event_root = managed_event_root
        else:
            retained_event_root = (
                Path(recorded_event_root).expanduser().absolute()
                if isinstance(recorded_event_root, str)
                and recorded_event_root.strip()
                else _event_root(self._managed_state_paths)
            )
        if discovered_workspace_journal and not (
            isinstance(
                metadata.get(_SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY),
                str,
            )
            and str(
                metadata.get(_SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY)
            ).strip()
        ):
            metadata[_SESSION_DURABLE_PRODUCT_WORKSPACE_METADATA_KEY] = str(
                retained_workspace
            )
            record.metadata = metadata
        record.product_session = _restore_product_session(
            record.session_id,
            self._managed_state_paths,
            event_root=retained_event_root,
        )
        restored_status = record.projected_status()
        if restored_status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.STOPPED,
        }:
            terminal_runner = SessionRunner(
                session=record,
                registry=self.registry,
                request=SessionCreateRequest(task="", metadata=dict(record.metadata or {})),
            )
            self._bind_restored_durable_product_session(
                record,
                terminal_runner,
            )
            self._restore_retained_workspace_attachments(record, terminal_runner)
            terminal_runner.reconcile_retained_input_admissions()
            terminal_outcome = {
                SessionStatus.COMPLETED: "completed",
                SessionStatus.FAILED: "failed",
                SessionStatus.STOPPED: "cancelled",
            }[restored_status]
            await terminal_runner._terminalize_admitted_turns(
                outcome=terminal_outcome,
                reason="restored_terminal_session",
                error_code=(
                    "runtime_failure"
                    if restored_status is SessionStatus.FAILED
                    else None
                ),
            )
            terminal_runner._commit_terminal_product_session_locked()
            await self.registry.update_status(record.session_id, restored_status)
            async with record.dispatch_lock:
                setattr(record, "_dispatcher_complete", True)
            record.loaded_from_retained_state = False
            return
        try:
            metadata = dict(record.metadata or {})
            recorded_config_path = str(metadata.get("config_path") or "").strip()
            retained_config_path = (
                Path(recorded_config_path).expanduser()
                if recorded_config_path
                else None
            )
            if retained_config_path is not None and retained_config_path.is_absolute():
                config_path = str(retained_config_path)
            else:
                profile = resolve_default_profile()
                default_identity = profile.public_identity()
                config_path = (
                    str(profile.source_path)
                    if not recorded_config_path
                    or recorded_config_path == default_identity["definition_ref"]
                    else recorded_config_path
                )
            recorded_workspace = metadata.get("workspace")
            workspace = (
                str(recorded_workspace).strip()
                if isinstance(recorded_workspace, str)
                and recorded_workspace.strip()
                else None
            )
            permission_mode = str(
                metadata.get("permission_mode") or "configured"
            ).strip().lower()
            if permission_mode not in {
                "prompt",
                "ask",
                "interactive",
                "configured",
            }:
                permission_mode = "configured"
            metadata["permission_mode"] = permission_mode
            runtime_overrides = metadata.get("runtime_overrides")
            request_overrides = retained_runtime_overrides(runtime_overrides)
            record.metadata = metadata
            runner = SessionRunner(
                session=record,
                registry=self.registry,
                request=SessionCreateRequest(
                    config_path=config_path,
                    task="",
                    overrides=request_overrides,
                    metadata=metadata,
                    workspace=workspace,
                    permission_mode=permission_mode,
                ),
            )
            runtime_config = runner.prepare_runtime_config()
            rebuilt_generation = self._runtime_lock(
                record.session_id,
                runtime_config,
                runner.request.config_path,
            ).as_dict()["graph_hash"]
        except Exception as error:
            raise ReplayError(
                "generation_unavailable",
                f"retained session {record.session_id!r} runtime generation cannot be restored",
            ) from error
        if rebuilt_generation != record.product_session.pinned_generation_id:
            raise ReplayError(
                "generation_mismatch",
                f"retained session {record.session_id!r} runtime generation does not match its durable journal",
            )
        self._bind_restored_durable_product_session(
            record,
            runner,
        )
        self._restore_retained_workspace_attachments(record, runner)
        runner.reconcile_retained_input_admissions()
        if record.product_session.read_model.status == "awaiting_approval":
            pending_approval = record.product_session.read_model.pending_approval
            if pending_approval is None:
                raise ReplayError(
                    "invalid_event_record",
                    f"retained session {record.session_id!r} has no pending approval identity",
                )
            record.product_session.resolve_approval(pending_approval, "deny")
        for turn in record.turns_by_id.values():
            if turn.terminal_outcome is not None:
                continue
            if turn.cancellation_requested:
                await runner._finish_turn(
                    turn,
                    "cancelled",
                    reason=turn.cancellation_reason,
                    advance_queue=False,
                )
            else:
                await runner._finish_turn(
                    turn,
                    "failed",
                    error_code="runtime_failure",
                    advance_queue=False,
                )
        record.active_turn_id = None
        record.queued_turn_ids.clear()
        record.turn_admission = record.turn_admission.__class__.IDLE
        record.runner = runner
        runner.schedule_start()
        runner.authorize_start()
        record.loaded_from_retained_state = False
    async def event_stream(
        self,
        session_id: str,
        *,
        replay: bool = False,
        limit: Optional[int] = None,
        from_id: Optional[str] = None,
        validated: bool = False,
    ) -> AsyncIterator[SessionEvent]:
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)
        subscriber: asyncio.Queue[Optional[SessionEvent]] = asyncio.Queue()
        await self._register_subscriber(
            record,
            subscriber,
            replay=replay,
            limit=limit,
            from_id=from_id,
            validated=validated,
        )
        try:
            while True:
                event = await subscriber.get()
                if event is None:
                    break
                yield event
        finally:
            await self._unregister_subscriber(record, subscriber)

    async def _ensure_dispatcher(self, record: SessionRecord) -> None:
        task = record.dispatcher_task
        if getattr(record, "_dispatcher_complete", False) or task and not task.done():
            return
        record.dispatcher_task = asyncio.get_running_loop().create_task(
            self._dispatch_events(record)
        )

    async def _register_subscriber(
        self,
        record: SessionRecord,
        queue: "asyncio.Queue[Optional[SessionEvent]]",
        *,
        replay: bool = False,
        limit: Optional[int] = None,
        from_id: Optional[str] = None,
        validated: bool = False,
    ) -> None:
        replay_enabled = replay or bool(from_id)
        async with record.dispatch_lock:
            if replay_enabled:
                self._ensure_event_sequence(record)
                events = list(record.event_log)
                if from_id:
                    start_index = self._resolve_start_index(events, from_id)
                    if start_index is not None and not self._retained_replay_suffix_is_contiguous(
                        record,
                        events,
                        start_index,
                    ):
                        start_index = None
                    if start_index is None and self._is_retained_head_cursor(record, from_id):
                        events = [
                            event for event in events
                            if event.seq is not None and event.seq > record.replay_head_sequence
                        ]
                    elif start_index is None:
                        if not validated:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail={
                                    "message": "resume window exceeded or event id not found",
                                    "code": "resume_window_exceeded",
                                    "last_event_id": from_id,
                                    "event_log_size": len(events),
                                    "first_seq": events[0].seq if events else None,
                                    "last_seq": events[-1].seq if events else None,
                                },
                            )
                        events = []
                    else:
                        events = events[start_index:]
                if isinstance(limit, int) and limit > 0:
                    events = events[-limit:]
                for event in events:
                    queue.put_nowait(event)
            else:
                # Snapshot-on-reconnect: if the client connects without replay/from_id,
                # push the most recent todo snapshot into its queue so the TUI can
                # converge even when history is missing (resume window exceeded, etc).
                envelope = (
                    record.metadata.get("todo_last_update")
                    if isinstance(record.metadata, dict)
                    else None
                )
                if not isinstance(envelope, dict):
                    runner = getattr(record, "runner", None)
                    workspace_dir = runner.get_workspace_dir() if runner else None
                    if workspace_dir:
                        try:
                            from breadboard_engine.todo import TodoStore
                            from breadboard_engine.todo.projection import (
                                project_store_snapshot_to_tui_envelope,
                            )

                            store = TodoStore(str(workspace_dir), load_existing=True)
                            envelope = project_store_snapshot_to_tui_envelope(
                                store.snapshot(), scope_key="main", scope_label="main"
                            )
                        except Exception:
                            envelope = None
                if isinstance(envelope, dict):
                    queue.put_nowait(
                        SessionEvent(
                            EventType.TOOL_RESULT,
                            record.session_id,
                            {
                                "call_id": f"todo:snapshot:connect:{uuid.uuid4().hex[:8]}",
                                "todo": envelope,
                            },
                        )
                    )
            if getattr(record, "_dispatcher_complete", False):
                queue.put_nowait(None)
            else:
                record.subscribers[queue] = SubscriberState(queue=queue)

    async def _unregister_subscriber(
        self,
        record: SessionRecord,
        queue: "asyncio.Queue[Optional[SessionEvent]]",
    ) -> None:
        async with record.dispatch_lock:
            try:
                record.subscribers.pop(queue, None)
            except Exception:
                pass

    async def _dispatch_events(self, record: SessionRecord) -> None:
        """Fan-out events from the producer queue to all subscribers."""
        while True:
            event = await record.event_queue.get()
            if event is None:
                record.event_queue.task_done()
                break
            try:
                async with record.dispatch_lock:
                    previous_event_seq = record.event_seq
                    previous_event_seq_value = event.seq
                    record.event_seq += 1
                    if event.seq is None:
                        event.seq = record.event_seq
                    else:
                        record.event_seq = max(record.event_seq, int(event.seq))
                    try:
                        if event.type in {
                            EventType.TURN_COMPLETED,
                            EventType.TURN_FAILED,
                            EventType.TURN_CANCELLED,
                        }:
                            await self.registry.persist(record, terminal_event=event)
                        else:
                            await self.registry.persist(record, cursor_event=event)
                    except Exception:
                        record.event_seq = previous_event_seq
                        event.seq = previous_event_seq_value
                        # Never expose an event cursor that is not durably resumable.
                        setattr(record, "_dispatcher_complete", True)
                        break
                    record.event_log.append(event)
                    if record.subscribers:
                        for subscriber in list(record.subscribers):
                            try:
                                subscriber.put_nowait(event)
                            except asyncio.QueueFull:
                                # Drop on overflow; subscribers are best-effort observers.
                                continue
            finally:
                record.event_queue.task_done()
        while True:
            try:
                record.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                record.event_queue.task_done()
        async with record.dispatch_lock:
            setattr(record, "_dispatcher_complete", True)
            subscribers = list(record.subscribers)
            for subscriber in subscribers:
                try:
                    subscriber.put_nowait(None)
                except asyncio.QueueFull:
                    subscriber.get_nowait()
                    subscriber.put_nowait(None)

    async def list_sessions(self):
        return await self.registry.list()

    async def list_session_records(
        self,
        session_id: str,
        *,
        schema_version: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        record = await self.ensure_session(session_id)
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        runtime_base = (
            self._managed_state_paths.runtime_records
            if self._managed_state_paths is not None
            else default_runtime_record_root()
        )
        runtime_dir = (
            runtime_base / session_id
            if self._managed_state_paths is not None
            else (
                Path(str(metadata["runtime_record_dir"]))
                if metadata.get("runtime_record_dir")
                else runtime_base / session_id
            )
        )
        rows: list[dict[str, Any]] = []
        committed = (
            not (runtime_dir / _START_PENDING).exists()
            or (runtime_dir / _START_COMMITTED).exists()
        )
        if committed:
            for path in sorted((runtime_dir / "records").glob("*.jsonl")):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line_no, line in enumerate(lines, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    row_record = (
                        payload.get("record")
                        if isinstance(payload, dict)
                        and isinstance(payload.get("record"), dict)
                        else payload
                    )
                    row_schema = (
                        row_record.get("schema_version")
                        if isinstance(row_record, dict)
                        else None
                    )
                    if row_schema is None and isinstance(payload, dict):
                        row_schema = payload.get("schema_version")
                    if not schema_version or row_schema == schema_version:
                        rows.append(
                            {
                                "schema_version": row_schema,
                                "path": str(path),
                                "line": line_no,
                                "record": row_record,
                            }
                        )
        safe_offset, safe_limit = max(0, int(offset)), max(1, min(int(limit), 1000))
        return {
            "session_id": session_id,
            "records": rows[safe_offset : safe_offset + safe_limit],
            "offset": safe_offset,
            "limit": safe_limit,
            "total": len(rows),
        }

    async def list_skills(self, session_id: str) -> SkillCatalogResponse:
        record = await self.ensure_session(session_id)
        runner = record.runner
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session runner not found"
            )
        payload = runner.get_skill_catalog()
        return SkillCatalogResponse(
            catalog=payload.get("catalog") or {},
            selection=payload.get("selection"),
            sources=payload.get("sources"),
        )

    async def get_ctree_snapshot(self, session_id: str) -> CTreeSnapshotResponse:
        record = await self.ensure_session(session_id)
        runner = record.runner
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session runner not found"
            )
        payload = runner.get_ctree_snapshot()
        return CTreeSnapshotResponse(
            snapshot=payload.get("snapshot"),
            compiler=payload.get("compiler"),
            collapse=payload.get("collapse"),
            runner=payload.get("runner"),
            last_node=payload.get("last_node"),
        )

    async def get_limits_status(self, session_id: str) -> dict[str, Any] | None:
        from .events import EventType

        record = await self.ensure_session(session_id)
        try:
            for event in reversed(list(record.event_log)):
                event_type = getattr(event, "type", None)
                if (
                    event_type == EventType.LIMITS_UPDATE
                    or getattr(event_type, "value", None)
                    == EventType.LIMITS_UPDATE.value
                ):
                    payload = getattr(event, "payload", None)
                    if isinstance(payload, dict):
                        return dict(payload)
                    return None
        except Exception:
            return None
        return None

    async def validate_event_stream(
        self,
        session_id: str,
        *,
        from_id: Optional[str] = None,
        replay: bool = False,
    ) -> None:
        if not from_id:
            return
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)
        async with record.dispatch_lock:
            if not (replay or from_id):
                return
            self._ensure_event_sequence(record)
            events = list(record.event_log)
            start_index = self._resolve_start_index(events, from_id)
            if start_index is not None and not self._retained_replay_suffix_is_contiguous(
                record,
                events,
                start_index,
            ):
                start_index = None
            if start_index is None and not self._is_retained_head_cursor(record, from_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "resume window exceeded or event id not found",
                        "code": "resume_window_exceeded",
                        "last_event_id": from_id,
                        "event_log_size": len(events),
                        "first_seq": events[0].seq if events else None,
                        "last_seq": events[-1].seq if events else None,
                    },
                )

    def _ensure_event_sequence(self, record: SessionRecord) -> None:
        seq = record.event_seq
        for event in record.event_log:
            if event.seq is None:
                seq += 1
                event.seq = seq
            else:
                seq = max(seq, int(event.seq))
        record.event_seq = seq
    @staticmethod
    def _is_retained_head_cursor(record: SessionRecord, from_id: str) -> bool:
        return (
            record.replay_head_sequence > 0
            and record.replay_head_event_id is not None
            and from_id
            in {
                record.replay_head_event_id,
                str(record.replay_head_sequence),
            }
        )

    @staticmethod
    def _retained_replay_suffix_is_contiguous(
        record: SessionRecord,
        events: list[SessionEvent],
        start_index: int,
    ) -> bool:
        if not record.replay_history_partial:
            return True
        if start_index <= 0:
            return False
        cursor_sequence = events[start_index - 1].seq
        replay_head = record.replay_head_sequence
        if cursor_sequence is None or cursor_sequence > replay_head:
            return False
        expected_sequence = cursor_sequence + 1
        for event in events[start_index:]:
            if event.seq != expected_sequence or expected_sequence > replay_head:
                return False
            expected_sequence += 1
        return expected_sequence == replay_head + 1

    def _resolve_start_index(
        self, events: list[SessionEvent], from_id: str
    ) -> Optional[int]:
        seq_value: Optional[int] = None
        try:
            if from_id is not None:
                seq_value = int(from_id)
        except ValueError:
            seq_value = None
        if seq_value is not None:
            for idx, event in enumerate(events):
                if event.seq == seq_value:
                    return idx + 1
        for idx, event in enumerate(events):
            if event.event_id == from_id:
                return idx + 1
        return None

    async def stop_session(self, session_id: str, *, reason: str | None = None) -> None:
        async with self._session_lock(session_id):
            await self._stop_session_locked(session_id, reason=reason)

    async def _stop_session_locked(
        self, session_id: str, *, reason: str | None = None
    ) -> None:
        record = await self._ensure_session_locked(session_id)
        initial_metadata = record.metadata if isinstance(record.metadata, Mapping) else {}
        if isinstance(initial_metadata.get("durable_child"), Mapping) and not callable(
            getattr(self._durable_child_reconciler, "cancel", None)
        ):
            raise RuntimeError("durable child cancellation requires an authoritative reconciler")
        cancel_tree = getattr(self._durable_child_reconciler, "cancel_tree", None)
        records = await self.registry.records()
        retained_children = []
        for child_record in records:
            child_metadata = (
                child_record.metadata
                if isinstance(child_record.metadata, Mapping)
                else {}
            )
            child_state = child_metadata.get("durable_child")
            if (
                isinstance(child_state, Mapping)
                and child_state.get("parent_session_id") == session_id
                and not int(child_state.get("terminal_count", 0) or 0)
            ):
                retained_children.append(child_state)
        if retained_children and not callable(cancel_tree):
            raise RuntimeError(
                "cannot stop parent with retained children without an authoritative reconciler"
            )
        if callable(cancel_tree):
            try:
                result = cancel_tree(session_id, reason=reason or "operator request")
                if inspect.isawaitable(result):
                    await result
            except (ExpectedRevisionConflict, LateResultRejected):
                reconcile_child = getattr(self._durable_child_reconciler, "__call__", None)
                if callable(reconcile_child):
                    records = await self.registry.records()
                    for child_record in records:
                        child_metadata = child_record.metadata if isinstance(child_record.metadata, Mapping) else {}
                        child_state = child_metadata.get("durable_child")
                        if not isinstance(child_state, Mapping) or child_state.get("parent_session_id") != session_id:
                            continue
                        child_ref = str(child_state.get("recovery_ref") or "").strip()
                        if not child_ref:
                            continue
                        try:
                            repaired = reconcile_child(child_ref)
                            if inspect.isawaitable(repaired):
                                await repaired
                        except (ExpectedRevisionConflict, LateResultRejected):
                            continue
                    retry = cancel_tree(session_id, reason=reason or "operator request")
                    if inspect.isawaitable(retry):
                        await retry
        metadata = dict(record.metadata or {})
        durable_child = metadata.get("durable_child")
        recovery_ref = (
            str(durable_child.get("recovery_ref") or "").strip()
            if isinstance(durable_child, Mapping)
            else ""
        )
        cancel_child = getattr(self._durable_child_reconciler, "cancel", None)
        if recovery_ref and callable(cancel_child) and not (
            isinstance(durable_child, Mapping)
            and int(durable_child.get("terminal_count", 0) or 0)
        ):
            try:
                result = cancel_child(recovery_ref, reason=reason or "operator request")
                if inspect.isawaitable(result):
                    await result
            except (ExpectedRevisionConflict, LateResultRejected):
                reconcile_child = getattr(self._durable_child_reconciler, "__call__", None)
                if callable(reconcile_child):
                    try:
                        repaired = reconcile_child(recovery_ref)
                        if inspect.isawaitable(repaired):
                            await repaired
                    except (ExpectedRevisionConflict, LateResultRejected):
                        pass
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        try:
            if runner:
                await runner.stop(reason) if reason is not None else await runner.stop()
        finally:
            try:
                product_session: ProductSession | None = getattr(
                    record, "product_session", None
                )
                terminal_status = {
                    "canceled": SessionStatus.STOPPED,
                    "completed": SessionStatus.COMPLETED,
                    "failed": SessionStatus.FAILED,
                }.get(
                    getattr(
                        getattr(product_session, "read_model", None), "status", None
                    )
                )
                if terminal_status is not None:
                    await self.registry.update_status(session_id, terminal_status)
            finally:
                dispatcher = getattr(record, "dispatcher_task", None)
                if dispatcher and not dispatcher.done():
                    await record.event_queue.put(None)
                    await dispatcher

    async def delete_session(self, session_id: str) -> None:
        async with self._session_lock(session_id):
            try:
                await self._stop_session_locked(session_id)
            except ReplayError:
                pass
            await self.registry.delete(session_id)
    async def send_input(
        self,
        session_id: str,
        payload: SessionInputRequest,
        *,
        defer_execution: Callable[[Callable[[], Awaitable[None]]], None] | None = None,
    ) -> SessionInputResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="session not active"
            )
        client_message_id = payload.client_message_id or uuid.uuid4().hex
        try:
            attachments = tuple(
                SessionRunner.canonicalize_input_attachments(payload.attachments)
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        body_digest = submission_body_digest(payload.content, attachments)
        key_digest = identity_digest(client_message_id)
        scheduled_after_admission: list[Callable[[], Awaitable[None]]] = []

        async def admit() -> SessionInputResponse:
            async with record.admission_lock:
                existing = record.submissions_by_key.get(
                    client_message_id
                ) or record.submissions_by_key_digest.get(key_digest)
                if existing is not None:
                    if existing.body_digest != body_digest:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "code": "input_idempotency_conflict",
                                "turn_id": existing.turn_id,
                            },
                        )
                    return SessionInputResponse(
                        client_message_id=client_message_id,
                        input_id=existing.input_id,
                        turn_id=existing.turn_id,
                        disposition="deduplicated",
                        original_disposition=existing.original_disposition,
                    )
                if record.admission_closed:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="session admission is closed",
                    )
                accepted_content = runner.prepare_input_content(payload.content)
                disposition = "started" if record.active_turn_id is None else "queued"
                product_session = getattr(record, "product_session", None)
                event_count = (
                    getattr(
                        getattr(product_session, "read_model", None),
                        "event_count",
                        None,
                    )
                    if product_session is not None
                    else None
                )
                session_status = getattr(
                    getattr(product_session, "read_model", None), "status", None
                )
                event_count_is_valid = type(event_count) is int and event_count >= 1
                content_hash = (
                    "sha256:"
                    + hashlib.sha256(accepted_content.encode("utf-8")).hexdigest()
                    if event_count_is_valid
                    else None
                )
                turn = TurnRecord(
                    input_id=f"input-{uuid.uuid4().hex}",
                    turn_id=f"turn-{uuid.uuid4().hex}",
                    client_message_id=client_message_id,
                    content=accepted_content,
                    attachments=attachments,
                    original_disposition=disposition,
                    state="active" if disposition == "started" else "queued",
                    body_digest=body_digest,
                    logical_input_content_hash=content_hash,
                    logical_event_count_before_admission=(
                        event_count if event_count_is_valid else None
                    ),
                    logical_input_session_status_before_admission=(
                        session_status if event_count_is_valid else None
                    ),
                )
                record.turns_by_id[turn.turn_id] = turn
                record.submissions_by_key[client_message_id] = turn
                record.submissions_by_key_digest[key_digest] = turn
                if disposition == "started":
                    record.active_turn_id = turn.turn_id
                else:
                    record.queued_turn_ids.append(turn.turn_id)
                record.turn_admission = record.turn_admission.__class__.ACTIVE
                scheduled_operations: list[Callable[[], Awaitable[None]]] = []
                admission_persisted = False
                logical_input_committed = False
                try:
                    runner.validate_input_admission(
                        accepted_content,
                        attachments,
                        input_id=turn.input_id,
                        turn_id=turn.turn_id,
                    )
                    await self.registry.persist(record)
                    admission_persisted = True
                    accepted_content = await runner.enqueue_input(
                        accepted_content,
                        attachments=list(attachments),
                        input_id=turn.input_id,
                        turn_id=turn.turn_id,
                        defer_execution=scheduled_operations.append,
                    )
                    logical_input_committed = True
                    if len(scheduled_operations) != 1:
                        raise RuntimeError("input execution was not scheduled exactly once")
                    turn.content = accepted_content
                    if payload.content != accepted_content:
                        runner.record_input_boundary_repair(
                            payload.content,
                            accepted_content,
                        )
                except Exception as exc:
                    if not logical_input_committed:
                        record.turns_by_id.pop(turn.turn_id, None)
                        record.submissions_by_key.pop(client_message_id, None)
                        record.submissions_by_key_digest.pop(key_digest, None)
                        if record.active_turn_id == turn.turn_id:
                            record.active_turn_id = None
                        else:
                            try:
                                record.queued_turn_ids.remove(turn.turn_id)
                            except ValueError:
                                pass
                        record.turn_admission = (
                            record.turn_admission.__class__.ACTIVE
                            if record.active_turn_id is not None
                            else record.turn_admission.__class__.IDLE
                        )
                        if admission_persisted:
                            await self.registry.persist(record)
                    if not isinstance(exc, (ValueError, RuntimeError)):
                        raise
                    http_status = (
                        status.HTTP_400_BAD_REQUEST
                        if isinstance(exc, ValueError)
                        else status.HTTP_409_CONFLICT
                    )
                    raise HTTPException(
                        status_code=http_status, detail=str(exc)
                    ) from exc
                scheduled_operation = scheduled_operations[0]

                async def execute_admitted_turn() -> None:
                    try:
                        await scheduled_operation()
                    except Exception:
                        advance_queue = (
                            turn.state == "active"
                            and record.active_turn_id == turn.turn_id
                        )
                        await runner._finish_turn(
                            turn,
                            "failed",
                            error_code="runtime_failure",
                            advance_queue=advance_queue,
                        )
                        logger.warning(
                            "Input execution failed after durable admission",
                            extra={
                                "session_id": record.session_id,
                                "turn_id": turn.turn_id,
                            },
                        )

                scheduled_after_admission.append(execute_admitted_turn)
                return SessionInputResponse(
                    client_message_id=client_message_id,
                    input_id=turn.input_id,
                    turn_id=turn.turn_id,
                    disposition=disposition,
                    original_disposition=disposition,
                )

        response = await self.registry.admit_turn(admit)
        if scheduled_after_admission:
            if len(scheduled_after_admission) != 1:
                raise RuntimeError("admitted input has an invalid execution schedule")
            scheduled_operation = scheduled_after_admission[0]
            if defer_execution is None:
                await scheduled_operation()
            else:
                defer_execution(scheduled_operation)
        return response

    async def execute_command(
        self, session_id: str, payload: SessionCommandRequest
    ) -> SessionCommandResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="session not active"
            )

        def durable_reconfigure(runtime_config: dict[str, Any]) -> None:
            # Use the applied agent config when present; before agent setup the
            # prepared candidate is the authoritative immutable input.
            agent_config = getattr(getattr(runner, "_agent", None), "config", None)
            candidate_config = (
                dict(agent_config)
                if isinstance(agent_config, dict)
                else dict(runtime_config)
            )
            candidate_lock = self._runtime_lock(
                session_id, candidate_config, runner.request.config_path
            )
            runner.transition_product_session(
                "reconfigure",
                candidate_lock,
                payload.command,
            )

        reconfigure_commands = {
            "set_model",
            "set_mode",
            "set_skills",
            "set_role",
            "set_model_role",
        }
        try:
            if payload.command in reconfigure_commands:
                async with record.admission_lock:
                    if record.admission_closed:
                        raise GenerationAdoptionError(
                            "admission_closed",
                            "generation adoption is closed for this session",
                        )
                    if record.active_turn_id is not None or record.queued_turn_ids:
                        raise GenerationAdoptionError(
                            "non_quiescent",
                            "generation adoption requires a quiescent turn boundary",
                        )
                    runner._admission_lock_owner = asyncio.current_task()
                    try:
                        detail = await runner.handle_command(
                            payload.command,
                            payload.payload,
                            durable_reconfigure=durable_reconfigure,
                        )
                    finally:
                        runner._admission_lock_owner = None
            else:
                detail = await runner.handle_command(
                    payload.command,
                    payload.payload,
                    durable_reconfigure=None,
                )
        except ModelRoleResolutionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=exc.problem.to_dict()
            ) from exc
        except GenerationAdoptionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": exc.code, "detail": exc.detail},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except NotImplementedError as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            product_session: ProductSession | None = getattr(
                record, "product_session", None
            )
            if product_session and product_session.read_model.status == "failed":
                await runner.stop()
                await self.registry.update_status(session_id, SessionStatus.FAILED)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        return SessionCommandResponse(detail=detail)

    async def upload_attachments(
        self,
        session_id: str,
        files: Sequence[UploadFile],
        metadata: Optional[dict[str, Any]] = None,
    ) -> AttachmentUploadResponse:
        async with self._session_lock(session_id):
            if not files:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="no files provided"
                )
            record = await self._ensure_session_locked(session_id)
            runner: Optional[SessionRunner] = getattr(record, "runner", None)
            if not runner:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="session not active"
                )
            workspace_dir = runner.get_workspace_dir()
            if not workspace_dir:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="workspace not ready"
                )
            async def persist_upload() -> None:
                await self.registry.persist(record)

            async with self._workspace_upload_lock(workspace_dir):
                return await runner.artifacts.upload(
                    files,
                    workspace_dir=workspace_dir,
                    metadata=metadata,
                    persist=persist_upload,
                )


    @staticmethod
    def _resolve_workspace_path(workspace_dir: Path, requested_path: str) -> Path:
        candidate = (requested_path or ".").strip() or "."
        if os.path.isabs(candidate):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file paths must be workspace-relative",
            )
        workspace_root = workspace_dir.resolve()
        resolved = (workspace_root / candidate).resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="invalid path"
            ) from exc
        return resolved

    async def list_files(
        self, session_id: str, root: str = "."
    ) -> list[SessionFileInfo]:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="session not active"
            )
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="workspace not ready"
            )
        target = self._resolve_workspace_path(workspace_dir, root)
        if not target.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="path not found"
            )

        def to_info(path: Path) -> SessionFileInfo:
            rel = path.relative_to(workspace_dir).as_posix()
            if path.is_dir():
                return SessionFileInfo(path=rel, type="directory")
            stat = path.stat()
            return SessionFileInfo(path=rel, type="file", size=stat.st_size)

        if target.is_file():
            return [to_info(target)]
        children = sorted(
            target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
        )
        return [to_info(child) for child in children]

    async def read_file(
        self,
        session_id: str,
        file_path: str,
        *,
        mode: str = "cat",
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
    ) -> SessionFileContent:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="session not active"
            )
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="workspace not ready"
            )
        if not file_path or not str(file_path).strip() or str(file_path).strip() == ".":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="file path required"
            )
        target = self._resolve_workspace_path(workspace_dir, str(file_path))
        if not target.exists() or not target.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="file not found"
            )
        stat = target.stat()
        total_bytes = stat.st_size
        if mode == "snippet":
            resolved_head_lines = 200 if head_lines is None else head_lines
            resolved_tail_lines = 80 if tail_lines is None else tail_lines
            resolved_max_bytes = 80_000 if max_bytes is None else max_bytes
            snippet, returned_bytes = self._read_snippet(
                target,
                head_lines=resolved_head_lines,
                tail_lines=resolved_tail_lines,
                max_bytes=resolved_max_bytes,
            )
            return SessionFileContent(
                path=target.relative_to(workspace_dir).as_posix(),
                content=snippet,
                truncated=True if returned_bytes < total_bytes else False,
                total_bytes=total_bytes,
            )
        # Optional: bounded reads for "cat" to keep focus/raw mode performant on large artifacts.
        if mode == "cat":
            effective_tail_lines = (
                None if tail_lines is None else max(0, int(tail_lines))
            )
            effective_max_bytes = None if max_bytes is None else max(1, int(max_bytes))
            if effective_tail_lines is not None and effective_max_bytes is None:
                # Defensive fallback: avoid unbounded reads if caller asked for tail lines but omitted a byte cap.
                effective_max_bytes = 80_000
            if (
                effective_tail_lines is not None
                and effective_tail_lines > 0
                and effective_max_bytes is not None
            ):
                content, meta = _TAIL_LINE_INDEX_CACHE.read_tail_text(
                    target,
                    tail_lines=effective_tail_lines,
                    max_bytes=effective_max_bytes,
                )
                start_offset = int(meta.get("start_offset", 0))
                return SessionFileContent(
                    path=target.relative_to(workspace_dir).as_posix(),
                    content=content,
                    truncated=True if start_offset > 0 else False,
                    total_bytes=total_bytes,
                )
            if effective_max_bytes is not None and total_bytes > effective_max_bytes:
                try:
                    with target.open("rb") as handle:
                        handle.seek(max(0, total_bytes - effective_max_bytes))
                        raw = handle.read(effective_max_bytes)
                except Exception as exc:  # pragma: no cover - defensive
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                    ) from exc
                text = raw.decode("utf-8", errors="replace")
                return SessionFileContent(
                    path=target.relative_to(workspace_dir).as_posix(),
                    content=text,
                    truncated=True,
                    total_bytes=total_bytes,
                )
        try:
            content = target.read_text("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return SessionFileContent(
            path=target.relative_to(workspace_dir).as_posix(),
            content=content,
            truncated=False,
            total_bytes=total_bytes,
        )

    async def list_models(self, config_path: str) -> ModelCatalogResponse:
        requested_path = str(config_path).strip()
        if not requested_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="config_path required")
        default_profile = resolve_default_profile()
        default_identity = default_profile.public_identity()
        resolved_path = (
            str(default_profile.source_path)
            if requested_path == default_identity["definition_ref"]
            else requested_path
        )
        try:
            config = load_agent_config(resolved_path)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to load config: {exc}",
            ) from exc
        providers = config.get("providers") or {}
        default_model = providers.get("default_model") or config.get("model")
        models_cfg = providers.get("models") or []
        if not models_cfg and default_model:
            models_cfg = [{"id": default_model}]
        entries, issues = build_model_catalog(
            models_cfg,
            credential_origin=lambda route: provider_router.get_credential_origin(
                str(route),
                session_id="model_catalog",
            ),
        )
        policy = policy_pack_for_config_authority(
            config,
            session_id="model_catalog",
            config_path=resolved_path,
            logger=logger,
        )
        if policy.model_allowlist is not None or policy.model_denylist:
            entries = [entry for entry in entries if policy.is_model_allowed(entry.id)]
            if default_model and not policy.is_model_allowed(str(default_model)):
                default_model = entries[0].id if entries else None
        if default_model and all(entry.id != str(default_model) for entry in entries):
            default_model = entries[0].id if entries else None
        return ModelCatalogResponse(
            models=entries,
            default_model=str(default_model) if default_model else None,
            config_path=requested_path,
            discovery_policy="configured_only",
            issues=issues,
        )

    def atp_feature_status(self, *, enabled: bool | None = None) -> dict[str, Any]:
        return {
            "enabled": bool(self._atp_repl_enabled if enabled is None else enabled),
            "service_initialized": bool(self._atp_service_initialized),
            "runtime_capabilities": dict(self._atp_runtime_capabilities or {}),
        }

    async def _ensure_atp_repl_service(self):
        if not bool(self._atp_repl_enabled):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "atp_repl_disabled",
                    "message": "ATP REPL is disabled",
                },
            )
        if self._atp_repl_service is not None:
            return self._atp_repl_service
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "atp_repl_unavailable",
                "message": "ATP REPL backend not initialized",
            },
        )

    @staticmethod
    def _build_atp_backend_request(payload: ATPReplRequest):
        metadata = dict(payload.metadata or {})
        if payload.tenant_id:
            metadata["tenant_id"] = payload.tenant_id
        return SimpleNamespace(
            commands=list(payload.commands),
            state_ref=payload.state_ref,
            timeout_s=payload.timeout_s,
            memory_mb=payload.memory_mb,
            max_heartbeats=payload.max_heartbeats,
            want_state=bool(payload.want_state),
            metadata=metadata,
        )

    @staticmethod
    async def _maybe_await(value):
        if asyncio.iscoroutine(value):
            return await value
        return value

    @staticmethod
    def _coerce_metrics(metrics: Any) -> list[ATPReplMetrics]:
        rows: list[ATPReplMetrics] = []
        for item in list(metrics or []):
            rows.append(
                ATPReplMetrics(
                    repl_ms=(
                        None
                        if getattr(item, "repl_ms", None) is None
                        else float(getattr(item, "repl_ms"))
                    ),
                    restore_ms=(
                        None
                        if getattr(item, "restore_ms", None) is None
                        else float(getattr(item, "restore_ms"))
                    ),
                )
            )
        return rows

    @staticmethod
    def _coerce_errors(errors: Any) -> list[ATPReplError]:
        rows: list[ATPReplError] = []
        for item in list(errors or []):
            rows.append(
                ATPReplError(
                    severity=getattr(item, "severity", None),
                    message=str(getattr(item, "message", "")),
                    pos_line=getattr(item, "pos_line", None),
                    pos_col=getattr(item, "pos_col", None),
                    signature=getattr(item, "signature", None),
                )
            )
        return rows

    @staticmethod
    def _coerce_sorries(sorries: Any) -> list[ATPReplSorry]:
        rows: list[ATPReplSorry] = []
        for item in list(sorries or []):
            rows.append(
                ATPReplSorry(
                    pos_line=getattr(item, "pos_line", None),
                    pos_col=getattr(item, "pos_col", None),
                    goal=getattr(item, "goal", None),
                )
            )
        return rows

    def _map_atp_result(self, result: Any, metrics: Any) -> ATPReplResponse:
        error_code = getattr(result, "error_code", None)
        error_detail = getattr(result, "error_detail", None)
        return ATPReplResponse(
            request_id=getattr(result, "request_id", None),
            success=bool(getattr(result, "success", False)),
            messages=list(getattr(result, "messages", []) or []),
            errors=self._coerce_errors(getattr(result, "errors", None)),
            sorries=self._coerce_sorries(getattr(result, "sorries", None)),
            metrics=self._coerce_metrics(metrics),
            new_state_ref=getattr(result, "new_state_ref", None),
            error_code=error_code,
            error_detail=error_detail,
            harness_diagnostic=build_atp_harness_diagnostic(error_code, error_detail),
        )

    @staticmethod
    def _append_metrics_rows(
        path: str,
        *,
        result: Any,
        response: ATPReplResponse,
        batch_size: int,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rows = response.metrics or [ATPReplMetrics(repl_ms=None, restore_ms=None)]
        with target.open("a", encoding="utf-8") as handle:
            for metric in rows:
                payload = {
                    "ts": now,
                    "request_id": response.request_id,
                    "success": bool(response.success),
                    "repl_ms": metric.repl_ms,
                    "restore_ms": metric.restore_ms,
                    "batch_size": int(batch_size),
                    "error_code": response.error_code,
                    "header_cache_hit": bool(
                        getattr(result, "header_cache_hit", False)
                    ),
                    "header_cache_miss": bool(
                        getattr(result, "header_cache_miss", False)
                    ),
                }
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

    async def atp_repl(self, payload: ATPReplRequest) -> ATPReplResponse:
        service = await self._ensure_atp_repl_service()
        backend_request = self._build_atp_backend_request(payload)
        result, metrics = await self._maybe_await(
            service.submit_request_with_metrics(backend_request)
        )
        response = self._map_atp_result(result, metrics)
        metrics_path = os.environ.get("ATP_REPL_METRICS_PATH", "").strip()
        if metrics_path:
            self._append_metrics_rows(
                metrics_path, result=result, response=response, batch_size=1
            )
        return response

    async def atp_repl_batch(
        self, payload: ATPReplBatchRequest
    ) -> ATPReplBatchResponse:
        service = await self._ensure_atp_repl_service()
        backend_requests = [
            self._build_atp_backend_request(item) for item in payload.requests
        ]
        results, metrics_rows = await self._maybe_await(
            service.submit_batch_requests(backend_requests)
        )
        result_list = list(results or [])
        metric_list = list(metrics_rows or [])
        if len(result_list) != len(backend_requests) or len(metric_list) != len(
            backend_requests
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "protocol_batch_mismatch",
                    "message": "ATP REPL batch size mismatch",
                    "detail": {
                        "requests": len(backend_requests),
                        "results": len(result_list),
                        "metrics": len(metric_list),
                    },
                },
            )
        response_rows: list[ATPReplResponse] = []
        metrics_path = os.environ.get("ATP_REPL_METRICS_PATH", "").strip()
        for result, row_metrics in zip(result_list, metric_list):
            response = self._map_atp_result(result, row_metrics)
            response_rows.append(response)
            if metrics_path:
                self._append_metrics_rows(
                    metrics_path,
                    result=result,
                    response=response,
                    batch_size=len(backend_requests),
                )
        return ATPReplBatchResponse(results=response_rows)

    async def resolve_artifact_path(self, session_id: str, artifact: str) -> Path:
        if not artifact or not str(artifact).strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="artifact path required"
            )
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        workspace_dir = runner.get_workspace_dir() if runner else None
        candidate_raw = str(artifact).strip()
        candidate_path = Path(candidate_raw)
        allowed_roots: list[Path] = []
        if workspace_dir:
            allowed_roots.append(workspace_dir.resolve())
        if record.logging_dir:
            try:
                allowed_roots.append(Path(record.logging_dir).resolve())
            except Exception:
                pass
        if candidate_path.is_absolute():
            resolved = candidate_path.resolve()
        else:
            resolved = None
            for root in allowed_roots:
                possible = (root / candidate_raw).resolve()
                try:
                    possible.relative_to(root)
                except ValueError:
                    continue
                if possible.exists():
                    resolved = possible
                    break
            if resolved is None and allowed_roots:
                resolved = (allowed_roots[0] / candidate_raw).resolve()
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found"
            )
        if allowed_roots:
            permitted = False
            for root in allowed_roots:
                try:
                    resolved.relative_to(root)
                    permitted = True
                    break
                except ValueError:
                    continue
            if not permitted:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="artifact path outside workspace",
                )
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found"
            )
        return resolved

    @staticmethod
    def _read_snippet(
        target: Path, *, head_lines: int, tail_lines: int, max_bytes: int
    ) -> tuple[str, int]:
        max_bytes = max(1, int(max_bytes))
        head_lines = max(0, int(head_lines))
        tail_lines = max(0, int(tail_lines))
        stat = target.stat()
        size = stat.st_size
        # Tail-only snippet: used by focus modal when it explicitly requests head_lines=0.
        if head_lines == 0:
            if tail_lines == 0:
                return "", 0
            tail_text, meta = _TAIL_LINE_INDEX_CACHE.read_tail_text(
                target, tail_lines=tail_lines, max_bytes=max_bytes
            )
            returned_bytes = int(meta.get("returned_bytes", 0))
            return tail_text, returned_bytes
        if size <= max_bytes:
            raw = target.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            return text.replace("\r\n", "\n").replace("\r", "\n"), len(raw)
        # Classic head+tail snippet behavior (used by @read + large file mentions).
        if tail_lines == 0:
            head_bytes = max_bytes
            tail_bytes = 0
        else:
            head_bytes = max(1, max_bytes // 2)
            tail_bytes = max(1, max_bytes - head_bytes)
        with target.open("rb") as handle:
            head_raw = handle.read(head_bytes) if head_bytes > 0 else b""
            tail_raw = b""
            if tail_bytes > 0:
                if size > tail_bytes:
                    handle.seek(max(0, size - tail_bytes))
                tail_raw = handle.read(tail_bytes)
        head_text = head_raw.decode("utf-8", errors="replace")
        tail_text = tail_raw.decode("utf-8", errors="replace")
        head_list = (
            head_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:head_lines]
        )
        tail_list = (
            tail_text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .split("\n")[-tail_lines:]
            if tail_lines
            else []
        )
        parts: list[str] = []
        if head_list:
            parts.extend(head_list)
        parts.extend(["", "… (truncated) …", ""])
        if tail_list:
            parts.extend(tail_list)
        return "\n".join(parts), len(head_raw) + len(tail_raw)

