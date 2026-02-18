from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_coder_prototype.agent import AgenticCoder


def _write_temp_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"workspace": {"root": "agent_ws/test_guard"}}), encoding="utf-8")
    return cfg


def test_workspace_guard_rejects_repo_root(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    agent = AgenticCoder(str(cfg), workspace_dir=".")
    with pytest.raises(RuntimeError, match="repo root"):
        agent._resolve_workspace_path()


def test_workspace_guard_rejects_repo_ancestor(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    agent = AgenticCoder(str(cfg), workspace_dir="..")
    with pytest.raises(RuntimeError, match="ancestor of repo root"):
        agent._resolve_workspace_path()


def test_workspace_guard_allows_repo_subdir(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    agent = AgenticCoder(str(cfg), workspace_dir="agent_ws/safe_test_workspace")
    resolved = agent._resolve_workspace_path()
    repo_root = Path(__file__).resolve().parents[1]
    assert str(resolved).startswith(str(repo_root))
