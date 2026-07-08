from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


CLAIM_BOUNDARY = "p2_m7_benchflow_probe_not_full_security_coverage_claim"


@dataclass(frozen=True)
class BenchFlowHardeningImportReport:
    report_id: str
    target_run_id: str
    source_artifact: str
    preserved_fields: list[str]
    lost_fields: list[str]
    field_mapping: dict[str, str]
    imported_probe_catches_fixture: bool
    fixture_detection_ref: str | None
    claim_boundary: str = CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "target_run_id": self.target_run_id,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "passed": self.imported_probe_catches_fixture and bool(self.preserved_fields),
            "source_artifact": self.source_artifact,
            "preserved_fields": list(self.preserved_fields),
            "lost_fields": list(self.lost_fields),
            "field_mapping": dict(self.field_mapping),
            "imported_probe_catches_fixture": self.imported_probe_catches_fixture,
            "fixture_detection_ref": self.fixture_detection_ref,
            "metadata": dict(self.metadata),
        }


def build_benchflow_hardening_import_report(
    *,
    imported_fields: Sequence[str],
    breadboard_policy_fields: Mapping[str, str],
    source_artifact: str,
    adversarial_fixture_detected: bool,
    target_run_id: str = "fixture-phase2-benchflow-hardening",
) -> BenchFlowHardeningImportReport:
    imported = list(imported_fields)
    preserved = [field for field in imported if field in breadboard_policy_fields]
    lost = [field for field in imported if field not in breadboard_policy_fields]
    field_mapping = {field: breadboard_policy_fields[field] for field in preserved}
    fixture_detection_ref = "cas://benchflow/imported-probe/adversarial-fixture" if adversarial_fixture_detected else None
    return BenchFlowHardeningImportReport(
        report_id="bb_zyphra_rl_phase2_benchflow_hardening_import_v1",
        target_run_id=target_run_id,
        source_artifact=source_artifact,
        preserved_fields=preserved,
        lost_fields=lost,
        field_mapping=field_mapping,
        imported_probe_catches_fixture=adversarial_fixture_detected,
        fixture_detection_ref=fixture_detection_ref,
        metadata={
            "fixture_scope": "benchflow_policy_field_import",
            "minimum_promotion_requirement": "real_benchflow_probe_with_sandbox_attestation",
        },
    )


def fixture_benchflow_import_report() -> BenchFlowHardeningImportReport:
    return build_benchflow_hardening_import_report(
        imported_fields=[
            "workspace_isolation",
            "network_egress_block",
            "path_traversal_probe",
            "benchflow_harbor_attestation",
        ],
        breadboard_policy_fields={
            "workspace_isolation": "EnvPackage.security.workspace_policy",
            "network_egress_block": "EnvPackage.security.egress_policy",
            "path_traversal_probe": "RewardHackProbeSuite.path_escape",
        },
        source_artifact="fixtures/benchflow/hardening_import_fixture.json",
        adversarial_fixture_detected=True,
    )
