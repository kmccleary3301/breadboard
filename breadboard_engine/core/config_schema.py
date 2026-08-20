"""
Enhanced configuration schema for the new tool calling system.

Provides comprehensive configuration options for format selection, performance
monitoring, A/B testing, and provider-specific settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from enum import Enum
import json


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


@dataclass
class FormatConfig:
    """Configuration for a specific tool calling format."""
    enabled: bool = True
    priority: int = 1
    models: List[str] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)
    use_cases: List[str] = field(default_factory=list)
    min_success_rate: float = 0.5
    fallback: bool = False
    
    # A/B testing configuration
    test_weight: float = 1.0
    test_group: Optional[str] = None
    
    # Performance thresholds
    max_execution_time: Optional[float] = None
    max_token_overhead: Optional[int] = None
    
    # Provider-specific settings
    provider_settings: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class ABTestConfig:
    """A/B test configuration."""
    name: str
    enabled: bool = True
    formats: List[str] = field(default_factory=list)
    traffic_split: List[float] = field(default_factory=list)
    success_metrics: List[str] = field(default_factory=lambda: ["completion_rate", "error_rate"])
    min_sample_size: int = 100
    confidence_level: float = 0.95
    duration_days: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@dataclass
class PerformanceMonitoringConfig:
    """Performance monitoring configuration."""
    enabled: bool = True
    database_path: str = "tool_calling_performance.db"
    collection_interval: int = 300  # seconds
    cleanup_interval_days: int = 30
    alert_enabled: bool = True
    
    # Performance thresholds for alerts
    low_success_rate_threshold: float = 0.5
    high_error_rate_threshold: float = 0.3
    slow_execution_threshold: float = 10.0  # seconds
    
    # Retention settings
    metrics_retention_days: int = 90
    snapshots_retention_days: int = 365


@dataclass
class AdaptiveSelectionConfig:
    """Adaptive format selection configuration."""
    enabled: bool = True
    performance_weight: float = 0.7
    compatibility_weight: float = 0.3
    cache_ttl_seconds: int = 300
    min_sample_size: int = 10
    
    # Learning parameters
    exploration_rate: float = 0.1
    decay_factor: float = 0.95


@dataclass
class ProviderConfig:
    """Provider-specific configuration."""
    name: str
    enabled: bool = True
    preferred_formats: List[str] = field(default_factory=list)
    fallback_formats: List[str] = field(default_factory=list)
    max_tools_per_call: Optional[int] = None
    supports_streaming: bool = False
    supports_parallel_calls: bool = True
    
    # API settings
    api_settings: Dict[str, Any] = field(default_factory=dict)
    
    # Format-specific overrides
    format_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class ToolCallingConfig:
    """Main tool calling configuration."""
    # Strategy selection
    strategy: str = "adaptive"  # adaptive, fixed, a_b_test, round_robin, random
    
    # Format configurations
    formats: Dict[str, FormatConfig] = field(default_factory=dict)
    
    # A/B testing
    ab_testing: Dict[str, Any] = field(default_factory=dict)
    
    # Performance monitoring
    performance_monitoring: PerformanceMonitoringConfig = field(default_factory=PerformanceMonitoringConfig)
    
    # Adaptive selection
    adaptive_selection: AdaptiveSelectionConfig = field(default_factory=AdaptiveSelectionConfig)
    
    # Provider configurations
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    
    # Global settings
    default_format: str = "xml_python_hybrid"
    fallback_chain: List[str] = field(default_factory=lambda: [
        "xml_python_hybrid", "json_block", "yaml_command"
    ])
    
    # Debug and logging
    debug_mode: bool = False
    log_level: str = "INFO"
    detailed_metrics: bool = False


class ConfigurationManager:
    """Manages and validates tool calling configuration."""
    
    DEFAULT_CONFIG = {
        "tool_calling": {
            "strategy": "adaptive",
            "default_format": "xml_python_hybrid",
            "fallback_chain": ["xml_python_hybrid", "json_block", "yaml_command"],
            
            "formats": {
                "native_function_calling": {
                    "enabled": True,
                    "priority": 10,
                    "providers": ["openai", "azure_openai"],
                    "use_cases": ["api_operations", "data_processing"],
                    "min_success_rate": 0.8,
                    "provider_settings": {
                        "openai": {
                            "function_call": "auto",
                            "supports_parallel": True
                        }
                    }
                },
                
                "unified_diff": {
                    "enabled": True,
                    "priority": 9,
                    "use_cases": ["code_editing", "file_modification"],
                    "min_success_rate": 0.6,
                    "fallback": False
                },
                
                "anthropic_xml": {
                    "enabled": True,
                    "priority": 8,
                    "providers": ["anthropic", "claude"],
                    "use_cases": ["general", "file_modification"],
                    "min_success_rate": 0.7
                },
                
                "json_block": {
                    "enabled": True,
                    "priority": 7,
                    "use_cases": ["api_operations", "data_processing", "configuration"],
                    "min_success_rate": 0.6,
                    "fallback": True
                },
                
                "yaml_command": {
                    "enabled": True,
                    "priority": 6,
                    "use_cases": ["configuration", "shell_commands"],
                    "min_success_rate": 0.5,
                    "fallback": True
                },
                
                "xml_python_hybrid": {
                    "enabled": True,
                    "priority": 1,
                    "fallback": True,
                    "min_success_rate": 0.3
                }
            },
            
            "ab_testing": {
                "enabled": False,
                "test_groups": []
            },
            
            "performance_monitoring": {
                "enabled": True,
                "database_path": "tool_calling_performance.db",
                "collection_interval": 300,
                "cleanup_interval_days": 30,
                "alert_enabled": True,
                "low_success_rate_threshold": 0.5,
                "high_error_rate_threshold": 0.3,
                "slow_execution_threshold": 10.0
            },
            
            "adaptive_selection": {
                "enabled": True,
                "performance_weight": 0.7,
                "compatibility_weight": 0.3,
                "cache_ttl_seconds": 300,
                "min_sample_size": 10
            },
            
            "providers": {
                "openai": {
                    "enabled": True,
                    "preferred_formats": ["native_function_calling", "json_block"],
                    "max_tools_per_call": 128,
                    "supports_streaming": True,
                    "supports_parallel_calls": True
                },
                
                "anthropic": {
                    "enabled": True,
                    "preferred_formats": ["anthropic_xml", "unified_diff"],
                    "max_tools_per_call": 64,
                    "supports_streaming": True,
                    "supports_parallel_calls": True
                },
                
                "default": {
                    "enabled": True,
                    "preferred_formats": ["json_block", "yaml_command", "xml_python_hybrid"],
                    "max_tools_per_call": 32,
                    "supports_streaming": False,
                    "supports_parallel_calls": True
                }
            }
        }
    }
    
    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        self.config_dict = config_dict or self.DEFAULT_CONFIG.copy()
        self._validate_config()
    
    def _validate_config(self) -> None:
        """Validate the configuration."""
        tool_config = self.config_dict.get("tool_calling", {})
        
        # Validate strategy
        valid_strategies = ["adaptive", "fixed", "a_b_test", "round_robin", "random"]
        strategy = tool_config.get("strategy", "adaptive")
        if strategy not in valid_strategies:
            raise ConfigValidationError(f"Invalid strategy: {strategy}. Must be one of {valid_strategies}")
        
        # Validate formats
        formats = tool_config.get("formats", {})
        # Some tests pass minimal configs without explicit formats; tolerate by defaulting
        if not formats:
            # Inject a minimal default to satisfy downstream parsing
            tool_config["formats"] = {
                "json_block": {"enabled": True, "priority": 1, "fallback": True}
            }
            self.config_dict["tool_calling"] = tool_config
            formats = tool_config["formats"]
        
        # Check that at least one format is enabled
        enabled_formats = [name for name, config in formats.items() 
                          if config.get("enabled", True)]
        if not enabled_formats:
            raise ConfigValidationError("At least one format must be enabled")
        
        # Validate A/B testing config
        ab_config = tool_config.get("ab_testing", {})
        if ab_config.get("enabled", False):
            self._validate_ab_testing_config(ab_config)
        
        # Validate performance monitoring
        perf_config = tool_config.get("performance_monitoring", {})
        self._validate_performance_config(perf_config)
    
    def _validate_ab_testing_config(self, ab_config: Dict[str, Any]) -> None:
        """Validate A/B testing configuration."""
        test_groups = ab_config.get("test_groups", [])
        
        for test_group in test_groups:
            if not isinstance(test_group, dict):
                raise ConfigValidationError("Each A/B test group must be a dictionary")
            
            required_fields = ["name", "formats", "traffic_split"]
            for field in required_fields:
                if field not in test_group:
                    raise ConfigValidationError(f"A/B test group missing required field: {field}")
            
            formats = test_group["formats"]
            traffic_split = test_group["traffic_split"]
            
            if len(formats) != len(traffic_split):
                raise ConfigValidationError("A/B test formats and traffic_split must have same length")
            
            if abs(sum(traffic_split) - 1.0) > 0.01:
                raise ConfigValidationError("A/B test traffic_split must sum to 1.0")
    
    def _validate_performance_config(self, perf_config: Dict[str, Any]) -> None:
        """Validate performance monitoring configuration."""
        # Validate threshold values
        thresholds = {
            "low_success_rate_threshold": (0.0, 1.0),
            "high_error_rate_threshold": (0.0, 1.0),
            "slow_execution_threshold": (0.0, float('inf'))
        }
        
        for threshold_name, (min_val, max_val) in thresholds.items():
            if threshold_name in perf_config:
                value = perf_config[threshold_name]
                if not (min_val <= value <= max_val):
                    raise ConfigValidationError(f"{threshold_name} must be between {min_val} and {max_val}")
    
    def get_tool_calling_config(self) -> ToolCallingConfig:
        """Get parsed tool calling configuration."""
        tool_config = self.config_dict.get("tool_calling", {})
        
        # Parse formats
        formats = {}
        for name, config_dict in tool_config.get("formats", {}).items():
            formats[name] = FormatConfig(**config_dict)
        
        # Parse performance monitoring
        perf_dict = tool_config.get("performance_monitoring", {})
        # Backward-compat: ignore unknown keys like 'detailed_metrics' here
        perf_clean = {k: v for k, v in perf_dict.items() if k in PerformanceMonitoringConfig.__dataclass_fields__}
        performance_monitoring = PerformanceMonitoringConfig(**perf_clean)
        
        # Parse adaptive selection
        adaptive_dict = tool_config.get("adaptive_selection", {})
        adaptive_selection = AdaptiveSelectionConfig(**adaptive_dict)
        
        # Parse providers
        providers = {}
        for name, config_dict in tool_config.get("providers", {}).items():
            providers[name] = ProviderConfig(name=name, **config_dict)
        
        return ToolCallingConfig(
            strategy=tool_config.get("strategy", "adaptive"),
            formats=formats,
            ab_testing=tool_config.get("ab_testing", {}),
            performance_monitoring=performance_monitoring,
            adaptive_selection=adaptive_selection,
            providers=providers,
            default_format=tool_config.get("default_format", "xml_python_hybrid"),
            fallback_chain=tool_config.get("fallback_chain", []),
            debug_mode=tool_config.get("debug_mode", False),
            log_level=tool_config.get("log_level", "INFO"),
            detailed_metrics=tool_config.get("detailed_metrics", False)
        )
    
    def enable_format(self, format_name: str) -> None:
        """Enable a specific format."""
        formats = self.config_dict.setdefault("tool_calling", {}).setdefault("formats", {})
        if format_name not in formats:
            formats[format_name] = {"enabled": True}
        else:
            formats[format_name]["enabled"] = True
    
    def disable_format(self, format_name: str) -> None:
        """Disable a specific format."""
        formats = self.config_dict.setdefault("tool_calling", {}).setdefault("formats", {})
        if format_name in formats:
            formats[format_name]["enabled"] = False
    
    def set_strategy(self, strategy: str) -> None:
        """Set the selection strategy."""
        valid_strategies = ["adaptive", "fixed", "a_b_test", "round_robin", "random"]
        if strategy not in valid_strategies:
            raise ConfigValidationError(f"Invalid strategy: {strategy}")
        
        self.config_dict.setdefault("tool_calling", {})["strategy"] = strategy
    
    def enable_ab_testing(self, test_name: str, formats: List[str], traffic_split: List[float]) -> None:
        """Enable A/B testing with specified configuration."""
        if len(formats) != len(traffic_split):
            raise ConfigValidationError("Formats and traffic_split must have same length")
        
        if abs(sum(traffic_split) - 1.0) > 0.01:
            raise ConfigValidationError("Traffic split must sum to 1.0")
        
        ab_config = self.config_dict.setdefault("tool_calling", {}).setdefault("ab_testing", {})
        ab_config["enabled"] = True
        
        test_groups = ab_config.setdefault("test_groups", [])
        test_groups.append({
            "name": test_name,
            "formats": formats,
            "traffic_split": traffic_split,
            "enabled": True
        })
    
    def disable_ab_testing(self) -> None:
        """Disable A/B testing."""
        ab_config = self.config_dict.setdefault("tool_calling", {}).setdefault("ab_testing", {})
        ab_config["enabled"] = False
    
    def set_performance_threshold(self, threshold_name: str, value: float) -> None:
        """Set a performance monitoring threshold."""
        valid_thresholds = [
            "low_success_rate_threshold",
            "high_error_rate_threshold", 
            "slow_execution_threshold"
        ]
        
        if threshold_name not in valid_thresholds:
            raise ConfigValidationError(f"Invalid threshold: {threshold_name}")
        
        perf_config = self.config_dict.setdefault("tool_calling", {}).setdefault("performance_monitoring", {})
        perf_config[threshold_name] = value
    
    def export_config(self) -> str:
        """Export configuration as JSON string."""
        return json.dumps(self.config_dict, indent=2)
    
    def save_to_file(self, file_path: str) -> None:
        """Save configuration to file."""
        with open(file_path, 'w') as f:
            json.dump(self.config_dict, f, indent=2)
    
    @classmethod
    def load_from_file(cls, file_path: str) -> 'ConfigurationManager':
        """Load configuration from file."""
        with open(file_path, 'r') as f:
            config_dict = json.load(f)
        return cls(config_dict)
    
    @classmethod
    def create_example_config(cls, output_path: str) -> None:
        """Create an example configuration file."""
        manager = cls()
        manager.save_to_file(output_path)


def create_minimal_config() -> Dict[str, Any]:
    """Create a minimal configuration for basic usage."""
    return {
        "tool_calling": {
            "strategy": "adaptive",
            "formats": {
                "xml_python_hybrid": {
                    "enabled": True,
                    "fallback": True
                },
                "json_block": {
                    "enabled": True
                }
            }
        }
    }


def create_high_performance_config() -> Dict[str, Any]:
    """Create a configuration optimized for performance."""
    return {
        "tool_calling": {
            "strategy": "adaptive",
            "formats": {
                "native_function_calling": {
                    "enabled": True,
                    "priority": 10,
                    "providers": ["openai"],
                    "min_success_rate": 0.8
                },
                "unified_diff": {
                    "enabled": True,
                    "priority": 9,
                    "use_cases": ["code_editing"],
                    "min_success_rate": 0.6
                },
                "anthropic_xml": {
                    "enabled": True,
                    "priority": 8,
                    "providers": ["anthropic"],
                    "min_success_rate": 0.7
                }
            },
            "performance_monitoring": {
                "enabled": True,
                "detailed_metrics": True
            }
        }
    }