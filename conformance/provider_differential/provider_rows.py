"""Hermetic provider observations and common semantic projections."""

from __future__ import annotations

import copy
import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from breadboard_engine.provider.contracts import (
    ProviderCorrelation,
    ProviderErrorTerminal,
    ProviderExchangeRecorder,
    ProviderIdentity,
    ProviderRequest,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    normalize_request_messages,
    normalize_usage,
)
from breadboard_engine.provider.runtime_codex import _codex_launch_contract
from breadboard_engine.provider.normalizer import (
    normalized_result_messages,
    normalized_result_replay,
)
from breadboard_engine.provider.registry import provider_registry
from breadboard_engine.provider.invoker import ProviderInvoker
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider_broker.catalog import get_provider_catalog_entry
from .contracts import canonical_json, validate_semantic_trace

for _runtime_module in (
    "breadboard_engine.provider.runtime_codex",
    "breadboard_engine.provider.runtimes.anthropic",
    "breadboard_engine.provider.runtimes.openai",
):
    import_module(_runtime_module)

_PROVIDERS = ("codex", "openai", "anthropic", "openrouter")
_FAMILIES = (
    "catalog_model_route",
    "request_ir",
    "text_stream",
    "tool_stream",
    "usage_finish",
    "error_terminal",
    "cancel_terminal",
)
PROVIDER_ROW_IDS = tuple(f"{p}.{f}" for p in _PROVIDERS for f in _FAMILIES)
_SCENARIO_PATH = Path(__file__).with_name("scenario.v1.json")
_SCENARIO = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
_MODELS = {provider: data["id"] for provider, data in _SCENARIO["models"].items()}


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    raise TypeError(f"non-JSON observation value: {type(value).__name__}")


def _row(
    row_id: str, provider: str, claim: str, observed: Any, *evidence: str
) -> dict[str, Any]:
    return {
        "row_id": row_id,
        "subject": provider,
        "claim": claim,
        "observed": _json(observed),
        "evidence": list(evidence),
    }


def _scenario_hash(value: Any) -> str:
    return (
        "sha256:"
        + __import__("hashlib")
        .sha256((canonical_json(value) + "\n").encode("utf-8"))
        .hexdigest()
    )


def scenario_input(row_id: str) -> dict[str, Any]:
    """Return the immutable logical scenario for a provider row."""
    provider, family = row_id.split(".", 1)
    if provider not in _PROVIDERS or family not in _FAMILIES:
        raise ValueError(f"unknown provider row: {row_id!r}")
    fake = copy.deepcopy(_SCENARIO["fake"])
    if family == "catalog_model_route":
        fake = {"kind": "catalog_identity_only"}
    elif family == "text_stream" or family == "request_ir":
        fake = {"kind": "text", **fake["text"]}
    elif family in {"tool_stream", "usage_finish"}:
        fake = {"kind": "tool", **fake["tool"], "usage": copy.deepcopy(fake["usage"])}
    elif family == "error_terminal":
        fake = {"kind": "errors", "cases": copy.deepcopy(fake["errors"])}
    elif family == "cancel_terminal":
        fake = {"kind": "cancellation", **copy.deepcopy(fake["cancel"])}
    return {
        "provider": provider,
        "family": family,
        "model": copy.deepcopy(_SCENARIO["models"][provider]),
        "request": copy.deepcopy(_SCENARIO["request"]),
        "fake": fake,
    }


class _Session:
    def __init__(self) -> None:
        self.workspace = "/bb-fixture/workspace"
        self._active_turn_index = 0
        self._metadata: dict[str, Any] = {}
        self.provider_metadata = self._metadata
        self.messages: list[dict[str, Any]] = []
        self.transcript: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any], Any]] = []

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self._metadata.get(key, default)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self._metadata[key] = value

    def add_transcript_entry(self, value: Mapping[str, Any]) -> None:
        self.transcript.append(dict(value))

    def _emit_event(
        self, event_type: str, payload: dict[str, Any], *, turn: Any = None
    ) -> None:
        self.events.append((event_type, dict(payload), turn))


class _CancellationSignal:
    def __init__(self, requested: bool = False) -> None:
        self.requested = requested

    def __call__(self) -> bool:
        return self.requested


class _ChatStream:
    def __init__(
        self,
        events: list[Any],
        final: Any,
        *,
        cancel_signal: _CancellationSignal | None = None,
        cancel_after_event: int | None = None,
    ) -> None:
        self.events, self.final = events, final
        self.cancel_signal = cancel_signal
        self.cancel_after_event = cancel_after_event

    def __enter__(self) -> "_ChatStream":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def __iter__(self):
        for index, event in enumerate(self.events):
            yield event
            if self.cancel_signal is not None and self.cancel_after_event == index:
                self.cancel_signal.requested = True

    def get_final_completion(self) -> Any:
        return self.final


class _RawCollection:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.with_raw_response = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.response


class _ChatClient:
    def __init__(self, stream: _ChatStream) -> None:
        collection = _RawCollection(stream.get_final_completion())
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                stream=lambda **_kwargs: stream, create=collection.create
            )
        )


class _AnthropicStream:
    def __init__(
        self,
        events: list[Any],
        final: Any,
        usage: dict[str, Any],
        *,
        cancel_signal: _CancellationSignal | None = None,
        cancel_after_event: int | None = None,
    ) -> None:
        self.events, self.final, self.usage = events, final, usage
        self.cancel_signal = cancel_signal
        self.cancel_after_event = cancel_after_event

    def __enter__(self) -> "_AnthropicStream":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def __iter__(self):
        for index, event in enumerate(self.events):
            yield event
            if self.cancel_signal is not None and self.cancel_after_event == index:
                self.cancel_signal.requested = True

    def get_final_message(self) -> Any:
        return self.final

    def get_final_usage(self) -> dict[str, Any]:
        return dict(self.usage)


