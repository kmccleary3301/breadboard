"""Paired contextual-user markers matching stock fragment boundaries."""
from __future__ import annotations

from ..params import Params, PresetError
from .accounting import _text_parts

# Rust str::trim uses Unicode White_Space, not Python's additional C0 separators.
_WHITESPACE = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def marker_pairs(params: Params, key: str) -> tuple[tuple[str, str], ...]:
    pairs = []
    values = params.list(key, [])
    if values is None:
        raise PresetError(f"{params.where}.{key} must be a list")
    for index, value in enumerate(values):
        child = params.child(value, f"{key}[{index}]")
        start, end = child.str("start"), child.str("end")
        if not start or not end:
            raise PresetError(f"{child.where} start and end must be nonempty strings")
        child.done()
        pairs.append((start.translate(_ASCII_LOWER), end.translate(_ASCII_LOWER)))
    return tuple(pairs)


def sampling_phases(params: Params, key: str) -> frozenset[str]:
    phases = params.list(key, [])
    if phases is None or any(phase not in ("pre_turn", "mid_turn") for phase in phases):
        raise PresetError(f"{params.where}.{key} must list pre_turn or mid_turn")
    return frozenset(phases)


def contextual_user(message, pairs) -> bool:
    if message.get("role") != "user":
        return False
    for text in _text_parts(message.get("content")):
        text = text.strip(_WHITESPACE).translate(_ASCII_LOWER)
        if any(text.startswith(start) and text.endswith(end) for start, end in pairs):
            return True
    return False
