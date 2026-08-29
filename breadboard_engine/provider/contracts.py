"""Stable public facade for strict provider runtime and exchange contracts.

Implementations live in focused internal modules; this module intentionally
re-exports the historical public contract surface.
"""

from __future__ import annotations

from .routing import ProviderDescriptor
from .contract_wire import (
    ProviderContractError,
    ProviderProtocolError,
    ProviderRole,
    canonical_json,
    parse_canonical_json,
)
from .contract_messages import (
    ProviderCorrelation,
    ProviderIdentity,
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
    normalize_content,
    normalize_provider_replay,
    normalize_request_messages,
    normalize_tool_call_dict,
    normalize_tool_result_dict,
)
from .contract_events import (
    ProviderCancelled,
    ProviderDone,
    ProviderErrorTerminal,
    ProviderEvent,
    normalize_terminal_message,
    normalize_usage,
)
from .contract_exchange import (
    ProviderExchangeV2,
    encode_provider_exchange,
    strip_provider_exchange_completion_sentinels,
    strip_public_completion_sentinel_lines,
    strip_public_completion_sentinel_tree,
)
from .contract_recorder import ProviderExchangeRecorder
from .profiles import (
    OpenAICompletionsCapabilities,
    OpenAICompletionsCompatibility,
    OpenAICompletionsProviderProfile,
    OpenAICompletionsSampling,
)
from .contract_runtime import (
    ProviderErrorKind,
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    sanitize_provider_result,
)

__all__ = [
    "ProviderContractError",
    "ProviderProtocolError",
    "ProviderCorrelation",
    "ProviderIdentity",
    "ProviderRequest",
    "ProviderEvent",
    "ProviderDone",
    "ProviderErrorTerminal",
    "ProviderCancelled",
    "ProviderExchangeV2",
    "ProviderExchangeRecorder",
    "encode_provider_exchange",
    "strip_provider_exchange_completion_sentinels",
    "strip_public_completion_sentinel_lines",
    "strip_public_completion_sentinel_tree",
    "canonical_json",
    "parse_canonical_json",
    "normalize_usage",
    "ProviderToolCall",
    "ProviderMessage",
    "ProviderResult",
    "OpenAICompletionsCapabilities",
    "OpenAICompletionsCompatibility",
    "OpenAICompletionsProviderProfile",
    "OpenAICompletionsSampling",
    "ProviderRuntimeContext",
    "ProviderRuntimeError",
    "ProviderRuntime",
    "sanitize_provider_result",
    "normalize_request_messages",
    "normalize_content",
]
