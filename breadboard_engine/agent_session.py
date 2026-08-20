from importlib import import_module
import sys
import warnings

warnings.warn(
    "`breadboard_engine.agent_session` is deprecated; use "
    "`breadboard_engine.orchestration.agent_session` instead.",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = import_module("breadboard_engine.orchestration.agent_session")
