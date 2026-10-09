"""Token accounting primitives: estimators and occupancy policies.

An *estimator* counts tokens for chat-format messages without a provider.
An *occupancy* policy turns the last provider usage plus the history into the
context size a trigger compares against its limit. Harnesses differ in both,
so presets choose them by kind name.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from ..params import Params
from ..tokens import context_tokens_from_usage, estimate_messages_tokens

Message = Mapping[str, Any]
Estimator = Callable[[Sequence[Message]], int]


# --------------------------------------------------------------------------
# Usage normalization
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    """Provider usage in one vocabulary. ``total`` is as reported (0 if absent)."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0
    raw_context: Optional[int] = None
    """BreadBoard v1 reading (prompt plus completion, cache-aware)."""


def _num(source: Mapping[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = source.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def normalize_usage(usage: Any) -> Optional[Usage]:
    """Map OpenAI Chat, Responses, Anthropic or oracle-case usage to :class:`Usage`."""
    if usage is None:
        return None
    raw_context = context_tokens_from_usage(usage)  # v1 reading, from the object as given
    if not isinstance(usage, Mapping):
        usage = {
            name: getattr(usage, name)
            for name in (
                "prompt_tokens",
                "input_tokens",
                "completion_tokens",
                "output_tokens",
                "total_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
            if getattr(usage, name, None) is not None
        }
    input_tokens = _num(usage, "input_tokens", "prompt_tokens")
    if input_tokens is None and raw_context is None and _num(usage, "total_tokens") is None:
        return None
    cache_read = _num(usage, "cache_read_tokens", "cache_read_input_tokens") or 0
    cache_write = _num(usage, "cache_write_tokens", "cache_creation_input_tokens") or 0
    return Usage(
        input=input_tokens or 0,
        output=_num(usage, "output_tokens", "completion_tokens") or 0,
        cache_read=cache_read,
        cache_write=cache_write,
        total=_num(usage, "total_tokens") or 0,
        raw_context=raw_context,
    )


# --------------------------------------------------------------------------
# Estimators
# --------------------------------------------------------------------------


def _utf16_len(text: str) -> int:
    """JavaScript ``String.length``: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def _js_json(value: Any) -> str:
    """``JSON.stringify`` of a parsed value (compact separators, no ASCII escaping)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            part["text"]
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text" and isinstance(part.get("text"), str)
        ]
    return []


def _image_parts(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for part in content
        if isinstance(part, Mapping) and part.get("type") in {"image_url", "image", "input_image"}
    )


def _pi_tool_call_chars(call: Mapping[str, Any]) -> int:
    function = call.get("function") if isinstance(call.get("function"), Mapping) else call
    name = str(function.get("name") or "")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except ValueError:
            return _utf16_len(name) + _utf16_len(json.dumps(arguments, ensure_ascii=False))
    return _utf16_len(name) + _utf16_len(_js_json(arguments if arguments is not None else {}))


def pi_estimate_message(message: Message) -> int:
    """Pi ``estimateTokens`` (``core/compaction/compaction.js:161-221``) on chat messages.

    User images are free, tool-result images cost 4800 chars, assistant
    reasoning counts as thinking, tool-call arguments are measured as
    ``JSON.stringify`` of the parsed object, lengths are UTF-16 units.
    System messages count 0: Pi keeps the system prompt outside session
    entries, so its estimate never sees it (unknown roles return 0).
    """
    role = message.get("role")
    chars = 0
    content = message.get("content")
    if role == "user":
        chars = sum(_utf16_len(text) for text in _text_parts(content))
    elif role == "assistant":
        chars = sum(_utf16_len(text) for text in _text_parts(content))
        for key in ("reasoning_content", "reasoning"):
            value = message.get(key)
            if isinstance(value, str):
                chars += _utf16_len(value)
        for call in message.get("tool_calls") or ():
            if isinstance(call, Mapping):
                chars += _pi_tool_call_chars(call)
    elif role in {"tool", "function"}:
        chars = sum(_utf16_len(text) for text in _text_parts(content))
        chars += 4800 * _image_parts(content)
    else:
        return 0
    return math.ceil(chars / 4)


def pi_estimate_messages(messages: Sequence[Message]) -> int:
    return sum(pi_estimate_message(m) for m in messages if isinstance(m, Mapping))


ESTIMATORS: Dict[str, Estimator] = {
    "bb_chars4": estimate_messages_tokens,
    "pi_chars4": pi_estimate_messages,
}


def build_estimator(name: str) -> Estimator:
    try:
        return ESTIMATORS[name]
    except KeyError:
        raise ValueError(f"unknown estimator {name!r}; expected one of {sorted(ESTIMATORS)}") from None


# --------------------------------------------------------------------------
# Occupancy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OccupancyInput:
    """``messages`` is the projected request view; ``usage`` the last response's."""

    messages: Sequence[Message]
    usage: Optional[Usage]
    usage_fresh: bool


@dataclass(frozen=True)
class Occupancy:
    tokens: int
    source: str
    """``usage``, ``estimate``, ``usage+trailing`` or ``max(usage,estimate)``."""


def _usage_total(usage: Usage, total_rule: str) -> int:
    if total_rule == "bb_context":
        return max(0, usage.raw_context or 0)
    components = usage.input + usage.output + usage.cache_read + usage.cache_write
    if total_rule == "total_or_components":
        return usage.total or components
    if total_rule == "components":
        return components
    raise ValueError(f"unknown usage total rule {total_rule!r}")


_TOTAL_RULES = ("bb_context", "total_or_components", "components")


def _last_index(messages: Sequence[Message], role: str) -> Optional[int]:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == role:
            return index
    return None


class MaxUsageEstimate:
    """OMP ``compactionContextTokens``: max(provider occupancy, view estimate)."""

    kind = "max_usage_estimate"

    def __init__(self, params: Params) -> None:
        self.estimate = build_estimator(params.str("estimator", "bb_chars4"))
        params.done()

    def measure(self, data: OccupancyInput) -> Optional[Occupancy]:
        estimate = self.estimate(data.messages)
        provider = data.usage.raw_context if (data.usage is not None and data.usage_fresh) else None
        if provider is None:
            return Occupancy(estimate, "estimate")
        return Occupancy(max(provider, estimate), "max(usage,estimate)")


_TRAILING = ("all", "non_user", "none")


class UsagePlusTrailing:
    """Last usage plus estimates of the messages after the last assistant message.

    Pi ``estimateContextTokens`` (``compaction.js:120-145``) adds every
    trailing message (``trailing: all``). Pi's post-run threshold check reads
    the assistant usage alone (``calculateContextTokens``,
    ``agent-session.js:1441``): ``trailing: none``. ``non_user`` drops
    trailing user messages (input not yet sent).

    ``without_usage``: with no fresh usage, ``estimate`` counts the whole view;
    ``skip`` reports nothing, so the trigger does not fire (Pi skips usage
    older than the latest compaction, ``agent-session.js:1391-1396``).
    """

    kind = "usage_plus_trailing"

    def __init__(self, params: Params) -> None:
        self.estimate = build_estimator(params.str("estimator", "bb_chars4"))
        self.total_rule = params.choice("usage_total", _TOTAL_RULES, "total_or_components")
        self.trailing = params.choice("trailing", _TRAILING, "all")
        self.without_usage = params.choice("without_usage", ("estimate", "skip"), "estimate")
        params.done()

    def measure(self, data: OccupancyInput) -> Optional[Occupancy]:
        if data.usage is None or not data.usage_fresh:
            if self.without_usage == "skip":
                return None
            return Occupancy(self.estimate(data.messages), "estimate")
        usage_tokens = _usage_total(data.usage, self.total_rule)
        anchor = _last_index(data.messages, "assistant")
        if anchor is None or self.trailing == "none":
            return Occupancy(usage_tokens, "usage")
        trailing = list(data.messages[anchor + 1 :])
        if self.trailing == "non_user":
            trailing = [m for m in trailing if m.get("role") != "user"]
        if not trailing:
            return Occupancy(usage_tokens, "usage")
        return Occupancy(usage_tokens + self.estimate(trailing), "usage+trailing")


class ProviderTotal:
    """Provider usage only (OpenCode ``tokens.total || sum``, Pi ``calculateContextTokens``).

    Without fresh usage (none yet, or it predates the latest record):
    ``without_usage: estimate`` counts the view; ``skip`` measures nothing,
    so the trigger does not fire (Pi ``_checkCompaction`` returns early).
    """

    kind = "provider_total"

    def __init__(self, params: Params) -> None:
        self.estimate = build_estimator(params.str("estimator", "bb_chars4"))
        self.total_rule = params.choice("usage_total", _TOTAL_RULES, "total_or_components")
        self.without_usage = params.choice("without_usage", ("estimate", "skip"), "estimate")
        params.done()

    def measure(self, data: OccupancyInput) -> Optional[Occupancy]:
        if data.usage is None or not data.usage_fresh:
            if self.without_usage == "skip":
                return None
            return Occupancy(self.estimate(data.messages), "estimate")
        return Occupancy(_usage_total(data.usage, self.total_rule), "usage")


class EstimateOnly:
    """Count the projected view with the estimator; ignore provider usage."""

    kind = "estimate_only"

    def __init__(self, params: Params) -> None:
        self.estimate = build_estimator(params.str("estimator", "bb_chars4"))
        params.done()

    def measure(self, data: OccupancyInput) -> Optional[Occupancy]:
        return Occupancy(self.estimate(data.messages), "estimate")


OCCUPANCY_KINDS = {
    cls.kind: cls for cls in (MaxUsageEstimate, UsagePlusTrailing, ProviderTotal, EstimateOnly)
}


def build_occupancy(params: Params) -> Any:
    kind = params.kind(OCCUPANCY_KINDS)
    return OCCUPANCY_KINDS[kind](params)
