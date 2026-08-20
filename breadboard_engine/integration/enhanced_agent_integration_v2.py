"""Compatibility wrapper for the canonical enhanced agent integration module."""

import warnings

from .enhanced_agent_integration import EnhancedAgentIntegrationV2

warnings.warn(
    "`breadboard_engine.integration.enhanced_agent_integration_v2` is deprecated; use "
    "`breadboard_engine.integration.enhanced_agent_integration` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["EnhancedAgentIntegrationV2"]
