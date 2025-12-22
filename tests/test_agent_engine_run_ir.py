from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.engine import create_engine
from agentic_coder_prototype.parity import RunIR


def test_agent_engine_builds_run_ir(tmp_path: Path) -> None:
    """
    Basic sanity check: running a mock-based config via AgentEngine should
    produce a RunIR with a valid workspace path and completion summary.

    This uses the existing mock provider configuration to avoid real API calls.
    """
    config_path = "agent_configs/opencode_mock_c_fs.yaml"
    workspace = tmp_path / "engine-ws"
    engine = create_engine(config_path, workspace_dir=str(workspace))

    # Use a simple textual task; the mock provider does not hit real APIs.
    raw_result, run_ir = engine.run("Implement a simple C function.")

    assert isinstance(raw_result, dict)
    assert isinstance(run_ir, RunIR)
    # Workspace path in IR should exist
    assert run_ir.workspace_path.exists()
    # Completion summary should at least be a dict, possibly empty but present
    assert isinstance(run_ir.completion_summary, dict)

