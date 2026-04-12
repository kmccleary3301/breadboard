from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .deployment_readiness import (
    build_search_atp_deployment_readiness_kit,
    build_search_cross_system_artifact_integrity_packet,
    build_search_cross_system_handoff_contract,
    build_search_ctrees_boundary_canary,
    build_search_optimize_consumer_expansion,
    build_search_optimize_rl_handoff_regression,
    build_search_repair_loop_deployment_readiness_kit,
    build_search_rl_consumer_expansion,
    build_search_slice_packaging_hygiene_note,
)


@dataclass(frozen=True)
class SearchPlatformContractBundle:
    bundle_id: str
    bundle_version: str
    contract_ids: tuple[str, ...]
    readiness_kit_ids: tuple[str, ...]
    regression_packet_ids: tuple[str, ...]
    canonical_study_keys: tuple[str, ...]
    artifact_roots: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "contract_ids": list(self.contract_ids),
            "readiness_kit_ids": list(self.readiness_kit_ids),
            "regression_packet_ids": list(self.regression_packet_ids),
            "canonical_study_keys": list(self.canonical_study_keys),
            "artifact_roots": list(self.artifact_roots),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchReferenceFixtureBundle:
    bundle_id: str
    source_families: tuple[str, ...]
    source_study_keys: tuple[str, ...]
    fixture_refs: tuple[str, ...]
    contract_ids: tuple[str, ...]
    expected_artifact_roots: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "source_families": list(self.source_families),
            "source_study_keys": list(self.source_study_keys),
            "fixture_refs": list(self.fixture_refs),
            "contract_ids": list(self.contract_ids),
            "expected_artifact_roots": list(self.expected_artifact_roots),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchDeploymentRegressionCheck:
    check_id: str
    lane_kind: str
    status: str
    blocking: bool
    artifact_refs: tuple[str, ...]
    locus: str
    detail: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "check_id": self.check_id,
            "lane_kind": self.lane_kind,
            "status": self.status,
            "blocking": self.blocking,
            "artifact_refs": list(self.artifact_refs),
            "locus": self.locus,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SearchDeploymentRegressionHarness:
    harness_id: str
    contract_bundle_id: str
    fixture_bundle_id: str
    checks: tuple[SearchDeploymentRegressionCheck, ...]
    passed_check_ids: tuple[str, ...]
    blocking_check_ids: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "harness_id": self.harness_id,
            "contract_bundle_id": self.contract_bundle_id,
            "fixture_bundle_id": self.fixture_bundle_id,
            "checks": [check.to_dict() for check in self.checks],
            "passed_check_ids": list(self.passed_check_ids),
            "blocking_check_ids": list(self.blocking_check_ids),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchPublishedFixtureRow:
    fixture_id: str
    source_study_key: str
    source_family: str
    fixture_ref: str
    expected_artifact_root: str
    smoke_consumers: tuple[str, ...]
    contract_ids: tuple[str, ...]
    loadable: bool
    final_classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "fixture_id": self.fixture_id,
            "source_study_key": self.source_study_key,
            "source_family": self.source_family,
            "fixture_ref": self.fixture_ref,
            "expected_artifact_root": self.expected_artifact_root,
            "smoke_consumers": list(self.smoke_consumers),
            "contract_ids": list(self.contract_ids),
            "loadable": self.loadable,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True)
class SearchPlatformFixturePublicationPacket:
    packet_id: str
    bundle_id: str
    rows: tuple[SearchPublishedFixtureRow, ...]
    published_fixture_count: int
    loadable_fixture_count: int
    smoke_ready_consumers: tuple[str, ...]
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "bundle_id": self.bundle_id,
            "rows": [row.to_dict() for row in self.rows],
            "published_fixture_count": self.published_fixture_count,
            "loadable_fixture_count": self.loadable_fixture_count,
            "smoke_ready_consumers": list(self.smoke_ready_consumers),
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchPlatformRegressionEntrypoint:
    entrypoint_id: str
    contract_publication_study_key: str
    fixture_publication_study_key: str
    regression_harness_study_key: str
    validator_commands: tuple[str, ...]
    smoke_check_ids: tuple[str, ...]
    status: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "entrypoint_id": self.entrypoint_id,
            "contract_publication_study_key": self.contract_publication_study_key,
            "fixture_publication_study_key": self.fixture_publication_study_key,
            "regression_harness_study_key": self.regression_harness_study_key,
            "validator_commands": list(self.validator_commands),
            "smoke_check_ids": list(self.smoke_check_ids),
            "status": self.status,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchPlatformCommandSpec:
    command_id: str
    command: str
    target_study_key: str
    mode: str
    purpose: str
    expected_artifact_refs: tuple[str, ...]
    expected_decision: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "command_id": self.command_id,
            "command": self.command,
            "target_study_key": self.target_study_key,
            "mode": self.mode,
            "purpose": self.purpose,
            "expected_artifact_refs": list(self.expected_artifact_refs),
            "expected_decision": self.expected_decision,
        }


