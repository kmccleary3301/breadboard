"""Compatibility wrapper for `agentic_coder_prototype.run_logging.request_recorder`."""

import warnings

from ..run_logging.request_recorder import *  # noqa: F403

warnings.warn(
    "`agentic_coder_prototype.logging_v2.request_recorder` is deprecated; use "
    "`agentic_coder_prototype.run_logging.request_recorder` instead.",
    DeprecationWarning,
    stacklevel=2,
)
