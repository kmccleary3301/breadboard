"""OpenAI-compatible provider runtimes."""

from __future__ import annotations

import base64
import json
import os
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderToolCall,
)
from ....logging.provider_dump import provider_dump_logger
from ...registry import provider_registry
from ...sdk_bindings import provider_sdk_bindings
from .chat import OpenAIChatRuntime
from .responses import OpenAIResponsesRuntime
from .streaming import OpenAIBaseRuntime

# Preserve the original pickle/import surface while allowing each implementation
# to live in its focused module.
OpenAIBaseRuntime.__module__ = __name__
OpenAIChatRuntime.__module__ = __name__
OpenAIResponsesRuntime.__module__ = __name__

provider_registry.register_runtime("openai_chat", OpenAIChatRuntime)
provider_registry.register_runtime("openrouter_chat", OpenAIChatRuntime)
provider_registry.register_runtime("openai_responses", OpenAIResponsesRuntime)

# Loading focused submodules creates parent attributes; the former flat module did
# not expose those names, so keep the package-level public surface exact.
for _submodule_name in ("conversion", "streaming", "chat", "responses"):
    globals().pop(_submodule_name, None)

__all__ = [
    "Any",
    "Dict",
    "List",
    "OpenAIBaseRuntime",
    "OpenAIChatRuntime",
    "OpenAIResponsesRuntime",
    "Optional",
    "ProviderMessage",
    "ProviderResult",
    "ProviderRuntime",
    "ProviderRuntimeContext",
    "ProviderRuntimeError",
    "ProviderToolCall",
    "SimpleNamespace",
    "Tuple",
    "annotations",
    "base64",
    "json",
    "os",
    "provider_dump_logger",
    "provider_registry",
    "provider_sdk_bindings",
    "re",
]
