"""
Integration layer for the enhanced tool calling system with existing agent architecture.

Provides backward compatibility while enabling new format selection and performance features.
"""

from __future__ import annotations

import json
import os
import time
import logging
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from ..dialects.enhanced_base_dialect import (
    EnhancedBaseDialect,
    EnhancedToolDefinition,
    EnhancedToolParameter,
    ParsedToolCall,
    ToolCallExecutionMetrics,
    TaskType
)
from ..execution.enhanced_dialect_manager import EnhancedDialectManager
from ..monitoring.performance_monitor import PerformanceMonitor
from ..core.config_schema import ConfigurationManager, ToolCallingConfig

# Import existing dialects for backward compatibility
from ..core.core import ToolDefinition, ToolParameter, ToolCallParsed, BaseToolDialect
from ..execution.composite import CompositeToolCaller

# Import new dialects
from ..dialects.openai_function_calling import OpenAIFunctionCallingDialect
from ..dialects.unified_diff import UnifiedDiffDialect
from ..dialects.anthropic_xml import AnthropicXMLDialect
from ..dialects.json_block import JSONBlockDialect
from ..dialects.yaml_command import YAMLCommandDialect


class EnhancedAgentToolManager:
    """Enhanced tool manager that integrates with existing agent systems."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = logging.getLogger(__name__)
        
        # Load configuration
        self.config_manager = ConfigurationManager({"tool_calling": self.config})
        self.tool_config = self.config_manager.get_tool_calling_config()
        
        # Initialize dialect manager
        self.dialect_manager = EnhancedDialectManager({"tool_calling": self.config})
        
        # Initialize performance monitor
        if self.tool_config.performance_monitoring.enabled:
            self.performance_monitor = PerformanceMonitor({
                "database_path": self.tool_config.performance_monitoring.database_path
            })
        else:
            self.performance_monitor = None
        
        # Register all available dialects
        self._register_dialects()
        
        # Backward compatibility with existing CompositeToolCaller
        self.legacy_caller = None
        self._setup_legacy_compatibility()
    
    def _register_dialects(self):
        """Register all available dialects with the manager."""
        # Register new enhanced dialects
        dialects = [
            OpenAIFunctionCallingDialect(),
            UnifiedDiffDialect(),
            AnthropicXMLDialect(),
            JSONBlockDialect(),
            YAMLCommandDialect()
        ]
        
        for dialect in dialects:
            format_config = self.tool_config.formats.get(dialect.format.value)
            if format_config is None or format_config.enabled:
                self.dialect_manager.register_dialect(dialect)
                self.logger.info(f"Registered enhanced dialect: {dialect.format.value}")
    
    def _setup_legacy_compatibility(self):
        """Setup compatibility with existing CompositeToolCaller."""
        try:
            # Import existing dialects for backward compatibility
            from ..dialects.pythonic02 import Pythonic02Dialect
            from ..dialects.pythonic_inline import PythonicInlineDialect
            from ..dialects.aider_diff import AiderDiffDialect
            from ..dialects.opencode_patch import OpenCodePatchDialect
            from ..dialects.bash_block import BashBlockDialect
            
            legacy_dialects = [
                Pythonic02Dialect(),
                PythonicInlineDialect(),
                AiderDiffDialect(),
                OpenCodePatchDialect(),
                BashBlockDialect()
            ]
            
            self.legacy_caller = CompositeToolCaller(legacy_dialects)
            self.logger.info("Legacy dialect compatibility enabled")
            
        except ImportError as e:
            self.logger.warning(f"Could not setup legacy compatibility: {e}")
    
    def convert_legacy_tools(self, legacy_tools: List[ToolDefinition]) -> List[EnhancedToolDefinition]:
        """Convert legacy tool definitions to enhanced format."""
        enhanced_tools = []
        
        for tool in legacy_tools:
            enhanced_params = []
            for param in tool.parameters:
                enhanced_params.append(EnhancedToolParameter(
                    name=param.name,
                    type=param.type,
                    description=param.description,
                    default=param.default,
                    required=(param.default is None)  # Infer required from default
                ))
            
            enhanced_tool = EnhancedToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=enhanced_params,
                type_id=tool.type_id,
                blocking=getattr(tool, 'blocking', False)
            )
            
            enhanced_tools.append(enhanced_tool)
        
        return enhanced_tools
    
    def get_optimal_format(self, 
                          model_id: str, 
                          content: str = "", 
                          context: Optional[Dict[str, Any]] = None,
                          user_id: Optional[str] = None) -> str:
        """Get optimal format for given context."""
        return self.dialect_manager.select_optimal_format(
            model_id=model_id,
            content=content,
            context=context or {},
            user_id=user_id
        )
    
    def build_system_prompt(self, 
                           tools: List[Union[ToolDefinition, EnhancedToolDefinition]],
                           model_id: str,
                           context: Optional[Dict[str, Any]] = None) -> str:
        """Build system prompt with optimal format selection."""
        # Convert legacy tools if needed
        if tools and isinstance(tools[0], ToolDefinition):
            enhanced_tools = self.convert_legacy_tools(tools)
        else:
            enhanced_tools = tools
        
        # Select optimal format
        selected_format = self.get_optimal_format(
            model_id=model_id,
            context=context or {}
        )
        
        # Get dialect and build prompt
        dialect = self.dialect_manager.dialects.get(selected_format)
        if not dialect:
            # Fallback to legacy system
            if self.legacy_caller:
                return self.legacy_caller.build_prompt(tools)
            return ""
        
        return dialect.format_tools_for_prompt(enhanced_tools)
    
    def parse_tool_calls(self, 
                        content: str, 
                        tools: List[Union[ToolDefinition, EnhancedToolDefinition]],
                        model_id: str,
                        context: Optional[Dict[str, Any]] = None,
                        user_id: Optional[str] = None) -> List[Union[ToolCallParsed, ParsedToolCall]]:
        """Parse tool calls using enhanced format selection."""
        start_time = time.time()
        
        try:
            # Convert legacy tools if needed
            if tools and isinstance(tools[0], ToolDefinition):
                enhanced_tools = self.convert_legacy_tools(tools)
            else:
                enhanced_tools = tools
            
            # Try enhanced parsing first
            selected_format, parsed_calls = self.dialect_manager.execute_with_format_selection(
                tools=enhanced_tools,
                content=content,
                model_id=model_id,
                context=context or {},
                user_id=user_id
            )
            
            # Record success metrics
            if self.performance_monitor:
                metrics = ToolCallExecutionMetrics(
                    format=selected_format,
                    model_id=model_id,
                    task_type=context.get("task_type", "general") if context else "general",
                    success=True,
                    execution_time=time.time() - start_time,
                    token_count=context.get("token_count", 0) if context else 0
                )
                self.performance_monitor.record_execution(metrics)
            
            return parsed_calls
            
        except Exception as e:
            # Record failure metrics
            if self.performance_monitor:
                metrics = ToolCallExecutionMetrics(
                    format="unknown",
                    model_id=model_id,
                    task_type=context.get("task_type", "general") if context else "general",
                    success=False,
                    error_type=type(e).__name__,
                    execution_time=time.time() - start_time,
                    token_count=context.get("token_count", 0) if context else 0
                )
                self.performance_monitor.record_execution(metrics)
            
            # Fallback to legacy parsing
            if self.legacy_caller:
                try:
                    return self.legacy_caller.parse_all(content, tools)
                except Exception:
                    pass
            
            # Re-raise original exception if all parsing fails
            raise e
    
    def format_for_provider(self, 
                           tools: List[Union[ToolDefinition, EnhancedToolDefinition]],
                           provider: str,
                           model_id: str,
                           context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Format tools for specific provider API."""
        # Convert legacy tools if needed
        if tools and isinstance(tools[0], ToolDefinition):
            enhanced_tools = self.convert_legacy_tools(tools)
        else:
            enhanced_tools = tools
        
        # Select optimal format for provider
        context = context or {}
        context["provider"] = provider
        
        selected_format = self.get_optimal_format(
            model_id=model_id,
            context=context
        )
        
        # Get dialect and format for provider
        dialect = self.dialect_manager.dialects.get(selected_format)
        if not dialect:
            # Return basic format
            return {
                "system_prompt_addition": "",
                "requires_user_injection": True,
                "supports_parallel_calls": False
            }
        
        return dialect.format_for_provider(provider, enhanced_tools)
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Get comprehensive performance report."""
        if not self.performance_monitor:
            return {"error": "Performance monitoring not enabled"}
        
        return self.performance_monitor.get_performance_summary()
    
    def update_configuration(self, config_updates: Dict[str, Any]) -> None:
        """Update configuration dynamically."""
        # Update internal config
        self.config.update(config_updates)
        
        # Reload configuration manager
        self.config_manager = ConfigurationManager({"tool_calling": self.config})
        self.tool_config = self.config_manager.get_tool_calling_config()
        
        # Update dialect manager
        self.dialect_manager = EnhancedDialectManager({"tool_calling": self.config})
        self._register_dialects()
        
        self.logger.info("Configuration updated successfully")
    
    def enable_format(self, format_name: str) -> None:
        """Enable a specific format."""
        self.config_manager.enable_format(format_name)
        self.update_configuration(self.config_manager.config_dict.get("tool_calling", {}))
    
    def disable_format(self, format_name: str) -> None:
        """Disable a specific format."""
        self.config_manager.disable_format(format_name)
        self.update_configuration(self.config_manager.config_dict.get("tool_calling", {}))
    
    def set_strategy(self, strategy: str) -> None:
        """Set the format selection strategy."""
        self.config_manager.set_strategy(strategy)
        self.update_configuration(self.config_manager.config_dict.get("tool_calling", {}))
    
    def compare_formats(self, 
                       formats: List[str], 
                       task_type: Optional[str] = None, 
                       hours: float = 24.0) -> Dict[str, Any]:
        """Compare performance between formats."""
        if not self.performance_monitor:
            return {"error": "Performance monitoring not enabled"}
        
        return self.performance_monitor.compare_formats(formats, task_type, hours)


class LegacyAgentAdapter:
    """Adapter to integrate enhanced tool calling with legacy agent systems."""
    
    def __init__(self, enhanced_manager: EnhancedAgentToolManager):
        self.enhanced_manager = enhanced_manager
        self.logger = logging.getLogger(__name__)
    
    def adapt_openai_conductor(self, conductor_class):
        """Adapt OpenAIConductor to use enhanced tool calling."""
        
        class EnhancedOpenAIConductor(conductor_class):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.enhanced_tool_manager = self.enhanced_manager
            
            def run_agentic_loop(self, 
                               system_prompt: str,
                               user_prompt: str,
                               model: str,
                               **kwargs) -> Dict[str, Any]:
                """Enhanced agentic loop with new tool calling system."""
                # Inject enhanced tool calling context
                context = {
                    "provider": "openai" if not os.getenv("OPENROUTER_API_KEY") else "openrouter",
                    "model_id": model,
                    "task_type": "general"
                }
                
                # Get optimal format for this context
                optimal_format = self.enhanced_tool_manager.get_optimal_format(
                    model_id=model,
                    content=user_prompt,
                    context=context
                )
                
                self.logger.info(f"Using optimal format: {optimal_format} for model: {model}")
                
                # Call original method with enhanced context
                kwargs["context"] = context
                return super().run_agentic_loop(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    **kwargs
                )
        
        return EnhancedOpenAIConductor
    
    def adapt_agent_session(self, agent_class):
        """Adapt OpenCodeAgent to use enhanced tool calling."""
        
        class EnhancedOpenCodeAgent(agent_class):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.enhanced_tool_manager = self.enhanced_manager
            
            def run_message(self, parts: List[Dict[str, Any]]) -> Dict[str, Any]:
                """Enhanced message processing with new tool calling."""
                # Add context for tool call parts
                enhanced_parts = []
                for part in parts:
                    if part.get("type") == "tool_call":
                        # Add enhanced context
                        part["context"] = {
                            "session_id": self.session_id,
                            "timestamp": time.time()
                        }
                    enhanced_parts.append(part)
                
                return super().run_message(enhanced_parts)
        
        return EnhancedOpenCodeAgent


def create_enhanced_agent_integration(config: Optional[Dict[str, Any]] = None) -> EnhancedAgentToolManager:
    """Factory function to create enhanced agent integration."""
    return EnhancedAgentToolManager(config)


def migrate_existing_agent(agent_instance, 
                          config: Optional[Dict[str, Any]] = None) -> Any:
    """Migrate existing agent instance to use enhanced tool calling."""
    enhanced_manager = create_enhanced_agent_integration(config)
    
    # Add enhanced tool manager to existing agent
    agent_instance.enhanced_tool_manager = enhanced_manager
    
    # Monkey patch parsing methods if they exist
    if hasattr(agent_instance, 'parse_tool_calls'):
        original_parse = agent_instance.parse_tool_calls
        
        def enhanced_parse(content: str, tools: List, **kwargs):
            try:
                return enhanced_manager.parse_tool_calls(
                    content=content,
                    tools=tools,
                    model_id=kwargs.get("model_id", "unknown"),
                    context=kwargs.get("context"),
                    user_id=kwargs.get("user_id")
                )
            except Exception:
                # Fallback to original method
                return original_parse(content, tools, **kwargs)
        
        agent_instance.parse_tool_calls = enhanced_parse
    
    return agent_instance


# Utility functions for common integration patterns
def get_optimal_format_for_model(model_id: str, 
                                config: Optional[Dict[str, Any]] = None) -> str:
    """Get optimal format for a specific model."""
    manager = EnhancedAgentToolManager(config)
    return manager.get_optimal_format(model_id=model_id)


def get_performance_summary(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get performance summary across all formats."""
    manager = EnhancedAgentToolManager(config)
    return manager.get_performance_report()


def benchmark_formats(formats: List[str], 
                     model_id: str,
                     test_content: str,
                     iterations: int = 10,
                     config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Benchmark different formats for comparison."""
    import time
    
    manager = EnhancedAgentToolManager(config)
    results = {}
    
    for format_name in formats:
        format_results = {
            "success_count": 0,
            "error_count": 0,
            "avg_execution_time": 0.0,
            "errors": []
        }
        
        total_time = 0.0
        
        for _ in range(iterations):
            start_time = time.time()
            
            try:
                # Force use of specific format
                dialect = manager.dialect_manager.dialects.get(format_name)
                if dialect:
                    parsed = dialect.parse_tool_calls(test_content)
                    format_results["success_count"] += 1
                else:
                    format_results["error_count"] += 1
                    format_results["errors"].append(f"Format {format_name} not available")
            
            except Exception as e:
                format_results["error_count"] += 1
                format_results["errors"].append(str(e))
            
            total_time += time.time() - start_time
        
        format_results["avg_execution_time"] = total_time / iterations
        format_results["success_rate"] = format_results["success_count"] / iterations
        
        results[format_name] = format_results
    
    return results