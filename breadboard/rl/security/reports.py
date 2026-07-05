from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerifierRunReport:
    report_id: str
    verifier_id: str
    status: str
    output: str
    evidence_sha256: str
    rerun_agreement: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def verify_evidence_hash(self) -> bool:
        digest = "sha256:" + hashlib.sha256(self.output.encode("utf-8")).hexdigest()
        return digest == self.evidence_sha256

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "report_id": self.report_id,
            "verifier_id": self.verifier_id,
            "status": self.status,
            "output": self.output,
            "evidence_sha256": self.evidence_sha256,
            "evidence_hash_valid": self.verify_evidence_hash(),
            "metadata": dict(self.metadata),
        }
        if self.rerun_agreement is not None:
            payload["rerun_agreement"] = self.rerun_agreement
        return payload


def build_verifier_run_report(
    *,
    report_id: str,
    verifier_id: str,
    status: str,
    output: str,
    rerun_output: str | None = None,
) -> VerifierRunReport:
    evidence_sha256 = "sha256:" + hashlib.sha256(output.encode("utf-8")).hexdigest()
    return VerifierRunReport(
        report_id=report_id,
        verifier_id=verifier_id,
        status=status,
        output=output,
        evidence_sha256=evidence_sha256,
        rerun_agreement=(output == rerun_output) if rerun_output is not None else None,
    )