class _AnthropicClient:
    def __init__(self, stream: _AnthropicStream) -> None:
        self.messages = SimpleNamespace(
            stream=lambda **_kwargs: stream, create=lambda **_kwargs: stream.final
        )


def _descriptor(provider: str):
    descriptor, model = provider_router.get_runtime_descriptor(_MODELS[provider])
    return descriptor, model


def _runtime_messages() -> list[dict[str, Any]]:
    raw = copy.deepcopy(_SCENARIO["request"]["messages"])
    for message in raw:
        if message.get("role") == "tool":
            message["role"] = "tool_result"
            message["content"] = [
                {
                    "type": "tool_result",
                    "call_id": message.pop("tool_call_id"),
                    "content": message["content"],
                    "is_error": False,
                }
            ]
        elif message.get("role") == "assistant":
            message["tool_calls"] = [
                {
                    "id": item["id"],
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "arguments": item["arguments_json"],
                    },
                }
                for item in message.get("tool_calls", [])
            ]
    return normalize_request_messages(raw)


def _context(
    recorder: ProviderExchangeRecorder | None = None,
    *,
    session: _Session | None = None,
    cancel_requested: _CancellationSignal | None = None,
) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        session_state=session or _Session(),
        agent_config={},
        stream=True,
        session_id="session-fixture",
        input_id="input-fixture",
        turn_id="turn-fixture",
        exchange_recorder=recorder,
        cancel_requested=cancel_requested,
    )


_TOOL_SCHEMA = copy.deepcopy(_SCENARIO["request"]["tools"])
_TOOL_FAKE = copy.deepcopy(_SCENARIO["fake"]["tool"])
_REQUEST_MESSAGES = _runtime_messages()


def _recorder(provider: str) -> ProviderExchangeRecorder:
    descriptor, model = _descriptor(provider)
    return ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session-fixture",
            input_id="input-fixture",
            turn_id="turn-fixture",
        ),
        provider=ProviderIdentity(
            provider_id=descriptor.provider_id,
            runtime_id=descriptor.runtime_id,
            route_id=_MODELS[provider],
            model=model,
        ),
        request=ProviderRequest(
            stream=True, messages=_REQUEST_MESSAGES, tools=_TOOL_SCHEMA
        ),
    )


def _chat_chunk(
    *,
    content: Any = None,
    reasoning: Any = None,
    tool_arguments: str | None = None,
) -> Any:
    tool_calls = None
    if tool_arguments is not None:
        tool_calls = [
            SimpleNamespace(
                index=0,
                id=_TOOL_FAKE["call_id"],
                function=SimpleNamespace(
                    name=_TOOL_FAKE["name"],
                    arguments=tool_arguments,
                ),
            )
        ]
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        reasoning=None,
        reasoning_details=None,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        type="chunk",
        chunk=SimpleNamespace(
            id="chat-fixture",
            choices=[SimpleNamespace(index=0, delta=delta)],
        ),
    )


def _chat_response(*, tool: bool = False, usage: dict[str, Any] | None = None) -> Any:
    tool_calls = []
    if tool:
        tool_calls = [
            SimpleNamespace(
                id=_TOOL_FAKE["call_id"],
                type="function",
                function=SimpleNamespace(
                    name=_TOOL_FAKE["name"],
                    arguments=_TOOL_FAKE["arguments_json"],
                ),
            )
        ]
    message = SimpleNamespace(
        role="assistant",
        content=None if tool else _SCENARIO["fake"]["text"]["delta"],
        tool_calls=tool_calls,
        reasoning_content=_TOOL_FAKE["reasoning_delta"] if tool else None,
        reasoning=None,
        reasoning_details=None,
    )
    return SimpleNamespace(
        id="chat-fixture",
        choices=[
            SimpleNamespace(
                index=0,
                message=message,
                finish_reason="tool_calls" if tool else "stop",
            )
        ],
        usage=usage,
        model="fixture-model",
    )


def _anthropic_events(
    *, tools: bool = False, usage: bool = False
) -> tuple[list[Any], Any, dict[str, Any]]:
    raw_usage = (
        {
            "input_tokens": 9,
            "output_tokens": 18,
            "cache_read_input_tokens": 3,
        }
        if usage
        else {"input_tokens": 0, "output_tokens": 0}
    )
    events: list[Any] = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                id="message-fixture",
                usage={"input_tokens": raw_usage["input_tokens"]},
            ),
        )
    ]
    if tools:
        content = [
            {"type": "thinking", "thinking": _TOOL_FAKE["reasoning_delta"]},
            {
                "type": "tool_use",
                "id": _TOOL_FAKE["call_id"],
                "name": _TOOL_FAKE["name"],
                "input": copy.deepcopy(_TOOL_FAKE["arguments"]),
            },
        ]
        events.extend(
            [
                SimpleNamespace(
                    type="content_block_start",
                    index=0,
                    content_block={"type": "thinking", "thinking": ""},
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=0,
                    delta={
                        "type": "thinking_delta",
                        "thinking": _TOOL_FAKE["reasoning_delta"],
                    },
                ),
                SimpleNamespace(type="content_block_stop", index=0),
                SimpleNamespace(
                    type="content_block_start",
                    index=1,
                    content_block={
                        "type": "tool_use",
                        "id": _TOOL_FAKE["call_id"],
                        "name": _TOOL_FAKE["name"],
                        "input": {},
                    },
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=1,
                    delta={
                        "type": "input_json_delta",
                        "partial_json": _TOOL_FAKE["arguments_json"],
                    },
                ),
                SimpleNamespace(type="content_block_stop", index=1),
            ]
        )
        stop = "tool_use"
    else:
        text = _SCENARIO["fake"]["text"]["delta"]
        content = [{"type": "text", "text": text}]
        events.extend(
            [
                SimpleNamespace(
                    type="content_block_start",
                    index=0,
                    content_block={"type": "text", "text": ""},
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=0,
                    delta={"type": "text_delta", "text": text},
                ),
                SimpleNamespace(type="content_block_stop", index=0),
            ]
        )
        stop = "end_turn"
    events.extend(
        [
            SimpleNamespace(
                type="message_delta",
                delta={"stop_reason": stop},
                usage=raw_usage,
            ),
            SimpleNamespace(type="message_stop"),
        ]
    )
    final = SimpleNamespace(
        id="message-fixture",
        content=content,
        stop_reason=stop,
        model="fixture-model",
        usage=raw_usage,
    )
    return events, final, raw_usage


