"""
Enhanced dialect manager implementing modular format selection and performance monitoring.

Provides dynamic format selection, A/B testing capabilities, and performance-driven
optimization based on research findings from tool syntax analysis.
"""

from __future__ import annotations

import random
import hashlib
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
import time
import logging

from ..dialects.enhanced_base_dialect import (
    EnhancedBaseDialect, 
    ToolCallFormat, 
    TaskType, 
    EnhancedToolDefinition,
    ParsedToolCall,
    ToolCallExecutionMetrics,
    detect_task_type,
    calculate_format_preference_score
)


class SelectionStrategy(Enum):
    """Format selection strategies."""
    ADAPTIVE = "adaptive"        # Performance-based selection
    FIXED = "fixed"             # Use single configured format
    A_B_TEST = "a_b_test"       # A/B testing between formats
    ROUND_ROBIN = "round_robin" # Cycle through formats
    RANDOM = "random"           # Random selection


@dataclass
class FormatConfiguration:
    """Configuration for a specific format."""
    enabled: bool = True
    priority: int = 1
    models: List[str] = field(default_factory=list)
    use_cases: List[str] = field(default_factory=list)
    min_success_rate: float = 0.5
    fallback: bool = False
    
    # A/B testing configuration
    test_weight: float = 1.0
    test_group: Optional[str] = None


@dataclass 
class ABTestConfiguration:
    """A/B test configuration."""
    name: str
    formats: List[str]
    traffic_split: List[float]
    success_metrics: List[str] = field(default_factory=lambda: ["completion_rate", "error_rate"])
    min_sample_size: int = 100
    confidence_level: float = 0.95
    enabled: bool = True


class FormatDetector:
    """Detect optimal format based on model capabilities and task type."""
    
    # Model-specific format preferences based on research
    MODEL_FORMAT_MAPPING = {
        # OpenAI models - prefer native function calling
        "gpt-4": ["native_function_calling", "unified_diff", "json_block"],
        "gpt-4-turbo": ["native_function_calling", "unified_diff", "json_block"],
        "gpt-4o": ["native_function_calling", "unified_diff", "json_block"],
        "gpt-3.5-turbo": ["native_function_calling", "json_block", "yaml_command"],
        
        # Claude models - prefer XML
        "claude-3": ["anthropic_xml", "unified_diff", "yaml_command"],
        "claude-3.5": ["anthropic_xml", "unified_diff", "json_block"],
        "claude-3-opus": ["anthropic_xml", "unified_diff", "json_block"],
        "claude-3-sonnet": ["anthropic_xml", "unified_diff", "yaml_command"],
        "claude-3-haiku": ["anthropic_xml", "json_block", "yaml_command"],
        
        # Generic/unknown models - use universal formats
        "default": ["unified_diff", "json_block", "yaml_command"]
    }
    
    # Task-specific format preferences based on research
    TASK_FORMAT_PREFERENCES = {
        TaskType.CODE_EDITING: ["unified_diff", "aider_search_replace"],
        TaskType.FILE_MODIFICATION: ["unified_diff", "anthropic_xml"],
        TaskType.API_OPERATIONS: ["native_function_calling", "json_block"],
        TaskType.SHELL_COMMANDS: ["yaml_command", "anthropic_xml"],
        TaskType.DATA_PROCESSING: ["json_block", "native_function_calling"],
        TaskType.CONFIGURATION: ["yaml_command", "json_block"],
        TaskType.GENERAL: ["json_block", "yaml_command"]
    }
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def detect_optimal_format(self, 
                            model_id: str, 
                            task_type: Union[str, TaskType],
                            available_formats: List[str],
                            performance_data: Dict[str, float] = None) -> str:
        """Detect optimal format considering all factors."""
        
        if isinstance(task_type, str):
            try:
                task_type = TaskType(task_type)
            except ValueError:
                task_type = TaskType.GENERAL
        
        # Get model preferences
        model_formats = self.MODEL_FORMAT_MAPPING.get(
            model_id, self.MODEL_FORMAT_MAPPING["default"]
        )
        
        # Get task preferences
        task_formats = [f.value if isinstance(f, ToolCallFormat) else f 
                       for f in self.TASK_FORMAT_PREFERENCES.get(task_type, [])]
        
        # Find intersection of available, model-preferred, and task-suitable formats
        candidate_formats = []
        
        # Priority 1: Formats that are model-preferred, task-suitable, and available
        for fmt in model_formats:
            if fmt in task_formats and fmt in available_formats:
                candidate_formats.append((fmt, 3))  # Highest priority
        
        # Priority 2: Model-preferred and available
        for fmt in model_formats:
            if fmt in available_formats and fmt not in [c[0] for c in candidate_formats]:
                candidate_formats.append((fmt, 2))
        
        # Priority 3: Task-suitable and available  
        for fmt in task_formats:
            if fmt in available_formats and fmt not in [c[0] for c in candidate_formats]:
                candidate_formats.append((fmt, 1))
        
        # Priority 4: Any available format
        for fmt in available_formats:
            if fmt not in [c[0] for c in candidate_formats]:
                candidate_formats.append((fmt, 0))
        
        if not candidate_formats:
            # Fallback to first available format
            return available_formats[0] if available_formats else "xml_python_hybrid"
        
        # Sort by priority and performance data
        if performance_data:
            candidate_formats = self._rank_by_performance(candidate_formats, performance_data)
        else:
            candidate_formats.sort(key=lambda x: x[1], reverse=True)
        
        selected_format = candidate_formats[0][0]
        
        self.logger.debug(f"Selected format {selected_format} for model {model_id}, task {task_type}")
        return selected_format
    
    def _rank_by_performance(self, 
                           candidates: List[Tuple[str, int]], 
                           performance_data: Dict[str, float]) -> List[Tuple[str, int]]:
        """Rank candidates by performance data."""
        
        def score_function(fmt_priority_tuple):
            fmt, priority = fmt_priority_tuple
            performance_score = performance_data.get(fmt, 0.5)  # Default 50%
            # Combine priority and performance (weighted)
            return (priority * 0.3) + (performance_score * 0.7)
        
        return sorted(candidates, key=score_function, reverse=True)


