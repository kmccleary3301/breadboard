from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.m12 import (
    build_m12_load_ladder_report,
    build_m12_soak_report,
    validate_m12_load_ladder_report,
    validate_m12_soak_report,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_m12_load_ladder_report_builder_supports_pass_and_resource_skip() -> None:
    package = load_env_package(PYTHON_TOY)

    report = build_m12_load_ladder_report(
        package=package,
        levels=[1, 2],
        skip_levels={2: "unit-test resource skip"},
        min_rows_per_level=1,
        local_mode=True,
    )

    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_load_ladder_report_v1"
    assert report["claim_boundary"] == "target_load_ladder_probe_not_scorecard_update"
    assert report["policy_version_integrity"] is True
    assert report["queue_backpressure_integrity"] is True
    assert report["concurrency_levels"][0]["target_sessions"] == 1
    assert report["concurrency_levels"][0]["status"] == "passed"
    assert report["concurrency_levels"][0]["row_count"] >= 1
    assert report["concurrency_levels"][1]["target_sessions"] == 2
    assert report["concurrency_levels"][1]["status"] == "resource_skipped"
    assert report["resource_skips"] == [{"target_sessions": 2, "reason": "unit-test resource skip"}]
    assert validate_m12_load_ladder_report(
        report,
        required_levels=[1],
        optional_levels=[2],
        require_distributed=False,
    ) == []
    assert "load ladder requires distributed Ray for non-skipped levels" in validate_m12_load_ladder_report(
        report,
        required_levels=[1],
        optional_levels=[2],
        require_distributed=True,
    )


def test_m12_soak_report_builder_supports_tiny_local_smoke() -> None:
    package = load_env_package(PYTHON_TOY)

    report = build_m12_soak_report(
        package=package,
        duration_seconds=0,
        minimum_duration_seconds=0,
        interval_seconds=0,
        num_workers=1,
        rows_per_iteration=1,
        min_iterations=1,
        local_mode=True,
    )

    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_soak_report_v1"
    assert report["claim_boundary"] == "target_soak_probe_not_scorecard_update"
    assert report["status"] == "passed"
    assert report["duration_seconds"] >= 0
    assert report["runtime_failure_count"] == 0
    assert report["row_count"] >= 1
    assert report["accepted_count"] >= 1
    assert validate_m12_soak_report(
        report,
        minimum_duration_seconds=0,
        require_distributed=False,
    ) == []
    assert "soak requires distributed Ray, not local_mode" in validate_m12_soak_report(
        report,
        minimum_duration_seconds=0,
        require_distributed=True,
    )


def test_m12_load_ladder_cli_writes_report(tmp_path) -> None:
    output = tmp_path / "load_ladder_report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_load_ladder.py",
            "--package",
            str(PYTHON_TOY),
            "--output",
            str(output),
            "--levels",
            "1",
            "--min-rows-per-level",
            "1",
            "--local-mode",
            "--smoke-mode",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["concurrency_levels"][0]["status"] == "passed"
    assert "policy_version_integrity=True" in result.stdout


def test_m12_load_ladder_cli_fails_closed_without_smoke_mode(tmp_path) -> None:
    output = tmp_path / "load_ladder_report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_load_ladder.py",
            "--package",
            str(PYTHON_TOY),
            "--output",
            str(output),
            "--levels",
            "1",
            "--min-rows-per-level",
            "1",
            "--local-mode",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert output.exists()
    assert "invalid_m12_load_ladder_report" in result.stderr


def test_m12_soak_cli_writes_report(tmp_path) -> None:
    output = tmp_path / "soak_report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_soak.py",
            "--package",
            str(PYTHON_TOY),
            "--output",
            str(output),
            "--duration-seconds",
            "0",
            "--minimum-duration-seconds",
            "0",
            "--interval-seconds",
            "0",
            "--num-workers",
            "1",
            "--rows-per-iteration",
            "1",
            "--min-iterations",
            "1",
            "--local-mode",
            "--smoke-mode",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["runtime_failure_count"] == 0
    assert "status=passed" in result.stdout


def test_m12_soak_cli_fails_closed_without_smoke_mode(tmp_path) -> None:
    output = tmp_path / "soak_report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_soak.py",
            "--package",
            str(PYTHON_TOY),
            "--output",
            str(output),
            "--duration-seconds",
            "0",
            "--minimum-duration-seconds",
            "0",
            "--interval-seconds",
            "0",
            "--num-workers",
            "1",
            "--rows-per-iteration",
            "1",
            "--min-iterations",
            "1",
            "--local-mode",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert output.exists()
    assert "invalid_m12_soak_report" in result.stderr
