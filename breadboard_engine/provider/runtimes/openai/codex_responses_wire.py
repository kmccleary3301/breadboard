"""Codex 0.139 Responses struct field ordering at the SDK HTTP boundary.

The SDK constructs and encodes its own JSON object. Source ordering is applied
to copied final request options before SDK encoding, not to keyword arguments.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ...contract_runtime import ProviderRuntimeContext
from .source_responses_wire import project_source_request, source_field_order

_CODEX_WIRE_ACTIVE: ContextVar[bool] = ContextVar("codex_responses_wire", default=False)
_ROOT_FIELDS = (
    "model", "instructions", "input", "tools", "tool_choice",
    "parallel_tool_calls", "reasoning", "store", "stream", "include",
    "prompt_cache_key", "text", "client_metadata",
)
_MESSAGE_FIELDS = ("type", "role", "content")
_FUNCTION_CALL_FIELDS = ("type", "name", "arguments", "call_id")


def selected(context: ProviderRuntimeContext) -> bool:
    return (context.agent_config.get("tools") or {}).get("native_semantics") == "codex_0_139"


def _ordered(value: dict, fields: tuple[str, ...]) -> dict:
    ordered = {name: value[name] for name in fields if name in value}
    ordered.update((name, item) for name, item in value.items() if name not in ordered)
    return ordered


def project_codex_request(body: dict) -> dict:
    """Preserve values and unknown fields, changing only pinned struct order."""
    projected = _ordered(body, _ROOT_FIELDS)
    if isinstance(body.get("input"), list):
        items = []
        for item in body["input"]:
            if isinstance(item, dict) and item.get("type") == "message":
                item = _ordered(item, _MESSAGE_FIELDS)
            elif isinstance(item, dict) and item.get("type") == "function_call":
                item = _ordered(item, _FUNCTION_CALL_FIELDS)
            items.append(item)
        projected["input"] = items
    return projected


@contextmanager
def codex_wire_scope(context: ProviderRuntimeContext) -> Iterator[None]:
    token = _CODEX_WIRE_ACTIVE.set(selected(context))
    try:
        yield
    finally:
        _CODEX_WIRE_ACTIVE.reset(token)


def create_responses_client(client_type, **configuration):
    # The installed SDK serializes JSON before HTTPX build_request. Project
    # its copied request options before that serializer, keeping the SDK's own
    # merge semantics, encoding, HTTP client, auth, retries and streaming.
    from openai._base_client import _merge_mappings
    from openai._compat import model_copy

    class ResponsesWireClient(client_type):
        def _build_request(self, options, *args, **kwargs):
            field_order = source_field_order()
            if ((_CODEX_WIRE_ACTIVE.get() or field_order is not None)
                    and str(options.url).split("?", 1)[0].rstrip("/").endswith("/responses")
                    and isinstance(options.json_data, dict)):
                options = model_copy(options)
                body = options.json_data
                if options.extra_json is not None:
                    body = _merge_mappings(body, options.extra_json)
                    options.extra_json = None
                options.json_data = (
                    project_source_request(body, field_order)
                    if field_order is not None else project_codex_request(body)
                )
            return super()._build_request(options, *args, **kwargs)

    return ResponsesWireClient(**configuration)
