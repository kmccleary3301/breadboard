from __future__ import annotations

from typing import Any, Dict, List

import yaml

from ..core.core import ToolCallParsed, ToolDefinition
from .enhanced_base_dialect import (
    EnhancedBaseDialect,
    EnhancedToolDefinition,
    ParsedToolCall,
    TaskType,
    ToolCallFormat,
)


class YAMLCommandDialect(EnhancedBaseDialect):
    """Enhanced YAML-command dialect with legacy parse compatibility."""

    def __init__(self):
        super().__init__()
        self.type_id = "yaml_command"
        self.format = ToolCallFormat.YAML_COMMAND
        self.provider_support = ["*"]
        self.performance_baseline = 0.68
        self.use_cases = [
            TaskType.CONFIGURATION,
            TaskType.SHELL_COMMANDS,
            TaskType.GENERAL,
        ]

    def get_system_prompt_section(self) -> str:
        return (
            "When you need to call tools in text, emit a YAML block with a top-level "
            "`tools` list containing `name` and `args` fields."
        )

    def parse_tool_calls(self, content: str) -> List[ParsedToolCall]:
        if not content:
            return []

        yaml_blocks: List[str] = []
        fenced_blocks = content.split("```yaml")
        if len(fenced_blocks) > 1:
            for block in fenced_blocks[1:]:
                yaml_blocks.append(block.split("```", 1)[0])
        else:
            yaml_blocks.append(content)

        parsed: List[ParsedToolCall] = []
        for block in yaml_blocks:
            try:
                payload = yaml.safe_load(block.strip()) or {}
            except yaml.YAMLError:
                continue

            tools = payload.get("tools") if isinstance(payload, dict) else None
            if not isinstance(tools, list):
                continue

            for item in tools:
                if not isinstance(item, dict) or "name" not in item:
                    continue
                arguments = item.get("args", {})
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
                parsed.append(
                    ParsedToolCall(
                        function=item["name"],
                        arguments=arguments,
                        raw_content=block.strip(),
                        format=self.format.value,
                        confidence=0.85,
                    )
                )

        return parsed

    def format_tools_for_prompt(self, tools: List[EnhancedToolDefinition]) -> str:
        return (
            "Use YAML blocks with `tools:` entries when the model needs a readable, "
            "provider-neutral command syntax."
        )

    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:
        """Legacy BaseToolDialect compatibility."""
        return self.get_system_prompt_section()

    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:
        """Legacy BaseToolDialect compatibility."""
        return [
            ToolCallParsed(function=call.function, arguments=call.arguments)
            for call in self.parse_tool_calls(text)
        ]