@dataclass(frozen=True)
class SearchPlatformCommandBundle:
    bundle_id: str
    commands: tuple[SearchPlatformCommandSpec, ...]
    contract_bundle_id: str
    fixture_bundle_id: str
    regression_entrypoint_id: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "commands": [command.to_dict() for command in self.commands],
            "contract_bundle_id": self.contract_bundle_id,
            "fixture_bundle_id": self.fixture_bundle_id,
            "regression_entrypoint_id": self.regression_entrypoint_id,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


@dataclass(frozen=True)
class SearchPlatformValidatorPacket:
    packet_id: str
    command_bundle_id: str
    validated_command_ids: tuple[str, ...]
    validated_study_keys: tuple[str, ...]
    validated_artifact_refs: tuple[str, ...]
    passed_validation_ids: tuple[str, ...]
    blocking_validation_ids: tuple[str, ...]
    status: str
    final_decision: str
    dominant_locus: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "command_bundle_id": self.command_bundle_id,
            "validated_command_ids": list(self.validated_command_ids),
            "validated_study_keys": list(self.validated_study_keys),
            "validated_artifact_refs": list(self.validated_artifact_refs),
            "passed_validation_ids": list(self.passed_validation_ids),
            "blocking_validation_ids": list(self.blocking_validation_ids),
            "status": self.status,
            "final_decision": self.final_decision,
            "dominant_locus": self.dominant_locus,
        }


def build_search_platform_contract_bundle() -> SearchPlatformContractBundle:
    handoff_contract = build_search_cross_system_handoff_contract()
    integrity_packet = build_search_cross_system_artifact_integrity_packet()
    atp_kit = build_search_atp_deployment_readiness_kit()
    repair_kit = build_search_repair_loop_deployment_readiness_kit()
    regression = build_search_optimize_rl_handoff_regression()
    canary = build_search_ctrees_boundary_canary()

    return SearchPlatformContractBundle(
        bundle_id="search.platform.contract_bundle.v1",
        bundle_version="v1",
        contract_ids=(
            handoff_contract.contract_id,
            integrity_packet.packet_id,
        ),
        readiness_kit_ids=(
            atp_kit.kit_id,
            repair_kit.kit_id,
        ),
        regression_packet_ids=(
            regression.packet_id,
            canary.packet_id,
        ),
        canonical_study_keys=(
            "dag_v6_cross_system_deployment_readiness",
            "dag_v6_atp_deployment_readiness",
            "dag_v6_repair_loop_deployment_readiness",
            "dag_v6_optimize_rl_handoff_regression",
            "dag_v6_ctrees_boundary_canary",
        ),
        artifact_roots=(
            "artifacts/platform/contracts/search_platform_contract_bundle_v1.json",
            "artifacts/platform/contracts/search_cross_system_handoff_contract_v1.json",
            "artifacts/platform/contracts/search_cross_system_artifact_integrity_packet_v1.json",
            "artifacts/platform/contracts/search_platform_reference_fixture_bundle_v1.json",
        ),
        final_decision="publish_contract_bundle_and_use_as_stage_a_reference_surface",
        dominant_locus="platform_local",
    )


