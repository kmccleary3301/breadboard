import os
from pathlib import Path

import pytest

from tool_calling.tool_yaml_loader import load_yaml_tools


def test_load_yaml_tools_success():
    repo_root = Path(__file__).resolve().parents[1]
    defs_dir = str(repo_root / "implementations/tools/defs")
    loaded = load_yaml_tools(defs_dir)
    assert loaded.tools, "Expected at least one tool to be loaded"
    # Expect a couple of known tool IDs to appear in manipulations map
    m = loaded.manipulations_by_id
    assert "run_shell" in m and "shell.exec" in m["run_shell"]
    assert "apply_unified_patch" in m and "diff.apply" in m["apply_unified_patch"]


def test_load_yaml_tools_required_fields(tmp_path: Path):
    # Create a minimal invalid YAML missing required fields
    bad = tmp_path / "bad.yaml"
    bad.write_text("""
name: missing_required_id
description: no id or parameters
""".strip())
    with pytest.raises(ValueError):
        load_yaml_tools(str(tmp_path))


