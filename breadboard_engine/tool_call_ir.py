from importlib import import_module
import sys
import warnings

warnings.warn(
    "`breadboard_engine.tool_call_ir` is deprecated; use "
    "`breadboard_engine.tool_calling.ir` instead.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("breadboard_engine.tool_calling.ir")
