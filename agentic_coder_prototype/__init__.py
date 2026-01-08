"""
Agentic Coder Prototype Module

A simplified, modular implementation of the agentic coding system.
This module abstracts complex implementation details and provides
a clean interface for agent-based code generation and manipulation.
"""

from typing import TYPE_CHECKING

__all__ = [
    "AgenticCoder",
    "create_agent",
    "OpenAIConductor",
    "provider_router",
    "provider_adapter_manager",
]


if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from .agent import AgenticCoder, create_agent  # noqa: F401
    from .agent_llm_openai import OpenAIConductor  # noqa: F401
    from .provider_routing import provider_router  # noqa: F401
    from .provider_adapters import provider_adapter_manager  # noqa: F401


def __getattr__(name: str):
    """Lazy-import heavy modules to keep lightweight entrypoints fast/robust."""
    if name in {"AgenticCoder", "create_agent"}:
        from .agent import AgenticCoder, create_agent

        return {"AgenticCoder": AgenticCoder, "create_agent": create_agent}[name]
    if name == "OpenAIConductor":
        from .agent_llm_openai import OpenAIConductor

        return OpenAIConductor
    if name == "provider_router":
        from .provider_routing import provider_router

        return provider_router
    if name == "provider_adapter_manager":
        from .provider_adapters import provider_adapter_manager

        return provider_adapter_manager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
