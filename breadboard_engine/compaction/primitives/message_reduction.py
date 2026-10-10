"""Tool-output masking and tool-free summaries of model-visible messages."""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..methods import CompactionContext, MethodUnavailable, SummaryRequest
from ..params import Params
from ..state import MessageEdit
from .chat_reducers import summary_request_options
from .history_selection import edited_history
from .reducers import Reduction, resolve_prompt
from .selectors import Selection


def model_message(message: Mapping[str, Any], *, strip_media: bool) -> dict[str, Any]:
    """Normalize text and replace user image/PDF parts without retaining tool attachments."""
    result = copy.deepcopy(dict(message))
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
        mime = part.get("mediaType") or part.get("mime")
        if kind in {"image_url", "image", "input_image"} and not mime:
            image = part.get("image_url") or {}
            url = image.get("url", "") if isinstance(image, Mapping) else str(image)
            match = re.match(r"data:([^;,]+)", url)
            mime = match.group(1) if match else "image/png"
        media = isinstance(mime, str) and (mime.startswith("image/") or mime == "application/pdf")
        if strip_media and result.get("role") in {"tool", "tool_result"}:
            if kind not in {"text", "tool_result"}:
                continue
            if kind == "tool_result":
                part = {key: value for key, value in part.items() if key != "attachments"}
        if strip_media and result.get("role") == "user" and media:
            parts.append({"type": "text", "text": f"[Attached {mime}: {part.get('filename') or 'file'}]"})
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
        messages = edited_history(context)
        edits = tuple(
            MessageEdit(i, {key: value for key, value in {**messages[i], "content": self.placeholder}.items()
                            if key not in self.remove_fields})
            for i in selection.targets
        )
        return Reduction(edits=edits, details=selection.details)


class MessageSummary:
    kind = "message_summary"

    def __init__(self, params: Params) -> None:
        self.system = resolve_prompt(params.value("system"), params, "system")
        self.prompt = resolve_prompt(params.value("prompt"), params, "prompt")
        self.contributors = tuple(resolve_prompt(value, params, "contributors")
                                  for value in params.list("contributors", []))
        self.strip_media = params.bool("strip_media", True)
        self.max_tokens = params.int("max_tokens", None, minimum=1)
        self.stream, self.tool_names, self.request_params, self.request_options_declared, self.stateless = summary_request_options(params)
        marker = params.value("marker", None)
        self.marker = None if marker is None else resolve_prompt(marker, params, "marker")
        params.done()

    def reduce(self, context: CompactionContext, selection: Selection) -> Reduction:
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        messages = edited_history(context)
        prior = context.state.latest_readable_boundary()
        history = list(prior.summary_messages) if prior is not None else []
        history.extend(messages[i] for i in selection.summarize)
        request_messages = [model_message(message, strip_media=self.strip_media) for message in history]
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
            request_options_declared=self.request_options_declared, stateless=self.stateless,
        )).text
        return Reduction(summary=summary, details={
            "summary": True,
            **({"stateless_summary": True} if self.stateless else {}),
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
            result.extend(model_message(context.messages[i], strip_media=True) for i in selection.replay)
        else:
            prefix = self.overflow_prefix if context.reason == "overflow" else ""
            result.append({"role": "user", "content": [{"type": "text", "text": prefix + self.continuation}]})
        return result
