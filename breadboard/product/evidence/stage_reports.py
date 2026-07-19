"""Small state machine for lane stage reports and pre-capture failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


STAGES = ("capture", "normalize", "replay", "compare", "claim")
_TERMINAL = frozenset(("completed", "failed"))
_TRANSITIONS = {
    "pending": frozenset(("running", "failed")),
    "running": frozenset(("completed", "failed")),
}


class StageStateError(ValueError):
    """Raised when a lane stage is used out of order."""


@dataclass(frozen=True)
class StageReport:
    lane_id: str
    stage: str
    status: str = "pending"
    detail: str | None = None
    evidence_sha256: str | None = None
    started_at_utc: str | None = None
    completed_at_utc: str | None = None

    def __post_init__(self) -> None:
        if not self.lane_id or not isinstance(self.lane_id, str):
            raise StageStateError("stage report lane_id must be a non-empty string")
        if self.stage not in STAGES:
            raise StageStateError(f"unknown lane stage: {self.stage!r}")
        if self.status not in ("pending", "running", "completed", "failed"):
            raise StageStateError(f"invalid stage status: {self.status!r}")
        if self.status == "completed" and not self.evidence_sha256:
            raise StageStateError("completed stage reports require evidence_sha256")
        if self.status in _TERMINAL and not self.completed_at_utc:
            raise StageStateError("terminal stage reports require completed_at_utc")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.stage_report.v1",
            "lane_id": self.lane_id,
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "evidence_sha256": self.evidence_sha256,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
        }

    def transition(self, status: str, *, detail: str | None = None, evidence_sha256: str | None = None) -> "StageReport":
        if status not in _TRANSITIONS.get(self.status, frozenset()):
            raise StageStateError(f"cannot transition {self.stage} from {self.status} to {status}")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return StageReport(
            lane_id=self.lane_id,
            stage=self.stage,
            status=status,
            detail=detail,
            evidence_sha256=evidence_sha256,
            started_at_utc=self.started_at_utc or (now if status == "running" else None),
            completed_at_utc=now if status in _TERMINAL else None,
        )


def require_capture_ready(reports: Mapping[str, StageReport] | None = None) -> None:
    """Fail closed before capture when an earlier stage is not in a usable state."""
    if reports is None:
        return
    for stage in STAGES:
        if stage == "capture":
            break
        report = reports.get(stage)
        if report is not None and report.status != "completed":
            raise StageStateError(f"capture requires completed {stage} stage")


def pre_capture_failure(lane_id: str, detail: str) -> dict[str, Any]:
    """Return the stable error record used by CLI and stage-report readers."""
    if not isinstance(detail, str) or not detail.strip():
        raise StageStateError("pre-capture failure detail must be non-empty")
    return {
        "schema_version": "bb.stage_report.v1",
        "lane_id": lane_id,
        "stage": "capture",
        "status": "failed",
        "detail": detail,
        "evidence_sha256": None,
        "started_at_utc": None,
        "completed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
