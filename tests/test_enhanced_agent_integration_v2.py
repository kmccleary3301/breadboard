import importlib
import warnings

from agentic_coder_prototype.integration.enhanced_agent_integration import (
    EnhancedAgentIntegrationV2 as CanonicalIntegration,
)
from agentic_coder_prototype.integration.enhanced_agent_integration_v2 import (
    EnhancedAgentIntegrationV2 as CompatIntegration,
)
from tool_calling.enhanced_agent_integration import (
    EnhancedAgentIntegrationV2 as ToolCallingCanonicalIntegration,
)
from tool_calling.enhanced_agent_integration_v2 import (
    EnhancedAgentIntegrationV2 as ToolCallingCompatIntegration,
)


def test_integration_v2_wrapper_aliases_canonical_class():
    assert CompatIntegration is CanonicalIntegration
    assert ToolCallingCompatIntegration is ToolCallingCanonicalIntegration
    assert ToolCallingCanonicalIntegration is CanonicalIntegration


def test_integration_v2_wrappers_emit_deprecation_warnings():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        integration_module = importlib.import_module(
            "agentic_coder_prototype.integration.enhanced_agent_integration_v2"
        )
        tool_calling_module = importlib.import_module(
            "tool_calling.enhanced_agent_integration_v2"
        )
        importlib.reload(integration_module)
        importlib.reload(tool_calling_module)

    messages = [str(item.message) for item in caught if item.category is DeprecationWarning]
    assert any("enhanced_agent_integration_v2" in message for message in messages)
