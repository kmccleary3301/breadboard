"""Runtime abstractions and concrete runtimes for model providers."""

from __future__ import annotations

import base64
import datetime
import json
import os
import re
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple


from .contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderToolCall,
)
from .registry import ProviderRuntimeRegistry, provider_registry
from .runtimes.openai import OpenAIBaseRuntime, OpenAIChatRuntime, OpenAIResponsesRuntime
from .runtimes.anthropic import AnthropicMessagesRuntime
from .sdk_bindings import provider_sdk_bindings
from .runtimes.testing import CliMockRuntime, MockRuntime, SmokeRuntime
from .builtins import register_builtin_runtimes
from ..logging.provider_dump import provider_dump_logger


# ---------------------------------------------------------------------------
# Normalised result objects shared by runtimes
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Shared helper mixins
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Anthropic Messages runtime
# ---------------------------------------------------------------------------



register_builtin_runtimes()
try:  # pragma: no cover - optional replay runtime
    from .runtime_replay import ReplayRuntime
except Exception:
    pass
try:  # pragma: no cover - optional codex runtime
    from .runtime_codex import CodexAppServerRuntime
except Exception:
    pass
__all__ = [
    "ProviderRuntime",
    "ProviderRuntimeContext",
    "ProviderRuntimeError",
    "ProviderRuntimeRegistry",
    "ProviderResult",
    "ProviderMessage",
    "ProviderToolCall",
    "provider_registry",
    "OpenAIChatRuntime",
    "OpenAIResponsesRuntime",
    "AnthropicMessagesRuntime",
    "CodexAppServerRuntime",
    "MockRuntime",
    "ReplayRuntime",
]
