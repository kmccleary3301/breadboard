from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentic_coder_prototype.conductor_components import initialize_yaml_tools


def _names(conductor: SimpleNamespace) -> set[str]:
    return {str(getattr(t, "name", "")) for t in (getattr(conductor, "yaml_tools", None) or [])}


def _defs_dir() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return str((repo_root / "implementations" / "tools" / "defs").resolve())


def test_rlm_tools_hidden_when_feature_disabled() -> None:
    conductor = SimpleNamespace(
        config={
            "tools": {"defs_dir": _defs_dir(), "registry": {"include": ["*"]}},
            "features": {},
        },
        workspace=".",
    )
    initialize_yaml_tools(conductor)
    names = _names(conductor)
    assert "blob.put" not in names
    assert "blob.put_file_slice" not in names
    assert "blob.get" not in names
    assert "blob.search" not in names
    assert "llm.query" not in names
    assert "llm.batch_query" not in names


def test_rlm_tools_present_when_feature_enabled() -> None:
    conductor = SimpleNamespace(
        config={
            "tools": {"defs_dir": _defs_dir(), "registry": {"include": ["*"]}},
            "features": {"rlm": {"enabled": True}},
        },
        workspace=".",
    )
    initialize_yaml_tools(conductor)
    names = _names(conductor)
    assert "blob.put" in names
    assert "blob.put_file_slice" in names
    assert "blob.get" in names
    assert "blob.search" in names
    assert "llm.query" in names
    assert "llm.batch_query" in names
