"""Handoff compaction: generates a self-contained handoff document.

Ports OMP ``compaction.ts`` (lines 1034-1246) and session maintenance handoff handling
(``session-maintenance.ts`` lines 198-201, 374-385, 1980-2020).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .file_ops import compute_file_lists, extract_file_operations, upsert_file_operations
from .methods import CompactionContext, CompactionRecord, MethodUnavailable, SummaryRequest
from .prompts import (
    render_handoff_prompt,
    render_handoff_summary_context,
)
from .settings import summary_max_tokens
from .transcript import find_cut_point


def generate_handoff(
    messages: Sequence[Mapping[str, Any]],
    context: CompactionContext,
    custom_instructions: Optional[str] = None,
) -> str:
    """Generate a handoff document using the context summarizer."""
    if context.summarizer is None:
        raise MethodUnavailable("No summarizer configured for handoff generation")

    prompt_text = render_handoff_prompt(custom_instructions)
    request_messages = [*messages, {"role": "user", "content": prompt_text}]

    max_tokens = summary_max_tokens(context.context_window, context.settings)
    model_name = context.settings.summary_model or context.target.model

    req = SummaryRequest(
        system="",
        messages=request_messages,
        max_tokens=max_tokens,
        purpose="handoff",
        model=model_name,
    )
    res = context.summarizer.complete(req)
    return res.text


generateHandoff = generate_handoff


class HandoffCompaction:
    """Compaction method that writes a handoff document and keeps recent history."""

    name: str = "handoff"

    def run(self, context: CompactionContext) -> CompactionRecord:
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured for handoff compaction")

        # Handoff appends to the conversation, so it cannot clear an overflow.
        if context.reason == "overflow":
            raise MethodUnavailable("Handoff compaction is unavailable for overflow")

        start = context.state.kept_start(context.messages)
        cut_point = find_cut_point(
            context.messages,
            start=start,
            end=len(context.messages),
            keep_recent_tokens=context.settings.keep_recent_tokens,
        )
        first_kept_index = cut_point.first_kept_index

        if first_kept_index <= start or len(context.messages) < 2:
            raise MethodUnavailable("Nothing to hand off")

        messages_to_summarize = context.messages[start:first_kept_index]
        file_ops = extract_file_operations(messages_to_summarize, context.state.records)

        instructions = (
            "\n\n".join(ci for ci in [context.custom_instructions, context.settings.custom_instructions] if ci)
            or None
        )

        document = generate_handoff(
            messages=context.projected(),
            context=context,
            custom_instructions=instructions,
        )

        read_files, modified_files = compute_file_lists(file_ops)
        summary = upsert_file_operations(document, read_files, modified_files, file_ops.read)

        summary_context_text = render_handoff_summary_context(summary)
        summary_messages = [{"role": "user", "content": summary_context_text}]

        details = {"read_files": read_files, "modified_files": modified_files}

        return context.new_record(
            method=self.name,
            first_kept_index=first_kept_index,
            summary=summary,
            short_summary=None,
            summary_messages=summary_messages,
            details=details,
        )
