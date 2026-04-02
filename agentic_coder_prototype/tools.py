from importlib import import_module
import sys
import warnings

warnings.warn(
    "`agentic_coder_prototype.tools` is deprecated; use "
    "`agentic_coder_prototype.tool_calling.catalog` instead.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("agentic_coder_prototype.tool_calling.catalog")
