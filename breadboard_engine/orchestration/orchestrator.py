"""Multi-agent orchestrator scaffold (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from .coordination import (
    build_coordination_inspection_snapshot,
    build_directive,
    build_review_verdict,
    validate_directive,
    validate_review_verdict,
)
from .schema import TeamConfig
from .event_log import Event, EventLog
from .job_manager import JobManager, JobRef
from .bus_adapter import BusAdapter


@dataclass
class SpawnResult:
    job: JobRef
    ack_payload: Dict[str, Any]


class MultiAgentOrchestrator:
    """Coordinates multi-agent execution and produces deterministic event logs.

    This scaffold is intentionally minimal. Phase 8 will extend this with
    deterministic scheduling, wakeup injections, and replay.
    """

    def __init__(
        self,
        team_config: TeamConfig,
        *,
        event_log: Optional[EventLog] = None,
        job_manager: Optional[JobManager] = None,
        bus_adapter: Optional[BusAdapter] = None,
    ) -> None:
        self.team_config = team_config
        self.event_log = event_log or EventLog()
        self.job_manager = job_manager or JobManager()
        self.bus_adapter = bus_adapter or BusAdapter()
        self._event_log_path: Optional[str] = None
        self._subscription_cursors: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._handled_signal_keys: set[Tuple[str, str]] = set()
        self._rebuild_runtime_state()

    def set_event_log_path(self, path: Optional[str]) -> None:
        self._event_log_path = path

    def persist_event_log(self) -> None:
        if self._event_log_path:
            self.event_log.to_jsonl(self._event_log_path)

    def spawn_subagent(
        self,
        *,
        owner_agent: str,
        agent_id: str,
        kind: str = "agent",
        async_mode: bool = False,
        payload: Optional[Dict[str, Any]] = None,
        task_descriptor: Optional[Dict[str, Any]] = None,
    ) -> SpawnResult:
        job = self.job_manager.create_job(
            agent_id=agent_id,
            owner_agent=owner_agent,
            kind=kind,
            task_descriptor=task_descriptor,
        )
        ack_payload = dict(payload or {})
        ack_payload.setdefault("job_id", job.job_id)
        ack_payload.setdefault("agent_id", job.agent_id)
        ack_payload.setdefault("owner_agent", job.owner_agent)
        ack_payload.setdefault("kind", job.kind)
        ack_payload.setdefault("seq", job.seq)
        if task_descriptor:
            ack_payload.setdefault("task_descriptor", dict(task_descriptor))

        self.event_log.add(
            "agent.spawned",
            agent_id=agent_id,
            parent_agent_id=owner_agent,
            payload=ack_payload,
            mvi_payload=self.bus_adapter.format_spawn_ack(ack_payload),
        )

        if async_mode:
            self.event_log.add(
                "agent.job_accepted",
                agent_id=agent_id,
                parent_agent_id=owner_agent,
                payload={"job_id": job.job_id, "state": job.state},
            )

        return SpawnResult(job=job, ack_payload=ack_payload)

    def mark_job_completed(self, job_id: str, *, result_payload: Optional[Dict[str, Any]] = None) -> Optional[JobRef]:
        job = self.job_manager.update_state(job_id, "completed")
        if job is None:
            return None
        payload = dict(result_payload or {})
        payload.setdefault("job_id", job.job_id)
        payload.setdefault("agent_id", job.agent_id)
        payload.setdefault("state", job.state)
        payload.setdefault("seq", job.seq)
        self.event_log.add(
            "agent.job_completed",
            agent_id=job.agent_id,
            parent_agent_id=job.owner_agent,
            payload=payload,
        )
        return job

    def emit_coordination_signal(self, job: JobRef, signal: Dict[str, Any]) -> Event:
        payload = dict(signal or {})
        payload.setdefault("task_id", self._task_id_for_job(job))
        payload.setdefault("parent_task_id", self._parent_task_id_for_job(job))
        event = self.event_log.add(
            "coordination.signal",
            agent_id=job.agent_id,
            parent_agent_id=job.owner_agent,
            payload=payload,
        )
        if str(payload.get("status") or "") == "accepted":
            self._process_accepted_signal(job, event)
        return event

    def emit_wakeup(
        self,
        job: JobRef,
        *,
        reason: str,
        message: str,
        subscription_id: Optional[str] = None,
        trigger_signal_id: Optional[str] = None,
        trigger_code: Optional[str] = None,
        resume_reason: Optional[str] = None,
        cursor_event_id: Optional[int] = None,
        source_task_id: Optional[str] = None,
        directive_id: Optional[str] = None,
        directive_code: Optional[str] = None,
        based_on_verdict_id: Optional[str] = None,
        causal_parent_event_id: Optional[int] = None,
    ) -> None:
        payload = {
            "job_id": job.job_id,
            "agent_id": job.agent_id,
            "owner_agent": job.owner_agent,
            "seq": job.seq,
            "reason": reason,
            "message": message,
        }
        if subscription_id:
            payload["subscription_id"] = subscription_id
        if trigger_signal_id:
            payload["trigger_signal_id"] = trigger_signal_id
        if trigger_code:
            payload["trigger_code"] = trigger_code
        if resume_reason:
            payload["resume_reason"] = resume_reason
        if cursor_event_id is not None:
            payload["cursor_event_id"] = int(cursor_event_id)
        if source_task_id:
            payload["source_task_id"] = source_task_id
        if directive_id:
            payload["directive_id"] = directive_id
        if directive_code:
            payload["directive_code"] = directive_code
        if based_on_verdict_id:
            payload["based_on_verdict_id"] = based_on_verdict_id
        self.event_log.add(
            "agent.wakeup_emitted",
            agent_id=job.agent_id,
            parent_agent_id=job.owner_agent,
            payload=payload,
            causal_parent_event_id=causal_parent_event_id,
            mvi_payload=self.bus_adapter.format_wakeup(payload),
        )

    def coordination_inspection_snapshot(self) -> Dict[str, Any]:
        """Return a read-only coordination snapshot derived from durable event-log truth."""
        return build_coordination_inspection_snapshot(
            signals=(
                dict(event.payload or {})
                for event in self.event_log.events
                if event.type == "coordination.signal"
            ),
            review_verdicts=(
                dict(event.payload or {})
                for event in self.event_log.events
                if event.type == "coordination.review_verdict"
            ),
            directives=(
                dict(event.payload or {})
                for event in self.event_log.events
                if event.type == "coordination.directive"
            ),
        )

    def subscription_cursors(self, job_id: str) -> Dict[str, Dict[str, Any]]:
        return {
            key: dict(value)
            for key, value in (self._subscription_cursors.get(job_id) or {}).items()
        }

    def latest_subscription_cursor(
        self,
        job_id: str,
        *,
        subscription_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cursors = self._subscription_cursors.get(job_id) or {}
        if subscription_id:
            cursor = cursors.get(subscription_id)
            return dict(cursor) if isinstance(cursor, dict) else None
        if not cursors:
            return None
        ordered = sorted(
            cursors.values(),
            key=lambda item: int((item or {}).get("last_seen_event_id") or 0),
            reverse=True,
        )
        cursor = ordered[0] if ordered else None
        return dict(cursor) if isinstance(cursor, dict) else None

    def supervisor_review_signal(
        self,
        supervisor_job: JobRef,
        *,
        subscription_id: Optional[str] = None,
        required_deliverable_refs: Optional[Iterable[str]] = None,
        allowed_blocked_actions: Optional[Iterable[str]] = None,
    ) -> Event:
        cursor = self.latest_subscription_cursor(supervisor_job.job_id, subscription_id=subscription_id)
        if cursor is None:
            raise ValueError(f"no subscription cursor found for job {supervisor_job.job_id}")

        trigger_signal_id = str(cursor.get("trigger_signal_id") or "")
        trigger_event_id = int(cursor.get("last_seen_event_id") or 0)
        signal_event = self._signal_event_by_id(trigger_signal_id)
        if signal_event is None:
            raise ValueError(f"missing signal event for trigger_signal_id={trigger_signal_id}")

        existing = self._existing_review_verdict(supervisor_job.job_id, trigger_signal_id)
        if existing is not None:
            return existing

        signal = dict(signal_event.payload or {})
        validation = signal.get("validation") if isinstance(signal.get("validation"), dict) else {}
        if not bool(validation.get("accepted")):
            raise ValueError(f"cannot review non-accepted signal {trigger_signal_id}")

        payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
        required_refs = [str(item) for item in (required_deliverable_refs or []) if str(item).strip()]
        deliverable_refs = [str(item) for item in (payload.get("deliverable_refs") or []) if str(item).strip()]
        done_policy = self.team_config.coordination.done
        review_policy = self.team_config.coordination.review
        reviewer_role = str(self.team_config.coordination.mission_owner_role or "supervisor").strip()
        allowed_reviewer_roles = {
            str(item).strip()
            for item in (review_policy.allowed_reviewer_roles or [])
            if str(item).strip()
        }
        if reviewer_role not in allowed_reviewer_roles:
            raise ValueError(f"mission_owner_role {reviewer_role!r} is not allowed to issue review verdicts")
        code = str(signal.get("code") or "")
        aggregate_validation = self._validate_aggregate_result_contract(payload)
        verification_validation = self._validate_verification_result_contract(
            payload,
            signal_code=code,
        )
        if done_policy.require_deliverable_refs and not required_refs and not deliverable_refs:
            required_refs = ["deliverable_ref_required"]
        missing_deliverable_refs = [item for item in required_refs if item not in deliverable_refs]
        allowed_actions = {
            str(item).strip().lower()
            for item in (
                allowed_blocked_actions
                or review_policy.allowed_blocked_actions
                or ("retry", "checkpoint", "escalate", "human_required")
            )
            if str(item).strip()
        }

        recommended_next_action = str(payload.get("recommended_next_action") or "").strip().lower()
        blocking_reason = str(payload.get("blocking_reason") or "").strip()
        blocked_action: Optional[str] = None
        verdict_code = "noted"
        mission_completed = False

        if code == "complete":
            if (
                missing_deliverable_refs
                or aggregate_validation["invalid"]
                or verification_validation["invalid"]
            ):
                verdict_code = "pending_validation"
            else:
                verdict_code = "validated"
                mission_completed = True
        elif code == "blocked":
            blocked_action = recommended_next_action if recommended_next_action in allowed_actions else "escalate"
            verdict_code = blocked_action if blocked_action in allowed_actions else "escalate"
        elif code == "human_required":
            blocked_action = "human_required"
            verdict_code = "human_required"

        decision_payload = validate_review_verdict(
            build_review_verdict(
                reviewer_task_id=self._task_id_for_job(supervisor_job),
                reviewer_role=reviewer_role,
                subject_signal=signal,
                verdict_code=verdict_code,
                verdict_id=(
                    f"review_{self._task_id_for_job(supervisor_job)}_{trigger_signal_id}"
                    if trigger_signal_id
                    else None
                ),
                subject_event_id=signal_event.event_id,
                subscription_id=str(cursor.get("subscription_id") or ""),
                trigger_signal_id=trigger_signal_id,
                trigger_event_id=trigger_event_id,
                trigger_code=str(cursor.get("trigger_code") or code),
                mission_completed=mission_completed,
                required_deliverable_refs=required_refs,
                deliverable_refs=deliverable_refs,
                missing_deliverable_refs=missing_deliverable_refs,
                blocking_reason=blocking_reason or None,
                recommended_next_action=recommended_next_action or None,
                support_claim_ref=str(payload.get("support_claim_ref") or "") or None,
                signal_evidence_refs=[
                    str(item) for item in (signal.get("evidence_refs") or []) if str(item).strip()
                ],
                metadata={
                    "job_id": supervisor_job.job_id,
                    "blocked_action": blocked_action,
                    "aggregate_contract_required": aggregate_validation["required_contract"],
                    "aggregate_contract_received": aggregate_validation["received_contract"],
                    "aggregate_missing_worker_task_ids": list(aggregate_validation["missing_worker_task_ids"]),
                    "aggregate_artifact_refs": list(aggregate_validation["aggregate_artifact_refs"]),
                    "verification_contract_required": verification_validation["required_contract"],
                    "verification_contract_received": verification_validation["received_contract"],
                    "verification_status": verification_validation["verification_status"],
                    "verification_subject_signal_id": verification_validation["subject_signal_id"],
                    "verification_subject_signal_found": verification_validation["subject_signal_found"],
                    "verification_subject_signal_code": verification_validation["subject_signal_code"],
                    "verification_subject_task_id": verification_validation["subject_task_id"],
                    "verification_artifact_refs": list(verification_validation["verification_artifact_refs"]),
                    "allowed_host_actions": self._allowed_host_intervention_actions(
                        {
                            "support_claim_ref": str(payload.get("support_claim_ref") or "") or None,
                        }
                    ),
                    "require_supervisor_escalate": bool(
                        self.team_config.coordination.intervention.require_supervisor_escalate
                    ),
                    "review_role_profile": sorted(allowed_reviewer_roles),
                    "legacy_decision": self._legacy_supervisor_decision_name(
                        code=code,
                        verdict_code=verdict_code,
                    ),
                },
            )
        )
        event = self.event_log.add(
            "coordination.review_verdict",
            agent_id=supervisor_job.agent_id,
            parent_agent_id=supervisor_job.owner_agent,
            causal_parent_event_id=signal_event.event_id,
            payload=decision_payload,
        )
        if mission_completed:
            self.mark_job_completed(
                supervisor_job.job_id,
                result_payload={
                    "verdict_code": verdict_code,
                    "mission_completed": True,
                    "trigger_signal_id": trigger_signal_id,
                    "trigger_event_id": trigger_event_id,
                },
            )
        self._issue_directive_from_review_verdict(
            supervisor_job=supervisor_job,
            review_event=event,
            review_verdict=decision_payload,
        )
        return event

    def _task_id_for_job(self, job: JobRef) -> str:
        descriptor = job.task_descriptor or {}
        task_id = descriptor.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            return task_id
        return str(job.job_id)

    def _parent_task_id_for_job(self, job: JobRef) -> Optional[str]:
        descriptor = job.task_descriptor or {}
        parent_task_id = descriptor.get("parent_task_id")
        if isinstance(parent_task_id, str) and parent_task_id.strip():
            return parent_task_id
        return None

    def _wake_subscriptions_for_job(self, job: JobRef) -> list[Dict[str, Any]]:
        descriptor = job.task_descriptor or {}
        raw = descriptor.get("wake_subscriptions") if isinstance(descriptor, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[Dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and str(item.get("schema_version") or "") == "bb.wake_subscription.v1":
                out.append(dict(item))
        return out

    @staticmethod
    def _subscription_scope_matches(subscription: Dict[str, Any], signal: Dict[str, Any]) -> bool:
        from_task_ids = [str(item) for item in (subscription.get("from_task_ids") or []) if str(item).strip()]
        if not from_task_ids:
            return True
        signal_task_id = str(signal.get("task_id") or "")
        if signal_task_id in from_task_ids:
            return True
        if bool(subscription.get("include_descendants")):
            parent_task_id = str(signal.get("parent_task_id") or "")
            mission_task_id = str(signal.get("mission_task_id") or "")
            return parent_task_id in from_task_ids or mission_task_id in from_task_ids
        return False

    @staticmethod
    def _default_wakeup_message(signal: Dict[str, Any]) -> str:
        code = str(signal.get("code") or "").strip()
        task_id = str(signal.get("task_id") or "unknown-task")
        if code == "complete":
            return f"Task {task_id} signaled complete."
        if code == "blocked":
            blocking_reason = str((signal.get("payload") or {}).get("blocking_reason") or "").strip()
            if blocking_reason:
                return f"Task {task_id} blocked: {blocking_reason}"
            return f"Task {task_id} signaled blocked."
        if code == "human_required":
            return f"Task {task_id} requires human input."
        return f"Task {task_id} emitted signal {code}."

    def _signal_event_by_id(self, signal_id: str) -> Optional[Event]:
        signal_id_text = str(signal_id or "").strip()
        if not signal_id_text:
            return None
        for event in reversed(self.event_log.events):
            if event.type != "coordination.signal":
                continue
            if str((event.payload or {}).get("signal_id") or "") == signal_id_text:
                return event
        return None

    def _existing_review_verdict(self, job_id: str, trigger_signal_id: str) -> Optional[Event]:
        job_id_text = str(job_id or "").strip()
        signal_id_text = str(trigger_signal_id or "").strip()
        if not job_id_text or not signal_id_text:
            return None
        for event in reversed(self.event_log.events):
            if event.type != "coordination.review_verdict":
                continue
            payload = dict(event.payload or {})
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            subject = payload.get("subject") if isinstance(payload.get("subject"), dict) else {}
            if (
                str(metadata.get("job_id") or "") == job_id_text
                and str(subject.get("trigger_signal_id") or subject.get("signal_id") or "") == signal_id_text
            ):
                return event
        return None

    def _review_event_by_verdict_id(self, verdict_id: str) -> Optional[Event]:
        verdict_id_text = str(verdict_id or "").strip()
        if not verdict_id_text:
            return None
        for event in reversed(self.event_log.events):
            if event.type != "coordination.review_verdict":
                continue
            payload = dict(event.payload or {})
            if str(payload.get("verdict_id") or "") == verdict_id_text:
                return event
        return None

    def _directive_by_id(self, directive_id: str) -> Optional[Event]:
        directive_id_text = str(directive_id or "").strip()
        if not directive_id_text:
            return None
        for event in reversed(self.event_log.events):
            if event.type != "coordination.directive":
                continue
            payload = dict(event.payload or {})
            if str(payload.get("directive_id") or "") == directive_id_text:
                return event
        return None

    def _issue_directive_from_review_verdict(
        self,
        *,
        supervisor_job: JobRef,
        review_event: Event,
        review_verdict: Dict[str, Any],
    ) -> Optional[Event]:
        verdict = dict(review_verdict or {})
        verdict_code = str(verdict.get("verdict_code") or "")
        subject = verdict.get("subject") if isinstance(verdict.get("subject"), dict) else {}
        source_task_id = str(subject.get("source_task_id") or "")
        based_on_verdict_id = str(verdict.get("verdict_id") or "")
        if not based_on_verdict_id:
            return None

        directive_code: Optional[str] = None
        target_task_id: Optional[str] = None
        wake_target = False
        if verdict_code == "pending_validation":
            directive_code = "continue"
            target_task_id = source_task_id
            wake_target = True
        elif verdict_code in {"retry", "checkpoint"}:
            directive_code = verdict_code
            target_task_id = source_task_id
            wake_target = True
        elif verdict_code in {"escalate", "human_required"}:
            directive_code = "escalate"
            target_task_id = self._task_id_for_job(supervisor_job)
            wake_target = False
        elif verdict_code == "validated":
            return None

        if not directive_code or not target_task_id:
            return None

        directive_id = f"directive_{self._task_id_for_job(supervisor_job)}_{based_on_verdict_id}"
        existing = self._directive_by_id(directive_id)
        if existing is not None:
            return existing

        target_job = self._job_for_task_id(target_task_id)
        directive = validate_directive(
            build_directive(
                directive_code=directive_code,
                issuer_task_id=self._task_id_for_job(supervisor_job),
                issuer_role=str(self.team_config.coordination.mission_owner_role or "supervisor"),
                target_task_id=target_task_id,
                target_job_id=target_job.job_id if target_job is not None else None,
                based_on_verdict=verdict,
                directive_id=directive_id,
                payload={
                    "wake_target": wake_target,
                    "recommended_next_action": verdict.get("recommended_next_action"),
                    "blocking_reason": verdict.get("blocking_reason"),
                },
                evidence_refs=[str(item) for item in (verdict.get("signal_evidence_refs") or []) if str(item).strip()],
                metadata={
                    "review_event_id": int(review_event.event_id),
                    "trigger_signal_id": str(subject.get("trigger_signal_id") or subject.get("signal_id") or ""),
                },
            )
        )
        event = self.event_log.add(
            "coordination.directive",
            agent_id=supervisor_job.agent_id,
            parent_agent_id=supervisor_job.owner_agent,
            causal_parent_event_id=review_event.event_id,
            payload=directive,
        )
        if wake_target and target_job is not None:
            self.emit_wakeup(
                target_job,
                reason=f"directive:{directive_code}",
                message=self._default_directive_message(directive),
                source_task_id=self._task_id_for_job(supervisor_job),
                directive_id=str(directive.get("directive_id") or ""),
                directive_code=directive_code,
                based_on_verdict_id=based_on_verdict_id,
                causal_parent_event_id=event.event_id,
            )
        return event

    def issue_host_intervention_directive(
        self,
        *,
        based_on_verdict_id: str,
        directive_code: str,
        evidence_refs: Optional[Iterable[str]] = None,
        note: Optional[str] = None,
    ) -> Event:
        verdict_id_text = str(based_on_verdict_id or "").strip()
        if not verdict_id_text:
            raise ValueError("missing based_on_verdict_id")

        action = str(directive_code or "").strip().lower()
        evidence = [str(item) for item in (evidence_refs or []) if str(item).strip()]
        if self.team_config.coordination.intervention.require_evidence_refs and not evidence:
            raise ValueError("host intervention requires evidence_refs")

        review_event = self._review_event_by_verdict_id(verdict_id_text)
        if review_event is None:
            raise ValueError(f"missing review verdict {verdict_id_text}")
        verdict = dict(review_event.payload or {})
        if str(verdict.get("verdict_code") or "") != "human_required":
            raise ValueError("host intervention requires a human_required review verdict")
        reviewer_role = str(verdict.get("reviewer_role") or "").strip().lower()
        allowed_reviewer_roles = {
            str(item).strip().lower()
            for item in (self.team_config.coordination.review.allowed_reviewer_roles or [])
            if str(item).strip()
        }
        if reviewer_role not in allowed_reviewer_roles:
            raise ValueError(f"reviewer_role {reviewer_role!r} is not allowed for host intervention")
        if self.team_config.coordination.intervention.require_supervisor_escalate and not self._has_supervisor_escalate_for_verdict(
            verdict_id_text
        ):
            raise ValueError("host intervention requires a prior supervisor escalate directive")
        allowed_actions = self._allowed_host_intervention_actions(verdict)
        if action not in allowed_actions:
            raise ValueError(f"directive_code {action!r} not allowed for host intervention")

        metadata = verdict.get("metadata") if isinstance(verdict.get("metadata"), dict) else {}
        supervisor_job_id = str(metadata.get("job_id") or "")
        supervisor_job = self.job_manager.get(supervisor_job_id) if supervisor_job_id else None
        if supervisor_job is None:
            raise ValueError(f"missing supervisor job for review verdict {verdict_id_text}")

        subject = verdict.get("subject") if isinstance(verdict.get("subject"), dict) else {}
        target_task_id = str(subject.get("source_task_id") or "")
        if not target_task_id:
            raise ValueError(f"review verdict {verdict_id_text} is missing source_task_id")

        wake_target = action in {"continue", "checkpoint"}
        target_job = self._job_for_task_id(target_task_id)
        if wake_target and target_job is None:
            raise ValueError(f"missing target job for task {target_task_id}")

        supervisor_task_id = self._task_id_for_job(supervisor_job)
        directive_id = f"directive_host_{supervisor_task_id}_{action}_{verdict_id_text}"
        existing = self._directive_by_id(directive_id)
        if existing is not None:
            return existing

        directive = validate_directive(
            build_directive(
                directive_code=action,
                issuer_task_id=f"host::{supervisor_task_id}",
                issuer_role="host",
                target_task_id=target_task_id,
                target_job_id=target_job.job_id if target_job is not None else None,
                based_on_verdict=verdict,
                directive_id=directive_id,
                payload={
                    "wake_target": wake_target,
                    "note": str(note or "").strip() or None,
                    "intervention_response": True,
                },
                evidence_refs=evidence,
                metadata={
                    "review_event_id": int(review_event.event_id),
                    "trigger_signal_id": str(subject.get("trigger_signal_id") or subject.get("signal_id") or ""),
                    "coordination_origin": "host.intervention",
                    "response_to_human_required": True,
                },
            )
        )
        event = self.event_log.add(
            "coordination.directive",
            agent_id=supervisor_job.agent_id,
            parent_agent_id=supervisor_job.owner_agent,
            causal_parent_event_id=review_event.event_id,
            payload=directive,
        )
        if wake_target and target_job is not None:
            self.emit_wakeup(
                target_job,
                reason=f"directive:{action}",
                message=self._default_directive_message(directive),
                source_task_id=f"host::{supervisor_task_id}",
                directive_id=str(directive.get("directive_id") or ""),
                directive_code=action,
                based_on_verdict_id=verdict_id_text,
                causal_parent_event_id=event.event_id,
            )
        return event

    def _allowed_host_intervention_actions(self, verdict: Dict[str, Any]) -> list[str]:
        intervention_policy = self.team_config.coordination.intervention
        allowed_actions = [
            str(item).strip().lower()
            for item in (intervention_policy.host_allowed_actions or [])
            if str(item).strip()
        ]
        support_claim_ref = str(verdict.get("support_claim_ref") or "").strip()
        if not support_claim_ref:
            return allowed_actions
        limited_actions = [
            str(item).strip().lower()
            for item in (intervention_policy.support_claim_limited_actions or [])
            if str(item).strip()
        ]
        intersection = [item for item in allowed_actions if item in set(limited_actions)]
        return intersection or limited_actions

    def _has_supervisor_escalate_for_verdict(self, verdict_id: str) -> bool:
        verdict_id_text = str(verdict_id or "").strip()
        if not verdict_id_text:
            return False
        for event in self.event_log.events:
            if event.type != "coordination.directive":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("based_on_verdict_id") or "").strip() != verdict_id_text:
                continue
            if str(payload.get("issuer_role") or "").strip() != "supervisor":
                continue
            if str(payload.get("directive_code") or "").strip() == "escalate":
                return True
        return False

    def _job_for_task_id(self, task_id: str) -> Optional[JobRef]:
        task_id_text = str(task_id or "").strip()
        if not task_id_text:
            return None
        for job in self.job_manager.all_jobs().values():
            if self._task_id_for_job(job) == task_id_text:
                return job
        return None

    def _validate_aggregate_result_contract(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        merge_policy = self.team_config.coordination.merge
        required_contract = str(merge_policy.reducer_result_contract or "").strip()
        aggregate_result = payload.get("aggregate_result") if isinstance(payload.get("aggregate_result"), dict) else {}
        received_contract = str(payload.get("aggregate_result_contract") or "").strip()
        required_worker_task_ids = [
            str(item) for item in (aggregate_result.get("required_worker_task_ids") or []) if str(item).strip()
        ]
        completed_worker_task_ids = [
            str(item) for item in (aggregate_result.get("completed_worker_task_ids") or []) if str(item).strip()
        ]
        aggregate_artifact_refs = [
            str(item) for item in (aggregate_result.get("aggregate_artifact_refs") or []) if str(item).strip()
        ]
        missing_worker_task_ids = [
            task_id for task_id in required_worker_task_ids if task_id not in completed_worker_task_ids
        ]
        applies = bool(required_contract)
        invalid = False
        if applies:
            invalid = (
                received_contract != required_contract
                or not aggregate_result
                or not aggregate_artifact_refs
                or bool(missing_worker_task_ids)
            )
        return {
            "applies": applies,
            "invalid": invalid,
            "required_contract": required_contract or None,
            "received_contract": received_contract or None,
            "missing_worker_task_ids": missing_worker_task_ids,
            "aggregate_artifact_refs": aggregate_artifact_refs,
        }

    def _validate_verification_result_contract(
        self,
        payload: Dict[str, Any],
        *,
        signal_code: str,
    ) -> Dict[str, Any]:
        review_policy = self.team_config.coordination.review
        required_contract = str(review_policy.verification_result_contract or "").strip()
        verification_result = (
            payload.get("verification_result") if isinstance(payload.get("verification_result"), dict) else {}
        )
        received_contract = str(payload.get("verification_result_contract") or "").strip()
        verification_status = str(verification_result.get("status") or "").strip().lower()
        subject_signal_id = str(verification_result.get("subject_signal_id") or "").strip()
        subject_task_id = str(verification_result.get("subject_task_id") or "").strip()
        verification_artifact_refs = [
            str(item) for item in (verification_result.get("verification_artifact_refs") or []) if str(item).strip()
        ]

        applies = bool(required_contract)
        invalid = False
        subject_signal_found = False
        subject_signal_code: Optional[str] = None
        if applies:
            subject_signal_event = self._signal_event_by_id(subject_signal_id)
            if subject_signal_event is not None:
                subject_signal_found = True
                subject_payload = dict(subject_signal_event.payload or {})
                subject_signal_code = str(subject_payload.get("code") or "").strip() or None
                subject_validation = (
                    subject_payload.get("validation") if isinstance(subject_payload.get("validation"), dict) else {}
                )
                if not subject_task_id:
                    subject_task_id = str(subject_payload.get("task_id") or "").strip()
                elif subject_task_id != str(subject_payload.get("task_id") or "").strip():
                    invalid = True
                if not bool(subject_validation.get("accepted")):
                    invalid = True
            invalid = invalid or (
                received_contract != required_contract
                or not verification_result
                or verification_status not in {"pass", "fail", "soft_fail"}
                or not subject_signal_id
                or not subject_signal_found
                or subject_signal_code != "complete"
                or not verification_artifact_refs
            )
            if signal_code == "complete" and verification_status != "pass":
                invalid = True
            if signal_code == "blocked" and verification_status not in {"fail", "soft_fail"}:
                invalid = True

        return {
            "applies": applies,
            "invalid": invalid,
            "required_contract": required_contract or None,
            "received_contract": received_contract or None,
            "verification_status": verification_status or None,
            "subject_signal_id": subject_signal_id or None,
            "subject_signal_found": subject_signal_found,
            "subject_signal_code": subject_signal_code,
            "subject_task_id": subject_task_id or None,
            "verification_artifact_refs": verification_artifact_refs,
        }

    @staticmethod
    def _default_directive_message(directive: Dict[str, Any]) -> str:
        code = str(directive.get("directive_code") or "").strip()
        task_id = str(directive.get("target_task_id") or "unknown-task")
        if code == "retry":
            return f"Retry requested for task {task_id}."
        if code == "checkpoint":
            return f"Checkpoint requested for task {task_id}."
        if code == "continue":
            return f"Continue requested for task {task_id}."
        if code == "escalate":
            return f"Escalation requested for task {task_id}."
        if code == "terminate":
            return f"Terminate requested for task {task_id}."
        return f"Directive {code} issued for task {task_id}."

    @staticmethod
    def _legacy_supervisor_decision_name(*, code: str, verdict_code: str) -> str:
        signal_code = str(code or "").strip()
        review_code = str(verdict_code or "").strip()
        if signal_code == "complete" and review_code == "validated":
            return "mission_complete_validated"
        if signal_code == "complete" and review_code == "pending_validation":
            return "worker_complete_pending_validation"
        if signal_code == "blocked" and review_code == "retry":
            return "blocked_retry_requested"
        if signal_code == "blocked" and review_code == "checkpoint":
            return "blocked_checkpoint_requested"
        if signal_code == "blocked" and review_code == "escalate":
            return "blocked_escalated"
        if review_code == "human_required":
            return "human_review_required"
        return "signal_reviewed"

    def _handled_signal_key(self, job_id: str, subscription_id: str, signal_id: str) -> Tuple[str, str]:
        return (f"{job_id}:{subscription_id}", signal_id)

    def _process_accepted_signal(self, source_job: JobRef, signal_event: Event) -> None:
        signal = dict(signal_event.payload or {})
        signal_id = str(signal.get("signal_id") or "")
        code = str(signal.get("code") or "")
        if not signal_id or not code:
            return
        for job in self.job_manager.all_jobs().values():
            for subscription in self._wake_subscriptions_for_job(job):
                subscription_id = str(subscription.get("subscription_id") or "")
                if not subscription_id:
                    continue
                on_codes = [str(item) for item in (subscription.get("on_codes") or []) if str(item).strip()]
                if code not in on_codes:
                    continue
                if not self._subscription_scope_matches(subscription, signal):
                    continue
                dedupe_key = self._handled_signal_key(job.job_id, subscription_id, signal_id)
                if dedupe_key in self._handled_signal_keys:
                    continue
                self._handled_signal_keys.add(dedupe_key)
                cursor_payload = {
                    "subscription_id": subscription_id,
                    "last_seen_event_id": int(signal_event.event_id),
                    "trigger_signal_id": signal_id,
                    "trigger_code": code,
                    "resume_reason": f"signal:{code}",
                    "source_task_id": str(signal.get("task_id") or ""),
                }
                self._subscription_cursors.setdefault(job.job_id, {})[subscription_id] = cursor_payload
                self.emit_wakeup(
                    job,
                    reason=f"signal:{code}",
                    message=self._default_wakeup_message(signal),
                    subscription_id=subscription_id,
                    trigger_signal_id=signal_id,
                    trigger_code=code,
                    resume_reason=f"signal:{code}",
                    cursor_event_id=int(signal_event.event_id),
                    source_task_id=str(signal.get("task_id") or ""),
                    causal_parent_event_id=int(signal_event.event_id),
                )

    def _rebuild_runtime_state(self) -> None:
        events = self.event_log.events
        if not events:
            return
        self._subscription_cursors = {}
        self._handled_signal_keys = set()
        self.job_manager = self.job_manager or JobManager()
        for event in events:
            payload = dict(event.payload or {})
            if event.type == "agent.spawned":
                job_id = str(payload.get("job_id") or "")
                agent_id = str(payload.get("agent_id") or event.agent_id or "")
                owner_agent = str(payload.get("owner_agent") or event.parent_agent_id or "")
                kind = str(payload.get("kind") or "agent")
                seq = int(payload.get("seq") or 0)
                task_descriptor = payload.get("task_descriptor")
                self.job_manager.restore_job(
                    JobRef(
                        job_id=job_id,
                        agent_id=agent_id,
                        owner_agent=owner_agent,
                        kind=kind,
                        seq=seq,
                        task_descriptor=dict(task_descriptor) if isinstance(task_descriptor, dict) else None,
                    )
                )
            elif event.type == "agent.job_accepted":
                job_id = str(payload.get("job_id") or "")
                if job_id:
                    self.job_manager.update_state(job_id, str(payload.get("state") or "accepted"))
            elif event.type == "agent.job_completed":
                job_id = str(payload.get("job_id") or "")
                if job_id:
                    self.job_manager.update_state(job_id, str(payload.get("state") or "completed"))
            elif event.type == "agent.wakeup_emitted":
                job_id = str(payload.get("job_id") or "")
                subscription_id = str(payload.get("subscription_id") or "")
                trigger_signal_id = str(payload.get("trigger_signal_id") or "")
                if job_id and subscription_id and trigger_signal_id:
                    self._handled_signal_keys.add(self._handled_signal_key(job_id, subscription_id, trigger_signal_id))
                    self._subscription_cursors.setdefault(job_id, {})[subscription_id] = {
                        "subscription_id": subscription_id,
                        "last_seen_event_id": int(payload.get("cursor_event_id") or 0),
                        "trigger_signal_id": trigger_signal_id,
                        "trigger_code": str(payload.get("trigger_code") or ""),
                        "resume_reason": str(payload.get("resume_reason") or ""),
                        "source_task_id": str(payload.get("source_task_id") or ""),
                    }