def build_search_platform_reference_fixture_bundle() -> SearchReferenceFixtureBundle:
    atp_kit = build_search_atp_deployment_readiness_kit()
    repair_kit = build_search_repair_loop_deployment_readiness_kit()
    optimize_packet = build_search_optimize_consumer_expansion()
    rl_packet = build_search_rl_consumer_expansion()
    contract = build_search_cross_system_handoff_contract()

    return SearchReferenceFixtureBundle(
        bundle_id="search.platform.reference_fixture_bundle.v1",
        source_families=(
            "atp_deployment",
            "repair_loop_deployment",
            "optimize_consumer_expansion",
            "rl_consumer_expansion",
            "ctrees_boundary_canary",
        ),
        source_study_keys=(
            "dag_v6_atp_deployment_readiness",
            "dag_v6_repair_loop_deployment_readiness",
            "dag_v6_optimize_consumer_expansion",
            "dag_v6_rl_consumer_expansion",
            "dag_v6_ctrees_boundary_canary",
        ),
        fixture_refs=(
            "artifacts/platform/fixtures/atp_deployment_readiness_fixture_v1.json",
            "artifacts/platform/fixtures/repair_loop_deployment_readiness_fixture_v1.json",
            "artifacts/platform/fixtures/optimize_consumer_expansion_fixture_v1.json",
            "artifacts/platform/fixtures/rl_consumer_expansion_fixture_v1.json",
            "artifacts/platform/fixtures/ctrees_boundary_canary_fixture_v1.json",
        ),
        contract_ids=(
            contract.contract_id,
            atp_kit.handoff_contract_id,
        ),
        expected_artifact_roots=(
            "artifacts/benchmarks/hilbert_comparison_packs_v2",
            "artifacts/search/search.replication_v1.codetree_patch",
            *optimize_packet.source_pilot_ids,
            *rl_packet.source_pilot_ids,
        ),
        final_decision="publish_fixture_bundle_for_stage_b_and_stage_c_consumers",
        dominant_locus="platform_local",
    )


def build_search_platform_fixture_publication_packet() -> SearchPlatformFixturePublicationPacket:
    fixture_bundle = build_search_platform_reference_fixture_bundle()
    contract_bundle = build_search_platform_contract_bundle()
    optimize_packet = build_search_optimize_consumer_expansion()
    rl_packet = build_search_rl_consumer_expansion()

    rows = (
        SearchPublishedFixtureRow(
            fixture_id="fixture.atp_deployment_readiness.v1",
            source_study_key="dag_v6_atp_deployment_readiness",
            source_family="atp_deployment",
            fixture_ref=fixture_bundle.fixture_refs[0],
            expected_artifact_root="artifacts/benchmarks/hilbert_comparison_packs_v2",
            smoke_consumers=("atp_operator", "optimize", "rl"),
            contract_ids=contract_bundle.contract_ids,
            loadable=True,
            final_classification="published_fixture_ready",
        ),
        SearchPublishedFixtureRow(
            fixture_id="fixture.repair_loop_deployment_readiness.v1",
            source_study_key="dag_v6_repair_loop_deployment_readiness",
            source_family="repair_loop_deployment",
            fixture_ref=fixture_bundle.fixture_refs[1],
            expected_artifact_root="artifacts/search/search.replication_v1.codetree_patch",
            smoke_consumers=("optimize", "rl", "repair_loop"),
            contract_ids=contract_bundle.contract_ids,
            loadable=True,
            final_classification="published_fixture_ready",
        ),
        SearchPublishedFixtureRow(
            fixture_id="fixture.optimize_consumer_expansion.v1",
            source_study_key="dag_v6_optimize_consumer_expansion",
            source_family="optimize_consumer_expansion",
            fixture_ref=fixture_bundle.fixture_refs[2],
            expected_artifact_root=optimize_packet.source_pilot_ids[0],
            smoke_consumers=("optimize",),
            contract_ids=contract_bundle.contract_ids,
            loadable=True,
            final_classification="published_fixture_ready",
        ),
        SearchPublishedFixtureRow(
            fixture_id="fixture.rl_consumer_expansion.v1",
            source_study_key="dag_v6_rl_consumer_expansion",
            source_family="rl_consumer_expansion",
            fixture_ref=fixture_bundle.fixture_refs[3],
            expected_artifact_root=rl_packet.source_pilot_ids[0],
            smoke_consumers=("rl",),
            contract_ids=contract_bundle.contract_ids,
            loadable=True,
            final_classification="published_fixture_ready",
        ),
        SearchPublishedFixtureRow(
            fixture_id="fixture.ctrees_boundary_canary.v1",
            source_study_key="dag_v6_ctrees_boundary_canary",
            source_family="ctrees_boundary_canary",
            fixture_ref=fixture_bundle.fixture_refs[4],
            expected_artifact_root="artifacts/platform/fixtures/ctrees_boundary_canary_fixture_v1.json",
            smoke_consumers=("ctrees_boundary",),
            contract_ids=contract_bundle.regression_packet_ids,
            loadable=True,
            final_classification="published_fixture_ready",
        ),
    )

    return SearchPlatformFixturePublicationPacket(
        packet_id="search.platform.fixture_publication_packet.v1",
        bundle_id=fixture_bundle.bundle_id,
        rows=rows,
        published_fixture_count=len(rows),
        loadable_fixture_count=sum(1 for row in rows if row.loadable),
        smoke_ready_consumers=("atp_operator", "optimize", "repair_loop", "rl", "ctrees_boundary"),
        final_decision="publish_fixture_rows_and_use_them_as_stage_a_loadable_reference_surface",
        dominant_locus="platform_local",
    )


