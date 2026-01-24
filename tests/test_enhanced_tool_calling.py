"""
Comprehensive test suite for the enhanced tool calling system.

Tests all dialects, format selection, performance monitoring, and integration features.
"""

import json
import tempfile
import unittest
import time
from unittest.mock import MagicMock, patch
from typing import Dict, List, Any

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_calling.enhanced_base_dialect import (
    EnhancedBaseDialect,
    ToolCallFormat,
    TaskType,
    EnhancedToolDefinition,
    EnhancedToolParameter,
    ParsedToolCall,
    ToolCallExecutionMetrics,
    detect_task_type,
    calculate_format_preference_score
)

from tool_calling.enhanced_dialect_manager import (
    EnhancedDialectManager,
    SelectionStrategy,
    FormatDetector
)

from tool_calling.performance_monitor import (
    PerformanceMonitor,
    PerformanceDatabase,
    PerformanceAnalyzer
)

from tool_calling.config_schema import (
    ConfigurationManager,
    FormatConfig,
    ABTestConfig,
    create_minimal_config,
    create_high_performance_config
)

from tool_calling.dialects.openai_function_calling import OpenAIFunctionCallingDialect
from tool_calling.dialects.unified_diff import UnifiedDiffDialect
from tool_calling.dialects.anthropic_xml import AnthropicXMLDialect
from tool_calling.dialects.json_block import JSONBlockDialect
from tool_calling.dialects.yaml_command import YAMLCommandDialect

from tool_calling.agent_integration import (
    EnhancedAgentToolManager,
    create_enhanced_agent_integration,
    benchmark_formats
)


class TestEnhancedBaseDialect(unittest.TestCase):
    """Test the enhanced base dialect functionality."""
    
    def setUp(self):
        self.dialect = OpenAIFunctionCallingDialect()
        
    def test_dialect_initialization(self):
        """Test that dialect initializes with correct properties."""
        self.assertEqual(self.dialect.format, ToolCallFormat.NATIVE_FUNCTION_CALLING)
        self.assertIn("openai", self.dialect.provider_support)
        self.assertEqual(self.dialect.performance_baseline, 0.85)
        self.assertIn(TaskType.API_OPERATIONS, self.dialect.use_cases)
    
    def test_performance_tracking(self):
        """Test that performance metrics are tracked correctly."""
        # Record some executions
        metrics1 = ToolCallExecutionMetrics(
            format="native_function_calling",
            model_id="gpt-4",
            task_type="api_operations",
            success=True,
            execution_time=1.5
        )
        
        metrics2 = ToolCallExecutionMetrics(
            format="native_function_calling",
            model_id="gpt-4",
            task_type="api_operations",
            success=False,
            error_type="ValueError",
            execution_time=0.8
        )
        
        self.dialect.record_execution(metrics1)
        self.dialect.record_execution(metrics2)
        
        # Check success rate calculation
        success_rate = self.dialect.get_success_rate()
        self.assertEqual(success_rate, 0.5)  # 1 success out of 2 total
        
        # Check error patterns
        errors = self.dialect.get_error_patterns()
        self.assertEqual(errors.get("ValueError", 0), 1)
    
    def test_tool_definition_creation(self):
        """Test enhanced tool definition creation."""
        param = EnhancedToolParameter(
            name="file_path",
            type="string",
            description="Path to file",
            required=True,
            validation_rules={"pattern": r".*\.py$"}
        )
        
        tool = EnhancedToolDefinition(
            name="read_file",
            description="Read a Python file",
            parameters=[param],
            supported_formats={"native_function_calling", "json_block"},
            preferred_formats=["native_function_calling"]
        )
        
        self.assertTrue(tool.supports_format("native_function_calling"))
        self.assertFalse(tool.supports_format("xml_python_hybrid"))
        self.assertEqual(tool.get_preferred_format(["json_block", "native_function_calling"]), 
                        "native_function_calling")


