"""Chat-format summaries and artifact-backed tool-result masking."""
from __future__ import annotations

import json
import re
from dataclasses import replace

from ..methods import CompactionContext, MethodUnavailable, SummaryRequest
from ..params import Params
from ..state import MessageEdit
from .accounting import _text_parts
from .byte_estimator import response_items
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


class ChatSummary:
    kind = "chat_summary"

    def __init__(self, params: Params) -> None:
        self.system = resolve_prompt(params.value("system", ""), params, "system")
        self.prompt = resolve_prompt(params.value("prompt"), params, "prompt")
        self.max_tokens = params.int("max_tokens", None, minimum=1)
        self.empty_fallback = params.str("empty_fallback", None)
        self.normalize_tags = params.bool("normalize_tags", False)
        self.system_from_history = params.bool("system_from_history", False)
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
            users = selection.details.get("retained_messages", ())
            native = replace(record.native, items=tuple(item for user in users for item in response_items(user)) + record.native.items)
            return Reduction(native=native)
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
                                 max_tokens=self.max_tokens, purpose="summary", model=context.target.model)
        text = context.summarizer.complete(request).text
        if not text and self.empty_fallback is not None:
            text = self.empty_fallback
        if self.normalize_tags:
            text = normalize_xml_summary(text)
        return Reduction(summary=text)


class MaskToolOutputs:
    kind = "mask_tool_outputs"

    def __init__(self, params: Params) -> None:
        self.placeholder = resolve_prompt(params.value("placeholder"), params, "placeholder")
        template = params.value("artifact_template", None)
        self.artifact_template = None if template is None else resolve_prompt(template, params, "artifact_template")
        params.done()

    def reduce(self, context: CompactionContext, selection: Selection) -> Reduction:
        if not selection.targets:
            raise MethodUnavailable("No selected tool outputs")
        prior = {e.index: e.message for r in context.state.records for e in r.edits}
        edits = []
        for index in selection.targets:
            message = dict(prior.get(index, context.messages[index]))
            content = message.get("content")
            replacement = self.placeholder
            textual = isinstance(content, str) or (isinstance(content, list) and all(p.get("type") == "text" for p in content))
            if self.artifact_template is not None and context.artifacts is not None and textual:
                call_id = str(message.get("tool_call_id") or index)
                is_text = isinstance(content, str)
                try:
                    path = context.artifacts.store(call_id + (".txt" if is_text else ".json"),
                                                   content if is_text else json.dumps(content, ensure_ascii=False),
                                                   "text/plain" if is_text else "application/json")
                    replacement = self.artifact_template.replace("{{filepath}}", path)
                except Exception:
                    # The selected policy intentionally drops content if persistence fails.
                    replacement = self.placeholder
            message["content"] = replacement
            message.pop("toolUseResult", None)
            edits.append(MessageEdit(index, message))
        return Reduction(edits=tuple(edits))
