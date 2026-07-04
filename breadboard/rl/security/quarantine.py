from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.env_package.schema import HardeningPolicy
from breadboard.rl.security.hardening import HardeningFinding


@dataclass(frozen=True)
class QuarantineDecision:
    row_id: str
    quarantined: bool
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "quarantined": self.quarantined,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


def quarantine_on_findings(
    *,
    row_id: str,
    findings: list[HardeningFinding],
    policy: HardeningPolicy,
) -> QuarantineDecision:
    policy_triggers = set(policy.quarantine_on_findings)
    reasons: list[str] = []
    for finding in findings:
        if finding.finding_id in policy_triggers or finding.severity in {"high", "critical"}:
            reasons.append(finding.finding_id)
    return QuarantineDecision(
        row_id=row_id,
        quarantined=bool(reasons),
        reasons=list(dict.fromkeys(reasons)),
        metadata={"policy_id": policy.policy_id},
    )
