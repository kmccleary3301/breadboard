from __future__ import annotations

from breadboard_engine.agent_session import OpenCodeAgent as legacy_open_code_agent
from breadboard_engine.conductor.prompt_planner import ToolPromptPlanner
from breadboard_engine.orchestration.agent_session import OpenCodeAgent
from breadboard_engine.provider.runtime import provider_registry
from breadboard_engine.tool_call_ir import ToolCallIR as legacy_tool_call_ir
from breadboard_engine.tool_calling.catalog import build_tool_catalog_specs
from breadboard_engine.tool_calling.ir import ToolCallIR
from breadboard_engine.tool_prompt_planner import (
    ToolPromptPlanner as legacy_tool_prompt_planner,
)
from breadboard_engine.tools import build_tool_catalog_specs as legacy_build_tool_catalog_specs
import breadboard_engine.provider_runtime as legacy_provider_runtime


def test_root_tool_call_paths_alias_canonical_symbols() -> None:
    assert legacy_tool_call_ir is ToolCallIR
    assert legacy_build_tool_catalog_specs is build_tool_catalog_specs
    assert legacy_tool_prompt_planner is ToolPromptPlanner


def test_root_orchestration_path_aliases_canonical_symbols() -> None:
    assert legacy_open_code_agent is OpenCodeAgent


def test_provider_runtime_wrapper_preserves_live_module_identity() -> None:
    assert legacy_provider_runtime.provider_registry is provider_registry
