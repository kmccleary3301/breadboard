"""Single-request event summaries and bounded whole-view reset retries."""
from __future__ import annotations

from jinja2 import Environment

from ..methods import CompactionCancelled, MethodUnavailable, SummaryRequest
from ..params import Params, PresetError
from .event_messages import display, summary_message
from .event_selection import NoCondensationAvailableException


class EventSummary:
    kind = "event_summary"

    def __init__(self, params: Params):
        from .reducers import resolve_prompt

        self.template = Environment(autoescape=False).from_string(resolve_prompt(params.value("prompt"), params, "prompt"))
        self.attempts = params.int("attempts", 1, minimum=1)
        self.scaling = params.number("context_scaling", 0.8)
        self.notice = resolve_prompt(params.value("truncate_notice"), params, "truncate_notice")
        params.done()
        if self.scaling is None or not 0 < self.scaling < 1:
            raise PresetError("context_scaling must be in (0, 1)")

    def _truncate(self, text, limit):
        if not limit or len(text) <= limit:
            return text
        if len(self.notice) >= limit:
            return self.notice[:limit]
        remaining = limit - len(self.notice)
        head = (remaining + 1) // 2
        tail = remaining - head
        return text[:head] + self.notice + (text[-tail:] if tail else "")

    def reduce(self, context, selection):
        from .reducers import Reduction

        if context.summarizer is None:
            raise MethodUnavailable("No summarizer configured")
        events = selection.details.get("events", [context.messages[i] for i in selection.summarize])
        strings = [display(event) for event in events]
        limit = None
        original = None
        for _ in range(self.attempts):
            prompt = self.template.render(events=[self._truncate(text, limit) for text in strings]).strip()
            try:
                response = context.summarizer.complete(SummaryRequest(system=None, messages=({"role": "user", "content": prompt},), max_tokens=None, purpose="summary", model=context.settings.summary_model))
                return Reduction(summary=response.text)
            except CompactionCancelled:
                raise
            except Exception as error:
                if original is None:
                    original = error
                limit = int((max(map(len, strings), default=0) if limit is None else limit) * self.scaling)
        raise NoCondensationAvailableException(f"Summarization LLM call failed: {original}") from original


class OffsetSummary:
    """Insert a bare summary after the selector's verbatim prefix."""
    kind = "offset_summary"

    def __init__(self, params: Params):
        self.coalesce_user = params.bool("coalesce_user", True)
        params.done()

    def place(self, context, selection, reduction):
        return [*selection.details.get("synthetic_prefix", ()), summary_message(reduction.summary or "")]
