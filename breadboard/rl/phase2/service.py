from __future__ import annotations
from collections.abc import Mapping

import json

from dataclasses import dataclass, field
from typing import Any


SERVICE_CLAIM_BOUNDARY = "p2_m9_service_contract_not_live_fastapi_integration"
SERVICE_REPORT_ID = "bb_zyphra_rl_phase2_service_surface_v1"
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "rejected"}
ACTIVE_STATES = {"queued", "running", "cancel_requested"}


@dataclass(frozen=True)
class ResourceCaps:
    max_tasks: int
    max_gpus: int
    max_budget_usd: float
    max_duration_seconds: int
    max_artifact_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_artifact_bytes": self.max_artifact_bytes,
            "max_budget_usd": self.max_budget_usd,
            "max_duration_seconds": self.max_duration_seconds,
            "max_gpus": self.max_gpus,
            "max_tasks": self.max_tasks,
        }


@dataclass(frozen=True)
class RunSubmission:
    run_id: str
    tenant_id: str
    workspace_id: str
    env_package_ref: str
    target_run_id: str
    requested_tasks: int
    requested_gpus: int
    requested_budget_usd: float
    requested_duration_seconds: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_package_ref": self.env_package_ref,
            "metadata": dict(self.metadata),
            "requested_budget_usd": self.requested_budget_usd,
            "requested_duration_seconds": self.requested_duration_seconds,
            "requested_gpus": self.requested_gpus,
            "requested_tasks": self.requested_tasks,
            "run_id": self.run_id,
            "target_run_id": self.target_run_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
        }


@dataclass(frozen=True)
class StreamEvent:
    sequence: int
    run_id: str
    event_type: str
    state: str
    message: str
    target_run_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "message": self.message,
            "payload": dict(self.payload),
            "run_id": self.run_id,
            "sequence": self.sequence,
            "state": self.state,
            "target_run_id": self.target_run_id,
        }


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    state: str
    target_run_id: str
    accepted: bool
    cancellation_state: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "cancellation_state": self.cancellation_state,
            "reason": self.reason,
            "run_id": self.run_id,
            "state": self.state,
            "target_run_id": self.target_run_id,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    run_id: str
    artifact_id: str
    relative_path: str
    sha256: str
    bytes: int
    egress_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "bytes": self.bytes,
            "egress_allowed": self.egress_allowed,
            "relative_path": self.relative_path,
            "run_id": self.run_id,
            "sha256": self.sha256,
        }


@dataclass
class _RunRecord:
    submission: RunSubmission
    state: str
    accepted: bool
    cancellation_state: str = "not_cancelled"
    reason: str = ""
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    events: list[StreamEvent] = field(default_factory=list)

    def status(self) -> RunStatus:
        return RunStatus(
            run_id=self.submission.run_id,
            state=self.state,
            target_run_id=self.submission.target_run_id,
            accepted=self.accepted,
            cancellation_state=self.cancellation_state,
            reason=self.reason,
        )


