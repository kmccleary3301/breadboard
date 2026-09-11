"""Typed adapter for compiler-bound author child streams.

The durable child factory owns lifecycle, sequencing and retention.  This
adapter only translates that owner seam to Main's module dispatcher; existing
process and Ray adapters intentionally do not implement this protocol.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from breadboard.modules.author import (
    ChildFailed,
    ChildHandle,
    ChildOutput,
    ChildSucceeded,
    ChildTarget,
    ChildUnknown,
    InputEnvelope,
)
from breadboard.product.runtime._child_state import (
    ChildActivation,
    ChildError,
    ChildSpec,
    ChildStreamingExecutionAdapter,
    ExecutionTarget,
)
from breadboard.product.runtime.artifacts import ArtifactRef


ChildStreamItem = ChildOutput | ChildSucceeded | ChildFailed | ChildUnknown


class AuthorChildBackend(Protocol):
    """Internal host seam implemented by Main's semantic dispatcher.

    The backend receives owner-minted public identities and a compiler-resolved
    target.  It must not accept authority, generation or target declarations
    from a worker payload.  ``next_output`` may block while the author worker
    produces an item; ``ChildUnknown`` is an unresolved observation, not a
    terminal result.
    """

    def start(
        self,
        *,
        target: ChildTarget,
        module_binding: str,
        handle: ChildHandle,
        initial_input: InputEnvelope,
        execution_target_ref: str,
        scope_fence: Callable[[], None] | None,
    ) -> ExecutionTarget:
        ...

    def submit_input(
        self,
        target: Mapping[str, Any],
        envelope: InputEnvelope,
        *,
        scope_fence: Callable[[], None] | None,
    ) -> None:
        ...

    def next_output(
        self,
        target: Mapping[str, Any],
        *,
        scope_fence: Callable[[], None] | None,
    ) -> ChildStreamItem:
        ...

    def observe(self, target: Mapping[str, Any]) -> str:
        ...

    def cancel(self, target: Mapping[str, Any]) -> bool | None:
        ...

    def prepare_result(
        self,
        target: Mapping[str, Any],
        spec: ChildSpec,
    ) -> bytes | ArtifactRef | None:
        ...


class AuthorChildStreamAdapter(ChildStreamingExecutionAdapter):
    """Execution adapter that consumes an owner-provided module backend."""

    family = "author-child-stream"

    def __init__(self, backend: AuthorChildBackend) -> None:
        self.backend = backend

    @staticmethod
    def _binding(activation: ChildActivation, spec: ChildSpec) -> ChildTarget:
        binding = activation.target_binding
        if binding is None:
            raise ChildError("author child target is not compiler-bound")
        binding_name = spec.adapter_config.get("module_binding")
        if type(binding_name) is not str or not binding_name.strip():
            raise ChildError("author child adapter config requires module_binding")
        return binding

    @staticmethod
    def _handle(activation: ChildActivation, spec: ChildSpec) -> ChildHandle:
        if activation.child_handle is not None:
            return activation.child_handle
        return ChildHandle(
            child_work_id=activation.child_work_item_id,
            child_generation_id=spec.lock.generation_id,
            child_instance_id=activation.child_session_id,
            child_attempt_id=activation.attempt_id,
            parent_work_id=activation.parent_work_item_id,
            child_label=spec.title,
        )

    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        binding = self._binding(activation, spec)
        initial_input = activation.initial_input
        if initial_input is None:
            raise ChildError("author child stream requires an initial input")
        target = self.backend.start(
            target=binding,
            module_binding=spec.adapter_config["module_binding"],
            handle=self._handle(activation, spec),
            initial_input=initial_input,
            execution_target_ref=activation.execution_target_ref,
            scope_fence=activation.scope_fence,
        )
        if not isinstance(target, ExecutionTarget):
            raise ChildError("author child backend returned an invalid execution target")
        if target.execution_target_ref != activation.execution_target_ref:
            raise ChildError("author child backend returned an unbound execution target")
        return target

    def submit_input(
        self,
        target: Mapping[str, Any],
        envelope: InputEnvelope,
        *,
        scope_fence: Callable[[], None] | None = None,
    ) -> None:
        if scope_fence is not None:
            scope_fence()
        self.backend.submit_input(target, envelope, scope_fence=scope_fence)

    def next_output(
        self,
        target: Mapping[str, Any],
        *,
        scope_fence: Callable[[], None] | None = None,
    ) -> ChildStreamItem:
        if scope_fence is not None:
            scope_fence()
        item = self.backend.next_output(target, scope_fence=scope_fence)
        if not isinstance(item, (ChildOutput, ChildSucceeded, ChildFailed, ChildUnknown)):
            raise ChildError("author child backend returned an invalid stream item")
        return item

    def observe(self, target: Mapping[str, Any]) -> str:
        status = self.backend.observe(target)
        if status == "unknown":
            return "pending"
        if status not in {"absent", "pending", "running", "started", "live", "accepted", "completed", "failed"}:
            raise ChildError("author child backend returned an invalid observation")
        return status

    def cancel(self, target: Mapping[str, Any]) -> bool:
        # ``None`` is an unresolved cleanup observation.  Existing factory
        # cancellation treats False as retained/pending and must not release.
        return self.backend.cancel(target) is True

    def prepare_result(
        self,
        target: Mapping[str, Any],
        spec: ChildSpec,
    ) -> bytes | ArtifactRef | None:
        return self.backend.prepare_result(target, spec)

    def release_terminal(self, target: Mapping[str, Any]) -> bool:
        release = getattr(self.backend, "release_terminal", None)
        if not callable(release):
            return False
        return release(target) is True

    def acknowledge_result(
        self,
        target: Mapping[str, Any],
        *,
        result_refs: tuple[ArtifactRef, ...],
    ) -> None:
        acknowledge = getattr(self.backend, "acknowledge_result", None)
        if callable(acknowledge):
            acknowledge(target, result_refs=result_refs)

    def recover(self, target: Mapping[str, Any]) -> ExecutionTarget | None:
        recover = getattr(self.backend, "recover", None)
        if not callable(recover):
            return None
        recovered = recover(target)
        if recovered is None:
            return None
        if not isinstance(recovered, ExecutionTarget) or recovered.execution_target_ref != target.get("ref"):
            raise ChildError("author child backend returned an unbound recovered target")
        return recovered

    def cleanup_handoff(self, target: Mapping[str, Any]) -> None:
        cleanup = getattr(self.backend, "cleanup_handoff", None)
        if callable(cleanup):
            cleanup(target)


__all__ = ["AuthorChildBackend", "AuthorChildStreamAdapter", "ChildStreamItem"]
