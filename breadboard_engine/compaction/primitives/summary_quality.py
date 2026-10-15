"""Bounded opaque-identifier extraction for strict summary preservation.

Matches compaction-safeguard-quality.ts:385-420 at the preset's pinned source.
Decimal/scientific values are data, not opaque identifiers; pure hexadecimal
identifiers are case-insensitive. The source bounds extraction at twelve IDs.
"""

import re


_IDENTIFIER = re.compile(
    r"(https?://\S+|(?<![A-Za-z0-9._-])/[\w.-]{2,}(?:/[\w.-]+)+|"
    r"[A-Za-z]:\\[\w\\.-]+|(?<![A-Za-z0-9._-])[A-Za-z0-9._-]+\.[A-Za-z0-9._/-]+:\d{1,5})|"
    r"(?:(?:(?:\d+\.\d+|\.\d+)(?:[eE][+-]?\d+)?|\d+\.[eE][+-]?\d+|"
    r"\d+\.?[eE][+-]\d+|(?![A-Fa-f0-9]{8,}(?![A-Fa-f0-9]))\d+\.?[eE]\d+)"
    r"(?:(?=[A-Za-z]+(?![A-Za-z0-9]))(?=[A-Za-z]*[G-Zg-z])[A-Za-z]+)?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*(?:[A-Fa-f0-9]{8,}|\d{6,}))([A-Za-z0-9_-]+))",
    re.ASCII,
)
_HEX = re.compile(r"[A-Fa-f0-9]{8,}\Z")


def extract_identifiers(text: str) -> list[str]:
    identifiers = []
    seen = set()
    for match in _IDENTIFIER.finditer(text):
        value = (match[1] or match[2] or "").strip().lstrip('(\"\'`[{<').rstrip(')]\"\'`,;:.!?<>')
        if _HEX.fullmatch(value):
            value = value.upper()
        if len(value) < 4 or value in seen:
            continue
        seen.add(value)
        identifiers.append(value)
        if len(identifiers) == 12:
            break
    return identifiers


def includes_identifier(summary: str, identifier: str) -> bool:
    return identifier.upper() in summary.upper() if _HEX.fullmatch(identifier) else identifier in summary
