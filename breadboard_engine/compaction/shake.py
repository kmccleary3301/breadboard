"""Context-reducing surgical compaction ("shake").

Ported from OMP:
- ``packages/agent/src/compaction/shake.ts``
- ``packages/coding-agent/src/session/session-maintenance.ts``
- ``packages/agent/src/compaction/tool-protection.ts``

Elides heavy tool results and oversized fenced/XML blocks across model history,
preserving the active recent tail and protected tool results. Elided contents
are offloaded to ``context.artifacts.store(...)`` with recoverable references.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import math
import re
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from .methods import CompactionContext, MethodUnavailable
from .settings import ShakeSettings
from .state import CompactionRecord, MessageEdit
from .tokens import estimate_message_tokens, estimate_text_tokens
from .transcript import is_tool_result, role_of, tool_call_ids

# Token cost heuristic for placeholder line; used only for the savings gate.
PLACEHOLDER_TOKEN_ESTIMATE = 16

OPENING_XML = re.compile(r"^<([a-z_-]+)(?:\s+[^>]*)?>$")
CLOSING_XML = re.compile(r"^<\/([a-z_-]+)>$")

SKILL_INTERNAL_URL_PREFIX = "skill://"
ARTIFACT_INTERNAL_URL_PREFIX = "artifact://"


@dataclass(frozen=True)
class ProtectedToolContext:
    tool_result: Mapping[str, Any]
    tool_call: Optional[Mapping[str, Any]]


ProtectedToolMatcher = Union[str, Callable[[ProtectedToolContext], bool]]


def get_read_tool_path(context: ProtectedToolContext) -> Optional[str]:
    """Extract path argument from read/read_file tool result or paired call."""
    tool_result = context.tool_result
    tool_call = context.tool_call
    name = (
        tool_result.get("tool_name")
        or tool_result.get("name")
        or (tool_call.get("name") if tool_call else "")
        or ""
    )
    if name not in ("read", "read_file"):
        return None

    if tool_call:
        args = tool_call.get("arguments")
        if isinstance(args, Mapping):
            path = args.get("path") or args.get("file_path")
            if isinstance(path, str):
                return path

    path = tool_result.get("path")
    if isinstance(path, str):
        return path

    details = tool_result.get("details")
    if isinstance(details, Mapping):
        path = details.get("path") or details.get("file_path")
        if isinstance(path, str):
            return path
        meta = details.get("meta")
        if isinstance(meta, Mapping):
            source = meta.get("source")
            if isinstance(source, Mapping):
                val = source.get("value")
                if isinstance(val, str):
                    return val

    return None


def is_skill_read_tool_result(context: ProtectedToolContext) -> bool:
    path = get_read_tool_path(context)
    return path.startswith(SKILL_INTERNAL_URL_PREFIX) if path else False


def is_artifact_recovery_tool_result(context: ProtectedToolContext) -> bool:
    path = get_read_tool_path(context)
    if path and path.startswith(ARTIFACT_INTERNAL_URL_PREFIX):
        return True
    details = context.tool_result.get("details")
    if isinstance(details, Mapping):
        meta = details.get("meta")
        if isinstance(meta, Mapping):
            source = meta.get("source")
            if isinstance(source, Mapping):
                val = source.get("value")
                if isinstance(val, str) and val.startswith(ARTIFACT_INTERNAL_URL_PREFIX):
                    return True
    return False


DEFAULT_PROTECTED_TOOLS: Tuple[ProtectedToolMatcher, ...] = (
    "skill",
    is_skill_read_tool_result,
    is_artifact_recovery_tool_result,
)


def collect_tool_calls_by_id(messages: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    calls: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        if role_of(msg) != "assistant":
            continue
        for call in msg.get("tool_calls") or ():
            if isinstance(call, Mapping):
                call_id = call.get("id")
                if not isinstance(call_id, str):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), Mapping) else call
                name = fn.get("name") if isinstance(fn, Mapping) else ""
                args = fn.get("arguments") if isinstance(fn, Mapping) else {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                calls[call_id] = {"id": call_id, "name": name, "arguments": args}
    return calls


def is_protected_tool_result(
    tool_result: Mapping[str, Any],
    tool_call: Optional[Mapping[str, Any]],
    matchers: Sequence[ProtectedToolMatcher],
) -> bool:
    tool_name = (
        tool_result.get("tool_name")
        or tool_result.get("name")
        or (tool_call.get("name") if tool_call else "")
        or ""
    )
    ctx = ProtectedToolContext(tool_result=tool_result, tool_call=tool_call)
    for matcher in matchers:
        if isinstance(matcher, str):
            if tool_name == matcher:
                return True
        elif callable(matcher):
            try:
                if matcher(ctx):
                    return True
            except Exception:
                pass
    return False


def is_useless_tool_result(message: Mapping[str, Any]) -> bool:
    if not is_tool_result(message):
        return False
    is_error = bool(
        message.get("is_error")
        or message.get("isError")
        or message.get("error")
    )
    if is_error:
        return False
    if message.get("useless") is True:
        return True
    details = message.get("details")
    if isinstance(details, Mapping) and details.get("useless") is True:
        return True
    return False


@dataclass(frozen=True)
class ToolResultShakeRegion:
    index: int
    tokens: int
    original_text: str
    label: str
    kind: str = "toolResult"


@dataclass(frozen=True)
class BlockShakeRegion:
    index: int
    block_index: int
    start: int
    end: int
    tokens: int
    original_text: str
    label: str
    kind: str = "block"


ShakeRegion = Union[ToolResultShakeRegion, BlockShakeRegion]


@dataclass(frozen=True)
class ShakeConfig:
    protect_tokens: int = 16000
    min_savings: int = 4000
    fence_min_tokens: int = 400
    protected_tools: Sequence[ProtectedToolMatcher] = DEFAULT_PROTECTED_TOOLS
    keep_boundary_index: int = 0


DEFAULT_SHAKE_CONFIG = ShakeConfig()
AGGRESSIVE_SHAKE_CONFIG = ShakeConfig(
    protect_tokens=4000,
    min_savings=0,
    fence_min_tokens=400,
    protected_tools=("skill", is_skill_read_tool_result),
)
RESCUE_SHAKE_CONFIG = ShakeConfig(
    protect_tokens=0,
    min_savings=0,
    fence_min_tokens=400,
    protected_tools=("skill", is_skill_read_tool_result, is_artifact_recovery_tool_result),
)


def scan_text_for_block_ranges(text: str) -> List[Tuple[int, int]]:
    """Locate fenced code blocks and top-level XML element spans inside text.

    Mirrors OMP ``scanTextForBlockRanges``. Returns character ranges [start, end)
    excluding trailing newline. XML detection is suppressed inside code fences.
    """
    ranges: List[Tuple[int, int]] = []
    in_fence = False
    fence_start = -1
    tag_stack: List[str] = []
    xml_start = -1

    line_start = 0
    text_len = len(text)
    for i in range(text_len + 1):
        if i != text_len and text[i] != "\n":
            continue
        line = text[line_start:i]
        line_end = i
        trimmed_start = line.lstrip()

        is_fence_line = trimmed_start.startswith("```") or trimmed_start.startswith("~~~")
        if is_fence_line:
            if not in_fence:
                in_fence = True
                fence_start = line_start
            else:
                in_fence = False
                ranges.append((fence_start, line_end))
                fence_start = -1
            line_start = i + 1
            continue

        if not in_fence:
            is_opening_xml = len(line) == len(trimmed_start) and bool(OPENING_XML.match(trimmed_start))
            if is_opening_xml:
                m = OPENING_XML.match(trimmed_start)
                if m:
                    if len(tag_stack) == 0:
                        xml_start = line_start
                    tag_stack.append(m.group(1))
            else:
                closing_m = CLOSING_XML.match(trimmed_start)
                if closing_m and tag_stack and tag_stack[-1] == closing_m.group(1):
                    tag_stack.pop()
                    if len(tag_stack) == 0 and xml_start >= 0:
                        ranges.append((xml_start, line_end))
                        xml_start = -1

        line_start = i + 1

    return _merge_ranges(ranges)


def _merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if len(ranges) <= 1:
        return ranges
    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    kept: List[Tuple[int, int]] = []
    last_end = -1
    for start, end in sorted_ranges:
        if start < last_end:
            continue
        kept.append((start, end))
        last_end = end
    return kept


def _extract_tool_result_text(
    message: Mapping[str, Any],
    count_text_tokens: Callable[[str], int],
) -> Optional[Tuple[str, int]]:
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        return content, count_text_tokens(content)
    if isinstance(content, list):
        fragments: List[str] = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                txt = block.get("text")
                if isinstance(txt, str) and txt:
                    fragments.append(txt)
            elif isinstance(block, str) and block:
                fragments.append(block)
        if not fragments:
            return None
        joined = "\n".join(fragments)
        return joined, count_text_tokens(joined)
    return None


def collect_shake_regions(
    messages: Sequence[Mapping[str, Any]],
    config: Optional[Union[ShakeConfig, ShakeSettings]] = None,
    *,
    keep_boundary_index: int = 0,
    protected_tools: Optional[Sequence[ProtectedToolMatcher]] = None,
    count_tokens: Callable[[Mapping[str, Any]], int] = estimate_message_tokens,
    count_text_tokens: Callable[[str], int] = estimate_text_tokens,
) -> List[ShakeRegion]:
    """Port of OMP ``collectShakeRegions``.

    Locates every eligible shake region in document order across the un-summarized
    portion of history (at or after ``keep_boundary_index``).
    """
    n = len(messages)
    if n == 0:
        return []

    if config is None:
        cfg = DEFAULT_SHAKE_CONFIG
    elif isinstance(config, ShakeSettings):
        cfg = ShakeConfig(
            protect_tokens=config.protect_tokens,
            min_savings=config.min_savings,
            fence_min_tokens=config.fence_min_tokens,
            protected_tools=protected_tools or DEFAULT_PROTECTED_TOOLS,
            keep_boundary_index=keep_boundary_index,
        )
    else:
        cfg = config
        if protected_tools is not None:
            cfg = ShakeConfig(
                protect_tokens=cfg.protect_tokens,
                min_savings=cfg.min_savings,
                fence_min_tokens=cfg.fence_min_tokens,
                protected_tools=protected_tools,
                keep_boundary_index=keep_boundary_index or cfg.keep_boundary_index,
            )

    boundary_idx = max(0, keep_boundary_index or cfg.keep_boundary_index)

    accumulated_after = [0] * n
    acc = 0
    for i in range(n - 1, -1, -1):
        accumulated_after[i] = acc
        acc += count_tokens(messages[i])

    tool_calls_by_id = collect_tool_calls_by_id(messages)
    regions: List[ShakeRegion] = []

    for i in range(n):
        if i < boundary_idx:
            continue
        msg = messages[i]
        useless_res = is_useless_tool_result(msg)
        if not useless_res and accumulated_after[i] < cfg.protect_tokens:
            continue

        if is_tool_result(msg):
            # Already pruned or shaken
            if msg.get("pruned_at") is not None or msg.get("prunedAt") is not None:
                continue
            call_id = msg.get("tool_call_id")
            tool_call = tool_calls_by_id.get(call_id) if isinstance(call_id, str) else None
            if is_protected_tool_result(msg, tool_call, cfg.protected_tools):
                continue
            res = _extract_tool_result_text(msg, count_text_tokens)
            if not res:
                continue
            original_text, tokens = res
            tool_name = (
                msg.get("tool_name")
                or msg.get("name")
                or (tool_call.get("name") if tool_call else "")
                or "tool"
            )
            regions.append(
                ToolResultShakeRegion(
                    index=i,
                    tokens=tokens,
                    original_text=original_text,
                    label=str(tool_name),
                )
            )
            continue

        role = role_of(msg)
        if role in ("assistant", "user", "developer"):
            content = msg.get("content")
            if isinstance(content, str):
                for start, end in scan_text_for_block_ranges(content):
                    slice_text = content[start:end]
                    if not slice_text:
                        continue
                    tokens = count_text_tokens(slice_text)
                    if tokens < cfg.fence_min_tokens:
                        continue
                    regions.append(
                        BlockShakeRegion(
                            index=i,
                            block_index=-1,
                            start=start,
                            end=end,
                            tokens=tokens,
                            original_text=slice_text,
                            label=role,
                        )
                    )
            elif isinstance(content, list):
                for bi, block in enumerate(content):
                    if isinstance(block, Mapping) and block.get("type") == "text":
                        txt = block.get("text", "")
                        if isinstance(txt, str) and txt:
                            for start, end in scan_text_for_block_ranges(txt):
                                slice_text = txt[start:end]
                                if not slice_text:
                                    continue
                                tokens = count_text_tokens(slice_text)
                                if tokens < cfg.fence_min_tokens:
                                    continue
                                regions.append(
                                    BlockShakeRegion(
                                        index=i,
                                        block_index=bi,
                                        start=start,
                                        end=end,
                                        tokens=tokens,
                                        original_text=slice_text,
                                        label=role,
                                    )
                                )

    savings = sum(max(0, r.tokens - PLACEHOLDER_TOKEN_ESTIMATE) for r in regions)
    if savings < cfg.min_savings:
        return []

    return regions


# Alias matching OMP camelCase naming
collectShakeRegions = collect_shake_regions


def format_shake_artifact_text(regions: Sequence[ShakeRegion]) -> str:
    """Concatenate original regions into the persisted shake artifact body.

    Mirrors OMP ``session-maintenance.ts`` ``#shakeArtifactText``.
    """
    parts: List[str] = []
    for i, region in enumerate(regions):
        parts.extend(
            [f"### region {i + 1} ({region.label}, ~{region.tokens} tok)", "", region.original_text, ""]
        )
    return "\n".join(parts)


def shake_elide_placeholder(region: ShakeRegion, index: int, artifact_ref: Optional[str]) -> str:
    """Mirrors OMP ``#shakeElidePlaceholder``."""
    if artifact_ref:
        ref = artifact_ref if "://" in artifact_ref else f"artifact://{artifact_ref}"
        return f"[shaken ~{region.tokens} tokens — recover: {ref} (region {index + 1})]"
    return f"[shaken ~{region.tokens} tokens]"


