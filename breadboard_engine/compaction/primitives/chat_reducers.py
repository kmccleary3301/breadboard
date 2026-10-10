"""Chat-format summaries and cleared-placeholder tool-result masking."""
from __future__ import annotations

import re

from ..methods import CompactionContext, MethodUnavailable, SummaryRequest
from ..params import Params, PresetError
from ..state import MessageEdit
from .accounting import _text_parts
from .reducers import Reduction, resolve_prompt
from .selectors import Selection


def normalize_xml_summary(text: str) -> str:
    """Reproduce JS String.replace semantics, including replacement-dollar expansion."""
    for tag, heading in (("analysis", "Analysis"), ("summary", "Summary")):
        match = re.search(f"<{tag}>([\\s\\S]*?)</{tag}>", text)
        if match is None:
            continue
        replacement = heading + ":\n" + match.group(1).strip()
        substitutions = {"$$": "$", "$&": match.group(0), "$`": text[:match.start()], "$'": text[match.end():]}
        replacement = re.sub(r"\$(?:\$|&|`|')", lambda m: substitutions[m.group(0)], replacement)
        text = text[:match.start()] + replacement + text[match.end():]
    return re.sub(r"\n\n+", "\n\n", text).strip()


def summary_request_options(params: Params):
    """Preserve omission versus explicit empty tools in the request block."""
    options = params.mapping("request", None)
    if options is None:
        return False, (), None, False, False
    request = params.child(options, "request")
    stream = request.bool("stream", False)
    stateless = request.bool("stateless", False)
    names = request.list("tools", [])
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise PresetError(f"{request.where}.tools must contain unique nonempty tool names")
    raw_params = request.mapping("params", {})
    request_params = dict(raw_params) if "params" in options or "tools" in options else None
    if any(key in raw_params for key in ("model", "messages", "tools", "stream", "max_tokens")):
        raise PresetError(f"{request.where}.params must not override model, messages, tools, stream, or max_tokens")
    request.done()
    return stream, tuple(names), request_params, True, stateless


class ChatSummary:
    kind = "chat_summary"

    def __init__(self, params: Params) -> None:
        self.system = resolve_prompt(params.value("system", ""), params, "system")
        self.prompt = resolve_prompt(params.value("prompt"), params, "prompt")
        self.max_tokens = params.int("max_tokens", None, minimum=1)
        self.empty_fallback = params.str("empty_fallback", None)
        self.normalize_tags = params.bool("normalize_tags", False)
        self.system_from_history = params.bool("system_from_history", False)
        self.stream, self.tool_names, self.request_params, self.request_options_declared, self.stateless = summary_request_options(params)
        params.done()

    def reduce(self, context: CompactionContext, selection: Selection) -> Reduction:
        if selection.details.get("native"):
            port = next((p for p in context.remote_ports if p.provider == context.target.provider
                         and p.api == context.target.api and p.supports(context.target.model)), None)
            if port is None:
                raise MethodUnavailable("No compatible native compaction port")
            record = port.compact(context)
            if record.native is None:
                raise ValueError("Native compaction port returned no native payload")
            return Reduction(native=record.native, details=record.details)
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        view = context.projected()
        systems = ["".join(_text_parts(m.get("content"))) for m in view if m.get("role") == "system"]
        messages = [m for m in view if m.get("role") != "system"]
        if selection.replay and not selection.details:
            replay = [context.messages[i] for i in selection.replay]
            messages = [m for m in messages if m not in replay]
        prompt = self.prompt
        if context.custom_instructions:
            marker = "\n\nIMPORTANT:"
            focus = "\n\nAdditional Instructions:\n" + context.custom_instructions
            if marker in prompt:
                before, after = prompt.rsplit(marker, 1)
                prompt = before + focus + marker + after
            else:
                prompt += focus
        request = SummaryRequest(system="\n\n".join(systems) if self.system_from_history else self.system,
                                 messages=tuple([*messages, {"role": "user", "content": prompt}]),
                                 max_tokens=self.max_tokens, purpose="summary",
                                 model=context.settings.summary_model or context.target.model,
                                 stream=self.stream, tool_names=self.tool_names, request_params=self.request_params,
                                 request_options_declared=self.request_options_declared, stateless=self.stateless)
        text = context.summarizer.complete(request).text
        if not text and self.empty_fallback is not None:
            text = self.empty_fallback
        if self.normalize_tags:
            text = normalize_xml_summary(text)
        return Reduction(summary=text, details={"stateless_summary": True} if self.stateless else {})

