from __future__ import annotations

from pathlib import Path
import os

import pytest

import breadboard_engine.agent as agent_module
from breadboard_engine.agent import AgenticCoder

from breadboard_engine.engine import create_engine
from breadboard_engine.parity import RunIR


def test_agent_engine_builds_run_ir(tmp_path: Path) -> None:
    """
    Basic sanity check: running a mock-based config via AgentEngine should
    produce a RunIR with a valid workspace path and completion summary.

    This uses the existing mock provider configuration to avoid real API calls.
    """
    config_path = "agent_configs/misc/opencode_mock_c_fs.yaml"
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


def _task_coder(workspace: Path) -> AgenticCoder:
    coder = AgenticCoder.__new__(AgenticCoder)
    coder.workspace_dir = str(workspace)
    coder.config = {"workspace": {"root": str(workspace)}}
    return coder


def test_task_spec_rejects_workspace_symlink_and_protected_external_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "credential"
    secret.write_text("task-read-canary", encoding="utf-8")
    (workspace / "TASK.md").symlink_to(secret)
    coder = _task_coder(workspace)

    with pytest.raises(agent_module._UnsafeTaskSpecPath) as linked_failure:
        coder._read_task_spec(str(workspace / "TASK.md"))
    assert "task-read-canary" not in str(linked_failure.value)

    monkeypatch.setattr(
        agent_module,
        "protected_credential_paths",
        lambda: (secret,),
    )
    with pytest.raises(agent_module._UnsafeTaskSpecPath) as direct_failure:
        coder._read_task_spec(str(secret))
    assert "task-read-canary" not in str(direct_failure.value)


def test_task_materialization_rejects_late_link_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    coder = _task_coder(workspace)

    symlink_secret = tmp_path / "symlink-secret"
    symlink_secret.write_text("symlink-seed-canary", encoding="utf-8")
    (workspace / "TASK.md").symlink_to(symlink_secret)
    coder._materialize_task_spec(Path("/trusted/TASK.md"), "replacement")
    assert symlink_secret.read_text(encoding="utf-8") == "symlink-seed-canary"

    os.unlink(workspace / "TASK.md")
    hardlink_secret = tmp_path / "hardlink-secret"
    hardlink_secret.write_text("hardlink-seed-canary", encoding="utf-8")
    os.link(hardlink_secret, workspace / "TASK.md")
    coder._materialize_task_spec(Path("/trusted/TASK.md"), "replacement")
    assert hardlink_secret.read_text(encoding="utf-8") == "hardlink-seed-canary"
