"""Anthropic on-demand compaction and native replay port.

Port of OMP:
- packages/agent/src/compaction/anthropic.ts
- packages/ai/src/providers/anthropic-compaction.ts
- packages/ai/src/providers/anthropic-wire.ts (lines 160-200)
- packages/ai/src/providers/anthropic.ts
- packages/agent/src/compaction/prompts/anthropic-compaction-instructions.md

Official Anthropic docs:
- https://platform.claude.com/docs/en/build-with-claude/compaction-on-demand
  Beta header: "compact-2026-09-04"
  Legacy threshold compaction beta: "compact-2026-01-12"
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...provider.contracts import ProviderContractError
from ..methods import (
    CompactionCancelled,
    CompactionContext,
    CompactionError,
    MethodUnavailable,
    NativeCompactionError,
    RemoteCompactionPort,
)
from ..state import (
    CompactionRecord,
    NativeCompaction,
    NATIVE_MARKER_KEY,
)
from ..tokens import estimate_messages_tokens
from ..transcript import (
    find_cut_point,
    is_tool_result,
    leading_system_count,
    role_of,
    tool_call_ids,
)

COMPACTION_BETA = "compact-2026-09-04"
LEGACY_COMPACTION_BETA = "compact-2026-01-12"
CONTEXT_MANAGEMENT_BETA = "context-management-2025-06-27"

# Embedded verbatim from packages/agent/src/compaction/prompts/anthropic-compaction-instructions.md
ANTHROPIC_COMPACTION_INSTRUCTIONS_PROMPT = """The conversation above is the complete history to summarize. The API replaces every message in this request with your summary; no message you leave out survives.

