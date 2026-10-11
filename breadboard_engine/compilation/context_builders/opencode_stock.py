"""Pinned stock OpenCode input hooks and summary instruction projection.

Only the new profiles select this module. Stock supplies the current input's
chat.message transformation and the summary instruction/contributor. BB keeps
its own canonical history, provider calls and committed compaction records.
Sources: OpenCode compaction.ts:202-220; OmO keyword-detector/hook.ts:96-105,
compaction-context-injector/hook.ts:59-67.
"""
from __future__ import annotations

import copy
from dataclasses import replace

from ...compaction.methods import SummaryRequest, SummaryResponse
from ...execution.opencode.wire import StockExecutionError
from ...execution.opencode.driver import prepare_stock_initial_user


def _last_user_content(request: dict):
    messages = request.get("input")
    if not isinstance(messages, list) or not messages or messages[-1].get("role") != "user":
        raise StockExecutionError("Stock projection omitted its final user instruction")
    content = messages[-1].get("content")
    if not isinstance(content, (str, list)):
        raise StockExecutionError("Stock user instruction has no provider-facing content")
    if isinstance(content, str):
        return content
    # AI SDK serializes canonical text to input_text. Reverse that exact
    # representation without dropping fields or accepting provider wire blocks
    # in BB's closed semantic content union.
    canonical = []
    for block in content:
        if not isinstance(block, dict) or set(block) != {"type", "text"} or block["type"] != "input_text" or not isinstance(block["text"], str):
            raise StockExecutionError("Stock text projection requires exact input_text blocks")
        canonical.append({"type": "text", "text": block["text"]})
    return canonical


def build_context(owner, state, run_context, *, fresh, user_message):
    # The raw input is saved before this hook. Stock transforms it exactly once,
    # then BB records that transformed current input, not stock's old history.
    native_request = prepare_stock_initial_user(owner, state)
    if native_request is None:
        raise StockExecutionError("Source input projection requires the stock executor")
    # Stock supplies current transformed text and opaque media ordinals only.
    # Preserve the original authorized BB media at those exact source slots.
    content = user_message["content"]
    media = [] if isinstance(content, str) else [part for part in content if part.get("type") != "text"]
    projected = []
    used = set()
    for part in state._opencode_tool_session.latest_user_sequence():
        if part.get("type") == "stock_media":
            index = part.get("index")
            if type(index) is not int or not 0 <= index < len(media) or index in used:
                raise StockExecutionError("Source input projection has an unauthorized media ordinal")
            used.add(index)
            projected.append(media[index])
        elif set(part) == {"type", "text"} and part["type"] == "text" and isinstance(part["text"], str):
            projected.append(copy.deepcopy(part))
        else:
            raise StockExecutionError("Source current-user projection contains an unknown part")
    if len(used) != len(media):
        raise StockExecutionError("Source input projection omitted authorized media")
    user_message["content"] = projected
    return []

def project_inflight_current_user(state, native_request, messages):
    """Overlay only the current user DTO for this request, never stored history."""
    index = next((index for index in range(len(messages) - 1, -1, -1)
                  if messages[index].get("role") == "user"), None)
    if index is None:
        raise StockExecutionError("BB omitted its canonical current user")
    content = messages[index]["content"]
    media = [] if isinstance(content, str) else [part for part in content if part.get("type") != "text"]
    projected = []
    used = set()
    for part in state._opencode_tool_session.latest_user_sequence(native_request):
        if part.get("type") == "stock_media":
            ordinal = part.get("index")
            if type(ordinal) is not int or not 0 <= ordinal < len(media) or ordinal in used:
                raise StockExecutionError("In-flight source input has an unauthorized media ordinal")
            used.add(ordinal)
            projected.append(media[ordinal])
        elif set(part) == {"type", "text"} and part["type"] == "text":
            projected.append(copy.deepcopy(part))
        else:
            raise StockExecutionError("In-flight source input contains an unknown part")
    if len(used) != len(media) and not state._opencode_tool_session.is_compaction_continuation():
        raise StockExecutionError("In-flight source input omitted authorized media")
    result = list(messages)
    result[index] = {**messages[index], "content": projected}
    return result