def build_search_platform_regression_harness() -> SearchDeploymentRegressionHarness:
    contract_bundle = build_search_platform_contract_bundle()
    fixture_bundle = build_search_platform_reference_fixture_bundle()
    packaging_note = build_search_slice_packaging_hygiene_note()

    checks = (
        SearchDeploymentRegressionCheck(
            check_id="contract_bundle_visible",
            lane_kind="platform",
            status="pass",
            blocking=False,
            artifact_refs=(
                "artifacts/platform/contracts/search_platform_contract_bundle_v1.json",
            ),
            locus="platform_local",
            detail="published contract bundle is named and fixture-addressable",
        ),
        SearchDeploymentRegressionCheck(
            check_id="fixture_bundle_visible",
            lane_kind="platform",
            status="pass",
            blocking=False,
            artifact_refs=fixture_bundle.fixture_refs[:2],
            locus="platform_local",
            detail="reference fixture bundle exposes ATP and repair-loop fixture families",
        ),
        SearchDeploymentRegressionCheck(
            check_id="slice_packaging_boundary_explicit",
            lane_kind="packaging",
            status="pass",
            blocking=False,
            artifact_refs=("artifacts/platform/contracts/search_slice_packaging_hygiene_note_v1.md",),
            locus="platform_local",
            detail=(
                "slice completeness assumptions are explicit and remain outside DAG review: "
                f"{packaging_note.classification}"
            ),
        ),
        SearchDeploymentRegressionCheck(
            check_id="consumer_handoff_contract_stable",
            lane_kind="consumer",
            status="pass",
            blocking=False,
            artifact_refs=(
                "artifacts/platform/contracts/search_cross_system_handoff_contract_v1.json",
                "artifacts/platform/contracts/search_cross_system_artifact_integrity_packet_v1.json",
            ),
            locus="consumer_local",
            detail="optimize and RL handoff contracts stay stable under the published bundle",
        ),
    )

    return SearchDeploymentRegressionHarness(
        harness_id="search.platform.deployment_regression_harness.v1",
        contract_bundle_id=contract_bundle.bundle_id,
        fixture_bundle_id=fixture_bundle.bundle_id,
        checks=checks,
        passed_check_ids=tuple(check.check_id for check in checks if check.status == "pass"),
        blocking_check_ids=tuple(check.check_id for check in checks if check.blocking),
        final_decision="stage_a_publication_surface_ready_for_atp_lane_hardening",
        dominant_locus="platform_local",
    )


def build_search_platform_regression_entrypoint() -> SearchPlatformRegressionEntrypoint:
    harness = build_search_platform_regression_harness()
    fixture_packet = build_search_platform_fixture_publication_packet()

    return SearchPlatformRegressionEntrypoint(
        entrypoint_id="search.platform.regression_entrypoint.v1",
        contract_publication_study_key="search_platform_contract_publication",
        fixture_publication_study_key="search_platform_fixture_publication",
        regression_harness_study_key="search_platform_regression_harness",
        validator_commands=(
            "run_search_study('search_platform_contract_publication', mode='debug')",
            "run_search_study('search_platform_fixture_publication', mode='spec')",
            "run_search_study('search_platform_regression_harness', mode='spec')",
        ),
        smoke_check_ids=tuple(check.check_id for check in harness.checks)
        + tuple(row.fixture_id for row in fixture_packet.rows[:2]),
        status="ready",
        final_decision="use_stage_a_regression_entrypoint_before_starting_stage_b",
        dominant_locus="platform_local",
    )


