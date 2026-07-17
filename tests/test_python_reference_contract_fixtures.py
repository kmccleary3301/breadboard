from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.build_python_reference_contract_fixtures as fixture_builder
from scripts.build_python_reference_contract_fixtures import (
    FROZEN_REFERENCE_FIXTURE_PATHS,
    build_active_python_reference_contract_fixtures,
    load_python_reference_contract_fixtures,
    write_active_python_reference_contract_fixtures,
)


EXPECTED_FROZEN_PATHS = {
    "coordination/delegated_verification_fail_fixture.json",
    "coordination/delegated_verification_pass_fixture.json",
    "coordination/intervention_continue_fixture.json",
    "coordination/longrun_human_required_fixture.json",
    "coordination/longrun_no_progress_fixture.json",
    "coordination/longrun_retryable_failure_fixture.json",
    "coordination/multi_worker_blocked_fixture.json",
    "coordination/multi_worker_complete_fixture.json",
    "coordination/reference_blocked_fixture.json",
    "coordination/reference_complete_fixture.json",
    "task_subagent/reference_background_fixture.json",
    "task_subagent/reference_fixture.json",
}
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "conformance" / "engine_fixtures"


def _copy_frozen_inputs(target_root: Path) -> dict[str, bytes]:
    frozen_bytes = {rel: (FIXTURE_ROOT / rel).read_bytes() for rel in EXPECTED_FROZEN_PATHS}
    for rel, payload in frozen_bytes.items():
        (target_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (target_root / rel).write_bytes(payload)
    return frozen_bytes


def test_frozen_reference_contract_fixtures_are_loaded_without_byte_changes() -> None:
    before = {rel: (FIXTURE_ROOT / rel).read_bytes() for rel in EXPECTED_FROZEN_PATHS}

    loaded = load_python_reference_contract_fixtures()

    assert FROZEN_REFERENCE_FIXTURE_PATHS == EXPECTED_FROZEN_PATHS
    assert before == {rel: (FIXTURE_ROOT / rel).read_bytes() for rel in EXPECTED_FROZEN_PATHS}
    for rel, raw_payload in before.items():
        assert loaded[rel] == json.loads(raw_payload)


def test_active_reference_contract_fixture_generation_is_deterministic() -> None:
    first = build_active_python_reference_contract_fixtures()
    second = build_active_python_reference_contract_fixtures()

    assert first == second
    assert not FROZEN_REFERENCE_FIXTURE_PATHS.intersection(first)


def test_reference_contract_fixture_loader_combines_active_and_frozen_outputs() -> None:
    active = build_active_python_reference_contract_fixtures()
    loaded = load_python_reference_contract_fixtures()

    assert set(loaded) == set(active) | EXPECTED_FROZEN_PATHS
    for rel, payload in active.items():
        assert loaded[rel] == payload


def test_active_fixture_writer_skips_frozen_paths(monkeypatch, tmp_path: Path) -> None:
    active = build_active_python_reference_contract_fixtures()
    generated = dict(active)
    frozen_rel = min(EXPECTED_FROZEN_PATHS)
    generated[frozen_rel] = {"must_not": "be written"}
    frozen_bytes = _copy_frozen_inputs(tmp_path)

    monkeypatch.setattr(fixture_builder, "FIXTURE_ROOT", tmp_path)
    monkeypatch.setattr(fixture_builder, "build_active_python_reference_contract_fixtures", lambda: generated)

    write_active_python_reference_contract_fixtures()

    assert frozen_bytes == {rel: (tmp_path / rel).read_bytes() for rel in EXPECTED_FROZEN_PATHS}
    for rel, payload in active.items():
        assert json.loads((tmp_path / rel).read_text(encoding="utf-8")) == payload


@pytest.mark.parametrize("relative_path", sorted(EXPECTED_FROZEN_PATHS))
def test_active_fixture_writer_rejects_tampered_frozen_input_before_output(
    monkeypatch, tmp_path: Path, relative_path: str
) -> None:
    tampered = _copy_frozen_inputs(tmp_path)[relative_path]
    (tmp_path / relative_path).write_bytes(bytes([tampered[0] ^ 1]) + tampered[1:])
    monkeypatch.setattr(fixture_builder, "FIXTURE_ROOT", tmp_path)
    monkeypatch.setattr(fixture_builder, "build_active_python_reference_contract_fixtures", lambda: {"active.json": {}})

    with pytest.raises(ValueError, match=f"frozen reference fixture digest mismatch: {relative_path}"):
        write_active_python_reference_contract_fixtures()
    assert not (tmp_path / "active.json").exists()