def _codex_client(
    *,
    tools: bool = False,
    usage: bool = False,
    text: str | None = None,
    cancel_signal: _CancellationSignal | None = None,
    cancel_before_notification: int | None = None,
):
    thread_id, turn_id = "thread-fixture", "turn-fixture"
    message_text = text if text is not None else _SCENARIO["fake"]["text"]["delta"]
    notifications: list[dict[str, Any]] = [
        {
            "method": "turn/started",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "turn": {
                    "id": turn_id,
                    "status": "inProgress",
                    "items": [],
                },
            },
        }
    ]
    if tools:
        reasoning = {
            "id": "reasoning-fixture",
            "type": "reasoning",
            "content": [_TOOL_FAKE["reasoning_delta"]],
            "summary": [],
        }
        command = {
            "id": _TOOL_FAKE["call_id"],
            "type": "commandExecution",
            "command": _TOOL_FAKE["arguments"]["command"],
            "commandActions": _TOOL_FAKE["arguments"]["command_actions"],
            "cwd": _TOOL_FAKE["arguments"]["cwd"],
            "status": "completed",
            "aggregatedOutput": "ok",
            "source": _TOOL_FAKE["arguments"]["source"],
            "exitCode": 0,
        }
        items = [reasoning, command]
        notifications.extend(
            [
                {
                    "method": "item/started",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "startedAtMs": 0,
                        "item": reasoning,
                    },
                },
                {
                    "method": "item/reasoning/textDelta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": reasoning["id"],
                        "contentIndex": 0,
                        "delta": _TOOL_FAKE["reasoning_delta"],
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "completedAtMs": 0,
                        "item": reasoning,
                    },
                },
                {
                    "method": "item/started",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "startedAtMs": 0,
                        "item": command,
                    },
                },
                {
                    "method": "item/commandExecution/outputDelta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": command["id"],
                        "delta": "ok",
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "completedAtMs": 0,
                        "item": command,
                    },
                },
            ]
        )
    else:
        item = {
            "id": "message-fixture",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": message_text,
        }
        items = [item]
        notifications.extend(
            [
                {
                    "method": "item/started",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "startedAtMs": 0,
                        "item": {**item, "text": ""},
                    },
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": item["id"],
                        "delta": item["text"],
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "completedAtMs": 0,
                        "item": item,
                    },
                },
            ]
        )
    if usage:
        breakdown = {
            "cachedInputTokens": 3,
            "inputTokens": 9,
            "outputTokens": 18,
            "reasoningOutputTokens": 4,
            "totalTokens": 30,
        }
        notifications.append(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "tokenUsage": {
                        "last": breakdown,
                        "total": breakdown,
                        "modelContextWindow": 32000,
                    },
                },
            }
        )
    notifications.append(
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "status": "completed",
                    "items": items,
                    "itemsView": "full",
                },
            },
        }
    )

    class Client:
        def __init__(self) -> None:
            self.returned = 0

        def turn_start(
            self, _thread: str, _input: Any, *, overrides: Any = None
        ) -> dict[str, Any]:
            return {"turn": {"id": turn_id}}

        def next_notification(self, timeout_s: Any = None) -> dict[str, Any] | None:
            del timeout_s
            if (
                cancel_signal is not None
                and cancel_before_notification == self.returned
            ):
                cancel_signal.requested = True
            notification = notifications.pop(0) if notifications else None
            self.returned += 1
            return notification

    return Client()


def _bind_codex_fixture_client(runtime: Any, model: str, client: Any) -> dict[str, str]:
    fixture_client = {"api_key": "fixture-key"}
    _environment, _credentials, _roots, auth_identity = _codex_launch_contract(
        fixture_client
    )
    runtime._client = client
    runtime._thread_id = "thread-fixture"
    runtime._session_model = model
    runtime._session_cwd = "/bb-fixture/workspace"
    runtime._leased_client_key = (
        "fixture-codex",
        runtime._session_cwd,
        model,
        auth_identity,
    )
    return fixture_client


