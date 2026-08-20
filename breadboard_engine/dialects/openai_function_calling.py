"""
Native OpenAI function calling dialect implementation.

Implements the highest-performing tool calling format according to UC Berkeley research,
achieving 85%+ success rates in production environments.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import re

from .enhanced_base_dialect import (
    EnhancedBaseDialect,
    ToolCallFormat,
    TaskType,
    EnhancedToolDefinition,
    EnhancedToolParameter,
    ParsedToolCall
)


class OpenAIFunctionCallingDialect(EnhancedBaseDialect):
    """Native OpenAI function calling dialect using structured API."""
    
    def __init__(self):
        super().__init__()
        self.type_id = "openai_function_calling"
        self.format = ToolCallFormat.NATIVE_FUNCTION_CALLING
        self.provider_support = ["openai", "azure_openai", "compatible"]
        self.performance_baseline = 0.85  # UC Berkeley leaderboard data
        self.use_cases = [
            TaskType.API_OPERATIONS,
            TaskType.DATA_PROCESSING,
            TaskType.CODE_EDITING,
            TaskType.GENERAL
        ]
    
    def get_system_prompt_section(self) -> str:
        """Get system prompt section for OpenAI function calling."""
        return """You have access to function calling. When you need to use tools, call the appropriate functions with the required parameters. The function calls will be processed automatically."""
    
    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        """Parse tool calls from OpenAI function calling format."""
        # This method handles parsing from raw OpenAI API response format
        # In practice, this would typically be handled by the OpenAI client library
        parsed_calls = []
        
        try:
            # Handle JSON-like function call format that might appear in content
            function_call_pattern = r'{"name":\s*"([^"]+)",\s*"arguments":\s*({[^}]*})}'
            matches = re.findall(function_call_pattern, content)
            
            for function_name, arguments_str in matches:
                try:
                    arguments = json.loads(arguments_str)
                    parsed_calls.append(ParsedToolCall(
                        function=function_name,
                        arguments=arguments,
                        raw_content=f'{{"name": "{function_name}", "arguments": {arguments_str}}}',
                        format=self.format.value,
                        confidence=0.95  # High confidence for structured format
                    ))
                except json.JSONDecodeError:
                    continue
            
            # Also handle direct function call mentions in text
            if not parsed_calls:
                # Look for function names followed by parameters
                func_pattern = r'(\w+)\((.*?)\)'
                func_matches = re.findall(func_pattern, content, re.DOTALL)
                
                for func_name, params_str in func_matches:
                    try:
                        # Try to parse as JSON-like parameters
                        if params_str.strip():
                            # Simple parameter parsing for common cases
                            arguments = self._parse_simple_parameters(params_str)
                        else:
                            arguments = {}
                        
                        parsed_calls.append(ParsedToolCall(
                            function=func_name,
                            arguments=arguments,
                            raw_content=f"{func_name}({params_str})",
                            format=self.format.value,
                            confidence=0.7  # Lower confidence for text parsing
                        ))
                    except Exception:
                        continue
        
        except Exception:
            # Return empty list if parsing fails completely
            pass
        
        return parsed_calls
    
    def _parse_simple_parameters(self, params_str: str) -> Dict[str, Any]:
        """Parse simple parameter strings."""
        arguments = {}
        
        # Handle JSON-like parameter format
        if params_str.strip().startswith('{'):
            try:
                return json.loads(params_str.strip())
            except json.JSONDecodeError:
                pass
        
        # Handle key=value format
        kv_pattern = r'(\w+)\s*=\s*([^,]+)'
        matches = re.findall(kv_pattern, params_str)
        
        for key, value in matches:
            value = value.strip()
            # Try to parse as JSON value
            try:
                if value.startswith('"') and value.endswith('"'):
                    arguments[key] = value[1:-1]  # Remove quotes
                elif value.lower() in ['true', 'false']:
                    arguments[key] = value.lower() == 'true'
                elif value.isdigit():
                    arguments[key] = int(value)
                elif '.' in value and value.replace('.', '').isdigit():
                    arguments[key] = float(value)
                else:
                    arguments[key] = value
            except ValueError:
                arguments[key] = value
        
        return arguments
    
    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        """Format tools for OpenAI function calling prompt."""
        # For native function calling, the tools are typically passed via the API
        # This method provides a fallback description for the system prompt
        if not tools:
            return ""
        
        sections = ["Available functions:"]
        
        for tool in tools:
            if not tool.supports_format(self.format):
                continue
            
            # Create function signature
            params = []
            for param in tool.parameters:
                param_type = param.type or "any"
                if param.required:
                    params.append(f"{param.name}: {param_type}")
                else:
                    default = param.default if param.default is not None else "optional"
                    params.append(f"{param.name}: {param_type} = {default}")
            
            signature = f"{tool.name}({', '.join(params)})"
            sections.append(f"- {signature}")
            sections.append(f"  {tool.description}")
            
            # Add parameter descriptions
            for param in tool.parameters:
                if param.description:
                    sections.append(f"  - {param.name}: {param.description}")
        
        return "\n".join(sections)
    
    def format_for_provider(self, provider: str, tools: List[EnhancedToolDefinition]) -> Dict[str, Any]:
        """Format tools for OpenAI provider API."""
        if provider not in ["openai", "azure_openai", "compatible"]:
            return super().format_for_provider(provider, tools)
        
        # Convert to OpenAI function calling format
        functions = []
        
        for tool in tools:
            if not tool.supports_format(self.format):
                continue
            
            function_def = {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
            
            # Add parameters
            for param in tool.parameters:
                prop_def = {
                    "type": self._convert_type_to_openai(param.type),
                    "description": param.description or ""
                }
                
                # Add validation rules
                if param.validation_rules:
                    if "enum" in param.validation_rules:
                        prop_def["enum"] = param.validation_rules["enum"]
                    if "minimum" in param.validation_rules:
                        prop_def["minimum"] = param.validation_rules["minimum"]
                    if "maximum" in param.validation_rules:
                        prop_def["maximum"] = param.validation_rules["maximum"]
                    if "pattern" in param.validation_rules:
                        prop_def["pattern"] = param.validation_rules["pattern"]
                
                function_def["parameters"]["properties"][param.name] = prop_def
                
                if param.required:
                    function_def["parameters"]["required"].append(param.name)
            
            functions.append(function_def)
        
        return {
            "functions": functions,
            "function_call": "auto",  # Let model decide when to call functions
            "requires_user_injection": False,  # Native API handles this
            "supports_parallel_calls": True,
            "provider_metadata": {
                "supports_structured_outputs": True,
                "max_tools_per_call": 128,
                "supports_streaming": True
            }
        }
    
    def _convert_type_to_openai(self, param_type: Optional[str]) -> str:
        """Convert parameter type to OpenAI schema type."""
        if not param_type:
            return "string"
        
        param_type = param_type.lower()
        
        type_mapping = {
            "str": "string",
            "string": "string",
            "int": "integer",
            "integer": "integer",
            "float": "number",
            "number": "number",
            "bool": "boolean",
            "boolean": "boolean",
            "list": "array",
            "array": "array",
            "dict": "object",
            "object": "object",
            "any": "string"
        }
        
        return type_mapping.get(param_type, "string")
    
    def requires_user_message_injection(self) -> bool:
        """OpenAI function calling doesn't require user message injection."""
        return False
    
    def estimate_token_overhead(self, num_tools: int) -> int:
        """Estimate token overhead for OpenAI function calling."""
        # Native function calling has minimal prompt overhead
        # Most overhead is in the function definitions passed via API
        base_overhead = 10  # Minimal system prompt addition
        per_tool_overhead = 0  # Tools are passed via API, not prompt
        return base_overhead + (num_tools * per_tool_overhead)
    
    def create_openai_tools_format(self, tools: List[EnhancedToolDefinition]) -> List[Dict[str, Any]]:
        """Create OpenAI tools format for new API."""
        openai_tools = []
        
        for tool in tools:
            if not tool.supports_format(self.format):
                continue
            
            tool_def = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }
            
            # Add parameters
            for param in tool.parameters:
                prop_def = {
                    "type": self._convert_type_to_openai(param.type),
                    "description": param.description or ""
                }
                
                # Add validation rules
                if param.validation_rules:
                    if "enum" in param.validation_rules:
                        prop_def["enum"] = param.validation_rules["enum"]
                    if "minimum" in param.validation_rules:
                        prop_def["minimum"] = param.validation_rules["minimum"]
                    if "maximum" in param.validation_rules:
                        prop_def["maximum"] = param.validation_rules["maximum"]
                
                tool_def["function"]["parameters"]["properties"][param.name] = prop_def
                
                if param.required:
                    tool_def["function"]["parameters"]["required"].append(param.name)
            
            openai_tools.append(tool_def)
        
        return openai_tools
    
    def parse_openai_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[ParsedToolCall]:
        """Parse tool calls from OpenAI API response format."""
        parsed_calls = []
        
        for tool_call in tool_calls:
            try:
                function_name = tool_call["function"]["name"]
                arguments_str = tool_call["function"]["arguments"]
                
                # Parse arguments
                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    # Handle malformed JSON by trying to fix common issues
                    arguments = self._repair_json(arguments_str)
                
                parsed_calls.append(ParsedToolCall(
                    function=function_name,
                    arguments=arguments,
                    raw_content=json.dumps(tool_call),
                    call_id=tool_call.get("id"),
                    format=self.format.value,
                    confidence=0.98  # Very high confidence for API format
                ))
                
            except (KeyError, TypeError) as e:
                # Skip malformed tool calls
                continue
        
        return parsed_calls
    
    def _repair_json(self, json_str: str) -> Dict[str, Any]:
        """Attempt to repair malformed JSON from function arguments."""
        try:
            # Try to fix common JSON issues
            repaired = json_str.strip()
            
            # Fix unquoted strings (basic cases)
            repaired = re.sub(r':\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*([,}])', r': "\1"\2', repaired)
            
            # Fix trailing commas
            repaired = re.sub(r',\s*}', '}', repaired)
            repaired = re.sub(r',\s*]', ']', repaired)
            
            return json.loads(repaired)
        except:
            # If repair fails, return empty dict
            return {}