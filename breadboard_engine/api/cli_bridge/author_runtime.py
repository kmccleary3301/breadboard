"""Session-owned execution of a captured module graph.

The existing registry retains admission/resource identities; the world owns
processes, Session owns outputs, and domain/child owners retain their effects.
This module owns only the live framed conversations between those owners.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from breadboard.modules.author import (
    CheckpointEnvelope, CheckpointProposal, CheckpointRefusal, CheckpointRequest,
    ChildTarget, InputEnvelope, ModuleInput, OutputEnvelope,
)
from breadboard.modules.authority import AdmissionGrant, AuthorityDeclaration
from breadboard.modules.transport import (
    MAX_CHECKPOINT_BYTES, MAX_FRAME_BYTES, PROTOCOL_VERSION, RequestKey,
    WireHeader, WireKind, WireMessage, WireProtocolError, decode_bytes, encode_bytes,
)
from breadboard.product.coordination.work_items import WorkItem, WorkItemRepository
from breadboard.product.harness.packages import ModuleContract, ModulePackage
from breadboard_engine.execution.author_worker import (
    AuthorWorker, AuthorWorkerCleanupResult, AuthorWorkerLaunchError,
    AuthorWorkerResourceReceipt, AuthorWorkerSpec, open_author_worker,
)

from .author_domains import AuthorDomainDispatcher, AuthorDomainError, EffectiveDomainScope
from .registry import ModuleExecutionRecord, ModuleWorkerOwnership, SessionRecord, SessionRegistry
from .runtime_emission import CapturedRuntimeConfig
from breadboard.product.runtime.artifacts import ArtifactStore


class ModuleExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: bool = False,
        cleanup_confirmed: bool = False,
    ) -> None:
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.cleanup_confirmed = cleanup_confirmed
        super().__init__(f"{code}: {detail}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise WireProtocolError(f"{label} must be non-empty text")
    return value


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise WireProtocolError(f"{label} must be an integer >= {minimum}")
    return value


def _object(value: object, fields: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise WireProtocolError(f"{label} has unsupported or missing fields")
    return value


def _same_scope(left: RequestKey, right: RequestKey) -> bool:
    return (
        left.worker_session_id == right.worker_session_id
        and left.generation_id == right.generation_id
        and left.instance_id == right.instance_id
        and left.work_id == right.work_id
        and left.attempt_id == right.attempt_id
        and left.authority_epoch == right.authority_epoch
    )


def _checkpoint_to_wire(envelope: CheckpointEnvelope) -> dict[str, object]:
    if not isinstance(envelope, CheckpointEnvelope):
        raise TypeError("resume checkpoint must be a CheckpointEnvelope")
    return {
        "source_generation_id": envelope.source_generation_id,
        "source_module_id": envelope.source_module_id,
        "source_instance_id": envelope.source_instance_id,
        "source_work_id": envelope.source_work_id,
        "source_attempt_id": envelope.source_attempt_id,
        "schema_id": envelope.schema_id,
        "body": encode_bytes(envelope.body, maximum=MAX_CHECKPOINT_BYTES),
    }


@dataclass(frozen=True, slots=True)
class _Limits:
    max_children: int
    max_message_bytes: int
    max_checkpoint_bytes: int
    deadline_ms: int | None

    @classmethod
    def from_package(cls, package: ModulePackage) -> _Limits:
        raw = package.manifest.resource_budget
        if set(raw) - {"max_children", "max_message_bytes", "max_checkpoint_bytes", "deadline_ms"}:
            raise ModuleExecutionError("capacity_approval_required", "unsupported module resource profile")
        children = _integer(raw.get("max_children", 0), "max_children")
        message = _integer(raw.get("max_message_bytes", MAX_FRAME_BYTES), "max_message_bytes", 1)
        checkpoint = _integer(raw.get("max_checkpoint_bytes", MAX_CHECKPOINT_BYTES), "max_checkpoint_bytes", 1)
        deadline = raw.get("deadline_ms")
        if deadline is not None:
            deadline = _integer(deadline, "deadline_ms", 1)
        if message > MAX_FRAME_BYTES or checkpoint > MAX_CHECKPOINT_BYTES:
            raise ModuleExecutionError("capacity_approval_required", "module exceeds the admitted protocol profile")
        return cls(children, message, checkpoint, deadline)


@dataclass(frozen=True, slots=True)
class ModuleDisposal:
    status: Literal["confirmed_absent", "unknown"]
    resource_refs: tuple[str, ...]
    pending_domain_refs: tuple[str, ...]


class ModuleRuntime:
    """One admitted Session/Work Item and its separately isolated bindings."""

    def __init__(
        self,
        *,
        record: SessionRecord,
        registry: SessionRegistry,
        captured: CapturedRuntimeConfig,
        workspace: Path,
        storage_root: Path,
        repository: WorkItemRepository,
        loop: asyncio.AbstractEventLoop,
        session_lock: Any,
        persist_session: Callable[[], None],
        emit_output: Callable[[OutputEnvelope, RequestKey, str, int, bool], None],
        parent_fence: Callable[[], None] | None = None,
        depth: int = 0,
        owns_work_lifecycle: bool = True,
        resume_checkpoints: Mapping[str, CheckpointEnvelope] | None = None,
    ) -> None:
        execution, grant = record.module_execution, record.module_grant
        if execution is None or grant is None or record.product_session is None:
            raise ModuleExecutionError("admission_missing", "module execution requires a retained Session, graph and grant")
        if captured.materialization.lock.generation_id != execution.generation_id:
            raise ModuleExecutionError("closure_mismatch", "captured graph differs from admitted generation")
        self.record, self.registry, self.captured = record, registry, captured
        self.workspace, self.storage_root = workspace.resolve(), storage_root.resolve()
        self.repository, self.loop = repository, loop
        self.session_lock, self.persist_session = session_lock, persist_session
        self.emit_output, self.parent_fence, self.depth = emit_output, parent_fence, depth
        self.owns_work_lifecycle = owns_work_lifecycle
        self.generation_id, self.grant = execution.generation_id, grant
        self.work_id, self.attempt_id = execution.work_item_id, execution.attempt_id
        self.root_binding = execution.root_binding
        self.bindings = captured.materialization.lock["modules"]["bindings"]
        self.packages = captured.materialization.packages
        self.resume_checkpoints = dict(resume_checkpoints or {})
        if not set(self.resume_checkpoints) <= set(self.bindings):
            raise ModuleExecutionError(
                "checkpoint_incompatible",
                "resume checkpoints name a binding outside the target graph",
            )
        self.stopped = threading.Event()
        self._stop_reason = "owner_scope_closed"
        self._mutation_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._workers: dict[str, _ModuleWorker] = {}
        self._current_input_id = f"{self.work_id}.prepare"
        self._current_turn_id = f"{self.work_id}.prepare"
        self._disposal: ModuleDisposal | None = None
        self.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.storage_root.lstat()
        if self.storage_root.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ModuleExecutionError("ownership_mismatch", "module staging root must be owner-private")
        self.artifacts = ArtifactStore(self.storage_root / "child-artifacts")

    def registry_call(self, method: str, *args: Any) -> Any:
        operation = getattr(self.registry, method)(*args)
        return asyncio.run_coroutine_threadsafe(operation, self.loop).result(timeout=30)

    def require_live(self) -> None:
        if self.stopped.is_set():
            raise ModuleExecutionError(self._stop_reason, "module owner is no longer accepting operations")
        if self.parent_fence is not None:
            self.parent_fence()
        execution, grant = self.record.module_execution, self.record.module_grant
        if (
            execution is None or execution.generation_id != self.generation_id
            or execution.work_item_id != self.work_id or execution.attempt_id != self.attempt_id
            or grant is None or grant.grant_id != self.grant.grant_id
            or grant.authority_epoch != self.grant.authority_epoch
        ):
            raise ModuleExecutionError("stale_owner", "module admission identity or authority changed")
        if grant.expires_at_ms is not None and time.time_ns() // 1_000_000 >= grant.expires_at_ms:
            raise ModuleExecutionError("grant_expired", "module grant expired")
        if self.record.product_session.read_model.status != "running":
            raise ModuleExecutionError("owner_closed", "Session is no longer running")

    def _persist(self) -> None:
        self.persist_session()

    def _replace_worker(self, worker: _ModuleWorker, **changes: Any) -> None:
        with self._mutation_lock:
            execution = self.record.module_execution
            if execution is None or execution.generation_id != self.generation_id:
                raise ModuleExecutionError("stale_owner", "module execution record changed")
            updated = replace(worker.ownership, **changes)
            workers = tuple(updated if row.binding == updated.binding else row for row in execution.workers)
            if not any(row.binding == updated.binding for row in execution.workers):
                workers = (*workers, updated)
            self.record.module_execution = replace(execution, workers=workers)
            worker.ownership = updated
            self._persist()

    def _ensure_work(self) -> None:
        if not self.repository.read(self.work_id):
            work = WorkItem.create(
                f"module {self.root_binding}", work_item_id=self.work_id,
                repository=self.repository,
            )
            lease = work.acquire_lease(f"module:{self.record.session_id}")
            work.start_attempt(
                self.record.session_id, lease_id=lease.active_lease.lease_id,
                attempt_id=self.attempt_id,
            )
        work = WorkItem.restore(self.repository, self.work_id)
        if (
            work.read_model.status != "running" or work.read_model.current_attempt is None
            or work.read_model.current_attempt.attempt_id != self.attempt_id
        ):
            raise ModuleExecutionError("stale_attempt", "Work Item is not executing the admitted attempt")

    def canonical_root_session_id(self) -> str:
        record = self.registry_call("get", self.record.session_id)
        metadata = getattr(record, "metadata", None) if record is not None else None
        retained = metadata.get("durable_child") if isinstance(metadata, dict) else None
        root = retained.get("root_session_id") if isinstance(retained, Mapping) else None
        return root if isinstance(root, str) and root.strip() else self.record.session_id

    def worker(self, binding: str) -> _ModuleWorker:
        self.require_live()
        with self._mutation_lock:
            worker = self._workers.get(binding)
            if worker is None:
                worker = _ModuleWorker(self, binding)
                self._workers[binding] = worker
        worker.prepare()
        return worker

    def prepare(self) -> None:
        self.require_live()
        self._ensure_work()
        self.worker(self.root_binding)

    def execute(
        self, envelope: InputEnvelope, *, input_id: str, turn_id: str
    ) -> OutputEnvelope | None:
        self.require_live()
        self._ensure_work()
        self._current_input_id = _text(input_id, "input ID")
        self._current_turn_id = _text(turn_id, "turn ID")
        worker = self.worker(self.root_binding)
        timer = None
        if worker.limits.deadline_ms is not None:
            timer = threading.Timer(
                worker.limits.deadline_ms / 1000,
                self.close, kwargs={"reason": "deadline_exceeded"},
            )
            timer.daemon = True
            timer.start()
        try:
            output = worker.step(envelope, turn_id)
            if envelope.final:
                if output is None:
                    raise ModuleExecutionError(
                        "input_exhausted",
                        "policy requested continuation after final input",
                    )
                disposal = self.close(reason="final_output")
                if disposal.status != "confirmed_absent":
                    raise ModuleExecutionError(
                        "cleanup_unknown",
                        "module output exists but owned operations have not settled",
                    )
                work = WorkItem.restore(self.repository, self.work_id)
                if self.owns_work_lifecycle and work.read_model.status == "running":
                    work.complete("module final output", attempt_id=self.attempt_id)
            return output
        finally:
            if timer is not None:
                timer.cancel()

    def capture_checkpoints(
        self,
        *,
        request_id: str,
        reason: str,
    ) -> dict[str, CheckpointProposal | CheckpointRefusal]:
        self.require_live()
        self.worker(self.root_binding)
        with self._mutation_lock:
            workers = tuple(sorted(self._workers.values(), key=lambda row: row.binding))
        unresolved: list[str] = []
        for worker in workers:
            domains = worker.domains.quiescence()
            unresolved.extend(
                f"{worker.binding}:{reference}"
                for reference in (
                    *domains.provider_exchanges,
                    *domains.tool_executions,
                    *domains.approvals,
                )
            )
            if worker.children is not None:
                unresolved.extend(
                    f"{worker.binding}:{reference}"
                    for reference in worker.children.quiescence()
                )
        if unresolved:
            raise ModuleExecutionError(
                "boundary_unavailable",
                "checkpoint requires settled owned operations: "
                + ", ".join(sorted(unresolved)),
            )
        return {
            worker.binding: worker.checkpoint(
                f"{_text(request_id, 'checkpoint request ID')}:{worker.binding}",
                _text(reason, "checkpoint reason"),
            )
            for worker in workers
        }

    def prepare_checkpoint_adoption(
        self,
        source_checkpoints: Mapping[str, CheckpointEnvelope],
        source_dependencies: Mapping[str, tuple[str, ...]],
    ) -> tuple[dict[str, CheckpointEnvelope], tuple[dict[str, str], ...]]:
        self.require_live()
        if self.root_binding not in source_checkpoints:
            raise ModuleExecutionError(
                "checkpoint_incompatible",
                "source checkpoint does not contain the root module binding",
            )
        resumed: dict[str, CheckpointEnvelope] = {}
        decisions: list[dict[str, str]] = []
        for binding, source in sorted(source_checkpoints.items()):
            if binding not in self.bindings:
                raise ModuleExecutionError(
                    "checkpoint_incompatible",
                    f"target graph does not contain source binding {binding!r}",
                )
            package = self.packages[binding]
            disposition, target_schema_id, reason, proposal = self.worker(
                binding
            ).prepare_checkpoint(
                source,
                tuple(source_dependencies.get(binding, ())),
                tuple(sorted(package.manifest.dependency_contracts.values())),
            )
            if disposition == "incompatible" or proposal is None:
                raise ModuleExecutionError(
                    "checkpoint_incompatible",
                    reason or f"target binding {binding!r} refused the checkpoint",
                )
            resumed[binding] = proposal.payload
            decisions.append(
                {
                    "binding": binding,
                    "disposition": disposition,
                    "source_schema_id": source.schema_id,
                    "target_schema_id": target_schema_id,
                    "reason": reason,
                }
            )
        return resumed, tuple(decisions)

    def contract(self, binding: str, contract_id: str) -> ModuleContract:
        return next(
            contract for contract in self.packages[binding].manifest.contracts
            if contract.contract_id == contract_id
        )

    def child_targets(self, binding: str) -> tuple[ChildTarget, ...]:
        manifest = self.packages[binding].manifest
        result = []
        for target in manifest.child_targets:
            selected = self.bindings[binding]["children"][target.label]
            contract = self.contract(selected, target.contract_id)
            result.append(ChildTarget(
                target.label, target.target, target.contract_id,
                contract.input_schema_ids, contract.output_schema_ids,
            ))
        return tuple(result)

    def dispatch(self, worker: _ModuleWorker, message: WireMessage) -> Mapping[str, object]:
        self.require_live()
        body = message.body
        if body.get("request_id") != message.header.key.request_id:
            raise WireProtocolError("service body does not match request header")
        kind = message.header.kind
        if kind == "dependency_request":
            _object(body, {"request_id", "dependency", "contract_id", "input"}, "dependency request")
            name = _text(body["dependency"], "dependency name")
            declared = worker.package.manifest.dependency_contracts
            if name not in declared or body["contract_id"] != declared[name]:
                raise ModuleExecutionError("dependency_denied", "dependency does not match the pinned contract")
            binding = self.bindings[worker.binding]["dependencies"][name]
            contract = self.contract(binding, declared[name])
            value = ModuleInput.from_dict(body["input"])
            if value.schema_id not in contract.input_schema_ids:
                raise ModuleExecutionError("schema_mismatch", "dependency input is outside its declared contract")
            dependency = self.worker(binding)
            envelope = InputEnvelope(value.schema_id, dependency.ownership.next_input_sequence, value.body, value.final)
            output = dependency.step(envelope, f"{worker.key.worker_session_id}:{message.header.key.request_id}")
            if output is None or output.schema_id not in contract.output_schema_ids:
                raise ModuleExecutionError("dependency_output_unavailable", "dependency did not produce a declared output")
            return {"schema_id": output.schema_id, "body": encode_bytes(output.body)}
        if kind == "child_request":
            if worker.children is None:
                from .author_children import AuthorChildren
                worker.children = AuthorChildren(self, worker)
            return worker.children.dispatch(body)
        return worker.domains.dispatch(kind, body)

    def close(self, reason: str = "owner_scope_closed") -> ModuleDisposal:
        self._stop_reason = reason
        self.stopped.set()
        with self._close_lock:
            if self._disposal is not None and self._disposal.status == "confirmed_absent":
                return self._disposal
            deadline = time.monotonic() + 15
            with self._mutation_lock:
                workers = tuple(self._workers.values())
            if not workers:
                self._disposal = ModuleDisposal("confirmed_absent", (), ())
                return self._disposal
            executor = ThreadPoolExecutor(max_workers=len(workers), thread_name_prefix="module-disposal")
            futures = [executor.submit(worker.close, reason, deadline) for worker in workers]
            done, pending = wait(futures, timeout=max(0, deadline - time.monotonic()))
            executor.shutdown(wait=False)
            settled = not pending
            pending_domains: list[str] = []
            for future in done:
                try:
                    absent, refs = future.result()
                except Exception:
                    settled = False
                else:
                    settled = settled and absent
                    pending_domains.extend(refs)
            resource_refs = tuple(
                worker.ownership.resource_id for worker in workers
                if worker.ownership.resource_id is not None
            )
            self._disposal = ModuleDisposal(
                "confirmed_absent" if settled else "unknown", resource_refs, tuple(pending_domains),
            )
            return self._disposal


class _ModuleWorker:
    def __init__(self, owner: ModuleRuntime, binding: str) -> None:
        self.owner, self.binding = owner, binding
        self.package = owner.packages[binding]
        self.limits = _Limits.from_package(self.package)
        if self.package.manifest.execution_tier != "enforced_isolated" or self.package.manifest.runtime.kind != "oci":
            raise ModuleExecutionError("native_approval_required", "this admission has no exact native closure approval")
        execution = owner.record.module_execution
        retained = next((item for item in execution.workers if item.binding == binding), None)
        if retained is not None:
            raise ModuleExecutionError("recovery_required", "retained module ownership must be reconciled before another receiver starts")
        worker_id = str(uuid4())
        self.ownership = ModuleWorkerOwnership(
            binding=binding, instance_id=str(uuid4()), worker_session_id=worker_id,
            owner_ref=f"module:{owner.record.session_id}:{worker_id}",
            execution_id=str(uuid4()), execution_token=secrets.token_hex(32),
            staging_root=str(owner.storage_root / worker_id / "captured"),
            staging_owner_ref=f"module-staging:{worker_id}",
        )
        self.key = RequestKey(
            worker_id, f"prepare:{worker_id}", owner.generation_id,
            self.ownership.instance_id, owner.work_id, owner.attempt_id,
            owner.grant.authority_epoch,
        )
        self.scope = EffectiveDomainScope.from_grants(
            self.package.manifest.requested_authority, owner.grant.declaration,
        )
        self.domains = AuthorDomainDispatcher.for_worker(
            config=owner.captured.config, workspace=owner.workspace,
            session=owner.record.product_session, session_record=owner.record,
            scope=self.scope, worker_key=self.key,
            current_turn_id=lambda: owner._current_turn_id,
            current_input_id=lambda: owner._current_input_id,
            scope_fence=owner.require_live, session_lock=owner.session_lock,
            persist_session=owner.persist_session,
        )
        self.children = None
        self.channel: AuthorWorker | None = None
        self._prepare_lock = threading.Lock()
        self._prepared = threading.Event()
        self._prepare_error: BaseException | None = None
        self._step_lock = threading.Lock()
        self._input_closed = False
        self._sent = self._received = self._next_service = 0
        self.latest_checkpoint: CheckpointProposal | None = None
        owner._replace_worker(self)

    def _intent(self, value: Mapping[str, str]) -> None:
        self.owner.require_live()
        self.owner._replace_worker(self, resource_id=value["resourceId"], container_name=value["containerName"])

    def _receipt(self, value: AuthorWorkerResourceReceipt) -> None:
        self.owner.require_live()
        self.owner._replace_worker(self, receipt=value)

    def _start_body(self) -> dict[str, object]:
        manifest = self.package.manifest
        schemas = list(manifest.accepted_checkpoint_schema_ids)
        if manifest.checkpoint_schema_id is not None and manifest.checkpoint_schema_id not in schemas:
            schemas.append(manifest.checkpoint_schema_id)
        return {
            "package_path": "/breadboard-captured/package.zip",
            "package_digest": self.package.package_digest,
            "module_id": manifest.logical_package,
            "instance_id": self.key.instance_id,
            "generation_id": self.key.generation_id,
            "instance_label": self.binding,
            "initial_input": None,
            "resume": (
                _checkpoint_to_wire(self.owner.resume_checkpoints[self.binding])
                if self.binding in self.owner.resume_checkpoints
                else None
            ),
            "input_schemas": list(manifest.input_schema_ids),
            "output_schemas": list(manifest.output_schema_ids),
            "checkpoint_schemas": schemas,
            "dependencies": [
                {"name": name, "contract_id": contract}
                for name, contract in manifest.dependency_contracts.items()
            ],
            "child_targets": [
                {"label": target.label, "target": target.target, "contract_id": target.contract_id,
                 "input_schema_ids": list(target.input_schema_ids), "output_schema_ids": list(target.output_schema_ids)}
                for target in self.owner.child_targets(self.binding)
            ],
        }

    def prepare(self) -> None:
        with self._prepare_lock:
            if self._prepared.is_set():
                if self._prepare_error is not None:
                    raise self._prepare_error
                return
            try:
                self.owner.require_live()
                start = WireMessage(WireHeader(PROTOCOL_VERSION, "start", self.key, 0), self._start_body())
                start.encode(max_bytes=self.limits.max_message_bytes)
                root = Path(self.ownership.staging_root)
                root.parent.mkdir(mode=0o700)
                root.mkdir(mode=0o755)
                payload = self.owner.captured.materialization.package_bytes[self.binding]
                if "sha256:" + hashlib.sha256(payload).hexdigest() != self.package.package_digest:
                    raise ModuleExecutionError("closure_mismatch", "captured package bytes changed before staging")
                with (root / "package.zip").open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                (root / "package.zip").chmod(0o444)
                root.chmod(0o555)
                runtime = self.package.manifest.runtime
                spec = AuthorWorkerSpec(
                    owner_ref=self.ownership.owner_ref, execution_id=self.ownership.execution_id,
                    execution_token=self.ownership.execution_token, image_ref=runtime.ref,
                    platform=runtime.platform, command=runtime.entrypoint,
                    captured_staging_root=str(root), staging_owner_ref=self.ownership.staging_owner_ref,
                    expected_package_sha256=self.package.package_digest,
                )
                self.channel = open_author_worker(
                    spec, on_intent=self._intent, on_receipt=self._receipt,
                    cancel_requested=self.owner.stopped.is_set,
                )
                self._send("start", self.key, start.body)
                response = self._receive(30)
                if response.header.kind == "failure":
                    self._raise_failure(response)
                if response.header.key != self.key or response.header.kind != "ready":
                    raise WireProtocolError("worker performed an operation before preparation completed")
                body = _object(response.body, {"status", "instance_open", "module_id", "instance_id"}, "worker ready")
                if body != {
                    "status": "ready", "instance_open": False,
                    "module_id": self.package.manifest.logical_package, "instance_id": self.key.instance_id,
                }:
                    raise WireProtocolError("worker readiness identity or initial state differs from admission")
            except BaseException as error:
                self._prepare_error = error
                if isinstance(error, AuthorWorkerLaunchError) and error.cleanup is not None:
                    self.owner._replace_worker(self, cleanup=error.cleanup)
                raise
            finally:
                self._prepared.set()

    def _send(self, kind: WireKind, key: RequestKey, body: Mapping[str, object]) -> None:
        if self.channel is None:
            raise ModuleExecutionError("worker_unavailable", "worker channel is not prepared")
        frame = WireMessage(WireHeader(PROTOCOL_VERSION, kind, key, self._sent), body)
        self.channel.send_frame(frame.encode(max_bytes=self.limits.max_message_bytes))
        self._sent += 1

    def _receive(self, timeout: float | None = None) -> WireMessage:
        if self.channel is None:
            raise ModuleExecutionError("worker_unavailable", "worker channel is not prepared")
        payload = self.channel.receive_frame(timeout)
        if payload is None:
            raise ModuleExecutionError("worker_lost", "worker channel closed before its result")
        if len(payload) > self.limits.max_message_bytes:
            raise WireProtocolError("worker exceeded its admitted frame budget")
        message = WireMessage.decode(payload)
        if not _same_scope(message.header.key, self.key) or message.header.sequence != self._received:
            raise WireProtocolError("worker frame does not match the admitted scope and sequence")
        self._received += 1
        self.owner.require_live()
        return message

    @staticmethod
    def _raise_failure(message: WireMessage) -> None:
        body = _object(message.body, {"code", "detail", "retryable", "output_emitted"}, "worker failure")
        if type(body["retryable"]) is not bool or type(body["output_emitted"]) is not bool:
            raise WireProtocolError("worker failure flags must be boolean")
        raise ModuleExecutionError(_text(body["code"], "failure code"), _text(body["detail"], "failure detail"), retryable=body["retryable"])

    def _service(self, message: WireMessage) -> None:
        request_id = f"svc-{self._next_service}"
        if message.header.key.request_id != request_id:
            raise WireProtocolError("worker service request is not the next unique request")
        self._next_service += 1
        try:
            result = self.owner.dispatch(self, message)
            body = {**result, "request_id": request_id, "status": "ok"}
        except (AuthorDomainError, ModuleExecutionError) as error:
            body = {"request_id": request_id, "status": "failed", "code": error.code, "detail": error.detail}
        self._send("service_result", message.header.key, body)

    def step(self, envelope: InputEnvelope, request_id: str) -> OutputEnvelope | None:
        if not self._step_lock.acquire(blocking=False):
            raise ModuleExecutionError("instance_busy", "dependency instance has an active operation")
        try:
            self.owner.require_live()
            if self._input_closed or envelope.sequence != self.ownership.next_input_sequence:
                raise ModuleExecutionError("input_sequence_conflict", "module input is final or not the next admitted sequence")
            if envelope.schema_id not in self.package.manifest.input_schema_ids:
                raise ModuleExecutionError("schema_mismatch", "module input schema is not declared")
            key = replace(self.key, request_id=request_id)
            body = {"schema_id": envelope.schema_id, "sequence": envelope.sequence, "body": encode_bytes(envelope.body), "final": envelope.final}
            WireMessage(WireHeader(PROTOCOL_VERSION, "input", key, self._sent), body).encode(max_bytes=self.limits.max_message_bytes)
            self.owner._replace_worker(self, next_input_sequence=envelope.sequence + 1)
            self._input_closed = envelope.final
            self._send("input", key, body)
            output = None
            chunks = _CheckpointChunks(self, envelope.sequence)
            while True:
                message = self._receive()
                kind = message.header.kind
                if kind in {"provider_request", "tool_request", "context_request", "child_request", "dependency_request"}:
                    self._service(message)
                    continue
                if message.header.key != key:
                    raise WireProtocolError("worker result belongs to another input")
                if kind == "failure":
                    self._raise_failure(message)
                elif kind == "output":
                    if output is not None:
                        raise WireProtocolError("worker emitted two outputs for one step")
                    body = _object(message.body, {"schema_id", "body"}, "module output")
                    output = OutputEnvelope(_text(body["schema_id"], "output schema"), decode_bytes(body["body"]))
                    if output.schema_id not in self.package.manifest.output_schema_ids:
                        raise WireProtocolError("worker output schema is outside its declared closure")
                    self.owner.emit_output(output, key, self.package.manifest.logical_package, 0, envelope.final)
                elif kind == "checkpoint":
                    chunks.append(message.body)
                elif kind == "result":
                    body = _object(message.body, {"status", "output_emitted"}, "module result")
                    expected = "output" if output is not None else "continue"
                    if body["status"] != expected or body["output_emitted"] is not (output is not None):
                        raise WireProtocolError("worker result disagrees with observed output")
                    self.latest_checkpoint = chunks.finish()
                    return output
                else:
                    raise WireProtocolError(f"unexpected worker message during step: {kind}")
        finally:
            self._step_lock.release()

    def checkpoint(
        self,
        request_id: str,
        reason: str,
    ) -> CheckpointProposal | CheckpointRefusal:
        if not self._step_lock.acquire(blocking=False):
            return CheckpointRefusal(
                "boundary_unavailable",
                "module instance has an active step",
                True,
            )
        try:
            self.owner.require_live()
            self.prepare()
            observed_sequence = self.ownership.next_input_sequence
            key = replace(self.key, request_id=request_id)
            body = {
                "request_id": request_id,
                "reason": reason,
                "requested_at_sequence": observed_sequence,
            }
            self._send("checkpoint_request", key, body)
            chunks = _CheckpointChunks(self, observed_sequence)
            deadline = time.monotonic() + 30.0
            while True:
                message = self._receive(max(0.0, deadline - time.monotonic()))
                if message.header.key != key:
                    raise WireProtocolError(
                        "checkpoint response does not match its request"
                    )
                if message.header.kind == "failure":
                    self._raise_failure(message)
                if message.header.kind == "checkpoint":
                    chunks.append(message.body)
                    continue
                if message.header.kind != "result":
                    raise WireProtocolError(
                        "unexpected worker message during checkpoint"
                    )
                status = message.body.get("status")
                if status == "checkpoint_refused":
                    result = _object(
                        message.body,
                        {
                            "status",
                            "request_id",
                            "code",
                            "detail",
                            "retryable",
                            "observed_sequence",
                        },
                        "checkpoint refusal",
                    )
                    if (
                        result["request_id"] != request_id
                        or type(result["retryable"]) is not bool
                    ):
                        raise WireProtocolError(
                            "checkpoint refusal identity is invalid"
                        )
                    return CheckpointRefusal(
                        _text(result["code"], "checkpoint refusal code"),
                        _text(result["detail"], "checkpoint refusal detail"),
                        result["retryable"],
                    )
                result = _object(
                    message.body,
                    {"status", "request_id", "observed_sequence"},
                    "checkpoint result",
                )
                if (
                    result["status"] != "checkpoint"
                    or result["request_id"] != request_id
                    or result["observed_sequence"] != observed_sequence
                ):
                    raise WireProtocolError(
                        "checkpoint result advanced from its requested frontier"
                    )
                proposal = chunks.finish()
                if proposal is None:
                    raise WireProtocolError(
                        "checkpoint result omitted its state proposal"
                    )
                self.latest_checkpoint = proposal
                return proposal
        finally:
            self._step_lock.release()


    def prepare_checkpoint(
        self,
        source: CheckpointEnvelope,
        source_dependencies: tuple[str, ...],
        target_dependencies: tuple[str, ...],
    ) -> tuple[str, str, str, CheckpointProposal | None]:
        self.prepare()
        if not self._step_lock.acquire(blocking=False):
            raise ModuleExecutionError(
                "boundary_unavailable",
                "target checkpoint preparation is already active",
                retryable=True,
            )
        try:
            key = replace(self.key, request_id=f"checkpoint-adoption:{self.binding}")
            chunk_messages: list[Mapping[str, object]] = []
            self._send(
                "checkpoint_prepare",
                key,
                {
                    "source": _checkpoint_to_wire(source),
                    "source_dependencies": list(source_dependencies),
                    "target_dependencies": list(target_dependencies),
                    "declared_at_sequence": 0,
                },
            )
            deadline = time.monotonic() + 30.0
            while True:
                message = self._receive(max(0.0, deadline - time.monotonic()))
                if not _same_scope(message.header.key, key):
                    raise WireProtocolError(
                        "checkpoint compatibility response escaped its request scope"
                    )
                if message.header.kind == "checkpoint":
                    if len(chunk_messages) >= 7:
                        raise WireProtocolError(
                            "checkpoint preparation emitted too many chunks"
                        )
                    chunk_messages.append(message.body)
                    continue
                if message.header.kind == "checkpoint_compatibility":
                    body = _object(
                        message.body,
                        {"disposition", "target_schema_id", "reason"},
                        "checkpoint compatibility",
                    )
                    disposition = _text(body["disposition"], "checkpoint disposition")
                    if disposition not in {"compatible", "migrate", "incompatible"}:
                        raise WireProtocolError(
                            "checkpoint compatibility disposition is invalid"
                        )
                    chunks = _CheckpointChunks(
                        self,
                        0,
                        expected_envelope=(
                            source if disposition == "compatible" else None
                        ),
                    )
                    for chunk_message in chunk_messages:
                        chunks.append(chunk_message)
                    proposal = (
                        chunks.finish() if disposition != "incompatible" else None
                    )
                    if disposition == "compatible" and (
                        proposal is None or proposal.payload != source
                    ):
                        raise WireProtocolError(
                            "compatible checkpoint response changed source state"
                        )
                    return (
                        disposition,
                        _text(body["target_schema_id"], "target checkpoint schema"),
                        _text(body["reason"], "checkpoint compatibility reason"),
                        proposal,
                    )
                if message.header.kind == "failure":
                    self._raise_failure(message)
                raise WireProtocolError(
                    f"unexpected worker message during checkpoint preparation: {message.header.kind}"
                )
        finally:
            self._step_lock.release()


    def close(self, reason: str, deadline: float) -> tuple[bool, tuple[str, ...]]:
        child_absent = True
        if self.children is not None:
            child_absent = self.children.close(reason, deadline)
        domains = self.domains.close(reason)
        pending = (*domains.provider_exchanges, *domains.tool_executions, *domains.approvals)
        if not self._prepared.wait(timeout=max(0, deadline - time.monotonic())):
            return False, tuple(pending)
        if self.channel is not None:
            cleanup = self.channel.close(reason)
            self.owner._replace_worker(self, cleanup=cleanup)
        else:
            cleanup = self.ownership.cleanup
        absent = (
            cleanup is not None and cleanup.status == "confirmed_absent"
            or self.channel is None and self.ownership.resource_id is None
        )
        if absent and child_absent and not pending:
            root = Path(self.ownership.staging_root)
            if root.parent.exists():
                if root.exists():
                    root.chmod(0o755)
                shutil.rmtree(root.parent)
            return True, ()
        return False, tuple(pending)


class _CheckpointChunks:
    def __init__(
        self,
        worker: _ModuleWorker,
        sequence: int,
        *,
        expected_envelope: CheckpointEnvelope | None = None,
    ) -> None:
        self.worker, self.sequence = worker, sequence
        self.expected_envelope = expected_envelope
        self._metadata: Mapping[str, object] | None = None
        self._chunks: list[bytes] = []
        self._bytes = 0

    def append(self, value: Mapping[str, object]) -> None:
        fields = {"phase", "chunk_index", "chunk_count", "total_bytes", "schema_id", "source_generation_id", "source_module_id", "source_instance_id", "source_work_id", "source_attempt_id", "declared_at_sequence", "body"}
        body = _object(value, fields, "checkpoint chunk")
        index = _integer(body["chunk_index"], "chunk index")
        count = _integer(body["chunk_count"], "chunk count", 1)
        total = _integer(body["total_bytes"], "checkpoint total bytes")
        key = self.worker.key
        expected = self.expected_envelope
        if (
            body["phase"] != "proposal" or index != len(self._chunks) or index >= count
            or count > 7 or total > self.worker.limits.max_checkpoint_bytes
            or body["source_generation_id"] != (
                expected.source_generation_id if expected is not None else key.generation_id
            )
            or body["source_module_id"] != (
                expected.source_module_id
                if expected is not None
                else self.worker.package.manifest.logical_package
            )
            or body["source_instance_id"] != (
                expected.source_instance_id if expected is not None else key.instance_id
            )
            or body["source_work_id"] != (
                expected.source_work_id if expected is not None else key.work_id
            )
            or body["source_attempt_id"] != (
                expected.source_attempt_id if expected is not None else key.attempt_id
            )
            or body["declared_at_sequence"] != self.sequence
            or (
                expected is not None
                and body["schema_id"] != expected.schema_id
            )
            or (
                expected is None
                and body["schema_id"] != self.worker.package.manifest.checkpoint_schema_id
            )
        ):
            raise WireProtocolError(
                "checkpoint does not match its owner, frontier or declared bound"
            )
        metadata = {
            name: item
            for name, item in body.items()
            if name not in {"body", "chunk_index"}
        }
        if self._metadata is not None and self._metadata != metadata:
            raise WireProtocolError("checkpoint metadata changed between chunks")
        self._metadata = metadata
        chunk = decode_bytes(body["body"], maximum=160 * 1024)
        self._bytes += len(chunk)
        if self._bytes > total:
            raise WireProtocolError("checkpoint exceeds its declared length")
        self._chunks.append(chunk)

    def finish(self) -> CheckpointProposal | None:
        metadata = self._metadata
        if metadata is None:
            return None
        if (
            len(self._chunks) != metadata["chunk_count"]
            or self._bytes != metadata["total_bytes"]
        ):
            raise WireProtocolError("checkpoint transfer is incomplete")
        envelope = CheckpointEnvelope(
            schema_id=metadata["schema_id"],
            body=b"".join(self._chunks),
            source_generation_id=metadata["source_generation_id"],
            source_module_id=metadata["source_module_id"],
            source_instance_id=metadata["source_instance_id"],
            source_work_id=metadata["source_work_id"],
            source_attempt_id=metadata["source_attempt_id"],
        )
        return CheckpointProposal(envelope, self.sequence)
