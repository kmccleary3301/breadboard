from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.env_package.schema import EnvPackage
from breadboard.rl.runtime.base import RuntimeBackend, RuntimeHealth, RuntimeResult, RuntimeSnapshot
from breadboard.rl.runtime.local_process import LocalProcessToyRuntime
from breadboard.rl.session.events import SessionEvent
from breadboard.rl.session.lifecycle import SessionLifecycleState, SessionStatus


@dataclass
class LocalSession:
    session_id: str
    package: EnvPackage
    task_id: str
    runtime: RuntimeBackend
    lifecycle: SessionLifecycleState = field(default_factory=SessionLifecycleState)
    events: list[SessionEvent] = field(default_factory=list)
    last_result: RuntimeResult | None = None
    last_evaluation: RuntimeResult | None = None

    def health(self) -> RuntimeHealth:
        return self.runtime.health()

    def reset(self) -> RuntimeResult:
        self._require_not_terminated("reset")
        result = self.runtime.reset()
        self._record_result("reset", result, SessionStatus.READY if result.success else SessionStatus.FAILED)
        return result

    def observe(self) -> RuntimeResult:
        if self.lifecycle.status not in {SessionStatus.READY, SessionStatus.RUNNING}:
            raise ValueError(f"observe requires ready/running session, got {self.lifecycle.status}")
        result = self.runtime.observe()
        self.last_result = result
        self._append_event("observe", self.lifecycle.status, self.lifecycle.status, result)
        return result

    def step(self, action: dict[str, Any]) -> RuntimeResult:
        if self.lifecycle.status not in {SessionStatus.READY, SessionStatus.RUNNING}:
            raise ValueError(f"step requires ready/running session, got {self.lifecycle.status}")
        result = self.runtime.step(action)
        self._record_result("step", result, SessionStatus.RUNNING if result.success else SessionStatus.FAILED)
        return result

    def evaluate(self) -> RuntimeResult:
        if self.lifecycle.status not in {SessionStatus.READY, SessionStatus.RUNNING}:
            raise ValueError(f"evaluate requires ready/running session, got {self.lifecycle.status}")
        result = self.runtime.evaluate()
        self.last_evaluation = result if result.success else None
        self._record_result("evaluate", result, SessionStatus.EVALUATED if result.success else SessionStatus.FAILED)
        return result

    def snapshot(self) -> RuntimeSnapshot:
        if self.lifecycle.status == SessionStatus.TERMINATED:
            raise ValueError("snapshot is not allowed after terminate")
        snapshot = self.runtime.snapshot()
        self._append_event(
            "snapshot",
            self.lifecycle.status,
            self.lifecycle.status,
            RuntimeResult(success=True, result_kind="snapshot", evidence={"snapshot_id": snapshot.snapshot_id}),
        )
        return snapshot

    def restore(self, snapshot: RuntimeSnapshot) -> RuntimeResult:
        if self.lifecycle.status == SessionStatus.TERMINATED:
            raise ValueError("restore is not allowed after terminate")
        result = self.runtime.restore(snapshot)
        self._append_event("restore", self.lifecycle.status, self.lifecycle.status, result)
        self.last_result = result
        return result

    def terminate(self) -> RuntimeResult:
        if self.lifecycle.status == SessionStatus.TERMINATED:
            return RuntimeResult(success=True, result_kind="terminate", done=True, evidence={"already_terminated": True})
        result = self.runtime.terminate()
        self._record_result("terminate", result, SessionStatus.TERMINATED)
        return result

    def export_admission(self) -> dict[str, Any]:
        reasons: list[str] = []
        if self.lifecycle.status != SessionStatus.EVALUATED:
            reasons.append(f"lifecycle_status={self.lifecycle.status}")
        if self.last_evaluation is None or not self.last_evaluation.success:
            reasons.append("missing_successful_evaluation")
        return {
            "session_id": self.session_id,
            "lifecycle_status": self.lifecycle.status,
            "trainable": False,
            "exportable_debug": not reasons,
            "blocked_reasons": reasons,
            "claim_boundary": "m3_lifecycle_only_not_trainable_export",
        }

    def _record_result(self, event_kind: str, result: RuntimeResult, success_status: str) -> None:
        before = self.lifecycle.status
        next_status = success_status if result.success else SessionStatus.FAILED
        self.lifecycle = self.lifecycle.transition(next_status)
        self.last_result = result
        self._append_event(event_kind, before, self.lifecycle.status, result)

    def _append_event(
        self,
        event_kind: str,
        status_before: str,
        status_after: str,
        result: RuntimeResult,
    ) -> None:
        self.events.append(
            SessionEvent(
                event_id=f"{self.session_id}.event.{len(self.events) + 1}",
                session_id=self.session_id,
                event_kind=event_kind,
                status_before=status_before,
                status_after=status_after,
                payload=result.to_dict(),
                error=result.error,
            )
        )

    def _require_not_terminated(self, operation: str) -> None:
        if self.lifecycle.status == SessionStatus.TERMINATED:
            raise ValueError(f"{operation} is not allowed after terminate")


def create_local_session(package: EnvPackage, task_id: str) -> LocalSession:
    session_id = f"{package.package_id}.{task_id}.session"
    return LocalSession(
        session_id=session_id,
        package=package,
        task_id=task_id,
        runtime=LocalProcessToyRuntime(package, task_id),
    )
