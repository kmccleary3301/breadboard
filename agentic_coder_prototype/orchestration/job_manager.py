"""Async job tracking for multi-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
import uuid


@dataclass
class JobRef:
    job_id: str
    agent_id: str
    owner_agent: str
    kind: str
    state: str = "accepted"  # accepted|running|completed|failed|killed


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobRef] = {}

    def create_job(self, *, agent_id: str, owner_agent: str, kind: str) -> JobRef:
        job_id = uuid.uuid4().hex[:12]
        job = JobRef(job_id=job_id, agent_id=agent_id, owner_agent=owner_agent, kind=kind)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[JobRef]:
        return self._jobs.get(job_id)

    def update_state(self, job_id: str, state: str) -> Optional[JobRef]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.state = state
        return job

    def all_jobs(self) -> Dict[str, JobRef]:
        return dict(self._jobs)
