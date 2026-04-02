"""Capability descriptors for providers and runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class ProviderCapabilities:
    tool_calls: str  # e.g., "parallel", "sequential"
    streaming: str  # e.g., "text_deltas", "event_deltas", "none"
    json_mode: str  # e.g., "strict", "best_effort", "none"
    reasoning: str  # e.g., "encrypted", "summary", "none"
    caching: str  # e.g., "explicit", "implicit", "none"


CAPABILITY_MATRIX: Dict[str, ProviderCapabilities] = {
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
}

