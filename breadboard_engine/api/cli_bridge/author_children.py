"""Owner-backed execution of worker child operations.

`_ModuleWorker` translates framed ``child_request`` operations into calls on
:class:`AuthorChildren`.  This module does not invent state machines: durable
child ownership (lifecycle, sequencing, retention, settlement,
reconciliation) belongs to the existing :class:`DurableChildFactory` in
``breadboard.product.runtime``.  This module is the semantic backend between
those owners: it validates worker payloads against the admitted graph,
delegates to the durable owner, and translates results back across the wire.

Handles are minted by the durable factory and validated by it on every
operation; worker payloads never contribute identity.  Start targets must
match an edge the admitted graph already pins.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Mapping

from breadboard.modules.author import (
    ChildFailed,
    ChildHandle,
    ChildOutput,
    ChildSucceeded,
    ChildTarget,
    ChildUnknown,
    InputEnvelope,
    ModuleInput,
    OutputEnvelope,
)
from breadboard.modules.transport import encode_bytes
from breadboard.product.runtime._child_state import ChildSpec, ExecutionTarget
from breadboard.product.runtime._child_stream_adapter import (
    AuthorChildStreamAdapter,
)
from breadboard.product.runtime.children import DurableChildFactory

from .author_runtime import ModuleExecutionError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .author_runtime import ModuleRuntime, _ModuleWorker


def _require(condition: Any, code: str, detail: str) -> None:
    if not condition:
        raise ModuleExecutionError(code, detail)


def _child_target_from_dict(raw: Mapping[str, Any]) -> ChildTarget:
    _require(isinstance(raw, Mapping), "child_protocol_mismatch", "child target must be an object")
    try:
        return ChildTarget(
            label=raw["label"],
            target=raw["target"],
            contract_id=raw["contract_id"],
            input_schema_ids=tuple(raw["input_schema_ids"]),
            output_schema_ids=tuple(raw["output_schema_ids"]),
        )
    except (KeyError, TypeError) as error:
        raise ModuleExecutionError(
            "child_protocol_mismatch", "child target payload is malformed"
        ) from error


def _handle_from_dict(raw: Mapping[str, Any]) -> ChildHandle:
    _require(isinstance(raw, Mapping), "child_protocol_mismatch", "child handle must be an object")
    try:
        return ChildHandle(
            child_work_id=raw["child_work_id"],
            child_generation_id=raw["child_generation_id"],
            child_instance_id=raw["child_instance_id"],
            child_attempt_id=raw["child_attempt_id"],
            parent_work_id=raw["parent_work_id"],
            child_label=raw["child_label"],
        )
    except (KeyError, TypeError) as error:
        raise ModuleExecutionError(
            "child_protocol_mismatch", "child handle payload is malformed"
        ) from error


def _item_payload(item: ChildOutput | ChildSucceeded | ChildFailed | ChildUnknown) -> dict[str, Any]:
    if isinstance(item, ChildOutput):
        return {
            "item_kind": "output",
            "output": {
                "child_work_id": item.child_work_id,
                "child_attempt_id": item.child_attempt_id,
                "sequence": item.sequence,
                "schema_id": item.output.schema_id,
                "body": encode_bytes(item.output.body),
            },
        }
    return {"item_kind": "outcome", "outcome": _outcome_payload(item)}


def _outcome_payload(item: ChildSucceeded | ChildFailed | ChildUnknown) -> dict[str, Any]:
    if isinstance(item, ChildSucceeded):
        return {
            "kind": "succeeded",
            "child_work_id": item.child_work_id,
            "child_attempt_id": item.child_attempt_id,
            "output": {
                "schema_id": item.output.schema_id,
                "body": encode_bytes(item.output.body),
            },
        }
    if isinstance(item, ChildFailed):
        return {
            "kind": "failed",
            "child_work_id": item.child_work_id,
            "child_attempt_id": item.child_attempt_id,
            "code": item.code,
            "detail": item.detail,
        }
    return {
        "kind": "unknown",
        "child_work_id": item.child_work_id,
        "child_attempt_id": item.child_attempt_id,
        "reason": item.reason,
    }


class _ChildRun:
    """One started child: its module runtime, driver thread and stream items."""

    def __init__(
        self,
        runtime: "ModuleRuntime",
        handle: ChildHandle,
        target: ChildTarget,
        execution_target_ref: str,
        initial_input: InputEnvelope,
    ) -> None:
        self.runtime = runtime
        self.handle = handle
        self.target = target
        self.execution_target_ref = execution_target_ref
        self.items: "queue.Queue[ChildOutput | ChildSucceeded | ChildFailed | ChildUnknown]" = queue.Queue()
        self.inputs: "queue.Queue[InputEnvelope | None]" = queue.Queue()
        self._next_output_sequence = 0
        self._closed = threading.Event()
        self.thread = threading.Thread(
            target=self._drive, args=(initial_input,),
            name=f"author-child-{handle.child_work_id}", daemon=True,
        )

    def emit_output(
        self, output: OutputEnvelope, key: Any, module_id: str,
        output_sequence: int, final: bool,
    ) -> None:
        """Owner-supplied emit hook: intermediate outputs join the stream."""
        if final:
            # Terminal delivery happens after the driver's runtime settles.
            return
        if output.schema_id not in self.target.output_schema_ids:
            raise ModuleExecutionError(
                "schema_mismatch", "child output is outside its declared contract"
            )
        sequence = self._next_output_sequence
        self._next_output_sequence += 1
        self.items.put(ChildOutput(
            child_work_id=self.handle.child_work_id,
            child_attempt_id=self.handle.child_attempt_id,
            sequence=sequence,
            output=output,
        ))

    def _drive(self, envelope: InputEnvelope) -> None:
        sequence = 0
        try:
            self.runtime.prepare()
            while envelope is not None:
                try:
                    output = self.runtime.execute(
                        envelope,
                        input_id=f"child:{self.handle.child_work_id}:input:{sequence}",
                        turn_id=f"child:{self.handle.child_work_id}:turn:{sequence}",
                    )
                    if envelope.final and output is not None:
                        _require(
                            output.schema_id in self.target.output_schema_ids,
                            "schema_mismatch",
                            "child output is outside its declared contract",
                        )
                except ModuleExecutionError as error:
                    try:
                        self.runtime.close(reason=error.code)
                    except Exception:
                        pass
                    self.items.put(ChildFailed(
                        child_work_id=self.handle.child_work_id,
                        child_attempt_id=self.handle.child_attempt_id,
                        code=error.code, detail=error.detail,
                    ))
                    return
                if envelope.final:
                    _require(
                        output is not None, "input_exhausted",
                        "child policy produced no final output",
                    )
                    self.items.put(ChildSucceeded(
                        child_work_id=self.handle.child_work_id,
                        child_attempt_id=self.handle.child_attempt_id,
                        output=output,
                    ))
                    return
                envelope = self.inputs.get()
                sequence += 1
        except BaseException as error:  # noqa: BLE001 - the stream owns failure reporting
            logger.exception(
                "Author child runtime failed for child Work Item %s",
                self.handle.child_work_id,
            )
            try:
                self.runtime.close(reason="child_failed")
            except Exception:
                pass
            self.items.put(ChildFailed(
                child_work_id=self.handle.child_work_id,
                child_attempt_id=self.handle.child_attempt_id,
                code="child_failed", detail=str(error),
            ))
        finally:
            self._closed.set()

    def cancel(self, reason: str, deadline: float) -> bool:
        self.inputs.put(None)
        disposal = self.runtime.close(reason)
        self.thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return disposal.status == "confirmed_absent" and not self.thread.is_alive()


class _Backend:
    """Author child backend driven by the durable factory's adapter."""

    def __init__(self, children: "AuthorChildren") -> None:
        self.children = children
        self._runs: dict[str, _ChildRun] = {}
        self._runs_by_handle: dict[ChildHandle, _ChildRun] = {}
        self._runs_lock = threading.Lock()

    def run_for_handle(self, handle: ChildHandle) -> _ChildRun | None:
        with self._runs_lock:
            return self._runs_by_handle.get(handle)

    def _run(self, target: Mapping[str, Any]) -> _ChildRun:
        ref = target.get("ref") if isinstance(target, Mapping) else None
        _require(isinstance(ref, str) and bool(ref), "child_protocol_mismatch", "child target reference is missing")
        with self._runs_lock:
            run = self._runs.get(ref)
        _require(run is not None, "child_denied", "child target reference was not minted by this owner")
        assert run is not None
        return run

    def start(
        self, *, target: ChildTarget, module_binding: str, handle: ChildHandle,
        initial_input: InputEnvelope, execution_target_ref: str, scope_fence: Any,
    ) -> ExecutionTarget:
        owner = self.children.owner
        _require(
            scope_fence is not None, "child_protocol_mismatch",
            "author child start requires the owner's liveness fence",
        )
        record = owner.registry_call("get", handle.child_instance_id)
        _require(
            record is not None, "child_failed",
            "retained child Session record is missing during start",
        )
        from .author_runtime import ModuleExecutionRecord
        from breadboard.modules.authority import AdmissionGrant

        binding = module_binding
        record.module_execution = ModuleExecutionRecord(
            generation_id=owner.captured.materialization.lock.generation_id,
            root_binding=binding,
            work_item_id=handle.child_work_id,
            attempt_id=handle.child_attempt_id,
        )
        record.module_grant = AdmissionGrant(
            grant_id=f"{owner.grant.grant_id}:{handle.child_work_id}",
            authority_epoch=owner.grant.authority_epoch,
            declaration=owner.grant.declaration,
        )
        run: _ChildRun | None = None

        def emit_output(
            output: OutputEnvelope,
            key: Any,
            module_id: str,
            output_sequence: int,
            final: bool,
        ) -> None:
            assert run is not None
            # Dependency workers share this runtime's event callback, but their
            # outputs do not belong to the child root's declared stream.
            if key.instance_id != runtime.worker(binding).key.instance_id:
                return
            run.emit_output(output, key, module_id, output_sequence, final)

        runtime = self.children._child_runtime(
            record, emit_output=emit_output, parent_fence=scope_fence
        )
        run = _ChildRun(
            runtime=runtime,
            handle=handle,
            target=target,
            execution_target_ref=execution_target_ref,
            initial_input=initial_input,
        )
        with self._runs_lock:
            self._runs[execution_target_ref] = run
            self._runs_by_handle[handle] = run
        run.thread.start()
        return ExecutionTarget(
            execution_target_ref=execution_target_ref,
            metadata={
                "module_binding": binding,
                "worker_session_id": owner.record.session_id,
                "child_session_id": handle.child_instance_id,
            },
        )

    def submit_input(
        self, target: Mapping[str, Any], envelope: InputEnvelope, *, scope_fence: Any,
    ) -> None:
        run = self._run(target)
        if envelope.schema_id not in run.target.input_schema_ids:
            raise ModuleExecutionError(
                "schema_mismatch", "child input is outside its declared contract"
            )
        run.inputs.put(envelope)

    def next_output(self, target: Mapping[str, Any], *, scope_fence: Any) -> Any:
        run = self._run(target)
        while True:
            try:
                return run.items.get(timeout=0.5)
            except queue.Empty:
                if scope_fence is not None:
                    scope_fence()
    def observe(self, target: Mapping[str, Any]) -> str:
        run = self._run(target)
        if run._closed.is_set():
            return "absent"
        return "running"

    def cancel(self, target: Mapping[str, Any]) -> bool:
        run = self._run(target)
        return run.cancel("cancelled", time.monotonic() + 15.0)

    def prepare_result(self, target: Mapping[str, Any], spec: Any) -> None:
        return None

    def release_terminal(self, target: Mapping[str, Any]) -> bool:
        return self._run(target)._closed.is_set()

    def acknowledge_result(self, target: Mapping[str, Any], *, result_refs: Any) -> None:
        return None

    def cleanup_handoff(self, target: Mapping[str, Any]) -> None:
        return None

    def recover(self, target: Mapping[str, Any]) -> None:
        # Cross-restart child recovery is owned by the checkpoint/recovery
        # packet; an unresolved child attempt stays unknown, not replayed.
        return None

    def quiescence(self) -> tuple[str, ...]:
        with self._runs_lock:
            runs = tuple(self._runs.values())
        return tuple(
            f"child:{run.handle.child_work_id}"
            for run in runs
            if not run._closed.is_set() or not run.items.empty()
        )

    def close(self, reason: str, deadline: float) -> bool:
        with self._runs_lock:
            runs = tuple(self._runs.values())
        settled = True
        for run in runs:
            try:
                settled = run.cancel(reason, deadline) and settled
            except BaseException:
                settled = False
        return settled