def _run_stream(
    provider: str, *, tools: bool = False, usage: bool = False
) -> tuple[ProviderResult, ProviderExchangeRecorder]:
    descriptor, model = _descriptor(provider)
    recorder = _recorder(provider)
    context = _context(recorder)
    if provider == "codex":
        runtime = provider_registry.create_runtime(descriptor)
        fixture_client = _bind_codex_fixture_client(
            runtime,
            model,
            _codex_client(tools=tools, usage=usage),
        )
        result = runtime.invoke(
            client=fixture_client,
            model=model,
            messages=_REQUEST_MESSAGES,
            tools=_TOOL_SCHEMA,
            stream=True,
            context=context,
        )
        return result, recorder
    if provider == "anthropic":
        events, final, raw_usage = _anthropic_events(tools=tools, usage=usage)
        runtime = provider_registry.create_runtime(descriptor)
        result = runtime.invoke(
            client=_AnthropicClient(_AnthropicStream(events, final, raw_usage)),
            model=model,
            messages=_REQUEST_MESSAGES,
            tools=_TOOL_SCHEMA,
            stream=True,
            context=context,
        )
        return result, recorder
    chunks = (
        [
            _chat_chunk(reasoning=_TOOL_FAKE["reasoning_delta"]),
            _chat_chunk(tool_arguments=_TOOL_FAKE["arguments_json"]),
        ]
        if tools
        else [_chat_chunk(content=_SCENARIO["fake"]["text"]["delta"])]
    )
    raw_usage = None
    if usage:
        raw_usage = {
            "prompt_tokens": 9,
            "completion_tokens": 18,
            "total_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 4},
        }
    runtime = provider_registry.create_runtime(descriptor)
    result = runtime.invoke(
        client=_ChatClient(
            _ChatStream(chunks, _chat_response(tool=tools, usage=raw_usage))
        ),
        model=model,
        messages=_REQUEST_MESSAGES,
        tools=_TOOL_SCHEMA,
        stream=True,
        context=context,
    )
    return result, recorder


def _runtime_failure_probe(provider: str, case: Mapping[str, Any]) -> dict[str, Any]:
    descriptor, model = _descriptor(provider)
    runtime = provider_registry.create_runtime(descriptor)
    recorder = _recorder(provider)
    context = _context(recorder)
    malformed = case["label"] == "malformed"
    status = case.get("http_status")

    def fail(**_kwargs: Any) -> Any:
        raise ProviderRuntimeError(
            f"synthetic {case['label']} provider rejection",
            kind="provider",
            details={
                "code": case["error_code"],
                "status_code": status,
                "classification": case["label"],
            },
            output_emitted=False,
        )

    if provider == "codex":

        class CodexFailureClient:
            def turn_start(
                self,
                _thread: str,
                _input: Any,
                *,
                overrides: Any = None,
            ) -> dict[str, Any]:
                del overrides
                if not malformed:
                    return fail()
                return {"turn": {"id": "turn-fixture"}}

            def next_notification(self, timeout_s: Any = None) -> dict[str, Any]:
                del timeout_s
                return {
                    "method": "fixture/malformed",
                    "params": {
                        "threadId": "thread-fixture",
                        "turnId": "turn-fixture",
                    },
                }

        client = _bind_codex_fixture_client(
            runtime,
            model,
            CodexFailureClient(),
        )
    elif provider == "anthropic":
        if malformed:
            malformed_stream = _AnthropicStream(
                [SimpleNamespace(type="fixture_malformed")],
                SimpleNamespace(
                    id="message-fixture",
                    content=[],
                    stop_reason="end_turn",
                    model="fixture-model",
                    usage={},
                ),
                {},
            )
            client = _AnthropicClient(malformed_stream)
        else:
            client = SimpleNamespace(messages=SimpleNamespace(stream=fail))
    elif malformed:
        client = _ChatClient(
            _ChatStream(
                [SimpleNamespace(type="fixture_malformed")],
                _chat_response(),
            )
        )
    else:
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(stream=fail))
        )
    try:
        runtime.invoke(
            client=client,
            model=model,
            messages=_REQUEST_MESSAGES,
            tools=_TOOL_SCHEMA,
            stream=True,
            context=context,
        )
    except ProviderRuntimeError as exc:
        if exc.output_emitted:
            raise AssertionError(
                f"{provider}.{case['label']} emitted output before failure"
            ) from exc
        if malformed and exc.kind != "protocol":
            raise AssertionError(
                f"{provider} malformed fixture was not a protocol failure"
            ) from exc
        return {
            "kind": exc.kind,
            "safe_code": exc.safe_code,
            "output_emitted": exc.output_emitted,
            "replay_safe": exc.replay_safe,
            "runtime_events": _event_view(recorder),
        }
    raise AssertionError(
        f"{provider}.{case['label']} did not produce a runtime failure"
    )


def _event_view(recorder: ProviderExchangeRecorder) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in recorder.events:
        item = {"kind": event.kind}
        for attr in (
            "content_index",
            "message_id",
            "call_id",
            "name",
            "delta",
            "arguments_json",
            "arguments",
        ):
            value = getattr(event, attr, None)
            if value is not None:
                item[attr] = value
        result.append(item)
    return result


def _trace_request() -> dict[str, Any]:
    return copy.deepcopy(_SCENARIO["request"])


