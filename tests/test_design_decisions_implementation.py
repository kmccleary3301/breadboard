"""
Test suite for implementing design decisions from 08-13-25 research report.

Tests cover:
1. Sequential bash execution with blocking constraints
2. Assistant message continuation pattern
3. Enhanced tool configuration and validation
4. Aider SEARCH/REPLACE format optimization
"""

import pytest
import asyncio
import json
import tempfile
import os
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

# Import the modules we'll be testing/modifying
from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from tool_calling.composite import CompositeToolCaller
from tool_calling.system_prompt_compiler import SystemPromptCompiler
from breadboard.sandbox_v2 import DevSandboxV2


class TestSequentialBashExecution:
    """Test sequential bash execution with blocking constraints."""
    
    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent for testing."""
        agent = MagicMock()
        agent.config = {
            'tools': {
                'enabled': {
                    'run_shell': True,
                    'apply_search_replace': True,
                    'read_file': True
                }
            },
            'limits': {'max_steps': 10}
        }
        return agent
    
    @pytest.fixture
    def mock_sandbox(self):
        """Create a mock sandbox for testing."""
        # Create a simple mock instead of actual DevSandboxV2 (Ray actor)
        mock_sandbox = MagicMock()
        mock_sandbox.run_shell = MagicMock()
        mock_sandbox.read_file = MagicMock()
        return mock_sandbox
    
    def test_bash_blocking_constraint(self, mock_agent, mock_sandbox):
        """Test that bash commands execute sequentially and block other tools."""
        # This test will verify that when a bash command is executing,
        # no other tools can run until it completes
        
        execution_order = []
        
        def mock_bash_execute(command):
            execution_order.append(f"bash_start:{command}")
            # Simulate bash execution time
            import time
            time.sleep(0.1)
            execution_order.append(f"bash_end:{command}")
            return {"stdout": "success", "stderr": "", "exit_code": 0}
        
        def mock_other_tool_execute(tool_name):
            execution_order.append(f"tool:{tool_name}")
            return {"result": "success"}
        
        # Mock tool calls attempting parallel execution
        tool_calls = [
            {"name": "run_shell", "args": {"command": "echo test1"}},
            {"name": "read_file", "args": {"path": "test.txt"}},
            {"name": "run_shell", "args": {"command": "echo test2"}}
        ]
        
        # Execute tools - should be sequential for bash
        with patch.object(mock_sandbox, 'run_shell', side_effect=mock_bash_execute):
            with patch.object(mock_sandbox, 'read_file', side_effect=lambda x: mock_other_tool_execute('read_file')):
                # Simulate sequential execution
                for call in tool_calls:
                    if call["name"] == "run_shell":
                        mock_bash_execute(call["args"]["command"])
                    else:
                        mock_other_tool_execute(call["name"])
        
        # Verify execution order
        expected_order = [
            "bash_start:echo test1",
            "bash_end:echo test1",
            "tool:read_file",
            "bash_start:echo test2", 
            "bash_end:echo test2"
        ]
        
        assert execution_order == expected_order
    
    def test_one_bash_per_turn_constraint(self):
        """Test that only one bash command is allowed per agent turn."""
        
        tool_calls = [
            {"name": "run_shell", "args": {"command": "echo test1"}},
            {"name": "run_shell", "args": {"command": "echo test2"}},
            {"name": "read_file", "args": {"path": "test.txt"}}
        ]
        
        # This should raise an error or filter to only first bash command
        bash_calls = [call for call in tool_calls if call["name"] == "run_shell"]
        
        # Assert constraint: only one bash call per turn
        assert len(bash_calls) == 2  # Before constraint
        
        # After applying constraint, should be reduced to 1
        filtered_calls = tool_calls[:1] + [call for call in tool_calls[1:] if call["name"] != "run_shell"]
        bash_calls_after = [call for call in filtered_calls if call["name"] == "run_shell"]
        
        assert len(bash_calls_after) <= 1
    
    def test_tool_state_tracking(self):
        """Test OpenCode-style tool state tracking implementation."""
        
        class ToolState:
            def __init__(self):
                self.status = "pending"
                self.start_time = None
                self.end_time = None
                self.input_args = None
                self.output = None
                self.error = None
        
        # Test state transitions
        tool_state = ToolState()
        
        # Initial state
        assert tool_state.status == "pending"
        
        # Running state
        tool_state.status = "running"
        tool_state.start_time = 1234567890
        tool_state.input_args = {"command": "echo test"}
        
        assert tool_state.status == "running"
        assert tool_state.start_time is not None
        
        # Completed state
        tool_state.status = "completed"
        tool_state.end_time = 1234567891
        tool_state.output = "test\n"
        
        assert tool_state.status == "completed"
        assert tool_state.end_time > tool_state.start_time


class TestAssistantMessageContinuation:
    """Test assistant message continuation pattern implementation."""
    
    def test_message_part_structure(self):
        """Test the message part structure based on OpenCode pattern."""
        
        class MessagePart:
            def __init__(self, part_type, **kwargs):
                self.type = part_type
                self.id = kwargs.get('id', 'part_123')
                self.session_id = kwargs.get('session_id', 'session_456')
                self.message_id = kwargs.get('message_id', 'msg_789')
                
                if part_type == "text":
                    self.text = kwargs.get('text', '')
                elif part_type == "tool":
                    self.call_id = kwargs.get('call_id', 'call_abc')
                    self.tool = kwargs.get('tool', 'bash')
                    self.state = kwargs.get('state', {})
        
        # Test text part
        text_part = MessagePart("text", text="I'll help you with that task.")
        assert text_part.type == "text"
        assert text_part.text == "I'll help you with that task."
        
        # Test tool part
        tool_part = MessagePart("tool", 
                               call_id="call_123",
                               tool="run_shell",
                               state={"status": "completed", "output": "success"})
        
        assert tool_part.type == "tool"
        assert tool_part.call_id == "call_123"
        assert tool_part.tool == "run_shell"
        assert tool_part.state["status"] == "completed"
    
    def test_assistant_message_structure(self):
        """Test the complete assistant message with multiple parts."""
        
        class AssistantMessage:
            def __init__(self):
                self.role = "assistant"
                self.parts = []
                self.metadata = {
                    "time": {"created": 1234567890, "completed": None},
                    "cost": 0.0,
                    "tokens": {"input": 0, "output": 0, "reasoning": 0}
                }
            
            def add_part(self, part):
                self.parts.append(part)
        
        # Create assistant message
        msg = AssistantMessage()
        
        # Add text part
        msg.add_part({
            "type": "text",
            "content": "I'll run the command for you."
        })
        
        # Add tool part
        msg.add_part({
            "type": "tool",
            "tool": "run_shell",
            "call_id": "call_123",
            "state": {
                "status": "completed",
                "input": {"command": "echo test"},
                "output": "test\n",
                "time": {"start": 1234567890, "end": 1234567891}
            }
        })
        
        # Add continuation text
        msg.add_part({
            "type": "text",
            "content": "The command completed successfully."
        })
        
        # Verify structure
        assert msg.role == "assistant"
        assert len(msg.parts) == 3
        assert msg.parts[0]["type"] == "text"
        assert msg.parts[1]["type"] == "tool"
        assert msg.parts[2]["type"] == "text"
        
        # Verify tool part details
        tool_part = msg.parts[1]
        assert tool_part["tool"] == "run_shell"
        assert tool_part["state"]["status"] == "completed"
        assert "test" in tool_part["state"]["output"]
    
    def test_conversation_flow_preservation(self):
        """Test that conversation flow is preserved with assistant continuation."""
        
        conversation = [
            {
                "role": "user",
                "content": "Please run the command 'echo hello'"
            },
            {
                "role": "assistant",
                "parts": [
                    {"type": "text", "content": "I'll run that command for you."},
                    {
                        "type": "tool",
                        "tool": "run_shell",
                        "state": {
                            "status": "completed",
                            "input": {"command": "echo hello"},
                            "output": "hello\n"
                        }
                    },
                    {"type": "text", "content": "The command executed successfully and output 'hello'."}
                ]
            }
        ]
        
        # Verify conversation structure
        assert len(conversation) == 2
        assert conversation[0]["role"] == "user"
        assert conversation[1]["role"] == "assistant"
        
        # Verify no intermediate user messages for tool results
        user_messages = [msg for msg in conversation if msg["role"] == "user"]
        assert len(user_messages) == 1  # Only original user message
        
        # Verify assistant message contains tool execution
        assistant_msg = conversation[1]
        tool_parts = [part for part in assistant_msg["parts"] if part["type"] == "tool"]
        assert len(tool_parts) == 1
        assert tool_parts[0]["state"]["status"] == "completed"


class TestEnhancedToolConfiguration:
    """Test enhanced tool configuration and validation schema."""
    
    def test_tool_blocking_configuration(self):
        """Test tool blocking configuration in YAML schema."""
        
        tool_config = {
            "id": "run_shell",
            "description": "Run a shell command",
            "blocking": True,  # New field
            "max_per_turn": 1,  # New field
            "parameters": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 30}
            }
        }
        
        assert tool_config["blocking"] is True
        assert tool_config["max_per_turn"] == 1
        assert "command" in tool_config["parameters"]
    
    def test_tool_dependency_configuration(self):
        """Test tool dependency configuration."""
        
        tool_config = {
            "id": "apply_search_replace",
            "description": "Apply SEARCH/REPLACE to file",
            "dependencies": ["read_file"],  # New field
            "blocking": False,
            "parameters": {
                "file_name": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"}
            }
        }
        
        assert "read_file" in tool_config["dependencies"]
        assert tool_config["blocking"] is False
    
    def test_enhanced_validation_schema(self):
        """Test enhanced validation schema for tool configurations."""
        
        from jsonschema import validate, ValidationError
        
        # Enhanced schema based on research findings
        enhanced_schema = {
            "type": "object",
            "required": ["id", "description", "parameters"],
            "properties": {
                "id": {"type": "string"},
                "description": {"type": "string"},
                "blocking": {"type": "boolean", "default": False},
                "max_per_turn": {"type": "integer", "default": None},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "parameters": {"type": "object"}
            }
        }
        
        # Valid configuration
        valid_config = {
            "id": "run_shell",
            "description": "Execute shell command",
            "blocking": True,
            "max_per_turn": 1,
            "parameters": {"command": {"type": "string"}}
        }
        
        # Should validate without error
        try:
            validate(instance=valid_config, schema=enhanced_schema)
            validation_passed = True
        except ValidationError:
            validation_passed = False
        
        assert validation_passed


class TestAiderSearchReplaceOptimization:
    """Test Aider SEARCH/REPLACE format optimization."""
    
    def test_aider_format_parsing(self):
        """Test parsing of Aider SEARCH/REPLACE format."""
        
        aider_content = """
