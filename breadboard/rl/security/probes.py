from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from breadboard.rl.env_package.schema import HardeningPolicy
from breadboard.rl.security.hardening import HardeningFinding, build_hardening_report
from breadboard.rl.security.quarantine import quarantine_on_findings


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    status: str
    findings: list[HardeningFinding] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "status": self.status,
            "findings": [item.to_dict() for item in self.findings],
            "evidence": dict(self.evidence),
        }


ProbeMaterializer = Callable[[Path], list[HardeningFinding]]


def run_probe_suite(
    *,
    workspace: Path,
    policy: HardeningPolicy,
    probes: dict[str, ProbeMaterializer],
) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for probe_id, materializer in probes.items():
        probe_workspace = workspace / probe_id
        probe_workspace.mkdir(parents=True, exist_ok=True)
        extra_findings = materializer(probe_workspace)
        report = build_hardening_report(
            report_id=f"{probe_id}.hardening",
            workspace=probe_workspace,
            policy=policy,
            clean_baseline_passed=True,
            reference_solution_passed=True,
            process_cleanup_observed=True,
            extra_findings=extra_findings,
        )
        quarantine = quarantine_on_findings(
            row_id=probe_id,
            findings=report.findings,
            policy=policy,
        )
        results.append(
            ProbeResult(
                probe_id=probe_id,
                status="quarantined" if quarantine.quarantined else report.status,
                findings=report.findings,
                evidence={
                    "hardening_status": report.status,
                    "quarantine": quarantine.to_dict(),
                },
            )
        )
    return results