def _usage_projection(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    normalized = normalize_usage(dict(value))

    def get(*keys: str) -> Any:
        for key in keys:
            if key in normalized:
                return normalized[key]
        return None

    extensions = normalized.get("extensions")
    extension_map = extensions if isinstance(extensions, Mapping) else {}
    prompt_details = extension_map.get("prompt_tokens_details")
    completion_details = extension_map.get("completion_tokens_details")
    prompt_map = prompt_details if isinstance(prompt_details, Mapping) else {}
    completion_map = (
        completion_details if isinstance(completion_details, Mapping) else {}
    )
    input_tokens = get("inputTokens", "input_tokens", "prompt_tokens")
    output_tokens = get("outputTokens", "output_tokens", "completion_tokens")
    total_tokens = get("totalTokens", "total_tokens")
    if (
        total_tokens is None
        and isinstance(input_tokens, int)
        and isinstance(output_tokens, int)
    ):
        total_tokens = input_tokens + output_tokens
    cached = get("cacheReadTokens", "cached_input_tokens", "cache_read_tokens")
    if cached is None:
        cached = prompt_map.get("cached_tokens")
    reasoning = get("reasoningTokens", "reasoning_tokens")
    if reasoning is None:
        reasoning = completion_map.get("reasoning_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning,
        "cached_input_tokens": cached,
        "cache_read_tokens": cached,
        "cache_write_tokens": get("cacheWriteTokens", "cache_write_tokens"),
    }


def _result_projection(result: ProviderResult) -> dict[str, Any]:
    messages = normalized_result_messages(result)
    text: list[str] = []
    reasoning: list[str] = []
    calls: list[dict[str, Any]] = []
    for message in messages:
        for block in message.get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                text.append(str(block.get("text") or ""))
            elif block_type == "thinking":
                reasoning.append(str(block.get("text") or ""))
            elif block_type == "tool_call":
                calls.append(
                    {
                        "call_id": block["call_id"],
                        "name": block["name"],
                        "arguments_json": block["arguments_json"],
                        "arguments": block["arguments"],
                    }
                )
    return {
        "assembled_text": "".join(text),
        "assembled_reasoning": "".join(reasoning),
        "tool_calls": calls,
    }


def _canonical_events(
    raw_events: list[dict[str, Any]], result: Mapping[str, Any]
) -> list[dict[str, Any]]:
    kinds = [event["kind"] for event in raw_events]
    if not kinds or kinds[0] != "response_start":
        raise ValueError("provider stream lacks response_start")
    events: list[dict[str, Any]] = [{"sequence": 0, "kind": "response_start"}]
    text = str(result["assembled_text"])
    reasoning = str(result["assembled_reasoning"])
    calls = list(result["tool_calls"])
    if text:
        raw_text = "".join(
            str(event.get("delta") or "")
            for event in raw_events
            if event["kind"] == "text_delta"
        )
        if raw_text != text:
            raise ValueError("provider text deltas disagree with result")
        events.extend(
            [
                {"sequence": 1, "kind": "text_start", "content_index": 0},
                {
                    "sequence": 2,
                    "kind": "text_delta",
                    "content_index": 0,
                    "delta": text,
                },
                {"sequence": 3, "kind": "text_end", "content_index": 0},
            ]
        )
    if reasoning:
        raw_reasoning = "".join(
            str(event.get("delta") or "")
            for event in raw_events
            if event["kind"] == "thinking_delta"
        )
        if raw_reasoning != reasoning:
            raise ValueError("provider reasoning deltas disagree with result")
        events.extend(
            [
                {
                    "sequence": len(events),
                    "kind": "thinking_start",
                    "content_index": 0,
                },
                {
                    "sequence": len(events) + 1,
                    "kind": "thinking_delta",
                    "content_index": 0,
                    "delta": reasoning,
                },
                {
                    "sequence": len(events) + 2,
                    "kind": "thinking_end",
                    "content_index": 0,
                },
            ]
        )
    for call in calls:
        raw_call = [
            event for event in raw_events if event.get("call_id") == call["call_id"]
        ]
        if not any(event["kind"] == "tool_call_start" for event in raw_call):
            raise ValueError("provider tool call lacks start event")
        if not any(event["kind"] == "tool_call_end" for event in raw_call):
            raise ValueError("provider tool call lacks end event")
        content_index = 0
        events.extend(
            [
                {
                    "sequence": len(events),
                    "kind": "tool_call_start",
                    "content_index": content_index,
                    "call_id": call["call_id"],
                    "name": call["name"],
                },
                {
                    "sequence": len(events) + 1,
                    "kind": "tool_call_delta",
                    "content_index": content_index,
                    "call_id": call["call_id"],
                    "delta": call["arguments_json"],
                },
                {
                    "sequence": len(events) + 2,
                    "kind": "tool_call_end",
                    "content_index": content_index,
                    **call,
                },
            ]
        )
    return events


def _stream_trace(
    provider: str,
    family: str,
    result: ProviderResult,
    recorder: ProviderExchangeRecorder,
) -> dict[str, Any]:
    result_projection = _result_projection(result)
    raw_events = _event_view(recorder)
    finish = result.messages[0].finish_reason if result.messages else "stop"
    trace = {
        "schema_version": "bb.provider_semantic_trace.v1",
        "provider": provider,
        "model": _MODELS[provider],
        "request": _trace_request(),
        "events": _canonical_events(raw_events, result_projection),
        "result": result_projection,
        "usage": (
            _usage_projection(result.usage) if family == "usage_finish" else None
        ),
        "terminal": {
            "state": "done",
            "finish_reason": (
                "toolUse" if finish in {"tool_calls", "tool_use", "toolUse"} else "stop"
            ),
            "output_emitted": True,
        },
    }
    validate_semantic_trace(trace)
    return trace


def _observe_catalog(
    row_id: str, provider: str, scenario: Mapping[str, Any]
) -> dict[str, Any]:
    descriptor, _model = _descriptor(provider)
    parsed_provider, parsed_model, path = provider_router.parse_model_id(
        _MODELS[provider]
    )
    catalog = get_provider_catalog_entry(provider)
    if catalog is None:
        raise AssertionError(f"missing product catalog entry for {provider}")
    canonical = {"provider_id": parsed_provider, "model_id": parsed_model}
    return _row(
        row_id,
        provider,
        "catalog identity and model route resolve to one stable runtime",
        {
            "canonical_projection": canonical,
            "provider_id": parsed_provider,
            "aliases": list(catalog.aliases),
            "auth_schemes": list(catalog.auth_schemes),
            "auth_owner": catalog.auth_owner,
            "support_tier": catalog.support_tier,
            "runtime_id": descriptor.runtime_id,
            "catalog_runtime_id": catalog.runtime_id,
            "model": parsed_model,
            "route": path,
            "supports_streaming": descriptor.supports_streaming,
            "supports_reasoning_traces": descriptor.supports_reasoning_traces,
            "scenario_input": scenario,
            "scenario_input_sha256": _scenario_hash(scenario),
        },
        f"catalog:{catalog.provider_id}",
        f"route:{_MODELS[provider]}",
    )


def _visible_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "")
            for item in value
            if isinstance(item, Mapping) and item.get("type") == "text"
        )
    return ""


