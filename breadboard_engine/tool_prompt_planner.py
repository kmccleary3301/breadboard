from importlib import import_module
import sys
import warnings

warnings.warn(
    "`agentic_coder_prototype.tool_prompt_planner` is deprecated; use "
    "`agentic_coder_prototype.conductor.prompt_planner` instead.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("agentic_coder_prototype.conductor.prompt_planner")
