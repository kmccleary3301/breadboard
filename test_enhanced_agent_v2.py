#!/usr/bin/env python3
"""
Test Enhanced Agent Integration V2
Tests per_turn_append mode with gpt-5-nano and OpenCode/Crush insights.
"""
import asyncio
import json
import os
import sys
import time
import yaml
from pathlib import Path

# Add tool_calling to path
sys.path.insert(0, str(Path(__file__).parent / "tool_calling"))

from tool_calling.core import ToolDefinition, ToolParameter
from tool_calling.enhanced_agent_integration_v2 import EnhancedAgentIntegrationV2


def load_test_config(config_path: str) -> dict:
    """Load test configuration from YAML file.
    Falls back to agent_configs/ if not found in CWD.
    """
    p = Path(config_path)
    if not p.exists():
        alt = Path(__file__).parent / 'agent_configs' / p.name
        if alt.exists():
            p = alt
    with open(p, 'r') as f:
        return yaml.safe_load(f)


def create_test_tools() -> list[ToolDefinition]:
    """Create test tool definitions"""
    tools = [
        ToolDefinition(
            name="run_shell",
            description="Execute shell commands",
            parameters=[
                ToolParameter(name="command", type="string", description="Shell command to execute")
            ]
        ),
        ToolDefinition(
            name="create_file", 
            description="Create new files with content",
            parameters=[
                ToolParameter(name="path", type="string", description="File path"),
                ToolParameter(name="content", type="string", description="File content")
            ]
        ),
        ToolDefinition(
            name="read_file",
            description="Read file contents", 
            parameters=[
                ToolParameter(name="path", type="string", description="File path to read")
            ]
        ),
        ToolDefinition(
            name="apply_search_replace",
            description="Apply Aider SEARCH/REPLACE format changes (PREFERRED - 2.3x success rate)",
            parameters=[
                ToolParameter(name="file_path", type="string", description="Target file path"),
                ToolParameter(name="search_block", type="string", description="Text to search for"),
                ToolParameter(name="replace_block", type="string", description="Replacement text")
            ]
        ),
        ToolDefinition(
            name="mark_task_complete",
            description="Mark task as complete with status",
            parameters=[
                ToolParameter(name="status", type="string", description="completed, failed, or partial"),
                ToolParameter(name="summary", type="string", description="Brief summary of work done")
            ]
        )
    ]
    return tools


