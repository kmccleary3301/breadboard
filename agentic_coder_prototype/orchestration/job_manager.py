"""Async job tracking for multi-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
import threading
import uuid


@dataclass
class JobRef:
    job_id: str
    agent_id: str
    owner_agent: str
    kind: str
    state: str = "accepted"  # accepted|running|completed|failed|killed
    seq: int = 0


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobRef] = {}
        self._lock = threading.Lock()
        self._next_seq = 1

    def create_job(self, *, agent_id: str, owner_agent: str, kind: str) -> JobRef:
        with self._lock:
            job_id = uuid.uuid4().hex[:12]
            seq = self._next_seq
            self._next_seq += 1
            job = JobRef(job_id=job_id, agent_id=agent_id, owner_agent=owner_agent, kind=kind, seq=seq)
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> Optional[JobRef]:
        with self._lock:
            return self._jobs.get(job_id)

    def update_state(self, job_id: str, state: str) -> Optional[JobRef]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.state = state
            return job

    def all_jobs(self) -> Dict[str, JobRef]:
        with self._lock:
            return dict(self._jobs)
