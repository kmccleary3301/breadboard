from __future__ import annotations

from pathlib import Path
from typing import Any

from breadboard.rl.phase2.hardening import ArtifactEgressRequest, EgressPolicy
from breadboard.rl.phase2.service import ArtifactRecord, ResourceCaps, RunStatus, RunSubmission, StreamEvent, resource_cap_rejections
from breadboard.rl.phase3.security_enforcement import enforce_artifact_egress, enforce_command_request, enforce_workspace_path
from breadboard.rl.phase3.store import SQLiteRLRunStore

DEFAULT_CAPS = ResourceCaps(max_tasks=128, max_gpus=8, max_budget_usd=500.0, max_duration_seconds=7200, max_artifact_bytes=512 * 1024 * 1024)
DEFAULT_EGRESS_POLICY = EgressPolicy(allowed_prefixes=("ws",), max_artifact_bytes=DEFAULT_CAPS.max_artifact_bytes)


class LiveRLRunService:
    def __init__(self, store: SQLiteRLRunStore | str | Path | None = None, *, caps: ResourceCaps = DEFAULT_CAPS, egress_policy: EgressPolicy = DEFAULT_EGRESS_POLICY):
        self.store = store if isinstance(store, SQLiteRLRunStore) else SQLiteRLRunStore(store or ":memory:")
        self.caps = caps
        self.egress_policy = egress_policy

    def submit(self, submission: RunSubmission) -> RunStatus:
        command = submission.metadata.get("command") if isinstance(submission.metadata, dict) else None
        if command is not None:
            if not isinstance(command, list):
                raise PermissionError("command request denied: command must be a list")
            enforce_command_request(command, workspace_relative_path=submission.env_package_ref, workspace_id=submission.workspace_id)
        enforce_workspace_path(submission.env_package_ref, tenant_id=submission.tenant_id, workspace_id=submission.workspace_id)
        rejections = resource_cap_rejections(submission, self.caps)
        state = "queued" if not rejections else "rejected"
        reason = "; ".join(rejections)
        self.store.create_run(submission, self.caps, state=state, reason=reason)
        self.store.append_event(submission.run_id, event_type="run.submitted", state=state, message=reason or "run queued", target_run_id=submission.target_run_id)
        return self.store.status(submission.run_id)

    def status(self, run_id: str, *, tenant_id: str | None = None, workspace_id: str | None = None) -> RunStatus:
        if tenant_id is not None:
            self.store.assert_tenant(run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        return self.store.status(run_id)

    def start(self, run_id: str) -> RunStatus:
        status = self.store.status(run_id)
        self.store.update_state(run_id, state="running")
        self.store.append_event(run_id, event_type="run.start", state="running", message="run started", target_run_id=status.target_run_id)
        return self.store.status(run_id)

    def cancel(self, run_id: str, *, reason: str = "cancel requested") -> RunStatus:
        status = self.store.status(run_id)
        self.store.update_state(run_id, state="cancel_requested", cancellation_state="requested", reason=reason)
        self.store.append_event(run_id, event_type="cancel.requested", state="cancel_requested", message=reason, target_run_id=status.target_run_id)
        return self.store.status(run_id)

    def acknowledge_cancelled(self, run_id: str, *, reason: str = "cancelled") -> RunStatus:
        status = self.store.status(run_id)
        self.store.update_state(run_id, state="cancelled", cancellation_state="acknowledged", reason=reason)
        self.store.append_event(run_id, event_type="cancel.acknowledged", state="cancelled", message=reason, target_run_id=status.target_run_id)
        return self.store.status(run_id)

    def complete(self, run_id: str, *, succeeded: bool = True, reason: str = "") -> RunStatus:
        status = self.store.status(run_id)
        state = "succeeded" if succeeded else "failed"
        self.store.update_state(run_id, state=state, reason=reason)
        self.store.append_event(run_id, event_type="run.end", state=state, message=reason or state, target_run_id=status.target_run_id)
        return self.store.status(run_id)

    def add_artifact(self, record: ArtifactRecord, *, tenant_id: str, workspace_id: str) -> ArtifactRecord:
        self.store.assert_tenant(record.run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        self.store.add_artifact(record, tenant_id=tenant_id, workspace_id=workspace_id)
        status = self.store.status(record.run_id)
        self.store.append_event(record.run_id, event_type="artifact.added", state=status.state, message=record.artifact_id, target_run_id=status.target_run_id, payload=record.to_dict())
        return record

    def collect(self, run_id: str, *, tenant_id: str, workspace_id: str) -> list[ArtifactRecord]:
        self.store.assert_tenant(run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        artifacts = self.store.artifacts(run_id)
        for artifact in artifacts:
            enforce_workspace_path(artifact.relative_path, tenant_id=tenant_id, workspace_id=workspace_id)
            if artifact.egress_allowed:
                enforce_artifact_egress(ArtifactEgressRequest(artifact.relative_path, artifact.bytes, "tenant_internal"), self.egress_policy)
        return artifacts

    def replay(self, run_id: str, artifact_id: str, *, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        self.store.assert_tenant(run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        artifact = self.store.artifact(run_id, artifact_id)
        enforce_workspace_path(artifact.relative_path, tenant_id=tenant_id, workspace_id=workspace_id)
        if not artifact.egress_allowed:
            return {"available": False, "artifact_id": artifact_id, "reason": "egress denied"}
        enforce_artifact_egress(ArtifactEgressRequest(artifact.relative_path, artifact.bytes, "tenant_internal"), self.egress_policy)
        return {"available": True, "artifact_id": artifact_id, "replay_path": artifact.relative_path, "sha256": artifact.sha256}

    def audit(self, run_id: str, *, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        row = self.store.assert_tenant(run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        status = self.store.status(run_id)
        return {
            "run_id": run_id,
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "target_run_id": status.target_run_id,
            "state": status.state,
            "persistent_store": "sqlite",
            "scorecard_update_allowed": False,
        }

    def stream_since(self, run_id: str, *, from_sequence: int = 0, tenant_id: str | None = None, workspace_id: str | None = None) -> list[StreamEvent]:
        if tenant_id is not None:
            self.store.assert_tenant(run_id, tenant_id=tenant_id, workspace_id=workspace_id)
        return self.store.events_since(run_id, from_sequence)
