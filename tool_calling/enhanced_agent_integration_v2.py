import warnings

from .enhanced_agent_integration import EnhancedAgentIntegrationV2

warnings.warn(
    "`tool_calling.enhanced_agent_integration_v2` is deprecated; use "
    "`tool_calling.enhanced_agent_integration` instead.",
    DeprecationWarning,
    stacklevel=2,
)
