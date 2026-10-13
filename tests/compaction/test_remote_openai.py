from __future__ import annotations

import json
import pytest

from breadboard_engine.compaction import (
    CompactionContext,
    CompactionState,
    ProjectionTarget,
    estimate_messages_tokens,
    settings_from_config,
)
from .support import run_pipeline
from breadboard_engine.compaction.methods import CompactionCancelled
from breadboard_engine.compaction.remote import RemoteCompaction
from breadboard_engine.compaction.remote.openai import (
    COMPACTION_TRIGGER_ITEM,
    CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE,
    OpenAIResponsesCompactionPort,
    build_compaction_v2_replacement_history,
    build_openai_native_history,
    parse_sse_events,
    trim_remote_compaction_input_to_context_window,
)
from breadboard_engine.provider.contracts import ProviderRuntimeContext
from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
import types


def test_build_openai_native_history_formats():
    messages = [
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Please inspect this code"},
        {
            "role": "assistant",
            "content": "Let me run bash.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "file1.py\nfile2.py"},
    ]

    items = build_openai_native_history(messages, "gpt-5")
    assert len(items) == 4
    # User message
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "user"
    assert items[0]["content"][0]["text"] == "Please inspect this code"
    # Assistant text and function call
    assert items[1]["type"] == "message"
    assert items[1]["role"] == "assistant"
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "call_123"
    assert items[2]["name"] == "bash"
    # Function call output
    assert items[3]["type"] == "function_call_output"
    assert items[3]["call_id"] == "call_123"
    assert items[3]["output"] == "file1.py\nfile2.py"


def test_authorized_canonical_media_encoded_in_remote_history(tmp_path):
    from breadboard.product.runtime.artifacts import ArtifactStore

    workspace = tmp_path / "workspace"
    artifact = ArtifactStore(workspace / ".breadboard" / "artifacts").put(
        b"\x89PNG\r\n\x1a\nimage-data",
        media_type="image/png",
    )
    uri = f"attachment://{artifact.digest}"
    metadata = {"attachment_capabilities": {uri: artifact.as_dict()}}
    session_state = types.SimpleNamespace(
        workspace=str(workspace),
        get_provider_metadata=lambda key, default=None: metadata.get(key, default),
    )
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "inspect this"},
        {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"},
    ]}]
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append(payload)
        return {"output": [{"type": "compaction", "encrypted_content": "enc_1"}]}

    OpenAIResponsesCompactionPort(http_poster=fake_poster).compact(CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": False}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=100,
        runtime_context=ProviderRuntimeContext(session_state, {}),
    ))
    image = posted_calls[0]["input"][0]["content"][1]
    assert image["type"] == "input_image" and image["image_url"].startswith("data:image/png;base64,")


def test_assistant_hosted_replay_is_in_remote_history():
    payload = {
        "type": "web_search_call",
        "status": "completed",
        "action": {"type": "search", "query": "needle"},
    }
    replay = {
        "type": "provider_replay",
        "provider_id": "openai",
        "schema_version": "openai.responses.hosted.v1",
        "replay_scope": "same_provider",
        "payload": payload,
    }
    messages = [
        {"role": "user", "content": "research this"},
        {"role": "assistant", "content": [replay]},
    ]
    items = build_openai_native_history(messages, "gpt-5")
    assert payload in items


def test_custom_tool_results_emitted_as_custom_tool_call_output():
    messages = [
        {
            "bb_native_compaction": {
                "provider": "openai",
                "api": "responses",
                "items": [{"type": "custom_tool_call", "call_id": "call_marker_c1"}],
            }
        },
        {"role": "tool", "tool_call_id": "call_marker_c1", "content": "marker custom out"},
        {"role": "tool", "tool_call_id": "call_prev_c2", "content": "prev custom out"},
        {"role": "tool", "tool_call_id": "call_func_1", "content": "func out"},
    ]
    prev_replacement = [
        {"type": "custom_tool_call", "call_id": "call_prev_c2"},
        {"type": "function_call", "call_id": "call_func_1"},
    ]
    items = build_openai_native_history(
        messages, "gpt-5", previous_replacement_history=prev_replacement
    )
    outputs = {it.get("call_id"): it for it in items if "output" in it}
    assert outputs["call_marker_c1"]["type"] == "custom_tool_call_output"
    assert outputs["call_prev_c2"]["type"] == "custom_tool_call_output"
    assert outputs["call_func_1"]["type"] == "function_call_output"


def test_typed_tool_output_envelope_is_replayed_in_remote_history():
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

    image = [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_view", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}]},
        responses_image_tool_result_message("call_view", image),
    ]
    items = build_openai_native_history(messages, "gpt-5")
    assert {"type": "function_call_output", "call_id": "call_view", "output": image} in items



