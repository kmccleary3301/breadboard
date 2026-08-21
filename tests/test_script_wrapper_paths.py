from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCRIPT_MODULES = {
    "scripts.audit_e4_target_drift": "scripts.research.parity.audit_e4_target_drift",
    "scripts.bless_golden": "scripts.release.bless_golden",
    "scripts.check_e4_snapshot_coverage": "scripts.research.parity.check_e4_snapshot_coverage",
    "scripts.cli_session_health": "scripts.ops.cli_session_health",
    "scripts.compat_dump_request_bodies": "scripts.migration.compat_dump_request_bodies",
    "scripts.export_cli_bridge_contracts": "scripts.release.export_cli_bridge_contracts",
    "scripts.export_provider_metrics": "scripts.ops.export_provider_metrics",
    "scripts.fixtures_doctor": "scripts.ops.fixtures_doctor",
    "scripts.guardrail_metrics": "scripts.ops.guardrail_metrics",
    "scripts.import_ir_to_events_jsonl": "scripts.migration.import_ir_to_events_jsonl",
    "scripts.phase11_benchmark_runner_stub": "scripts.archive.phase11_benchmark_runner_stub",
    "scripts.phase11_export_trajectory_stub": "scripts.archive.phase11_export_trajectory_stub",
    "scripts.phase11_paired_eval_stub": "scripts.archive.phase11_paired_eval_stub",
    "scripts.preflight_workspace_safety": "scripts.ops.preflight_workspace_safety",
    "scripts.recover_missing_files_from_codex_outputs": "scripts.migration.recover_missing_files_from_codex_outputs",
    "scripts.validate_kernel_contract_fixtures": "scripts.release.validate_kernel_contract_fixtures",
}
LEGACY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(?P<from>scripts\.[A-Za-z0-9_]+)\b|import\s+(?P<import>scripts\.[A-Za-z0-9_]+)\b)"
)
LEGACY_IMPORT_SCAN_ROOTS = ("breadboard", "scripts", "tests")




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


def test_repository_imports_canonical_script_modules_instead_of_legacy_shims() -> None:
    offenders: list[str] = []
    for root_name in LEGACY_IMPORT_SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root_name).rglob("*.py")):
            if any(part in {".venv", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                match = LEGACY_IMPORT_RE.match(line)
                if not match:
                    continue
                legacy_module = match.group("from") or match.group("import")
                canonical_module = LEGACY_SCRIPT_MODULES.get(legacy_module)
                if canonical_module is None:
                    continue
                rel_path = path.relative_to(REPO_ROOT)
                offenders.append(
                    f"{rel_path}:{line_number} imports {legacy_module}; use {canonical_module}"
                )

    assert offenders == []