class AuthorChildren:
    """Durable-owner-backed execution of one worker's child operations."""

    def __init__(self, owner: "ModuleRuntime", worker: "_ModuleWorker") -> None:
        self.owner = owner
        self.worker = worker
        self._backend = _Backend(self)
        self._adapter = AuthorChildStreamAdapter(self._backend)
        self._factory: DurableChildFactory | None = None
        self._factory_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._started_children = 0

    def _ensure_factory(self) -> DurableChildFactory:
        with self._factory_lock:
            factory = self._factory
            if factory is None:
                async def build() -> DurableChildFactory:
                    return DurableChildFactory.with_async_registry(
                        self.owner.workspace,
                        registry=self.owner.registry,
                        repository=self.owner.repository,
                        adapters=(self._adapter,),
                        artifact_store=self.owner.artifacts,
                    )
                factory = asyncio.run_coroutine_threadsafe(
                    build(), self.owner.loop,
                ).result(timeout=30)
                self._factory = factory
            return factory

    def _child_runtime(
        self, record: Any, *, emit_output: Any, parent_fence: Any,
    ) -> "ModuleRuntime":
        from .author_runtime import ModuleRuntime

        return ModuleRuntime(
            record=record,
            registry=self.owner.registry,
            captured=self.owner.captured,
            workspace=self.owner.workspace,
            storage_root=self.owner.storage_root / "children",
            repository=self.owner.repository,
            loop=self.owner.loop,
            session_lock=self.owner.session_lock,
            persist_session=lambda: self.owner.registry_call("persist", record),
            emit_output=emit_output,
            parent_fence=parent_fence,
            depth=self.owner.depth + 1,
            owns_work_lifecycle=False,
        )


    def quiescence(self) -> tuple[str, ...]:
        return self._backend.quiescence()

    def close(self, reason: str, deadline: float) -> bool:
        return self._backend.close(reason, deadline)

    def dispatch(self, body: Mapping[str, Any]) -> dict[str, Any]:
        operation = body.get("operation")
        if operation == "start":
            return self._start(body)
        if operation == "submit_input":
            return self._submit_input(body)
        if operation == "next_output":
            return self._next_output(body)
        if operation == "join":
            return self._join(body)
        raise ModuleExecutionError("child_protocol_mismatch", "unsupported child operation")

    def _start(self, body: Mapping[str, Any]) -> dict[str, Any]:
        pinned = next(
            (
                item for item in self.owner.child_targets(self.worker.binding)
                if item == _child_target_from_dict(body.get("target", {}))
            ),
            None,
        )
        if pinned is None:
            raise ModuleExecutionError("child_denied", "child target is not a pinned edge")
        child_scope = self.worker.scope.child
        if (
            child_scope is None
            or pinned.target not in child_scope.allowed_module_ids
            or self.owner.depth >= child_scope.max_depth
        ):
            raise ModuleExecutionError(
                "child_denied", "child target or depth is outside the effective grant"
            )
        try:
            initial = ModuleInput.from_dict(body["initial_input"])
        except (KeyError, TypeError, ValueError) as error:
            raise ModuleExecutionError(
                "child_protocol_mismatch", "child initial input is malformed"
            ) from error
        if initial.schema_id not in pinned.input_schema_ids:
            raise ModuleExecutionError(
                "schema_mismatch", "child input is outside its declared contract"
            )
        selected_binding = self.owner.bindings[self.worker.binding]["children"][pinned.label]
        spec = ChildSpec(
            title=f"{self.worker.binding}:{pinned.label}",
            task=f"author child {pinned.label} of binding {self.worker.binding}",
            lock=self.owner.captured.materialization.lock,
            worker_id=self.worker.key.worker_session_id,
            adapter_family=self._adapter.family,
            adapter_config={"module_binding": selected_binding},
        )
        with self._start_lock:
            if self._started_children >= self.worker.limits.max_children:
                raise ModuleExecutionError(
                    "capacity_approval_required",
                    "module exhausted its admitted child budget",
                )
            try:
                activation = self._ensure_factory().start(
                    parent_session_id=self.owner.record.session_id,
                    root_session_id=self.owner.canonical_root_session_id(),
                    parent_work_item_id=self.owner.work_id,
                    spec=spec,
                    initial_input=initial,
                    target_binding=pinned,
                    scope_fence=self.owner.require_live,
                )
            except Exception as error:
                logger.exception(
                    "Author child start failed for parent session %s",
                    self.owner.record.session_id,
                )
                raise ModuleExecutionError("child_failed", str(error)) from error
            self._started_children += 1
        return {"handle": asdict(activation.child_handle)}

    def _submit_input(self, body: Mapping[str, Any]) -> dict[str, Any]:
        handle = _handle_from_dict(body.get("handle", {}))
        try:
            value = ModuleInput.from_dict(body["input"])
        except (KeyError, TypeError, ValueError) as error:
            raise ModuleExecutionError(
                "child_protocol_mismatch", "child input is malformed"
            ) from error
        run = self._backend.run_for_handle(handle)
        if run is None:
            raise ModuleExecutionError(
                "child_denied", "child handle has no execution owned by this runtime"
            )
        if value.schema_id not in run.target.input_schema_ids:
            raise ModuleExecutionError(
                "schema_mismatch", "child input is outside its declared contract"
            )
        self._ensure_factory().submit_input(
            handle,
            value,
            scope_fence=self.owner.require_live,
        )
        return {}

    def _next_output(self, body: Mapping[str, Any]) -> dict[str, Any]:
        item = self._ensure_factory().next_output(
            _handle_from_dict(body.get("handle", {})),
            scope_fence=self.owner.require_live,
        )
        return _item_payload(item)

    def _join(self, body: Mapping[str, Any]) -> dict[str, Any]:
        raw = body.get("handles")
        _require(
            isinstance(raw, list), "child_protocol_mismatch",
            "child join handles must be an array",
        )
        handles = tuple(_handle_from_dict(item) for item in raw)
        outcomes = self._ensure_factory().join(
            handles, scope_fence=self.owner.require_live,
        )
        return {"outcomes": [_outcome_payload(item) for item in outcomes]}
