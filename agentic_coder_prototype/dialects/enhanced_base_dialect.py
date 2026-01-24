"""
Enhanced base dialect class implementing design decisions from tool syntax analysis.

Provides foundation for modular tool calling with performance monitoring,
format selection, and extensible architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union
from enum import Enum
import math
import time
import uuid


class ToolCallFormat(Enum):
    """Supported tool calling formats."""
    NATIVE_FUNCTION_CALLING = "native_function_calling"
    UNIFIED_DIFF = "unified_diff"
    ANTHROPIC_XML = "anthropic_xml"
    JSON_BLOCK = "json_block"
    YAML_COMMAND = "yaml_command"
    XML_PYTHON_HYBRID = "xml_python_hybrid"  # Legacy format
    AIDER_SEARCH_REPLACE = "aider_search_replace"


class TaskType(Enum):
    """Types of tasks for format optimization."""
    CODE_EDITING = "code_editing"
    FILE_MODIFICATION = "file_modification"
    API_OPERATIONS = "api_operations"
    SHELL_COMMANDS = "shell_commands"
    DATA_PROCESSING = "data_processing"
    CONFIGURATION = "configuration"
    GENERAL = "general"


@dataclass
class ToolCallExecutionMetrics:
    """Metrics for tool call execution."""
    format: str
    model_id: str
    task_type: str
    success: bool
    error_type: Optional[str] = None
    execution_time: float = 0.0
    token_count: int = 0
    timestamp: float = field(default_factory=time.time)
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ParsedToolCall:
    """Enhanced parsed tool call with metadata."""
    function: str
    arguments: Dict[str, Any]
    raw_content: Optional[str] = None
    call_id: Optional[str] = None
    format: Optional[str] = None
    confidence: float = 1.0  # Parsing confidence (0.0 to 1.0)
    
    # Compatibility with existing ToolCallParsed
    @property
    def ToolCallParsed(self):
        """Backward compatibility property."""
        from ..core.core import ToolCallParsed
        return ToolCallParsed(
            function=self.function,
            arguments=self.arguments
        )


@dataclass
class EnhancedToolParameter:
    """Enhanced tool parameter with validation and metadata."""
    name: str
    type: str | None = None
    description: str | None = None
    default: Any | None = None
    required: bool = False
    validation_rules: Dict[str, Any] = field(default_factory=dict)
    examples: List[str] = field(default_factory=list)
    schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EnhancedToolDefinition:
    """Enhanced tool definition with format support and metadata."""
    name: str
    description: str
    parameters: List[EnhancedToolParameter] = field(default_factory=list)
    type_id: str = "python"
    blocking: bool = False
    
    # New enhanced fields
    supported_formats: Set[str] = field(default_factory=set)
    preferred_formats: List[str] = field(default_factory=list)
    use_cases: List[str] = field(default_factory=list)
    performance_data: Dict[str, float] = field(default_factory=dict)
    max_per_turn: Optional[int] = None
    dependencies: List[str] = field(default_factory=list)
    
    # Provider-specific settings
    provider_settings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def supports_format(self, format: Union[str, ToolCallFormat]) -> bool:
        """Check if tool supports a specific format."""
        format_str = format.value if isinstance(format, ToolCallFormat) else format
        return format_str in self.supported_formats or len(self.supported_formats) == 0
    
    def get_preferred_format(self, available_formats: List[str]) -> Optional[str]:
        """Get the most preferred format from available options."""
        for preferred in self.preferred_formats:
            if preferred in available_formats:
                return preferred
        return None


class EnhancedBaseDialect(ABC):
    """Enhanced base class for tool dialects with performance tracking."""
    
    def __init__(self):
        self.type_id: str = "unknown"
        self.format: ToolCallFormat = ToolCallFormat.XML_PYTHON_HYBRID
        self.provider_support: List[str] = ["*"]  # Which providers support this format
        self.performance_baseline: float = 0.0
        self.use_cases: List[TaskType] = [TaskType.GENERAL]
        
        # Performance tracking
        self._execution_history: List[ToolCallExecutionMetrics] = []
        self._success_rate_cache: Optional[float] = None
        self._cache_timestamp: float = 0
        
    @abstractmethod
    def get_system_prompt_section(self) -> str:
        """Get the system prompt section for this dialect."""
        pass
    
    @abstractmethod
    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        """Parse tool calls from content."""
        pass
    
    @abstractmethod
    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        """Format tools for inclusion in prompts."""
        pass
    
    def supports_provider(self, provider: str) -> bool:
        """Check if this dialect supports a specific provider."""
        return "*" in self.provider_support or provider in self.provider_support
    
    def supports_task_type(self, task_type: Union[str, TaskType]) -> bool:
        """Check if this dialect is suitable for a task type."""
        if isinstance(task_type, str):
            try:
                task_type = TaskType(task_type)
            except ValueError:
                return False
        return task_type in self.use_cases or TaskType.GENERAL in self.use_cases
    
    def get_success_rate(self, time_window_hours: float = 24.0) -> float:
        """Get recent success rate for this dialect."""
        current_time = time.time()
        
        # Use cache if recent
        if (current_time - self._cache_timestamp) < 300:  # 5 minutes
            return self._success_rate_cache or 0.0
        
        # Calculate from recent history
        cutoff_time = current_time - (time_window_hours * 3600)
        recent_executions = [
            m for m in self._execution_history 
            if m.timestamp >= cutoff_time
        ]
        
        if not recent_executions:
            success_rate = self.performance_baseline
        else:
            successes = sum(1 for m in recent_executions if m.success)
            success_rate = successes / len(recent_executions)
        
        # Update cache
        self._success_rate_cache = success_rate
        self._cache_timestamp = current_time
        
        return success_rate
    
    def record_execution(self, metrics: ToolCallExecutionMetrics) -> None:
        """Record execution metrics for performance tracking."""
        self._execution_history.append(metrics)
        
        # Keep only recent history (last 1000 executions)
        if len(self._execution_history) > 1000:
            self._execution_history = self._execution_history[-1000:]
        
        # Invalidate cache
        self._success_rate_cache = None

    def get_error_patterns(self, time_window_hours: float = 24.0) -> Dict[str, int]:
        """Get recent error pattern counts for this dialect."""
        current_time = time.time()
        cutoff_time = current_time - (time_window_hours * 3600)
        errors: Dict[str, int] = {}
        for metrics in self._execution_history:
            if metrics.timestamp < cutoff_time:
                continue
            if metrics.success:
                continue
            error_type = metrics.error_type or "unknown"
            errors[error_type] = errors.get(error_type, 0) + 1
        return errors


def detect_task_type(content: str, context: Optional[Dict[str, Any]] = None) -> TaskType:
    """Best-effort task type detection from user content + optional context.

    This is intentionally simple and deterministic: it is used only for format selection
    heuristics and should never gate correctness.
    """

    ctx = context or {}
    hinted = ctx.get("task_type") or ctx.get("taskType")
    if hinted:
        try:
            return hinted if isinstance(hinted, TaskType) else TaskType(str(hinted))
        except Exception:
            pass

    text = str(content or "")
    lowered = text.lower()

    # Configuration tasks.
    if any(token in lowered for token in ("config", "configuration", "settings", ".env", "dotenv", "yaml", "toml", "ini", "dockerfile", "docker-compose")):
        return TaskType.CONFIGURATION

    # API operations.
    if any(token in lowered for token in (" api", "api ", "endpoint", "http", "https://", "curl", "request", "webhook", "rest", "graphql")):
        return TaskType.API_OPERATIONS

    # Shell / terminal operations.
    if any(token in lowered for token in ("run ", "execute", "shell", "bash", "terminal", "cmd", "powershell", "pytest", "npm ", "pnpm ", "yarn ", "make ", "tests", "test suite")):
        return TaskType.SHELL_COMMANDS

    # Code editing / bugfixing.
    if any(token in lowered for token in ("edit", "fix", "bug", "refactor", "implement", "patch", "diff", "function", "class", "module", "code")):
        return TaskType.CODE_EDITING

    # File modification (non-code).
    if any(token in lowered for token in ("create file", "new file", "rename", "move file", "delete file", "copy file")):
        return TaskType.FILE_MODIFICATION

    # Data processing (heuristic).
    if any(token in lowered for token in ("csv", "json", "parse", "transform", "aggregate", "dataset", "dataframe", "pandas")):
        return TaskType.DATA_PROCESSING

    return TaskType.GENERAL


def calculate_format_preference_score(
    format_name: str,
    model_id: str,
    task_type: Union[str, TaskType],
    performance_data: Optional[Dict[str, float]] = None,
    *,
    priority: Optional[int] = None,
) -> float:
    """Return a heuristic preference score for a format (higher is better).

    This is used for *non-critical* selection guidance. The scale is arbitrary but stable.
    """

    fmt = str(format_name or "").strip()
    model = str(model_id or "").strip().lower()
    try:
        task = task_type if isinstance(task_type, TaskType) else TaskType(str(task_type))
    except Exception:
        task = TaskType.GENERAL

    perf = 0.5
    if isinstance(performance_data, dict):
        try:
            perf = float(performance_data.get(fmt, perf))
        except Exception:
            perf = 0.5
    perf = max(0.0, min(1.0, perf))

    # Model preference ordering (simple pattern match; avoids dependency on manager module).
    model_prefs: List[str]
    if model.startswith("gpt-") or "openai" in model:
        model_prefs = ["native_function_calling", "unified_diff", "json_block", "yaml_command"]
    elif model.startswith("claude") or "anthropic" in model:
        model_prefs = ["anthropic_xml", "unified_diff", "json_block", "yaml_command"]
    else:
        model_prefs = ["unified_diff", "json_block", "yaml_command", "native_function_calling"]

    task_prefs: Dict[TaskType, List[str]] = {
        TaskType.CODE_EDITING: ["unified_diff", "aider_search_replace", "json_block"],
        TaskType.FILE_MODIFICATION: ["unified_diff", "json_block"],
        TaskType.API_OPERATIONS: ["native_function_calling", "json_block"],
        TaskType.SHELL_COMMANDS: ["yaml_command", "json_block"],
        TaskType.DATA_PROCESSING: ["json_block", "native_function_calling"],
        TaskType.CONFIGURATION: ["yaml_command", "json_block"],
        TaskType.GENERAL: ["json_block", "yaml_command", "unified_diff"],
    }

    def rank_bonus(prefs: List[str], name: str, weight: float) -> float:
        if name not in prefs:
            return 0.0
        idx = prefs.index(name)
        denom = max(1, len(prefs) - 1)
        return weight * (1.0 - (idx / denom))

    score = 0.0
    score += perf * 0.6
    score += rank_bonus(model_prefs, fmt, 0.25)
    score += rank_bonus(task_prefs.get(task, task_prefs[TaskType.GENERAL]), fmt, 0.15)

    if priority is not None:
        try:
            p = int(priority)
            score += max(0.0, min(0.1, p / 100.0))
        except Exception:
            pass

    # Guardrail: keep score finite.
    if not math.isfinite(score):
        return 0.0
    return float(score)
    
