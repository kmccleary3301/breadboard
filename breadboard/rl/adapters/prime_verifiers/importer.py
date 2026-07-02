from __future__ import annotations

from breadboard.rl.adapters.probe import AdapterProbeReport


def build_prime_verifiers_fixture_probe_report() -> AdapterProbeReport:
    return AdapterProbeReport(
        adapter_id="prime_verifiers.fixture.v1",
        adapter_kind="prime_verifiers_fixture",
        support_level="fixture_probe",
        workload_family="verifier",
        preserved_fields=[
            "verifier_id",
            "verifier_kind",
            "evidence_sha256",
            "rerun_agreement",
            "reward_scalar",
        ],
        field_mapping={
            "verifier_id": "M6 row_evidence.verifier.id",
            "verifier_kind": "M6 row_evidence.verifier.kind",
            "evidence_sha256": "M6 row_evidence.sha256",
            "rerun_agreement": "M6 row_evidence.rerun_agreement",
            "reward_scalar": "M6 row_evidence.reward_scalar",
        },
        lost_fields=["remote_prime_registry_identity", "live_verifier_service_attestation"],
        unsupported_fields=["production_prime_verifiers_execution"],
        source_artifacts=["docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/row_evidence/"],
        fidelity_notes=[
            "This probe records verifier identity/evidence semantics from local M6 row evidence only.",
            "It does not register a verifier with Prime Verifiers or prove service-side execution/attestation.",
        ],
        promotion_requirements=[
            "Wrap one BreadBoard verifier in the real Prime Verifiers interface and capture registry identity plus service attestation.",
            "Confirm local rerun agreement and remote verifier result agreement on accepted, rejected, and quarantined examples.",
        ],
        metadata={
            "fixture_scope": "local_verifier_evidence_mapping",
            "minimum_real_promotion_level": "live_probe",
        },
    )
