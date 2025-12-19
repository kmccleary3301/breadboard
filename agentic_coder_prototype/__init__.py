"""
Agentic Coder Prototype Module

A simplified, modular implementation of the agentic coding system.
This module abstracts complex implementation details and provides
a clean interface for agent-based code generation and manipulation.
"""

from .agent import AgenticCoder, create_agent
from .agent_llm_openai import OpenAIConductor
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager

__all__ = [
    'AgenticCoder',
    'create_agent', 
    'OpenAIConductor',
    'provider_router',
    'provider_adapter_manager'
]