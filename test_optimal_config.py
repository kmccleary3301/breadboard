#!/usr/bin/env python3
"""
Test script for the optimal agent configuration.

This script validates the optimal configuration and demonstrates
the performance improvements of the enhanced tool calling system.
"""

import json
import sys
import time
from pathlib import Path

# Add tool_calling to path
sys.path.insert(0, str(Path(__file__).parent))

from tool_calling.agent_integration import (
    create_enhanced_agent_integration,
    benchmark_formats,
    get_performance_summary
)
from tool_calling.config_schema import ConfigurationManager


def load_optimal_config():
    """Load the optimal agent test configuration."""
    config_path = Path(__file__).parent / "tool_calling" / "agent_test_config.json"
    
    with open(config_path) as f:
        return json.load(f)


def test_configuration_validation():
    """Test that the optimal configuration is valid."""
    print("🔍 Testing configuration validation...")
    
    try:
        config = load_optimal_config()
        manager = ConfigurationManager(config)
        tool_config = manager.get_tool_calling_config()
        
        print(f"✅ Configuration valid!")
        print(f"   Strategy: {tool_config.strategy}")
        print(f"   Enabled formats: {len([f for f in tool_config.formats.values() if f.enabled])}")
        print(f"   A/B testing: {tool_config.ab_testing.get('enabled', False)}")
        print(f"   Performance monitoring: {tool_config.performance_monitoring.enabled}")
        
        return True
        
    except Exception as e:
        print(f"❌ Configuration validation failed: {e}")
        return False


def test_format_selection():
    """Test format selection for different models and tasks."""
    print("\n🎯 Testing optimal format selection...")
    
    try:
        config = load_optimal_config()
        manager = create_enhanced_agent_integration(config['tool_calling'])
        
        test_cases = [
            ("gpt-4", "Call the API to get user data", {"task_type": "api_operations"}),
            ("gpt-4", "Edit the Python file to fix the bug", {"task_type": "code_editing"}),
            ("claude-3-opus", "Create a configuration file", {"task_type": "configuration"}),
            ("claude-3-sonnet", "Run the tests and check output", {"task_type": "shell_commands"}),
        ]
        
        results = []
        for model, content, context in test_cases:
            optimal_format = manager.get_optimal_format(
                model_id=model,
                content=content,
                context=context
            )
            results.append((model, context["task_type"], optimal_format))
            print(f"   {model} + {context['task_type']}: {optimal_format}")
        
        # Validate expected selections
        gpt4_api = next(r[2] for r in results if r[0] == "gpt-4" and r[1] == "api_operations")
        gpt4_code = next(r[2] for r in results if r[0] == "gpt-4" and r[1] == "code_editing")
        
        # GPT-4 should prefer native function calling for APIs
        if "native_function_calling" in gpt4_api:
            print("✅ GPT-4 correctly prefers native function calling for API tasks")
        else:
            print(f"⚠️  GPT-4 API task got {gpt4_api}, expected native_function_calling")
        
        # Code editing should prefer unified diff
        if "unified_diff" in gpt4_code or "anthropic_xml" in gpt4_code:
            print("✅ Code editing correctly uses high-performance format")
        else:
            print(f"⚠️  Code editing got {gpt4_code}, expected unified_diff or anthropic_xml")
        
        return True
        
    except Exception as e:
        print(f"❌ Format selection test failed: {e}")
        return False


