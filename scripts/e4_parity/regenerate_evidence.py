#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import fnmatch
import os
import shutil
import tempfile
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Sequence


try:
    from scripts.e4_parity.immutable_inputs import provision_immutable_inputs
    from scripts.e4_parity.lane_definitions import (
        DEFAULT_LANE_DEF_DIR,
        load_lane_defs,
        record_builder_source_paths,
    )
    from scripts.e4_parity.lane_runtime import LANE_SHARED_READ_ONLY_PATHS
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.e4_parity.immutable_inputs import provision_immutable_inputs
    from scripts.e4_parity.lane_definitions import (
        DEFAULT_LANE_DEF_DIR,
        load_lane_defs,
        record_builder_source_paths,
    )
    from scripts.e4_parity.lane_runtime import LANE_SHARED_READ_ONLY_PATHS

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
PYTHON = "{python}"
EXPECTED_POINTS = "{expected_points}"
EXPECTED_CLAIMS = "{expected_claims}"

PINNED_GENERATED_AT_UTC = "2026-07-03T00:00:00Z"


@dataclass(frozen=True)
class Stage:
    """One executable node in the E4 evidence regeneration DAG."""

    stage_id: str
    phase: str
    label: str
    argv: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    read_only: bool = False
    note: str | None = None
    blocker: str | None = None
    allowed_exit_codes: tuple[int, ...] = ()

    def expanded_argv(
        self,
        python: str,
        *,
        resolve_execution_values: bool = False,
        workspace_root: Path | None = None,
    ) -> list[str]:
        # ".venv/bin/python" in lane-derived argv is the same interpreter the
        # canonical stages run ({python} = the live venv); substitute the
        # absolute form so candidate roots (which exclude the gitignored venv)
        # resolve it identically.
        substitutions = {PYTHON: python}
        if resolve_execution_values and (
            EXPECTED_POINTS in self.argv or EXPECTED_CLAIMS in self.argv
        ):
            # The readiness module resolves the external evidence workspace at
            # import time. Reload it for every candidate workspace and evaluate
            # the substitutions before restoring the caller's environment.
            # A fixed-point run creates a new candidate root on each pass.
            previous_workspace = os.environ.get("BB_WORKSPACE_ROOT")
            if workspace_root is not None:
                os.environ["BB_WORKSPACE_ROOT"] = str(workspace_root)
            try:
                final_readiness = importlib.import_module(
                    "scripts.e4_parity.build_e4_final_readiness_packet"
                )
                final_readiness = importlib.reload(final_readiness)
                expected_points = final_readiness.expected_points()
                expected_claims = final_readiness._expected_target_support_claims()
            finally:
                if workspace_root is not None:
                    if previous_workspace is None:
                        os.environ.pop("BB_WORKSPACE_ROOT", None)
                    else:
                        os.environ["BB_WORKSPACE_ROOT"] = previous_workspace

            substitutions[EXPECTED_POINTS] = str(expected_points)
            substitutions[EXPECTED_CLAIMS] = str(expected_claims)
        return [
            substitutions.get(
                part, python if part in (".venv/bin/python", "python") else part
            )
            for part in self.argv
        ]