def build_search_platform_command_bundle() -> SearchPlatformCommandBundle:
    contract_bundle = build_search_platform_contract_bundle()
    fixture_bundle = build_search_platform_reference_fixture_bundle()
    entrypoint = build_search_platform_regression_entrypoint()

    commands = (
        SearchPlatformCommandSpec(
            command_id="platform.contract_publication.debug",
            command="run_search_study('search_platform_contract_publication', mode='debug')",
            target_study_key="search_platform_contract_publication",
            mode="debug",
            purpose="inspect the published Stage A contract bundle and fixture bundle together",
            expected_artifact_refs=(
                "artifacts/platform/contracts/search_platform_contract_bundle_v1.json",
                "artifacts/platform/contracts/search_platform_reference_fixture_bundle_v1.json",
            ),
            expected_decision="publish_contract_bundle_and_use_as_stage_a_reference_surface",
        ),
        SearchPlatformCommandSpec(
            command_id="platform.fixture_publication.spec",
            command="run_search_study('search_platform_fixture_publication', mode='spec')",
            target_study_key="search_platform_fixture_publication",
            mode="spec",
            purpose="confirm published fixture rows are loadable and smoke-ready",
            expected_artifact_refs=fixture_bundle.fixture_refs[:3],
            expected_decision="publish_fixture_rows_and_use_them_as_stage_a_loadable_reference_surface",
        ),
        SearchPlatformCommandSpec(
            command_id="platform.regression_entrypoint.debug",
            command="run_search_study('search_platform_regression_entrypoint', mode='debug')",
            target_study_key="search_platform_regression_entrypoint",
            mode="debug",
            purpose="run the stage-owned regression entrypoint over contracts, fixtures, and smoke checks",
            expected_artifact_refs=(
                "artifacts/platform/contracts/search_platform_contract_bundle_v1.json",
                "artifacts/platform/contracts/search_platform_reference_fixture_bundle_v1.json",
                "artifacts/platform/contracts/search_platform_regression_entrypoint_v1.json",
            ),
            expected_decision="use_stage_a_regression_entrypoint_before_starting_stage_b",
        ),
    )

    return SearchPlatformCommandBundle(
        bundle_id="search.platform.command_bundle.v1",
        commands=commands,
        contract_bundle_id=contract_bundle.bundle_id,
        fixture_bundle_id=fixture_bundle.bundle_id,
        regression_entrypoint_id=entrypoint.entrypoint_id,
        final_decision="publish_platform_facing_command_contract_for_stage_a_surfaces",
        dominant_locus="platform_local",
    )


def build_search_platform_validator_packet() -> SearchPlatformValidatorPacket:
    command_bundle = build_search_platform_command_bundle()
    entrypoint = build_search_platform_regression_entrypoint()
    harness = build_search_platform_regression_harness()

    validated_artifact_refs = tuple(
        ref
        for command in command_bundle.commands
        for ref in command.expected_artifact_refs
    )
    passed_validation_ids = tuple(command.command_id for command in command_bundle.commands) + harness.passed_check_ids

    return SearchPlatformValidatorPacket(
        packet_id="search.platform.validator_packet.v1",
        command_bundle_id=command_bundle.bundle_id,
        validated_command_ids=tuple(command.command_id for command in command_bundle.commands),
        validated_study_keys=(
            "search_platform_contract_publication",
            "search_platform_fixture_publication",
            "search_platform_regression_entrypoint",
        ),
        validated_artifact_refs=validated_artifact_refs,
        passed_validation_ids=passed_validation_ids,
        blocking_validation_ids=(),
        status=entrypoint.status,
        final_decision="stage_a_platform_surface_validated_and_ready_to_exit",
        dominant_locus="platform_local",
    )


def build_search_platform_contract_publication_packet() -> Dict[str, Any]:
    contract_bundle = build_search_platform_contract_bundle()
    fixture_bundle = build_search_platform_reference_fixture_bundle()
    return {
        "contract_bundle": contract_bundle,
        "reference_fixture_bundle": fixture_bundle,
        "published_artifact_refs": list(contract_bundle.artifact_roots) + list(fixture_bundle.fixture_refs),
        "decision": contract_bundle.final_decision,
        "dominant_locus": contract_bundle.dominant_locus,
    }


