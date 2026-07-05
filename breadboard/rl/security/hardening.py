from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from breadboard.rl.env_package.schema import HardeningPolicy


@dataclass(frozen=True)
class HardeningFinding:
    finding_id: str
    severity: str
    path: str
    message: str
    probe_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }
        if self.probe_id:
            payload["probe_id"] = self.probe_id
        return payload


@dataclass(frozen=True)
class HardeningReport:
    report_id: str
    status: str
    findings: list[HardeningFinding] = field(default_factory=list)
    clean_baseline_passed: bool = True
    reference_solution_passed: bool = True
    process_cleanup_observed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "status": self.status,
            "findings": [item.to_dict() for item in self.findings],
            "clean_baseline_passed": self.clean_baseline_passed,
            "reference_solution_passed": self.reference_solution_passed,
            "process_cleanup_observed": self.process_cleanup_observed,
            "metadata": dict(self.metadata),
        }


def scan_python_import_hooks(workspace: Path) -> list[HardeningFinding]:
    workspace = workspace.resolve()
    findings: list[HardeningFinding] = []
    suspicious_names = {"sitecustomize.py", "usercustomize.py"}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        if path.name in suspicious_names:
            findings.append(
                HardeningFinding(
                    finding_id=path.stem,
                    severity="high",
                    path=rel,
                    message=f"Python import hook file detected: {path.name}",
                )
            )
        if path.suffix == ".pth":
            findings.append(
                HardeningFinding(
                    finding_id="pth_injection",
                    severity="high",
                    path=rel,
                    message=".pth import path injection detected",
                )
            )
        if path.name == "conftest.py" and not rel.startswith("tests/"):
            findings.append(
                HardeningFinding(
                    finding_id="conftest_outside_tests",
                    severity="medium",
                    path=rel,
                    message="pytest conftest.py outside tests/ detected",
                )
            )
    return findings


def scan_symlink_escapes(workspace: Path) -> list[HardeningFinding]:
    workspace = workspace.resolve()
    findings: list[HardeningFinding] = []
    for path in workspace.rglob("*"):
        if not path.is_symlink():
            continue
        rel = path.relative_to(workspace).as_posix()
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path.absolute()
        if workspace not in resolved.parents and resolved != workspace:
            findings.append(
                HardeningFinding(
                    finding_id="symlink_escape",
                    severity="high",
                    path=rel,
                    message=f"symlink escapes workspace: {resolved}",
                )
            )
    return findings


def validate_process_cleanup_before_verify(events: Iterable[str]) -> list[str]:
    event_list = list(events)
    if "process_cleanup" not in event_list:
        return ["process_cleanup event is required before verify"]
    if "verify" in event_list and event_list.index("process_cleanup") > event_list.index("verify"):
        return ["process_cleanup must occur before verify"]
    return []


def build_hardening_report(
    *,
    report_id: str,
    workspace: Path,
    policy: HardeningPolicy,
    clean_baseline_passed: bool,
    reference_solution_passed: bool,
    process_cleanup_observed: bool,
    extra_findings: list[HardeningFinding] | None = None,
) -> HardeningReport:
    findings = [
        *scan_python_import_hooks(workspace),
        *scan_symlink_escapes(workspace),
        *list(extra_findings or []),
    ]
    status = "passed"
    if not clean_baseline_passed or not reference_solution_passed:
        status = "failed"
    elif findings:
        status = "quarantined"
    elif policy.verifier_isolated_required and not process_cleanup_observed:
        status = "quarantined"
        findings.append(
            HardeningFinding(
                finding_id="missing_process_cleanup",
                severity="medium",
                path=".",
                message="process cleanup was not observed before verifier",
            )
        )
    return HardeningReport(
        report_id=report_id,
        status=status,
        findings=findings,
        clean_baseline_passed=clean_baseline_passed,
        reference_solution_passed=reference_solution_passed,
        process_cleanup_observed=process_cleanup_observed,
        metadata={"policy_id": policy.policy_id},
    )