def _arg_value(argv: Sequence[str], flag: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return None


def _lane_def_reverify_writes(argv: Sequence[str]) -> tuple[str, ...]:
    report_path = _arg_value(argv, "--json-out")
    if report_path is None:
        raise ValueError("lane_def reverify_command must declare --json-out")
    return (report_path,)


_LANE_SHARED_READ_ONLY_PATHS = LANE_SHARED_READ_ONLY_PATHS


def _lane_def_projection_sources(lane_def: dict[str, object]) -> tuple[str, ...]:
    return record_builder_source_paths(lane_def, verbatim_only=True)


def _lane_def_output_paths(
    lane_def: dict[str, object], argv: Sequence[str]
) -> tuple[str, ...]:
    outputs: list[str] = []
    capture = lane_def.get("capture")
    inputs = capture.get("inputs") if isinstance(capture, dict) else None
    declared_inputs = (
        frozenset(item for item in inputs if isinstance(item, str))
        if isinstance(inputs, list)
        else frozenset()
    )
    preserved_sources = declared_inputs | frozenset(
        _lane_def_projection_sources(lane_def)
    )
    normalize = lane_def.get("normalize")
    config = normalize.get("config") if isinstance(normalize, dict) else None
    roles = config.get("roles") if isinstance(config, dict) else None
    if isinstance(roles, dict):
        outputs.extend(
            value
            for value in roles.values()
            if isinstance(value, str)
            and value not in _LANE_SHARED_READ_ONLY_PATHS
            # Mirror run_lane._capture_owned_paths: declared and projection
            # sources are preserved inputs, never lane writes.
            and value not in preserved_sources
        )
    for flag in ("--support-claim", "--evidence-manifest", "--json-out"):
        value = _arg_value(argv, flag)
        if value is not None:
            outputs.append(value)
    return tuple(dict.fromkeys(outputs))


def _lane_def_reverify_reads(
    lane_def: dict[str, object], argv: Sequence[str]
) -> tuple[str, ...]:
    reads: list[str] = []
    if len(argv) > 1:
        reads.append(argv[1])
    capture = lane_def.get("capture")
    if isinstance(capture, dict):
        inputs = capture.get("inputs")
        if isinstance(inputs, list):
            reads.extend(str(item) for item in inputs if isinstance(item, str))
    artifacts_root = lane_def.get("artifacts_root")
    if isinstance(artifacts_root, str):
        reads.append(artifacts_root)
    for flag in ("--support-claim", "--evidence-manifest"):
        value = _arg_value(argv, flag)
        if value is not None:
            reads.append(value)
    return tuple(dict.fromkeys(reads))


def _lane_def_reverify_stages(
    lane_defs: dict[str, dict[str, object]], *, depends_on: str
) -> tuple[Stage, ...]:
    stages: list[Stage] = []
    previous = depends_on
    for lane_id in sorted(lane_defs):
        lane_def = lane_defs[lane_id]
        command = lane_def.get("reverify_command")
        argv = command.get("argv") if isinstance(command, dict) else None
        if not (isinstance(argv, list) and all(isinstance(item, str) for item in argv)):
            continue
        stage_argv = tuple(item for item in argv if item != "--check-only")
        stage = Stage(
            stage_id=f"lane_def_reverify_{lane_id}",
            phase="lane_def_reverify",
            label=f"Reverify lane_def-derived accepted lane {lane_id}.",
            argv=stage_argv,
            depends_on=(previous,),
            reads=_lane_def_reverify_reads(lane_def, stage_argv),
            writes=_lane_def_reverify_writes(stage_argv),
            note="Derived from config/e4_lanes/*.yaml reverify_command; --check-only is removed so the declared --json-out report is physically refreshed.",
        )
        stages.append(stage)
        previous = stage.stage_id
    return tuple(stages)


LANE_DEFS = load_lane_defs(DEFAULT_LANE_DEF_DIR, materialize_inputs=False)


_CLAIM_DERIVED_LANE_ROLES = (
    "evidence_manifest",
    "node_gate",
    "support_claim",
    "validator_output",
)
_PRIMITIVE_PROJECTION_LANE_ROLES = (
    "capability_registry",
    "effective_config_graph",
    "effective_tool_surface",
    "primitive_projection_manifest",
)


def _catalog_argv(
    output: str,
    *,
    excluded_lane_roles: Sequence[str] = (),
    referenced_static_only: bool = False,
) -> tuple[str, ...]:
    argv = [
        PYTHON,
        "scripts/e4_parity/build_artifact_catalog.py",
        "--schema-version",
        "v2",
        "--output",
        output,
    ]
    if referenced_static_only:
        argv.append("--referenced-static-only")
    for role_key in excluded_lane_roles:
        argv.extend(("--exclude-lane-role", role_key))
    return tuple(argv)


LANE_DEF_REVERIFY_STAGES = _lane_def_reverify_stages(
    LANE_DEFS,
    depends_on="support_claim_generation",
)


STAGES: tuple[Stage, ...] = (
    Stage(
        stage_id="source_index",
        phase="lane_artifacts",
        label="Build the deterministic source/path index consumed by lane capture.",
        argv=(PYTHON, "scripts/e4_parity/build_source_index.py", "--json"),
        writes=("../docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",),
    ),
    Stage(
        stage_id="north_star_proof_packets",
        phase="claims_manifests_node_gates_ws_j",
        label="Regenerate WS-J north-star proof packets through the lane runner.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "north-star",
            "--stage",
            "all",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("source_index",),
        reads=(
            # Workspace-level tool defs consumed by the self-capture session's agent
            # config (agent_configs/atp_hilbert_like_gpt54_v1.yaml resolves
            # ../implementations/tools/defs against the execution root). Without this
            # seed the candidate session silently falls back to the full built-in tool
            # registry, changing rendered prompt bytes and staling kernel-event pins.
            "../implementations/tools/defs",
        ),
        writes=(
            "docs/conformance/e4_target_support/*north_star*",
            "docs/conformance/e4_target_support/breadboard_self_runtime_records_v1",
        ),
    ),
    Stage(
        stage_id="scrub_absorbed_validator_refs",
        phase="cleanup",
        label="Remove deleted B3 validator wrapper refs from auxiliary E4 reports before cataloging.",
        argv=(PYTHON, "scripts/e4_parity/scrub_absorbed_validator_refs.py", "--json"),
        depends_on=("north_star_proof_packets",),
        writes=(
            "../docs_tmp/phase_15/oh_my_pi_p6/BB_E4_OH_MY_PI_P6_TERMINAL_REPORT.json",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_HASH_MANIFEST.json",
        ),
    ),
    Stage(
        stage_id="materialize_oh_my_pi_source_freeze",
        phase="lane_artifacts",
        label="Safely materialize the tracked Oh-My-Pi source archive for legacy L5/L6 adapters.",
        argv=(PYTHON, "scripts/e4_parity/materialize_source_freeze.py", "--json"),
        depends_on=("scrub_absorbed_validator_refs",),
        reads=(
            "config/e4_lanes/source_freezes/oh_my_pi_main_5356713e_git_tracked.zip",
        ),
        writes=("../docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest",),
    ),
    Stage(
        stage_id="p3_1_effective_config_graph",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.1 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_1_effective_config_graph_compiler",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("materialize_oh_my_pi_source_freeze",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_1_effective_config_graph_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_1_effective_config_graph_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p31_effective_config_graph_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_2_context_resource_pack",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.2 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_2_context_resource_pack_compiler",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_1_effective_config_graph",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_2_context_resource_pack_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_2_context_resource_pack_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p32_context_resource_pack_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_3_capability_registry",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.3 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_3_capability_registry_compiler",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_2_context_resource_pack",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_3_capability_registry_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_3_capability_registry_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p33_capability_registry_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_4_extension_hook_execution",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.4 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_4_extension_hook_execution_compiler",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_3_capability_registry",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_4_extension_hook_execution_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_4_extension_hook_execution_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p34_extension_hook_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_5_resource_blob",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.5 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_5_resource_blob_compiler",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_4_extension_hook_execution",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_5_resource_blob_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_5_resource_blob_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p35_resource_blob_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_6_protocol_provider_policy",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.6 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_6_protocol_provider_policy_compiler",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_5_resource_blob",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_6_protocol_provider_policy_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_6_protocol_provider_policy_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p36_protocol_provider_policy_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_8_projection_broker",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate P3.8 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p3_8_projection_broker_adapter",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_6_protocol_provider_policy",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_8_projection_broker_adapter_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_8_projection_broker_adapter_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p38_projection_broker_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="pi_p5_cli_config_context_tool_surface",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate Pi P5 L1 CLI/config/context/tool-surface through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "pi_p5_l1_cli_config_context_tool_surface",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("p3_8_projection_broker",),
        writes=(
            "docs/conformance/support_claims/pi_p5_l1_cli_config_context_tool_surface_v1_c4_support_claim.json",
            "docs/conformance/support_claims/pi_p5_l1_cli_config_context_tool_surface_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p5_pi_l1_cli_config_context_tool_surface_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="oh_my_pi_l5_memory_compaction",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate Oh-My-Pi P6 L5 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p6_0_l5_memory_compaction",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("pi_p5_cli_config_context_tool_surface",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p6_0_l5_memory_compaction_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p6_0_l5_memory_compaction_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p6_oh_my_pi_l5_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="oh_my_pi_l6_tui_projection",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate Oh-My-Pi P6 L6 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p6_0_l6_tui_projection",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("oh_my_pi_l5_memory_compaction",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p6_0_l6_tui_projection_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p6_0_l6_tui_projection_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p6_oh_my_pi_l6_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="oh_my_pi_p66_task_job_subagent",
        phase="claims_manifests_node_gates_lane_def_v2",
        label="Regenerate Oh-My-Pi P6.6 through lane_def v2 capture adapter.",
        argv=(
            PYTHON,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            "oh_my_pi_p6_6_task_job_subagent_v2",
            "--stage",
            "capture",
            "--promote-accepted",
            "--defer-promotion-refresh",
            "--json",
        ),
        depends_on=("oh_my_pi_l6_tui_projection",),
        writes=(
            "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent_v2",
            "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v2_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v2_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p6_oh_my_pi_p66_task_job_subagent_v2.json",
        ),
    ),
    Stage(
        stage_id="ledger_seed",
        phase="ledger",
        label="Seed the atomic feature ledger from refreshed capture and source refs.",
        argv=(
            PYTHON,
            "scripts/e4_parity/seed_atomic_feature_ledger.py",
            "--lane-extensions",
            "config/e4_lanes/evidence_inputs/e4_lane_ledger_seed_extensions.v1.json",
            "--json",
        ),
        depends_on=("oh_my_pi_p66_task_job_subagent",),
        reads=(
            "../docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",
            "config/e4_lanes/evidence_inputs/e4_lane_ledger_seed_extensions.v1.json",
        ),
        writes=("../docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",),
    ),
    Stage(
        stage_id="ledger_report",
        phase="ledger",
        label="Build the grouped atomic feature ledger report from the refreshed seed.",
        argv=(PYTHON, "scripts/e4_parity/report_atomic_feature_ledger.py", "--json"),
        depends_on=("ledger_seed",),
        reads=("../docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",),
        writes=("../docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_REPORT.json",),
    ),
    Stage(
        stage_id="catalog_stable_snapshot",
        phase="catalog_stable_snapshot",
        label="Build the stable catalog after every regenerable lane capture.",
        argv=_catalog_argv(
            "docs/conformance/e4_artifact_catalog_stable_snapshot.json",
            excluded_lane_roles=(
                *_CLAIM_DERIVED_LANE_ROLES,
                *_PRIMITIVE_PROJECTION_LANE_ROLES,
            ),
            referenced_static_only=True,
        ),
        depends_on=("ledger_report",),
        writes=("docs/conformance/e4_artifact_catalog_stable_snapshot.json",),
        note="Contains fresh capture outputs but excludes support claims, manifests, node gates, and primitive projection outputs produced later.",
    ),
    Stage(
        stage_id="primitive_projection",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate primitive projection artifacts backed by the current lane packet data.",
        argv=(PYTHON, "scripts/e4_parity/build_primitive_projection.py", "--json"),
        depends_on=("catalog_stable_snapshot",),
        writes=("artifacts/conformance/e4_primitive_projection/*",),
    ),
    Stage(
        stage_id="catalog_claim_binding_snapshot",
        phase="catalog_stable_snapshot",
        label="Rebuild the stable catalog snapshot after promoted lane packet refreshes and before support-claim binding.",
        argv=_catalog_argv(
            "docs/conformance/e4_artifact_catalog.json",
            excluded_lane_roles=_CLAIM_DERIVED_LANE_ROLES,
            referenced_static_only=True,
        ),
        depends_on=("primitive_projection",),
        writes=("docs/conformance/e4_artifact_catalog.json",),
        note="Lane promotion refreshes can rewrite catalog/support artifacts internally; claim generation must bind to the stable catalog snapshot that catalog_full will preserve once claim-derived churn is excluded.",
    ),
    Stage(
        stage_id="support_claim_generation",
        phase="support_claim_generation",
        label="Generate support claims and manifests in manifest-to-claim order.",
        argv=(
            PYTHON,
            "scripts/e4_parity/generate_support_claims.py",
            "--defer-node-gates",
            "--json",
        ),
        depends_on=("catalog_claim_binding_snapshot",),
        writes=(
            "docs/conformance/support_claims/*_support_claim.json",
            "docs/conformance/support_claims/v1_archive/*_support_claim.json",
            "docs/conformance/support_claims/*_evidence_manifest.json",
        ),
        note="Owns canonical support claims and manifests; the generated lane_def reverify stages own their --json-out node-gate reports.",
    ),
    *LANE_DEF_REVERIFY_STAGES,
    Stage(
        stage_id="catalog_full",
        phase="catalog_full",
        label="Build the full lane-artifact catalog after support claims, manifests, and node gates.",
        argv=_catalog_argv(
            "docs/conformance/e4_artifact_catalog_full_snapshot.json",
            referenced_static_only=True,
        ),
        depends_on=(
            (
                LANE_DEF_REVERIFY_STAGES[-1].stage_id
                if LANE_DEF_REVERIFY_STAGES
                else "support_claim_generation"
            ),
        ),
        writes=("docs/conformance/e4_artifact_catalog_full_snapshot.json",),
    ),
    Stage(
        stage_id="ct_scenarios",
        phase="reports",
        label="Run CT scenarios to produce result and rows JSON for matrix sync.",
        argv=(
            PYTHON,
            "scripts/run_ct_scenarios.py",
            "--json-out",
            "artifacts/conformance/ct_scenarios_result_e4_1000.json",
            "--rows-out",
            "artifacts/conformance/ct_scenarios_rows_e4_1000.json",
            "--generated-at-utc",
            PINNED_GENERATED_AT_UTC,
            "--zero-durations",
        ),
        depends_on=("catalog_full",),
        writes=(
            "artifacts/conformance/ct_scenarios_result_e4_1000.json",
            "artifacts/conformance/ct_scenarios_rows_e4_1000.json",
        ),
        blocker="CT is fail-closed while blocking rows still lack executable commands; exit 1 is allowed to refresh downstream fail-closed summaries, but exit 2+ remains fatal because outputs may be stale or missing.",
        allowed_exit_codes=(1,),
    ),
    Stage(
        stage_id="sync_conformance_matrix",
        phase="reports",
        label="Sync generated CT row status into the conformance matrix and summaries.",
        argv=(
            PYTHON,
            "scripts/sync_conformance_matrix_status.py",
            "--rows-json",
            "artifacts/conformance/ct_scenarios_rows_e4_1000.json",
            "--out-csv",
            "artifacts/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv",
            "--summary-json",
            "artifacts/conformance/conformance_matrix_sync_summary_v1.json",
            "--summary-md",
            "artifacts/conformance/conformance_matrix_sync_summary_v1.md",
            "--generated-at-utc",
            PINNED_GENERATED_AT_UTC,
            "--fail-on-summary-not-ok",
        ),
        depends_on=("ct_scenarios",),
        writes=(
            "artifacts/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv",
            "artifacts/conformance/conformance_matrix_sync_summary_v1.json",
            "artifacts/conformance/conformance_matrix_sync_summary_v1.md",
        ),
        blocker="Matrix sync remains fail-closed while its generated summary has ok=false.",
        allowed_exit_codes=(1,),
    ),
    Stage(
        stage_id="final_readiness_packet",
        phase="reports",
        label="Build final readiness packet from regenerated lane, ledger, catalog, CT, and matrix artifacts.",
        argv=(PYTHON, "scripts/e4_parity/build_e4_final_readiness_packet.py", "--json"),
        depends_on=("sync_conformance_matrix",),
        writes=(
            "../docs_tmp/phase_15/BB_E4_COMPATIBILITY_MIGRATION_NOTES.md",
            "../docs_tmp/phase_15/BB_E4_SCORE_SUBLEDGER.json",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json",
            "../docs_tmp/phase_15/BB_E4_CURRENT_BASELINE.json",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_PROGRESS.json",
            "../docs_tmp/phase_15/BB_E4_PRIMITIVE_FAMILY_READINESS_REPORT.json",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_VALIDATION_REPORT.json",
            "../docs_tmp/phase_15/BB_E4_FINAL_READINESS_REPORT.md",
            "../docs_tmp/phase_15/oh_my_pi_p6/BB_E4_OH_MY_PI_P6_TERMINAL_HASH_MANIFEST.json",
            "../docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_SCORECARD.json",
            "../docs_tmp/phase_15/BB_E4_FINAL_ARTIFACT_FRESHNESS_MANIFEST.json",
        ),
        blocker="Final readiness remains fail-closed until CT result, CT rows, and matrix sync summary are pass/all-zero.",
        allowed_exit_codes=(1,),
    ),
    Stage(
        stage_id="catalog_post_report_snapshot",
        phase="catalog_full",
        label="Refresh the full artifact catalog after report-generation stages update claim-layer report hashes.",
        argv=(
            PYTHON,
            "scripts/e4_parity/build_artifact_catalog.py",
            "--schema-version",
            "v2",
            "--output",
            "docs/conformance/e4_artifact_catalog_post_report_snapshot.json",
        ),
        depends_on=("final_readiness_packet",),
        writes=("docs/conformance/e4_artifact_catalog_post_report_snapshot.json",),
    ),
    Stage(
        stage_id="validate_e4_closure",
        phase="validators",
        label="Validate score subledger and primitive-family readiness through the consolidated closure gate.",
        argv=(PYTHON, "scripts/e4_parity/validate_e4_closure.py", "--json"),
        depends_on=("catalog_post_report_snapshot",),
    ),
    Stage(
        stage_id="validate_report_hash_freshness",
        phase="validators",
        label="Validate final report, scorecard, baseline, accepted-claim, and terminal-manifest freshness.",
        argv=(
            PYTHON,
            "scripts/e4_parity/validate_e4_report_hash_freshness.py",
            "--scorecard",
            "../docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_SCORECARD.json",
            "--baseline",
            "../docs_tmp/phase_15/BB_E4_CURRENT_BASELINE.json",
            "--progress",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_PROGRESS.json",
            "--accepted-report",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json",
            "--accepted-validation",
            "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_VALIDATION_REPORT.json",
            "--terminal-manifest",
            "../docs_tmp/phase_15/oh_my_pi_p6/BB_E4_OH_MY_PI_P6_TERMINAL_HASH_MANIFEST.json",
            "--expected-points",
            EXPECTED_POINTS,
            "--expected-claims",
            EXPECTED_CLAIMS,
        ),
        depends_on=("validate_e4_closure",),
    ),
)

_CLAIM_OUTPUT_PREFIX = "docs/conformance/support_claims/"
_NODE_GATE_OUTPUT_PREFIX = "artifacts/conformance/node_gate/"
_PRIMITIVE_PROJECTION_OUTPUT_PREFIX = "artifacts/conformance/e4_primitive_projection/"


def _reverify_argv(lane_def: dict[str, object]) -> tuple[str, ...]:
    command = lane_def.get("reverify_command")
    argv = command.get("argv") if isinstance(command, dict) else None
    return (
        tuple(argv)
        if isinstance(argv, list) and all(isinstance(item, str) for item in argv)
        else ()
    )


def _lane_defs_for_stage(stage: Stage) -> tuple[dict[str, object], ...]:
    if stage.argv[1:2] != ("scripts/e4_parity/run_lane.py",):
        return ()
    stage_name = _arg_value(stage.argv, "--stage")
    if stage_name not in {"capture", "all"}:
        return ()
    lane_id = _arg_value(stage.argv, "--lane")
    if lane_id == "north-star":
        return tuple(
            lane_def
            for lane_def in LANE_DEFS.values()
            if isinstance(lane_def.get("compare"), dict)
            and lane_def["compare"].get("comparator")
            == "north_star_stored_report_replay"
        )
    lane_def = LANE_DEFS.get(str(lane_id))
    return (lane_def,) if lane_def is not None else ()


def _isolated_capture_stage(stage: Stage) -> Stage:
    if not _lane_defs_for_stage(stage):
        return stage
    argv = list(stage.argv)
    stage_index = argv.index("--stage") + 1
    argv[stage_index] = "capture"
    if "--defer-derived-writes" not in argv:
        argv.append("--defer-derived-writes")
    return replace(stage, argv=tuple(argv))


def _lane_capture_reads(stage: Stage) -> tuple[str, ...]:
    lane_defs = _lane_defs_for_stage(stage)
    if not lane_defs:
        return ()
    reads: list[str] = [
        "docs/conformance/e4_lane_inventory.json",
        "config/e4_target_freeze_manifest.yaml",
        "docs/conformance/ct_scenarios_v1.json",
        # Candidate preparation installs the immutable ledger bootstrap in the
        # candidate workspace. The source index is a canonical prerequisite
        # generated before capture; the ledger is regenerated after capture.
        "../docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
        "../docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",
    ]
    for lane_def in lane_defs:
        lane_id = lane_def.get("lane_id")
        if isinstance(lane_id, str):
            reads.append(f"config/e4_lanes/{lane_id}.yaml")
        capture = lane_def.get("capture")
        inputs = capture.get("inputs") if isinstance(capture, dict) else None
        if isinstance(inputs, list):
            reads.extend(value for value in inputs if isinstance(value, str))
        reads.extend(_lane_def_projection_sources(lane_def))
    return tuple(dict.fromkeys(reads))


def _lane_capture_writes(stage: Stage) -> tuple[str, ...] | None:
    lane_defs = _lane_defs_for_stage(stage)
    if not lane_defs:
        return None
    writes: list[str] = []
    for lane_def in lane_defs:
        lane_id = lane_def.get("lane_id")
        if not isinstance(lane_id, str):
            continue
        outputs = tuple(
            output
            for output in _lane_def_output_paths(lane_def, _reverify_argv(lane_def))
            if not output.startswith(
                (
                    _CLAIM_OUTPUT_PREFIX,
                    _NODE_GATE_OUTPUT_PREFIX,
                    _PRIMITIVE_PROJECTION_OUTPUT_PREFIX,
                )
            )
        )
        if not _reverify_argv(lane_def): outputs += tuple(path for path in stage.writes if path.startswith(_NODE_GATE_OUTPUT_PREFIX))
        if outputs:
            writes.extend(outputs)
        else:
            # Capture adapters without enumerated roles publish one logical
            # packet directory. Scratch roots are transaction-local and must
            # never be promoted into the canonical checkout.
            writes.append(f"docs/conformance/e4_target_support/{lane_id}")
    return tuple(dict.fromkeys(writes))


def _declared_stage_writes(stage: Stage) -> tuple[str, ...]:
    lane_writes = _lane_capture_writes(stage)
    return stage.writes if lane_writes is None else lane_writes


def _looks_like_path(value: str) -> bool:
    return (
        value.startswith("../")
        or value.startswith("artifacts/")
        or value.startswith("config/")
        or value.startswith("docs/")
        or value.startswith("scripts/")
        or value.endswith((".csv", ".json", ".md", ".py", ".yaml", ".yml"))
    )


def _argv_declared_paths(argv: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    if len(argv) > 1 and _looks_like_path(argv[1]):
        values.append(argv[1])
    for value in argv[2:]:
        if value.startswith("--"):
            continue
        if _looks_like_path(value):
            values.append(value)
    return tuple(dict.fromkeys(values))


def _workspace_display_path(value: str) -> str:
    return f"../{value}" if value.startswith("docs_tmp/") else value


def _catalog_static_reads() -> tuple[str, ...]:
    report_roles_path = ROOT / "docs" / "conformance" / "e4_report_roles.json"
    reads = [
        "docs/conformance/e4_lane_inventory.json",
        "docs/conformance/e4_report_roles.json",
    ]
    try:
        report_roles = json.loads(report_roles_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return tuple(reads)
    roles = report_roles.get("static_artifact_roles", report_roles.get("roles", []))
    if isinstance(roles, list):
        for role in roles:
            if isinstance(role, dict) and isinstance(role.get("path"), str):
                reads.append(_workspace_display_path(role["path"]))
    return tuple(dict.fromkeys(reads))


def _stage_reads(stage: Stage, previous: dict[str, Stage]) -> tuple[str, ...]:
    reads: list[str] = list(stage.reads)
    reads.extend(
        value for value in _argv_declared_paths(stage.argv) if value not in stage.writes
    )
    reads.extend(_lane_capture_reads(stage))
    if stage.argv[1:2] == ("scripts/e4_parity/build_artifact_catalog.py",):
        reads.extend(_catalog_static_reads())
    if stage.stage_id == "support_claim_generation":
        reads.extend(
            (
                "docs/conformance/e4_lane_inventory.json",
                "docs/conformance/e4_target_support",
                "docs/conformance/support_claims",
            )
        )
    if stage.phase != "lane_def_reverify":
        for dependency in stage.depends_on:
            reads.extend(previous.get(dependency, Stage(dependency, "", "", ())).writes)
    return tuple(dict.fromkeys(reads))


def _with_declared_stage_io(stages: Sequence[Stage]) -> tuple[Stage, ...]:
    declared: list[Stage] = []
    by_id: dict[str, Stage] = {}
    for raw_stage in stages:
        stage = _isolated_capture_stage(raw_stage)
        writes = _declared_stage_writes(stage)
        read_only = stage.read_only or stage.phase == "validators"
        with_writes = replace(stage, writes=writes, read_only=read_only)
        updated = replace(with_writes, reads=_stage_reads(with_writes, by_id))
        declared.append(updated)
        by_id[stage.stage_id] = updated
    return tuple(declared)


STAGES = _with_declared_stage_io(STAGES)


def _is_glob(value: str) -> bool:
    return any(char in value for char in "*?[")


def _display_write_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        try:
            return "../" + resolved.relative_to(WORKSPACE.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()


@lru_cache(maxsize=None)
def _expanded_write_targets(value: str) -> frozenset[str]:
    if value.startswith("../"):
        base = WORKSPACE
        relative = value[3:]
    else:
        base = ROOT
        relative = value
    if _is_glob(value):
        expanded = frozenset(_display_write_path(path) for path in base.glob(relative))
        return expanded or frozenset((value,))
    path = base / relative
    if path.is_dir():
        return frozenset(
            (
                value,
                *(
                    _display_write_path(candidate)
                    for candidate in path.rglob("*")
                    if candidate.is_file()
                ),
            )
        )
    return frozenset((value,))


def _write_patterns_overlap(left: str, right: str) -> bool:
    left_targets = _expanded_write_targets(left)
    right_targets = _expanded_write_targets(right)
    if left_targets & right_targets:
        return True
    if not _is_glob(left) and not _is_glob(right):
        return left == right
    return fnmatch.fnmatchcase(left, right) or fnmatch.fnmatchcase(right, left)


def _lane_capture_outputs(stages: Sequence[Stage]) -> tuple[tuple[str, str], ...]:
    outputs: list[tuple[str, str]] = []
    for stage in stages:
        for lane_def in _lane_defs_for_stage(stage):
            for output in _lane_def_output_paths(lane_def, _reverify_argv(lane_def)):
                outputs.append((stage.stage_id, output))
    return tuple(outputs)


def validate_stage_graph(stages: Sequence[Stage] = STAGES) -> None:
    seen: set[str] = set()
    for stage in stages:
        if stage.stage_id in seen:
            raise ValueError(f"duplicate stage id: {stage.stage_id}")
        missing_or_late = [dep for dep in stage.depends_on if dep not in seen]
        if missing_or_late:
            raise ValueError(
                f"stage {stage.stage_id} depends on missing or later stages: {missing_or_late}"
            )
        seen.add(stage.stage_id)
    write_owners: list[tuple[str, str]] = []
    for stage in stages:
        if not stage.writes and not stage.read_only:
            raise ValueError(
                f"stage {stage.stage_id} declares no writes but is not read_only"
            )
        if stage.read_only and stage.writes:
            raise ValueError(
                f"read_only stage {stage.stage_id} must not declare writes"
            )
        for write in stage.writes:
            for owner, owned_write in write_owners:
                if owner != stage.stage_id and _write_patterns_overlap(
                    write, owned_write
                ):
                    raise ValueError(
                        f"write path {write} is declared by both {owner} and {stage.stage_id}"
                    )
            write_owners.append((stage.stage_id, write))
    if stages is STAGES:
        for capture_stage_id, output in _lane_capture_outputs(stages):
            owners = {
                stage.stage_id
                for stage in stages
                if any(_write_patterns_overlap(output, write) for write in stage.writes)
            }
            if len(owners) != 1:
                raise ValueError(
                    f"lane capture output {output} from {capture_stage_id} must have exactly one declared owner; "
                    f"found {sorted(owners)}"
                )
        catalog_stages = [
            stage
            for stage in stages
            if stage.argv[1:2] == ("scripts/e4_parity/build_artifact_catalog.py",)
        ]
        if [stage.stage_id for stage in catalog_stages] != [
            "catalog_stable_snapshot",
            "catalog_claim_binding_snapshot",
            "catalog_full",
            "catalog_post_report_snapshot",
        ]:
            raise ValueError(
                "regeneration DAG must include stable, claim-binding, full, and post-report catalog stages"
            )
        claim_stages = [
            stage
            for stage in stages
            if stage.argv[1:2] == ("scripts/e4_parity/generate_support_claims.py",)
        ]
        if [stage.stage_id for stage in claim_stages] != ["support_claim_generation"]:
            raise ValueError(
                "regeneration DAG must include support_claim_generation stage"
            )
    phases = [stage.phase for stage in stages]
    if "validators" in phases and phases.index("validators") < phases.index("reports"):
        raise ValueError("validator stages must not precede report stages")


def _generated_stage_count(stages: Sequence[Stage]) -> int:
    return sum(1 for stage in stages if stage.stage_id.startswith("lane_def_reverify_"))


def _plan_invariants() -> list[str]:
    return [
        "stage_ids_unique",
        "dependencies_precede_consumers",
        "non_read_only_stages_declare_writes",
        "read_only_stages_declare_no_writes",
        "write_paths_have_single_owner",
        "lane_capture_outputs_have_declared_owner",
        "catalog_has_one_canonical_claim_binding_owner",
        "validators_follow_report_stages",
    ]


def plan_dict(
    stages: Sequence[Stage] = STAGES, *, python: str = sys.executable
) -> dict[str, object]:
    generated_stage_count = _generated_stage_count(stages)
    return {
        "schema_version": "bb.e4.regen_plan.v1",
        "plan_id": "e4_regen_plan",
        "generated_at_utc": PINNED_GENERATED_AT_UTC,
        "explicit_stage_count": len(stages) - generated_stage_count,
        "generated_stage_count": generated_stage_count,
        "stage_count": len(stages),
        "invariants": _plan_invariants(),
        "repo_root": str(ROOT),
        "workspace": str(WORKSPACE),
        "stages": [
            {
                **asdict(stage),
                "argv": stage.expanded_argv(python),
            }
            for stage in stages
        ],
    }


def print_explain(
    stages: Sequence[Stage] = STAGES, *, python: str = sys.executable
) -> None:
    print("E4 evidence regeneration DAG")
    print(
        "Topological order: immutable ledger bootstrap + source index -> lane captures -> ledger -> ledger report -> catalog_stable_snapshot -> legacy generators -> support_claim_generation -> lane reverify -> catalog_full -> reports -> catalog_post_report_snapshot -> validators"
    )
    for index, stage in enumerate(stages, start=1):
        deps = ", ".join(stage.depends_on) if stage.depends_on else "<root>"
        print(f"{index:02d}. {stage.stage_id} [{stage.phase}]")
        print(f"    depends_on: {deps}")
        print(f"    argv: {' '.join(stage.expanded_argv(python))}")
        if stage.reads:
            print(f"    reads: {', '.join(stage.reads)}")
        if stage.writes:
            print(f"    writes: {', '.join(stage.writes)}")
        if stage.read_only:
            print("    read_only: true")
        if stage.note:
            print(f"    note: {stage.note}")
        if stage.blocker:
            print(f"    blocker: {stage.blocker}")


@dataclass(frozen=True)
class StageResult:
    stage_id: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def _json_object_from_output(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _gate_error_bullets(result: StageResult) -> list[str]:
    payload = _json_object_from_output(result.stdout) or _json_object_from_output(
        result.stderr
    )
    gate_errors = payload.get("gate_errors") if isinstance(payload, dict) else None
    if not isinstance(gate_errors, list):
        return []
    bullets: list[str] = []
    for error in gate_errors:
        if not isinstance(error, dict):
            continue
        klass = str(error.get("klass", "semantic")).upper()
        code = str(error.get("code", "validation_failed"))
        remedy = str(error.get("remedy", "Fix the failing gate and rerun."))
        message = str(error.get("message", ""))
        bullets.append(f"[{klass}] {code}: {message} remedy={remedy}")
    return bullets


def _run_stage_sequence(
    stages: Sequence[Stage],
    *,
    python: str,
    execution_root: Path,
    workspace_root: Path | None = None,
) -> tuple[int, list[StageResult]]:
    results: list[StageResult] = []
    blocked_exit_code: int | None = None
    for stage in stages:
        if blocked_exit_code is not None and stage.phase == "validators":
            print(
                "STOPPING before validators because an earlier report stage is fail-closed",
                file=sys.stderr,
            )
            return blocked_exit_code, results
        argv = stage.expanded_argv(
            python,
            resolve_execution_values=True,
            workspace_root=workspace_root,
        )
        print(f"==> {stage.stage_id}: {' '.join(argv)}", flush=True)
        start = time.perf_counter()
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(execution_root)
            if not existing_pythonpath
            else f"{execution_root}{os.pathsep}{existing_pythonpath}"
        )
        if workspace_root is not None:
            env["BB_WORKSPACE_ROOT"] = str(workspace_root)
        completed = subprocess.run(
            argv, cwd=execution_root, text=True, capture_output=True, env=env
        )
        duration_seconds = round(time.perf_counter() - start, 6)
        result = StageResult(
            stage_id=stage.stage_id,
            argv=argv,
            returncode=int(completed.returncode),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_seconds=duration_seconds,
        )
        results.append(result)
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=sys.stderr,
            )
        print(
            f"<== {stage.stage_id}: exit={result.returncode} "
            f"duration_seconds={result.duration_seconds:.3f}",
            flush=True,
        )
        if result.returncode != 0:
            if result.returncode in stage.allowed_exit_codes:
                blocked_exit_code = blocked_exit_code or result.returncode
                print(
                    f"CONTINUING blocked stage {stage.stage_id} exit={result.returncode}",
                    file=sys.stderr,
                )
                if stage.blocker:
                    print(f"BLOCKER: {stage.blocker}", file=sys.stderr)
                for bullet in _gate_error_bullets(result):
                    print(f"GATE_ERROR {bullet}", file=sys.stderr)
                continue
            print(
                f"FAILED stage {stage.stage_id} exit={result.returncode}",
                file=sys.stderr,
            )
            if stage.blocker:
                print(f"BLOCKER: {stage.blocker}", file=sys.stderr)
            for bullet in _gate_error_bullets(result):
                print(f"GATE_ERROR {bullet}", file=sys.stderr)
            return result.returncode, results
    return blocked_exit_code or 0, results


def _copy_path(
    source: Path,
    destination: Path,
    *,
    symlink_boundary: Path | None = None,
    dereference_symlinks: bool = False,
) -> None:
    if source.is_symlink():
        target = os.readlink(source)
        resolved_target = source.resolve(strict=dereference_symlinks)
        if symlink_boundary is not None and not resolved_target.is_relative_to(
            symlink_boundary.resolve()
        ):
            raise ValueError(
                f"candidate symlink escapes scratch workspace: {source} -> {target}"
            )
        if dereference_symlinks:
            if resolved_target.is_dir():
                shutil.copytree(
                    resolved_target,
                    destination,
                    dirs_exist_ok=True,
                    symlinks=True,
                )
            elif resolved_target.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved_target, destination)
            else:
                raise FileNotFoundError(
                    f"candidate symlink target does not exist: {source}"
                )
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target)
    elif source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _candidate_path(candidate_root: Path, accepted_path: Path) -> Path:
    accepted_absolute = Path(os.path.abspath(accepted_path))
    root_absolute = Path(os.path.abspath(ROOT))
    workspace_absolute = Path(os.path.abspath(WORKSPACE))
    try:
        return candidate_root / accepted_absolute.relative_to(root_absolute)
    except ValueError:
        return candidate_root.parent / accepted_absolute.relative_to(workspace_absolute)


def _tracked_checkout_paths() -> tuple[tuple[str, str, Path], ...]:
    output = subprocess.check_output(
        ("git", "ls-files", "--stage", "-z"),
        cwd=ROOT,
    )
    entries: list[tuple[str, str, Path]] = []
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        metadata, raw_path = raw_entry.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise RuntimeError(f"candidate checkout has unresolved index stage {stage}")
        entries.append((mode, object_id, Path(raw_path.decode("utf-8"))))
    return tuple(entries)


def _indexed_blob(object_id: str) -> bytes:
    return subprocess.check_output(("git", "cat-file", "blob", object_id), cwd=ROOT)


def _copy_tracked_checkout(candidate_root: Path) -> None:
    candidate_root.mkdir(parents=True)
    for mode, object_id, relative_path in _tracked_checkout_paths():
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"git returned unsafe tracked path: {relative_path}")
        if mode == "160000":
            raise RuntimeError(
                f"candidate checkout cannot materialize tracked gitlink: {relative_path}"
            )
        destination = candidate_root / relative_path
        if mode == "120000":
            target = _indexed_blob(object_id).decode("utf-8")
            resolved_target = (destination.parent / target).resolve(strict=False)
            if not resolved_target.is_relative_to(candidate_root.parent.resolve()):
                raise ValueError(
                    f"candidate symlink escapes scratch workspace: {destination} -> {target}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(target)
        elif mode in {"100644", "100755"}:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_indexed_blob(object_id))
            if mode == "100755":
                destination.chmod(destination.stat().st_mode | 0o111)
        else:
            raise RuntimeError(f"unsupported tracked mode {mode}: {relative_path}")


def _prepare_candidate_root(candidate_root: Path, stages: Sequence[Stage]) -> None:
    """Materialize exactly the staged checkout plus its verified immutable inputs."""

    _copy_tracked_checkout(candidate_root)
    bundle_root = candidate_root / "config/e4_lanes/evidence_inputs"
    provision_immutable_inputs(
        bundle_root / "e4_immutable_inputs.v1.zip",
        bundle_root / "e4_immutable_inputs.v1.manifest.json",
        repo_root=candidate_root,
        workspace_root=candidate_root.parent,
    )
    if not any(_lane_defs_for_stage(stage) for stage in stages):
        return

    bootstrap = bundle_root / "e4_regen_bootstrap/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    if not bootstrap.is_file():
        raise FileNotFoundError(
            f"missing immutable regeneration bootstrap input: {bootstrap}"
        )
    _copy_path(
        bootstrap,
        candidate_root.parent
        / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
    )


def _accepted_path(candidate_root: Path, candidate_path: Path) -> Path:
    """Map a candidate path lexically, without dereferencing symlinks."""

    candidate_absolute = Path(os.path.abspath(candidate_path))
    root_absolute = Path(os.path.abspath(candidate_root))
    workspace_absolute = Path(os.path.abspath(candidate_root.parent))
    try:
        return ROOT / candidate_absolute.relative_to(root_absolute)
    except ValueError:
        return WORKSPACE / candidate_absolute.relative_to(workspace_absolute)


def _write_set_entries(
    candidate_root: Path,
    stages: Sequence[Stage],
) -> list[tuple[Path | None, Path]]:
    entries: dict[Path, Path | None] = {}
    for pattern in dict.fromkeys(
        value for stage in stages for value in _declared_stage_writes(stage)
    ):
        candidate_pattern = candidate_root / pattern
        accepted_pattern = ROOT / pattern
        if _is_glob(pattern):
            candidate_matches = set(
                candidate_pattern.parent.glob(candidate_pattern.name)
            )
            accepted_matches = set(accepted_pattern.parent.glob(accepted_pattern.name))
            for source in candidate_matches:
                destination = _accepted_path(candidate_root, source)
                entries[destination] = source
            for destination in accepted_matches:
                candidate = _candidate_path(candidate_root, destination)
                entries.setdefault(
                    destination, candidate if candidate.exists() else None
                )
            continue
        source = candidate_pattern
        destination = accepted_pattern
        entries[destination] = source if source.exists() else None
    return [
        (source, destination)
        for destination, source in sorted(
            entries.items(), key=lambda item: str(item[0])
        )
    ]


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _rollback_promoted_write_set(applied: Sequence[tuple[Path, Path | None]]) -> None:
    for destination, backup in reversed(applied):
        _remove_path(destination)
        if backup is not None and backup.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup.replace(destination)


def _promote_write_set(
    candidate_root: Path,
    stages: Sequence[Stage],
    transaction_root: Path,
) -> list[tuple[Path, Path | None]]:
    entries = _write_set_entries(candidate_root, stages)
    staged_root = transaction_root / "staged"
    backup_root = transaction_root / "backup"
    staged: list[tuple[Path | None, Path, Path]] = []
    for index, (source, destination) in enumerate(entries):
        staged_path: Path | None = None
        if source is not None:
            staged_path = staged_root / str(index)
            _copy_path(
                source,
                staged_path,
                symlink_boundary=candidate_root.parent,
                dereference_symlinks=True,
            )
        staged.append((staged_path, destination, backup_root / str(index)))

    applied: list[tuple[Path, Path | None]] = []
    try:
        for staged_path, destination, backup_path in staged:
            backup: Path | None = None
            if destination.exists() or destination.is_symlink():
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(backup_path)
                backup = backup_path
            applied.append((destination, backup))
            if staged_path is None:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged_path.replace(destination)
    except Exception:
        _rollback_promoted_write_set(applied)
        raise
    return applied


def _run_regeneration_transaction(
    candidate_stages: Sequence[Stage],
    canonical_rebind_stages: Sequence[Stage],
    final_c4_stages: Sequence[Stage],
    *,
    python: str = sys.executable,
) -> tuple[int, list[StageResult]]:
    results: list[StageResult] = []
    with tempfile.TemporaryDirectory(
        prefix=".e4-candidate-", dir=WORKSPACE
    ) as temp_name:
        candidate_root = Path(temp_name) / "repo"
        _prepare_candidate_root(candidate_root, candidate_stages)
        candidate_code, candidate_results = _run_stage_sequence(
            candidate_stages,
            python=python,
            execution_root=candidate_root,
            workspace_root=candidate_root.parent,
        )
        results.extend(candidate_results)
        if candidate_code != 0:
            return candidate_code, results

        with tempfile.TemporaryDirectory(
            prefix=".e4-promotion-", dir=WORKSPACE
        ) as promotion_name:
            try:
                applied = _promote_write_set(
                    candidate_root,
                    candidate_stages,
                    Path(promotion_name),
                )
            except Exception as exc:
                print(f"FAILED atomic candidate promotion: {exc}", file=sys.stderr)
                return 1, results

            try:
                rebind_code, rebind_results = _run_stage_sequence(
                    canonical_rebind_stages,
                    python=python,
                    execution_root=ROOT,
                )
                results.extend(rebind_results)
                if rebind_code != 0:
                    _rollback_promoted_write_set(applied)
                    return rebind_code, results
                final_code, final_results = _run_stage_sequence(
                    final_c4_stages,
                    python=python,
                    execution_root=ROOT,
                )
                results.extend(final_results)
                if final_code != 0:
                    _rollback_promoted_write_set(applied)
                return final_code, results
            except Exception:
                _rollback_promoted_write_set(applied)
                raise


def _candidate_stage(stage: Stage) -> Stage:
    if stage.stage_id != "north_star_proof_packets":
        return stage
    argv = list(stage.argv)
    stage_index = argv.index("--stage") + 1
    argv[stage_index] = "capture"
    if "--defer-derived-writes" not in argv:
        argv.append("--defer-derived-writes")
    return replace(stage, argv=tuple(argv))


def _canonical_transaction_groups(
    stages: Sequence[Stage],
) -> tuple[tuple[Stage, ...], tuple[Stage, ...], tuple[Stage, ...]]:
    candidate_stages = tuple(_candidate_stage(stage) for stage in stages)
    rebind_start = next(
        index
        for index, stage in enumerate(stages)
        if stage.stage_id == "catalog_claim_binding_snapshot"
    )
    final_start = next(
        index for index, stage in enumerate(stages) if stage.phase == "validators"
    )
    canonical_rebind_stages = tuple(stages[rebind_start:final_start])
    final_c4_stages = tuple(stages[final_start:])
    return candidate_stages, canonical_rebind_stages, final_c4_stages


def run_pipeline(
    stages: Sequence[Stage] = STAGES, *, python: str = sys.executable
) -> tuple[int, list[StageResult]]:
    validate_stage_graph(stages)
    stage_ids = {stage.stage_id for stage in stages}
    if {
        "north_star_proof_packets",
        "catalog_claim_binding_snapshot",
        "validate_e4_closure",
    }.issubset(stage_ids):
        return _run_regeneration_transaction(
            *_canonical_transaction_groups(stages),
            python=python,
        )
    return _run_stage_sequence(stages, python=python, execution_root=ROOT)


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(
        "regenerate_evidence.py is the importable regeneration DAG implementation, not an executable front door.\n"
        "Use scripts/e4_parity/regen.py run, explain, fixed-point, or classify.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
