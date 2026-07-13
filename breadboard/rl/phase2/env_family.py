from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CLAIM_BOUNDARY = "p2_m8_second_environment_probe_not_general_env_support_claim"


@dataclass(frozen=True)
class ProbeCheck:
    name: str
    status: str
    evidence_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class SecondEnvironmentFamilyProbeReport:
    report_id: str
    family_id: str
    env_package_id: str
    target_run_id: str
    renderer_probe: ProbeCheck
    replay_probe: ProbeCheck
    export_probe: ProbeCheck
    target_smoke: ProbeCheck
    claim_boundary: str = CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False
    preserved_fields: list[str] = field(default_factory=list)
    lost_fields: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def smoke_ready(self) -> bool:
        checks = (self.renderer_probe, self.replay_probe, self.export_probe, self.target_smoke)
        return all(check.status == "passed" for check in checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "family_id": self.family_id,
            "env_package_id": self.env_package_id,
            "target_run_id": self.target_run_id,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "passed": self.smoke_ready,
            "smoke_ready": self.smoke_ready,
            "renderer_probe": self.renderer_probe.to_dict(),
            "replay_probe": self.replay_probe.to_dict(),
            "export_probe": self.export_probe.to_dict(),
            "target_smoke": self.target_smoke.to_dict(),
            "preserved_fields": list(self.preserved_fields),
            "lost_fields": list(self.lost_fields),
            "metadata": dict(self.metadata),
        }


def build_second_env_family_probe_report(
    *,
    family_id: str,
    env_package_id: str,
    target_run_id: str = "fixture-phase2-second-env-family",
    target_smoke_status: str = "passed",
) -> SecondEnvironmentFamilyProbeReport:
    return SecondEnvironmentFamilyProbeReport(
        report_id="bb_zyphra_rl_phase2_second_env_family_v1",
        family_id=family_id,
        env_package_id=env_package_id,
        target_run_id=target_run_id,
        renderer_probe=ProbeCheck(
            name="renderer_transcript_shape",
            status="passed",
            evidence_ref="cas://env-family/renderer/" + family_id,
        ),
        replay_probe=ProbeCheck(
            name="deterministic_replay",
            status="passed",
            evidence_ref="cas://env-family/replay/" + family_id,
        ),
        export_probe=ProbeCheck(
            name="trainer_projection_shape",
            status="passed",
            evidence_ref="cas://env-family/export/" + family_id,
        ),
        target_smoke=ProbeCheck(
            name="target_smoke",
            status=target_smoke_status,
            evidence_ref="cas://env-family/target-smoke/" + family_id,
        ),
        preserved_fields=[
            "env_package_id",
            "renderer_events",
            "replay_seed",
            "export_projection_ref",
            "target_smoke_status",
        ],
        lost_fields=["live_target_scheduler_log"],
        metadata={
            "fixture_scope": "second_environment_family_schema_smoke",
            "candidate_family": family_id,
        },
    )


def build_lean_console_fixture_probe_report() -> SecondEnvironmentFamilyProbeReport:
    return build_second_env_family_probe_report(
        family_id="lean_console",
        env_package_id="lean_console_fixture_env",
    )
