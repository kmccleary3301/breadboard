"""LLM-backed soft compaction (summarization).

Ports OMP ``compaction.ts`` (lines 859-1034, 1340-1583, 1583-end): creates an LLM
summary of older transcript messages while retaining recent turns and pairing tool calls.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from .file_ops import (
    compute_file_lists,
    extract_file_operations,
    extract_file_ops_from_message,
    upsert_file_operations,
)
from .methods import CompactionContext, CompactionRecord, MethodUnavailable, SummaryRequest
from .overflow import is_context_overflow
from .prompts import (
    SHORT_SUMMARY_PROMPT,
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SYSTEM_PROMPT,
    TURN_PREFIX_SUMMARIZATION_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT,
    render_compaction_summary_context,
)
from .serialize import (
    escape_summary_boundary_tags,
    serialize_conversation_for_summary,
)
from .settings import (
    MAX_SUMMARY_TOKENS,
    resolve_budget_reserve_tokens,
    summary_max_tokens,
)
from .tokens import estimate_message_tokens, estimate_text_tokens
from .transcript import find_cut_point

MIN_SUMMARY_INPUT_TOKENS = 16_384


def min_summary_input_tokens(context_window: int) -> int:
    """Smallest window worth planning; below this, overflow recovery gives up."""
    window = context_window if context_window > 0 else 200_000
    return min(MIN_SUMMARY_INPUT_TOKENS, max(64, math.floor(window / 64)))


def clamp_conversation_to_budget(text: str, budget_tokens: int, tokens: int) -> str:
    """Proportionally clamp a single oversized message string to fit the budget."""
    if tokens <= budget_tokens:
        return text
    keep = max(1024, math.floor((len(text) * budget_tokens * 0.95) / tokens))
    if keep >= len(text):
        return text
    return f"{text[:keep]}\n\n[... {len(text) - keep} more characters truncated]"


def plan_summary_windows(
    messages: Sequence[Mapping[str, Any]],
    budget_tokens: int,
) -> list[list[Mapping[str, Any]]]:
    """Partition messages into windows that fit budget_tokens."""
    windows: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_tokens = 0
    for message in messages:
        tokens = estimate_message_tokens(message)
        if current_tokens > 0 and current_tokens + tokens > budget_tokens:
            windows.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += tokens
    if current:
        windows.append(current)
    return windows


def summarize_conversation_window(
    conversation_text: str,
    previous_summary: Optional[str],
    context: CompactionContext,
    max_tokens: int,
    custom_instructions: Optional[str] = None,
) -> str:
    """Run one summarization completion over a single window."""
    base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    if previous_summary:
        prompt_text += (
            f"<previous-summary>\n{escape_summary_boundary_tags(previous_summary)}\n</previous-summary>\n\n"
        )
    prompt_text += base_prompt

    model_name = context.settings.summary_model or context.target.model
    purpose = "update_summary" if previous_summary else "summary"
    req = SummaryRequest(
        system=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt_text}],
        max_tokens=max_tokens,
        purpose=purpose,
        model=model_name,
    )
    assert context.summarizer is not None
    res = context.summarizer.complete(req)
    return res.text


def generate_summary(
    messages: Sequence[Mapping[str, Any]],
    context: CompactionContext,
    max_tokens: int,
    previous_summary: Optional[str] = None,
    custom_instructions: Optional[str] = None,
) -> str:
    """Fold a sequence of messages into a summary across windows if oversized."""
    window = context.context_window if context.context_window > 0 else 200_000
    min_tokens = min_summary_input_tokens(window)
    reserve_cap = min(MAX_SUMMARY_TOKENS, math.floor(window * 0.2)) if window < 30_000 else MAX_SUMMARY_TOKENS
    budget_tokens = max(min_tokens, math.floor(window * 0.8) - max_tokens - reserve_cap)

    whole_conversation = serialize_conversation_for_summary(messages)
    whole_tokens = estimate_text_tokens(whole_conversation)

    if whole_tokens <= budget_tokens:
        pending: list[dict[str, Any]] = [
            {"messages": list(messages), "budget": budget_tokens, "text": whole_conversation}
        ]
    else:
        windows = plan_summary_windows(messages, budget_tokens)
        pending = [{"messages": w, "budget": budget_tokens} for w in windows]

    carried_summary = previous_summary
    while pending:
        curr = pending[0]
        text = curr.get("text") or serialize_conversation_for_summary(curr["messages"])
        tokens = estimate_text_tokens(text)
        b = curr["budget"]
        if tokens > b:
            text = clamp_conversation_to_budget(text, b, tokens)

        try:
            carried_summary = summarize_conversation_window(
                conversation_text=text,
                previous_summary=carried_summary,
                context=context,
                max_tokens=max_tokens,
                custom_instructions=custom_instructions,
            )
        except Exception as exc:
            if not is_context_overflow(exc):
                raise
            sent_tokens = tokens
            halved = math.floor(min(b, sent_tokens) / 2)
            if halved < min_tokens:
                raise
            new_windows = plan_summary_windows(curr["messages"], halved)
            pending[0:1] = [{"messages": w, "budget": halved} for w in new_windows]
            continue

        pending.pop(0)

    return carried_summary or ""


def generate_turn_prefix_summary(
    messages: Sequence[Mapping[str, Any]],
    context: CompactionContext,
    max_tokens: int,
) -> str:
    """Summarize a turn prefix when a turn is split across the cut point."""
    conv_text = serialize_conversation_for_summary(messages)
    prompt_text = f"<conversation>\n{conv_text}\n</conversation>\n\n{TURN_PREFIX_SUMMARIZATION_PROMPT}"
    model_name = context.settings.summary_model or context.target.model
    req = SummaryRequest(
        system=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt_text}],
        max_tokens=max_tokens,
        purpose="turn_prefix",
        model=model_name,
    )
    assert context.summarizer is not None
    res = context.summarizer.complete(req)
    return res.text


def generate_short_summary(
    recent_messages: Sequence[Mapping[str, Any]],
    history_summary: Optional[str],
    context: CompactionContext,
    max_tokens: int,
) -> str:
    """Generate a PR-style 2-3 sentence short summary."""
    conv_text = serialize_conversation_for_summary(recent_messages)
    prompt_text = f"<conversation>\n{conv_text}\n</conversation>\n\n"
    if history_summary:
        prompt_text += (
            f"<previous-summary>\n{escape_summary_boundary_tags(history_summary)}\n</previous-summary>\n\n"
        )
    prompt_text += SHORT_SUMMARY_PROMPT
    model_name = context.settings.summary_model or context.target.model
    req = SummaryRequest(
        system=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt_text}],
        max_tokens=max_tokens,
        purpose="short_summary",
        model=model_name,
    )
    assert context.summarizer is not None
    res = context.summarizer.complete(req)
    return res.text


class SoftCompaction:
    """Compaction method that summarizes earlier turns with an LLM."""

    name: str = "soft"

    def __init__(self) -> None:
        pass

    def run(self, context: CompactionContext) -> CompactionRecord:
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured for soft compaction")

        start = context.state.kept_start(context.messages)
        cut_point = find_cut_point(
            context.messages,
            start=start,
            end=len(context.messages),
            keep_recent_tokens=context.settings.keep_recent_tokens,
        )
        first_kept_index = cut_point.first_kept_index

        history_end = cut_point.turn_start_index if cut_point.is_split_turn else first_kept_index
        messages_to_summarize = context.messages[start:history_end]
        turn_prefix_messages = (
            context.messages[cut_point.turn_start_index:first_kept_index]
            if cut_point.is_split_turn
            else []
        )
        recent_messages = context.messages[first_kept_index:]

        if not messages_to_summarize and not turn_prefix_messages:
            raise MethodUnavailable("Nothing to summarize")

        file_ops = extract_file_operations(messages_to_summarize, context.state.records)
        if cut_point.is_split_turn:
            for msg in turn_prefix_messages:
                extract_file_ops_from_message(msg, file_ops)

        prev_boundary = context.state.latest_readable_boundary()
        previous_summary = prev_boundary.summary if prev_boundary else None

        instructions = (
            "\n\n".join(ci for ci in [context.custom_instructions, context.settings.custom_instructions] if ci)
            or None
        )

        max_tokens = summary_max_tokens(context.context_window, context.settings)

        if not messages_to_summarize:
            history_summary = "No prior history."
        else:
            history_summary = generate_summary(
                messages=messages_to_summarize,
                context=context,
                max_tokens=max_tokens,
                previous_summary=previous_summary,
                custom_instructions=instructions,
            )

        if cut_point.is_split_turn:
            turn_prefix_max_tokens = max(
                1,
                min(
                    MAX_SUMMARY_TOKENS,
                    math.floor(0.5 * resolve_budget_reserve_tokens(context.context_window, context.settings)),
                ),
            )
            turn_prefix_summary = generate_turn_prefix_summary(
                messages=turn_prefix_messages,
                context=context,
                max_tokens=turn_prefix_max_tokens,
            )
            summary = f"{history_summary}\n\n---\n\n**Turn Context (split turn):**\n\n{turn_prefix_summary}"
        else:
            summary = history_summary

        short_summary_max_tokens = max(
            1,
            min(
                512,
                math.floor(0.2 * resolve_budget_reserve_tokens(context.context_window, context.settings)),
            ),
        )
        short_summary = generate_short_summary(
            recent_messages=recent_messages,
            history_summary=summary,
            context=context,
            max_tokens=short_summary_max_tokens,
        )

        read_files, modified_files = compute_file_lists(file_ops)
        summary = upsert_file_operations(summary, read_files, modified_files, file_ops.read)

        summary_context_text = render_compaction_summary_context(summary)
        summary_messages = [{"role": "user", "content": summary_context_text}]

        details = {"read_files": read_files, "modified_files": modified_files}

        return context.new_record(
            method=self.name,
            first_kept_index=first_kept_index,
            summary=summary,
            short_summary=short_summary,
            summary_messages=summary_messages,
            details=details,
        )
