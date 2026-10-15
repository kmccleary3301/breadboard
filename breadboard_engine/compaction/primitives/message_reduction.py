"""Tool-output masking and tool-free summaries of model-visible messages."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from ..methods import CompactionContext, MethodUnavailable, SummaryRequest
from ..params import Params
from ..state import MessageEdit
from ..transcript import anthropic_replay_conversation, media_placeholder, rewrite_result_content
from ..serialize import TranscriptStyle, serialize_conversation
from .chat_reducers import summary_request_options
from .protected_windows import visible_messages
from .reducers import Reduction, resolve_prompt
from .selectors import Selection


_UNDECODED_REPLAY = object()

def model_message(
    message: Mapping[str, Any],
    *,
    strip_media: bool,
    replay_view: Any = _UNDECODED_REPLAY,
) -> dict[str, Any]:
    """Normalize one chat message, replacing user image/PDF parts when requested."""
    result = copy.deepcopy(dict(message))
    if replay_view is _UNDECODED_REPLAY:
        replay_view = anthropic_replay_conversation(message)
    if replay_view is not None:
        if any(item.get("role") == "user" for item in replay_view):
            transcript = serialize_conversation(
                replay_view, TranscriptStyle(skip_useless=False, include_tool_result_ids=True),
            )
            return {"role": "user", "content": [{"type": "text", "text": transcript}]}
        if strip_media:
            call_ids = [
                part.get("call_id")
                for replay_message in replay_view
                for part in replay_message.get("content", [])
            ]
            if all(isinstance(call_id, str) for call_id in call_ids) and len(set(call_ids)) == len(call_ids):
                projected_content = []
                for replay_message in replay_view:
                    for part in replay_message.get("content", []):
                        projected_part = dict(part)
                        value = projected_part.get("content")
                        if isinstance(value, (list, tuple)):
                            projected_part["content"] = model_message(
                                {"role": "user", "content": list(value)},
                                strip_media=True,
                                replay_view=None,
                            )["content"]
                        projected_part.pop("attachments", None)
                        projected_content.append(projected_part)
                result["content"] = projected_content

    content = result.get("content")
    if isinstance(content, str):
        if result.get("role") not in {"tool", "tool_result"}:
            result["content"] = [{"type": "text", "text": content}]
        return result
    if not isinstance(content, list):
        return result
    parts = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        kind = part.get("type")
        placeholder = media_placeholder(part, image_label=None)
        if strip_media and result.get("role") in {"tool", "tool_result"}:
            if kind == "provider_replay" and part.get("schema_version") == "openai.responses.tool_output.v1":
                payload = part.get("payload") if isinstance(part.get("payload"), Mapping) else {}
                output = payload.get("output") if isinstance(payload.get("output"), list) else []
                text = "\n".join(item.get("text", "") for item in output
                                 if isinstance(item, Mapping) and item.get("type") in {"input_text", "output_text"})
                part = {"type": "tool_result", "call_id": payload.get("call_id"), "content": text, "is_error": False}
            elif kind not in {"text", "tool_result"}:
                continue
            if kind == "tool_result":
                part = {key: value for key, value in part.items() if key != "attachments"}
        if strip_media and result.get("role") == "user" and placeholder is not None:
            parts.append({"type": "text", "text": placeholder})
        else:
            parts.append(dict(part))
    result["content"] = parts
    return result




class MaskOutputs:
    kind = "mask_outputs"

    def __init__(self, params: Params) -> None:
        self.placeholder = resolve_prompt(params.value("placeholder"), params, "placeholder")
        self.remove_fields = params.list("remove_fields", [])
        params.done()

    def reduce(self, context: CompactionContext, selection: Selection) -> Reduction:
        if not selection.targets:
            raise MethodUnavailable(str(selection.details.get("noop") or "No eligible tool outputs"))
        messages = visible_messages(context)
        mask_call_ids_by_index = selection.details.get("mask_call_ids_by_index", {})
        edits = []
        for index in selection.targets:
            selected_call_ids = (
                mask_call_ids_by_index.get(str(index))
                if isinstance(mask_call_ids_by_index, Mapping) else None
            )
            message = messages[index]
            masked = dict(message)
            if not rewrite_result_content(masked, self.placeholder, selected_call_ids):
                masked["content"] = self.placeholder
            edits.append(MessageEdit(index, {
                key: value for key, value in masked.items() if key not in self.remove_fields
            }))
        details = {key: value for key, value in selection.details.items() if key != "mask_call_ids_by_index"}
        return Reduction(edits=tuple(edits), details=details)


class MessageSummary:
    kind = "message_summary"

    def __init__(self, params: Params) -> None:
        self.system = resolve_prompt(params.value("system"), params, "system")
        self.prompt = resolve_prompt(params.value("prompt"), params, "prompt")
        self.contributors = tuple(resolve_prompt(value, params, "contributors")
                                  for value in params.list("contributors", []))
        self.strip_media = params.bool("strip_media", True)
        self.max_tokens = params.int("max_tokens", None, minimum=1)
        self.stream, self.tool_names, self.request_params, self.request_options_declared = summary_request_options(params)
        marker = params.value("marker", None)
        self.marker = None if marker is None else resolve_prompt(marker, params, "marker")
        params.done()

    def reduce(self, context: CompactionContext, selection: Selection) -> Reduction:
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        messages = visible_messages(context)
        prior = context.state.latest_readable_boundary()
        history = list(prior.summary_messages) if prior is not None else []
        history.extend(messages[i] for i in selection.summarize)
        projected_history = []
        has_native_observations = False
        for message in history:
            replay_view = anthropic_replay_conversation(message)
            if replay_view is not None and any(item.get("role") == "user" for item in replay_view):
                has_native_observations = True
                projected_history.extend(replay_view)
            else:
                projected_history.append(model_message(
                    message, strip_media=self.strip_media, replay_view=replay_view,
                ))
        if has_native_observations:
            transcript = serialize_conversation(
                projected_history, TranscriptStyle(skip_useless=False, include_tool_result_ids=True),
            )
            request_messages = [{"role": "user", "content": [{"type": "text", "text": transcript}]}]
        else:
            request_messages = projected_history
        for message in request_messages:
            message.pop("summary", None)
        if self.marker is not None and not selection.replay:
            request_messages.append({"role": "user", "content": [{"type": "text", "text": self.marker}]})
        prompt = "\n\n".join((self.prompt, *self.contributors))
        if context.custom_instructions:
            prompt += "\n\n" + context.custom_instructions
        request_messages.append({"role": "user", "content": [{"type": "text", "text": prompt}]})
        summary = context.summarizer.complete(SummaryRequest(
            system=self.system, messages=tuple(request_messages), max_tokens=self.max_tokens,
            purpose="summary", model=context.settings.summary_model or context.target.model,
            stream=self.stream, tool_names=self.tool_names, request_params=self.request_params,
            request_options_declared=self.request_options_declared,
            context_reason=context.reason,
        )).text
        return Reduction(summary=summary, details={
            "summary": True,
            **({"queued_user_reminder": True} if selection.replay else {}),
        })


class SummaryReplay:
    """Place a summary bridge and optionally replay the excluded user or continue."""

    kind = "summary_replay"

    def __init__(self, params: Params) -> None:
        self.marker = resolve_prompt(params.value("marker"), params, "marker")
        self.continuation = resolve_prompt(params.value("continuation"), params, "continuation")
        self.overflow_prefix = resolve_prompt(params.value("overflow_prefix"), params, "overflow_prefix")
        self.auto_reasons = params.list("auto_reasons", ["threshold", "overflow"])
        params.done()

    def place(self, context: CompactionContext, selection: Selection, reduction: Reduction) -> list[dict[str, Any]]:
        result = [
            {"role": "user", "content": [{"type": "text", "text": self.marker}]},
            {"role": "assistant", "content": [{"type": "text", "text": reduction.summary or ""}]},
        ]
        if context.reason not in self.auto_reasons:
            return result
        if selection.replay:
            visible = visible_messages(context)
            replay_history = []
            has_native_observations = False
            for index in selection.replay:
                message = visible[index]
                replay_view = anthropic_replay_conversation(message)
                if replay_view is not None and any(item.get("role") == "user" for item in replay_view):
                    replay_history.extend(replay_view)
                    has_native_observations = True
                else:
                    replay_history.append(model_message(
                        message, strip_media=True, replay_view=replay_view,
                    ))
            if has_native_observations:
                transcript = serialize_conversation(
                    replay_history, TranscriptStyle(skip_useless=False, include_tool_result_ids=True),
                )
                result.append({"role": "user", "content": [{"type": "text", "text": transcript}]})
            else:
                result.extend(replay_history)
        else:
            prefix = self.overflow_prefix if context.reason == "overflow" else ""
            result.append({"role": "user", "content": [{"type": "text", "text": prefix + self.continuation}]})
        return result
