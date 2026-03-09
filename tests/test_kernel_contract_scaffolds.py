from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_kernel_contract_fixtures import validate_kernel_contract_fixtures


def test_kernel_contract_scaffolds_validate() -> None:
    errors = validate_kernel_contract_fixtures()
    assert not errors, "\n".join(errors)


def test_kernel_semantics_directory_tracks_expected_v1_dossiers() -> None:
    root = Path(__file__).resolve().parents[1]
    semantics_dir = root / "docs" / "contracts" / "kernel" / "semantics"
    expected = {
        "kernel_event_v1.md",
        "session_transcript_v1.md",
        "tool_lifecycle_and_render_v1.md",
        "run_request_and_context_v1.md",
        "provider_exchange_v1.md",
        "permission_and_guardrails_v1.md",
        "middleware_lifecycle_v1.md",
        "task_and_subagent_v1.md",
        "checkpoint_and_longrun_v1.md",
        "execution_capability_and_placement_v1.md",
        "sandbox_envelopes_v1.md",
        "distributed_task_descriptor_v1.md",
        "durable_orchestration_and_resume_v1.md",
        "transcript_continuation_patch_v1.md",
        "unsupported_case_v1.md",
        "replay_session_v1.md",
        "conformance_evidence_v1.md",
    }
    present = {path.name for path in semantics_dir.glob("*.md") if path.name != "README.md"}
    missing = sorted(expected - present)
    assert not missing, f"Missing semantic dossiers: {missing}"


def test_kernel_example_schema_files_parse() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "contracts" / "kernel" / "schemas"
    example_dir = root / "contracts" / "kernel" / "examples"
    for path in list(schema_dir.glob("*.json")) + list(example_dir.glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))


def test_engine_fixture_families_cover_permission_task_and_checkpoint() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_dir = root / "conformance" / "engine_fixtures"
    expected = {
        "run_request/minimal_fixture.json",
        "run_request/reference_fixture.json",
        "run_context/minimal_fixture.json",
        "run_context/reference_fixture.json",
        "tool_spec/minimal_fixture.json",
        "tool_spec/reference_fixture.json",
        "permission/minimal_fixture.json",
        "task_subagent/minimal_fixture.json",
        "checkpoint_metadata/minimal_fixture.json",
        "provider_exchange/reference_fallback_fixture.json",
        "tool_lifecycle/reference_denied_execution_fixture.json",
        "tool_lifecycle/reference_denied_render_fixture.json",
        "replay_session/reference_fixture.json",
        "execution_capability/minimal_fixture.json",
        "execution_placement/minimal_fixture.json",
        "sandbox_roundtrip/minimal_fixture.json",
        "transcript_continuation_patch/minimal_fixture.json",
        "unsupported_case/minimal_fixture.json",
        "distributed_task/minimal_fixture.json",
    }
    present = {str(path.relative_to(fixture_dir)) for path in fixture_dir.rglob("*.json")}
    missing = sorted(expected - present)
    assert not missing, f"Missing engine fixture files: {missing}"


def test_kernel_program_docs_cover_event_registry_and_hybrid_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    docs_dir = root / "docs" / "contracts" / "kernel"
    expected = {
        "KERNEL_EVENT_FAMILY_REGISTRY_V1.md",
        "HYBRID_DELEGATION_BOUNDARIES_V1.md",
        "ORCHESTRATION_BACKEND_DECISION_V1.md",
        "PYTHON_SERVICE_BOUNDARY_MATRIX_V1.md",
        "OPENCLAW_PROVING_GROUND_READINESS_V1.md",
    }
    present = {path.name for path in docs_dir.glob("*.md")}
    missing = sorted(expected - present)
    assert not missing, f"Missing kernel program docs: {missing}"


def test_kernel_examples_include_execution_driver_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    example_dir = root / "contracts" / "kernel" / "examples"
    expected = {
        "execution_capability_minimal.json",
        "execution_placement_minimal.json",
        "sandbox_request_minimal.json",
        "sandbox_result_minimal.json",
        "distributed_task_descriptor_minimal.json",
        "transcript_continuation_patch_minimal.json",
        "unsupported_case_minimal.json",
    }
    present = {path.name for path in example_dir.glob("*.json")}
    missing = sorted(expected - present)
    assert not missing, f"Missing execution-driver examples: {missing}"


def test_kernel_conformance_scripts_cover_manifest_compare_and_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts_dir = root / "scripts"
    expected = {
        "validate_engine_conformance_manifest.py",
        "compare_kernel_conformance_engines.py",
        "run_kernel_conformance_gate.py",
    }
    present = {path.name for path in scripts_dir.glob("*.py")}
    missing = sorted(expected - present)
    assert not missing, f"Missing kernel conformance scripts: {missing}"


def test_ts_runtime_surface_includes_remote_driver_and_temporal_adapter() -> None:
    root = Path(__file__).resolve().parents[1]
    sdk_dir = root / "sdk"
    expected = {
        "ts-kernel-contracts",
        "ts-kernel-core",
        "ts-host-bridges",
        "ts-execution-drivers",
        "ts-execution-driver-local",
        "ts-execution-driver-oci",
        "ts-execution-driver-remote",
        "ts-orchestration-temporal",
    }
    present = {path.name for path in sdk_dir.iterdir() if path.is_dir()}
    missing = sorted(expected - present)
    assert not missing, f"Missing TS runtime surfaces: {missing}"