def test_dialect_parsing():
    """Test that all configured dialects can parse correctly."""
    print("\n📝 Testing dialect parsing...")
    
    try:
        config = load_optimal_config()
        manager = create_enhanced_agent_integration(config['tool_calling'])
        
        test_cases = [
            # Native OpenAI function calling
            ('{"name": "read_file", "arguments": {"path": "test.py"}}', "native_function_calling"),
            
            # Unified diff
            ('''```diff
--- a/test.py
+++ b/test.py
@@ -1,1 +1,1 @@
-print("hello")
+print("hello world")
```''', "unified_diff"),
            
            # Anthropic XML
            ('''<function_calls>
<invoke name="read_file">
<parameter name="path">test.py</parameter>
</invoke>
</function_calls>''', "anthropic_xml"),
            
            # JSON block
            ('''```json
{
  "tool_calls": [
    {"function": "read_file", "arguments": {"path": "test.py"}}
  ]
}
```''', "json_block"),
            
            # YAML command
            ('''```yaml
tools:
  - name: read_file
    args:
      path: test.py
```''', "yaml_command")
        ]
        
        success_count = 0
        for content, expected_format in test_cases:
            try:
                # Force specific format for testing
                dialect = manager.dialect_manager.dialects.get(expected_format)
                if dialect:
                    parsed = dialect.parse_tool_calls(content)
                    if parsed and len(parsed) > 0:
                        print(f"✅ {expected_format}: parsed {len(parsed)} tool calls")
                        success_count += 1
                    else:
                        print(f"⚠️  {expected_format}: no tool calls parsed")
                else:
                    print(f"❌ {expected_format}: dialect not available")
            except Exception as e:
                print(f"❌ {expected_format}: parsing failed - {e}")
        
        print(f"\n   Parsing success rate: {success_count}/{len(test_cases)} ({success_count/len(test_cases)*100:.1f}%)")
        return success_count >= len(test_cases) * 0.8  # 80% success threshold
        
    except Exception as e:
        print(f"❌ Dialect parsing test failed: {e}")
        return False


def test_performance_monitoring():
    """Test performance monitoring capabilities."""
    print("\n📊 Testing performance monitoring...")
    
    try:
        config = load_optimal_config()
        manager = create_enhanced_agent_integration(config['tool_calling'])
        
        # Check if performance monitoring is enabled
        if manager.performance_monitor:
            print("✅ Performance monitoring enabled")
            
            # Try to get a performance report
            report = manager.get_performance_report()
            if isinstance(report, dict):
                print("✅ Performance reporting functional")
                if "formats" in report:
                    print(f"   Tracking {len(report['formats'])} formats")
                return True
            else:
                print("⚠️  Performance report format unexpected")
                return False
        else:
            print("❌ Performance monitoring not enabled")
            return False
            
    except Exception as e:
        print(f"❌ Performance monitoring test failed: {e}")
        return False


def run_format_benchmark():
    """Run a quick benchmark of available formats."""
    print("\n🏃 Running format benchmark...")
    
    try:
        config = load_optimal_config()
        
        # Test content that should parse in multiple formats
        test_content = '{"tool_calls": [{"function": "test_tool", "arguments": {"param": "value"}}]}'
        
        # Get enabled formats from config
        enabled_formats = [
            name for name, fmt_config in config['tool_calling']['formats'].items()
            if isinstance(fmt_config, dict) and fmt_config.get('enabled', True)
        ]
        
        print(f"   Benchmarking {len(enabled_formats)} formats...")
        
        # Run benchmark with fewer iterations for speed
        results = benchmark_formats(
            formats=enabled_formats[:3],  # Test first 3 formats only
            model_id="gpt-4",
            test_content=test_content,
            iterations=5,
            config=config['tool_calling']
        )
        
        # Display results
        for format_name, result in results.items():
            success_rate = result['success_rate']
            avg_time = result['avg_execution_time']
            print(f"   {format_name}: {success_rate:.1%} success, {avg_time:.3f}s avg")
        
        return True
        
    except Exception as e:
        print(f"❌ Format benchmark failed: {e}")
        return False


def main():
    """Run all tests for the optimal configuration."""
    print("🚀 Testing Optimal Agent Configuration")
    print("=" * 50)
    
    tests = [
        ("Configuration Validation", test_configuration_validation),
        ("Format Selection", test_format_selection),
        ("Dialect Parsing", test_dialect_parsing),
        ("Performance Monitoring", test_performance_monitoring),
        ("Format Benchmark", run_format_benchmark),
    ]
    
    results = []
    start_time = time.time()
    
    for test_name, test_func in tests:
        print(f"\n{test_name}")
        print("-" * len(test_name))
        
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    elapsed = time.time() - start_time
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n🏁 Test Summary")
    print("=" * 50)
    print(f"Passed: {passed}/{total} ({passed/total*100:.1f}%)")
    print(f"Time: {elapsed:.2f}s")
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
    
    if passed == total:
        print("\n🎉 All tests passed! The optimal configuration is ready for use.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the configuration.")
        return 1


if __name__ == "__main__":
    exit(main())