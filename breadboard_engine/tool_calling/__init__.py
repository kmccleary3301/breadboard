"""
Tool calling system for the agentic coder prototype.

This package has been reorganized for better modularity. 
Core functionality is now distributed across specialized submodules.

DEPRECATED: This module structure has been reorganized.
Use the new submodules directly:
- breadboard_engine.core
- breadboard_engine.dialects  
- breadboard_engine.execution
- breadboard_engine.compilation
- breadboard_engine.integration
- breadboard_engine.monitoring
- breadboard_engine.utils
"""

# Backwards compatibility imports
from breadboard_engine.core.core import ToolDefinition, ToolParameter
from breadboard_engine.compilation.tool_yaml_loader import load_yaml_tools
from breadboard_engine.compilation.system_prompt_compiler import get_compiler
from .catalog import build_tool_catalog_specs, tool_catalog_hash
from .ir import ToolCallIR, as_simplenamespace, to_tool_call_ir
