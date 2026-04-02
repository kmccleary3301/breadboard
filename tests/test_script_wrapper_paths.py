from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("legacy_path", "canonical_path", "args", "expected_token"),
    [
        (
            "scripts/preflight_workspace_safety.py",
            "scripts/ops/preflight_workspace_safety.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/guardrail_metrics.py",
            "scripts/ops/guardrail_metrics.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/import_ir_to_events_jsonl.py",
            "scripts/migration/import_ir_to_events_jsonl.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/compat_dump_request_bodies.py",
            "scripts/migration/compat_dump_request_bodies.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/phase11_benchmark_runner_stub.py",
            "scripts/archive/phase11_benchmark_runner_stub.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/export_cli_bridge_contracts.py",
            "scripts/release/export_cli_bridge_contracts.py",
            [],
            None,
        ),
        (
            "scripts/validate_kernel_contract_fixtures.py",
            "scripts/release/validate_kernel_contract_fixtures.py",
            [],
            "ok",
        ),
        (
            "scripts/bless_golden.py",
            "scripts/release/bless_golden.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/audit_e4_target_drift.py",
            "scripts/research/parity/audit_e4_target_drift.py",
            ["--help"],
            "usage",
        ),
        (
            "scripts/check_e4_snapshot_coverage.py",
            "scripts/research/parity/check_e4_snapshot_coverage.py",
            ["--json"],
            '"ok"',
        )
    ],
)
def test_legacy_and_canonical_script_paths_stay_compatible(
    legacy_path: str,
    canonical_path: str,
    args: list[str],
    expected_token: str | None,
) -> None:
    for script_path in (legacy_path, canonical_path):
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / script_path), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        if expected_token is not None:
            assert expected_token.lower() in completed.stdout.lower()