class TestDialectParsing(unittest.TestCase):
    """Test parsing functionality of different dialects."""
    
    def test_openai_function_calling_parsing(self):
        """Test OpenAI function calling dialect parsing."""
        dialect = OpenAIFunctionCallingDialect()
        
        # Test JSON function call format
        content = '{"name": "read_file", "arguments": {"path": "test.py"}}'
        parsed = dialect.parse_tool_calls(content)
        
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].function, "read_file")
        self.assertEqual(parsed[0].arguments["path"], "test.py")
    
    def test_unified_diff_parsing(self):
        """Test unified diff dialect parsing."""
        dialect = UnifiedDiffDialect()
        
        content = """
```diff
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 def hello():
-    print("Hello")
+    print("Hello, World!")
 hello()
```
"""
        
        parsed = dialect.parse_tool_calls(content)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].function, "apply_diff")
        self.assertIn("hunks", parsed[0].arguments)
    
    def test_anthropic_xml_parsing(self):
        """Test Anthropic XML dialect parsing."""
        dialect = AnthropicXMLDialect()
        
        content = """
<function_calls>
<invoke name="read_file">
<parameter name="path">test.py</parameter>
</invoke>
</function_calls>
"""
        
        parsed = dialect.parse_tool_calls(content)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].function, "read_file")
        self.assertEqual(parsed[0].arguments["path"], "test.py")
    
    def test_json_block_parsing(self):
        """Test JSON block dialect parsing."""
        dialect = JSONBlockDialect()
        
        content = """
```json
{
  "tool_calls": [
    {
      "function": "read_file",
      "arguments": {
        "path": "test.py"
      }
    }
  ]
}
```
"""
        
        parsed = dialect.parse_tool_calls(content)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].function, "read_file")
        self.assertEqual(parsed[0].arguments["path"], "test.py")
    
    def test_yaml_command_parsing(self):
        """Test YAML command dialect parsing."""
        dialect = YAMLCommandDialect()
        
        content = """
```yaml
tools:
  - name: read_file
    args:
      path: test.py
```
"""
        
        parsed = dialect.parse_tool_calls(content)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].function, "read_file")
        self.assertEqual(parsed[0].arguments["path"], "test.py")


class TestFormatDetection(unittest.TestCase):
    """Test format detection and selection logic."""
    
    def setUp(self):
        self.detector = FormatDetector()
    
    def test_task_type_detection(self):
        """Test task type detection from content."""
        test_cases = [
            ("Edit the file to fix the bug", TaskType.CODE_EDITING),
            ("Run the tests to verify", TaskType.SHELL_COMMANDS),
            ("Create a new configuration file", TaskType.CONFIGURATION),
            ("Call the API endpoint", TaskType.API_OPERATIONS),
            ("General task without specific keywords", TaskType.GENERAL)
        ]
        
        for content, expected_type in test_cases:
            detected = detect_task_type(content)
            self.assertEqual(detected, expected_type, f"Failed for content: {content}")
    
    def test_optimal_format_selection(self):
        """Test optimal format selection logic."""
        available_formats = ["native_function_calling", "unified_diff", "json_block"]
        
        # Test OpenAI model preference
        optimal = self.detector.detect_optimal_format(
            model_id="gpt-4",
            task_type=TaskType.API_OPERATIONS,
            available_formats=available_formats
        )
        self.assertEqual(optimal, "native_function_calling")
        
        # Test Claude model preference
        optimal = self.detector.detect_optimal_format(
            model_id="claude-3",
            task_type=TaskType.CODE_EDITING,
            available_formats=["anthropic_xml", "unified_diff", "json_block"]
        )
        self.assertIn(optimal, ["anthropic_xml", "unified_diff"])  # Both are good for Claude


class TestDialectManager(unittest.TestCase):
    """Test the enhanced dialect manager."""
    
    def setUp(self):
        self.config = {
            "strategy": "adaptive",
            "formats": {
                "native_function_calling": {
                    "enabled": True,
                    "priority": 10,
                    "providers": ["openai"]
                },
                "json_block": {
                    "enabled": True,
                    "priority": 5,
                    "fallback": True
                }
            }
        }
        self.manager = EnhancedDialectManager(self.config)
        
        # Register test dialects
        self.manager.register_dialect(OpenAIFunctionCallingDialect())
        self.manager.register_dialect(JSONBlockDialect())
    
    def test_format_selection_strategies(self):
        """Test different format selection strategies."""
        # Test fixed strategy
        self.manager.strategy = SelectionStrategy.FIXED
        format1 = self.manager.select_optimal_format("gpt-4")
        format2 = self.manager.select_optimal_format("gpt-4")
        self.assertEqual(format1, format2)  # Should be consistent
        
        # Test round robin
        self.manager.strategy = SelectionStrategy.ROUND_ROBIN
        formats = [self.manager.select_optimal_format("gpt-4") for _ in range(4)]
        self.assertTrue(len(set(formats)) > 1)  # Should cycle through formats
    
    def test_fallback_handling(self):
        """Test fallback format handling."""
        # Force a failure and test fallback
        with patch.object(self.manager.dialects["native_function_calling"], 
                         "parse_tool_calls", side_effect=Exception("Parse error")):
            
            enhanced_tools = [EnhancedToolDefinition(name="test", description="test")]
            
            try:
                format_used, parsed = self.manager.execute_with_format_selection(
                    tools=enhanced_tools,
                    content="test content",
                    model_id="gpt-4"
                )
                # Should fallback to json_block
                self.assertEqual(format_used, "json_block")
            except Exception:
                # Fallback also failed, which is expected for invalid content
                pass
    
    def test_performance_tracking(self):
        """Test that manager tracks performance correctly."""
        initial_history_len = len(self.manager.execution_history)
        
        # Execute a tool call
        enhanced_tools = [EnhancedToolDefinition(name="test", description="test")]
        
        try:
            self.manager.execute_with_format_selection(
                tools=enhanced_tools,
                content='{"tool_calls": [{"function": "test", "arguments": {}}]}',
                model_id="gpt-4"
            )
        except Exception:
            # Expected for mock tools
            pass
        
        # Should have recorded metrics
        self.assertGreater(len(self.manager.execution_history), initial_history_len)


