from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.replay.parity import ReplayParityReport


@dataclass(frozen=True)
class AdmissionDecision:
    exportable: bool
    trainable: bool
    replay_status: str
    hardening_status: str
    quarantine_status: str
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exportable": self.exportable,
            "trainable": self.trainable,
            "replay_status": self.replay_status,
            "hardening_status": self.hardening_status,
            "quarantine_status": self.quarantine_status,
            "blocked_reasons": list(self.blocked_reasons),
        }


def decide_export_admission(
    *,
    replay_report: ReplayParityReport,
    hardening_status: str = "not_applicable",
    quarantine_status: str = "clear",
    token_records_valid: bool = True,
) -> AdmissionDecision:
    blocked: list[str] = []
    if not replay_report.passed:
        blocked.append("replay_mismatch")
    if hardening_status not in {"passed", "not_applicable"}:
        blocked.append(f"hardening_status={hardening_status}")
    if quarantine_status != "clear":
        blocked.append(f"quarantine_status={quarantine_status}")
    if not token_records_valid:
        blocked.append("token_records_invalid")
    return AdmissionDecision(
        exportable=not blocked,
        trainable=False,
        replay_status="passed" if replay_report.passed else "failed",
        hardening_status=hardening_status,
        quarantine_status=quarantine_status,
        blocked_reasons=blocked,
    )