def test_live_custom_tool_call_replays_native_item_in_call_order():
    custom = {"type": "custom_tool_call", "call_id": "call_patch", "name": "apply_patch", "input": "*** Begin Patch"}
    replay = {
        "type": "provider_replay", "provider_id": "openai",
        "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider", "payload": custom,
    }
    messages = [
        {"role": "user", "content": "patch it"},
        {
            "role": "assistant",
            "content": [replay],
            "tool_calls": [
                {"id": "call_read", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                {"id": "call_patch", "type": "function", "function": {"name": "apply_patch", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_read", "content": "read out"},
        {"role": "tool", "tool_call_id": "call_patch", "content": "patch out"},
    ]
    items = build_openai_native_history(messages, "gpt-5")
    calls = [(it["type"], it["call_id"]) for it in items if it["type"].endswith("call")]
    assert calls == [("function_call", "call_read"), ("custom_tool_call", "call_patch")]
    assert custom in items
    outputs = {it["call_id"]: it["type"] for it in items if "output" in it}
    assert outputs == {"call_read": "function_call_output", "call_patch": "custom_tool_call_output"}

def test_build_openai_native_history_with_previous_replacement():
    prev_items = [
        {"type": "compaction", "encrypted_content": "enc_prev_token"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier retained"}]},
    ]
    new_messages = [
        {"role": "user", "content": "Follow-up question"},
    ]
    items = build_openai_native_history(new_messages, "gpt-5", previous_replacement_history=prev_items)
    assert items[:2] == prev_items
    assert items[2]["type"] == "message"
    assert items[2]["role"] == "user"
    assert items[2]["content"][0]["text"] == "Follow-up question"


def test_trim_remote_compaction_input_to_context_window():
    items = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "start"}]},
        {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "x" * 10000},
    ]
    result = trim_remote_compaction_input_to_context_window(items, context_window=500)
    assert result["fits"] is True
    assert result["rewritten_outputs"] == 1
    rewritten_item = result["input"][2]
    assert rewritten_item["output"] == CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE


def test_build_compaction_v2_replacement_history_filters_contextual():
    input_items = [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "dev"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>\nrepo"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "User actual task"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "response"}]},
    ]
    compaction_item = {"type": "compaction", "encrypted_content": "enc_v2"}

    replacement, img_count = build_compaction_v2_replacement_history(
        input_items, compaction_item, retained_budget=10000
    )
    # Developer and contextual user messages are excluded from retained user history
    # Real user message is kept, followed by compaction item
    assert len(replacement) == 2
    assert replacement[0]["content"][0]["text"] == "User actual task"
    assert replacement[1] == compaction_item
    assert img_count == 0


def test_parse_sse_events():
    raw_sse = (
        "event: response.output_item.done\n"
        'data: {"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "enc_sse"}}\n\n'
        "event: response.completed\n"
        'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 450, "output_tokens": 10, "total_tokens": 460}}}\n\n'
    )
    events = parse_sse_events(raw_sse)
    assert len(events) == 2
    assert events[0]["item"]["encrypted_content"] == "enc_sse"
    assert events[1]["response"]["usage"]["input_tokens"] == 450


def test_v1_responses_compact_record_creation():
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload})
        return {
            "output": [
                {"type": "compaction", "encrypted_content": "enc_v1_token"},
            ]
        }

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Step 1"},
        {"role": "assistant", "content": "Step 1 answer"},
    ]
    ctx = CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": False}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=estimate_messages_tokens(messages),
    )

    record = port.compact(ctx)
    assert len(posted_calls) == 1
    assert posted_calls[0]["url"].endswith("/responses/compact")
    assert record.method == "remote"
    assert record.first_kept_index == len(messages)
    assert record.summary_messages == ()  # Non-readable OMP semantics
    assert record.native is not None
    assert record.native.provider == "openai"
    assert record.native.api == "responses"
    assert record.native.model == "gpt-5"
    assert len(record.native.items) == 1
    assert record.native.items[0]["type"] == "compaction"


