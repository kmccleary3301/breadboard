#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs

try:
    from scripts.e4_parity import build_e4_final_readiness_packet as final_readiness
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import build_e4_final_readiness_packet as final_readiness

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
PYTHON = "{python}"

PINNED_GENERATED_AT_UTC = "2026-07-03T00:00:00Z"

@dataclass(frozen=True)
class Stage:
    """One executable node in the E4 evidence regeneration DAG."""

    stage_id: str
    phase: str
    label: str
    argv: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    note: str | None = None
    blocker: str | None = None
    allowed_exit_codes: tuple[int, ...] = ()

    def expanded_argv(self, python: str) -> list[str]:
        return [python if part == PYTHON else part for part in self.argv]


def _json_out_from_argv(argv: Sequence[str]) -> str | None:
    for index, arg in enumerate(argv):
        if arg == "--json-out" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--json-out="):
            return arg.split("=", 1)[1]
    return None


def _lane_def_reverify_stages(*, depends_on: str) -> tuple[Stage, ...]:
    lane_defs = load_lane_defs(DEFAULT_LANE_DEF_DIR)
    stages: list[Stage] = []
    previous = depends_on
    for lane_id in sorted(lane_defs):
        lane_def = lane_defs[lane_id]
        command = lane_def.get("reverify_command")
        argv = command.get("argv") if isinstance(command, dict) else None
        if not (isinstance(argv, list) and all(isinstance(item, str) for item in argv)):
            continue
        json_out = _json_out_from_argv(argv)
        stage = Stage(
            stage_id=f"lane_def_reverify_{lane_id}",
            phase="lane_def_reverify",
            label=f"Reverify lane_def-derived accepted lane {lane_id}.",
            argv=tuple(argv),
            depends_on=(previous,),
            writes=(json_out,) if json_out is not None else (),
            note="Derived from config/e4_lanes/*.yaml reverify_command, not duplicated in regenerate_evidence.py.",
        )
        stages.append(stage)
        previous = stage.stage_id
    return tuple(stages)

LANE_DEF_REVERIFY_STAGES = _lane_def_reverify_stages(depends_on="catalog_full")