{{#if extraContext}}
{{extraContext}}

{{/if}}
{{basePrompt}}
{{#if customInstructions}}

Additional focus: {{customInstructions}}
{{/if}}

You MUST NOT call any tools while writing the summary; respond with the summary text only."""

DEFAULT_SUMMARIZATION_PROMPT = """You MUST summarize the conversation above into a structured handoff summary for another LLM to resume the task.

IMPORTANT: If the conversation ends with an unanswered question or a request awaiting user response (e.g., "Please run command and paste output"), you MUST preserve that exact question/request.

You MUST use this format (sections can be omitted if not applicable):

## Goal
[User goals; list multiple if session covers different tasks.]

## Constraints & Preferences
- [Constraints or requirements mentioned]

## Progress

### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of next actions]

## Critical Context
- [Important data, pending questions, references]

## Additional Notes
[Anything else important not covered above]

You MUST output only the structured summary; you NEVER include extra text.

Sections MUST be kept concise. You MUST preserve exact file paths, function names, error messages, and relevant tool outputs or command results. You MUST include repository state changes (branch, uncommitted changes) if mentioned."""


def build_anthropic_compaction_instructions(
    base_prompt: str = DEFAULT_SUMMARIZATION_PROMPT,
    custom_instructions: Optional[str] = None,
    extra_context: Optional[str] = None,
) -> str:
    """Render compaction instructions replacing the API default summarizer prompt.

    Ports OMP packages/agent/src/compaction/anthropic.ts:buildAnthropicCompactionInstructions.
    """
    parts = [
        "The conversation above is the complete history to summarize. The API replaces every message in this request with your summary; no message you leave out survives.\n"
    ]
    if extra_context and extra_context.strip():
        parts.append(extra_context.strip() + "\n")
    parts.append(base_prompt.strip())
    if custom_instructions and custom_instructions.strip():
        parts.append(f"\nAdditional focus: {custom_instructions.strip()}")
    parts.append("\nYou MUST NOT call any tools while writing the summary; respond with the summary text only.")
    return "\n".join(parts)


def find_anthropic_compaction_cut(
    messages: Sequence[Mapping[str, Any]],
    initial_cut: int,
) -> int:
    """Move initial_cut forward until the boundary alternates wire roles and no tool call is orphaned.

    Ports OMP packages/agent/src/compaction/anthropic.ts:findAnthropicCompactionCut.
    """
    calls: Dict[str, int] = {}
    for i, msg in enumerate(messages):
        if role_of(msg) != "assistant":
            continue
        for call_id in tool_call_ids(msg):
            calls[call_id] = i

    last_result: Dict[int, int] = {}
    if calls:
        for i, msg in enumerate(messages):
            if not is_tool_result(msg):
                continue
            call_id = (
                msg.get("tool_call_id")
                or msg.get("tool_use_id")
                or msg.get("call_id")
                or msg.get("id")
            )
            if isinstance(call_id, str) and call_id in calls:
                call_index = calls[call_id]
                last_result[call_index] = max(last_result.get(call_index, -1), i)

    protected_through = -1
    for i in range(initial_cut):
        if i in last_result:
            protected_through = max(protected_through, last_result[i])

    for cut in range(initial_cut, len(messages)):
        if cut - 1 in last_result:
            protected_through = max(protected_through, last_result[cut - 1])
        if cut <= protected_through:
            continue
        first = messages[cut]
        first_role = role_of(first)
        if first_role in {"system", "developer"}:
            continue
        if cut - 1 < 0:
            continue
        previous = messages[cut - 1]
        prev_role = role_of(previous)
        if (prev_role == "assistant") != (first_role == "assistant"):
            return cut

    return len(messages)


_SUPPORTED_PATTERNS = [
    re.compile(r"^claude-opus-(?:4[-.][6-9]|[5-9])"),
    re.compile(r"^claude-sonnet-(?:4[-.][6-9]|[5-9])"),
    re.compile(r"^claude-haiku-(?:5[-.][5-9]|[6-9])"),
    re.compile(r"^claude-(?:fable|mythos)-(?:[5-9])"),
    re.compile(r"^claude-mythos-preview"),
    re.compile(r"^(?:us\.)?anthropic\.claude-opus-(?:4[-.][6-9]|[5-9])"),
    re.compile(r"^(?:us\.)?anthropic\.claude-sonnet-(?:4[-.][6-9]|[5-9])"),
    re.compile(r"^(?:us\.)?anthropic\.claude-(?:fable|mythos)-(?:[5-9])"),
]

_UNSUPPORTED_PATTERNS = [
    re.compile(r"^claude-haiku-4"),
    re.compile(r"^claude-sonnet-4[-.][0-5]"),
    re.compile(r"^claude-opus-4[-.][0-5]"),
    re.compile(r"^claude-3"),
]


def supports_anthropic_compaction(model: str, endpoint: Optional[str] = None) -> bool:
    """Whether model and endpoint policy support on-demand Anthropic compaction.

    Ports OMP packages/ai/src/providers/anthropic-compaction.ts:supportsAnthropicCompaction.
    """
    model_lower = (model or "").lower()
    for pat in _UNSUPPORTED_PATTERNS:
        if pat.search(model_lower):
            return False
    for pat in _SUPPORTED_PATTERNS:
        if pat.search(model_lower):
            return True
    return False


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "compaction":
                    parts.append(str(item.get("content") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content or "")


class AnthropicCompactionPort:
    """RemoteCompactionPort implementation for Anthropic on-demand compaction."""

    provider: str = "anthropic"
    api: str = "messages"

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: Optional[Any] = None,
        context: Optional[Any] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.runtime = runtime
        self.runtime_context = context

    def supports(self, model: str) -> bool:
        return supports_anthropic_compaction(model)

    def compact(self, context: CompactionContext) -> CompactionRecord:
        if not self.supports(context.target.model):
            raise MethodUnavailable(
                f"Anthropic native compaction is not supported for model {context.target.model!r}"
            )
        if context.target.provider != self.provider or context.target.api != self.api:
            raise MethodUnavailable(
                f"Target {context.target} does not match {self.provider}/{self.api}"
            )

        messages = context.messages
        if not messages:
            raise MethodUnavailable("No messages to compact")

        start = context.state.kept_start(messages)
        cut_point = find_cut_point(
            messages,
            start=start,
            end=len(messages),
            keep_recent_tokens=context.settings.keep_recent_tokens,
        )
        final_cut = find_anthropic_compaction_cut(messages, cut_point.first_kept_index)
        first_kept_index = final_cut

        head = leading_system_count(messages)
        system_text: Optional[str] = None
        if head > 0:
            sys_parts = [_extract_text(m.get("content")) for m in messages[:head]]
            system_text = "\n\n".join(p for p in sys_parts if p)

        boundary = context.state._active_boundary(context.target)
        request_messages: List[Dict[str, Any]] = []

        if boundary is not None:
            if boundary.native is not None and boundary.native.usable_for(context.target):
                items = list(boundary.native.items)
                request_messages.append({"role": "assistant", "content": items})
            elif boundary.summary_messages:
                for sm in boundary.summary_messages:
                    request_messages.append(dict(sm))
            elif boundary.summary:
                request_messages.append({"role": "user", "content": boundary.summary})

        start_idx = int(boundary.first_kept_index) if boundary is not None else head
        for idx in range(start_idx, final_cut):
            request_messages.append(dict(messages[idx]))

        instructions = build_anthropic_compaction_instructions(
            base_prompt=DEFAULT_SUMMARIZATION_PROMPT,
            custom_instructions=context.custom_instructions or context.settings.custom_instructions,
        )

        max_tokens = min(4096, max(1024, int(context.context_window * 0.1)))

        compaction_payload = {
            "type": "summarize",
            "instructions": instructions,
        }

        wire_messages: List[Dict[str, Any]] = []
        if self.runtime is not None and hasattr(self.runtime, "_convert_messages"):
            sys_from_conv, wire_messages = self.runtime._convert_messages(
                request_messages, context=self.runtime_context
            )
            if sys_from_conv:
                system_text = (
                    f"{system_text}\n\n{sys_from_conv}" if system_text else sys_from_conv
                )
        else:
            wire_messages = [dict(m) for m in request_messages]

        try:
            response = self._dispatch_compaction_request(
                model=context.target.model,
                messages=wire_messages,
                system=system_text,
                compaction=compaction_payload,
                max_tokens=max_tokens,
            )
        except CompactionCancelled:
            raise
        except Exception as exc:
            raise NativeCompactionError(
                f"Anthropic server-side compaction failed: {exc}"
            ) from exc

        stop_reason = self._get_attr(response, "stop_reason")
        if stop_reason != "compaction":
            raise NativeCompactionError(
                f"Anthropic compaction response carried no compaction block (stop reason: {stop_reason})"
            )

        raw_content = self._get_attr(response, "content") or []
        compaction_block: Optional[Dict[str, Any]] = None
        for block in raw_content:
            b_type = self._get_attr(block, "type")
            if b_type == "compaction":
                compaction_block = {
                    "type": "compaction",
                    "content": self._get_attr(block, "content") or "",
                }
                sig = self._get_attr(block, "signature")
                if sig:
                    compaction_block["signature"] = sig
                enc = self._get_attr(block, "encrypted_content")
                if enc:
                    compaction_block["encrypted_content"] = enc
                break

        if not compaction_block or not compaction_block.get("content"):
            raise NativeCompactionError("Anthropic compaction returned no summary text")
        if not compaction_block.get("signature") and not compaction_block.get("encrypted_content"):
            raise NativeCompactionError("Anthropic compaction returned no signed summary")

        summary_text = compaction_block["content"]

        usage = self._get_attr(response, "usage")
        tokens_used = self._extract_iteration_tokens(usage)

        native = NativeCompaction(
            provider=self.provider,
            api=self.api,
            model=context.target.model,
            items=(compaction_block,),
            token_estimate=tokens_used,
        )

        summary_messages = (
            {
                "role": "user",
                "content": summary_text,
            },
        )

        return context.new_record(
            method="remote",
            first_kept_index=first_kept_index,
            summary=summary_text,
            short_summary="Remote compaction",
            summary_messages=summary_messages,
            native=native,
            details={
                "provider": self.provider,
                "api": self.api,
                "model": context.target.model,
                "tokens_used": tokens_used,
                "signature": compaction_block.get("signature"),
            },
        )

    def _dispatch_compaction_request(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str],
        compaction: Dict[str, Any],
        max_tokens: int,
    ) -> Any:
        extra_headers = {"anthropic-beta": COMPACTION_BETA}
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "compaction": compaction,
            "extra_headers": extra_headers,
        }
        if system:
            kwargs["system"] = system

        client = self.client
        if hasattr(client, "beta") and hasattr(client.beta, "messages") and callable(getattr(client.beta.messages, "create", None)):
            try:
                return client.beta.messages.create(betas=[COMPACTION_BETA], **kwargs)
            except TypeError:
                return client.beta.messages.create(**kwargs)
        elif hasattr(client, "messages") and callable(getattr(client.messages, "create", None)):
            return client.messages.create(**kwargs)
        elif callable(client):
            return client(**kwargs)
        raise NativeCompactionError("Anthropic client has no messages.create or beta.messages.create")

    def _get_attr(self, obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _extract_iteration_tokens(self, usage: Any) -> int:
        if not usage:
            return 0
        iterations = self._get_attr(usage, "iterations")
        if isinstance(iterations, list) and iterations:
            total = 0
            for item in iterations:
                if self._get_attr(item, "type") == "compaction":
                    inp = self._get_attr(item, "input_tokens") or 0
                    out = self._get_attr(item, "output_tokens") or 0
                    total += inp + out
            if total > 0:
                return total
        input_tokens = self._get_attr(usage, "input_tokens") or 0
        output_tokens = self._get_attr(usage, "output_tokens") or 0
        return input_tokens + output_tokens
