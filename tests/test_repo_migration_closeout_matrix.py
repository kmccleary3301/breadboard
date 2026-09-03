from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from breadboard.sandbox import DevSandboxV2
from breadboard.sandbox_factory import SandboxFactory
from breadboard.sandbox_virtualized import SandboxFactory as LegacySandboxFactory
from breadboard.sandbox_v2 import DevSandboxV2 as LegacyDevSandboxV2



REPO_ROOT = Path(__file__).resolve().parents[1]


def test_sandbox_compatibility_imports_preserve_symbols() -> None:
    assert LegacyDevSandboxV2 is DevSandboxV2
    assert LegacySandboxFactory is SandboxFactory


def test_mixed_canonical_and_legacy_script_paths_work_together() -> None:
    commands = [
        [sys.executable, "scripts/release/export_cli_bridge_contracts.py", "--help"],
        [sys.executable, "scripts/export_cli_bridge_contracts.py", "--help"],
        [sys.executable, "scripts/research/parity/audit_e4_target_drift.py", "--help"],
        [sys.executable, "scripts/audit_e4_target_drift.py", "--help"],
        [sys.executable, "scripts/research/parity/check_e4_snapshot_coverage.py", "--json"],
        [sys.executable, "scripts/check_e4_snapshot_coverage.py", "--json"],
    ]
    for cmd in commands:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