def test_v2_streaming_responses_compact_record_creation():
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload})
        return (
            "event: response.output_item.done\n"
            'data: {"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "enc_v2_stream"}}\n\n'
            "event: response.completed\n"
            'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 1280, "output_tokens": 5, "total_tokens": 1285}}}\n\n'
        )

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    messages = [
        {"role": "system", "content": "Instructions"},
        {"role": "user", "content": "Real user turn"},
        {"role": "assistant", "content": "Assistant answer"},
    ]
    ctx = CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": True}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=estimate_messages_tokens(messages),
    )

    record = port.compact(ctx)
    assert len(posted_calls) == 1
    call = posted_calls[0]
    assert call["url"].endswith("/responses")
    # Verified compaction_trigger item was appended as last input
    assert call["payload"]["input"][-1] == COMPACTION_TRIGGER_ITEM
    assert call["payload"]["stream"] is True
    assert call["payload"]["store"] is False

    assert record.method == "remote"
    assert record.first_kept_index == len(messages)
    assert record.summary_messages == ()
    assert record.native is not None
    assert record.native.model == "gpt-5"
    items = record.native.items
    assert items[-1] == {"type": "compaction", "encrypted_content": "enc_v2_stream"}
    # Contains retained user message
    user_items = [it for it in items if it.get("role") == "user"]
    assert len(user_items) == 1
    assert user_items[0]["content"][0]["text"] == "Real user turn"


def _v2_context() -> CompactionContext:
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Turn 1"},
        {"role": "assistant", "content": "Turn 1 ans"},
    ]
    return CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": True}),
        reason="threshold",
        target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000,
        tokens_before=estimate_messages_tokens(messages),
    )


@pytest.mark.parametrize("stream", [
    "event: response.failed\ndata: {\"error\": \"internal_error\"}\n\n",
    'event: response.output_item.done\ndata: {"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "partial"}}\n\n',
], ids=["failed", "no_terminal_event"])
def test_v2_fallback_to_v1_on_stream_failure(stream):
    calls = []

    def fake_poster(url, payload, headers):
        calls.append(url)
        if url.endswith("/responses"):
            return stream
        return {"output": [{"type": "compaction", "encrypted_content": "enc_v1_fallback"}]}

    record = OpenAIResponsesCompactionPort(http_poster=fake_poster).compact(_v2_context())
    assert [url.rsplit("/", 1)[-1] for url in calls] == ["responses", "compact"]
    assert record.native.items[0]["encrypted_content"] == "enc_v1_fallback"


def test_v2_cancellation_skips_v1_fallback():
    calls = []

    def fake_poster(url, payload, headers):
        calls.append(url)
        raise CompactionCancelled("cancelled")

    with pytest.raises(CompactionCancelled):
        OpenAIResponsesCompactionPort(http_poster=fake_poster).compact(_v2_context())
    assert len(calls) == 1

def test_end_to_end_compaction_and_responses_runtime_replay():
    """
    Acceptance test:
    With a fake transport, Pipeline with RemoteCompaction + an OpenAI port compacts
    and the Responses runtime request body for the next turn contains the native items
    in place of the summarized history; non-matching targets get no native items;
    existing Responses tests still pass.
    """
    def fake_v1_poster(url, payload, headers):
        return {
            "output": [
                {"type": "compaction", "encrypted_content": "enc_token_turn1"},
            ]
        }

    port = OpenAIResponsesCompactionPort(http_poster=fake_v1_poster)
    settings = settings_from_config({"enabled": True, "method_order": ["remote", "soft"]})

    initial_messages = [
        {"role": "system", "content": "System setup"},
        {"role": "user", "content": "Old message 1" * 100},
        {"role": "assistant", "content": "Old response 1" * 100},
        {"role": "user", "content": "Old message 2" * 100},
        {"role": "assistant", "content": "Old response 2" * 100},
    ]
    state = CompactionState()
    target = ProjectionTarget("openai", "responses", "gpt-5")
    ctx = CompactionContext(
        messages=initial_messages,
        state=state,
        settings=settings,
        reason="threshold",
        target=target,
        context_window=8000,
        tokens_before=estimate_messages_tokens(initial_messages),
        remote_ports=(port,),
    )
    outcome = run_pipeline(ctx, {"remote": RemoteCompaction()})
    assert outcome.compacted
    assert len(state.records) == 1
    record = state.records[0]
    assert record.method == "remote"
    assert record.native is not None

    # Next turn: a new user turn arrives and is appended to full append-only history
    full_messages = list(initial_messages)
    full_messages.append({"role": "user", "content": "New user prompt for next turn"})

    # 1. Matching target: projected view contains native marker
    projected_matching = state.project(full_messages, target)
    assert any("bb_native_compaction" in m for m in projected_matching)

    # 2. Check Responses runtime request body generation for the next turn
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    # The controller clears the Responses chain when it commits the boundary.
    mock_session_state = types.SimpleNamespace(get_provider_metadata=lambda name: None)
    runtime_context = ProviderRuntimeContext(
        mock_session_state,
        {"provider_tools": {"openai": {"responses_stateful": True}}},
    )

    payload = runtime._request_payload(
        model="gpt-5",
        messages=projected_matching,
        tools=None,
        context=runtime_context,
    )

    # Acceptance verification:
    # A. The request body contains the native items in place of the summarized history
    input_items = payload["input"]
    assert any(it.get("type") == "compaction" and it.get("encrypted_content") == "enc_token_turn1" for it in input_items)
    # B. The new turn is appended after the native items
    assert input_items[-1]["role"] == "user"
    assert input_items[-1]["content"][0]["text"] == "New user prompt for next turn"
    # C. bb_native_compaction marker key is NEVER sent on the wire
    payload_dump = json.dumps(payload)
    assert "bb_native_compaction" not in payload_dump

    # 3. Non-matching target (e.g. switched to another model):
    other_target = ProjectionTarget("openai", "responses", "gpt-other-model")
    projected_other = state.project(full_messages, other_target)
    # Does NOT carry native items of the non-matching model
    assert not any("bb_native_compaction" in m for m in projected_other)
    other_payload = runtime._request_payload(
        model="gpt-other-model",
        messages=projected_other,
        tools=None,
        context=runtime_context,
    )
    assert not any(it.get("type") == "compaction" for it in other_payload["input"])