def _visible_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(tool.get("function"), Mapping):
        function = tool["function"]
        return {
            "name": function["name"],
            "description": function.get("description", ""),
            "parameters": function["parameters"],
        }
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool["input_schema"],
    }


def _visible_request_projection(
    provider: str, visible: Mapping[str, Any]
) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    if provider == "anthropic":
        result.append({"role": "system", "content": _visible_text(visible["system"])})
        messages = visible["messages"]
        for message in messages:
            content = message["content"]
            first = content[0] if content else {}
            if first.get("type") == "tool_result":
                result.append(
                    {
                        "role": "tool",
                        "content": first["content"],
                        "tool_call_id": first["tool_use_id"],
                        "name": None,
                    }
                )
            elif message["role"] == "assistant":
                call = first
                arguments = call["input"]
                result.append(
                    {
                        "role": "assistant",
                        "content": [],
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "name": call["name"],
                                "arguments_json": canonical_json(arguments),
                                "arguments": arguments,
                            }
                        ],
                    }
                )
            else:
                result.append(
                    {
                        "role": "user",
                        "content": _visible_text(content),
                    }
                )
    elif provider == "codex":
        for message in visible["messages"]:
            role = message["role"]
            if role in {"system", "user"}:
                result.append(
                    {"role": role, "content": _visible_text(message["content"])}
                )
            elif role == "assistant":
                calls = []
                for block in message["content"]:
                    if block["type"] != "tool_call":
                        continue
                    calls.append(
                        {
                            "id": block["call_id"],
                            "name": block["name"],
                            "arguments_json": block["arguments_json"],
                            "arguments": block["arguments"],
                        }
                    )
                result.append(
                    {
                        "role": "assistant",
                        "content": [],
                        "tool_calls": calls,
                    }
                )
            elif role == "tool_result":
                block = message["content"][0]
                result.append(
                    {
                        "role": "tool",
                        "content": block["content"],
                        "tool_call_id": block["call_id"],
                        "name": None,
                    }
                )
    else:
        for message in visible["messages"]:
            role = message["role"]
            if role in {"system", "user"}:
                result.append(
                    {"role": role, "content": _visible_text(message["content"])}
                )
            elif role == "assistant":
                calls = []
                for call in message.get("tool_calls", []):
                    function = call["function"]
                    arguments_json = function["arguments"]
                    calls.append(
                        {
                            "id": call["id"],
                            "name": function["name"],
                            "arguments_json": arguments_json,
                            "arguments": json.loads(arguments_json),
                        }
                    )
                result.append(
                    {
                        "role": "assistant",
                        "content": [],
                        "tool_calls": calls,
                    }
                )
            elif role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "content": message["content"],
                        "tool_call_id": message["tool_call_id"],
                        "name": None,
                    }
                )
    return {
        "messages": result,
        "tools": [_visible_tool(tool) for tool in visible["tools"]],
    }


def _model_visible_request(provider: str) -> dict[str, Any]:
    descriptor, model = _descriptor(provider)
    runtime = provider_registry.create_runtime(descriptor)
    context = _context()
    translated = [
        provider_router.get_tool_translator(_MODELS[provider]).translate_tool_schema(
            _TOOL_SCHEMA[0]
        )
    ]
    if provider == "anthropic":
        system, messages = runtime._convert_messages(_REQUEST_MESSAGES, context=context)
        visible = {"system": system, "messages": messages, "tools": translated}
    elif descriptor.runtime_id == "openai_responses":
        instructions, messages = runtime._split_messages_for_responses(
            _REQUEST_MESSAGES, context
        )
        visible = {
            "instructions": instructions,
            "input": runtime._convert_messages_to_input(
                messages, include_tool_calls=True, context=context
            ),
            "tools": runtime._convert_tools_to_responses(translated),
        }
    elif provider == "codex":
        visible = {"messages": _REQUEST_MESSAGES, "tools": translated}
    else:
        visible = {
            "messages": runtime._convert_messages_to_chat(
                _REQUEST_MESSAGES, context=context
            ),
            "tools": runtime._convert_tools_to_openai(translated),
        }
    return {
        "route": {
            "provider_id": descriptor.provider_id,
            "runtime_id": descriptor.runtime_id,
            "model": model,
        },
        "wire": visible,
        "logical_request": _visible_request_projection(provider, visible),
    }


