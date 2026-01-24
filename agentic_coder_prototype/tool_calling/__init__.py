"""
Tool calling system for the agentic coder prototype.

This package has been reorganized for better modularity. 
Core functionality is now distributed across specialized submodules.

DEPRECATED: This module structure has been reorganized.
Use the new submodules directly:
- agentic_coder_prototype.core
- agentic_coder_prototype.dialects  
- agentic_coder_prototype.execution
- agentic_coder_prototype.compilation
- agentic_coder_prototype.integration
- agentic_coder_prototype.monitoring
- agentic_coder_prototype.utils
"""

# Backwards compatibility imports
from agentic_coder_prototype.core.core import ToolDefinition, ToolParameter
from agentic_coder_prototype.compilation.tool_yaml_loader import load_yaml_tools
from agentic_coder_prototype.compilation.system_prompt_compiler import get_compiler