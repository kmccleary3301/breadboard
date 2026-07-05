from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


READY = "READY"
ASSIGNED = "ASSIGNED"
QUARANTINED = "QUARANTINED"


@dataclass
class WorkerRecord:
    worker_id: str
    signature_digest: str
    state: str = READY
    assigned_count: int = 0
    quarantine_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "signature_digest": self.signature_digest,
            "state": self.state,
            "assigned_count": self.assigned_count,
            "quarantine_reason": self.quarantine_reason,
            "metadata": dict(self.metadata),
        }


class RuntimePool:
    def __init__(self) -> None:
        self.workers: dict[str, WorkerRecord] = {}

    def register(self, worker: WorkerRecord) -> None:
        self.workers[worker.worker_id] = worker

    def route(self, signature_digest: str) -> WorkerRecord | None:
        for worker in self.workers.values():
            if worker.signature_digest == signature_digest and worker.state == READY:
                worker.state = ASSIGNED
                worker.assigned_count += 1
                return worker
        return None

    def release(self, worker_id: str) -> None:
        worker = self.workers[worker_id]
        if worker.state != QUARANTINED:
            worker.state = READY

    def quarantine(self, worker_id: str, reason: str) -> None:
        worker = self.workers[worker_id]
        worker.state = QUARANTINED
        worker.quarantine_reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {"workers": [worker.to_dict() for worker in self.workers.values()]}
