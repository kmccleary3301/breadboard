"""Utilities for managing encrypted reasoning traces across provider calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EncryptedReasoningTrace:
    """Container for encrypted reasoning payloads returned by providers."""

    payload: Any
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasoningSummary:
    """Safe-to-display reasoning summaries."""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class ReasoningTraceStore:
    """In-memory store that tracks reasoning traces between turns."""

    def __init__(self) -> None:
        self._encrypted_traces: List[EncryptedReasoningTrace] = []
        self._summaries: List[ReasoningSummary] = []

    # --- Encrypted traces -----------------------------------------------------
    def record_encrypted_trace(
        self,
        payload: Any,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._encrypted_traces.append(
            EncryptedReasoningTrace(payload=payload, metadata=metadata or {})
        )

    def get_encrypted_traces(self) -> List[EncryptedReasoningTrace]:
        return list(self._encrypted_traces)

    def clear_encrypted_traces(self) -> None:
        self._encrypted_traces.clear()

    # --- Reasoning summaries --------------------------------------------------
    def record_summary(
        self,
        text: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not text:
            return
        self._summaries.append(
            ReasoningSummary(text=text, metadata=metadata or {})
        )

    def get_summaries(self) -> List[ReasoningSummary]:
        return list(self._summaries)

    def clear_summaries(self) -> None:
        self._summaries.clear()

    # --- Utilities ------------------------------------------------------------
    def reset(self) -> None:
        self.clear_encrypted_traces()
        self.clear_summaries()


__all__ = [
    "EncryptedReasoningTrace",
    "ReasoningSummary",
    "ReasoningTraceStore",
]