def apply_shake_region(
    message: Dict[str, Any],
    region: ShakeRegion,
    replacement: str,
    clock_now: Optional[str] = None,
) -> None:
    """Apply a single shake region in place to a message dict."""
    if region.kind == "toolResult":
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = replacement
        elif isinstance(content, list):
            rep_idx = -1
            for idx, b in enumerate(content):
                if isinstance(b, Mapping) and b.get("type") == "text":
                    txt = b.get("text")
                    if isinstance(txt, str) and txt:
                        rep_idx = idx
                        break
            if rep_idx < 0:
                message["content"] = [{"type": "text", "text": replacement}]
            else:
                kept: List[Any] = []
                for idx, b in enumerate(content):
                    if isinstance(b, Mapping) and b.get("type") != "text":
                        kept.append(b)
                    elif idx == rep_idx:
                        kept.append({"type": "text", "text": replacement})
                message["content"] = kept
        else:
            message["content"] = replacement
        now = clock_now or "now"
        message["pruned_at"] = now
        message["prunedAt"] = now
        return

    # Block region
    if not isinstance(region, BlockShakeRegion):
        return

    content = message.get("content")
    if region.block_index == -1:
        if isinstance(content, str):
            message["content"] = content[:region.start] + replacement + content[region.end:]
    elif isinstance(content, list) and 0 <= region.block_index < len(content):
        blk = content[region.block_index]
        if isinstance(blk, Mapping) and blk.get("type") == "text":
            txt = str(blk.get("text") or "")
            blk_copy = dict(blk)
            blk_copy["text"] = txt[:region.start] + replacement + txt[region.end:]
            content[region.block_index] = blk_copy


