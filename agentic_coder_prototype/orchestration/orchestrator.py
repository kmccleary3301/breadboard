"""Multi-agent orchestrator scaffold (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

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
        self.event_log.add(
            "agent.wakeup_emitted",
            agent_id=job.agent_id,
            parent_agent_id=job.owner_agent,
            payload=payload,
            causal_parent_event_id=causal_parent_event_id,
            mvi_payload=self.bus_adapter.format_wakeup(payload),
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

        existing = self._existing_supervisor_decision(supervisor_job.job_id, trigger_signal_id)
        if existing is not None:
            return existing

        signal = dict(signal_event.payload or {})
        validation = signal.get("validation") if isinstance(signal.get("validation"), dict) else {}
        if not bool(validation.get("accepted")):
            raise ValueError(f"cannot review non-accepted signal {trigger_signal_id}")

        payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
        required_refs = [str(item) for item in (required_deliverable_refs or []) if str(item).strip()]
        deliverable_refs = [str(item) for item in (payload.get("deliverable_refs") or []) if str(item).strip()]
        missing_deliverable_refs = [item for item in required_refs if item not in deliverable_refs]
        allowed_actions = {
            str(item).strip().lower()
            for item in (allowed_blocked_actions or ("retry", "checkpoint", "escalate", "human_required"))
            if str(item).strip()
        }

        code = str(signal.get("code") or "")
        recommended_next_action = str(payload.get("recommended_next_action") or "").strip().lower()
        blocking_reason = str(payload.get("blocking_reason") or "").strip()
        blocked_action: Optional[str] = None
        decision = "signal_reviewed"
        mission_completed = False

        if code == "complete":
            if missing_deliverable_refs:
                decision = "worker_complete_pending_validation"
            else:
                decision = "mission_complete_validated"
                mission_completed = True
        elif code == "blocked":
            blocked_action = recommended_next_action if recommended_next_action in allowed_actions else "escalate"
            decision_map = {
                "retry": "blocked_retry_requested",
                "checkpoint": "blocked_checkpoint_requested",
                "escalate": "blocked_escalated",
                "human_required": "human_review_required",
            }
            decision = decision_map.get(blocked_action, "blocked_escalated")
        elif code == "human_required":
            blocked_action = "human_required"
            decision = "human_review_required"

        decision_payload = {
            "job_id": supervisor_job.job_id,
            "supervisor_task_id": self._task_id_for_job(supervisor_job),
            "subscription_id": str(cursor.get("subscription_id") or ""),
            "trigger_signal_id": trigger_signal_id,
            "trigger_event_id": trigger_event_id,
            "trigger_code": str(cursor.get("trigger_code") or code),
            "source_task_id": str(cursor.get("source_task_id") or signal.get("task_id") or ""),
            "decision": decision,
            "mission_completed": mission_completed,
            "required_deliverable_refs": required_refs,
            "deliverable_refs": deliverable_refs,
            "missing_deliverable_refs": missing_deliverable_refs,
            "blocking_reason": blocking_reason or None,
            "recommended_next_action": recommended_next_action or None,
            "blocked_action": blocked_action,
            "support_claim_ref": str(payload.get("support_claim_ref") or "") or None,
            "signal_evidence_refs": [str(item) for item in (signal.get("evidence_refs") or []) if str(item).strip()],
        }
        event = self.event_log.add(
            "coordination.supervisor_decision",
            agent_id=supervisor_job.agent_id,
            parent_agent_id=supervisor_job.owner_agent,
            causal_parent_event_id=signal_event.event_id,
            payload=decision_payload,
        )
        if mission_completed:
            self.mark_job_completed(
                supervisor_job.job_id,
                result_payload={
                    "decision": decision,
                    "mission_completed": True,
                    "trigger_signal_id": trigger_signal_id,
                    "trigger_event_id": trigger_event_id,
                },
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

    def _existing_supervisor_decision(self, job_id: str, trigger_signal_id: str) -> Optional[Event]:
        job_id_text = str(job_id or "").strip()
        signal_id_text = str(trigger_signal_id or "").strip()
        if not job_id_text or not signal_id_text:
            return None
        for event in reversed(self.event_log.events):
            if event.type != "coordination.supervisor_decision":
                continue
            payload = dict(event.payload or {})
            if (
                str(payload.get("job_id") or "") == job_id_text
                and str(payload.get("trigger_signal_id") or "") == signal_id_text
            ):
                return event
        return None

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
