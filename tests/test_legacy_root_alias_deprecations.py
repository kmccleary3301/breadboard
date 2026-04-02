import importlib
import sys
import warnings

from agentic_coder_prototype.orchestration.agent_session import OpenCodeAgent
from agentic_coder_prototype.agent_session import OpenCodeAgent as LegacyOpenCodeAgent
from agentic_coder_prototype.conductor.prompt_planner import ToolPromptPlanner
from agentic_coder_prototype.tool_prompt_planner import ToolPromptPlanner as LegacyToolPromptPlanner
from agentic_coder_prototype.tool_calling.ir import ToolCallIR
from agentic_coder_prototype.tool_call_ir import ToolCallIR as LegacyToolCallIR
from agentic_coder_prototype.tool_calling.catalog import build_tool_catalog_specs
from agentic_coder_prototype.tools import build_tool_catalog_specs as LegacyBuildToolCatalogSpecs


def test_legacy_root_aliases_still_resolve_to_canonical_symbols():
    assert LegacyOpenCodeAgent is OpenCodeAgent
    assert LegacyToolPromptPlanner is ToolPromptPlanner
    assert LegacyToolCallIR is ToolCallIR
    assert LegacyBuildToolCatalogSpecs is build_tool_catalog_specs


def test_legacy_root_aliases_emit_deprecation_warnings():
    modules = [
        "agentic_coder_prototype.agent_session",
        "agentic_coder_prototype.tool_prompt_planner",
        "agentic_coder_prototype.tool_call_ir",
        "agentic_coder_prototype.tools",
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        for name in modules:
            sys.modules.pop(name, None)
            importlib.import_module(name)
    messages = [str(item.message) for item in caught if item.category is DeprecationWarning]
    for name in modules:
        assert any(name in message for message in messages), name