class EnhancedDialectManager:
    """Enhanced dialect manager with format selection and performance monitoring."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("tool_calling", {})
        self.logger = logging.getLogger(__name__)
        
        # Selection strategy
        self.strategy = SelectionStrategy(self.config.get("strategy", "adaptive"))
        
        # Format configurations
        self.format_configs = self._load_format_configurations()
        
        # A/B testing
        self.ab_tests = self._load_ab_test_configurations()
        
        # Components
        self.format_detector = FormatDetector()
        self.dialects: Dict[str, EnhancedBaseDialect] = {}
        
        # Performance tracking
        self.execution_history: List[ToolCallExecutionMetrics] = []
        self.performance_cache: Dict[str, Tuple[float, float]] = {}  # format -> (success_rate, timestamp)
        
        # State for round-robin and A/B testing
        self._round_robin_index = 0
        self._user_assignments: Dict[str, str] = {}  # user_id -> assigned_format
        
    def _load_format_configurations(self) -> Dict[str, FormatConfiguration]:
        """Load format configurations from config."""
        configs = {}
        formats_config = self.config.get("formats", {})
        
        for format_name, format_config in formats_config.items():
            configs[format_name] = FormatConfiguration(
                enabled=format_config.get("enabled", True),
                priority=format_config.get("priority", 1),
                models=format_config.get("models", []),
                use_cases=format_config.get("use_cases", []),
                min_success_rate=format_config.get("min_success_rate", 0.5),
                fallback=format_config.get("fallback", False),
                test_weight=format_config.get("test_weight", 1.0),
                test_group=format_config.get("test_group")
            )
        
        return configs
    
    def _load_ab_test_configurations(self) -> List[ABTestConfiguration]:
        """Load A/B test configurations."""
        ab_config = self.config.get("ab_testing", {})
        if not ab_config.get("enabled", False):
            return []
        
        tests = []
        for test_config in ab_config.get("test_groups", []):
            tests.append(ABTestConfiguration(
                name=test_config["name"],
                formats=test_config["formats"],
                traffic_split=test_config["traffic_split"],
                success_metrics=test_config.get("success_metrics", ["completion_rate", "error_rate"]),
                min_sample_size=test_config.get("min_sample_size", 100),
                confidence_level=test_config.get("confidence_level", 0.95),
                enabled=test_config.get("enabled", True)
            ))
        
        return tests
    
    def register_dialect(self, dialect: EnhancedBaseDialect) -> None:
        """Register a dialect with the manager."""
        format_name = dialect.format.value if hasattr(dialect, 'format') else dialect.type_id
        self.dialects[format_name] = dialect
        
        self.logger.info(f"Registered dialect: {format_name}")
    
    def get_available_formats(self, 
                            model_id: str = None, 
                            provider: str = None) -> List[str]:
        """Get list of available formats for model/provider."""
        available = []
        
        for format_name, dialect in self.dialects.items():
            config = self.format_configs.get(format_name)
            
            # Check if format is enabled
            if config and not config.enabled:
                continue
            
            # Check model compatibility
            if model_id and config and config.models:
                if not any(model in model_id for model in config.models):
                    continue
            
            # Check provider compatibility
            if provider and not dialect.supports_provider(provider):
                continue
            
            available.append(format_name)
        
        return available
    
    def select_optimal_format(self, 
                            model_id: str,
                            content: str = "",
                            context: Dict[str, Any] = None,
                            user_id: str = None) -> str:
        """Select optimal format based on strategy and context."""
        
        context = context or {}
        
        # Detect task type
        task_type = detect_task_type(content, context)
        
        # Get available formats
        available_formats = self.get_available_formats(
            model_id=model_id,
            provider=context.get("provider")
        )
        
        if not available_formats:
            return "xml_python_hybrid"  # Fallback to legacy format
        
        # Apply selection strategy
        if self.strategy == SelectionStrategy.FIXED:
            return self._select_fixed_format(available_formats)
        
        elif self.strategy == SelectionStrategy.A_B_TEST:
            return self._select_ab_test_format(user_id, available_formats)
        
        elif self.strategy == SelectionStrategy.ROUND_ROBIN:
            return self._select_round_robin_format(available_formats)
        
        elif self.strategy == SelectionStrategy.RANDOM:
            return random.choice(available_formats)
        
        elif self.strategy == SelectionStrategy.ADAPTIVE:
            return self._select_adaptive_format(
                model_id, task_type, available_formats, context
            )
        
        else:
            # Default to adaptive
            return self._select_adaptive_format(
                model_id, task_type, available_formats, context
            )
    
    def _select_fixed_format(self, available_formats: List[str]) -> str:
        """Select format using fixed strategy."""
        # Find the highest priority enabled format
        best_format = None
        best_priority = -1
        
        for format_name in available_formats:
            config = self.format_configs.get(format_name)
            if config and config.priority > best_priority:
                best_format = format_name
                best_priority = config.priority
        
        return best_format or available_formats[0]
    
    def _select_ab_test_format(self, user_id: str, available_formats: List[str]) -> str:
        """Select format using A/B testing."""
        if not user_id or not self.ab_tests:
            return self._select_fixed_format(available_formats)
        
        # Check if user is already assigned to a test
        if user_id in self._user_assignments:
            assigned_format = self._user_assignments[user_id]
            if assigned_format in available_formats:
                return assigned_format
        
        # Find applicable A/B test
        for test in self.ab_tests:
            if not test.enabled:
                continue
            
            # Check if test formats are available
            test_formats = [f for f in test.formats if f in available_formats]
            if len(test_formats) < 2:
                continue
            
            # Assign user to test group
            user_hash = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100
            
            cumulative_weight = 0
            for i, weight in enumerate(test.traffic_split):
                cumulative_weight += weight * 100
                if user_hash < cumulative_weight:
                    selected_format = test_formats[min(i, len(test_formats) - 1)]
                    self._user_assignments[user_id] = selected_format
                    self.logger.debug(f"Assigned user {user_id} to A/B test format: {selected_format}")
                    return selected_format
        
        # No applicable A/B test, use fixed strategy
        return self._select_fixed_format(available_formats)
    
    def _select_round_robin_format(self, available_formats: List[str]) -> str:
        """Select format using round-robin strategy."""
        if not available_formats:
            return "xml_python_hybrid"
        
        selected_format = available_formats[self._round_robin_index % len(available_formats)]
        self._round_robin_index += 1
        
        return selected_format
    
    def _select_adaptive_format(self, 
                              model_id: str,
                              task_type: TaskType,
                              available_formats: List[str],
                              context: Dict[str, Any]) -> str:
        """Select format using adaptive performance-based strategy."""
        
        # Get performance data
        performance_data = {}
        for format_name in available_formats:
            success_rate = self._get_cached_success_rate(format_name)
            
            # Apply minimum success rate filter
            config = self.format_configs.get(format_name)
            if config and success_rate < config.min_success_rate:
                continue
            
            performance_data[format_name] = success_rate
        
        if not performance_data:
            # No formats meet minimum success rate, use fallback
            fallback_formats = [f for f in available_formats 
                              if self.format_configs.get(f, {}).fallback]
            if fallback_formats:
                return fallback_formats[0]
            return available_formats[0]
        
        # Use format detector with performance data
        return self.format_detector.detect_optimal_format(
            model_id, task_type, list(performance_data.keys()), performance_data
        )
    
    def _get_cached_success_rate(self, format_name: str) -> float:
        """Get cached success rate for format."""
        current_time = time.time()
        
        # Check cache (5 minute TTL)
        if format_name in self.performance_cache:
            success_rate, timestamp = self.performance_cache[format_name]
            if current_time - timestamp < 300:  # 5 minutes
                return success_rate
        
        # Calculate from dialect if available
        dialect = self.dialects.get(format_name)
        if dialect:
            success_rate = dialect.get_success_rate()
        else:
            # Use default based on research data
            format_defaults = {
                "unified_diff": 0.61,  # Aider research
                "native_function_calling": 0.85,  # UC Berkeley leaderboard
                "anthropic_xml": 0.83,  # Production reports
                "aider_search_replace": 0.23,  # Aider research
                "json_block": 0.70,  # Estimated
                "yaml_command": 0.65,  # Estimated
                "xml_python_hybrid": 0.50  # Unknown baseline
            }
            success_rate = format_defaults.get(format_name, 0.50)
        
        # Cache result
        self.performance_cache[format_name] = (success_rate, current_time)
        
        return success_rate
    
    def execute_with_format_selection(self, 
                                    tools: List[EnhancedToolDefinition],
                                    content: str,
                                    model_id: str,
                                    context: Dict[str, Any] = None,
                                    user_id: str = None) -> Tuple[str, List[ParsedToolCall]]:
        """Execute tool calling with automatic format selection."""
        
        # Select optimal format
        selected_format = self.select_optimal_format(
            model_id, content, context, user_id
        )
        
        # Get dialect
        dialect = self.dialects.get(selected_format)
        if not dialect:
            raise ValueError(f"Dialect not found for format: {selected_format}")
        
        # Execute parsing
        start_time = time.time()
        try:
            parsed_calls = dialect.parse_tool_calls(content)
            execution_time = time.time() - start_time
            
            # Record success
            self._record_execution_metrics(
                format=selected_format,
                model_id=model_id,
                task_type=detect_task_type(content, context),
                success=True,
                execution_time=execution_time,
                context=context
            )
            
            return selected_format, parsed_calls
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            # Record failure
            self._record_execution_metrics(
                format=selected_format,
                model_id=model_id,
                task_type=detect_task_type(content, context),
                success=False,
                error_type=type(e).__name__,
                execution_time=execution_time,
                context=context
            )
            
            # Try fallback format
            fallback_format = self._get_fallback_format(selected_format)
            if fallback_format and fallback_format != selected_format:
                self.logger.warning(f"Format {selected_format} failed, trying fallback: {fallback_format}")
                
                fallback_dialect = self.dialects.get(fallback_format)
                if fallback_dialect:
                    try:
                        parsed_calls = fallback_dialect.parse_tool_calls(content)
                        return fallback_format, parsed_calls
                    except Exception:
                        pass
            
            # Re-raise original exception if no fallback works
            raise e
    
    def _record_execution_metrics(self, 
                                format: str,
                                model_id: str,
                                task_type: TaskType,
                                success: bool,
                                execution_time: float = 0.0,
                                error_type: str = None,
                                context: Dict[str, Any] = None) -> None:
        """Record execution metrics."""
        
        metrics = ToolCallExecutionMetrics(
            format=format,
            model_id=model_id,
            task_type=task_type.value,
            success=success,
            error_type=error_type,
            execution_time=execution_time,
            token_count=context.get("token_count", 0) if context else 0
        )
        
        # Record in global history
        self.execution_history.append(metrics)
        
        # Record in dialect
        dialect = self.dialects.get(format)
        if dialect:
            dialect.record_execution(metrics)
        
        # Keep only recent history (last 10000 executions)
        if len(self.execution_history) > 10000:
            self.execution_history = self.execution_history[-10000:]
        
        # Invalidate performance cache
        if format in self.performance_cache:
            del self.performance_cache[format]
    
    def _get_fallback_format(self, failed_format: str) -> Optional[str]:
        """Get fallback format for failed format."""
        # Look for explicitly configured fallback formats
        for format_name, config in self.format_configs.items():
            if config.fallback and config.enabled and format_name != failed_format:
                if format_name in self.dialects:
                    return format_name
        
        # Default fallback chain
        fallback_chain = [
            "xml_python_hybrid",  # Our legacy format
            "json_block",         # Simple and reliable
            "yaml_command"        # Readable fallback
        ]
        
        for fallback in fallback_chain:
            if fallback != failed_format and fallback in self.dialects:
                config = self.format_configs.get(fallback)
                if not config or config.enabled:
                    return fallback
        
        return None
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report."""
        current_time = time.time()
        
        # Calculate metrics for last 24 hours
        cutoff_time = current_time - (24 * 3600)
        recent_metrics = [m for m in self.execution_history if m.timestamp >= cutoff_time]
        
        report = {
            "overall": {
                "total_executions": len(recent_metrics),
                "success_rate": sum(1 for m in recent_metrics if m.success) / len(recent_metrics) if recent_metrics else 0.0,
                "avg_execution_time": sum(m.execution_time for m in recent_metrics) / len(recent_metrics) if recent_metrics else 0.0
            },
            "by_format": {},
            "by_model": {},
            "by_task_type": {},
            "errors": {}
        }
        
        # Group by format
        format_groups = {}
        for metric in recent_metrics:
            if metric.format not in format_groups:
                format_groups[metric.format] = []
            format_groups[metric.format].append(metric)
        
        for format_name, metrics in format_groups.items():
            successes = sum(1 for m in metrics if m.success)
            report["by_format"][format_name] = {
                "executions": len(metrics),
                "success_rate": successes / len(metrics),
                "avg_execution_time": sum(m.execution_time for m in metrics) / len(metrics)
            }
        
        # Error analysis
        error_counts = {}
        for metric in recent_metrics:
            if not metric.success and metric.error_type:
                key = f"{metric.format}:{metric.error_type}"
                error_counts[key] = error_counts.get(key, 0) + 1
        
        report["errors"] = error_counts
        
        return report