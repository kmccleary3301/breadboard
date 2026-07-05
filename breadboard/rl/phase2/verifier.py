from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


CLAIM_BOUNDARY = "p2_m6_live_verifier_probe_not_general_verifier_claim"


@dataclass(frozen=True)
class VerifierCallEvidence:
    call_id: str
    provider: str
    endpoint_id: str
    verifier_version: str
    request_hash: str
    response_hash: str
    reward_scalar: float
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "provider": self.provider,
            "endpoint_id": self.endpoint_id,
            "verifier_version": self.verifier_version,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "reward_scalar": self.reward_scalar,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class LiveVerifierIntegrationReport:
    report_id: str
    target_run_id: str
    calls: list[VerifierCallEvidence]
    baseline_reward: float
    drift_tolerance: float
    max_abs_drift: float
    status: str
    quarantine_reasons: list[str]
    preserved_fields: list[str]
    lost_fields: list[str]
    claim_boundary: str = CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def quarantined(self) -> bool:
        return self.status == "quarantined_verifier_instability"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "target_run_id": self.target_run_id,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "passed": not self.quarantined,
            "baseline_reward": self.baseline_reward,
            "drift_tolerance": self.drift_tolerance,
            "max_abs_drift": self.max_abs_drift,
            "status": self.status,
            "quarantined": self.quarantined,
            "quarantine_reasons": list(self.quarantine_reasons),
            "preserved_fields": list(self.preserved_fields),
            "lost_fields": list(self.lost_fields),
            "calls": [call.to_dict() for call in self.calls],
            "metadata": dict(self.metadata),
        }


def build_live_verifier_integration_report(
    calls: Sequence[VerifierCallEvidence],
    *,
    baseline_reward: float,
    drift_tolerance: float = 0.05,
    target_run_id: str = "fixture-phase2-live-verifier",
) -> LiveVerifierIntegrationReport:
    call_list = list(calls)
    quarantine_reasons: list[str] = []
    if not call_list:
        quarantine_reasons.append("missing_verifier_calls")
        max_abs_drift = 0.0
    else:
        max_abs_drift = round(max(abs(call.reward_scalar - baseline_reward) for call in call_list), 12)

    providers = {call.provider for call in call_list}
    versions = {call.verifier_version for call in call_list}
    request_hashes = {call.request_hash for call in call_list}
    errored_calls = [call.call_id for call in call_list if call.error]

    if errored_calls:
        quarantine_reasons.append("verifier_call_error:" + ",".join(sorted(errored_calls)))
    if max_abs_drift > drift_tolerance:
        quarantine_reasons.append("reward_drift_exceeded")
    if len(versions) > 1:
        quarantine_reasons.append("verifier_version_drift")
    if len(request_hashes) > 1:
        quarantine_reasons.append("request_hash_drift")
    if not providers.issubset({"ors", "openreward"}):
        quarantine_reasons.append("unsupported_verifier_provider")

    status = "quarantined_verifier_instability" if quarantine_reasons else "live_verifier_fixture_stable"
    return LiveVerifierIntegrationReport(
        report_id="bb_zyphra_rl_phase2_live_verifier_v1",
        target_run_id=target_run_id,
        calls=call_list,
        baseline_reward=baseline_reward,
        drift_tolerance=drift_tolerance,
        max_abs_drift=max_abs_drift,
        status=status,
        quarantine_reasons=quarantine_reasons,
        preserved_fields=[
            "provider",
            "endpoint_id",
            "verifier_version",
            "request_hash",
            "response_hash",
            "reward_scalar",
            "latency_ms",
        ],
        lost_fields=[
            "remote_model_weights",
            "provider_internal_trace",
        ],
        metadata={
            "fixture_scope": "ors_openreward_call_ledger",
            "quarantine_policy": "fail_closed_on_error_drift_or_version_change",
        },
    )


def stable_fixture_verifier_calls() -> list[VerifierCallEvidence]:
    return [
        VerifierCallEvidence(
            call_id="ors-call-1",
            provider="ors",
            endpoint_id="ors.fixture.local",
            verifier_version="openreward-fixture-v1",
            request_hash="4" * 64,
            response_hash="5" * 64,
            reward_scalar=0.75,
            latency_ms=42,
        ),
        VerifierCallEvidence(
            call_id="openreward-call-1",
            provider="openreward",
            endpoint_id="openreward.fixture.local",
            verifier_version="openreward-fixture-v1",
            request_hash="4" * 64,
            response_hash="6" * 64,
            reward_scalar=0.76,
            latency_ms=45,
        ),
    ]
