"""Destination-safe provider history projection."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract_messages import normalize_provider_replay
from .contract_wire import ProviderContractError, canonical_json


def _portable_raw_tool_call(call: Any) -> Optional[tuple[str, str, str]]:
    if not isinstance(call, Mapping) or not isinstance(call.get("raw_arguments"), str):
        return None
    call_id = call.get("call_id") or call.get("id") or call.get("tool_call_id")
    name = call.get("name")
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        return None
    return call_id, name, call["raw_arguments"]


def _portable_raw_call_text(call_id: str, name: str, raw_arguments: str) -> Dict[str, str]:
    return {
        "type": "text",
        "text": f"[Tool call {name}; call ID {call_id}; raw arguments]\n{raw_arguments}",
    }


def _portable_image_reference(call_id: str, ordinal: int) -> str:
    return f"[Image {ordinal} for tool call ID {call_id}]"


def _portable_result_text_and_images(
    content: Any,
    *,
    image_placeholder: Optional[str],
    image_call_id: Optional[str] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    from .input_media import is_provider_image_part, portable_image_media

    if isinstance(content, str):
        return content, []
    if content is None:
        return canonical_json(content), []
    if not isinstance(content, list):
        return canonical_json(content), []
    text_parts: List[str] = []
    images: List[Dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, Mapping):
            if image_placeholder is None:
                text_parts.append(canonical_json(part))
            continue
        if is_provider_image_part(part):
            if image_placeholder is not None:
                text_parts.append(image_placeholder)
            else:
                if image_call_id is not None:
                    text_parts.append(
                        _portable_image_reference(image_call_id, len(images) + 1)
                    )
                images.append(portable_image_media(part))
        elif part.get("type") in {"text", "input_text", "output_text"}:
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif image_placeholder is None:
            text_parts.append(canonical_json(dict(part)))
    return "".join(text_parts), images


def _portable_result_entries(
    message: Mapping[str, Any], *, preserve_responses_replay: bool,
) -> tuple[List[tuple[str, Any, bool]], bool]:
    content = message.get("content")
    if isinstance(content, list):
        response_replays = [
            block for block in content
            if isinstance(block, Mapping)
            and block.get("type") == "provider_replay"
            and block.get("schema_version") == "openai.responses.tool_output.v1"
            and block.get("provider_id") in {"openai", "openrouter"}
        ]
        if response_replays:
            if preserve_responses_replay and len(response_replays) == len(content):
                return [], True
            entries = []
            for replay in response_replays:
                normalized = normalize_provider_replay(replay)
                payload = normalized["payload"]
                entries.append((payload["call_id"], payload["output"], False))
            return entries, False

        entries = []
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_result":
                continue
            call_id = block.get("call_id") or block.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                entries.append((
                    call_id,
                    block.get("content", block.get("result", block.get("error"))),
                    bool(block.get("is_error", block.get("error") is not None)),
                ))
        if entries:
            return entries, False
    call_id = message.get("tool_call_id") or message.get("call_id")
    if isinstance(call_id, str) and call_id:
        return [(call_id, content, False)], False
    return [], False


def _append_portable_tool_result(
    messages: List[Dict[str, Any]],
    raw_call_messages: Mapping[str, Dict[str, Any]],
    call_id: str,
    content: Any,
    is_error: bool,
    *,
    image_placeholder: Optional[str],
    session_state: Any,
    pending_tool_images: List[Dict[str, Any]],
    pending_anthropic_results: Optional[List[Dict[str, Any]]] = None,
) -> None:
    raw_message = raw_call_messages.get(call_id)
    if raw_message is not None:
        result_text, images = _portable_result_text_and_images(
            content, image_placeholder=image_placeholder, image_call_id=call_id,
        )
        raw_message["content"].append({
            "type": "text",
            "text": f"[Tool result for call ID {call_id}]\n{result_text}",
        })
        if images:
            pending_tool_images.append({
                "type": "text",
                "text": f"[Images from tool call ID {call_id}]",
            })
            pending_tool_images.extend(images)
        return
    if image_placeholder is not None:
        result_text, _ = _portable_result_text_and_images(
            content, image_placeholder=image_placeholder,
        )
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": result_text,
        })
        return
    if pending_anthropic_results is not None:
        from .adapters import AnthropicAdapter

        pending_anthropic_results.append({
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": AnthropicAdapter().to_native_tool_result_content(
                content, session_state,
            ),
            "is_error": is_error,
        })
        return
    result_text, images = _portable_result_text_and_images(
        content,
        image_placeholder=None,
        image_call_id=call_id,
    )
    result_block = {
        "type": "tool_result",
        "call_id": call_id,
        "content": result_text,
        "is_error": is_error,
    }
    messages.append({"role": "tool", "content": [result_block]})
    for ordinal, image in enumerate(images, 1):
        pending_tool_images.extend([
            {
                "type": "text",
                "text": _portable_image_reference(call_id, ordinal),
            },
            image,
        ])

def portable_provider_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    source_provider: str,
    source_api: str,
    target_provider: str,
    target_api: str,
    session_state: Any = None,
    image_placeholder: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Project provider history into destination-safe portable messages."""
    from .input_media import is_provider_image_part, portable_image_media
    from .anthropic_replay import (
        is_tool_result_replay,
        portable_tool_continuation,
    )

    same_anthropic_api = (
        image_placeholder is None
        and source_provider == target_provider == "anthropic"
        and source_api == target_api == "messages"
    )

    def keep_native_anthropic_replay(content: Any) -> bool:
        return (
            same_anthropic_api
            and isinstance(content, list)
            and any(
                isinstance(block, dict) and is_tool_result_replay(block)
                for block in content
            )
        )

    def expanded_history():
        for message in messages:
            content = message.get("content") if isinstance(message, Mapping) else None
            if keep_native_anthropic_replay(content):
                yield message
                continue
            expanded = (
                portable_tool_continuation(content)
                if isinstance(content, list) else None
            )
            if expanded is None:
                yield message
            else:
                yield from expanded


    same_responses_api = (
        image_placeholder is None
        and source_provider == target_provider
        and source_provider in {"openai", "openrouter"}
        and source_api == target_api == "responses"
    )
    portable: List[Dict[str, Any]] = []
    raw_call_messages: Dict[str, Dict[str, Any]] = {}
    native_anthropic_tool_results = (
        target_provider == "anthropic"
        and target_api == "messages"
        and image_placeholder is None
    )
    pending_anthropic_results: List[Dict[str, Any]] = []
    pending_tool_images: List[Dict[str, Any]] = []

    def flush_pending_tool_batch() -> None:
        nonlocal pending_anthropic_results, pending_tool_images
        if native_anthropic_tool_results and pending_anthropic_results:
            from .adapters import AnthropicAdapter

            native_results = pending_anthropic_results
            pending_anthropic_results = []
            native_message = {
                "role": "user",
                "content": native_results,
            }
            portable.append(
                AnthropicAdapter().create_tool_result_batch_message([native_message])
            )
        if pending_tool_images:
            native_images = pending_tool_images
            pending_tool_images = []
            portable.append({
                "role": "user",
                "content": native_images,
            })

    for message in expanded_history():
        if not isinstance(message, Mapping):
            raise ProviderContractError("portable provider history messages must be objects")
        content = message.get("content")
        if keep_native_anthropic_replay(content):
            flush_pending_tool_batch()
            portable.append(copy.deepcopy(dict(message)))
            continue
        entries, keep_replay = _portable_result_entries(
            message, preserve_responses_replay=same_responses_api,
        )
        if keep_replay:
            replay_call_ids = {
                block["payload"]["call_id"]
                for block in content
                if isinstance(block, Mapping)
                and isinstance(block.get("payload"), Mapping)
                and isinstance(block["payload"].get("call_id"), str)
            }
            if not replay_call_ids.intersection(raw_call_messages):
                flush_pending_tool_batch()
                portable.append(copy.deepcopy(dict(message)))
                continue
            entries, keep_replay = _portable_result_entries(
                message, preserve_responses_replay=False,
            )
        if (
            entries
            and image_placeholder is None
            and isinstance(content, str)
            and message.get("role") in {"tool", "tool_result"}
            and not native_anthropic_tool_results
            and not any(raw_call_messages.get(call_id) is not None for call_id, _, _ in entries)
        ):
            portable.append(copy.deepcopy(dict(message)))
            continue
        if entries:
            for call_id, result, is_error in entries:
                _append_portable_tool_result(
                    portable,
                    raw_call_messages,
                    call_id,
                    result,
                    is_error,
                    image_placeholder=image_placeholder,
                    session_state=session_state,
                    pending_tool_images=pending_tool_images,
                    pending_anthropic_results=(
                        pending_anthropic_results
                        if native_anthropic_tool_results else None
                    ),
                )
            continue

        flush_pending_tool_batch()
        projected = copy.deepcopy(dict(message))
        for key in ("reasoning", "reasoning_content", "reasoning_details"):
            projected.pop(key, None)
        raw_calls: List[tuple[str, str, str]] = []
        if isinstance(content, list):
            kept = []
            for block in content:
                raw_call = (
                    _portable_raw_tool_call(block)
                    if message.get("role") == "assistant"
                    and isinstance(block, Mapping)
                    and block.get("type") == "tool_call"
                    else None
                )
                if raw_call is not None:
                    raw_calls.append(raw_call)
                    kept.append(_portable_raw_call_text(*raw_call))
                elif isinstance(block, Mapping) and is_provider_image_part(block):
                    if image_placeholder is not None:
                        kept.append({"type": "text", "text": image_placeholder})
                    else:
                        kept.append(portable_image_media(block))
                elif not (
                    isinstance(block, Mapping)
                    and block.get("type") in {
                        "thinking", "redacted_thinking", "provider_replay",
                    }
                ):
                    kept.append(copy.deepcopy(block))
            projected["content"] = kept

        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
            tool_calls = message["tool_calls"]
            ordinary_calls = []
            for call in tool_calls:
                raw_call = _portable_raw_tool_call(call)
                if raw_call is None:
                    ordinary_calls.append(copy.deepcopy(call))
                    continue
                raw_calls.append(raw_call)
                if not isinstance(projected.get("content"), list):
                    projected["content"] = (
                        [{"type": "text", "text": content}]
                        if isinstance(content, str) and content else []
                    )
                projected["content"].append(_portable_raw_call_text(*raw_call))
            if len(ordinary_calls) != len(tool_calls):
                if ordinary_calls:
                    projected["tool_calls"] = ordinary_calls
                else:
                    projected.pop("tool_calls", None)

        keep_message = (
            bool(projected.get("content") or message.get("tool_calls"))
            if isinstance(content, list) else True
        )
        if keep_message:
            portable.append(projected)
            for call_id, _, _ in raw_calls:
                raw_call_messages[call_id] = projected
    flush_pending_tool_batch()
    return portable
