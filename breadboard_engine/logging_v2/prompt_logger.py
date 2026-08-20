"""Compatibility wrapper for `agentic_coder_prototype.run_logging.prompt_logger`."""

import warnings

from ..run_logging.prompt_logger import *  # noqa: F403

warnings.warn(
    "`agentic_coder_prototype.logging_v2.prompt_logger` is deprecated; use "
    "`agentic_coder_prototype.run_logging.prompt_logger` instead.",
    DeprecationWarning,
    stacklevel=2,
)
