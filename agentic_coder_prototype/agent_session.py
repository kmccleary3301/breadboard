from importlib import import_module
import sys
import warnings

warnings.warn(
    "`agentic_coder_prototype.agent_session` is deprecated; use "
    "`agentic_coder_prototype.orchestration.agent_session` instead.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("agentic_coder_prototype.orchestration.agent_session")
