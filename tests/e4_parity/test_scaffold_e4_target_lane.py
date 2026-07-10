from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from scripts.e4_parity.scaffold_e4_target_lane import (
    INVENTORY_SCHEMA_REF,
    INVENTORY_SCHEMA_VERSION,
    LANE_DEF_SCHEMA_REF,
    LANE_DEF_SCHEMA_VERSION,
    PROBE_SCHEMA_PATH,
    SCAFFOLD_SCHEMA_PATH,
    ScaffoldInputs,
    build_inventory_row,
    build_probe_report,
    build_scaffold_manifest,
    collect_probe_report_errors,
    collect_scaffold_manifest_errors,
    default_output_root,
    dry_run_scaffold,
    write_scaffold,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "e4_parity" / "scaffold_e4_target_lane.py"
DEFAULT_PREFIX = "docs_tmp/phase_15/lane_scaffolds"


def _inputs(lane_id: str = "unit_probe_contract_lane") -> ScaffoldInputs:
    return ScaffoldInputs(
        lane_id=lane_id,
        config_id="unit_probe_contract_config_v1",
        target_family="oh_my_pi",
        target_version="unit",
    )


def _load_schema(path: Path) -> dict[str, object]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _inventory_validator() -> Draft202012Validator:
    schema_path = ROOT / INVENTORY_SCHEMA_REF
    common_path = ROOT / "contracts" / "kernel" / "schemas" / "bb.kernel.common.v1.schema.json"
    schema = _load_schema(schema_path)
    common = _load_schema(common_path)
    resolver = RefResolver(
        base_uri=(schema_path.parent.as_uri() + "/"),
        referrer=schema,
        store={
            common.get("$id", ""): common,
            "bb.kernel.common.v1.schema.json": common,
            common_path.as_uri(): common,
        },
    )
    return Draft202012Validator(schema, resolver=resolver)


def test_probe_report_and_scaffold_manifest_conform_to_schemas() -> None:
    probe_schema = _load_schema(PROBE_SCHEMA_PATH)
    scaffold_schema = _load_schema(SCAFFOLD_SCHEMA_PATH)
    inputs = _inputs()

    probe_report = build_probe_report(inputs)
    scaffold_manifest = build_scaffold_manifest(inputs)

    assert list(Draft202012Validator(probe_schema).iter_errors(probe_report)) == []
    assert list(Draft202012Validator(scaffold_schema).iter_errors(scaffold_manifest)) == []
    assert collect_probe_report_errors(probe_report) == []
    assert collect_scaffold_manifest_errors(scaffold_manifest) == []
    assert probe_report["promotion_state"] == "scaffold_only"
    assert scaffold_manifest["guardrails"] == {
        "changes_accepted_score": False,
        "creates_support_claim": False,
        "scaffold_only": True,
        "writes_accepted_evidence_roots": False,
    }


def test_default_dry_run_output_stays_under_lane_scaffolds_and_writes_nothing() -> None:
    inputs = _inputs("unit_probe_contract_no_write_lane")
    output_root = default_output_root(inputs.lane_id)
    existed_before = output_root.exists()

    payload = dry_run_scaffold(inputs)

    expected_root = f"{DEFAULT_PREFIX}/{inputs.lane_id}"
    assert payload["dry_run"] is True
    assert payload["output_root"] == expected_root
    assert payload["default_output_root"] == expected_root
    assert all(path == expected_root or path.startswith(f"{expected_root}/") for path in payload["generated_directories"])
    assert all(path.startswith(f"{expected_root}/") for path in payload["generated_files"])
    assert output_root.exists() is existed_before


def test_cli_dry_run_json_is_deterministic_and_schema_valid() -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "--lane-id",
        "sample_lane",
        "--config-id",
        "sample_config_v1",
        "--target-family",
        "oh_my_pi",
        "--target-version",
        "sample",
        "--dry-run-json",
    ]

    first = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)

    assert first.stdout == second.stdout
    assert first.stderr == ""
    payload = json.loads(first.stdout)
    assert payload["output_root"] == "docs_tmp/phase_15/lane_scaffolds/sample_lane"
    assert payload["generated_files"] == sorted(payload["generated_files"])
    assert collect_probe_report_errors(payload["probe_report"]) == []
    assert collect_scaffold_manifest_errors(payload["scaffold_manifest"]) == []



def test_emit_inventory_row_dry_run_validates_against_lane_inventory_schema() -> None:
    inputs = _inputs("sample_scaffold_lane")
    row = build_inventory_row(inputs)
    inventory = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "inventory_id": "sample_scaffold_inventory",
        "generated_at_utc": "2026-07-03T00:00:00Z",
        "revision": 1,
        "lanes": [row],
    }

    errors = sorted(
        _inventory_validator().iter_errors(inventory),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    assert errors == []
    assert row["status"] == "scaffolded"
    assert row["artifact_roles"] == {}
    assert row["ledger_feature_ids"] == []
    assert row["points"] == 0


def test_cli_emits_optional_scaffold_artifacts_in_dry_run() -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "--lane-id",
        "sample_full_scaffold",
        "--config-id",
        "sample_full_config_v1",
        "--target-family",
        "oh_my_pi",
        "--target-version",
        "sample",
        "--dry-run-json",
        "--emit-inventory-row",
        "--emit-builder-skeleton",
        "--emit-comparator-skeleton",
        "--emit-lane-def",
    ]

    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    payload = json.loads(completed.stdout)

    assert completed.stderr == ""
    assert payload["inventory_row"]["status"] == "scaffolded"
    assert payload["lane_def"]["schema_version"] == LANE_DEF_SCHEMA_VERSION
    assert "TODO" not in payload["builder_skeleton"]
    assert "TODO" not in payload["comparator_skeleton"]
    assert "ComparatorInput" in payload["comparator_skeleton"]
    assert set(payload["generated_files"]) >= {
        "docs_tmp/phase_15/lane_scaffolds/sample_full_scaffold/inventory/e4_lane_inventory_row.scaffold.json",
        "docs_tmp/phase_15/lane_scaffolds/sample_full_scaffold/config/e4_lane_def.scaffold.yaml",
        "docs_tmp/phase_15/lane_scaffolds/sample_full_scaffold/builder/build_scaffold_lane.py",
        "docs_tmp/phase_15/lane_scaffolds/sample_full_scaffold/comparator/scaffold_lane_comparator.py",
    }


