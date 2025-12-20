from agentic_coder_prototype.compilation.tool_yaml_loader import load_yaml_tools
from pathlib import Path


def test_overlays_rename_and_syntax(tmp_path, monkeypatch):
    # Use real defs dir
    defs_dir = str(Path(__file__).resolve().parents[1] / "implementations" / "tools" / "defs")
    overlays = [
        {
            "rename": {"run_shell": "bash"},
            "syntax_style": {"run_shell": "bash_block"},
        }
    ]
    loaded = load_yaml_tools(defs_dir, overlays=overlays, aliases={})
    names = {t.name for t in loaded.tools}
    assert "bash" in names  # renamed


def test_aliases_roundtrip():
    defs_dir = str(Path(__file__).resolve().parents[1] / "implementations" / "tools" / "defs")
    loaded = load_yaml_tools(defs_dir, overlays=[], aliases={"patch": "apply_unified_patch"})
    assert loaded.aliases.get("patch") == "apply_unified_patch"

