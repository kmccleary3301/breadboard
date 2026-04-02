"""Compatibility wrapper for the canonical enhanced agent integration module."""

import warnings

from .enhanced_agent_integration import EnhancedAgentIntegrationV2

warnings.warn(
    "`agentic_coder_prototype.integration.enhanced_agent_integration_v2` is deprecated; use "
    "`agentic_coder_prototype.integration.enhanced_agent_integration` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["EnhancedAgentIntegrationV2"]
