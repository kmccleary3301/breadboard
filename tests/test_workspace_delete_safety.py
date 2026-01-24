import os
from pathlib import Path

import pytest

from agentic_coder_prototype.agent import AgenticCoder
from agentic_coder_prototype.utils.safe_delete import safe_rmtree


def test_safe_rmtree_refuses_repo_root() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with pytest.raises(RuntimeError):
        safe_rmtree(repo_root, repo_root=repo_root, label="repo_root")


def test_agent_refuses_workspace_outside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    config = Path(__file__).resolve().parents[1] / "agent_configs" / "base_v2.yaml"
    coder = AgenticCoder(str(config), workspace_dir="..")
    with pytest.raises(RuntimeError):
        coder.initialize()

