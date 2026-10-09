"""Reducer primitives: turn a :class:`Selection` into a :class:`Reduction`.

``summarize`` condenses ``selection.summarize`` (and a split turn's prefix)
with the summary model. Its prompts, transcript labels, escaping, windowing,
token budgets and file-operation block are parameters, so OMP 18.4.5 and Pi
0.73.1 are two parameter sets of one reducer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..file_ops import (
    FileOperations,
    compute_file_lists,
    create_file_ops,
    extract_file_operations,
    extract_file_ops_from_message,
    upsert_file_operations,
)
from ..methods import CompactionContext, MethodUnavailable, NativeCompaction, SummaryRequest
from ..overflow import is_context_overflow
from ..params import Params, PresetError
from ..prompts import render_prompt
from ..serialize import (
    OMP_TRANSCRIPT,
    PI_TRANSCRIPT,
    TranscriptStyle,
    escape_summary_boundary_tags,
    serialize_conversation,
)
from ..settings import MAX_SUMMARY_TOKENS, resolve_budget_reserve_tokens, summary_max_tokens
from ..state import MessageEdit
from ..tokens import estimate_message_tokens, estimate_text_tokens
from .selectors import Selection


@dataclass(frozen=True)
class Reduction:
    summary: Optional[str] = None
    short_summary: Optional[str] = None
    native: Optional[NativeCompaction] = None
    edits: Tuple[MessageEdit, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Prompt references
# --------------------------------------------------------------------------


def resolve_prompt(value: Any, params: Params, key: str) -> str:
    """``{builtin: name}`` → packaged OMP prompt (rendered); ``{file: name}`` →
    preset prompt file, verbatim; a plain string is used as is."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and set(value) == {"builtin"}:
        return render_prompt(str(value["builtin"]))
    if isinstance(value, Mapping) and set(value) == {"file"}:
        if params.env.prompt_dir is None:
            raise PresetError(f"{params.where}.{key}: preset has no prompt directory")
        return params.env.prompt_dir.joinpath(str(value["file"])).read_text(encoding="utf-8")
    raise PresetError(f"{params.where}.{key} must be a string, {{builtin: name}} or {{file: name}}")


# --------------------------------------------------------------------------
# Token budgets for summary calls
# --------------------------------------------------------------------------


class OmpSettingsBudget:
    """OMP summary budgets from settings and the window.

    ``summary``: ``summaryMaxTokens``; ``turn_prefix``: min(16384,
    floor(0.5 * budget reserve)); ``short_summary``: min(512, floor(0.2 *
    budget reserve)). All at least 1.
    """

    def __init__(self, params: Params) -> None:
        self.use = params.choice("use", ("summary", "turn_prefix", "short_summary"))
        params.done()
        self.settings = params.env.settings

    def tokens(self, context_window: int) -> int:
        if self.use == "summary":
            return summary_max_tokens(context_window, self.settings)
        reserve = resolve_budget_reserve_tokens(context_window, self.settings)
        if self.use == "turn_prefix":
            return max(1, min(MAX_SUMMARY_TOKENS, math.floor(0.5 * reserve)))
        return max(1, min(512, math.floor(0.2 * reserve)))


class ReserveFraction:
    """``floor(fraction * reserve)`` (Pi: 0.8 for summaries, 0.5 for turn prefixes)."""

    def __init__(self, params: Params) -> None:
        self.reserve = params.int("reserve", minimum=0)
        self.fraction = params.number("fraction")
        params.done()

    def tokens(self, context_window: int) -> int:
        return math.floor(self.fraction * self.reserve)


_BUDGET_KINDS = {"omp_settings": OmpSettingsBudget, "reserve_fraction": ReserveFraction}


def _build_budget(params: Params) -> Any:
    return _BUDGET_KINDS[params.kind(_BUDGET_KINDS)](params)


# --------------------------------------------------------------------------
# File-operation blocks
# --------------------------------------------------------------------------


def _js_sorted(values: Any) -> list[str]:
    """JavaScript ``Array.prototype.sort`` order (UTF-16 code units)."""
    return sorted(values, key=lambda s: s.encode("utf-16-be", "surrogatepass"))


def _pi_file_ops(messages: Sequence[Mapping[str, Any]], previous: Any) -> FileOperations:
    """Pi ``extractFileOperations`` (``compaction.js:14-37``, ``utils.js:14-43``)."""
    import json

    ops = create_file_ops()
    details = getattr(previous, "details", None) if previous is not None else None
    if isinstance(details, Mapping):
        for name in details.get("read_files") or ():
            ops.read.add(name)
        for name in details.get("modified_files") or ():
            ops.edited.add(name)
    targets = {"read": ops.read, "write": ops.written, "edit": ops.edited}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            if not isinstance(call, Mapping):
                continue
            function = call.get("function") if isinstance(call.get("function"), Mapping) else call
            args = function.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            path = args.get("path") if isinstance(args, Mapping) else None
            bucket = targets.get(str(function.get("name") or ""))
            if isinstance(path, str) and path and bucket is not None:
                bucket.add(path)
    return ops