def test_runtime_compaction_port_factory():
    runtime = OpenAIResponsesRuntime(
        types.SimpleNamespace(provider_id="openai", runtime_id="openai_responses")
    )
    fake_client = types.SimpleNamespace(api_key="sk-test", base_url="https://api.openai.com/v1")
    port = runtime.compaction_port(client=fake_client, model="gpt-5")
    assert isinstance(port, OpenAIResponsesCompactionPort)
    assert port.provider == "openai"
    assert port.api == "responses"
    assert port.supports("gpt-5")


def test_two_successive_remote_compactions_slice_from_latest_boundary():
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload})
        idx = len(posted_calls)
        return {
            "output": [
                {"type": "compaction", "encrypted_content": f"enc_token_{idx}"},
            ]
        }

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    state = CompactionState()
    target = ProjectionTarget("openai", "responses", "gpt-5")
    settings = settings_from_config({"enabled": True, "remote_streaming_v2_enabled": False})

    messages = [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "Turn 1 question"},
        {"role": "assistant", "content": "Turn 1 answer"},
    ]
    ctx1 = CompactionContext(
        messages=messages,
        state=state,
        settings=settings,
        reason="threshold",
        target=target,
        context_window=8000,
        tokens_before=100,
    )
    rec1 = port.compact(ctx1)
    state.append(rec1, messages)

    assert rec1.first_kept_index == 3
    assert len(posted_calls) == 1

    messages = [
        *messages,
        {"role": "user", "content": "Turn 2 question"},
        {"role": "assistant", "content": "Turn 2 answer"},
    ]
    ctx2 = CompactionContext(
        messages=messages,
        state=state,
        settings=settings,
        reason="threshold",
        target=target,
        context_window=8000,
        tokens_before=200,
    )
    rec2 = port.compact(ctx2)
    state.append(rec2, messages)

    assert len(posted_calls) == 2
    second_input = posted_calls[1]["payload"]["input"]

    compaction_items = [it for it in second_input if it.get("type") == "compaction"]
    assert len(compaction_items) == 1
    assert compaction_items[0]["encrypted_content"] == "enc_token_1"

    assert not any(it.get("content", [{}])[0].get("text") == "Turn 1 question" for it in second_input if it.get("role") == "user")
    assert not any(it.get("content", [{}])[0].get("text") == "Turn 1 answer" for it in second_input if it.get("role") == "assistant")
    assert any(it.get("content", [{}])[0].get("text") == "Turn 2 question" for it in second_input if it.get("role") == "user")


def test_remote_record_estimate_counts_the_replacement_not_the_compacted_input():
    def projected_estimate(input_tokens: int) -> int:
        def fake_poster(url, payload, headers):
            return (
                'data: {"type": "response.output_item.done", "item": {"type": "compaction", "encrypted_content": "enc"}}\n\n'
                'data: {"type": "response.completed", "response": {"usage": {"input_tokens": %d}}}\n\n' % input_tokens
            )

        ctx = _v2_context()
        record = OpenAIResponsesCompactionPort(http_poster=fake_poster).compact(ctx)
        ctx.state.append(record, ctx.messages)
        return estimate_messages_tokens(ctx.state.project(ctx.messages, ctx.target))

    # The usage reports the history sent for compaction; the replacement is what later requests carry.
    assert projected_estimate(1280) == projected_estimate(900000)


