"""Compatibility wrapper for `breadboard_engine.run_logging.run_logger`."""

import warnings

from ..run_logging.run_logger import *  # noqa: F403

warnings.warn(
    "`breadboard_engine.logging_v2.run_logger` is deprecated; use "
    "`breadboard_engine.run_logging.run_logger` instead.",
    DeprecationWarning,
    stacklevel=2,
)