def _observe_stream(
    row_id: str,
    provider: str,
    family: str,
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    tools = family in {"tool_stream", "usage_finish"}
    result, recorder = _run_stream(
        provider,
        tools=tools,
        usage=family == "usage_finish",
    )
    observed: dict[str, Any] = {
        "scenario_input": scenario,
        "scenario_input_sha256": _scenario_hash(scenario),
        "semantic_trace": _stream_trace(provider, family, result, recorder),
        "raw_runtime_events": _event_view(recorder),
        "raw_result_messages": normalized_result_messages(result),
        "model_visible_request": _model_visible_request(provider),
    }
    if tools:
        observed["tool_result_replay"] = {
            "call_id": "call-fixture",
            "content": '{"ok":true}',
            "is_error": False,
        }
        observed["replay"] = normalized_result_replay(result, provider_id=provider)
    return _row(
        row_id,
        provider,
        "provider execution projects to the exact common semantic trace",
        observed,
        f"fixture:{provider}.{family}",
        "raw:provider-exchange",
    )


def _empty_result() -> dict[str, Any]:
    return {
        "assembled_text": "",
        "assembled_reasoning": "",
        "tool_calls": [],
    }


def _observe_errors(
    row_id: str, provider: str, scenario: Mapping[str, Any]
) -> dict[str, Any]:
    traces: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    for case in scenario["fake"]["cases"]:
        probes.append(
            {
                "label": case["label"],
                "runtime_failure": _runtime_failure_probe(provider, case),
            }
        )
        details = {
            "code": case["error_code"],
            "status_code": case["http_status"],
            "classification": case["label"],
        }
        error = ProviderRuntimeError(
            "synthetic provider rejection",
            kind="protocol" if case["label"] == "malformed" else "provider",
            details=details,
            output_emitted=False,
        )
        terminal = ProviderErrorTerminal(
            output_emitted=False,
            code=error.safe_code,
            category=error.kind,
            retryable=bool(case["retryable"]),
            http_status=case["http_status"],
        ).as_dict()
        trace = {
            "schema_version": "bb.provider_semantic_trace.v1",
            "provider": provider,
            "model": _MODELS[provider],
            "request": _trace_request(),
            "events": [{"sequence": 0, "kind": "response_start"}],
            "result": _empty_result(),
            "usage": None,
            "terminal": {
                "state": "error",
                "error_code": terminal["code"],
                "error_message": case["error_message"],
                "retryable": terminal["retryable"],
                "http_status": terminal.get("http_status"),
                "finish_reason": "error",
                "output_emitted": terminal["output_emitted"],
            },
        }
        validate_semantic_trace(trace)
        traces.append({"label": case["label"], "trace": trace})
    return _row(
        row_id,
        provider,
        "auth, rate-limit, and malformed failures are exact terminal traces",
        {
            "scenario_input": scenario,
            "scenario_input_sha256": _scenario_hash(scenario),
            "semantic_traces": traces,
            "raw_provider_failures": probes,
            "secrets_or_transport_headers": False,
            "replay_after_output": False,
        },
        f"fixture:{provider}.error_terminal",
        "raw:401,429,malformed-truncated",
    )


class _CancelMetrics:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_call(self, _model: str, **values: Any) -> None:
        self.calls.append(dict(values))


class _CancelRouteHealth:
    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []

    def is_circuit_open(self, _model: str) -> bool:
        return False

    def record_failure(self, model: str, reason: str) -> None:
        self.failures.append((model, reason))

    def record_success(self, _model: str) -> None:
        raise AssertionError("cancelled invocation recorded route success")


def _anthropic_cancel_stream(
    signal: _CancellationSignal, *, after_partial: bool
) -> _AnthropicStream:
    text = str(_SCENARIO["fake"]["cancel"]["after_partial"]["delta"])
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(id="message-fixture", usage={"input_tokens": 0}),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block={"type": "text", "text": ""},
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta={"type": "text_delta", "text": text},
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta={"stop_reason": "end_turn"},
            usage={"input_tokens": 0, "output_tokens": 0},
        ),
        SimpleNamespace(type="message_stop"),
    ]
    final = SimpleNamespace(
        id="message-fixture",
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        model="fixture-model",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    return _AnthropicStream(
        events,
        final,
        {"input_tokens": 0, "output_tokens": 0},
        cancel_signal=signal,
        cancel_after_event=2 if after_partial else 0,
    )


def _cancel_client(
    provider: str,
    runtime: Any,
    model: str,
    signal: _CancellationSignal,
    *,
    after_partial: bool,
) -> Any:
    text = str(_SCENARIO["fake"]["cancel"]["after_partial"]["delta"])
    if provider == "codex":
        return _bind_codex_fixture_client(
            runtime,
            model,
            _codex_client(
                text=text,
                cancel_signal=signal,
                cancel_before_notification=3 if after_partial else 1,
            ),
        )
    if provider == "anthropic":
        return _AnthropicClient(
            _anthropic_cancel_stream(signal, after_partial=after_partial)
        )
    chunks = (
        [_chat_chunk(content=text), _chat_chunk()]
        if after_partial
        else [_chat_chunk(), _chat_chunk(content=text)]
    )
    return _ChatClient(
        _ChatStream(
            chunks,
            _chat_response(),
            cancel_signal=signal,
            cancel_after_event=0,
        )
    )


def _semantic_cancel_events(
    raw_events: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    allowed = {
        "sequence",
        "kind",
        "content_index",
        "call_id",
        "name",
        "delta",
        "arguments_json",
        "arguments",
    }
    return [
        {key: value for key, value in event.items() if key in allowed}
        for event in raw_events
    ]


def _cancel_trace(
    provider: str, *, after_partial: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    descriptor, model = _descriptor(provider)
    runtime = provider_registry.create_runtime(descriptor)
    signal = _CancellationSignal()
    client = _cancel_client(
        provider, runtime, model, signal, after_partial=after_partial
    )
    session = _Session()
    context = _context(session=session, cancel_requested=signal)
    metrics = _CancelMetrics()
    route_health = _CancelRouteHealth()
    fallback_calls: list[bool] = []

    def retry_with_fallback(*_args: Any, **_kwargs: Any) -> None:
        fallback_calls.append(True)
        return None

    invoker = ProviderInvoker(
        provider_metrics=metrics,
        route_health=route_health,
        logger_v2=SimpleNamespace(run_dir=None),
        md_writer=SimpleNamespace(system=lambda value: value),
        retry_with_fallback=retry_with_fallback,
        update_health_metadata=lambda _state: None,
        set_last_latency=lambda _value: None,
        set_html_detected=lambda _value: None,
    )
    try:
        invoker.invoke(
            runtime=runtime,
            client=client,
            model=model,
            send_messages=_REQUEST_MESSAGES,
            tools_schema=_TOOL_SCHEMA,
            stream_responses=True,
            runtime_context=context,
            session_state=session,
            markdown_logger=SimpleNamespace(log_system_message=lambda _value: None),
            turn_index=0,
            route_id=_MODELS[provider],
        )
    except ProviderRuntimeError as error:
        details = error.details if isinstance(error.details, Mapping) else {}
        if (
            details.get("cancelled") is not True
            or details.get("cancel_owner") != "caller"
            or error.safe_code != "caller_cancelled"
        ):
            raise AssertionError(
                f"{provider} cancellation was not classified by the runtime"
            ) from error
    else:
        raise AssertionError(f"{provider} cancellation did not terminate invocation")

    history = session.get_provider_metadata("provider_exchange_history", [])
    if not isinstance(history, list) or len(history) != 1:
        raise AssertionError(f"{provider} cancellation did not persist one terminal")
    exchange = history[0]
    terminal = exchange["terminal"]
    raw_events = list(exchange["events"])
    semantic_events = _semantic_cancel_events(raw_events)
    text = "".join(
        str(event.get("delta") or "")
        for event in semantic_events
        if event.get("kind") == "text_delta"
    )
    trace = {
        "schema_version": "bb.provider_semantic_trace.v1",
        "provider": provider,
        "model": _MODELS[provider],
        "request": _trace_request(),
        "events": semantic_events,
        "result": {
            "assembled_text": text,
            "assembled_reasoning": "",
            "tool_calls": [],
        },
        "usage": None,
        "terminal": {
            "state": "cancelled",
            "reason": terminal["reason_code"],
            "finish_reason": "aborted",
            "output_emitted": terminal["output_emitted"],
        },
    }
    validate_semantic_trace(trace)
    runtime_invocations = len(metrics.calls)
    retry_attempts = max(0, runtime_invocations - 1) + len(fallback_calls)
    probe = {
        "raw_runtime_events": raw_events,
        "runtime_invocations": runtime_invocations,
        "retry_attempts": retry_attempts,
        "fallback_attempts": len(fallback_calls),
        "route_failure_count": len(route_health.failures),
        "terminal_count": len(history),
        "cancel_signal_observed": signal.requested,
    }
    if (
        runtime_invocations != 1
        or retry_attempts != 0
        or route_health.failures
        or not signal.requested
    ):
        raise AssertionError(
            f"{provider} cancellation retried or poisoned route health"
        )
    return trace, probe


def _observe_cancel(
    row_id: str, provider: str, scenario: Mapping[str, Any]
) -> dict[str, Any]:
    before, before_probe = _cancel_trace(provider, after_partial=False)
    after, after_probe = _cancel_trace(provider, after_partial=True)
    return _row(
        row_id,
        provider,
        "before-output and after-partial cancellation exercise one runtime terminal without retry",
        {
            "scenario_input": scenario,
            "scenario_input_sha256": _scenario_hash(scenario),
            "before_output": {
                "semantic_trace": before,
                **before_probe,
            },
            "after_partial": {
                "semantic_trace": after,
                **after_probe,
            },
            "terminal_count": 1,
            "unsafe_replay": False,
        },
        f"fixture:{provider}.cancel_terminal",
        "raw:runtime-cancel-before-output,runtime-cancel-after-partial,no-retry",
    )


def _observe(
    row_id: str, input_value: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    provider, family = row_id.split(".", 1)
    scenario = scenario_input(row_id)
    if input_value is not None and canonical_json(input_value) != canonical_json(
        scenario
    ):
        raise ValueError(f"scenario input mismatch for {row_id}")
    if family == "catalog_model_route":
        return _observe_catalog(row_id, provider, scenario)
    if family in {
        "request_ir",
        "text_stream",
        "tool_stream",
        "usage_finish",
    }:
        return _observe_stream(row_id, provider, family, scenario)
    if family == "error_terminal":
        return _observe_errors(row_id, provider, scenario)
    if family == "cancel_terminal":
        return _observe_cancel(row_id, provider, scenario)
    raise ValueError(f"unknown provider row: {row_id!r}")


def observe_provider_row(
    row_id: str, input_value: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(row_id, str) or row_id not in PROVIDER_ROW_IDS:
        raise ValueError(f"unknown provider row: {row_id!r}")
    return copy.deepcopy(_observe(row_id, input_value))


__all__ = ["PROVIDER_ROW_IDS", "observe_provider_row", "scenario_input"]
