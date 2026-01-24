"""
Sequential tool execution engine implementing design decisions from 08-13-25 report.

Key features:
1. Sequential bash execution with blocking constraints
2. Assistant message continuation pattern
3. OpenCode-style tool state tracking
4. Dependency resolution
5. Format preference enforcement
"""

import time
import asyncio
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import uuid

from ..compilation.enhanced_config_validator import EnhancedConfigValidator, ExecutionPlan


class ToolState(Enum):
    """Tool execution states based on OpenCode pattern."""
    PENDING = "pending"
    RUNNING = "running" 
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class ToolExecution:
    """Tool execution tracking with OpenCode-style state management."""
    call_id: str
    tool_name: str
    input_args: Dict[str, Any]
    state: ToolState = ToolState.PENDING
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    output: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def start(self) -> None:
        """Mark tool execution as started."""
        self.state = ToolState.RUNNING
        self.start_time = time.time()
    
    def complete(self, output: str, metadata: Dict[str, Any] = None) -> None:
        """Mark tool execution as completed."""
        self.state = ToolState.COMPLETED
        self.end_time = time.time()
        self.output = output
        if metadata:
            self.metadata.update(metadata)
    
    def fail(self, error: str) -> None:
        """Mark tool execution as failed."""
        self.state = ToolState.ERROR
        self.end_time = time.time()
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "input_args": self.input_args,
            "state": self.state.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata
        }


@dataclass
class MessagePart:
    """Message part structure based on OpenCode pattern."""
    type: str  # "text" | "tool" | "step-start" | "step-finish"
    id: str
    session_id: str
    message_id: str
    
    # Text part fields
    text: Optional[str] = None
    
    # Tool part fields  
    call_id: Optional[str] = None
    tool: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "type": self.type,
            "id": self.id,
            "session_id": self.session_id,
            "message_id": self.message_id
        }
        
        if self.text is not None:
            result["text"] = self.text
        if self.call_id is not None:
            result["call_id"] = self.call_id
        if self.tool is not None:
            result["tool"] = self.tool
        if self.state is not None:
            result["state"] = self.state
            
        return result


class AssistantMessage:
    """Assistant message with continuation pattern."""
    
    def __init__(self, session_id: str, message_id: str = None):
        self.session_id = session_id
        self.message_id = message_id or str(uuid.uuid4())
        self.role = "assistant"
        self.parts: List[MessagePart] = []
        self.metadata = {
            "time": {"created": time.time(), "completed": None},
            "cost": 0.0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0}
        }
    
    def add_text(self, text: str) -> MessagePart:
        """Add text part to message."""
        part = MessagePart(
            type="text",
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            message_id=self.message_id,
            text=text
        )
        self.parts.append(part)
        return part
    
    def add_tool_part(self, tool_execution: ToolExecution) -> MessagePart:
        """Add tool part to message."""
        part = MessagePart(
            type="tool",
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            message_id=self.message_id,
            call_id=tool_execution.call_id,
            tool=tool_execution.tool_name,
            state=tool_execution.to_dict()
        )
        self.parts.append(part)
        return part
    
    def complete(self) -> None:
        """Mark message as completed."""
        self.metadata["time"]["completed"] = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "role": self.role,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "parts": [part.to_dict() for part in self.parts],
            "metadata": self.metadata
        }