class RLRunServiceContract:
    """In-memory product-facing contract model for Phase 2 run operations.

    The class intentionally models submit/status/cancel/collect/replay/audit
    semantics without binding the repository to a concrete FastAPI server.
    """

    def __init__(self, caps: ResourceCaps) -> None:
        self._caps = caps
        self._runs: dict[str, _RunRecord] = {}

    @property
    def caps(self) -> ResourceCaps:
        return self._caps

    def submit(self, submission: RunSubmission) -> RunStatus:
        if submission.run_id in self._runs:
            raise ValueError(f"duplicate run_id: {submission.run_id}")
        rejections = resource_cap_rejections(submission, self._caps)
        record = _RunRecord(
            submission=submission,
            state="rejected" if rejections else "queued",
            accepted=not rejections,
            reason="; ".join(rejections),
        )
        self._append_event(record, "run_rejected" if rejections else "run_submitted", record.reason or "run submitted", {})
        self._runs[submission.run_id] = record
        return record.status()

    def status(self, run_id: str) -> RunStatus:
        return self._record(run_id).status()

    def start(self, run_id: str) -> RunStatus:
        record = self._record(run_id)
        if record.state != "queued":
            raise ValueError(f"run {run_id} cannot start from state {record.state}")
        record.state = "running"
        self._append_event(record, "run_started", "run started", {})
        return record.status()

    def complete(self, run_id: str, *, succeeded: bool, reason: str = "") -> RunStatus:
        record = self._record(run_id)
        if record.state not in ACTIVE_STATES:
            raise ValueError(f"run {run_id} cannot complete from state {record.state}")
        record.state = "succeeded" if succeeded else "failed"
        record.reason = reason
        self._append_event(record, "run_completed", reason or record.state, {"succeeded": succeeded})
        return record.status()

    def cancel(self, run_id: str, *, operator_id: str, reason: str) -> RunStatus:
        record = self._record(run_id)
        if record.state in TERMINAL_STATES:
            raise ValueError(f"run {run_id} is already terminal: {record.state}")
        record.state = "cancel_requested"
        record.cancellation_state = "operator_requested"
        record.reason = reason
        self._append_event(record, "cancel_requested", reason, {"operator_id": operator_id})
        return record.status()

    def acknowledge_cancelled(self, run_id: str) -> RunStatus:
        record = self._record(run_id)
        if record.state != "cancel_requested":
            raise ValueError(f"run {run_id} has no pending cancellation")
        record.state = "cancelled"
        record.cancellation_state = "cancelled"
        self._append_event(record, "run_cancelled", record.reason or "run cancelled", {})
        return record.status()

    def add_artifact(self, artifact: ArtifactRecord) -> None:
        record = self._record(artifact.run_id)
        if artifact.bytes > self._caps.max_artifact_bytes:
            raise ValueError("artifact exceeds max_artifact_bytes")
        record.artifacts.append(artifact)
        self._append_event(record, "artifact_recorded", artifact.relative_path, {"artifact_id": artifact.artifact_id})

    def collect(self, run_id: str) -> dict[str, Any]:
        record = self._record(run_id)
        return {
            "artifacts": [artifact.to_dict() for artifact in sorted(record.artifacts, key=lambda item: item.artifact_id)],
            "claim_boundary": SERVICE_CLAIM_BOUNDARY,
            "run_id": run_id,
            "scorecard_update_allowed": False,
            "target_run_id": record.submission.target_run_id,
        }

    def stream(self, run_id: str) -> list[dict[str, Any]]:
        record = self._record(run_id)
        return [event.to_dict() for event in record.events]

    def replay(self, run_id: str, *, artifact_id: str) -> dict[str, Any]:
        record = self._record(run_id)
        artifact_ids = {artifact.artifact_id for artifact in record.artifacts}
        return {
            "artifact_id": artifact_id,
            "claim_boundary": SERVICE_CLAIM_BOUNDARY,
            "replay_available": artifact_id in artifact_ids,
            "run_id": run_id,
            "scorecard_update_allowed": False,
            "target_run_id": record.submission.target_run_id,
        }

    def audit(self, run_id: str) -> dict[str, Any]:
        record = self._record(run_id)
        return build_service_surface_report(record.submission, self._caps, record.status(), record.events)

    def _record(self, run_id: str) -> _RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise KeyError(f"unknown run_id: {run_id}") from exc

    def _append_event(self, record: _RunRecord, event_type: str, message: str, payload: Mapping[str, Any]) -> None:
        record.events.append(
            StreamEvent(
                sequence=len(record.events) + 1,
                run_id=record.submission.run_id,
                event_type=event_type,
                state=record.state,
                message=message,
                target_run_id=record.submission.target_run_id,
                payload=dict(payload),
            )
        )


def resource_cap_rejections(submission: RunSubmission, caps: ResourceCaps) -> list[str]:
    rejections: list[str] = []
    if submission.requested_tasks > caps.max_tasks:
        rejections.append("requested_tasks exceeds max_tasks")
    if submission.requested_gpus > caps.max_gpus:
        rejections.append("requested_gpus exceeds max_gpus")
    if submission.requested_budget_usd > caps.max_budget_usd:
        rejections.append("requested_budget_usd exceeds max_budget_usd")
    if submission.requested_duration_seconds > caps.max_duration_seconds:
        rejections.append("requested_duration_seconds exceeds max_duration_seconds")
    return rejections


def build_service_surface_report(
    submission: RunSubmission,
    caps: ResourceCaps,
    status: RunStatus,
    events: list[StreamEvent],
) -> dict[str, Any]:
    return {
        "api_contract": {
            "audit": "audit(run_id)",
            "cancel": "cancel(run_id, operator_id, reason)",
            "collect": "collect(run_id)",
            "replay": "replay(run_id, artifact_id)",
            "status": "status(run_id)",
            "stream": "stream(run_id)",
            "submit": "submit(RunSubmission)",
        },
        "cancellation_states": ["not_cancelled", "operator_requested", "cancelled"],
        "claim_boundary": SERVICE_CLAIM_BOUNDARY,
        "milestone_id": "P2-M9",
        "passed": status.accepted,
        "report_id": SERVICE_REPORT_ID,
        "resource_caps": caps.to_dict(),
        "run_states": ["queued", "running", "cancel_requested", "cancelled", "succeeded", "failed", "rejected"],
        "scorecard_update_allowed": False,
        "status": status.to_dict(),
        "submission": submission.to_dict(),
        "stream_events": [event.to_dict() for event in events],
        "target_run_id": submission.target_run_id,
    }


def validate_service_surface_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != SERVICE_REPORT_ID:
        errors.append("report_id must be service surface v1")
    if report.get("claim_boundary") != SERVICE_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be service contract boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if not str(report.get("target_run_id") or ""):
        errors.append("target_run_id must be non-empty")
    for name in ["submit", "status", "cancel", "collect", "replay", "audit", "stream"]:
        if name not in dict(report.get("api_contract") or {}):
            errors.append(f"api_contract missing {name}")
    sequences = [event.get("sequence") for event in report.get("stream_events") or [] if isinstance(event, Mapping)]
    if sequences != list(range(1, len(sequences) + 1)):
        errors.append("stream_events must have contiguous sequence numbers")
    return errors

def report_to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(dict(report), sort_keys=True, separators=(",", ":")) + "\n"
