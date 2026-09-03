from __future__ import annotations

from breadboard_engine.compilation.system_prompt_compiler import SystemPromptCompiler
from breadboard_engine.core.core import ToolDefinition
from breadboard_engine.execution.dialect_manager import DialectManager


CATALOG_ORDER = [
    "pythonic02",
    "pythonic_inline",
    "bash_block",
    "aider_diff",
    "unified_diff",
    "opencode_patch",
    "yaml_command",
]


def test_dialect_selection_defaults_to_catalog_order() -> None:
    manager = DialectManager({})

    assert manager.get_dialects_for_model("unknown-model") == CATALOG_ORDER


def test_dialect_prompt_alias_matches_canonical_catalog_entry(tmp_path) -> None:
    tool = ToolDefinition(name="run_shell", description="run a shell command")
    canonical, _ = SystemPromptCompiler(cache_dir=str(tmp_path / "canonical")).get_or_create_system_prompt(
        [tool], ["pythonic02"]
    )
    alias, _ = SystemPromptCompiler(cache_dir=str(tmp_path / "alias")).get_or_create_system_prompt(
        [tool], ["pythonic"]
    )

    assert alias == canonical


def test_runtime_parse_uses_selected_catalog_dialect() -> None:
    manager = DialectManager({})
    tool = ToolDefinition(name="run_shell", description="run a shell command", type_id="python")

    calls = manager.parse_calls("<BASH>echo hi</BASH>", [tool], ["bash_block"])

    assert len(calls) == 1
    assert calls[0].function == "run_shell"
    assert calls[0].arguments == {"command": "echo hi"}
    assert calls[0].dialect == "python"