def test_native_pass_after_a_readable_boundary_sends_its_summary_not_the_prefix():
    posted = []

    def fake_poster(url, payload, headers):
        posted.append(payload)
        return {"output": [{"type": "compaction", "encrypted_content": "enc"}]}

    messages = [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "Turn 1 question"},
        {"role": "assistant", "content": "Turn 1 answer"},
        {"role": "user", "content": "Turn 2 question"},
        {"role": "assistant", "content": "Turn 2 answer"},
    ]
    state = CompactionState()
    ctx = CompactionContext(
        messages=messages, state=state,
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": False}),
        reason="threshold", target=ProjectionTarget("openai", "responses", "gpt-5"),
        context_window=8000, tokens_before=100,
    )
    state.append(ctx.new_record(method="soft", first_kept_index=3, summary="S",
                                summary_messages=({"role": "user", "content": "<summary>S</summary>"},)), messages)
    OpenAIResponsesCompactionPort(http_poster=fake_poster).compact(ctx)

    texts = [part["text"] for item in posted[0]["input"] for part in item.get("content", []) if "text" in part]
    assert texts == ["<summary>S</summary>", "Turn 2 question", "Turn 2 answer"]


@pytest.mark.parametrize(("v2", "path"), [(True, "/v1/responses"), (False, "/v1/responses/compact")])
def test_sdk_client_requests_are_authenticated_and_use_the_configured_base_url(v2, path):
    import httpx
    import openai

    seen = []

    def handler(request):
        seen.append(request)
        item = {"type": "compaction", "encrypted_content": "enc"}
        if v2:
            body = ('data: {"type": "response.output_item.done", "item": %s}\n\n'
                    'data: {"type": "response.completed", "response": {}}\n\n') % json.dumps(item)
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"output": [item]})

    client = openai.OpenAI(api_key="sk-test", base_url="https://api.example.test/v1",
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    ctx = _v2_context()
    ctx = CompactionContext(
        messages=ctx.messages, state=ctx.state, reason="threshold", context_window=8000, tokens_before=ctx.tokens_before,
        settings=settings_from_config({"enabled": True, "remote_streaming_v2_enabled": v2}),
        target=ProjectionTarget("openai", "responses", "gpt-5.1-codex-mini"),
    )
    OpenAIResponsesCompactionPort(client=client, model="gpt-5.1-codex-mini").compact(ctx)

    assert [(r.url.host, r.url.path) for r in seen] == [("api.example.test", path)]
    assert seen[0].headers["authorization"] == "Bearer sk-test"


def test_native_pass_after_a_native_boundary_sends_edited_kept_messages():
    from breadboard_engine.compaction.state import MessageEdit

    posted = []

    def fake_poster(url, payload, headers):
        posted.append(payload)
        return {"output": [{"type": "compaction", "encrypted_content": f"enc_{len(posted)}"}]}

    port = OpenAIResponsesCompactionPort(http_poster=fake_poster)
    settings = settings_from_config({"enabled": True, "remote_streaming_v2_enabled": False})
    target = ProjectionTarget("openai", "responses", "gpt-5")
    state = CompactionState()
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Turn 1"}]

    def context(history):
        return CompactionContext(messages=history, state=state, settings=settings, reason="threshold",
                                 target=target, context_window=8000, tokens_before=100)

    state.append(port.compact(context(messages)), messages)
    messages = [*messages, {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]},
                {"role": "assistant", "content": "Saw it"}]
    ctx = context(messages)
    state.append(ctx.new_record(method="drop_images", edits=(
        MessageEdit(2, {"role": "user", "content": [{"type": "text", "text": "[image removed]"}]}),)), messages)
    port.compact(ctx)

    sent = json.dumps(posted[1]["input"])
    assert "[image removed]" in sent and "input_image" not in sent


def test_codex_estimator_counts_a_native_marker_at_its_replacement_estimate():
    from breadboard_engine.compaction.primitives.byte_estimator import bytes4
    from breadboard_engine.compaction.state import NATIVE_MARKER_KEY

    marker = {"role": "user", "content": "", NATIVE_MARKER_KEY: {"record_id": "r1", "token_estimate": 50_000}}
    assert bytes4([marker]) >= 50_000


def test_codex_estimator_counts_a_typed_tool_result_as_its_wire_item():
    from breadboard_engine.compaction.primitives.byte_estimator import bytes4
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

    output = [{"type": "input_text", "text": "x" * 400},
              {"type": "input_image", "image_url": "data:image/png;base64," + "A" * 4000}]
    wire = {"type": "function_call_output", "call_id": "c1", "output": output}
    assert bytes4([responses_image_tool_result_message("c1", output)]) == bytes4([wire])