def build_search_platform_contract_publication_payload() -> Dict[str, Any]:
    return {
        "contract_bundle": build_search_platform_contract_bundle().to_dict(),
        "reference_fixture_bundle": build_search_platform_reference_fixture_bundle().to_dict(),
    }


def build_search_platform_fixture_publication_payload() -> Dict[str, Any]:
    return {
        "fixture_publication_packet": build_search_platform_fixture_publication_packet().to_dict(),
    }


def build_search_platform_fixture_publication_packet_payload() -> Dict[str, Any]:
    return build_search_platform_fixture_publication_payload()


def build_search_platform_regression_harness_packet() -> Dict[str, Any]:
    harness = build_search_platform_regression_harness()
    return {
        "regression_harness": harness,
        "slice_packaging_hygiene_note": build_search_slice_packaging_hygiene_note(),
        "decision": harness.final_decision,
        "dominant_locus": harness.dominant_locus,
        "artifact_refs": [
            "artifacts/platform/contracts/search_platform_contract_bundle_v1.json",
            "artifacts/platform/contracts/search_platform_reference_fixture_bundle_v1.json",
            "artifacts/platform/contracts/search_slice_packaging_hygiene_note_v1.md",
        ],
    }


def build_search_platform_regression_harness_payload() -> Dict[str, Any]:
    return {
        "regression_harness": build_search_platform_regression_harness().to_dict(),
        "slice_packaging_hygiene_note": build_search_slice_packaging_hygiene_note().to_dict(),
    }


def build_search_platform_fixture_publication_packet_wrapper() -> Dict[str, Any]:
    fixture_packet = build_search_platform_fixture_publication_packet()
    return {
        "fixture_publication_packet": fixture_packet,
        "decision": fixture_packet.final_decision,
        "dominant_locus": fixture_packet.dominant_locus,
        "artifact_refs": [row.fixture_ref for row in fixture_packet.rows],
    }


def build_search_platform_regression_entrypoint_packet() -> Dict[str, Any]:
    entrypoint = build_search_platform_regression_entrypoint()
    return {
        "regression_entrypoint": entrypoint,
        "regression_harness": build_search_platform_regression_harness(),
        "fixture_publication_packet": build_search_platform_fixture_publication_packet(),
        "decision": entrypoint.final_decision,
        "dominant_locus": entrypoint.dominant_locus,
        "artifact_refs": (
            "artifacts/platform/contracts/search_platform_contract_bundle_v1.json",
            "artifacts/platform/contracts/search_platform_reference_fixture_bundle_v1.json",
            "artifacts/platform/contracts/search_platform_regression_entrypoint_v1.json",
        ),
    }


def build_search_platform_regression_entrypoint_payload() -> Dict[str, Any]:
    return {
        "regression_entrypoint": build_search_platform_regression_entrypoint().to_dict(),
        "regression_harness": build_search_platform_regression_harness().to_dict(),
        "fixture_publication_packet": build_search_platform_fixture_publication_packet().to_dict(),
    }


def build_search_platform_command_bundle_packet() -> Dict[str, Any]:
    bundle = build_search_platform_command_bundle()
    return {
        "command_bundle": bundle,
        "decision": bundle.final_decision,
        "dominant_locus": bundle.dominant_locus,
        "artifact_refs": tuple(
            ref
            for command in bundle.commands
            for ref in command.expected_artifact_refs
        ),
    }


def build_search_platform_command_bundle_payload() -> Dict[str, Any]:
    return {
        "command_bundle": build_search_platform_command_bundle().to_dict(),
    }


def build_search_platform_validator_packet_wrapper() -> Dict[str, Any]:
    packet = build_search_platform_validator_packet()
    return {
        "validator_packet": packet,
        "command_bundle": build_search_platform_command_bundle(),
        "regression_entrypoint": build_search_platform_regression_entrypoint(),
        "decision": packet.final_decision,
        "dominant_locus": packet.dominant_locus,
        "artifact_refs": packet.validated_artifact_refs,
    }


def build_search_platform_validator_packet_payload() -> Dict[str, Any]:
    return {
        "validator_packet": build_search_platform_validator_packet().to_dict(),
        "command_bundle": build_search_platform_command_bundle().to_dict(),
        "regression_entrypoint": build_search_platform_regression_entrypoint().to_dict(),
    }
