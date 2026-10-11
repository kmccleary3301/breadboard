"""Byte-faithful JSON request comparison after individually guarded edits.

Only changed JSON values are rewritten. Untouched wire whitespace and member
order remain evidence and cannot pass by canonical equality.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from breadboard_engine.provider.contract_wire import canonical_json


def raw_mismatch(left: bytes, right: bytes, index: int) -> dict[str, Any] | None:
    if left == right:
        return None
    return {"index": index, "stock_sha256": hashlib.sha256(left).hexdigest(),
            "bb_sha256": hashlib.sha256(right).hexdigest(),
            "stock_size": len(left), "bb_size": len(right),
            "first_offset": next((i for i, pair in enumerate(zip(left, right))
                                  if pair[0] != pair[1]), min(len(left), len(right)))}


def project_guarded_values(raw: bytes, projected: Any) -> bytes:
    """Apply an already validated JSON projection without rewriting other bytes."""
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()

    def skip(pos: int) -> int:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def visit(pos: int, target: Any) -> tuple[str, int]:
        start = skip(pos)
        original, end = decoder.raw_decode(text, start)
        if original == target and type(original) is type(target):
            return text[start:end], end
        if isinstance(original, dict) and isinstance(target, dict):
            cursor = skip(start + 1)
            prefix = text[start + 1:cursor]
            suffix = ""
            members = []
            leading = ""
            while cursor < end - 1:
                member_start = cursor
                key, key_end = decoder.raw_decode(text, cursor)
                value_start = skip(skip(key_end) + 1)
                value, value_end = decoder.raw_decode(text, value_start)
                next_pos = skip(value_end)
                comma = next_pos < end and text[next_pos] == ","
                member_end = skip(next_pos + 1) if comma else next_pos
                if not comma:
                    suffix = text[value_end:end - 1]
                if key in target:
                    replacement, _ = visit(value_start, target[key])
                    members.append(leading + text[member_start:value_start] + replacement + (text[value_end:next_pos] if comma else ""))
                cursor = member_end
                leading = text[next_pos + 1:member_end] if comma else ""
            if set(target) - set(original):
                raise ValueError("Guarded projection must not insert JSON members")
            # Preserve the exact separator between surviving members. Deletion
            # is the only operation that may consume the removed member's bytes.
            if set(target) == set(original):
                edits = []
                cursor = skip(start + 1)
                for key in original:
                    _, key_end = decoder.raw_decode(text, cursor)
                    value_start = skip(skip(key_end) + 1)
                    replacement, value_end = visit(value_start, target[key])
                    edits.append((value_start, value_end, replacement))
                    cursor = skip(value_end)
                    if text[cursor:cursor+1] == ",":
                        cursor = skip(cursor + 1)
                result = text[start:end]
                for a, b, replacement in reversed(edits):
                    result = result[:a-start] + replacement + result[b-start:]
                return result, end
            return "{" + prefix + ",".join(members) + suffix + "}", end
        if isinstance(original, list) and isinstance(target, list) and len(original) == len(target):
            cursor = skip(start + 1)
            edits = []
            for item in target:
                value_start = cursor
                replacement, value_end = visit(cursor, item)
                edits.append((value_start, value_end, replacement))
                cursor = skip(value_end)
                if text[cursor:cursor+1] == ",":
                    cursor = skip(cursor + 1)
            result = text[start:end]
            for a, b, replacement in reversed(edits):
                result = result[:a-start] + replacement + result[b-start:]
            return result, end
        return canonical_json([target])[1:-1], end

    start = skip(0)
    result, end = visit(start, projected)
    return (text[:start] + result + text[end:]).encode("utf-8")


def raw_request_differences(stock: list[dict[str, Any]], bb: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain every unequal or missing wire body, including accepted deviations."""
    result = []
    for index in range(max(len(stock), len(bb))):
        left = bytes.fromhex(stock[index]["raw_body_bytes"]) if index < len(stock) else None
        right = bytes.fromhex(bb[index]["raw_body_bytes"]) if index < len(bb) else None
        if left is None or right is None:
            result.append({"index": index, "kind": "missing_request",
                           "stock_sha256": hashlib.sha256(left).hexdigest() if left is not None else None,
                           "bb_sha256": hashlib.sha256(right).hexdigest() if right is not None else None})
        else:
            mismatch = raw_mismatch(left, right, index)
            if mismatch is not None:
                result.append(mismatch)
    return result


def guarded_changes(original: Any, projected: Any, path: str = "$", /) -> list[dict[str, Any]]:
    """Exact JSON positions and values edited by a named source guard."""
    if type(original) is type(projected) and original == projected:
        return []
    if isinstance(original, dict) and isinstance(projected, dict):
        changes = []
        for key in sorted(original.keys() | projected.keys()):
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in projected:
                changes.append({"path": pointer, "operation": "remove", "original": original[key]})
            elif key not in original:
                changes.append({"path": pointer, "operation": "insert", "projected": projected[key]})
            else:
                changes.extend(guarded_changes(original[key], projected[key], pointer))
        return changes
    if isinstance(original, list) and isinstance(projected, list) and len(original) == len(projected):
        return [change for i, (a, b) in enumerate(zip(original, projected))
                for change in guarded_changes(a, b, path + "/" + str(i))]
    return [{"path": path, "operation": "replace", "original": original, "projected": projected}]
