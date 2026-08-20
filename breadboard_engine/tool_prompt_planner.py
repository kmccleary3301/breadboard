from importlib import import_module
import sys
import warnings

warnings.warn(
    "`breadboard_engine.tool_prompt_planner` is deprecated; use "
    "`breadboard_engine.conductor.prompt_planner` instead.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("breadboard_engine.conductor.prompt_planner")
