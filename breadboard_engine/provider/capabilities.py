"""Capability descriptors for providers and runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ProviderCapabilities:
    tool_calls: str  # e.g., "parallel", "sequential"
    streaming: str  # e.g., "text_deltas", "event_deltas", "none"
    json_mode: str  # e.g., "strict", "best_effort", "none"
    reasoning: str  # e.g., "encrypted", "summary", "none"
    caching: str  # e.g., "explicit", "implicit", "none"


@dataclass(frozen=True)
class ModelCapabilityOverride:
    """Wire-exact behavior for a model whose provider defaults are insufficient."""

    supports_native_tools: Optional[bool] = None
    runtime_id: Optional[str] = None
    api_variant: Optional[str] = None
    capabilities: Optional[ProviderCapabilities] = None


MODEL_CAPABILITY_OVERRIDES: Dict[Tuple[str, str], ModelCapabilityOverride] = {
    (
        "openrouter",
        "deepseek/deepseek-v4-flash-0731",
    ): ModelCapabilityOverride(
        supports_native_tools=True,
        runtime_id="openrouter_chat",
        api_variant="chat",
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="event_deltas",
            json_mode="best_effort",
            reasoning="openrouter",
            caching="none",
        ),
    ),
}


def get_model_capability_override(
    provider_id: str, model_id: str
) -> Optional[ModelCapabilityOverride]:
    return MODEL_CAPABILITY_OVERRIDES.get((provider_id, model_id))


CAPABILITY_MATRIX: Dict[str, ProviderCapabilities] = {
    "codex": ProviderCapabilities(
        tool_calls="parallel",
        streaming="event_deltas",
        json_mode="best_effort",
        reasoning="summary",
        caching="implicit",
    ),
    "openai": ProviderCapabilities(
        tool_calls="parallel",
        streaming="text_deltas",
        json_mode="strict",
        reasoning="encrypted",
        caching="implicit",
    ),
    "openrouter": ProviderCapabilities(
        tool_calls="parallel",
        streaming="event_deltas",
        json_mode="best_effort",
        reasoning="summary",
        caching="none",
    ),
    "anthropic": ProviderCapabilities(
        tool_calls="parallel",
        streaming="event_deltas",
        json_mode="best_effort",
        reasoning="summary",
        caching="explicit",
    ),
    "mock": ProviderCapabilities(
        tool_calls="sequential",
        streaming="none",
        json_mode="strict",
        reasoning="none",
        caching="none",
    ),
    "cli_mock": ProviderCapabilities(
        tool_calls="sequential",
        streaming="none",
        json_mode="strict",
        reasoning="none",
        caching="none",
    ),
    "smoke": ProviderCapabilities(
        tool_calls="sequential",
        streaming="none",
        json_mode="strict",
        reasoning="none",
        caching="none",
    ),
    "replay": ProviderCapabilities(
        tool_calls="parallel",
        streaming="none",
        json_mode="strict",
        reasoning="summary",
        caching="none",
    ),
}