def _pi_file_lists(ops: FileOperations) -> Tuple[list[str], list[str]]:
    modified = set(ops.edited) | set(ops.written)
    return _js_sorted(f for f in ops.read if f not in modified), _js_sorted(modified)


def _pi_format_file_operations(read_files: Sequence[str], modified_files: Sequence[str]) -> str:
    sections = []
    if read_files:
        sections.append("<read-files>\n" + "\n".join(read_files) + "\n</read-files>")
    if modified_files:
        sections.append("<modified-files>\n" + "\n".join(modified_files) + "\n</modified-files>")
    return "\n\n" + "\n\n".join(sections) if sections else ""


# --------------------------------------------------------------------------
# Summary engine
# --------------------------------------------------------------------------

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


def plan_summary_windows(messages: Sequence[Mapping[str, Any]], budget_tokens: int) -> list[list[Mapping[str, Any]]]:
    """Partition messages into windows that fit ``budget_tokens``."""
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


_TRANSCRIPTS = {"omp": OMP_TRANSCRIPT, "pi": PI_TRANSCRIPT}
DEFAULT_SPLIT_TURN_JOIN = "\n\n---\n\n**Turn Context (split turn):**\n\n"


class Summarize:
    """LLM summary of ``selection.summarize`` plus an optional split-turn prefix.

    ``windowing: fold`` (OMP) serializes once and, when the transcript is over
    the input budget, folds window by window, halving a window on overflow.
    ``single`` (Pi) sends one request. ``escape_boundary_tags`` (OMP) escapes
    ``<conversation>``/``<previous-summary>`` in the transcript and previous
    summary; Pi sends both raw. ``file_ops``: ``omp_files_block`` upserts OMP's
    grouped ``<files>`` block; ``read_modified_tags`` appends Pi's
    ``<read-files>``/``<modified-files>``.
    """

    kind = "summarize"

    def __init__(self, params: Params) -> None:
        self.style: TranscriptStyle = _TRANSCRIPTS[params.choice("transcript", tuple(_TRANSCRIPTS))]
        self.escape = params.bool("escape_boundary_tags")
        self.windowing = params.choice("windowing", ("fold", "single"))
        prompts = params.child(params.mapping("prompts"), "prompts")
        self.system = resolve_prompt(prompts.value("system"), prompts, "system")
        self.initial = resolve_prompt(prompts.value("initial"), prompts, "initial")
        self.update = resolve_prompt(prompts.value("update"), prompts, "update")
        self.turn_prefix = resolve_prompt(prompts.value("turn_prefix"), prompts, "turn_prefix")
        short = prompts.value("short_summary", None)
        self.short_summary_prompt = None if short is None else resolve_prompt(short, prompts, "short_summary")
        prompts.done()
        self.max_tokens = _build_budget(params.child(params.mapping("max_tokens"), "max_tokens"))
        self.turn_prefix_max_tokens = _build_budget(
            params.child(params.mapping("turn_prefix_max_tokens"), "turn_prefix_max_tokens")
        )
        short_budget = params.mapping("short_summary_max_tokens", None)
        self.short_summary_max_tokens = (
            None if short_budget is None else _build_budget(params.child(short_budget, "short_summary_max_tokens"))
        )
        if (self.short_summary_prompt is None) != (self.short_summary_max_tokens is None):
            raise PresetError(f"{params.where}: short_summary prompt and short_summary_max_tokens go together")
        self.file_ops = params.choice("file_ops", ("omp_files_block", "read_modified_tags", "none"))
        self.split_turn_join = params.str("split_turn_join", DEFAULT_SPLIT_TURN_JOIN)
        self.empty_history = params.str("empty_history", "No prior history.")
        params.done()

    # -- requests -------------------------------------------------------

    def _transcript(self, messages: Sequence[Mapping[str, Any]]) -> str:
        text = serialize_conversation(messages, self.style)
        return escape_summary_boundary_tags(text) if self.escape else text

    def _previous_block(self, previous_summary: str) -> str:
        body = escape_summary_boundary_tags(previous_summary) if self.escape else previous_summary
        return f"<previous-summary>\n{body}\n</previous-summary>\n\n"

    def _complete(self, context: CompactionContext, prompt: str, max_tokens: int, purpose: str) -> str:
        assert context.summarizer is not None
        request = SummaryRequest(
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            purpose=purpose,
            model=context.settings.summary_model or context.target.model,
        )
        return context.summarizer.complete(request).text

    def _summarize_window(
        self,
        context: CompactionContext,
        conversation: str,
        previous_summary: Optional[str],
        max_tokens: int,
    ) -> str:
        base = self.update if previous_summary else self.initial
        if context.custom_instructions:
            base = f"{base}\n\nAdditional focus: {context.custom_instructions}"
        prompt = f"<conversation>\n{conversation}\n</conversation>\n\n"
        if previous_summary:
            prompt += self._previous_block(previous_summary)
        prompt += base
        return self._complete(context, prompt, max_tokens, "update_summary" if previous_summary else "summary")

    def _history_summary(
        self,
        context: CompactionContext,
        messages: Sequence[Mapping[str, Any]],
        previous_summary: Optional[str],
        max_tokens: int,
    ) -> str:
        if self.windowing == "single":
            return self._summarize_window(context, self._transcript(messages), previous_summary, max_tokens)
        window = context.context_window if context.context_window > 0 else 200_000
        min_tokens = min_summary_input_tokens(window)
        reserve_cap = min(MAX_SUMMARY_TOKENS, math.floor(window * 0.2)) if window < 30_000 else MAX_SUMMARY_TOKENS
        budget = max(min_tokens, math.floor(window * 0.8) - max_tokens - reserve_cap)
        whole = self._transcript(messages)
        if estimate_text_tokens(whole) <= budget:
            pending: list[Dict[str, Any]] = [{"messages": list(messages), "budget": budget, "text": whole}]
        else:
            pending = [{"messages": w, "budget": budget} for w in plan_summary_windows(messages, budget)]
        carried = previous_summary
        while pending:
            current = pending[0]
            text = current.get("text") or self._transcript(current["messages"])
            tokens = estimate_text_tokens(text)
            if tokens > current["budget"]:
                text = clamp_conversation_to_budget(text, current["budget"], tokens)
            try:
                carried = self._summarize_window(context, text, carried, max_tokens)
            except Exception as exc:
                if not is_context_overflow(exc):
                    raise
                halved = math.floor(min(current["budget"], tokens) / 2)
                if halved < min_tokens:
                    raise
                pending[0:1] = [{"messages": w, "budget": halved} for w in plan_summary_windows(current["messages"], halved)]
                continue
            pending.pop(0)
        return carried or ""

    # -- reducer --------------------------------------------------------

    def reduce(self, context: CompactionContext, selection: Selection) -> Reduction:
        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        messages = context.messages
        to_summarize = [messages[i] for i in selection.summarize]
        prefix = [messages[i] for i in selection.turn_prefix]
        previous = context.state.latest_readable_boundary()
        previous_summary = previous.summary if previous is not None else None
        window = context.context_window

        # A split turn with no earlier history gets the placeholder; otherwise
        # the history is summarized even when empty (Pi ``compaction.js:568-577``).
        if to_summarize or not prefix:
            summary = self._history_summary(context, to_summarize, previous_summary, self.max_tokens.tokens(window))
        else:
            summary = self.empty_history
        if prefix:
            prefix_prompt = f"<conversation>\n{self._transcript(prefix)}\n</conversation>\n\n{self.turn_prefix}"
            prefix_summary = self._complete(
                context, prefix_prompt, self.turn_prefix_max_tokens.tokens(window), "turn_prefix"
            )
            summary = f"{summary}{self.split_turn_join}{prefix_summary}"

        short_summary = None
        if self.short_summary_prompt is not None and self.short_summary_max_tokens is not None:
            kept = messages[selection.first_kept_index :] if selection.first_kept_index is not None else []
            short_prompt = f"<conversation>\n{self._transcript(kept)}\n</conversation>\n\n"
            if summary:
                short_prompt += self._previous_block(summary)
            short_prompt += self.short_summary_prompt
            short_summary = self._complete(
                context, short_prompt, self.short_summary_max_tokens.tokens(window), "short_summary"
            )

        details: Dict[str, Any] = {}
        if self.file_ops == "omp_files_block":
            ops = extract_file_operations(to_summarize, context.state.records)
            for message in prefix:
                extract_file_ops_from_message(message, ops)
            read_files, modified_files = compute_file_lists(ops)
            summary = upsert_file_operations(summary, read_files, modified_files, ops.read)
            details = {"read_files": read_files, "modified_files": modified_files}
        elif self.file_ops == "read_modified_tags":
            ops = _pi_file_ops([*to_summarize, *prefix], context.state.latest_boundary())
            read_files, modified_files = _pi_file_lists(ops)
            summary += _pi_format_file_operations(read_files, modified_files)
            details = {"read_files": read_files, "modified_files": modified_files}
        return Reduction(summary=summary, short_summary=short_summary, details=details)


REDUCER_KINDS = {Summarize.kind: Summarize}


def build_reducer(params: Params) -> Any:
    kind = params.kind(REDUCER_KINDS)
    return REDUCER_KINDS[kind](params)
