from __future__ import annotations

import re


_PROGRESS_PATTERNS = [
    re.compile(
        r"\b(?:now|next|then)\s+i(?:'|’)?(?:m|ll| will| am)\b.{0,120}"
        r"\b(?:check|checking|inspect|inspecting|look|looking|verify|verifying|make|edit|continue|continuing|pick|picking)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi(?:'|’)?(?:m| am)\s+"
        r"(?:checking|inspecting|looking|going|about to|continuing|narrowing|picking up|reviewing|reading|verifying)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi(?:'|’)?ll\s+"
        r"(?:check|inspect|look|verify|make|edit|continue|review|read|run|do|use)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\blet me\s+(?:check|inspect|look|verify|make|edit|continue|review|read|run)\b",
        re.IGNORECASE,
    ),
]

_FINALITY_PATTERNS = [
    re.compile(r"^\s*(?:done|complete|completed|finished|verified|fixed|implemented)\b", re.IGNORECASE),
    re.compile(r"\b(?:completed|finished|verified|fixed|implemented)\b.{0,80}\b(?:successfully|now|locally|with)\b", re.IGNORECASE),
    re.compile(r"\btask\s+complete\b", re.IGNORECASE),
    re.compile(r"\bHARNESS_PARITY_[A-Z_]+_OK\b"),
]


def assistant_is_progress_update(text: str) -> bool:
    """Return True when assistant text is status/progress, not a final answer."""

    candidate = str(text or "").strip()
    if not candidate:
        return False
    if any(pattern.search(candidate) for pattern in _FINALITY_PATTERNS):
        return False
    return any(pattern.search(candidate) for pattern in _PROGRESS_PATTERNS)