def project_inflight_tool_arguments(native_request, messages):
    """Use stock's repaired name and JS arguments without changing history."""
    calls = {
        item["call_id"]: item for item in native_request["input"]
        if item.get("type") == "function_call"
    }
    result = list(messages)
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        projected = []
        changed = False
        for block in content:
            payload = block.get("payload")
            if (block.get("type") == "provider_replay"
                    and block.get("schema_version") == "openai.responses.tools.v1"
                    and isinstance(payload, dict) and payload.get("type") == "function_call"):
                call_id = payload.get("call_id")
                native = calls.get(call_id)
                if native is None or not isinstance(native.get("arguments"), str) or not isinstance(native.get("name"), str):
                    raise StockExecutionError(f"Native pending request omitted arguments/name for BB call {call_id}")
                block = {**block, "payload": {**payload, "arguments": native["arguments"], "name": native["name"]}}
                changed = True
            projected.append(block)
        if changed:
            result[index] = {**message, "content": projected}
    return result


def project_inflight_tool_outputs(native_request, messages):
    """Copy source-owned output edits into request DTOs, never canonical history."""
    outputs = {item["call_id"]: item for item in native_request["input"]
               if item.get("type") == "function_call_output"}
    result = list(messages)
    for index, message in enumerate(messages):
        if message.get("role") not in {"tool", "tool_result"}:
            continue
        content = message.get("content")
        if not isinstance(content, list):
            call_id = message.get("tool_call_id") or message.get("tool_use_id") or message.get("call_id")
            native = outputs.get(call_id)
            if native is not None:
                output = copy.deepcopy(native["output"])
                if not isinstance(output, str):
                    output = [{"type": "provider_replay", "provider_id": "openai",
                               "schema_version": "openai.responses.tool_output.v1",
                               "replay_scope": "same_provider", "payload": copy.deepcopy(native)}]
                result[index] = {**message, "content": output}
            continue
        projected = []
        changed = False
        for block in content:
            payload = block.get("payload")
            if (block.get("type") == "provider_replay"
                    and block.get("schema_version") == "openai.responses.tool_output.v1"
                    and isinstance(payload, dict)):
                native = outputs.get(payload.get("call_id"))
                if native is not None:
                    block = {**block, "payload": {**payload, "output": copy.deepcopy(native["output"])}}
                    changed = True
            elif block.get("type") == "tool_result":
                native = outputs.get(block.get("call_id"))
                if native is not None:
                    if isinstance(native["output"], str):
                        block = {**block, "content": native["output"]}
                    else:
                        block = {"type": "provider_replay", "provider_id": "openai",
                                 "schema_version": "openai.responses.tool_output.v1",
                                 "replay_scope": "same_provider", "payload": copy.deepcopy(native)}
                    changed = True
            projected.append(block)
        if changed:
            result[index] = {**message, "content": projected}
    return result



class StockSummaryProjection:
    def __init__(self, state):
        self.state = state

    def prepare(self, request: SummaryRequest) -> SummaryRequest:
        session = getattr(self.state, "_opencode_tool_session", None)
        if session is None:
            raise StockExecutionError("Source summary projection requires its active native session")
        if request.context_reason is None:
            raise StockExecutionError("Source summary projection requires its real compaction reason")
        native_request = session.compaction_prompt(auto=request.context_reason != "manual")
        if not request.messages or request.messages[-1].get("role") != "user":
            raise StockExecutionError("BB summary request omitted its canonical final instruction")
        instruction = {**request.messages[-1], "content": copy.deepcopy(_last_user_content(native_request))}
        messages = project_inflight_tool_arguments(native_request, [*request.messages[:-1], instruction])
        messages = project_inflight_tool_outputs(native_request, messages)
        return replace(request, messages=tuple(messages))

    def complete(self, response: SummaryResponse):
        # The mirror commits the genuine BB response, including usage/reasoning.
        if response.provider_result is None:
            raise StockExecutionError("Source summary mirror requires its genuine provider result")
        self.state._opencode_tool_session.set_compaction_response(response.provider_result)
        return None


def build_summary_projection(state, config):
    return StockSummaryProjection(state)
