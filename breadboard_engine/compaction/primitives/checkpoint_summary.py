"""A single tool-free checkpoint request with explicit invalid-output guards."""

import re

from ..methods import MethodUnavailable, SummaryRequest
from .protected_windows import visible_messages


class SummaryFailure(RuntimeError):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


class CheckpointSummary:
    kind = "checkpoint_summary"

    def __init__(self, params):
        from .reducers import resolve_prompt
        self.prompts = {}
        prompts = params.child(params.mapping("prompts"), "prompts")
        for key in ("initial_user", "initial_no_user", "update_user", "update_no_user"):
            self.prompts[key] = resolve_prompt(prompts.value(key), prompts, key)
        prompts.done()
        self.provider = params.str("provider", "auto")
        self.model = params.str("model", None)
        self.content_max = params.int("content_max", 6000, minimum=1)
        self.content_head = params.int("content_head", 4000, minimum=0)
        self.content_tail = params.int("content_tail", 2000, minimum=0)
        params.done()

    def reduce(self, context, selection):
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        messages = visible_messages(context)
        turns = [messages[i] for i in selection.summarize]
        has_user = any(m.get("role") == "user" and str(m.get("content") or "").strip() for m in turns)
        parts = []
        for message in turns:
            role = message.get("role", "unknown")
            content = message.get("content") or ""
            if isinstance(content, (list, tuple)):
                content = "\n".join(p if isinstance(p, str) else str(p.get("text") or "[image]") for p in content)
            content = re.sub(r"MEDIA:\S+", "[media attachment]", str(content))
            if role == "assistant":
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
            if len(content) > self.content_max:
                content = content[:self.content_head] + "\n...[truncated]...\n" + content[-self.content_tail:]
            if role == "tool":
                parts.append(f"[TOOL RESULT {message.get('tool_call_id', '')}]: {content}")
            else:
                if role == "assistant" and message.get("tool_calls"):
                    calls = []
                    for call in message["tool_calls"]:
                        function = call.get("function") or call
                        calls.append(f"{function.get('name', '')}({str(function.get('arguments', ''))[:1500]})")
                    content += "\n[Tool calls:\n" + "\n".join(calls) + "\n]"
                parts.append(f"[{role.upper()}]: {content}")
        previous = context.state.latest_readable_boundary()
        budget = max(2000, min(int(context.context_window * 0.05), 10000,
                               int(sum(len(p) for p in parts) / 4 * 0.2)))
        return self.reduce_text(context, "\n\n".join(parts), previous.summary if previous else "", budget, has_user)

    def reduce_text(self, context, conversation, previous_summary, summary_budget, has_user_turn):
        from .reducers import Reduction
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        key = ("update" if previous_summary else "initial") + ("_user" if has_user_turn else "_no_user")
        values = {"conversation": conversation, "previous_summary": previous_summary,
                  "summary_budget": str(summary_budget + 1500)}
        prompt = re.sub(r"\{\{(conversation|previous_summary|summary_budget)\}\}",
                        lambda match: values[match[1]] or "", self.prompts[key])
        model = self.model or context.settings.summary_model or context.target.model
        response = context.summarizer.complete(SummaryRequest(None, ({"role": "user", "content": prompt},),
                                                             None, "summary", model))
        where = f"(provider={self.provider} model={model})"
        if not response.text.strip():
            raise SummaryFailure("empty_content", f"Context compression LLM returned empty content {where}")
        if response.finish_reason == "length":
            raise SummaryFailure("length_truncated", "Context compression summary was truncated (finish_reason=length): "
                                 f"generation hit the output token cap and the summary is incomplete {where}")
        return Reduction(summary=response.text, details={"has_user_turn": has_user_turn})