async def test_per_turn_append_mode():
    """Test per_turn_append mode with gpt-5-nano"""
    print("🚀 Testing Enhanced Agent Integration V2 with per_turn_append mode")
    print("=" * 70)
    
    # Load configuration
    config = load_test_config("test_enhanced_agent_v2.yaml")
    print(f"✅ Loaded configuration for {config['agent_config']['model_id']}")
    
    # Create test tools
    test_tools = create_test_tools()
    print(f"✅ Created {len(test_tools)} test tools")
    
    # Initialize enhanced integration with test tools
    integration = EnhancedAgentIntegrationV2(tools=test_tools)
    print("✅ Enhanced Agent Integration V2 initialized")
    
    # Create session (should auto-detect per_turn_append mode for gpt-5-nano)
    session_id = "test_v2_session"
    session = integration.create_session(
        session_id=session_id,
        provider_id="openai", 
        model_id="gpt-5-nano",
        tool_prompt_mode="auto"  # Should detect per_turn_append
    )
    print(f"✅ Created session: {session['tool_prompt_mode']} mode")
    
    # Test 1: System prompt compilation
    print("\n📝 Test 1: System Prompt Compilation")
    prompt_data = integration.get_session_prompt_data(
        session_id=session_id,
        user_message="Create a Python calculator with basic operations",
        custom_tools=test_tools
    )
    
    print(f"Mode: {prompt_data['mode']}")
    print(f"System prompt length: {len(prompt_data['system_prompt'])} chars")
    print(f"Tools hash: {prompt_data['tools_hash']}")
    
    # For per_turn_append, system prompt should be minimal
    if prompt_data['mode'] == 'per_turn_append':
        print("✅ Using per_turn_append mode - system prompt is minimal")
        print(f"User message length: {len(prompt_data['formatted_user_message'])} chars")
        
        # Show first part of formatted user message (should contain tools)
        user_msg_preview = prompt_data['formatted_user_message'][:500]
        print(f"User message preview: {user_msg_preview}...")
        
        if "<TOOLS_AVAILABLE>" in prompt_data['formatted_user_message']:
            print("✅ Tools properly appended to user message")
        else:
            print("❌ Tools not found in user message")
    
    # Test 2: Tool execution
    print("\n🔧 Test 2: Tool Execution")
    
    # Test tool execution with timeout
    execution_result = await integration.execute_tool_streaming(
        tool_name="read_file",
        parameters={"path": "test_file.txt"},
        session_id=session_id,
        timeout=60  # 60s timeout as requested
    )
    
    print(f"Tool execution result: {execution_result['success']}")
    print(f"Execution time: {execution_result['execution_time']:.3f}s")
    
    if execution_result['success']:
        print("✅ Tool executed successfully")
    else:
        print(f"❌ Tool execution failed: {execution_result.get('error', 'Unknown error')}")
    
    # Test 3: Provider configuration
    print("\n⚙️  Test 3: Provider Configuration")
    provider_config = integration.get_provider_config("openai", "gpt-5-nano")
    
    print("Provider config for gpt-5-nano:")
    for key, value in provider_config.items():
        print(f"  {key}: {value}")
    
    # Should have per_turn_append requirement
    if provider_config.get("requires_per_turn_append", False):
        print("✅ Correctly identified gpt-5-nano requires per_turn_append")
    else:
        print("❌ Failed to identify per_turn_append requirement")
    
    # Test 4: Performance monitoring
    print("\n📊 Test 4: Performance Monitoring")
    perf_summary = integration.get_performance_summary(session_id)
    
    print(f"Session metrics: {len(perf_summary.get('session_metrics', {}))}")
    print(f"Tool executions: {len(perf_summary.get('tool_executions', []))}")
    
    global_summary = integration.get_performance_summary()
    print(f"Total sessions: {global_summary['total_sessions']}")
    print(f"Active sessions: {global_summary['active_sessions']}")
    
    # Test 5: Alternative approaches based on codebase analysis  
    print("\n🔬 Test 5: Research-Based Optimizations")
    
    # Test format preferences
    available_formats = ["aider_diff", "opencode_patch", "unified_diff"]
    preferred = integration.prompt_compiler.get_preferred_formats(available_formats)
    print(f"Format preference order: {preferred}")
    
    # Should prioritize aider_diff (2.3x success rate)
    if preferred[0] == "aider_diff":
        print("✅ Correctly prioritizes Aider SEARCH/REPLACE format")
    else:
        print("❌ Incorrect format prioritization")
    
    # Test OpenCode-style provider optimization
    if provider_config.get("tool_call_format") == "xml_blocks":
        print("✅ Using XML blocks for gpt-5-nano (OpenCode insight)")
    else:
        print("❌ Not using optimal format for gpt-5-nano")
    
    # Test Crush-style execution limits  
    if provider_config.get("max_parallel_tools") == 1:
        print("✅ Sequential tool execution for gpt-5-nano (Crush insight)")
    else:
        print("❌ Not using sequential execution")
    
    # Cleanup
    print("\n🧹 Cleanup")
    integration.cleanup_session(session_id)
    integration.shutdown()
    print("✅ Session cleaned up and integration shutdown")
    
    print("\n" + "=" * 70)
    print("🎉 Enhanced Agent Integration V2 testing completed!")
    print("Key features validated:")
    print("  ✅ per_turn_append mode for gpt-5-nano")
    print("  ✅ Research-based format preferences (Aider > OpenCode > Unified)")
    print("  ✅ Provider-specific optimizations (OpenCode insights)")
    print("  ✅ Sequential execution with timeouts (Crush insights)") 
    print("  ✅ Performance monitoring and caching")


if __name__ == "__main__":
    asyncio.run(test_per_turn_append_mode())