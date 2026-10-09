"""Tool-result pruning utilities for compaction.

Ported from OMP ``packages/agent/src/compaction/pruning.ts``:
- ``pruneSupersededToolResults``
- ``pruneToolOutputs``
- ``readToolSupersedeKey``
- ``SUPERSEDED_NOTICE``
- ``USELESS_NOTICE``
- ``MIN_PRUNE_TOKENS``
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
    Set,
    Tuple,
    Union,
)
from .file_ops import split_read_selector
from .methods import CompactionContext, MethodUnavailable
from .settings import PruneSettings
from .shake import (
    DEFAULT_PROTECTED_TOOLS,
    ProtectedToolMatcher,
    collect_tool_calls_by_id,
    is_protected_tool_result,
    is_useless_tool_result,
)
from .state import CompactionRecord, MessageEdit
from .tokens import estimate_message_tokens
from .transcript import is_tool_result, role_of

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
        if not is_tool_result(msg) or msg.get("pruned_at") is not None or msg.get("prunedAt") is not None:
            continue
        call_id = msg.get("tool_call_id")
        tool_call = tool_calls_by_id.get(call_id) if isinstance(call_id, str) else None
        if not tool_call:
            continue
        if is_protected_tool_result(msg, tool_call, protected_tools):
            continue
        name = str(tool_call.get("name") or "")
        arguments = tool_call.get("arguments") or {}
        key = supersede_key(name, arguments)
        if key is None:
            continue
        sep = key.find("\u0000")
        superseded = (key in seen_keys) or (sep >= 0 and key[:sep] in seen_keys)
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
        call_id = msg.get("tool_call_id")
        tool_call = tool_calls_by_id.get(call_id) if isinstance(call_id, str) else None
        if is_protected_tool_result(msg, tool_call, protected_tools):
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
        if isinstance(msg.get("content"), list):
            msg["content"] = [{"type": "text", "text": candidate.notice}]
        else:
            msg["content"] = candidate.notice
        msg["pruned_at"] = now_val
        msg["prunedAt"] = now_val
        tokens_saved += estimate_pruned_savings(candidate.tokens, candidate.notice)

    return PruneResult(len(to_prune), tokens_saved)



def prune_tool_outputs(
    entries: Sequence[Any],
    count_tokens: Optional[Any] = None,
    config: Optional[Any] = None,
    **kwargs: Any,
) -> PruneResult:
    """Full age-based, supersede-aware and useless-aware pruning pass.

    Ports OMP ``pruneToolOutputs``.
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

    protect_tokens = int(cfg_dict.get("protect_tokens", cfg_dict.get("protectTokens", 40000)))
    minimum_savings = int(cfg_dict.get("minimum_savings", cfg_dict.get("minimumSavings", 20000)))
    supersede_key = cfg_dict.get("supersede_key", cfg_dict.get("supersedeKey"))
    prune_useless = bool(cfg_dict.get("prune_useless", cfg_dict.get("pruneUseless", True)))
    keep_boundary_id = cfg_dict.get("keep_boundary_id", cfg_dict.get("keepBoundaryId"))
    boundary_index = int(
        cfg_dict.get("boundary_index", cfg_dict.get("boundaryIndex", _resolve_boundary_index(entries, keep_boundary_id)))
    )
    protected_tools = cfg_dict.get("protected_tools", cfg_dict.get("protectedTools", DEFAULT_PROTECTED_TOOLS))
    cache_warm_suffix_tokens = cfg_dict.get("cache_warm_suffix_tokens", cfg_dict.get("cacheWarmSuffixTokens"))

    raw_messages = [_unwrap_message(e)[0] for e in entries]
    tool_calls_by_id = collect_tool_calls_by_id(raw_messages)

    superseded_by_id: Dict[int, SupersedeCandidate] = {}
    if supersede_key is not None:
        for c in _collect_superseded_results(entries, count_fn, tool_calls_by_id, supersede_key, protected_tools):
            superseded_by_id[id(c.message)] = c

    useless_by_id: Dict[int, SupersedeCandidate] = {}
    if prune_useless:
        for c in _collect_useless_results(
            entries,
            count_fn,
            tool_calls_by_id,
            protected_tools,
            set(superseded_by_id.keys()),
        ):
            useless_by_id[id(c.message)] = c

    message_suffix = (
        compute_message_suffix_tokens(entries, count_fn) if cache_warm_suffix_tokens is not None else None
    )

    accumulated_tokens = 0
    candidates: List[Tuple[Any, Dict[str, Any], int, str]] = []

    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        msg, is_wrapped = _unwrap_message(entry)
        if not is_tool_result(msg):
            continue

        target_dict = entry["message"] if is_wrapped else entry
        tokens = count_fn(msg)
        call_id = msg.get("tool_call_id")
        tool_call = tool_calls_by_id.get(call_id) if isinstance(call_id, str) else None
        is_protected = is_protected_tool_result(msg, tool_call, protected_tools)

        if msg.get("pruned_at") is not None or msg.get("prunedAt") is not None:
            accumulated_tokens += tokens
            continue

        in_warm_prefix = (
            message_suffix is not None
            and cache_warm_suffix_tokens is not None
            and message_suffix[i] > cache_warm_suffix_tokens
        )
        if in_warm_prefix or i < boundary_index:
            accumulated_tokens += tokens
            continue

        superseded_cand = superseded_by_id.get(id(target_dict))
        useless_cand = useless_by_id.get(id(target_dict))
        too_small = tokens < MIN_PRUNE_TOKENS

        if superseded_cand is None and useless_cand is None:
            if accumulated_tokens < protect_tokens or is_protected or too_small:
                accumulated_tokens += tokens
                continue

        notice = (
            SUPERSEDED_NOTICE
            if superseded_cand is not None
            else (USELESS_NOTICE if useless_cand is not None else create_pruned_notice(tokens))
        )
        candidates.append((entry, target_dict, tokens, notice))
        accumulated_tokens += tokens

    tokens_saved = sum(estimate_pruned_savings(tokens, notice) for _, _, tokens, notice in candidates)
    if tokens_saved < minimum_savings or not candidates:
        return PruneResult(0, 0)

    now_val = "now"
    for _, target_dict, _, notice in candidates:
        if isinstance(target_dict.get("content"), list):
            target_dict["content"] = [{"type": "text", "text": notice}]
        else:
            target_dict["content"] = notice
        target_dict["pruned_at"] = now_val
        target_dict["prunedAt"] = now_val

    return PruneResult(len(candidates), tokens_saved)



