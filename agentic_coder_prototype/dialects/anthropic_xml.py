"""
Anthropic XML dialect implementation.

Implements the Anthropic-native XML tool calling format, achieving 83% success 
rate in production environments according to our research.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape, unescape

from .enhanced_base_dialect import (
    EnhancedBaseDialect,
    ToolCallFormat,
    TaskType,
    EnhancedToolDefinition,
    EnhancedToolParameter,
    ParsedToolCall
)


class AnthropicXMLDialect(EnhancedBaseDialect):
    """Anthropic XML tool calling dialect."""
    
    def __init__(self):
        super().__init__()
        self.type_id = "anthropic_xml"
        self.format = ToolCallFormat.ANTHROPIC_XML
        self.provider_support = ["anthropic", "claude", "compatible"]
        self.performance_baseline = 0.83  # Production data
        self.use_cases = [
            TaskType.GENERAL,
            TaskType.FILE_MODIFICATION,
            TaskType.CODE_EDITING,
            TaskType.SHELL_COMMANDS,
            TaskType.CONFIGURATION
        ]
    
    def get_system_prompt_section(self) -> str:
        """Get system prompt section for Anthropic XML format."""
        return """When you need to use tools, call them using XML tags with the following format:

<function_calls>
<invoke name="tool_name">
<parameter name="param1">value1</parameter>
<parameter name="param2">value2</parameter>
</invoke>
</function_calls>

