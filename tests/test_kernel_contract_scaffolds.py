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
        "permission/minimal_fixture.json",
        "task_subagent/minimal_fixture.json",
        "checkpoint_metadata/minimal_fixture.json",
    }
    present = {str(path.relative_to(fixture_dir)) for path in fixture_dir.rglob("*.json")}
    missing = sorted(expected - present)
    assert not missing, f"Missing engine fixture files: {missing}"
