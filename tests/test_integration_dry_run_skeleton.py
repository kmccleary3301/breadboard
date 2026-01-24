"""
Skeleton integration test for dry-run task flow.

Note: This does not call external APIs. It validates our internal wiring remains importable
and basic objects can be constructed with the new YAML-based tools and validator config.
"""

from tool_calling.tool_yaml_loader import load_yaml_tools
from tool_calling.sequence_validator import SequenceValidator
from tool_calling.enhanced_executor import EnhancedToolExecutor


def test_can_construct_core_components_without_errors():
    loaded = load_yaml_tools("implementations/tools/defs")
    assert loaded.tools and loaded.manipulations_by_id

    # Minimal validator with key rules
    validator = SequenceValidator(
        enabled_rules=["file_exists", "read_before_edit", "one_bash_per_turn"],
        config={
            "read_before_edit": {"strictness": "warn"},
            "one_bash_per_turn": {"turn_window_seconds": 3},
        },
        workspace_root=".",
    )
    assert "file_exists" in validator.get_active_rules()

    # Construct executor
    class Dummy:
        def run(self, command: str, timeout: int = 30, stream: bool = False):
            return {"stdout": "", "exit": 0}

    ex = EnhancedToolExecutor(sandbox=Dummy(), config={"yaml_tool_manipulations": loaded.manipulations_by_id})
    assert ex.get_workspace_context() and isinstance(ex.get_validation_summary(), dict)