You can call multiple tools in a single response by adding multiple <invoke> blocks within the <function_calls> tags."""
    
    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        """Parse tool calls from Anthropic XML format."""
        parsed_calls = []
        
        # Look for function_calls blocks
        function_calls_pattern = r'<function_calls>(.*?)</function_calls>'
        function_blocks = re.findall(function_calls_pattern, content, re.DOTALL | re.IGNORECASE)
        
        for block in function_blocks:
            # Parse individual invoke blocks
            invoke_pattern = r'<invoke\s+name=["\']([^"\']+)["\']>(.*?)</invoke>'
            invokes = re.findall(invoke_pattern, block, re.DOTALL | re.IGNORECASE)
            
            for tool_name, invoke_content in invokes:
                try:
                    arguments = self._parse_parameters(invoke_content)
                    parsed_calls.append(ParsedToolCall(
                        function=tool_name,
                        arguments=arguments,
                        raw_content=f'<invoke name="{tool_name}">{invoke_content}</invoke>',
                        format=self.format.value,
                        confidence=0.95  # High confidence for structured XML
                    ))
                except Exception:
                    continue
        
        # Also look for standalone invoke blocks (without function_calls wrapper)
        standalone_pattern = r'<invoke\s+name=["\']([^"\']+)["\']>(.*?)</invoke>'
        standalone_matches = re.findall(standalone_pattern, content, re.DOTALL | re.IGNORECASE)
        
        for tool_name, invoke_content in standalone_matches:
            # Skip if already processed in function_calls block
            if any(call.function == tool_name and call.raw_content and invoke_content in call.raw_content 
                   for call in parsed_calls):
                continue
            
            try:
                arguments = self._parse_parameters(invoke_content)
                parsed_calls.append(ParsedToolCall(
                    function=tool_name,
                    arguments=arguments,
                    raw_content=f'<invoke name="{tool_name}">{invoke_content}</invoke>',
                    format=self.format.value,
                    confidence=0.9  # Slightly lower for standalone
                ))
            except Exception:
                continue
        
        return parsed_calls
    
    def _parse_parameters(self, invoke_content: str) -> Dict[str, Any]:
        """Parse parameters from invoke block content."""
        arguments = {}
        
        # Find all parameter tags
        param_pattern = r'<parameter\s+name=["\']([^"\']+)["\']>(.*?)</parameter>'
        params = re.findall(param_pattern, invoke_content, re.DOTALL | re.IGNORECASE)
        
        for param_name, param_value in params:
            # Unescape XML entities
            value = unescape(param_value.strip())
            
            # Try to convert to appropriate type
            arguments[param_name] = self._convert_value_type(value)
        
        return arguments
    
    def _convert_value_type(self, value: str) -> Any:
        """Convert string value to appropriate Python type."""
        value = value.strip()
        
        # Boolean values
        if value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        
        # Numeric values
        if value.isdigit():
            return int(value)
        
        try:
            if '.' in value:
                return float(value)
        except ValueError:
            pass
        
        # JSON-like structures
        if value.startswith('{') and value.endswith('}'):
            try:
                import json
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        
        if value.startswith('[') and value.endswith(']'):
            try:
                import json
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        
        # Return as string
        return value
    
    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        """Format tools for Anthropic XML prompt."""
        if not tools:
            return ""
        
        sections = [
            "# Available Tools",
            "",
            "You have access to the following tools. Use them with the XML format shown:",
            ""
        ]
        
        for tool in tools:
            if not tool.supports_format(self.format):
                continue
            
            sections.append(f"## {tool.name}")
            sections.append(f"**Description**: {tool.description}")
            
            if tool.parameters:
                sections.append("**Parameters**:")
                for param in tool.parameters:
                    required_text = " (required)" if param.required else " (optional)"
                    param_type = param.type or "string"
                    sections.append(f"- `{param.name}` ({param_type}){required_text}: {param.description or ''}")
                    
                    if param.examples:
                        sections.append(f"  Examples: {', '.join(param.examples)}")
            
            sections.append("")
            sections.append("**Usage Example**:")
            
            # Generate example usage
            example_params = []
            for param in tool.parameters[:3]:  # Show first 3 params in example
                example_value = self._get_example_value(param)
                example_params.append(f'<parameter name="{param.name}">{example_value}</parameter>')
            
            sections.append("```xml")
            sections.append("<function_calls>")
            sections.append(f'<invoke name="{tool.name}">')
            for param_line in example_params:
                sections.append(param_line)
            sections.append("</invoke>")
            sections.append("</function_calls>")
            sections.append("```")
            sections.append("")
        
        return "\n".join(sections)
    
    def _get_example_value(self, param: EnhancedToolParameter) -> str:
        """Get example value for parameter."""
        if param.examples:
            return param.examples[0]
        
        if param.default is not None:
            return str(param.default)
        
        param_type = (param.type or "string").lower()
        
        type_examples = {
            "string": "example_value",
            "str": "example_value", 
            "integer": "42",
            "int": "42",
            "number": "3.14",
            "float": "3.14",
            "boolean": "true",
            "bool": "true",
            "array": '["item1", "item2"]',
            "list": '["item1", "item2"]',
            "object": '{"key": "value"}',
            "dict": '{"key": "value"}'
        }
        
        return type_examples.get(param_type, "example_value")
    
    def format_for_provider(self, provider: str, tools: List[EnhancedToolDefinition]) -> Dict[str, Any]:
        """Format tools for Anthropic provider."""
        if provider not in ["anthropic", "claude", "compatible"]:
            return super().format_for_provider(provider, tools)
        
        # Anthropic uses tool definitions in a specific format
        tool_definitions = []
        
        for tool in tools:
            if not tool.supports_format(self.format):
                continue
            
            # Create Anthropic tool definition
            tool_def = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
            
            # Add parameters
            for param in tool.parameters:
                prop_def = {
                    "type": self._convert_type_to_anthropic(param.type),
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
                
                tool_def["input_schema"]["properties"][param.name] = prop_def
                
                if param.required:
                    tool_def["input_schema"]["required"].append(param.name)
            
            tool_definitions.append(tool_def)
        
        return {
            "tools": tool_definitions,
            "system_prompt_addition": self.get_system_prompt_section(),
            "requires_user_injection": True,
            "supports_parallel_calls": True,
            "provider_metadata": {
                "supports_xml_format": True,
                "supports_prefill": True,
                "max_tools_per_call": 64
            }
        }
    
    def _convert_type_to_anthropic(self, param_type: Optional[str]) -> str:
        """Convert parameter type to Anthropic schema type."""
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
    
    def create_tool_call_xml(self, function_name: str, arguments: Dict[str, Any]) -> str:
        """Create XML tool call from function name and arguments."""
        lines = ["<function_calls>"]
        lines.append(f'<invoke name="{function_name}">')
        
        for param_name, param_value in arguments.items():
            # Escape XML entities
            escaped_value = escape(str(param_value))
            lines.append(f'<parameter name="{param_name}">{escaped_value}</parameter>')
        
        lines.append("</invoke>")
        lines.append("</function_calls>")
        
        return "\n".join(lines)
    
    def create_multi_tool_xml(self, tool_calls: List[tuple[str, Dict[str, Any]]]) -> str:
        """Create XML for multiple tool calls."""
        lines = ["<function_calls>"]
        
        for function_name, arguments in tool_calls:
            lines.append(f'<invoke name="{function_name}">')
            
            for param_name, param_value in arguments.items():
                escaped_value = escape(str(param_value))
                lines.append(f'<parameter name="{param_name}">{escaped_value}</parameter>')
            
            lines.append("</invoke>")
        
        lines.append("</function_calls>")
        
        return "\n".join(lines)
    
    def validate_xml_structure(self, xml_content: str) -> bool:
        """Validate XML structure for tool calls."""
        try:
            # Wrap in root element if not present
            if not xml_content.strip().startswith('<function_calls>'):
                xml_content = f"<function_calls>{xml_content}</function_calls>"
            
            ET.fromstring(xml_content)
            return True
        except ET.ParseError:
            return False
    
    def estimate_token_overhead(self, num_tools: int) -> int:
        """Estimate token overhead for Anthropic XML format."""
        base_overhead = 200  # XML format explanation and examples
        per_tool_overhead = 50  # XML tool definitions are more verbose
        return base_overhead + (num_tools * per_tool_overhead)
    
    def supports_streaming_partial_calls(self) -> bool:
        """Check if format supports parsing partial tool calls during streaming."""
        return True
    
    def parse_partial_xml(self, partial_content: str) -> List[ParsedToolCall]:
        """Parse potentially incomplete XML tool calls from streaming."""
        parsed_calls = []
        
        # Look for complete invoke blocks even if function_calls is incomplete
        invoke_pattern = r'<invoke\s+name=["\']([^"\']+)["\']>(.*?)</invoke>'
        complete_invokes = re.findall(invoke_pattern, partial_content, re.DOTALL | re.IGNORECASE)
        
        for tool_name, invoke_content in complete_invokes:
            try:
                arguments = self._parse_parameters(invoke_content)
                parsed_calls.append(ParsedToolCall(
                    function=tool_name,
                    arguments=arguments,
                    raw_content=f'<invoke name="{tool_name}">{invoke_content}</invoke>',
                    format=self.format.value,
                    confidence=0.85  # Lower confidence for partial parsing
                ))
            except Exception:
                continue
        
        return parsed_calls
    
    def repair_malformed_xml(self, xml_content: str) -> str:
        """Attempt to repair malformed XML content."""
        # Fix common XML issues
        repaired = xml_content
        
        # Fix unescaped ampersands
        repaired = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', repaired)
        
        # Fix unclosed tags (basic repair)
        repaired = re.sub(r'<parameter\s+name=["\']([^"\']+)["\']>([^<]*?)(?=<parameter|</invoke|$)', 
                         r'<parameter name="\1">\2</parameter>', repaired)
        
        # Add missing closing tags
        if '<function_calls>' in repaired and '</function_calls>' not in repaired:
            repaired += '\n</function_calls>'
        
        return repaired