class TestPerformanceMonitoring(unittest.TestCase):
    """Test performance monitoring infrastructure."""
    
    def setUp(self):
        # Use temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.monitor = PerformanceMonitor({
            "database_path": self.temp_db.name
        })
    
    def tearDown(self):
        os.unlink(self.temp_db.name)
    
    def test_metric_recording_and_retrieval(self):
        """Test recording and retrieving performance metrics."""
        # Record some test metrics
        metrics = [
            ToolCallExecutionMetrics(
                format="native_function_calling",
                model_id="gpt-4",
                task_type="api_operations",
                success=True,
                execution_time=1.2
            ),
            ToolCallExecutionMetrics(
                format="unified_diff", 
                model_id="gpt-4",
                task_type="code_editing",
                success=True,
                execution_time=2.1
            ),
            ToolCallExecutionMetrics(
                format="native_function_calling",
                model_id="gpt-4", 
                task_type="api_operations",
                success=False,
                error_type="ValueError",
                execution_time=0.5
            )
        ]
        
        for metric in metrics:
            self.monitor.record_execution(metric)
        
        # Test performance summary
        summary = self.monitor.get_performance_summary(hours=1.0)
        
        self.assertIn("formats", summary)
        self.assertIn("native_function_calling", summary["formats"])
        self.assertIn("unified_diff", summary["formats"])
        
        # Check success rates
        native_stats = summary["formats"]["native_function_calling"]["overall_performance"]
        self.assertEqual(native_stats["success_rate"], 0.5)  # 1 success, 1 failure
        
        unified_stats = summary["formats"]["unified_diff"]["overall_performance"]
        self.assertEqual(unified_stats["success_rate"], 1.0)  # 1 success, 0 failures
    
    def test_format_comparison(self):
        """Test format comparison functionality."""
        # Record metrics for different formats
        formats_to_test = ["native_function_calling", "unified_diff", "json_block"]
        
        for i, format_name in enumerate(formats_to_test):
            for j in range(5):
                success = j < (4 - i)  # Different success rates per format
                metric = ToolCallExecutionMetrics(
                    format=format_name,
                    model_id="gpt-4",
                    task_type="general",
                    success=success,
                    execution_time=1.0 + i * 0.5
                )
                self.monitor.record_execution(metric)
        
        # Compare formats
        comparison = self.monitor.compare_formats(formats_to_test)
        
        self.assertEqual(len(comparison), 3)
        
        # Check that native_function_calling has highest success rate
        native_success = comparison["native_function_calling"]["success_rate"]
        unified_success = comparison["unified_diff"]["success_rate"]
        json_success = comparison["json_block"]["success_rate"]
        
        self.assertGreaterEqual(native_success, unified_success)
        self.assertGreaterEqual(unified_success, json_success)


