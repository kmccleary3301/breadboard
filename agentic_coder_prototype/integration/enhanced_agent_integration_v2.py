"""
Enhanced Agent Integration V2
Incorporates findings from OpenCode and Crush analysis for improved tool calling.

Key improvements:
- per_turn_append mode support for gpt-5-nano
- Provider-specific optimizations
- Streaming tool execution (Crush-style)
- Lazy tool loading (Crush-style)  
- Enhanced error handling and permission systems
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, List, Any, Optional, Callable, Set
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from ..core.core import ToolDefinition, ToolParameter
from ..execution.enhanced_dialect_manager import EnhancedDialectManager
from ..monitoring.performance_monitor import PerformanceMonitor
from ..compilation.system_prompt_compiler import SystemPromptCompiler
from ..compilation.multi_level_prompt_compiler import MultiLevelPromptCompiler, PromptCompilationConfig
from ..dialects.enhanced_base_dialect import ToolCallExecutionMetrics

logger = logging.getLogger(__name__)


class EnhancedAgentIntegrationV2:
    """
    Enhanced agent integration incorporating OpenCode and Crush insights.
    
    Key features:
    - Multi-mode prompt compilation (system_once, per_turn_append, etc.)
    - Provider-specific optimizations
    - Streaming tool execution with cancellation
    - Lazy tool loading and caching
    - Permission-gated tool execution
    - Performance monitoring and A/B testing
    """
    
    def __init__(self, config_path: str = None, tools: List[ToolDefinition] = None, use_provided_tools: bool = True):
        """Initialize enhanced agent integration"""
        
        # Load configuration
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config_dict = json.load(f)
        else:
            # Default configuration
            config_dict = {
                "tool_calling": {
                    "strategy": "adaptive",
                    "formats": {
                        "aider_diff": {"success_rate": 0.59, "priority": 1},
                        "opencode_patch": {"success_rate": 0.45, "priority": 2},
                        "unified_diff": {"success_rate": 0.26, "priority": 3}
                    }
                }
            }
        
        # Core components
        self.dialect_manager = EnhancedDialectManager(config_dict)
        self.performance_monitor = PerformanceMonitor()
        self.prompt_compiler = SystemPromptCompiler()
        self.multi_level_compiler = MultiLevelPromptCompiler()
        
        # Tool management (Crush-style lazy loading)
        self._tools = tools or []
        self._tool_cache = {}
        self._lazy_tools_loaded = False
        self._use_provided_tools = use_provided_tools
        
        # Configuration
        self.config = config_dict.get("tool_calling", {})
        self.logger = logging.getLogger(__name__)
        
        # Execution state
        self._active_sessions: Dict[str, dict] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        # Provider-specific settings (OpenCode-style)
        self.provider_optimizations = {
            "openai": {
                "temperature": 0.1,
                "top_p": 0.95,
                "supports_streaming": True,
                "tool_call_format": "function_calling"
            },
            "anthropic": {
                "temperature": 0.0, 
                "supports_caching": True,
                "supports_streaming": True,
                "tool_call_format": "xml_blocks"
            },
            "google": {
                "temperature": 0.1,
                "supports_streaming": True, 
                "tool_call_format": "function_calling",
                "sanitize_schemas": True
            }
        }
        
        logger.info("Enhanced Agent Integration V2 initialized")

    def _lazy_load_tools(self) -> List[ToolDefinition]:
        """Lazy load tools on first access (Crush pattern)"""
        if not self._lazy_tools_loaded:
            # In production, this would initialize expensive tools
            # like LSP clients, MCP servers, etc.
            if self._use_provided_tools and self._tools:
                logger.debug(f"Using provided tools: {[t.name for t in self._tools]}")
            else:
                logger.debug("Lazy loading tools...")
            self._lazy_tools_loaded = True
        return self._tools
    
    def get_provider_config(self, provider_id: str, model_id: str) -> Dict[str, Any]:
        """Get provider-specific configuration (OpenCode pattern)"""
        base_config = self.provider_optimizations.get(provider_id, {})
        
        # Model-specific overrides
        if "gpt-5-nano" in model_id:
            base_config = {
                **base_config,
                "requires_per_turn_append": True,
                "tool_call_format": "xml_blocks",  # Better for smaller models
                "max_parallel_tools": 1,  # Sequential execution
                "timeout_per_tool": 60,
                "preferred_prompt_mode": "sys_compiled_per_turn_persistent--short-medium"  # Multi-level mode
            }
        elif "claude" in model_id.lower():
            base_config = {
                **base_config,
                "supports_thinking": True,
                "cache_system_prompt": True,
                "max_parallel_tools": 3,
                "preferred_prompt_mode": "sys_compiled_per_turn_persistent--long-medium"  # Multi-level mode
            }
        elif "gpt-4" in model_id.lower():
            base_config = {
                **base_config,
                "preferred_prompt_mode": "sys_compiled_per_turn_persistent--medium-long"  # Multi-level mode
            }
            
        return base_config
    
    def compile_system_prompt(self, tools: List[ToolDefinition], provider_id: str, 
                            model_id: str, tool_prompt_mode: str = "system_once",
                            primary_prompt_path: str = None) -> tuple[str, str]:
        """
        Compile system prompt based on mode and provider.
        Supports both legacy and multi-level compilation modes.
        Returns (system_prompt, tools_hash)
        """
        # Check if using new multi-level compilation mode
        if tool_prompt_mode.startswith("sys_compiled_per_turn_"):
            try:
                # Parse multi-level configuration
                config = PromptCompilationConfig.from_mode_string(tool_prompt_mode)
                config.provider_id = provider_id
                config.model_id = model_id
                
                # Get optimal dialects for this provider/model
                available_dialects = self.dialect_manager.get_available_formats(
                    provider=provider_id, model_id=model_id
                )
                
                # Use multi-level compiler
                system_prompt, compilation_hash = self.multi_level_compiler.compile_system_prompt(
                    config=config,
                    tools=tools,
                    dialects=available_dialects,
                    primary_prompt_path=primary_prompt_path
                )
                
                return system_prompt, compilation_hash
                
            except ValueError as e:
                self.logger.warning(f"Invalid multi-level mode '{tool_prompt_mode}': {e}. Falling back to legacy mode.")
                tool_prompt_mode = "system_once"  # Fallback
        
        # Legacy compilation mode
        if not primary_prompt_path:
            primary_prompt_path = "implementations/system_prompts/default.md"
        
        primary_prompt = ""
        if Path(primary_prompt_path).exists():
            primary_prompt = Path(primary_prompt_path).read_text(encoding='utf-8')
        else:
            self.logger.warning(f"System prompt file not found: {primary_prompt_path}")
        
        # Get optimal dialects for this provider/model
        available_dialects = self.dialect_manager.get_available_formats(
            provider=provider_id, model_id=model_id
        )
        
        # Use legacy compiler
        system_prompt, tools_hash = self.prompt_compiler.get_or_create_system_prompt(
            tools=tools,
            dialects=available_dialects,
            primary_prompt=primary_prompt,
            tool_prompt_mode=tool_prompt_mode
        )
        
        return system_prompt, tools_hash
    
    def format_user_message_with_tools(self, user_message: str, tools: List[ToolDefinition],
                                     provider_id: str, model_id: str, 
                                     session_context: Dict[str, Any] = None,
                                     tool_prompt_mode: str = "per_turn_append") -> str:
        """
        Format user message with per-turn tool availability.
        Supports both legacy and multi-level compilation modes.
        """
        # Check if using multi-level compilation mode
        if tool_prompt_mode.startswith("sys_compiled_per_turn_"):
            try:
                # Parse multi-level configuration
                config = PromptCompilationConfig.from_mode_string(tool_prompt_mode)
                config.provider_id = provider_id
                config.model_id = model_id
                
                # Get available dialects
                available_dialects = self.dialect_manager.get_available_formats(
                    provider=provider_id, model_id=model_id
                )
                
                # Get available tools
                enabled_tools = [tool.name for tool in tools]
                
                # Use multi-level compiler for per-turn formatting
                formatted_message = self.multi_level_compiler.compile_per_turn_prompt(
                    config=config,
                    available_tools=enabled_tools,
                    available_dialects=available_dialects,
                    user_message=user_message
                )
                
                return formatted_message
                
            except ValueError as e:
                self.logger.warning(f"Invalid multi-level mode '{tool_prompt_mode}': {e}. Using legacy formatting.")
        
        # Legacy formatting (per_turn_append mode)
        # Get optimal format for this context
        context_with_provider = {**(session_context or {}), "provider": provider_id}
        optimal_format = self.dialect_manager.select_optimal_format(
            model_id=model_id,
            content=user_message,
            context=context_with_provider
        )
        
        # Get available dialects
        available_dialects = self.dialect_manager.get_available_formats(
            provider=provider_id, model_id=model_id
        )
        
        # Get preferred format ordering based on research
        preferred_formats = self.prompt_compiler.get_preferred_formats(available_dialects)
        
        # Format tool availability section
        enabled_tools = [tool.name for tool in tools]
        tools_section = self.prompt_compiler.format_per_turn_availability(
            enabled_tools=enabled_tools,
            enabled_dialects=preferred_formats
        )
        
        # Combine user message with tools
        combined_message = f"{user_message}\n\n{tools_section}"
        
        return combined_message
    
    async def execute_tool_streaming(self, tool_name: str, parameters: Dict[str, Any],
                                   session_id: str, timeout: int = 60,
                                   permission_callback: Callable[[str, str, Dict], bool] = None) -> Dict[str, Any]:
        """
        Execute tool with streaming support and permission checking (Crush pattern).
        """
        start_time = time.time()
        
        try:
            # Find tool
            tools = self._lazy_load_tools()
            tool = next((t for t in tools if t.name == tool_name), None)
            if not tool:
                raise ValueError(f"Tool {tool_name} not found")
            
            # Permission check (Crush pattern)
            if permission_callback and hasattr(tool, 'requires_permission'):
                if tool.requires_permission:
                    allowed = permission_callback(session_id, tool_name, parameters)
                    if not allowed:
                        raise PermissionError(f"Permission denied for tool {tool_name}")
            
            # Execute with timeout
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(
                self._executor,
                self._execute_tool_sync,
                tool, parameters, session_id
            )
            
            result = await asyncio.wait_for(future, timeout=timeout)
            
            # Record performance metrics
            execution_time = time.time() - start_time
            metric = ToolCallExecutionMetrics(
                format=tool_name,
                model_id=self._active_sessions.get(session_id, {}).get('model_id', 'unknown'),
                task_type='tool_execution',
                success=True,
                execution_time=execution_time,
                call_id=session_id
            )
            self.performance_monitor.record_execution(metric)
            
            return {
                'success': True,
                'result': result,
                'execution_time': execution_time,
                'tool_name': tool_name
            }
            
        except asyncio.TimeoutError:
            logger.warning(f"Tool {tool_name} timed out after {timeout}s")
            metric = ToolCallExecutionMetrics(
                format=tool_name,
                model_id=self._active_sessions.get(session_id, {}).get('model_id', 'unknown'),
                task_type='tool_execution',
                success=False,
                execution_time=timeout,
                error_type='timeout',
                call_id=session_id
            )
            self.performance_monitor.record_execution(metric)
            return {
                'success': False,
                'error': 'timeout',
                'execution_time': timeout,
                'tool_name': tool_name
            }
            
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Tool {tool_name} failed: {e}")
            metric = ToolCallExecutionMetrics(
                format=tool_name,
                model_id=self._active_sessions.get(session_id, {}).get('model_id', 'unknown'),
                task_type='tool_execution',
                success=False,
                execution_time=execution_time,
                error_type=type(e).__name__,
                call_id=session_id
            )
            self.performance_monitor.record_execution(metric)
            return {
                'success': False,
                'error': str(e),
                'execution_time': execution_time,
                'tool_name': tool_name
            }
    
    def _execute_tool_sync(self, tool: ToolDefinition, parameters: Dict[str, Any], session_id: str) -> Any:
        """Synchronous tool execution wrapper"""
        # In production, this would call the actual tool implementation
        # For now, return a mock result
        return {
            'tool': tool.name,
            'parameters': parameters,
            'session_id': session_id,
            'status': 'completed'
        }
    
    def create_session(self, session_id: str, provider_id: str, model_id: str,
                      tool_prompt_mode: str = "system_once") -> Dict[str, Any]:
        """Create a new agent session with provider-specific optimizations"""
        
        # Get provider configuration
        provider_config = self.get_provider_config(provider_id, model_id)
        
        # Determine tool prompt mode based on provider config
        if provider_config.get("preferred_prompt_mode"):
            tool_prompt_mode = provider_config["preferred_prompt_mode"]
            logger.info(f"Using preferred multi-level mode '{tool_prompt_mode}' for {model_id}")
        elif provider_config.get("requires_per_turn_append", False):
            tool_prompt_mode = "per_turn_append"
            logger.info(f"Using legacy per_turn_append mode for {model_id}")
        
        # Initialize session state
        session_state = {
            'session_id': session_id,
            'provider_id': provider_id,
            'model_id': model_id,
            'tool_prompt_mode': tool_prompt_mode,
            'provider_config': provider_config,
            'created_at': time.time(),
            'tool_executions': [],
            'performance_metrics': {}
        }
        
        self._active_sessions[session_id] = session_state
        
        logger.info(f"Created session {session_id} for {provider_id}/{model_id} with {tool_prompt_mode} mode")
        
        return session_state
    
    def get_session_prompt_data(self, session_id: str, user_message: str = "",
                               custom_tools: List[ToolDefinition] = None) -> Dict[str, Any]:
        """
        Get prompt data for a session based on its configuration.
        
        Returns:
            dict with keys: system_prompt, formatted_user_message, tools_hash, mode
        """
        session = self._active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        # Get tools (custom or default)
        tools = custom_tools or self._lazy_load_tools()
        
        # Get provider/model info
        provider_id = session['provider_id']
        model_id = session['model_id']
        tool_prompt_mode = session['tool_prompt_mode']
        
        if tool_prompt_mode == "per_turn_append" or tool_prompt_mode.startswith("sys_compiled_per_turn_"):
            # System prompt + tools in user message (legacy per_turn_append or new multi-level modes)
            system_prompt, tools_hash = self.compile_system_prompt(
                tools=tools,
                provider_id=provider_id,
                model_id=model_id,
                tool_prompt_mode=tool_prompt_mode
            )
            
            # Format user message with tools
            formatted_user_message = self.format_user_message_with_tools(
                user_message=user_message,
                tools=tools,
                provider_id=provider_id,
                model_id=model_id,
                session_context={'session_id': session_id},
                tool_prompt_mode=tool_prompt_mode
            )
            
            # Determine mode name for response
            if tool_prompt_mode.startswith("sys_compiled_per_turn_"):
                mode_name = tool_prompt_mode  # Use full multi-level mode name
            else:
                mode_name = 'per_turn_append'  # Legacy mode
            
            return {
                'system_prompt': system_prompt,
                'formatted_user_message': formatted_user_message,
                'tools_hash': tools_hash,
                'mode': mode_name,
                'provider_config': session['provider_config']
            }
        
        else:
            # Comprehensive system prompt with tools
            system_prompt, tools_hash = self.compile_system_prompt(
                tools=tools,
                provider_id=provider_id,
                model_id=model_id,
                tool_prompt_mode=tool_prompt_mode
            )
            
            return {
                'system_prompt': system_prompt,
                'formatted_user_message': user_message,  # No modification needed
                'tools_hash': tools_hash,
                'mode': 'system_comprehensive',
                'provider_config': session['provider_config']
            }
    
    def get_performance_summary(self, session_id: str = None) -> Dict[str, Any]:
        """Get performance metrics for analysis"""
        if session_id:
            session = self._active_sessions.get(session_id, {})
            return {
                'session_metrics': session.get('performance_metrics', {}),
                'tool_executions': session.get('tool_executions', [])
            }
        
        # Global performance summary
        return {
            'total_sessions': len(self._active_sessions),
            'active_sessions': list(self._active_sessions.keys()),
            'performance_database': str(self.performance_monitor.database.db_path)
        }
    
    def cleanup_session(self, session_id: str):
        """Clean up session resources"""
        if session_id in self._active_sessions:
            session = self._active_sessions[session_id]
            logger.info(f"Cleaning up session {session_id}")
            
            # Record final metrics
            session_duration = time.time() - session['created_at']
            logger.info(f"Session {session_id} duration: {session_duration:.2f}s")
            
            del self._active_sessions[session_id]
    
    def shutdown(self):
        """Shutdown integration and cleanup resources"""
        logger.info("Shutting down Enhanced Agent Integration V2")
        
        # Close executor
        self._executor.shutdown(wait=True)
        
        # Clear sessions
        self._active_sessions.clear()
        
        # Close performance monitor
        if hasattr(self.performance_monitor, 'close'):
            self.performance_monitor.close()