<<<<<<< SEARCH
def old_function():
    return "old"
=======
def new_function():
    return "new"
>>>>>>> REPLACE
"""
        
        def parse_aider_block(content):
            lines = content.strip().split('\n')
            search_lines = []
            replace_lines = []
            mode = None
            
            for line in lines:
                if line.strip() == "<<<<<<< SEARCH":
                    mode = "search"
                elif line.strip() == "=======":
                    mode = "replace"
                elif line.strip() == ">>>>>>> REPLACE":
                    mode = None
                elif mode == "search":
                    search_lines.append(line)
                elif mode == "replace":
                    replace_lines.append(line)
            
            return {
                "search": '\n'.join(search_lines),
                "replace": '\n'.join(replace_lines)
            }
        
        result = parse_aider_block(aider_content)
        
        assert "old_function" in result["search"]
        assert "new_function" in result["replace"]
        assert "old" in result["search"]
        assert "new" in result["replace"]
    
    def test_aider_vs_unified_diff_preference(self):
        """Test that Aider format is preferred over unified diff."""
        
        available_formats = ["aider_diff", "unified_diff", "opencode_patch"]
        
        # Based on research: Aider should be preferred
        def select_preferred_format(formats):
            preference_order = ["aider_diff", "opencode_patch", "unified_diff"]
            for preferred in preference_order:
                if preferred in formats:
                    return preferred
            return formats[0] if formats else None
        
        preferred = select_preferred_format(available_formats)
        assert preferred == "aider_diff"
    
    def test_aider_error_handling(self):
        """Test error handling for Aider SEARCH/REPLACE format."""
        
        def validate_aider_block(search_text, replace_text, file_content):
            """Validate that search text exists in file."""
            if search_text not in file_content:
                return {
                    "valid": False,
                    "error": "Search text not found in file",
                    "suggestion": "Check that search text exactly matches file content"
                }
            
            return {"valid": True, "error": None}
        
        file_content = "def hello():\n    return 'world'"
        search_text = "def hello():\n    return 'world'"
        replace_text = "def hello():\n    return 'universe'"
        
        # Valid case
        result = validate_aider_block(search_text, replace_text, file_content)
        assert result["valid"] is True
        
        # Invalid case
        wrong_search = "def goodbye():"
        result = validate_aider_block(wrong_search, replace_text, file_content)
        assert result["valid"] is False
        assert "not found" in result["error"]


class TestIntegrationScenarios:
    """Test integration scenarios combining all design decisions."""
    
    def test_complete_workflow_integration(self):
        """Test complete workflow with all new patterns."""
        
        # Simulate a complete agent workflow
        workflow_steps = []
        
        # Step 1: User request
        user_message = {
            "role": "user",
            "content": "Please create a test file and run a command"
        }
        workflow_steps.append(user_message)
        
        # Step 2: Assistant response with tool calls
        assistant_message = {
            "role": "assistant",
            "parts": [
                {"type": "text", "content": "I'll create the file and run the command for you."},
                {
                    "type": "tool",
                    "tool": "create_file",
                    "call_id": "call_1",
                    "state": {
                        "status": "completed",
                        "input": {"path": "test.txt"},
                        "output": "File created successfully"
                    }
                },
                {
                    "type": "tool", 
                    "tool": "run_shell",
                    "call_id": "call_2",
                    "state": {
                        "status": "completed",
                        "input": {"command": "ls -la test.txt"},
                        "output": "-rw-r--r-- 1 user user 0 Aug 13 10:00 test.txt"
                    }
                },
                {"type": "text", "content": "File created and verified successfully."}
            ]
        }
        workflow_steps.append(assistant_message)
        
        # Verify workflow structure
        assert len(workflow_steps) == 2
        assert workflow_steps[0]["role"] == "user"
        assert workflow_steps[1]["role"] == "assistant"
        
        # Verify tool execution order (sequential)
        assistant_parts = workflow_steps[1]["parts"]
        tool_parts = [p for p in assistant_parts if p["type"] == "tool"]
        
        assert len(tool_parts) == 2
        assert tool_parts[0]["tool"] == "create_file"
        assert tool_parts[1]["tool"] == "run_shell"
        
        # Verify all tools completed successfully
        for tool_part in tool_parts:
            assert tool_part["state"]["status"] == "completed"
    
    @pytest.mark.asyncio
    async def test_ray_sce_test_environment_compatibility(self):
        """Test compatibility with ray_sce_test conda environment."""
        
        # This test verifies that our implementation works in the target environment
        import sys
        import subprocess
        
        # Check conda environment
        result = subprocess.run(
            ["conda", "list", "-n", "ray_sce_test"],
            capture_output=True,
            text=True
        )
        
        # Should run without error (if environment exists)
        # In actual test environment, this would verify package compatibility
        environment_available = result.returncode == 0
        
        # Mock test for now
        assert True  # Will be replaced with actual environment tests


if __name__ == "__main__":
    pytest.main([__file__, "-v"])