"""Correlation-only placement records; Work Item events own lifecycle state."""
from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class WorkPlacement:
    placement_id: str; work_item_id: str; attempt_id: str; worker_id: str; session_ref: str; execution_target_ref: str; attached_at: str
    def __post_init__(self) -> None:
        for name in ("placement_id", "work_item_id", "attempt_id", "worker_id", "session_ref", "execution_target_ref", "attached_at"):
            if type(value := getattr(self, name)) is not str or not value.strip(): raise ValueError(f"{name} must be a non-empty string")
    def as_dict(self) -> dict[str, str]:
        return {"placement_id": self.placement_id, "work_item_id": self.work_item_id, "attempt_id": self.attempt_id, "worker_id": self.worker_id, "session_ref": self.session_ref, "execution_target_ref": self.execution_target_ref, "attached_at": self.attached_at}