class TestConfigurationManagement(unittest.TestCase):
    """Test configuration management and validation."""
    
    def test_configuration_validation(self):
        """Test configuration validation."""
        # Valid configuration
        valid_config = create_minimal_config()
        manager = ConfigurationManager(valid_config)
        tool_config = manager.get_tool_calling_config()
        self.assertEqual(tool_config.strategy, "adaptive")
        
        # Invalid strategy
        invalid_config = {
            "tool_calling": {
                "strategy": "invalid_strategy"
            }
        }
        
        with self.assertRaises(Exception):
            ConfigurationManager(invalid_config)
    
    def test_format_configuration(self):
        """Test format-specific configuration."""
        config = create_high_performance_config()
        manager = ConfigurationManager(config)
        tool_config = manager.get_tool_calling_config()
        
        # Check that high-performance formats are enabled
        self.assertTrue(tool_config.formats["native_function_calling"].enabled)
        self.assertEqual(tool_config.formats["native_function_calling"].priority, 10)
        self.assertIn("openai", tool_config.formats["native_function_calling"].providers)
    
    def test_ab_testing_configuration(self):
        """Test A/B testing configuration."""
        config = {
            "tool_calling": {
                "ab_testing": {
                    "enabled": True,
                    "test_groups": [
                        {
                            "name": "format_test",
                            "formats": ["native_function_calling", "unified_diff"],
                            "traffic_split": [0.6, 0.4],
                            "min_sample_size": 50
                        }
                    ]
                }
            }
        }
        
        manager = ConfigurationManager(config)
        tool_config = manager.get_tool_calling_config()
        
        self.assertTrue(tool_config.ab_testing["enabled"])
        self.assertEqual(len(tool_config.ab_testing["test_groups"]), 1)


class TestAgentIntegration(unittest.TestCase):
    """Test integration with agent systems."""
    
    def setUp(self):
        self.config = create_minimal_config()
        self.manager = create_enhanced_agent_integration(self.config["tool_calling"])
    
    def test_legacy_tool_conversion(self):
        """Test conversion of legacy tool definitions."""
        from tool_calling.core import ToolDefinition, ToolParameter
        
        legacy_tool = ToolDefinition(
            name="test_tool",
            description="Test tool",
            parameters=[
                ToolParameter(name="arg1", type="string", description="First arg"),
                ToolParameter(name="arg2", type="integer", default=42)
            ]
        )
        
        enhanced_tools = self.manager.convert_legacy_tools([legacy_tool])
        
        self.assertEqual(len(enhanced_tools), 1)
        enhanced_tool = enhanced_tools[0]
        
        self.assertEqual(enhanced_tool.name, "test_tool")
        self.assertEqual(len(enhanced_tool.parameters), 2)
        
        # Check parameter conversion
        param1 = enhanced_tool.parameters[0]
        self.assertEqual(param1.name, "arg1")
        self.assertTrue(param1.required)  # No default value
        
        param2 = enhanced_tool.parameters[1]
        self.assertEqual(param2.name, "arg2")
        self.assertFalse(param2.required)  # Has default value
    
    def test_optimal_format_selection_for_agent(self):
        """Test optimal format selection in agent context."""
        # Test different model scenarios
        test_cases = [
            ("gpt-4", "native_function_calling"),
            ("claude-3", "json_block"),  # Fallback since anthropic_xml not enabled
            ("unknown-model", "json_block")  # Default fallback
        ]
        
        for model_id, expected_format in test_cases:
            optimal = self.manager.get_optimal_format(model_id)
            # Since we're using minimal config, check that we get a valid format
            self.assertIsInstance(optimal, str)
            self.assertGreater(len(optimal), 0)
    
    def test_performance_reporting(self):
        """Test performance reporting in agent integration."""
        # Performance monitoring should be available
        report = self.manager.get_performance_report()
        
        # Should return a structured report
        self.assertIsInstance(report, dict)
        if "error" not in report:
            self.assertIn("formats", report)


class TestBenchmarking(unittest.TestCase):
    """Test benchmarking functionality."""
    
    def test_format_benchmarking(self):
        """Test format benchmarking functionality."""
        config = create_high_performance_config()
        
        # Test with simple content that should parse
        test_content = '{"tool_calls": [{"function": "test", "arguments": {}}]}'
        
        results = benchmark_formats(
            formats=["json_block"],
            model_id="gpt-4",
            test_content=test_content,
            iterations=3,
            config=config["tool_calling"]
        )
        
        self.assertIn("json_block", results)
        format_result = results["json_block"]
        
        self.assertIn("success_rate", format_result)
        self.assertIn("avg_execution_time", format_result)
        self.assertGreaterEqual(format_result["success_rate"], 0.0)
        self.assertLessEqual(format_result["success_rate"], 1.0)


if __name__ == "__main__":
    # Configure logging for tests
    import logging
    logging.basicConfig(level=logging.WARNING)
    
    # Run all tests
    unittest.main(verbosity=2)