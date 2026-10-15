"""Retain dynamically captured native SDK request bytes after profile admission."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import json
from typing import Any, Iterator

from ...contract_runtime import ProviderRuntimeContext, ProviderRuntimeError
from ...profiles import OpenAICompletionsProviderProfile

_NATIVE_CHAT_BODY: ContextVar[bytes | None] = ContextVar("native_chat_body", default=None)


class NativeRequestAuthorityError(ProviderRuntimeError):
    """Captured source body disagrees with its admitted compiled request."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON member: {key!r}")
        result[key] = value
    return result


def _invalid_constant(token: str) -> None:
    raise ValueError(f"Non-finite JSON number: {token}")


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare JSON numeric values exactly, without treating booleans as numbers."""
    numeric = (int, float, Decimal)
    if type(left) in numeric and type(right) in numeric:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and a == b
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right)
        )
    return left == right


def _summary_projection(source: dict, expected: dict, cap_field: str) -> dict:
    projected = dict(source)
    if "tools" in expected:
        projected["tools"] = expected["tools"]
    else:
        projected.pop("tools", None)
    other_cap = "max_tokens" if cap_field == "max_completion_tokens" else "max_completion_tokens"
    if other_cap in projected and cap_field not in projected:
        projected = {cap_field if key == other_cap else key: value for key, value in projected.items()}
    projected[cap_field] = expected[cap_field]
    return projected


def _patch_summary_body(text: str, source: dict, expected: dict, cap_field: str) -> str:
    """Patch only admitted root tool/cap spans in an already validated object."""
    decoder = json.JSONDecoder()

    def skip(pos: int) -> int:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        return pos

    # Complete JSON value spans keep untouched whitespace and lexemes intact.
    members = []
    cursor = skip(skip(0) + 1)
    while text[cursor] != "}":
        key_start = cursor
        key, key_end = decoder.raw_decode(text, cursor)
        value_start = skip(skip(key_end) + 1)
        _, value_end = decoder.raw_decode(text, value_start)
        separator = skip(value_end)
        members.append((key, key_start, key_end, value_start, value_end, separator))
        cursor = skip(separator + 1) if text[separator] == "," else separator
    close = cursor

    def encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    other_cap = "max_tokens" if cap_field == "max_completion_tokens" else "max_completion_tokens"
    renamed = other_cap if other_cap in source and cap_field not in source else None
    updates = {"tools": expected["tools"]} if "tools" in expected else {}
    updates[cap_field] = expected[cap_field]
    edits = []
    surviving = 0
    present = set()
    for index, (key, key_start, key_end, value_start, value_end, separator) in enumerate(members):
        if key == "tools" and "tools" not in expected:
            if index < len(members) - 1:
                edits.append((key_start, separator + 1, ""))
            elif index:
                edits.append((members[index - 1][5], value_end, ""))
            else:
                edits.append((key_start, value_end, ""))
            continue
        surviving += 1
        target_key = cap_field if key == renamed else key
        present.add(target_key)
        if key == renamed:
            edits.append((key_start, key_end, encode(cap_field)))
        if target_key in updates and not _same_json_value(source[key], updates[target_key]):
            edits.append((value_start, value_end, encode(updates[target_key])))
    added = [encode(key) + ":" + encode(value) for key, value in updates.items() if key not in present]
    if added:
        edits.append((close, close, ("," if surviving else "") + ",".join(added)))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text



def admitted_native_request(
    context: ProviderRuntimeContext,
    profile: OpenAICompletionsProviderProfile,
    expected: dict,
) -> tuple[dict, bytes | None]:
    source_json = context.native_request_body_json
    if source_json is None:
        return expected, None
    try:
        # Decimal retains source numeric precision for authority checking only.
        # Receipts below still use ordinary JSON int/float values.
        source = json.loads(
            source_json, parse_float=Decimal, object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if not isinstance(source, dict):
            raise ValueError("native request must be an object")
        summary = context.extra.get("compaction_summary") is True
        projected = (
            _summary_projection(source, expected, profile.request_policy.max_token_field)
            if summary else source
        )
        if not _same_json_value(projected, expected):
            code = (
                "native_request_members_mismatch"
                if set(projected) != set(expected) else "native_request_body_mismatch"
            )
            raise NativeRequestAuthorityError(
                "native request does not match admitted profile projection",
                kind="configuration", details={"code": code},
            )
        if summary:
            source_json = _patch_summary_body(
                source_json, source, expected, profile.request_policy.max_token_field,
            )
        receipt = json.loads(source_json)
    except (TypeError, ValueError) as exc:
        raise NativeRequestAuthorityError(
            str(exc), kind="configuration", details={"code": "policy_request_invalid"},
        ) from None
    return receipt, source_json.encode("utf-8")


@contextmanager
def native_chat_wire_scope(body: bytes | None) -> Iterator[None]:
    token = _NATIVE_CHAT_BODY.set(body)
    try:
        yield
    finally:
        _NATIVE_CHAT_BODY.reset(token)


def create_chat_client(client_type, **configuration):
    from openai._compat import model_copy

    class NativeChatClient(client_type):
        def _build_request(self, options, *args, **kwargs):
            body = _NATIVE_CHAT_BODY.get()
            if body is not None and str(options.url).split("?", 1)[0].rstrip("/").endswith("/chat/completions"):
                options = model_copy(options)
                options.json_data = None
                options.extra_json = None
                options.content = body
            return super()._build_request(options, *args, **kwargs)

    return NativeChatClient(**configuration)
