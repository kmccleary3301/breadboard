"""Compatibility wrapper for `agentic_coder_prototype.run_logging.markdown_transcript`."""

import warnings

from ..run_logging.markdown_transcript import *  # noqa: F403

warnings.warn(
    "`agentic_coder_prototype.logging_v2.markdown_transcript` is deprecated; use "
    "`agentic_coder_prototype.run_logging.markdown_transcript` instead.",
    DeprecationWarning,
    stacklevel=2,
)
