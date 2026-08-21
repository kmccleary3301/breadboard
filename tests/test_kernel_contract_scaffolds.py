from __future__ import annotations

import json
from pathlib import Path

import scripts.release.validate_kernel_contract_fixtures as kernel_fixture_validator
import scripts.check_kernel_primitive_ct as primitive_ct
from scripts.release.validate_kernel_contract_fixtures import validate_kernel_contract_fixtures


def test_kernel_contract_scaffolds_validate() -> None:
    errors = validate_kernel_contract_fixtures()
    assert not errors, "\n".join(errors)


def test_kernel_example_schema_map_reports_missing_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(kernel_fixture_validator, "EXAMPLES_DIR", tmp_path)
    monkeypatch.setattr(
        kernel_fixture_validator,
        "EXAMPLE_SCHEMA_MAP",
        {"missing_example.json": "bb.resource_ref.v1.schema.json"},
    )

    errors = kernel_fixture_validator._validate_examples()

    assert errors == ["example missing_example.json missing example file"]


def test_kernel_fixture_validator_reports_invalid_reference_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fixture_dir = tmp_path / "engine_fixtures"
    fixture_dir.mkdir()
    manifest_path = fixture_dir / "python_reference_manifest_v1.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "bb.engine_conformance_manifest.v1",
                "contractVersion": "kernel_v1_draft",
                "rows": [
                    {
                        "engineFamily": "python_reference",
                        "engineRef": "working-tree",
                        "scenarioId": "resource_ref_invalid_reference_output",
                        "supportTier": "draft-shape",
                        "comparatorClass": "shape-equal",
                        "evidence": ["bad_resource_ref_fixture.json"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "bad_resource_ref_fixture.json").write_text(
        json.dumps(
            {
                "support_tier": "draft-shape",
                "comparator_class": "shape-equal",
                "contract": "bb.resource_ref.v1",
                "reference_output": {
                    "schema_version": "bb.resource_ref.v1",
                    "uri": "file:///workspace/src/main.py",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kernel_fixture_validator, "FIXTURE_DIR", fixture_dir)
    monkeypatch.setattr(kernel_fixture_validator, "MANIFEST_PATH", manifest_path)

    errors = kernel_fixture_validator._validate_manifest_and_fixtures()

    assert any(
        error.startswith(
            "fixture bad_resource_ref_fixture.json failed bb.resource_ref.v1:"
        )
        for error in errors
    )


def test_kernel_fixture_validator_requires_invalid_fixture_coverage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fixture_dir = tmp_path / "engine_fixtures"
    fixture_dir.mkdir()
    monkeypatch.setattr(kernel_fixture_validator, "FIXTURE_DIR", fixture_dir)
    monkeypatch.setattr(
        kernel_fixture_validator,
        "REQUIRED_INVALID_FIXTURE_LABELS",
        {"resource_ref/invalid_fixture.json"},
    )

    errors = kernel_fixture_validator._validate_invalid_fixtures()

    assert errors == [
        "missing invalid fixture coverage: resource_ref/invalid_fixture.json"
    ]


def test_kernel_fixture_validator_rejects_passing_invalid_fixture(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "conformance"
        / "engine_fixtures"
        / "resource_ref"
        / "minimal_fixture.json"
    )
    fixture_dir = tmp_path / "engine_fixtures"
    target_dir = fixture_dir / "resource_ref"
    target_dir.mkdir(parents=True)
    (target_dir / "invalid_fixture.json").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(kernel_fixture_validator, "FIXTURE_DIR", fixture_dir)
    monkeypatch.setattr(
        kernel_fixture_validator,
        "REQUIRED_INVALID_FIXTURE_LABELS",
        {"resource_ref/invalid_fixture.json"},
    )

    errors = kernel_fixture_validator._validate_invalid_fixtures()

    assert errors == [
        "invalid fixture resource_ref/invalid_fixture.json unexpectedly passed bb.resource_ref.v1"
    ]


def test_kernel_fixture_validator_reports_ts_contract_registry_drift(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ts_index = tmp_path / "index.ts"
    ts_index.write_text(
        "\n".join(
            [
                'const runRequestSchema = loadTrackedSchema("bb.run_request.v1.schema.json")',
                'const runContextSchema = loadTrackedSchema("bb.run_context.v1.schema.json")',
                'registerTrackedSchema("bb.run_request.v1.schema.json", runRequestSchema)',
                "const validators = {",
                "  runRequest: runRequestSchema,",
                "}",
                "export const kernelSchemas = {",
                "  runRequest: runRequestSchema,",
                "} as const",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kernel_fixture_validator, "TS_KERNEL_CONTRACT_INDEX", ts_index)

    errors = kernel_fixture_validator._validate_ts_kernel_contracts_registry()

    assert errors == [
        "ts kernel contracts registerTrackedSchema missing loaded schemas: bb.run_context.v1.schema.json",
        "ts kernel contracts kernelValidators missing loaded schemas: bb.run_context.v1.schema.json",
        "ts kernel contracts kernelSchemas missing loaded schemas: bb.run_context.v1.schema.json",
    ]

def test_kernel_primitive_ct_loads_fixture_example_ref(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fixture_dir = tmp_path / "engine_fixtures"
    family_dir = fixture_dir / "permission"
    family_dir.mkdir(parents=True)
    example_dir = tmp_path / "contracts" / "kernel" / "examples"
    example_dir.mkdir(parents=True)
    (example_dir / "permission_minimal.json").write_text(
        json.dumps({"schema_version": "bb.permission.v1", "request_id": "req-1"}),
        encoding="utf-8",
    )
    fixture_path = family_dir / "minimal_fixture.json"
    monkeypatch.setattr(primitive_ct, "ROOT", tmp_path)

    output = primitive_ct._fixture_reference_output(
        {
            "contract": "bb.permission.v1",
            "example_ref": "../../contracts/kernel/examples/permission_minimal.json",
        },
        fixture_path,
    )

    assert output == {"schema_version": "bb.permission.v1", "request_id": "req-1"}


def test_kernel_primitive_ct_rejects_family_contract_mismatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_dir = tmp_path / "engine_fixtures"
    family_dir = fixture_dir / "permission"
    family_dir.mkdir(parents=True)
    provider_exchange = json.loads(
        (
            root
            / "contracts"
            / "kernel"
            / "examples"
            / "provider_exchange_minimal.json"
        ).read_text(encoding="utf-8")
    )
    (family_dir / "minimal_fixture.json").write_text(
        json.dumps(
            {
                "fixture_family": "permission",
                "fixture_id": "permission_minimal",
                "support_tier": "draft-shape",
                "comparator_class": "shape-equal",
                "contract": "bb.provider_exchange.v1",
                "reference_output": provider_exchange,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(primitive_ct, "FIXTURE_DIR", fixture_dir)

    report = primitive_ct._validate_family(
        "permission",
        primitive_ct._schema_store(),
        {"permission/minimal_fixture.json"},
        require_invalid=False,
    )

    assert report["ok"] is False
    assert report["errors"] == [
        "valid fixture permission/minimal_fixture.json declares "
        "bb.provider_exchange.v1; expected bb.permission.v1"
    ]
    assert report["valid"][0]["expected_contract"] == "bb.permission.v1"


def test_kernel_primitive_ct_allows_non_p4_contract_families(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_dir = tmp_path / "engine_fixtures"
    family_dir = fixture_dir / "coordination"
    family_dir.mkdir(parents=True)
    source = (
        root
        / "conformance"
        / "engine_fixtures"
        / "coordination"
        / "minimal_fixture.json"
    )
    (family_dir / "minimal_fixture.json").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(primitive_ct, "FIXTURE_DIR", fixture_dir)

    report = primitive_ct._validate_family(
        "coordination",
        primitive_ct._schema_store(),
        {"coordination/minimal_fixture.json"},
        require_invalid=False,
    )

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["valid"][0]["contract"] == "bb.coordination_reference_slice.v1"
    assert report["valid"][0]["expected_contract"] is None

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
        "coordination_signals_v1.md",
        "coordination_reviews_v1.md",
        "coordination_directives_v1.md",
        "coordination_intervention_v1.md",
        "coordination_verification_v1.md",
        "durable_orchestration_and_resume_v1.md",
        "transcript_continuation_patch_v1.md",
        "unsupported_case_v1.md",
        "replay_session_v1.md",
        "conformance_evidence_v1.md",
        "terminal_sessions_v1.md",
        "tool_bindings_and_effective_surfaces_v1.md",
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
        "coordination/reference_complete_fixture.json",
        "coordination/reference_blocked_fixture.json",
        "coordination/reviewed_complete_fixture.json",
        "coordination/reviewed_blocked_fixture.json",
        "coordination/directive_retry_fixture.json",
        "coordination/multi_worker_complete_fixture.json",
        "coordination/multi_worker_blocked_fixture.json",
        "coordination/longrun_no_progress_fixture.json",
        "coordination/longrun_retryable_failure_fixture.json",
        "coordination/longrun_human_required_fixture.json",
        "coordination/intervention_continue_fixture.json",
        "coordination/delegated_verification_pass_fixture.json",
        "coordination/delegated_verification_fail_fixture.json",
        "execution_capability/minimal_fixture.json",
        "execution_capability/reference_fixture.json",
        "execution_placement/minimal_fixture.json",
        "execution_placement/reference_fixture.json",
        "sandbox_roundtrip/minimal_fixture.json",
        "sandbox_roundtrip/reference_fixture.json",
        "transcript_continuation_patch/minimal_fixture.json",
        "transcript_continuation_patch/reference_fixture.json",
        "unsupported_case/minimal_fixture.json",
        "unsupported_case/reference_fixture.json",
        "distributed_task/minimal_fixture.json",
        "distributed_task/reference_fixture.json",
        "terminal_session/minimal_start_fixture.json",
        "terminal_session/reference_begin_fixture.json",
        "terminal_session/reference_poll_fixture.json",
        "terminal_session/reference_stdin_fixture.json",
        "terminal_session/reference_output_fixture.json",
        "terminal_session/reference_end_fixture.json",
        "terminal_session/reference_registry_fixture.json",
        "terminal_session/reference_cleanup_fixture.json",
        "effective_tool_surface/minimal_fixture.json",
        "effective_tool_surface/reference_terminal_binding_fixture.json",
        "effective_tool_surface/reference_hidden_terminal_fixture.json",
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
        "review_verdict_complete_validated.json",
        "review_verdict_blocked_retry.json",
        "directive_retry_minimal.json",
        "transcript_continuation_patch_minimal.json",
        "unsupported_case_minimal.json",
        "terminal_session_descriptor_minimal.json",
        "terminal_output_delta_minimal.json",
        "terminal_interaction_minimal.json",
        "terminal_session_end_minimal.json",
        "terminal_registry_snapshot_minimal.json",
        "terminal_cleanup_result_minimal.json",
        "environment_selector_minimal.json",
        "tool_binding_minimal.json",
        "tool_support_claim_minimal.json",
        "effective_tool_surface_minimal.json",
        "effective_config_graph_minimal.json",
        "config_mutation_record_minimal.json",
        "context_resource_pack_minimal.json",
        "capability_registry_minimal.json",
        "extension_hook_execution_minimal.json",
        "resource_ref_minimal.json",
        "resource_access_minimal.json",
        "blob_ref_minimal.json",
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
