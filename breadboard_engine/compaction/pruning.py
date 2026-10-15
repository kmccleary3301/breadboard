"""Tool-result pruning utilities for compaction.

Ported from OMP ``packages/agent/src/compaction/pruning.ts``:
- ``pruneSupersededToolResults``
- ``pruneToolOutputs`` (as ``prune_tool_results`` over the compaction ledger)
- ``readToolSupersedeKey``
- ``SUPERSEDED_NOTICE``
- ``USELESS_NOTICE``
- ``MIN_PRUNE_TOKENS``
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from .file_ops import split_read_selector
from .methods import CompactionContext
from .settings import PruneSettings
from .shake import (
    DEFAULT_PROTECTED_TOOLS,
    ProtectedToolMatcher,
    collect_tool_calls_by_id,
    is_protected_tool_result_message,
    is_useless_tool_result,
)
from .state import CompactionRecord, MessageEdit
from .tokens import estimate_message_tokens
from .transcript import answers_tool_call, result_call_ids, rewrite_result_content

SUPERSEDED_NOTICE = "[Superseded by a newer read of this file]"
USELESS_NOTICE = "[Uneventful result elided]"
MIN_PRUNE_TOKENS = 50

DEFAULT_SUFFIX_TOKEN_LIMIT = 8000
DEFAULT_IDLE_FLUSH_MS = 30 * 60 * 1000


SupersedeKeyFn = Callable[[str, Mapping[str, Any]], Optional[str]]


def create_pruned_notice(tokens: int) -> str:
    return f"[Output truncated - {tokens} tokens]"


def estimate_pruned_savings(tokens: int, notice: str) -> int:
    notice_tokens = math.ceil(len(notice) / 4)
    return max(0, tokens - notice_tokens)



def read_tool_supersede_key(tool_name: str, args: Mapping[str, Any]) -> Optional[str]:
    """Supersede key for the ``read`` and ``read_file`` tools.

    Strikes trailing selectors into a ``\u0000``-delimited key. Internal or URL-scheme
    paths (``skill://...``, ``https://...``) are exempt.
    """
    if tool_name not in ("read", "read_file"):
        return None
    if not isinstance(args, Mapping):
        return None
    path = args.get("path") or args.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    if "://" in path:
        return None
    base, sel = split_read_selector(path)
    if sel is None:
        offset = args.get("offset")
        limit = args.get("limit")
        if offset is not None or limit is not None:
            sel = f"{offset if offset is not None else ''}-{limit if limit is not None else ''}"
    return base if sel is None else f"{base}\u0000{sel}"




@dataclass(frozen=True)
class PruneResult:
    pruned_count: int
    tokens_saved: int



@dataclass(frozen=True)
class SupersedeCandidate:
    entry: Any
    message: Dict[str, Any]
    index: int
    tokens: int
    notice: str


def _unwrap_message(entry: Any) -> Tuple[Dict[str, Any], bool]:
    """Return the inner chat dict and a flag indicating whether entry was a wrapper."""
    if isinstance(entry, Mapping) and "message" in entry:
        msg = entry["message"]
        if isinstance(msg, Mapping):
            return dict(msg), True
    if isinstance(entry, Mapping):
        return dict(entry), False
    return {}, False


def _replace_result_content(message: Dict[str, Any], notice: str) -> None:
    if rewrite_result_content(message, notice):
        return
    if isinstance(message.get("content"), list):
        message["content"] = [{"type": "text", "text": notice}]
    else:
        message["content"] = notice


def compute_message_suffix_tokens(
    entries: Sequence[Any],
    count_tokens: Callable[[Mapping[str, Any]], int] = estimate_message_tokens,
) -> List[int]:
    """Estimated token total of all message entries strictly after index i."""
    suffix = [0] * len(entries)
    accumulated = 0
    for i in range(len(entries) - 1, -1, -1):
        suffix[i] = accumulated
        msg, _ = _unwrap_message(entries[i])
        if msg:
            accumulated += count_tokens(msg)
    return suffix


def _resolve_boundary_index(entries: Sequence[Any], keep_boundary_id: Optional[str]) -> int:
    if keep_boundary_id is None:
        return 0
    for i, entry in enumerate(entries):
        if isinstance(entry, Mapping) and entry.get("id") == keep_boundary_id:
            return i
    return 0


def _collect_superseded_results(
    entries: Sequence[Any],
    count_tokens: Callable[[Mapping[str, Any]], int],
    tool_calls_by_id: Mapping[str, Mapping[str, Any]],
    supersede_key: SupersedeKeyFn,
    protected_tools: Sequence[ProtectedToolMatcher],
) -> List[SupersedeCandidate]:
    candidates: List[SupersedeCandidate] = []
    seen_keys: Set[str] = set()
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        msg, is_wrapped = _unwrap_message(entry)
        if not answers_tool_call(msg) or msg.get("pruned_at") is not None or msg.get("prunedAt") is not None:
            continue
        call_ids = result_call_ids(msg)
        if not call_ids or is_protected_tool_result_message(msg, tool_calls_by_id, protected_tools):
            continue
        result_keys: List[str] = []
        valid_group = True
        for call_id in reversed(call_ids):
            tool_call = tool_calls_by_id.get(call_id)
            if not tool_call:
                valid_group = False
                break
            key = supersede_key(str(tool_call.get("name") or ""), tool_call.get("arguments") or {})
            if key is None:
                valid_group = False
                break
            result_keys.append(key)
        if not valid_group:
            continue
        superseded = True
        for key in result_keys:
            sep = key.find("\u0000")
            if key not in seen_keys and (sep < 0 or key[:sep] not in seen_keys):
                superseded = False
            seen_keys.add(key)
        if not superseded:
            continue
        target_dict = entry["message"] if is_wrapped else entry
        candidates.append(
            SupersedeCandidate(
                entry=entry,
                message=target_dict,
                index=i,
                tokens=count_tokens(msg),
                notice=SUPERSEDED_NOTICE,
            )
        )
    candidates.reverse()
    return candidates


def _collect_useless_results(
    entries: Sequence[Any],
    count_tokens: Callable[[Mapping[str, Any]], int],
    tool_calls_by_id: Mapping[str, Mapping[str, Any]],
    protected_tools: Sequence[ProtectedToolMatcher],
    exclude_messages: Set[int],  # id(message)
) -> List[SupersedeCandidate]:
    candidates: List[SupersedeCandidate] = []
    for i, entry in enumerate(entries):
        msg, is_wrapped = _unwrap_message(entry)
        if not is_useless_tool_result(msg) or msg.get("pruned_at") is not None or msg.get("prunedAt") is not None:
            continue
        target_dict = entry["message"] if is_wrapped else entry
        if id(target_dict) in exclude_messages:
            continue
        if is_protected_tool_result_message(msg, tool_calls_by_id, protected_tools):
            continue
        tokens = count_tokens(msg)
        if estimate_pruned_savings(tokens, USELESS_NOTICE) <= 0:
            continue
        candidates.append(
            SupersedeCandidate(
                entry=entry,
                message=target_dict,
                index=i,
                tokens=tokens,
                notice=USELESS_NOTICE,
            )
        )
    return candidates


def prune_superseded_tool_results(
    entries: Sequence[Any],
    count_tokens: Optional[Any] = None,
    config: Optional[Any] = None,
    **kwargs: Any,
) -> PruneResult:
    """Prune superseded and useless tool results from ``entries``.

    Ports OMP ``pruneSupersededToolResults``.
    Supports either (entries, count_tokens, config) or (entries, config).
    """
    if count_tokens is not None and not callable(count_tokens):
        config = count_tokens
        count_fn = estimate_message_tokens
    elif callable(count_tokens):
        count_fn = count_tokens
    else:
        count_fn = estimate_message_tokens

    cfg_dict: Dict[str, Any] = {}
    if isinstance(config, Mapping):
        cfg_dict.update(config)
    elif hasattr(config, "__dict__"):
        cfg_dict.update(vars(config))
    cfg_dict.update(kwargs)

    supersede_key = cfg_dict.get("supersede_key", cfg_dict.get("supersedeKey", read_tool_supersede_key))
    prune_useless = bool(cfg_dict.get("prune_useless", cfg_dict.get("pruneUseless", False)))
    suffix_token_limit = cfg_dict.get(
        "suffix_token_limit", cfg_dict.get("suffixTokenLimit", DEFAULT_SUFFIX_TOKEN_LIMIT)
    )
    idle_flush_ms = cfg_dict.get("idle_flush_ms", cfg_dict.get("idleFlushMs", DEFAULT_IDLE_FLUSH_MS))
    now = cfg_dict.get("now")
    keep_boundary_id = cfg_dict.get("keep_boundary_id", cfg_dict.get("keepBoundaryId"))
    protected_tools = cfg_dict.get("protected_tools", cfg_dict.get("protectedTools", DEFAULT_PROTECTED_TOOLS))

    raw_messages = [_unwrap_message(e)[0] for e in entries]
    tool_calls_by_id = collect_tool_calls_by_id(raw_messages)

    candidates: List[SupersedeCandidate] = []
    if supersede_key is not None:
        candidates.extend(
            _collect_superseded_results(entries, count_fn, tool_calls_by_id, supersede_key, protected_tools)
        )

    if prune_useless:
        exclude = {id(c.message) for c in candidates}
        candidates.extend(
            _collect_useless_results(entries, count_fn, tool_calls_by_id, protected_tools, exclude)
        )
        candidates.sort(key=lambda c: c.index)

    if not candidates:
        return PruneResult(0, 0)

    # Check idle gap
    last_timestamp: Optional[float] = None
    for i in range(len(entries) - 1, -1, -1):
        msg, _ = _unwrap_message(entries[i])
        ts = msg.get("timestamp") or (entries[i].get("timestamp") if isinstance(entries[i], Mapping) else None)
        if isinstance(ts, (int, float)):
            last_timestamp = float(ts)
            break

    current_time = float(now) if now is not None else (last_timestamp or 0.0)
    idle = (
        last_timestamp is not None
        and idle_flush_ms is not None
        and (current_time - last_timestamp >= idle_flush_ms)
    )

    boundary_index = _resolve_boundary_index(entries, keep_boundary_id)

    if idle:
        to_prune = [c for c in candidates if c.index >= boundary_index]
    else:
        suffix_tokens = compute_message_suffix_tokens(entries, count_fn)
        limit = suffix_token_limit if suffix_token_limit is not None else DEFAULT_SUFFIX_TOKEN_LIMIT
        to_prune = [c for c in candidates if c.index >= boundary_index and suffix_tokens[c.index] <= limit]

    if not to_prune:
        return PruneResult(0, 0)

    now_val = current_time
    tokens_saved = 0
    for candidate in to_prune:
        msg = candidate.message
        _replace_result_content(msg, candidate.notice)
        msg["pruned_at"] = now_val
        msg["prunedAt"] = now_val
        tokens_saved += estimate_pruned_savings(candidate.tokens, candidate.notice)

    return PruneResult(len(to_prune), tokens_saved)


def _select_prune_candidates(
    messages: Sequence[Dict[str, Any]],
    settings: PruneSettings,
    boundary_index: int,
) -> Tuple[List[SupersedeCandidate], int]:
    """Select tool results to prune with OMP ``pruneToolOutputs`` policy.

    Superseded and useless results are always eligible; other results are age-pruned
    once ``settings.protect_tokens`` of newer tool output is retained, unless protected
    or smaller than ``MIN_PRUNE_TOKENS``. Results already pruned or before
    ``boundary_index`` are kept. Returns candidates in index order and their total
    savings, or ``([], 0)`` when savings fall short of ``settings.minimum_savings``.
    """
    tool_calls_by_id = collect_tool_calls_by_id(messages)

    superseded_ids: Set[int] = set()
    if settings.supersede_reads:
        superseded_ids = {
            id(c.message)
            for c in _collect_superseded_results(
                messages, estimate_message_tokens, tool_calls_by_id, read_tool_supersede_key, DEFAULT_PROTECTED_TOOLS
            )
        }

    useless_ids: Set[int] = set()
    if settings.prune_useless:
        useless_ids = {
            id(c.message)
            for c in _collect_useless_results(
                messages, estimate_message_tokens, tool_calls_by_id, DEFAULT_PROTECTED_TOOLS, superseded_ids
            )
        }

    accumulated_tokens = 0
    candidates: List[SupersedeCandidate] = []
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not answers_tool_call(msg):
            continue

        tokens = estimate_message_tokens(msg)
        accumulated_before = accumulated_tokens
        accumulated_tokens += tokens

        if msg.get("pruned_at") is not None or msg.get("prunedAt") is not None or i < boundary_index:
            continue

        if id(msg) in superseded_ids:
            notice = SUPERSEDED_NOTICE
        elif id(msg) in useless_ids:
            notice = USELESS_NOTICE
        elif (
            accumulated_before < settings.protect_tokens
            or tokens < MIN_PRUNE_TOKENS
            or is_protected_tool_result_message(msg, tool_calls_by_id, DEFAULT_PROTECTED_TOOLS)
        ):
            continue
        else:
            notice = create_pruned_notice(tokens)
        candidates.append(SupersedeCandidate(entry=msg, message=msg, index=i, tokens=tokens, notice=notice))

    tokens_saved = sum(estimate_pruned_savings(c.tokens, c.notice) for c in candidates)
    if not candidates or tokens_saved < settings.minimum_savings:
        return [], 0
    candidates.reverse()
    return candidates, tokens_saved



def prune_tool_results(context: CompactionContext) -> Optional[CompactionRecord]:
    """Execute tool-result pruning on ``context`` honoring ``context.settings.prune``.

    Returns an edit-only ``CompactionRecord`` if savings meet ``settings.prune.minimum_savings``,
    or ``None``.
    """
    settings: PruneSettings = context.settings.prune
    if not settings.enabled:
        return None

    current_messages: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in context.messages]
    for record in context.state.records:
        for edit in record.edits:
            current_messages[edit.index] = copy.deepcopy(dict(edit.message))

    candidates, tokens_saved = _select_prune_candidates(
        current_messages, settings, context.state.kept_start(context.messages, context.target)
    )
    if not candidates:
        return None

    now = context.clock()
    edits: List[MessageEdit] = []
    for candidate in candidates:
        new_msg = copy.deepcopy(candidate.message)
        _replace_result_content(new_msg, candidate.notice)
        new_msg["pruned_at"] = now
        new_msg["prunedAt"] = now
        edits.append(MessageEdit(index=candidate.index, message=new_msg))

    superseded_count = sum(1 for c in candidates if c.notice == SUPERSEDED_NOTICE)
    useless_count = sum(1 for c in candidates if c.notice == USELESS_NOTICE)
    record = context.new_record(
        method="prune",
        edits=tuple(edits),
        details={
            "pruned_count": len(edits),
            "tokens_saved": tokens_saved,
            "superseded_count": superseded_count,
            "useless_count": useless_count,
            "age_pruned_count": len(candidates) - superseded_count - useless_count,
        },
    )
    context.state.validate(record, context.messages)
    return record

