"""Compatibility wrapper for `breadboard_engine.run_logging.prompt_logger`."""

import warnings

from ..run_logging.prompt_logger import *  # noqa: F403

warnings.warn(
    "`breadboard_engine.logging_v2.prompt_logger` is deprecated; use "
    "`breadboard_engine.run_logging.prompt_logger` instead.",
    DeprecationWarning,
    stacklevel=2,
)