def prune_tool_results(context: CompactionContext) -> Optional[CompactionRecord]:
    """Execute tool-result pruning on ``context`` honoring ``context.settings.prune``.

    Returns an edit-only ``CompactionRecord`` if savings meet ``settings.prune.minimum_savings``,
    or ``None``.
    """
    settings: PruneSettings = context.settings.prune
    if not settings.enabled:
        return None

    kept_start = context.state.kept_start(context.messages)

    current_messages: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in context.messages]
    for record in context.state.records:
        for edit in record.edits:
            current_messages[edit.index] = copy.deepcopy(dict(edit.message))

    supersede_key: Optional[SupersedeKeyFn] = read_tool_supersede_key if settings.supersede_reads else None

    # Collect candidates using pruneToolOutputs semantics
    raw_messages = current_messages
    tool_calls_by_id = collect_tool_calls_by_id(raw_messages)

    superseded_by_id: Dict[int, SupersedeCandidate] = {}
    if supersede_key is not None:
        for c in _collect_superseded_results(
            raw_messages,
            estimate_message_tokens,
            tool_calls_by_id,
            supersede_key,
            DEFAULT_PROTECTED_TOOLS,
        ):
            superseded_by_id[id(c.message)] = c

    useless_by_id: Dict[int, SupersedeCandidate] = {}
    if settings.prune_useless:
        for c in _collect_useless_results(
            raw_messages,
            estimate_message_tokens,
            tool_calls_by_id,
            DEFAULT_PROTECTED_TOOLS,
            set(superseded_by_id.keys()),
        ):
            useless_by_id[id(c.message)] = c

    accumulated_tokens = 0
    candidates: List[Tuple[int, Dict[str, Any], int, str, bool, bool]] = []

    for i in range(len(raw_messages) - 1, -1, -1):
        msg = raw_messages[i]
        if not is_tool_result(msg):
            continue

        tokens = estimate_message_tokens(msg)
        call_id = msg.get("tool_call_id")
        tool_call = tool_calls_by_id.get(call_id) if isinstance(call_id, str) else None
        is_protected = is_protected_tool_result(msg, tool_call, DEFAULT_PROTECTED_TOOLS)

        if msg.get("pruned_at") is not None or msg.get("prunedAt") is not None:
            accumulated_tokens += tokens
            continue

        if i < kept_start:
            accumulated_tokens += tokens
            continue

        superseded_cand = superseded_by_id.get(id(msg))
        useless_cand = useless_by_id.get(id(msg))
        too_small = tokens < MIN_PRUNE_TOKENS

        if superseded_cand is None and useless_cand is None:
            if accumulated_tokens < settings.protect_tokens or is_protected or too_small:
                accumulated_tokens += tokens
                continue

        notice = (
            SUPERSEDED_NOTICE
            if superseded_cand is not None
            else (USELESS_NOTICE if useless_cand is not None else create_pruned_notice(tokens))
        )
        candidates.append((i, msg, tokens, notice, superseded_cand is not None, useless_cand is not None))
        accumulated_tokens += tokens

    tokens_saved = sum(estimate_pruned_savings(tokens, notice) for _, _, tokens, notice, _, _ in candidates)
    if tokens_saved < settings.minimum_savings or not candidates:
        return None

    now = context.clock()
    edits: List[MessageEdit] = []
    candidates.sort(key=lambda item: item[0])

    for idx, orig_msg, _, notice, _, _ in candidates:
        new_msg = copy.deepcopy(orig_msg)
        if isinstance(new_msg.get("content"), list):
            new_msg["content"] = [{"type": "text", "text": notice}]
        else:
            new_msg["content"] = notice
        new_msg["pruned_at"] = now
        new_msg["prunedAt"] = now
        edits.append(MessageEdit(index=idx, message=new_msg))

    record = context.new_record(
        method="prune",
        edits=tuple(edits),
        details={
            "pruned_count": len(edits),
            "tokens_saved": tokens_saved,
            "superseded_count": sum(1 for _, _, _, _, is_sup, _ in candidates if is_sup),
            "useless_count": sum(1 for _, _, _, _, _, is_use in candidates if is_use),
            "age_pruned_count": sum(1 for _, _, _, _, is_sup, is_use in candidates if not is_sup and not is_use),
        },
    )
    context.state.validate(record, context.messages)
    return record

