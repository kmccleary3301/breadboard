"""Retain dynamically captured native SDK request bytes after profile admission."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
from typing import Iterator

from ...contract_runtime import ProviderRuntimeContext, ProviderRuntimeError
from ...contract_wire import canonical_json
from ...profiles import OpenAICompletionsProviderProfile

_NATIVE_CHAT_BODY: ContextVar[bytes | None] = ContextVar("native_chat_body", default=None)


class NativeRequestAuthorityError(ProviderRuntimeError):
    """Captured source body disagrees with its admitted compiled request."""


def admitted_native_request(
    context: ProviderRuntimeContext,
    profile: OpenAICompletionsProviderProfile,
    expected: dict,
) -> tuple[dict, bytes | None]:
    source_json = context.native_request_body_json
    if source_json is None:
        return expected, None
    try:
        source = json.loads(source_json)
        if not isinstance(source, dict):
            raise ValueError("native request must be an object")
        if context.extra.get("compaction_summary") is True:
            # Only the already-admitted summary tool/cap policy is overlaid.
            # Updating existing keys retains their source insertion position.
            if "tools" in expected:
                source["tools"] = expected["tools"]
            elif "tools" in source:
                del source["tools"]
            cap_field = profile.request_policy.max_token_field
            other_cap = "max_tokens" if cap_field == "max_completion_tokens" else "max_completion_tokens"
            if other_cap in source and cap_field not in source:
                source = {cap_field if key == other_cap else key: value for key, value in source.items()}
            source[cap_field] = expected[cap_field]
            source_json = json.dumps(source, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if canonical_json(source) != canonical_json(expected):
            code = (
                "native_request_members_mismatch"
                if set(source) != set(expected) else "native_request_body_mismatch"
            )
            raise NativeRequestAuthorityError(
                "native request does not match admitted profile projection",
                kind="configuration", details={"code": code},
            )
    except (TypeError, ValueError) as exc:
        raise NativeRequestAuthorityError(
            str(exc), kind="configuration", details={"code": "policy_request_invalid"},
        ) from None
    return source, source_json.encode("utf-8")


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