def test_write_mode_emits_requested_optional_artifacts(tmp_path: Path) -> None:
    inputs = _inputs("sample_write_scaffold")
    output_root = ROOT.parent / "docs_tmp" / "phase_15" / "lane_scaffolds" / inputs.lane_id

    if output_root.exists():
        raise AssertionError(f"test scaffold output already exists: {output_root}")
    result = write_scaffold(
        inputs,
        output_root,
        emit_inventory_row=True,
        emit_builder_skeleton=True,
        emit_comparator_skeleton=True,
        emit_lane_def=True,
    )

    try:
        assert result["generated_files"] == sorted(result["generated_files"])
        assert (output_root / "inventory" / "e4_lane_inventory_row.scaffold.json").is_file()
        assert (output_root / "builder" / "build_scaffold_lane.py").is_file()
        assert (output_root / "comparator" / "scaffold_lane_comparator.py").is_file()
        assert (output_root / "config" / "e4_lane_def.scaffold.yaml").is_file()
        inventory_row = json.loads((output_root / "inventory" / "e4_lane_inventory_row.scaffold.json").read_text(encoding="utf-8"))
        assert inventory_row == build_inventory_row(inputs)
    finally:
        for path in sorted(output_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        output_root.rmdir()


def _remove_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


def test_cli_write_mode_emits_schema_valid_scaffold_files() -> None:
    lane_id = "sample_cli_write_scaffold"
    output_root = ROOT.parent / "docs_tmp" / "phase_15" / "lane_scaffolds" / lane_id
    if output_root.exists():
        raise AssertionError(f"test scaffold output already exists: {output_root}")
    command = [
        sys.executable,
        str(SCRIPT),
        "--lane-id",
        lane_id,
        "--config-id",
        "sample_cli_write_config_v1",
        "--target-family",
        "oh_my_pi",
        "--target-version",
        "sample",
        "--out-dir",
        str(output_root),
        "--emit-inventory-row",
        "--emit-builder-skeleton",
        "--emit-comparator-skeleton",
        "--emit-lane-def",
    ]

    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)

    try:
        result = json.loads(completed.stdout)
        assert completed.stderr == ""
        assert result["dry_run"] is False
        assert result["output_root"] == f"{DEFAULT_PREFIX}/{lane_id}"
        assert result["generated_files"] == sorted(result["generated_files"])
        assert all(path.startswith(f"{DEFAULT_PREFIX}/{lane_id}/") for path in result["generated_files"])

        scaffold_manifest = json.loads((output_root / "scaffold_manifest.json").read_text(encoding="utf-8"))
        probe_report = json.loads((output_root / "target_probe_report.placeholder.json").read_text(encoding="utf-8"))
        config_placeholder = json.loads((output_root / "config" / "target_lane_config.placeholder.json").read_text(encoding="utf-8"))
        inventory_row = json.loads((output_root / "inventory" / "e4_lane_inventory_row.scaffold.json").read_text(encoding="utf-8"))
        lane_def = json.loads((output_root / "config" / "e4_lane_def.scaffold.yaml").read_text(encoding="utf-8"))

        assert collect_scaffold_manifest_errors(scaffold_manifest) == []
        assert collect_probe_report_errors(probe_report) == []
        assert config_placeholder["promotion_state"] == "scaffold_only"
        assert inventory_row["status"] == "scaffolded"
        assert inventory_row["points"] == 0
        assert lane_def["schema_version"] == LANE_DEF_SCHEMA_VERSION
        assert (output_root / "builder" / "build_scaffold_lane.py").is_file()
        assert (output_root / "comparator" / "scaffold_lane_comparator.py").is_file()
        assert scaffold_manifest["guardrails"] == {
            "changes_accepted_score": False,
            "creates_support_claim": False,
            "scaffold_only": True,
            "writes_accepted_evidence_roots": False,
        }
    finally:
        if output_root.exists():
            _remove_tree(output_root)


def test_cli_write_mode_rejects_accepted_evidence_roots(tmp_path: Path) -> None:
    protected_root = ROOT / "docs" / "conformance" / "e4_target_support" / "sample_forbidden_scaffold"
    command = [
        sys.executable,
        str(SCRIPT),
        "--lane-id",
        "sample_forbidden_scaffold",
        "--config-id",
        "sample_forbidden_config_v1",
        "--target-family",
        "oh_my_pi",
        "--target-version",
        "sample",
        "--out-dir",
        str(protected_root),
    ]

    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)

    assert completed.returncode == 1
    assert "docs_tmp/phase_15/lane_scaffolds" in completed.stderr
    assert not protected_root.exists()