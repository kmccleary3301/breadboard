"""Sealed-profile conformance enforcement.

This is intentionally narrow: it computes a conformance hash over a subset of a
resolved config, addressed via JSON Pointers, and can return a sanitized diff.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


_MISSING = {"__breadboard_missing__": True}


def _decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _get_by_pointer(doc: Any, pointer: str) -> Any:
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise ValueError(f"invalid json pointer: {pointer!r}")
    current = doc
    for raw in pointer.lstrip("/").split("/"):
        token = _decode_pointer_token(raw)
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
            continue
        if isinstance(current, list):
            if not token.isdigit():
                return _MISSING
            idx = int(token)
            if idx < 0 or idx >= len(current):
                return _MISSING
            current = current[idx]
            continue
        return _MISSING
    return current


def _tokenize_dotted_path(path: str) -> List[Any]:
    tokens: List[Any] = []
    parts = path.split(".")
    for part in parts:
        cursor = part
        while cursor:
            if "[" in cursor:
                name, rest = cursor.split("[", 1)
                if name:
                    tokens.append(name)
                idx_str, _, remainder = rest.partition("]")
                if idx_str.isdigit():
                    tokens.append(int(idx_str))
                cursor = remainder.lstrip(".") if remainder.startswith(".") else remainder
            else:
                tokens.append(cursor)
                cursor = ""
    return tokens


def _set_nested(doc: Any, tokens: List[Any], value: Any) -> None:
    current = doc
    parent_stack: List[Tuple[Any, Any]] = []
    for idx, token in enumerate(tokens):
        is_last = idx == len(tokens) - 1
        if isinstance(token, str):
            if not isinstance(current, dict):
                if not parent_stack:
                    return
                parent, parent_token = parent_stack[-1]
                replacement: Dict[str, Any] = {}
                if isinstance(parent, dict):
                    parent[parent_token] = replacement
                elif isinstance(parent, list) and isinstance(parent_token, int):
                    parent[parent_token] = replacement
                current = replacement
            if is_last:
                current[token] = value
                return
            next_token = tokens[idx + 1]
            if token not in current or current[token] is None:
                current[token] = [] if isinstance(next_token, int) else {}
            parent_stack.append((current, token))
            current = current[token]
            continue
        # list index token
        if not isinstance(current, list):
            replacement_list: List[Any] = []
            if parent_stack:
                parent, parent_token = parent_stack[-1]
                if isinstance(parent, dict):
                    parent[parent_token] = replacement_list
                elif isinstance(parent, list) and isinstance(parent_token, int):
                    parent[parent_token] = replacement_list
            current = replacement_list
        while len(current) <= token:
            next_token = tokens[idx + 1] if not is_last else None
            current.append([] if isinstance(next_token, int) else {})
        if is_last:
            current[token] = value
            return
        parent_stack.append((current, token))
        current = current[token]


def apply_dotted_overrides(config: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of config with dotted-path overrides applied (best-effort)."""
    if not overrides:
        return config
    mutated = copy.deepcopy(config)
    if not isinstance(mutated, dict):
        mutated = {}
    for path, value in (overrides or {}).items():
        if not isinstance(path, str) or not path:
            continue
        _set_nested(mutated, _tokenize_dotted_path(path), value)
    return mutated


def locked_view(config: Dict[str, Any], locked_json_pointers: Iterable[str]) -> Dict[str, Any]:
    view: Dict[str, Any] = {}
    for ptr in locked_json_pointers:
        if not isinstance(ptr, str) or not ptr:
            continue
        try:
            view[ptr] = _get_by_pointer(config, ptr)
        except Exception:
            view[ptr] = _MISSING
    return view


def compute_conformance_hash(config: Dict[str, Any], locked_json_pointers: Iterable[str]) -> str:
    view = locked_view(config, locked_json_pointers)
    payload = json.dumps(view, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class ConformanceResult:
    ok: bool
    expected_hash: str
    actual_hash: str
    details: Dict[str, Any]


def check_conformance(
    *,
    config: Dict[str, Any],
    locked_json_pointers: Iterable[str],
    expected_hash: str,
) -> ConformanceResult:
    view = locked_view(config, locked_json_pointers)
    actual = compute_conformance_hash(config, locked_json_pointers)
    ok = bool(expected_hash) and (expected_hash == actual)
    # Keep diff payload conservative to avoid leaking secrets from locked pointers.
    details = {
        "locked_json_pointers": list(locked_json_pointers),
        "locked_value_kinds": {
            ptr: ("missing" if val is _MISSING else type(val).__name__)
            for ptr, val in view.items()
        },
    }
    return ConformanceResult(ok=ok, expected_hash=expected_hash, actual_hash=actual, details=details)

