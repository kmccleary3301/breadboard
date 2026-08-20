from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolCallParsed:
    function: str
    arguments: Dict[str, Any]
    dialect: str | None = None


@dataclass
class ToolParameter:
    name: str
    type: str | None = None
    description: str | None = None
    default: Any | None = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    type_id: str = "python"
    blocking: bool = False


class BaseToolDialect(ABC):
    type_id: str

    @abstractmethod
    def prompt_for_tools(self, tools: List[ToolDefinition]) -> str:  # pragma: no cover - interface
        ...

    @abstractmethod
    def parse_calls(self, text: str, tools: List[ToolDefinition]) -> List[ToolCallParsed]:  # pragma: no cover - interface
        ...



