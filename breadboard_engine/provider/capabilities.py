"""Capability descriptors for providers and runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


from ..provider_broker.catalog import ProviderCapabilities, routable_provider_catalog


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
    entry.provider_id: entry.capabilities
    for entry in routable_provider_catalog()
    if entry.capabilities is not None
}
