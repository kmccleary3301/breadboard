from __future__ import annotations

from copy import deepcopy
from typing import Any

from breadboard.rl.env_package.schema import EnvPackage
from breadboard.rl.runtime.base import RuntimeHealth, RuntimeResult, RuntimeSnapshot


class LocalProcessToyRuntime:
    """Deterministic local toy runtime for M3 lifecycle proof.

    This is not a security boundary. It only exercises the lifecycle contract
    before Docker/gVisor/SWE hardening is introduced in later milestones.
    """

    backend_id = "local_process"

    def __init__(self, package: EnvPackage, task_id: str, *, expected_answer: str = "42") -> None:
        self.package = package
        self.task_id = str(task_id or "").strip()
        self.expected_answer = str(expected_answer)
        self._state: dict[str, Any] = {
            "reset": False,
            "terminated": False,
            "turn": 0,
            "actions": [],
            "submitted_answer": None,
        }

    def health(self) -> RuntimeHealth:
        reasons: list[str] = []
        if self.package.runtime.backend != self.backend_id:
            reasons.append(f"runtime.backend must be {self.backend_id}")
        if self.package.verifier.kind != "exact_match":
            reasons.append("local toy runtime requires verifier.kind=exact_match")
        if not self.task_id:
            reasons.append("task_id must be non-empty")
        return RuntimeHealth(
            ready=not reasons,
            backend=self.backend_id,
            reasons=reasons,
            metadata={
                "package_id": self.package.package_id,
                "task_id": self.task_id,
            },
        )

    def reset(self) -> RuntimeResult:
        health = self.health()
        if not health.ready:
            return RuntimeResult(
                success=False,
                result_kind="reset",
                error={"kind": "runtime_not_ready", "reasons": health.reasons},
            )
        self._state = {
            "reset": True,
            "terminated": False,
            "turn": 0,
            "actions": [],
            "submitted_answer": None,
        }
        return RuntimeResult(
            success=True,
            result_kind="reset",
            observation=self._observation(),
            evidence={"task_id": self.task_id, "package_id": self.package.package_id},
        )

    def observe(self) -> RuntimeResult:
        if not self._state["reset"]:
            return self._error("observe", "not_reset", "runtime must be reset before observe")
        if self._state["terminated"]:
            return self._error("observe", "terminated", "runtime is terminated")
        return RuntimeResult(success=True, result_kind="observe", observation=self._observation())

    def step(self, action: dict[str, Any]) -> RuntimeResult:
        if not self._state["reset"]:
            return self._error("step", "not_reset", "runtime must be reset before step")
        if self._state["terminated"]:
            return self._error("step", "terminated", "runtime is terminated")

        tool = str(action.get("tool") or "").strip()
        if tool == "sleep":
            return self._error("step", "timeout", "step exceeded timeout", {"action": dict(action)})
        if tool == "runtime_crash":
            return self._error("step", "runtime_crash", "simulated runtime crash", {"action": dict(action)})
        if tool not in {"python", "submit_answer"}:
            return self._error("step", "unknown_tool", f"unknown tool: {tool}", {"action": dict(action)})

        self._state["turn"] += 1
        self._state["actions"].append(dict(action))
        if tool == "submit_answer":
            self._state["submitted_answer"] = str(action.get("answer") or "")
            return RuntimeResult(
                success=True,
                result_kind="step",
                observation=self._observation(),
                done=True,
                evidence={"submitted_answer": self._state["submitted_answer"]},
            )
        return RuntimeResult(
            success=True,
            result_kind="step",
            observation=self._observation(),
            done=False,
            evidence={"tool": tool},
        )

    def evaluate(self) -> RuntimeResult:
        if not self._state["reset"]:
            return self._error("evaluate", "not_reset", "runtime must be reset before evaluate")
        if self._state["terminated"]:
            return self._error("evaluate", "terminated", "runtime is terminated")
        submitted = self._state.get("submitted_answer")
        if submitted is None:
            return self._error("evaluate", "no_submission", "submit_answer is required before evaluate")
        reward = 1.0 if str(submitted).strip() == self.expected_answer else 0.0
        return RuntimeResult(
            success=True,
            result_kind="evaluate",
            reward=reward,
            done=True,
            evidence={
                "verifier_id": self.package.verifier.verifier_id,
                "expected_answer_hash": "sha256:toy-expected-answer",
                "submitted_answer": submitted,
            },
        )

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            snapshot_id=f"{self.package.package_id}.{self.task_id}.turn{self._state['turn']}",
            state=deepcopy(self._state),
            metadata={"backend": self.backend_id, "package_id": self.package.package_id},
        )

    def restore(self, snapshot: RuntimeSnapshot) -> RuntimeResult:
        self._state = deepcopy(snapshot.state)
        return RuntimeResult(
            success=True,
            result_kind="restore",
            observation=self._observation() if self._state.get("reset") and not self._state.get("terminated") else None,
            evidence={"snapshot_id": snapshot.snapshot_id},
        )

    def terminate(self) -> RuntimeResult:
        self._state["terminated"] = True
        return RuntimeResult(
            success=True,
            result_kind="terminate",
            done=True,
            evidence={"actions_recorded": len(self._state.get("actions", []))},
        )

    def _observation(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": "Use tools if needed, then submit the answer to the toy task.",
            "turn": self._state["turn"],
            "submitted": self._state.get("submitted_answer") is not None,
        }

    @staticmethod
    def _error(
        result_kind: str,
        error_kind: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        return RuntimeResult(
            success=False,
            result_kind=result_kind,
            error={"kind": error_kind, "message": message},
            evidence=dict(evidence or {}),
        )
