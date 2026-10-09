"""Summary carrier placement that preserves template-visible role alternation."""

from dataclasses import dataclass
from ..state import MessageEdit
from .protected_windows import visible_messages


@dataclass(frozen=True)
class PlacementResult:
    messages: tuple
    edits: tuple = ()


class AlternationTemplate:
    kind = "alternation_template"

    def __init__(self, params):
        from .placement import _template_source
        self.marker = _template_source(params.value("end_marker"), params, "end_marker")
        self.header = _template_source(params.value("prior_header"), params, "prior_header")
        self.delimiter = _template_source(params.value("merge_delimiter"), params, "merge_delimiter")
        params.done()

    def place(self, context, selection, reduction):
        messages = visible_messages(context)
        head = messages[:selection.prefix_end or 0]
        first = selection.first_kept_index
        tail = messages[first:]
        visible = lambda m: m.get("role") if m.get("role") in {"user", "assistant", "system"} else None
        last_role = next((visible(m) for m in reversed(head) if visible(m)), None)
        tail_index = next((i for i, m in enumerate(tail) if visible(m)), None)
        tail_role = visible(tail[tail_index]) if tail_index is not None else None
        force_user = not head or last_role in {None, "system"} or not any(
            m.get("role") == "user" and str(m.get("content") or "").strip() for m in [*head, *tail])
        role = "user" if last_role in {None, "assistant", "system"} or force_user else "assistant"
        merge = False
        if role == tail_role:
            flipped = "assistant" if role == "user" else "user"
            if flipped != last_role and last_role is not None and not force_user:
                role = flipped
            else:
                merge = True
        flags = {"_compressed_summary": True,
                 "_compressed_summary_has_user_turn": bool(reduction.details.get("has_user_turn"))}
        summary = reduction.summary or ""
        if not merge:
            return PlacementResult(({"role": role, "content": summary + "\n\n" + self.marker, **flags},))
        message = dict(tail[tail_index])
        old = message.get("content") or ""
        prefix = summary + "\n\n" + self.marker + "\n\n" if force_user else self.header + "\n"
        suffix = "" if force_user else "\n\n" + self.delimiter + "\n\n" + summary + "\n\n" + self.marker
        if isinstance(old, str):
            message["content"] = prefix + old + suffix
        else:
            message["content"] = [{"type": "text", "text": prefix}, *old]
            if suffix:
                message["content"].append({"type": "text", "text": suffix})
        message.update(flags)
        message.pop("api_content", None)
        # The merged row is the boundary's carrier; consume it once, not once as
        # a synthetic summary and again as a retained original.
        return PlacementResult((), (MessageEdit(first + tail_index, message),))
