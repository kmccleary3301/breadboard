"""Multi-agent orchestrator scaffold (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .schema import TeamConfig
from .event_log import EventLog
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
    ) -> SpawnResult:
        job = self.job_manager.create_job(agent_id=agent_id, owner_agent=owner_agent, kind=kind)
        ack_payload = dict(payload or {})
        ack_payload.setdefault("job_id", job.job_id)
        ack_payload.setdefault("agent_id", job.agent_id)
        ack_payload.setdefault("owner_agent", job.owner_agent)
        ack_payload.setdefault("seq", job.seq)

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

    def emit_wakeup(self, job: JobRef, *, reason: str, message: str) -> None:
        payload = {
            "job_id": job.job_id,
            "agent_id": job.agent_id,
            "owner_agent": job.owner_agent,
            "seq": job.seq,
            "reason": reason,
            "message": message,
        }
        self.event_log.add(
            "agent.wakeup_emitted",
            agent_id=job.agent_id,
            parent_agent_id=job.owner_agent,
            payload=payload,
            mvi_payload=self.bus_adapter.format_wakeup(payload),
        )
