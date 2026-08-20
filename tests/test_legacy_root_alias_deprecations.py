import importlib
import sys
import warnings

from breadboard_engine.orchestration.agent_session import OpenCodeAgent
from breadboard_engine.agent_session import OpenCodeAgent as LegacyOpenCodeAgent
from breadboard_engine.conductor.prompt_planner import ToolPromptPlanner
from breadboard_engine.tool_prompt_planner import ToolPromptPlanner as LegacyToolPromptPlanner
from breadboard_engine.tool_calling.ir import ToolCallIR
from breadboard_engine.tool_call_ir import ToolCallIR as LegacyToolCallIR
from breadboard_engine.tool_calling.catalog import build_tool_catalog_specs
from breadboard_engine.tools import build_tool_catalog_specs as LegacyBuildToolCatalogSpecs


def test_legacy_root_aliases_still_resolve_to_canonical_symbols():
    assert LegacyOpenCodeAgent is OpenCodeAgent
    assert LegacyToolPromptPlanner is ToolPromptPlanner
    assert LegacyToolCallIR is ToolCallIR
    assert LegacyBuildToolCatalogSpecs is build_tool_catalog_specs


def test_legacy_root_aliases_emit_deprecation_warnings():
    modules = [
        "breadboard_engine.agent_session",
        "breadboard_engine.tool_prompt_planner",
        "breadboard_engine.tool_call_ir",
        "breadboard_engine.tools",
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        for name in modules:
            sys.modules.pop(name, None)
            importlib.import_module(name)
    messages = [str(item.message) for item in caught if item.category is DeprecationWarning]
    for name in modules:
        assert any(name in message for message in messages), name
