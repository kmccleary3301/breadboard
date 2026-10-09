"""Protected prefix/tail selection and edit-only target selection."""

from ..methods import MethodUnavailable
from ..state import leading_system_count, is_tool_result
from .accounting import build_estimator


def visible_messages(context):
    messages = list(context.messages)
    for record in context.state.records:
        for edit in record.edits:
            messages[edit.index] = edit.message
    return messages


def align_backward(messages, index):
    if 0 < index < len(messages):
        previous = next((i for i in range(index - 1, -1, -1) if not is_tool_result(messages[i])), -1)
        if previous >= 0 and messages[previous].get("role") == "assistant" and messages[previous].get("tool_calls"):
            return previous
    return index


def align_forward(messages, index):
    while index < len(messages) and is_tool_result(messages[index]):
        index += 1
    return index


class DecayingPrefixTail:
    kind = "decaying_prefix_tail"

    def __init__(self, params):
        self.first = params.int("protect_first_n", 3, minimum=0)
        self.last = params.int("protect_last_n", 20, minimum=0)
        self.mode = params.choice("tail_mode", ("lean", "legacy"), "lean")
        self.fraction = params.number("tail_fraction", 0.025)
        self.minimum = params.int("tail_min", 10000, minimum=0)
        self.maximum = params.int("tail_max", 25000, minimum=0)
        self.ratio = params.number("target_ratio", 0.2)
        self.users = params.int("min_tail_user_messages", 1, minimum=1)
        self.count = build_estimator(params.str("estimator", "bb_chars4"))
        params.done()

    def select(self, context):
        from .selectors import Selection
        messages = visible_messages(context)
        n = len(messages)
        head = leading_system_count(messages)
        previous = context.state.latest_boundary()
        resumed = any(m.get("_compressed_summary") for m in messages[head:head + self.first + 4])
        start = align_forward(messages, min(n, head + (0 if previous or resumed or context.prior_compactions else self.first)))
        start = max(start, context.state.kept_start(messages))
        if start >= n:
            raise MethodUnavailable("Nothing to summarize")
        budget = (max(self.minimum, min(self.maximum, int(context.context_window * self.fraction)))
                  if self.mode == "lean" else int(context.context_window * self.ratio))
        available = max(0, n - start - 1)
        minimum = min(max(3, min(self.last, 8)), max(3, available - 2), available) if available > 1 else 0

        def walk(ceiling, at_break):
            total, cut = 0, n
            for i in range(n - 1, start - 1, -1):
                tokens = self.count([messages[i]])
                if total + tokens > ceiling and n - i >= minimum:
                    return (i if at_break else cut), total
                total += tokens
                cut = i
            return cut, total

        cut, total = walk(int(budget * 1.5), False)
        if cut <= start and 0 < total <= int(budget * 1.5):
            cut, _ = walk(budget, True)
        fallback = n - minimum
        cut = min(cut, fallback)
        if cut <= start:
            cut = max(fallback, start + 1)
        cut = align_backward(messages, cut)
        users = [i for i in range(n - 1, start - 1, -1)
                 if messages[i].get("role") == "user" and str(messages[i].get("content") or "").strip()
                 and not messages[i].get("_compressed_summary")]
        if users:
            latest = users[0]
            if latest < cut:
                cut = max(latest, start + 1)
                if cut > latest:
                    cut = next((i for i in range(latest + 1, n) if messages[i].get("role") == "user"), n)
        assistant = next((i for i in range(n - 1, start - 1, -1)
                          if messages[i].get("role") == "assistant" and not messages[i].get("_compressed_summary")), None)
        if assistant is not None and assistant < cut:
            cut = max(align_backward(messages, assistant), start + 1)
        if self.users > 1 and users:
            cut = min(cut, max(start + 1, users[min(self.users, len(users)) - 1]))
        cut = min(n, align_forward(messages, max(cut, start + 1)))
        return Selection(prefix_end=head if previous else start, first_kept_index=cut,
                         summarize=tuple(range(start, cut)))


class VisibleToolOutputs:
    kind = "visible_tool_outputs"

    def __init__(self, params):
        params.done()

    def select(self, context):
        from .selectors import Selection
        start = context.state.kept_start(context.messages)
        prefix = context.state.prefix_end(context.messages)
        head = leading_system_count(context.messages)
        return Selection(targets=tuple(i for i, m in enumerate(context.messages)
                                       if (head <= i < prefix or i >= start) and is_tool_result(m)))
