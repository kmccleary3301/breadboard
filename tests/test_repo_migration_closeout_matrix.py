from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from breadboard.sandbox import DevSandboxV2
from breadboard.sandbox_factory import SandboxFactory
from breadboard.sandbox_virtualized import SandboxFactory as LegacySandboxFactory
from breadboard.sandbox_v2 import DevSandboxV2 as LegacyDevSandboxV2

from agentic_coder_prototype.orchestration.agent_session import OpenCodeAgent
from agentic_coder_prototype.agent_session import OpenCodeAgent as LegacyOpenCodeAgent
from agentic_coder_prototype.tool_calling.ir import ToolCallIR
from agentic_coder_prototype.tool_call_ir import ToolCallIR as LegacyToolCallIR
from agentic_coder_prototype.provider.runtime import provider_registry
import agentic_coder_prototype.provider_runtime as legacy_provider_runtime


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_mixed_canonical_and_legacy_imports_resolve_to_same_symbols() -> None:
    assert LegacyDevSandboxV2 is DevSandboxV2
    assert LegacySandboxFactory is SandboxFactory
    assert LegacyOpenCodeAgent is OpenCodeAgent
    assert LegacyToolCallIR is ToolCallIR
    assert legacy_provider_runtime.provider_registry is provider_registry


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
