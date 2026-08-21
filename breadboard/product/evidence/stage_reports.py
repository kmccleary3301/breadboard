from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
def _valid_timestamp(value: Any) -> bool:
    try: return value is None or isinstance(value, str) and len(value) >= 20 and value[10] == "T" and value[13] == ":" and value[16] == ":" and "," not in value and (value.endswith("Z") or len(value) >= 6 and value[-6] in "+-" and value[-3] == ":") and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except (TypeError, ValueError): return False
STAGES = ("validate", "lock", "capture", "normalize", "replay", "compare", "claim", "reverify")
_TERMINAL = frozenset(("completed", "failed"))
class StageStateError(ValueError): pass
@dataclass(frozen=True)
class StageReport:
    stage_report_id: str
    lane_execution_id: str
    stage: str
    status: str = "pending"
    input_artifact_ids: tuple[str, ...] = ()
    output_artifact_ids: tuple[str, ...] = ()
    started_at_utc: str | None = None
    completed_at_utc: str | None = None
    evidence_sha256: str | None = None
    problem: Mapping[str, Any] | None = None
    def __post_init__(self) -> None:
        ids = (self.stage_report_id, self.lane_execution_id)
        if any(not isinstance(value, str) or not value or not value[0].isalnum() or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in value) for value in ids): raise StageStateError("stage_report_id and lane_execution_id must be portable identifiers")
        if self.stage not in STAGES: raise StageStateError(f"unknown lane stage: {self.stage!r}")
        if self.status not in ("pending", "running", "completed", "failed", "skipped"): raise StageStateError(f"invalid stage status: {self.status!r}")
        if any(not isinstance(rows, (list, tuple)) or any(not isinstance(value, str) or not value for value in rows) or len(rows) != len(set(rows)) for rows in (self.input_artifact_ids, self.output_artifact_ids)): raise StageStateError("artifact ids must be unique non-empty arrays")
        if self.evidence_sha256 is not None and (not isinstance(self.evidence_sha256, str) or len(self.evidence_sha256) != 71 or not self.evidence_sha256.startswith("sha256:") or any(char not in "0123456789abcdef" for char in self.evidence_sha256[7:])): raise StageStateError("evidence_sha256 must match bb.sha256")
        if not all(_valid_timestamp(value) for value in (self.started_at_utc, self.completed_at_utc)): raise StageStateError("stage timestamps must be RFC 3339 date-times")
        if self.status in ("pending", "skipped") and any(value is not None for value in (self.started_at_utc, self.completed_at_utc, self.evidence_sha256, self.problem)): raise StageStateError(f"{self.status} stage report must not carry terminal data")
        if self.status == "running" and (not self.started_at_utc or any(value is not None for value in (self.completed_at_utc, self.evidence_sha256, self.problem))): raise StageStateError("running stage report has invalid state data")
        if self.status == "completed" and (not self.evidence_sha256 or not self.started_at_utc or not self.completed_at_utc or self.problem is not None): raise StageStateError("completed stage report has invalid state data")
        if self.status == "failed" and (not isinstance(self.problem, Mapping) or not self.started_at_utc or not self.completed_at_utc): raise StageStateError("failed stage report has invalid state data")
        p = self.problem
        if p is not None and (not isinstance(p, Mapping) or set(p) != {"schema_version", "error_code", "message", "record_refs", "failed_stage", "hint", "next_actions"} or p.get("schema_version") != "bb.problem.v1" or not isinstance(p.get("error_code"), str) or not p["error_code"] or p["error_code"][0] not in "abcdefghijklmnopqrstuvwxyz" or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in p["error_code"]) or not isinstance(p.get("message"), str) or not p["message"] or not all(p.get(key) is None or isinstance(p[key], str) for key in ("failed_stage", "hint")) or any(not isinstance(p.get(key), list) or any(not isinstance(value, str) or not value for value in p[key]) for key in ("record_refs", "next_actions"))): raise StageStateError("problem does not match bb.problem.v1")
    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.stage_report.v1",
            "stage_report_id": self.stage_report_id,
            "lane_execution_id": self.lane_execution_id,
            "stage": self.stage,
            "status": self.status,
            "input_artifact_ids": list(self.input_artifact_ids),
            "output_artifact_ids": list(self.output_artifact_ids),
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "evidence_sha256": self.evidence_sha256,
            "problem": dict(self.problem) if self.problem is not None else None,
        }
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StageReport":
        if not isinstance(value, Mapping) or value.get("schema_version") != "bb.stage_report.v1":
            raise StageStateError("unsupported stage report schema_version")
        expected = {"schema_version", "stage_report_id", "lane_execution_id", "stage", "status", "input_artifact_ids", "output_artifact_ids", "started_at_utc", "completed_at_utc", "evidence_sha256", "problem"}
        if set(value) != expected or any(not isinstance(value.get(key), list) for key in ("input_artifact_ids", "output_artifact_ids")):
            raise StageStateError("stage report fields do not match bb.stage_report.v1")
        keys = ("stage_report_id", "lane_execution_id", "stage", "status", "started_at_utc", "completed_at_utc", "evidence_sha256", "problem")
        return cls(**{key: value.get(key) for key in keys}, input_artifact_ids=tuple(value.get("input_artifact_ids", ())), output_artifact_ids=tuple(value.get("output_artifact_ids", ())))
def pre_capture_failure(lane_execution_id: str, detail: str) -> dict[str, Any]:
    if not isinstance(lane_execution_id, str) or not lane_execution_id or not isinstance(detail, str) or not detail.strip():
        raise StageStateError("pre-capture failure identity and detail must be non-empty")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    problem = {"schema_version": "bb.problem.v1", "error_code": "lane.pre_capture_failed", "message": detail, "record_refs": [], "failed_stage": "capture", "hint": None, "next_actions": []}
    return StageReport(f"{lane_execution_id}:capture", lane_execution_id, "capture", "failed", started_at_utc=now, completed_at_utc=now, problem=problem).as_dict()
