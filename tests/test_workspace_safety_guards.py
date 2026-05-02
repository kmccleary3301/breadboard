from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_coder_prototype.agent import AgenticCoder
from agentic_coder_prototype.utils.safe_delete import is_disposable_workspace_path, validate_workspace_path


def _write_temp_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"workspace": {"root": "agent_ws/test_guard"}}), encoding="utf-8")
    return cfg


def test_workspace_guard_allows_preserved_repo_root(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    agent = AgenticCoder(str(cfg), workspace_dir=str(repo_root))
    resolved = agent._resolve_workspace_path()
    assert resolved == repo_root.resolve()
    assert not is_disposable_workspace_path(resolved, repo_root=repo_root)


def test_workspace_guard_rejects_repo_ancestor(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    ancestor = repo_root.parent
    agent = AgenticCoder(str(cfg), workspace_dir=str(ancestor))
    resolved = agent._resolve_workspace_path()
    assert resolved == ancestor.resolve()
    assert not is_disposable_workspace_path(resolved, repo_root=repo_root)


def test_workspace_guard_allows_repo_subdir(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    agent = AgenticCoder(str(cfg), workspace_dir="agent_ws/safe_test_workspace")
    resolved = agent._resolve_workspace_path()
    repo_root = Path(__file__).resolve().parents[1]
    assert str(resolved).startswith(str(repo_root))


def test_workspace_guard_allows_preserved_git_repo_subdir(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    nested_repo = tmp_path / "nested"
    (nested_repo / ".git").mkdir(parents=True)
    resolved = validate_workspace_path(nested_repo, repo_root=repo_root)
    assert resolved == nested_repo.resolve()
    assert not is_disposable_workspace_path(resolved, repo_root=repo_root)


def test_workspace_guard_marks_repo_tmp_as_disposable(tmp_path: Path) -> None:
    cfg = _write_temp_config(tmp_path)
    agent = AgenticCoder(str(cfg), workspace_dir="tmp/safe_test_workspace")
    resolved = agent._resolve_workspace_path()
    repo_root = Path(__file__).resolve().parents[1]
    assert is_disposable_workspace_path(resolved, repo_root=repo_root)