STAGES: tuple[Stage, ...] = (
    Stage(
        stage_id="source_index",
        phase="lane_artifacts",
        label="Build deterministic source/path index used by the ledger seed.",
        argv=(PYTHON, "scripts/e4_parity/build_source_index.py", "--json"),
        writes=("../docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",),
    ),
    Stage(
        stage_id="ledger_seed",
        phase="ledger",
        label="Seed the base atomic feature ledger from indexed source refs.",
        argv=(PYTHON, "scripts/e4_parity/seed_atomic_feature_ledger.py", "--json"),
        depends_on=("source_index",),
        writes=("../docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",),
    ),
    Stage(
        stage_id="north_star_proof_packets",
        phase="claims_manifests_node_gates_ws_j",
        label="Regenerate WS-J north-star proof packets through the lane runner.",
        argv=(PYTHON, "scripts/e4_parity/run_lane.py", "--lane", "north-star", "--stage", "all", "--promote-accepted", "--json"),
        depends_on=("ledger_seed",),
        writes=(
            "docs/conformance/support_claims/*north_star*_c4_support_claim.json",
            "docs/conformance/support_claims/*north_star*_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_north_star_*_c4_chain.json",
            "docs/conformance/e4_target_support/*north_star*",
        ),
    ),
    Stage(
        stage_id="catalog_stable_snapshot",
        phase="catalog_stable_snapshot",
        label="Build the stable artifact catalog snapshot before regenerated claim artifacts.",
        argv=(PYTHON, "scripts/e4_parity/build_artifact_catalog.py", "--schema-version", "v2"),
        depends_on=("north_star_proof_packets",),
        writes=("docs/conformance/e4_artifact_catalog.json",),
        note="B2 freeze point: downstream claim generation binds against the stable snapshot before generated support-claim/manifest/node-gate churn is introduced.",
    ),
    Stage(
        stage_id="p3_1_effective_config_graph",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate P3.1 effective-config-graph claim, manifest, ledger row, and node gate.",
        argv=(PYTHON, "scripts/e4_parity/build_oh_my_pi_p3_1_effective_config_graph.py", "--json"),
        depends_on=("catalog_stable_snapshot",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p3_1_effective_config_graph_compiler_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p3_1_effective_config_graph_compiler_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p31_effective_config_graph_compiler_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="p3_remaining_helper_runtime",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate P3.2-P3.8 helper/runtime claims, manifests, ledger rows, and node gates.",
        argv=(PYTHON, "scripts/e4_parity/build_oh_my_pi_p3_remaining_helper_runtime.py", "--json"),
        depends_on=("p3_1_effective_config_graph",),
        writes=("docs/conformance/support_claims/*p3_*", "artifacts/conformance/node_gate/ct_p3_oh_my_pi_p3*_c4_chain.json"),
    ),
    Stage(
        stage_id="pi_p5_cli_config_context_tool_surface",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate Pi P5 L1 CLI/config/context/tool-surface claim, manifest, ledger row, and node gate.",
        argv=(PYTHON, "scripts/e4_parity/build_pi_p5_cli_config_context_tool_surface.py", "--json"),
        depends_on=("p3_remaining_helper_runtime",),
        writes=(
            "docs/conformance/support_claims/pi_p5_l1_cli_config_context_tool_surface_v1_c4_support_claim.json",
            "docs/conformance/support_claims/pi_p5_l1_cli_config_context_tool_surface_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_pi_p5_l1_cli_config_context_tool_surface_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="pi_p5_extension_session_residual",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate Pi P5 residual extension/session claim, manifest, ledger row, and node gate.",
        argv=(PYTHON, "scripts/e4_parity/build_pi_p5_extension_session_residual.py", "--json"),
        depends_on=("pi_p5_cli_config_context_tool_surface",),
        writes=(
            "docs/conformance/support_claims/pi_p5_l2_extension_session_residual_v1_c4_support_claim.json",
            "docs/conformance/support_claims/pi_p5_l2_extension_session_residual_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_pi_p5_l2_extension_session_residual_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="oh_my_pi_l5_memory_compaction",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate Oh-My-Pi P6 L5 memory/compaction claim, manifest, ledger row, and node gate.",
        argv=(PYTHON, "scripts/e4_parity/build_oh_my_pi_l5_memory_compaction.py", "--json"),
        depends_on=("pi_p5_extension_session_residual",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p6_0_l5_memory_compaction_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p6_0_l5_memory_compaction_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_oh_my_pi_p6_0_l5_memory_compaction_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="oh_my_pi_l6_tui_projection",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate Oh-My-Pi P6 L6 TUI/projection claim, manifest, ledger row, and node gate.",
        argv=(PYTHON, "scripts/e4_parity/build_oh_my_pi_l6_tui_projection.py", "--json"),
        depends_on=("oh_my_pi_l5_memory_compaction",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p6_0_l6_tui_projection_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p6_0_l6_tui_projection_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_oh_my_pi_p6_0_l6_tui_projection_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="oh_my_pi_p66_task_job_subagent",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate Oh-My-Pi P6.6 task/job/subagent claim, manifest, ledger row, and node gate from canonical captures.",
        argv=(PYTHON, "scripts/e4_parity/build_oh_my_pi_p6_6_task_job_subagent.py", "--json"),
        depends_on=("oh_my_pi_l6_tui_projection",),
        writes=(
            "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v1_c4_support_claim.json",
            "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v1_c4_evidence_manifest.json",
            "artifacts/conformance/node_gate/ct_oh_my_pi_p6_6_task_job_subagent_c4_chain.json",
        ),
    ),
    Stage(
        stage_id="primitive_projection",
        phase="claims_manifests_node_gates_legacy_v1",
        label="Regenerate primitive projection artifacts backed by the current lane packet data.",
        argv=(PYTHON, "scripts/e4_parity/build_primitive_projection.py", "--json"),
        depends_on=("oh_my_pi_p66_task_job_subagent",),
        writes=("docs/conformance/e4_primitive_projection/*",),
    ),
    Stage(
        stage_id="support_claim_generation",
        phase="support_claim_generation",
        label="Generate support claims, manifests, and node gates in manifest-to-claim order.",
        argv=(PYTHON, "scripts/e4_parity/generate_support_claims.py", "--json"),
        depends_on=("primitive_projection",),
        writes=(
            "docs/conformance/support_claims/*_support_claim.json",
            "docs/conformance/support_claims/v1_archive/*_support_claim.json",
            "docs/conformance/support_claims/*_evidence_manifest.json",
            "artifacts/conformance/node_gate/*.json",
        ),
        note="Replaces the old support_claim_v2_generation/support_claim_v2_rebinding pair; generate_support_claims.py owns the manifest -> claim -> node-gate ordering internally.",
    ),
    Stage(
        stage_id="catalog_full",
        phase="catalog_full",
        label="Build the full artifact catalog after regenerated support claims, manifests, and node gates.",
        argv=(PYTHON, "scripts/e4_parity/build_artifact_catalog.py", "--schema-version", "v2"),
        depends_on=("support_claim_generation",),
        writes=("docs/conformance/e4_artifact_catalog.json",),
    ),
    *LANE_DEF_REVERIFY_STAGES,
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
        depends_on=((LANE_DEF_REVERIFY_STAGES[-1].stage_id if LANE_DEF_REVERIFY_STAGES else "catalog_full"),),
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
        ),
        depends_on=("ct_scenarios",),
        writes=(
            "artifacts/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv",
            "artifacts/conformance/conformance_matrix_sync_summary_v1.json",
            "artifacts/conformance/conformance_matrix_sync_summary_v1.md",
        ),
        blocker="Matrix sync remains fail-closed while blocking_not_implemented_rows is nonzero.",
    ),
    Stage(
        stage_id="final_readiness_packet",
        phase="reports",
        label="Build final readiness packet from regenerated lane, ledger, catalog, CT, and matrix artifacts.",
        argv=(PYTHON, "scripts/e4_parity/build_e4_final_readiness_packet.py", "--json"),
        depends_on=("sync_conformance_matrix",),
        writes=(
            "../docs_tmp/phase_15/BB_E4_FINAL_READINESS_REPORT.md",
            "../docs_tmp/phase_15/BB_E4_FINAL_ARTIFACT_FRESHNESS_MANIFEST.json",
            "../docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_SCORECARD.json",
        ),
        blocker="Final readiness remains fail-closed until CT result, CT rows, and matrix sync summary are pass/all-zero.",
        allowed_exit_codes=(1,),
    ),
    Stage(
        stage_id="validate_score_subledger",
        phase="validators",
        label="Validate score subledger and accepted support-claim score rows.",
        argv=(PYTHON, "scripts/e4_parity/validate_e4_score_subledger.py", "--json"),
        depends_on=("final_readiness_packet",),
    ),
    Stage(
        stage_id="validate_primitive_readiness",
        phase="validators",
        label="Validate primitive-family readiness without writing refreshed reports.",
        argv=(PYTHON, "scripts/e4_parity/validate_e4_primitive_readiness.py", "--no-write"),
        depends_on=("validate_score_subledger",),
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
            str(final_readiness.expected_points()),
            "--expected-claims",
            str(final_readiness._expected_target_support_claims()),
        ),
        depends_on=("validate_primitive_readiness",),
    ),
)


def validate_stage_graph(stages: Sequence[Stage] = STAGES) -> None:
    seen: set[str] = set()
    for stage in stages:
        if stage.stage_id in seen:
            raise ValueError(f"duplicate stage id: {stage.stage_id}")
        missing_or_late = [dep for dep in stage.depends_on if dep not in seen]
        if missing_or_late:
            raise ValueError(f"stage {stage.stage_id} depends on missing or later stages: {missing_or_late}")
        seen.add(stage.stage_id)
    if stages is STAGES:
        catalog_stages = [stage for stage in stages if stage.argv[1:2] == ("scripts/e4_parity/build_artifact_catalog.py",)]
        if [stage.stage_id for stage in catalog_stages] != ["catalog_stable_snapshot", "catalog_full"]:
            raise ValueError("regeneration DAG must include exactly catalog_stable_snapshot then catalog_full catalog stages")
        claim_stages = [stage for stage in stages if stage.argv[1:2] == ("scripts/e4_parity/generate_support_claims.py",)]
        if [stage.stage_id for stage in claim_stages] != ["support_claim_generation"]:
            raise ValueError("regeneration DAG must include exactly one support_claim_generation stage")
    phases = [stage.phase for stage in stages]
    if "validators" in phases and phases.index("validators") < phases.index("reports"):
        raise ValueError("validator stages must not precede report stages")


def plan_dict(stages: Sequence[Stage] = STAGES, *, python: str = sys.executable) -> dict[str, object]:
    return {
        "repo_root": str(ROOT),
        "workspace": str(WORKSPACE),
        "stage_count": len(stages),
        "stages": [
            {
                **asdict(stage),
                "argv": stage.expanded_argv(python),
            }
            for stage in stages
        ],
    }


def print_explain(stages: Sequence[Stage] = STAGES, *, python: str = sys.executable) -> None:
    print("E4 evidence regeneration DAG")
    print("Topological order: lane/source artifacts -> ledger -> catalog_stable_snapshot -> legacy generators -> support_claim_generation -> catalog_full -> lane reverify -> reports -> validators")
    for index, stage in enumerate(stages, start=1):
        deps = ", ".join(stage.depends_on) if stage.depends_on else "<root>"
        print(f"{index:02d}. {stage.stage_id} [{stage.phase}]")
        print(f"    depends_on: {deps}")
        print(f"    argv: {' '.join(stage.expanded_argv(python))}")
        if stage.writes:
            print(f"    writes: {', '.join(stage.writes)}")
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
    payload = _json_object_from_output(result.stdout) or _json_object_from_output(result.stderr)
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



def run_pipeline(stages: Sequence[Stage] = STAGES, *, python: str = sys.executable) -> tuple[int, list[StageResult]]:
    validate_stage_graph(stages)
    results: list[StageResult] = []
    blocked_exit_code: int | None = None
    for stage in stages:
        if blocked_exit_code is not None and stage.phase == "validators":
            print("STOPPING before validators because an earlier report stage is fail-closed", file=sys.stderr)
            return blocked_exit_code, results
        argv = stage.expanded_argv(python)
        print(f"==> {stage.stage_id}: {' '.join(argv)}", flush=True)
        start = time.perf_counter()
        completed = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
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
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
        print(f"<== {stage.stage_id}: exit={result.returncode} duration_seconds={result.duration_seconds:.3f}", flush=True)
        if result.returncode != 0:
            if result.returncode in stage.allowed_exit_codes:
                blocked_exit_code = blocked_exit_code or result.returncode
                print(f"CONTINUING blocked stage {stage.stage_id} exit={result.returncode}", file=sys.stderr)
                if stage.blocker:
                    print(f"BLOCKER: {stage.blocker}", file=sys.stderr)
                for bullet in _gate_error_bullets(result):
                    print(f"GATE_ERROR {bullet}", file=sys.stderr)
                continue
            print(f"FAILED stage {stage.stage_id} exit={result.returncode}", file=sys.stderr)
            if stage.blocker:
                print(f"BLOCKER: {stage.blocker}", file=sys.stderr)
            for bullet in _gate_error_bullets(result):
                print(f"GATE_ERROR {bullet}", file=sys.stderr)
            return result.returncode, results
    return blocked_exit_code or 0, results


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regenerate E4 evidence in explicit topological order.")
    parser.add_argument("--explain", action="store_true", help="print the regeneration DAG and exit")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the DAG without executing any stage")
    parser.add_argument("--check-plan", action="store_true", help="validate the stage graph without executing any stage")
    parser.add_argument("--json", action="store_true", help="print machine-readable plan or run summary")
    parser.add_argument("--python", default=sys.executable, help="Python executable used for stage commands")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        validate_stage_graph(STAGES)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"plan invalid: {exc}", file=sys.stderr)
        return 2

    if args.json and (args.explain or args.dry_run or args.check_plan):
        payload = plan_dict(STAGES, python=args.python)
        payload["ok"] = True
        payload["dry_run"] = bool(args.dry_run)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.explain:
        print_explain(STAGES, python=args.python)
        return 0

    if args.dry_run:
        print("DRY-RUN: no regeneration commands executed.")
        print_explain(STAGES, python=args.python)
        return 0

    if args.check_plan:
        print(f"plan ok: {len(STAGES)} stages")
        return 0

    code, results = run_pipeline(STAGES, python=args.python)
    total_duration_seconds = round(sum(result.duration_seconds for result in results), 6)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": code == 0,
                    "exit_code": code,
                    "completed_stage_count": len(results),
                    "total_duration_seconds": total_duration_seconds,
                    "results": [asdict(result) for result in results],
                },
                indent=2,
                sort_keys=True,
            )
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
