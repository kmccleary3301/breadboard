"""A single tool-free checkpoint request with explicit invalid-output guards."""

from collections.abc import Mapping
import re

from ..methods import MethodUnavailable, SummaryRequest
from ..transcript import anthropic_replay_conversation, media_placeholder, result_call_ids, result_parts
from .protected_windows import visible_messages



def _checkpoint_part_text(part):
    if isinstance(part, str):
        return part
    if not isinstance(part, Mapping):
        return "[image]"
    placeholder = media_placeholder(part)
    if placeholder is not None:
        return placeholder
    kind = part.get("type")
    if kind == "text" and isinstance(part.get("text"), str):
        return part["text"]
    return str(part.get("text") or "[image]")


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
        previous = context.state.latest_readable_boundary()
        carrier = previous.details.get("summary_carrier_index") if previous else None
        turns = [messages[i] for i in selection.summarize]
        turn_replays = [(message, anthropic_replay_conversation(message)) for message in turns]
        has_user = (
            bool(previous and previous.details.get("summary_has_user_turn"))
            or any(
                i != carrier and messages[i].get("role") == "user"
                and str(messages[i].get("content") or "").strip()
                for i in selection.summarize
            )
            or any(
                replay is not None and any(item.get("role") == "user" for item in replay)
                for _, replay in turn_replays
            )
        )
        parts = []
        for original_message, replay in turn_replays:
            if replay is None:
                expanded = (original_message,)
            elif any(item.get("role") == "user" for item in replay):
                expanded = replay
            else:
                flattened_message = dict(original_message)
                flattened_message["content"] = [
                    part for replay_message in replay for part in replay_message.get("content", [])
                ]
                expanded = (flattened_message,)
            rendered = []
            for message in expanded:
                role = message.get("role", "unknown")
                content = message.get("content") or ""
                if isinstance(content, (list, tuple)):
                    blocks = result_parts(list(content) if isinstance(content, tuple) else content) \
                        if role in ("tool", "tool_result") else content
                    content = "\n".join(_checkpoint_part_text(block) for block in blocks)
                if role == "assistant":
                    content = re.sub(r"<think>.*?</think>", "", str(content), flags=re.S)
                if role in ("tool", "tool_result"):
                    label = f"[TOOL RESULT {message.get('tool_call_id') or ','.join(result_call_ids(message))}]"
                else:
                    if role == "assistant" and message.get("tool_calls"):
                        calls = []
                        for call in message["tool_calls"]:
                            function = call.get("function") or call
                            calls.append(f"{function.get('name', '')}({str(function.get('arguments', ''))[:1500]})")
                        content = str(content) + "\n[Tool calls:\n" + "\n".join(calls) + "\n]"
                    label = f"[{role.upper()}]"
                rendered.append(f"{label}: {content}")
                if replay is None and len(content) > self.content_max:
                    rendered[-1] = f"{label}: {content[:self.content_head]}\n...[truncated]...\n{content[-self.content_tail:]}"
            conversation = "\n".join(rendered)
            if replay is not None and len(conversation) > self.content_max:
                conversation = conversation[:self.content_head] + "\n...[truncated]...\n" + conversation[-self.content_tail:]
            parts.append(conversation)
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
