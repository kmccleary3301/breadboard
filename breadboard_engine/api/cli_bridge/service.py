"""High level orchestration of session lifecycle for the CLI bridge."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import time
import uuid
import weakref
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Sequence
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import (
    AnchoredStorage,
    ArtifactStore,
    Session as ProductSession,
)
from breadboard.product.runtime.events import JsonlEventSink, ProcessLock
from breadboard.product.runtime.session_store import (
    authorize_session_artifact_manifest,
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
    AttachmentHandle,
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
from .session_runner import MAX_ATTACHMENT_BYTES, SessionRunner
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
    primitive_emission_enabled,
)
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


def _open_workspace_breadboard(
    workspace_dir: Path,
) -> tuple[Path, Path, int | None, list[int]]:
    workspace_root = workspace_dir.resolve()
    logical = workspace_root / ".breadboard"
    if os.name == "nt":
        handles: list[int] = []
        try:
            handles.append(
                AnchoredStorage.windows_handle(
                    workspace_root, directory=True, create=False
                )
            )
            handles.append(AnchoredStorage.windows_handle(logical, directory=True))
            return logical, workspace_root, None, handles
        except OSError as exc:
            for handle in reversed(handles):
                AnchoredStorage.close_windows_handle(handle)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid workspace metadata path",
            ) from exc
    try:
        expected = workspace_root.stat(follow_symlinks=False)
        root_fd = os.open(
            workspace_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid workspace root"
        ) from exc
    actual = os.fstat(root_fd)
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        os.close(root_fd)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="workspace root changed"
        )
    metadata_fd = None
    try:
        try:
            os.mkdir(".breadboard", dir_fd=root_fd)
        except FileExistsError:
            pass
        metadata_fd = os.open(
            ".breadboard",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        os.fsync(root_fd)
        return logical, workspace_root, metadata_fd, []
    except OSError as exc:
        if metadata_fd is not None:
            os.close(metadata_fd)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid workspace metadata path",
        ) from exc
    finally:
        os.close(root_fd)


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


class SessionService:
    """Facade that coordinates the registry, runners, and FastAPI endpoints."""

    def __init__(
        self,
        registry: SessionRegistry | None = None,
        *,
        state_root: str | Path | None = None,
        subscriber_queue_maxsize: int | None = None,
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
        _cleanup_incomplete_starts(state_paths=self._managed_state_paths)

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id.casefold(), asyncio.Lock())

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
            collision = next(
                (
                    existing
                    for existing in self.registry._records
                    if existing.casefold() == selected_session_id.casefold()
                ),
                None,
            )
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
        if request.workspace is not None and event_root is not None:
            candidate_workspace = Path(request.workspace).expanduser().resolve()
            requested_event_root = event_root.expanduser().resolve()
            durable_event_root = (
                session_event_path(candidate_workspace, session_id).parent.parent.resolve()
            )
            if requested_event_root == durable_event_root:
                durable_product_workspace = candidate_workspace
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
            "resources",
            "workspace",
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
        if runner.request.workspace:
            metadata["workspace"] = str(
                Path(runner.request.workspace).expanduser().resolve()
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
        record.runner, record.product_artifacts, published = runner, {}, False
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
            event_sink = JsonlEventSink(staged_event_dir / "session_events.jsonl")
            product_session = ProductSession.start(
                runtime_lock, session_title, session_id=session_id, sink=event_sink
            )
            record.product_session = product_session
            metadata["session_contract"] = product_session.read_model.as_dict()
            async with self.registry._lock:
                await runner.prepare_start(admission_serialized=True)
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
                self.registry._records[session_id] = record
                if durable_product_workspace is not None:
                    runner.bind_durable_product_session(durable_product_workspace)
            published = True
            await self._ensure_dispatcher(record)
            await self._maybe_prewarm_request_runtime(request, metadata, runtime_config)
            runner.authorize_start()
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
            try:
                runner.transition_product_session(
                    "fail", "session_setup_failed", "session setup failed"
                )
            except Exception:
                logger.exception(
                    "Failed to terminalize session %s after setup failure", session_id
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
                self.registry._records.pop(session_id, None)
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
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        key = payload.cancellation_request_key
        key_digest = identity_digest(key)
        body_digest = cancellation_body_digest(turn_id, payload.reason)
        async with record.admission_lock:
            existing = record.cancellations_by_key.get(key)
            if existing is None:
                existing = record.cancellations_by_key_digest.get(key_digest)
            if existing is not None:
                if existing.body_digest != body_digest:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={"code": "cancellation_idempotency_conflict", "turn_id": existing.turn_id},
                    )
                return SessionTurnCancelResponse(
                    cancellation_request_id=existing.cancellation_request_id,
                    cancellation_request_key=key,
                    input_id=existing.input_id,
                    turn_id=existing.turn_id,
                    disposition="deduplicated",
                    original_disposition=existing.original_disposition,
                )
            turn = record.turns_by_id.get(turn_id)
            if turn is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="turn not found")
            if turn.terminal_outcome is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="turn is already terminal")
            if record.active_turn_id == turn_id:
                disposition = "cancellation_requested"
            elif turn.state == "queued":
                disposition = "queued_cancelled"
                try:
                    record.queued_turn_ids.remove(turn_id)
                except ValueError:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="turn is not cancellable") from None
            else:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="turn is not cancellable")
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
            await self.registry.persist(record)
        if disposition == "queued_cancelled":
            await runner.finish_queued_turn_cancellation(turn, payload.reason)
        elif not runner.request_turn_cancellation(turn_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="turn is no longer active")
        return SessionTurnCancelResponse(
            cancellation_request_id=cancellation.cancellation_request_id,
            cancellation_request_key=key,
            input_id=turn.input_id,
            turn_id=turn_id,
            disposition=disposition,
            original_disposition=disposition,
        )

    async def ensure_session(self, session_id: str) -> SessionRecord:
        record = await self.registry.get(session_id)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        if record.loaded_from_retained_state:
            async with self._session_lock(session_id):
                if record.loaded_from_retained_state:
                    await self._resume_retained_session(record)
        return record

    async def _resume_retained_session(self, record: SessionRecord) -> None:
        profile = resolve_default_profile()
        default_identity = profile.public_identity()
        metadata = dict(record.metadata or {})
        recorded_config_path = str(metadata.get("config_path") or "").strip()
        config_path = (
            str(profile.source_path)
            if not recorded_config_path
            or recorded_config_path == default_identity["definition_ref"]
            else recorded_config_path
        )
        recorded_workspace = metadata.get("workspace")
        workspace = (
            str(recorded_workspace).strip()
            if isinstance(recorded_workspace, str) and recorded_workspace.strip()
            else None
        )
        permission_mode = str(
            metadata.get("permission_mode") or "configured"
        ).strip().lower()
        if permission_mode not in {"prompt", "ask", "interactive", "configured"}:
            permission_mode = "configured"
        metadata["permission_mode"] = permission_mode
        record.metadata = metadata
        runner = SessionRunner(
            session=record,
            registry=self.registry,
            request=SessionCreateRequest(
                config_path=config_path,
                task="",
                metadata=metadata,
                workspace=workspace,
                permission_mode=permission_mode,
            ),
        )
        runner.prepare_runtime_config()
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
                    if start_index is None:
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
                    if event.type in {
                        EventType.TURN_COMPLETED,
                        EventType.TURN_FAILED,
                        EventType.TURN_CANCELLED,
                    }:
                        try:
                            await self.registry.persist(record, terminal_event=event)
                        except Exception:
                            record.event_seq = previous_event_seq
                            event.seq = previous_event_seq_value
                            # A terminal event without durable retention is not safe
                            # to expose as resolved evidence.
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
            if start_index is None:
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
        record = await self.ensure_session(session_id)
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
            await self._stop_session_locked(session_id)
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
        attachments = tuple(payload.attachments or ())
        body_digest = submission_body_digest(payload.content, attachments)
        key_digest = identity_digest(client_message_id)

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
                disposition = "started" if record.active_turn_id is None else "queued"
                turn = TurnRecord(
                    input_id=f"input-{uuid.uuid4().hex}",
                    turn_id=f"turn-{uuid.uuid4().hex}",
                    client_message_id=client_message_id,
                    content=payload.content,
                    attachments=attachments,
                    original_disposition=disposition,
                    state="active" if disposition == "started" else "queued",
                    body_digest=body_digest,
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
                try:
                    accepted_content = await runner.enqueue_input(
                        payload.content,
                        attachments=list(attachments),
                        input_id=turn.input_id,
                        turn_id=turn.turn_id,
                        defer_execution=scheduled_operations.append,
                    )
                    if len(scheduled_operations) != 1:
                        raise RuntimeError("input execution was not scheduled exactly once")
                    turn.content = accepted_content
                    await self.registry.persist(record)
                except Exception as exc:
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
                if defer_execution is None:
                    await scheduled_operation()
                else:
                    defer_execution(scheduled_operation)
                return SessionInputResponse(
                    client_message_id=client_message_id,
                    input_id=turn.input_id,
                    turn_id=turn.turn_id,
                    disposition=disposition,
                    original_disposition=disposition,
                )

        return await self.registry.admit_turn(admit)

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
            runner.transition_product_session(
                "reconfigure",
                self._runtime_lock(
                    session_id, runtime_config, runner.request.config_path
                ),
                payload.command,
            )

        try:
            detail = await runner.handle_command(
                payload.command,
                payload.payload,
                durable_reconfigure=durable_reconfigure
                if payload.command
                in {"set_model", "set_mode", "set_skills", "set_role", "set_model_role"}
                else None,
            )
        except ModelRoleResolutionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=exc.problem.to_dict()
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
            return await self._upload_attachments_locked(session_id, files, metadata)

    async def _upload_attachments_locked(
        self,
        session_id: str,
        files: Sequence[UploadFile],
        metadata: Optional[dict[str, Any]] = None,
    ) -> AttachmentUploadResponse:
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="no files provided"
            )
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
        staged_uploads = []
        staged_bytes = 0
        for index, upload in enumerate(files, start=1):
            data = bytearray()
            try:
                while True:
                    chunk = await upload.read(
                        MAX_ATTACHMENT_BYTES - staged_bytes - len(data) + 1
                    )
                    if not chunk:
                        break
                    data.extend(chunk)
                    if staged_bytes + len(data) > MAX_ATTACHMENT_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"attachments exceed {MAX_ATTACHMENT_BYTES}-byte handoff limit",
                        )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"failed to read upload: {exc}",
                ) from exc
            if data:
                staged_uploads.append((index, upload, bytes(data)))
                staged_bytes += len(data)
        if not staged_uploads:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no attachment data found",
            )
        attachment_entries: list[dict[str, Any]] = []
        handles: list[AttachmentHandle] = []
        created_dirs: list[str] = []
        created_refs = set()
        anchor, workspace_root, descriptor, windows_handles = (
            _open_workspace_breadboard(workspace_dir)
        )
        artifact_fd = attachment_fd = None
        artifact_root, attachment_root = anchor / "artifacts", anchor / "attachments"
        artifact_refs = dict(getattr(record, "product_artifacts", {}))
        manifest_path: Path | None = None
        manifest_fd = None
        manifest_name = None
        transaction = None
        registered_before = dict(getattr(runner, "_attachment_store", {}))
        try:
            if descriptor is not None:
                artifact_fd = AnchoredStorage.open_directory(descriptor, "artifacts")
                try:
                    attachment_fd = AnchoredStorage.open_directory(
                        descriptor, "attachments"
                    )
                except BaseException:
                    os.close(artifact_fd)
                    artifact_fd = None
                    raise
                os.fsync(descriptor)
            artifact_store = ArtifactStore(artifact_root, descriptor=artifact_fd)
            candidate_transaction = artifact_store.transaction()
            candidate_transaction.__enter__()
            transaction = candidate_transaction
            if attachment_fd is None:
                attachment_root.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                windows_handles.append(
                    AnchoredStorage.windows_handle(artifact_root, directory=True)
                )
                windows_handles.append(
                    AnchoredStorage.windows_handle(attachment_root, directory=True)
                )
            try:
                for index, upload, data in staged_uploads:
                    attachment_id = f"att-{uuid.uuid4().hex[:10]}"
                    filename = self._sanitize_filename(
                        upload.filename or f"attachment-{index}.bin"
                    )
                    created_dirs.append(attachment_id)
                    if attachment_fd is not None:
                        target_fd = AnchoredStorage.open_directory(
                            attachment_fd, attachment_id
                        )
                    else:
                        target_fd = None
                        (attachment_root / attachment_id).mkdir(
                            parents=True, exist_ok=True
                        )
                    try:
                        artifact_ref = artifact_store.put(
                            data,
                            media_type=upload.content_type
                            or "application/octet-stream",
                            created=created_refs,
                        )
                        if target_fd is not None:
                            artifact_store.materialize_at(
                                artifact_ref, target_fd, filename
                            )
                        else:
                            artifact_store.materialize(
                                artifact_ref, attachment_root / attachment_id / filename
                            )
                        artifact_refs[attachment_id] = artifact_ref
                    finally:
                        if target_fd is not None:
                            os.close(target_fd)
                    logical_target = (
                        workspace_root
                        / ".breadboard"
                        / "attachments"
                        / attachment_id
                        / filename
                    )
                    handles.append(
                        AttachmentHandle(
                            id=attachment_id,
                            filename=filename,
                            mime=upload.content_type,
                            size_bytes=len(data),
                        )
                    )
                    attachment_entries.append(
                        {
                            "id": attachment_id,
                            "filename": filename,
                            "absolute_path": str(logical_target),
                            "relative_path": str(
                                logical_target.relative_to(workspace_root)
                            ),
                            "metadata": metadata or {},
                        }
                    )
                manifest = artifact_store.manifest(session_id, artifact_refs)
                manifest_ref = artifact_store.put_json(manifest, created=created_refs)
                manifest_name = (
                    f"{session_id}.{manifest_ref.digest.removeprefix('sha256:')}.json"
                )
                if artifact_fd is not None:
                    manifest_fd = AnchoredStorage.open_directory(
                        artifact_fd, "manifests"
                    )
                    artifact_store.materialize_at(
                        manifest_ref, manifest_fd, manifest_name
                    )
                    os.fsync(artifact_fd)
                else:
                    manifest_path = artifact_root / "manifests" / manifest_name
                    artifact_store.materialize(manifest_ref, manifest_path)
                if (
                    descriptor is not None
                    and (workspace_root / ".breadboard").resolve()
                    != AnchoredStorage.descriptor_path(descriptor).resolve()
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="workspace metadata path changed",
                    )
                runner.register_attachments(attachment_entries)
            except BaseException:
                if attachment_fd is not None:
                    for name in created_dirs:
                        try:
                            target_fd = AnchoredStorage.open_directory(
                                attachment_fd, name, create=False
                            )
                        except FileNotFoundError:
                            continue
                        try:
                            for child in os.listdir(target_fd):
                                os.unlink(child, dir_fd=target_fd)
                        finally:
                            os.close(target_fd)
                        os.rmdir(name, dir_fd=attachment_fd)
                    if manifest_fd is not None and manifest_name is not None:
                        try:
                            os.unlink(manifest_name, dir_fd=manifest_fd)
                        except FileNotFoundError:
                            pass
                        os.fsync(manifest_fd)
                        os.fsync(artifact_fd)
                    os.fsync(attachment_fd)
                else:
                    for name in created_dirs:
                        target = attachment_root / name
                        target_lock = (
                            AnchoredStorage.windows_handle(
                                target, directory=True, create=False
                            )
                            if os.name == "nt"
                            else None
                        )
                        try:
                            if target_lock is None:
                                shutil.rmtree(target, ignore_errors=True)
                            else:
                                for child in target.iterdir():
                                    child.unlink()
                        finally:
                            AnchoredStorage.close_windows_handle(target_lock)
                        if target_lock is not None:
                            target.rmdir()
                    if manifest_path is not None:
                        manifest_lock = (
                            AnchoredStorage.windows_handle(
                                manifest_path.parent, directory=True, create=False
                            )
                            if os.name == "nt"
                            else None
                        )
                        try:
                            manifest_path.unlink(missing_ok=True)
                        finally:
                            AnchoredStorage.close_windows_handle(manifest_lock)
                    for parent in {
                        attachment_root,
                        manifest_path.parent
                        if manifest_path is not None
                        else artifact_root,
                    }:
                        AnchoredStorage.sync_directory(
                            parent
                        ) if parent.is_dir() else None
                for artifact_ref in created_refs:
                    artifact_store.discard(artifact_ref)
                if hasattr(runner, "_attachment_store"):
                    runner._attachment_store = registered_before
                raise
        finally:
            if transaction is not None:
                transaction.__exit__(None, None, None)
            for open_descriptor in (
                manifest_fd,
                artifact_fd,
                attachment_fd,
                descriptor,
            ):
                if open_descriptor is not None:
                    os.close(open_descriptor)
            for handle in reversed(windows_handles):
                AnchoredStorage.close_windows_handle(handle)
        if manifest_name is None:
            raise RuntimeError("attachment manifest was not published")
        try:
            authorize_session_artifact_manifest(
                workspace_root,
                session_id,
                manifest_name,
            )
        except FileNotFoundError:
            # Live bridge sessions have no durable product projection yet.
            pass
        record.product_artifacts = artifact_refs
        (
            record.metadata["artifact_manifest"],
            record.metadata["artifact_manifest_ref"],
        ) = manifest, manifest_ref.as_dict()
        return AttachmentUploadResponse(attachments=handles)

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

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        candidate = filename.strip() or "attachment.bin"
        candidate = candidate.replace("\\", "/")
        return os.path.basename(candidate)