applyShakeRegion = apply_shake_region


def apply_shake_regions(
    messages: Sequence[Mapping[str, Any]],
    items: Sequence[Tuple[ShakeRegion, str]],
    clock_now: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Apply many regions at once.

    Block regions are applied highest-start-first so splicing never shifts
    offsets of another in the same text block; tool-result regions are independent.
    Returns deep-copied message dicts with edits applied.
    """
    out: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in messages]
    ordered = sorted(
        items,
        key=lambda item: item[0].start if isinstance(item[0], BlockShakeRegion) else -1,
        reverse=True,
    )
    for region, replacement in ordered:
        if 0 <= region.index < len(out):
            apply_shake_region(out[region.index], region, replacement, clock_now)
    return out


applyShakeRegions = apply_shake_regions


def shake(context: CompactionContext) -> Optional[CompactionRecord]:
    """Execute shake compaction on ``context``.

    Returns an edit-only ``CompactionRecord`` if eligible regions meet the savings
    threshold, or ``None``.
    """
    kept_start = context.state.kept_start(context.messages)

    # Reconstruct current active messages on top of prior edits
    current_messages: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in context.messages]
    for record in context.state.records:
        for edit in record.edits:
            current_messages[edit.index] = copy.deepcopy(dict(edit.message))

    regions = collect_shake_regions(
        current_messages,
        context.settings.shake,
        keep_boundary_index=kept_start,
    )
    if not regions:
        return None

    savings = sum(max(0, r.tokens - PLACEHOLDER_TOKEN_ESTIMATE) for r in regions)
    if savings < context.settings.shake.min_savings:
        return None

    # OMP ``#saveShakeArtifact``: a failed or missing artifact store still
    # shakes, with placeholders that carry no recovery reference.
    artifact_ref: Optional[str] = None
    if context.artifacts is not None:
        try:
            artifact_ref = context.artifacts.store("shake", format_shake_artifact_text(regions), "text/plain")
        except Exception:
            artifact_ref = None
    now = context.clock()

    items = [(r, shake_elide_placeholder(r, idx, artifact_ref)) for idx, r in enumerate(regions)]
    shaken_messages = apply_shake_regions(current_messages, items, clock_now=now)

    edits: List[MessageEdit] = []
    modified_indices = sorted({r.index for r in regions if r.index >= kept_start})
    for idx in modified_indices:
        edits.append(MessageEdit(index=idx, message=shaken_messages[idx]))

    if not edits:
        return None

    record = context.new_record(
        method="shake",
        edits=tuple(edits),
        details={
            "regions": len(regions),
            "tool_results_dropped": sum(1 for r in regions if r.kind == "toolResult"),
            "blocks_dropped": sum(1 for r in regions if r.kind == "block"),
            "tokens_saved": savings,
            "artifact_ref": artifact_ref,
        },
    )
    context.state.validate(record, context.messages)
    return record


class ShakeCompaction:
    """Edit-only compaction method ('shake') that offloads heavy content to artifacts."""

    name: str = "shake"

    def run(self, context: CompactionContext) -> CompactionRecord:
        record = shake(context)
        if record is None:
            raise MethodUnavailable("shake found no eligible regions meeting savings threshold")
        return record
