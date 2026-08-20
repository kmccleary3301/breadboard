"""Explicit seam for optional provider SDKs and retry dependencies."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

try:  # pragma: no cover - import guard exercised through runtime error paths
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _OpenAI = None

try:  # pragma: no cover - import guard exercised through runtime error paths
    from anthropic import Anthropic as _Anthropic
    from anthropic import RateLimitError as _AnthropicRateLimitError
    try:
        from anthropic._exceptions import OverloadedError as _AnthropicOverloadedError  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        _AnthropicOverloadedError = None
except ImportError:  # pragma: no cover
    _Anthropic = None
    _AnthropicRateLimitError = None
    _AnthropicOverloadedError = None


@dataclass
class ProviderSdkBindings:
    """SDK constructors, error types, and retry dependencies used by runtimes."""

    openai: Any = _OpenAI
    anthropic: Any = _Anthropic
    anthropic_rate_limit_error: Any = _AnthropicRateLimitError
    anthropic_overloaded_error: Any = _AnthropicOverloadedError
    sleep: Callable[[float], Any] = time.sleep
    uniform: Callable[[float, float], float] = random.uniform


provider_sdk_bindings = ProviderSdkBindings()

__all__ = ["ProviderSdkBindings", "provider_sdk_bindings"]
