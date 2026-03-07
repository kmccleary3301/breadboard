from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _manifest_payload() -> dict:
    return {
        "run_id": "run-2026-02-25-putnam-b1",
        "created_at_utc": "2026-02-25T21:00:00Z",
        "owner": "atp-team",
        "purpose": "pilot_comparison",
        "benchmark": {
            "name": "putnambench_lean",
            "version": {
                "benchmark_git_sha": "abc123",
                "dataset_sha256": "a" * 64,
            },
            "slice": {
                "method": "stratified_random",
                "seed": 1337,
                "n_tasks": 3,
                "task_ids": ["t1", "t2", "t3"],
            },
        },
        "toolchain": {
            "lean_version": "4.12.0",
            "mathlib_commit": "deadbeef",
            "docker_image_digest": "sha256:demo",
        },
        "budget": {
            "class": "B",
            "max_candidates": 4,
            "max_repair_rounds": 2,
            "wall_clock_cap_s": 300,
            "cost_cap_usd": 25.0,
        },
        "systems": [
            {"system_id": "bb_atp", "config_ref": "configs/atp/budgetB.yaml"},
            {"system_id": "aristotle_api", "config_ref": "configs/aristotle/default.yaml"},
        ],
        "artifacts": {
            "root_dir": "artifacts/experiments/demo",
            "store_proofs": True,
            "store_logs": True,
            "redact_secrets": True,
        },
        "acceptance": {
            "determinism_reruns": 2,
            "required_fields": [
                "verification_log_digest",
                "toolchain_id",
                "input_hash",
            ],
        },
    }


def _write_manifest(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_results(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def test_cross_system_validator_accepts_complete_matrix(tmp_path: Path) -> None:
    validator = _load_module("validate_cross_system_run_v1", "scripts/validate_cross_system_run_v1.py")
    manifest = _write_manifest(tmp_path / "manifest.json", _manifest_payload())
    rows = [
        {
            "task_id": "t1",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "b" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "v1",
        },
        {
            "task_id": "t2",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "c" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "UNSOLVED",
            "verification_log_digest": "v2",
        },
        {
            "task_id": "t3",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "d" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "TIMEOUT",
            "verification_log_digest": "v3",
        },
        {
            "task_id": "t1",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "e" * 64,
            "prover_system": "aristotle_api",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "v4",
        },
        {
            "task_id": "t2",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "f" * 64,
            "prover_system": "aristotle_api",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "v5",
        },
        {
            "task_id": "t3",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "1" * 64,
            "prover_system": "aristotle_api",
            "budget_class": "B",
            "status": "ERROR",
            "verification_log_digest": "v6",
        },
    ]
    results = _write_results(tmp_path / "results.jsonl", rows)
    payload = validator.load_manifest(manifest)
    report_rows = validator.load_jsonl([results])
    manifest_errors = validator.validate_manifest_shape(payload)
    result_errors, warnings, summary = validator.validate_results(
        payload,
        report_rows,
        allow_incomplete_matrix=False,
    )
    assert manifest_errors == []
    assert result_errors == []
    assert warnings == []
    assert summary["expected_pairs"] == 6
    assert summary["actual_pairs"] == 6


def test_cross_system_validator_rejects_missing_required_field(tmp_path: Path) -> None:
    validator = _load_module("validate_cross_system_run_v1_missing_field", "scripts/validate_cross_system_run_v1.py")
    manifest = _write_manifest(tmp_path / "manifest.json", _manifest_payload())
    rows = [
        {
            "task_id": "t1",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "b" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "SOLVED",
        }
    ]
    results = _write_results(tmp_path / "results.jsonl", rows)
    payload = validator.load_manifest(manifest)
    report_rows = validator.load_jsonl([results])
    errors, _, _ = validator.validate_results(payload, report_rows, allow_incomplete_matrix=True)
    assert any("verification_log_digest" in error for error in errors)


def test_cross_system_pilot_report_emits_mcnemar(tmp_path: Path) -> None:
    reporter = _load_module("build_pilot_comparison_report_v1", "scripts/build_pilot_comparison_report_v1.py")
    manifest = _write_manifest(tmp_path / "manifest.json", _manifest_payload())
    rows = [
        {
            "task_id": "t1",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "b" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "v1",
        },
        {
            "task_id": "t2",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "c" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "UNSOLVED",
            "verification_log_digest": "v2",
        },
        {
            "task_id": "t3",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "d" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "UNSOLVED",
            "verification_log_digest": "v3",
        },
        {
            "task_id": "t1",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "e" * 64,
            "prover_system": "aristotle_api",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "v4",
        },
        {
            "task_id": "t2",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "f" * 64,
            "prover_system": "aristotle_api",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "v5",
        },
        {
            "task_id": "t3",
            "toolchain_id": "lean4.12_mathlib.deadbeef",
            "input_hash": "1" * 64,
            "prover_system": "aristotle_api",
            "budget_class": "B",
            "status": "UNSOLVED",
            "verification_log_digest": "v6",
        },
    ]
    results = _write_results(tmp_path / "results.jsonl", rows)
    report = reporter._build_report(
        manifest=reporter.load_manifest(manifest),
        rows=reporter.load_jsonl([results]),
        baseline_system="bb_atp",
        candidate_system="aristotle_api",
    )
    assert report["task_count"] == 3
    assert report["paired_outcomes"]["n10_candidate_only"] == 1
    assert report["paired_outcomes"]["n01_baseline_only"] == 0
    assert report["mcnemar"]["discordant_pairs"] == 1
    assert 0.0 <= float(report["mcnemar"]["exact_two_sided_pvalue"]) <= 1.0
