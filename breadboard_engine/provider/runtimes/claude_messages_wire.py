"""Claude Code 2.1.63 Messages request ordering at the SDK HTTP boundary."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ..contract_runtime import ProviderRuntimeContext

_CLAUDE_WIRE_ACTIVE: ContextVar[bool] = ContextVar("claude_messages_wire", default=False)
_ROOT_FIELDS = (
    "model", "messages", "system", "tools", "metadata", "max_tokens",
    "thinking", "temperature", "stream",
)


def project_claude_request(body: dict) -> dict:
    """Keep all values and nested insertion order; project only source root order."""
    ordered = {name: body[name] for name in _ROOT_FIELDS if name in body}
    ordered.update((name, value) for name, value in body.items() if name not in ordered)
    return ordered


@contextmanager
def claude_wire_scope(context: ProviderRuntimeContext) -> Iterator[None]:
    selected = (context.agent_config.get("tools") or {}).get("native_semantics") == "claude_code_2_1_63"
    token = _CLAUDE_WIRE_ACTIVE.set(selected)
    try:
        yield
    finally:
        _CLAUDE_WIRE_ACTIVE.reset(token)


def create_claude_client(client_type, **configuration):
    # The SDK encodes JSON before HTTPX. Copy and project its request options
    # before encoding, preserving its own extra-body merge and transport.
    from anthropic._base_client import _merge_mappings
    from anthropic._compat import model_copy

    class ClaudeWireClient(client_type):
        def _build_request(self, options, *args, **kwargs):
            if (_CLAUDE_WIRE_ACTIVE.get() and str(options.url).split("?", 1)[0].rstrip("/").endswith("/messages")
                    and isinstance(options.json_data, dict)):
                options = model_copy(options)
                body = options.json_data
                if options.extra_json is not None:
                    body = _merge_mappings(body, options.extra_json)
                    options.extra_json = None
                options.json_data = project_claude_request(body)
            return super()._build_request(options, *args, **kwargs)

    return ClaudeWireClient(**configuration)
