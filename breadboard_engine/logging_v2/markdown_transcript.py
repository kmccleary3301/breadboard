"""Compatibility wrapper for `breadboard_engine.run_logging.markdown_transcript`."""

import warnings

from ..run_logging.markdown_transcript import *  # noqa: F403

warnings.warn(
    "`breadboard_engine.logging_v2.markdown_transcript` is deprecated; use "
    "`breadboard_engine.run_logging.markdown_transcript` instead.",
    DeprecationWarning,
    stacklevel=2,
)