class SequentialToolExecutor:
    """
    Sequential tool executor implementing design decisions.
    
    Based on research findings:
    - Sequential execution for bash commands
    - Tool state tracking
    - Assistant message continuation
    - Dependency resolution
    """
    
    def __init__(self, 
                 config_validator: EnhancedConfigValidator,
                 tool_executor_func: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.config_validator = config_validator
        self.tool_executor_func = tool_executor_func
        self.current_executions: Dict[str, ToolExecution] = {}
        self.session_id = str(uuid.uuid4())
    
    async def execute_tool_calls(self, 
                                tool_calls: List[Dict[str, Any]], 
                                context: Dict[str, Any] = None) -> AssistantMessage:
        """
        Execute tool calls sequentially with proper blocking and state tracking.
        
        Returns AssistantMessage with continuation pattern.
        """
        context = context or {}
        
        # Validate tool calls against constraints
        validation_result = self.config_validator.validate_tool_calls(tool_calls)
        
        if not validation_result["valid"]:
            # Create error message
            message = AssistantMessage(self.session_id)
            error_text = "Tool execution validation failed:\n" + "\n".join(validation_result["errors"])
            message.add_text(error_text)
            message.complete()
            return message
        
        # Create assistant message for this execution
        message = AssistantMessage(self.session_id)
        
        # Add initial text if provided
        if context.get("initial_text"):
            message.add_text(context["initial_text"])
        
        # Get execution plan
        execution_plan = validation_result["execution_plan"]
        
        # Execute tools in planned order
        bash_executed = False
        
        for order_index in execution_plan.execution_order:
            tool_call = tool_calls[order_index]
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args", {})
            
            # Check bash constraint (critical research finding)
            if tool_name == "run_shell":
                if bash_executed:
                    error_text = "Only one bash command allowed per turn (research constraint)"
                    message.add_text(error_text)
                    continue
                bash_executed = True
            
            # Create tool execution tracker
            tool_execution = ToolExecution(
                call_id=str(uuid.uuid4()),
                tool_name=tool_name,
                input_args=tool_args
            )
            
            self.current_executions[tool_execution.call_id] = tool_execution
            
            # Execute tool with state tracking
            await self._execute_single_tool(tool_execution, message, execution_plan)
            
            # Check if this is a blocking point
            if order_index in execution_plan.blocking_points:
                # For blocking tools, wait and ensure completion before continuing
                if tool_execution.state == ToolState.ERROR:
                    # Stop execution on blocking tool failure
                    message.add_text(f"Execution stopped due to blocking tool failure: {tool_execution.error}")
                    break
        
        # Add completion text if all tools succeeded
        successful_executions = [
            ex for ex in self.current_executions.values() 
            if ex.state == ToolState.COMPLETED
        ]
        
        if len(successful_executions) == len(tool_calls):
            message.add_text("All tools executed successfully.")
        
        message.complete()
        return message
    
    async def _execute_single_tool(self, 
                                  tool_execution: ToolExecution, 
                                  message: AssistantMessage,
                                  execution_plan: ExecutionPlan) -> None:
        """Execute a single tool with full state tracking."""
        
        # Start execution
        tool_execution.start()
        
        # Add tool part to message (in running state)
        message.add_tool_part(tool_execution)
        
        try:
            # Prepare tool call for executor
            tool_call = {
                "function": tool_execution.tool_name,
                "arguments": tool_execution.input_args
            }
            
            # Execute tool
            result = self.tool_executor_func(tool_call)
            
            # Process result based on tool type
            if tool_execution.tool_name == "run_shell":
                output = self._format_bash_output(result)
                metadata = {
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "exit_code": result.get("exit", 0)
                }
                
                # Check for bash failure
                exit_code = result.get("exit", 0)
                if exit_code != 0:
                    tool_execution.fail(f"Command failed with exit code {exit_code}")
                    return
                    
            else:
                output = self._format_generic_output(result)
                metadata = {"raw_result": result}
            
            # Mark as completed
            tool_execution.complete(output, metadata)
            
            # Update tool part in message
            message.add_tool_part(tool_execution)
            
        except Exception as e:
            # Mark as failed
            tool_execution.fail(str(e))
            
            # Update tool part in message  
            message.add_tool_part(tool_execution)
    
    def _format_bash_output(self, result: Dict[str, Any]) -> str:
        """Format bash command output."""
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        exit_code = result.get("exit", 0)
        
        output_parts = []
        
        if stdout:
            output_parts.append(f"<stdout>\n{stdout}\n</stdout>")
        
        if stderr:
            output_parts.append(f"<stderr>\n{stderr}\n</stderr>")
        
        if exit_code != 0:
            output_parts.append(f"<exit_code>{exit_code}</exit_code>")
        
        return "\n".join(output_parts) if output_parts else "Command completed successfully"
    
    def _format_generic_output(self, result: Dict[str, Any]) -> str:
        """Format generic tool output."""
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            elif "content" in result:
                return str(result["content"])
            else:
                return str(result)
        else:
            return str(result)
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get summary of all executions in this session."""
        return {
            "session_id": self.session_id,
            "total_executions": len(self.current_executions),
            "completed": len([ex for ex in self.current_executions.values() if ex.state == ToolState.COMPLETED]),
            "failed": len([ex for ex in self.current_executions.values() if ex.state == ToolState.ERROR]),
            "executions": [ex.to_dict() for ex in self.current_executions.values()]
        }


# Integration functions
def create_sequential_executor(config_path: str, 
                             tool_executor_func: Callable) -> SequentialToolExecutor:
    """Create sequential executor with enhanced configuration."""
    validator = EnhancedConfigValidator(config_path)
    return SequentialToolExecutor(validator, tool_executor_func)


async def execute_with_continuation(executor: SequentialToolExecutor,
                                  tool_calls: List[Dict[str, Any]],
                                  initial_text: str = None) -> Dict[str, Any]:
    """Execute tools and return assistant message continuation."""
    context = {"initial_text": initial_text} if initial_text else {}
    message = await executor.execute_tool_calls(tool_calls, context)
    
    return {
        "message": message.to_dict(),
        "execution_summary": executor.get_execution_summary()
    }


# Example usage
if __name__ == "__main__":
    import asyncio
    
    # Mock tool executor
    def mock_tool_executor(tool_call):
        tool_name = tool_call["function"]
        args = tool_call["arguments"]
        
        if tool_name == "run_shell":
            return {
                "stdout": f"Executed: {args.get('command', '')}",
                "stderr": "",
                "exit": 0
            }
        else:
            return {"result": "success", "tool": tool_name}
    
    # Test execution
    async def test_execution():
        from ..compilation.enhanced_config_validator import EnhancedConfigValidator
        
        # Mock validator
        validator = EnhancedConfigValidator()
        validator.config = {
            "tools": [
                {"id": "run_shell", "blocking": True, "max_per_turn": 1, "dependencies": []},
                {"id": "read_file", "blocking": False, "dependencies": []}
            ]
        }
        validator._parse_tool_constraints()
        
        executor = SequentialToolExecutor(validator, mock_tool_executor)
        
        tool_calls = [
            {"name": "run_shell", "args": {"command": "echo test"}},
            {"name": "read_file", "args": {"path": "test.txt"}}
        ]
        
        result = await executor.execute_tool_calls(tool_calls, {"initial_text": "Executing tools..."})
        print("Execution result:", result.to_dict())
    
    # Run test
    # asyncio.run(test_execution())