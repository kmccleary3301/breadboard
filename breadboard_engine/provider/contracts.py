"""Provider runtime contracts shared by concrete implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .routing import ProviderDescriptor


@dataclass
class ProviderToolCall:
    """Normalized representation of a provider tool call."""

    id: Optional[str]
    name: Optional[str]
    arguments: str
    type: str = "function"
    raw: Any = None


@dataclass
class ProviderMessage:
    """Normalized assistant message returned from a provider."""

    role: str
    content: Optional[str]
    tool_calls: List[ProviderToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    index: Optional[int] = None
    raw_message: Any = None
    raw_choice: Any = None
    reasoning: Optional[Any] = None
    annotations: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    """Result object returned from a provider runtime invocation."""

    messages: List[ProviderMessage]
    raw_response: Any
    usage: Optional[Dict[str, Any]] = None
    encrypted_reasoning: Optional[List[Any]] = None
    reasoning_summaries: Optional[List[str]] = None
    model: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderRuntimeContext:
    """Context object passed to provider runtimes."""

    session_state: Any
    agent_config: Dict[str, Any]
    stream: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class ProviderRuntimeError(RuntimeError):
    """Raised when a provider runtime encounters a fatal error."""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details: Dict[str, Any] = details or {}


class ProviderRuntime:
    """Interface for provider runtimes."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        raise NotImplementedError

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        raise NotImplementedError
