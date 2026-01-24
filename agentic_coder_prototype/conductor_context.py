"""
Protocol defining the interface that conductor helper modules require.

This protocol allows helper functions to be typed more precisely than `Any`,
while still allowing the actual `OpenAIConductor` class to implement it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .execution.agent_executor import AgentToolExecutor
from .execution.enhanced_executor import EnhancedToolExecutor
from .guardrail_orchestrator import GuardrailOrchestrator
from .logging_v2 import LoggerV2Manager
from .logging_v2.markdown_transcript import MarkdownTranscriptWriter
from .logging_v2.request_recorder import StructuredRequestRecorder
from .messaging.message_formatter import MessageFormatter
from .provider_metrics import ProviderMetricsCollector
from .permission_broker import PermissionBroker
from .provider_health import RouteHealthManager
from .state.session_state import SessionState


class ConductorContext(Protocol):
    """
    Protocol defining the minimal interface required by conductor helper modules.
    
    This allows helper functions to be typed with `ConductorContext` instead of `Any`,
    improving type safety while keeping the coupling explicit.
    """
    
    # Core attributes
    workspace: str
    config: Dict[str, Any]
    
    # Execution components
    agent_executor: AgentToolExecutor
    enhanced_executor: Optional[EnhancedToolExecutor]
    permission_broker: PermissionBroker
    
    # Guardrails and orchestration
    guardrail_orchestrator: GuardrailOrchestrator
    
    # Logging and formatting
    logger_v2: LoggerV2Manager
    message_formatter: MessageFormatter
    md_writer: MarkdownTranscriptWriter
    structured_request_recorder: StructuredRequestRecorder
    
    # Metrics and health
    provider_metrics: ProviderMetricsCollector
    route_health: RouteHealthManager
    
    # Execution method
    def _exec_raw(self, call: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a raw tool call."""
        ...
    
    # Patch-related methods (delegated to conductor_patching)
    def _synthesize_patch_blocks(self, message_text: Optional[str]) -> List[str]:
        """Extract patch blocks from message text."""
        ...
    
    def _normalize_patch_block(self, block: str) -> Optional[str]:
        """Normalize a patch block."""
        ...
    
    def _convert_patch_to_unified(self, patch_text: str) -> Optional[str]:
        """Convert patch text to unified diff format."""
        ...
    
    def _expand_multi_file_patches(self, parsed_calls: List[Any], session_state: Any, markdown_logger: Any) -> List[Any]:
        """Expand multi-file patches into individual calls."""
        ...
    
    # Additional helper methods (many delegate to conductor_components)
    def _normalize_assistant_text(self, text: Optional[str]) -> str:
        """Normalize assistant text."""
        ...
    
    def _completion_guard_check(self, session_state: SessionState) -> tuple[bool, Optional[str]]:
        """Check completion guard."""
        ...
    
    def _emit_completion_guard_feedback(self, session_state: SessionState, markdown_logger: Any, reason: str, stream_responses: bool) -> bool:
        """Emit completion guard feedback."""
        ...
    
    def _record_diff_metrics(self, tool_parsed: Any, tool_result: Dict[str, Any]) -> None:
        """Record diff metrics."""
        ...
    
    def _is_test_command(self, command: str) -> bool:
        """Check if command is a test command."""
        ...
    
    def _is_read_only_tool(self, tool_name: str) -> bool:
        """Check if tool is read-only."""
        ...
    
    def _record_lsp_reward_metrics(self, session_state: SessionState, turn_index: Optional[int]) -> None:
        """Record LSP reward metrics."""
        ...
    
    def _record_test_reward_metric(self, session_state: SessionState, turn_index: Optional[int], test_success: Optional[float]) -> None:
        """Record test reward metric."""
        ...
    
    def _get_model_routing_preferences(self, route_id: Optional[str]) -> Dict[str, Any]:
        """Get model routing preferences."""
        ...
    
    def _select_fallback_route(self, route_id: Optional[str], provider_id: Optional[str], model: str, explicit_fallbacks: List[str]) -> tuple[Optional[str], Optional[str]]:
        """Select fallback route."""
        ...
    
    def _log_routing_event(self, session_state: SessionState, markdown_logger: Any, turn_index: Optional[int], tag: str, message: str, payload: Dict[str, Any]) -> None:
        """Log routing event."""
        ...
    
    def _update_health_metadata(self, session_state: SessionState) -> None:
        """Update health metadata."""
        ...
