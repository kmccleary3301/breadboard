#!/usr/bin/env python3
"""Capture oracle cases and prompt templates for Hermes Agent 2026.9.11.

Pinned commit: 939e45c91d751fadd94dcd1b873ac3cb44846213
Preset ID: hermes_agent@2026.9.11
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

PRESET_ID = "hermes_agent@2026.9.11"
REPO_URL = "https://github.com/NousResearch/hermes-agent.git"
COMMIT_SHA = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def write_prompt_files(source_dir: Path, target_dirs: List[Path]) -> Dict[str, str]:
    """Extract verbatim prompt bytes from pinned source and write to target directories."""
    sys.path.insert(0, str(source_dir))
    from agent import context_compressor

    prompts = {
        "summary_prefix.txt": (
            context_compressor.SUMMARY_PREFIX,
            "agent/context_compressor.py:180-220",
        ),
        "summary_end_marker.txt": (
            context_compressor._SUMMARY_END_MARKER,
            "agent/context_compressor.py:356",
        ),
        "merged_prior_context_header.txt": (
            context_compressor._MERGED_PRIOR_CONTEXT_HEADER,
            "agent/context_compressor.py:360",
        ),
        "merged_summary_delimiter.txt": (
            context_compressor._MERGED_SUMMARY_DELIMITER,
            "agent/context_compressor.py:361",
        ),
        "inflight_task_replay_header.txt": (
            context_compressor._INFLIGHT_TASK_REPLAY_HEADER,
            "agent/context_compressor.py:373-376",
        ),
        "lean_session_log_section.txt": (
            context_compressor._LEAN_SESSION_LOG_SECTION,
            "agent/context_compressor.py:852-862",
        ),
        "micro_summary_user_prompt.txt": (
            (
                "You are a summarization agent creating a compact record of an "
                "ongoing conversation.  You are given a running summary and the "
                "next exchange from the conversation.  Merge the exchange's key "
                "decisions, requirements, file paths, and open questions into the "
                "summary.  Preserve the summary's structure.  Drop resolved details "
                "that are no longer relevant.  Add new decisions, file paths, and "
                "open questions.\n\n"
                "NEVER include API keys, tokens, passwords, secrets, credentials, "
                "or connection strings in the summary \u2014 replace any that appear "
                "with [REDACTED].\n\n"
                "## Current Running Summary\n{summary_block}\n\n"
                "## Next Exchange to Merge\n{exchange_text}\n\n"
                "Return ONLY the updated summary text, no preamble or explanation. "
                "Do not include this instruction block in your output."
            ),
            "agent/micro_compaction.py:95-110",
        ),
        "micro_summary_system_prompt.txt": (
            "You are a conversation summarization assistant.",
            "agent/micro_compaction.py:112",
        ),
    }

    source_json_content = {
        "repo": REPO_URL,
        "commit": COMMIT_SHA,
        "files": {name: cite for name, (_, cite) in prompts.items()},
        "license": "MIT",
    }

    for target_dir in target_dirs:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, (content, _) in prompts.items():
            (target_dir / name).write_text(content, encoding="utf-8")
        (target_dir / "SOURCE.json").write_text(
            json.dumps(source_json_content, indent=2) + "\n", encoding="utf-8"
        )

    return source_json_content["files"]


class MockChoice:
    def __init__(self, content: str, finish_reason: str | None):
        self.message = MagicMock(content=content, reasoning_content=None)
        self.finish_reason = finish_reason


class MockResp:
    def __init__(self, content: str, finish_reason: str | None = None):
        self.choices = [MockChoice(content, finish_reason)]


def capture_cases(source_dir: Path) -> List[Dict[str, Any]]:
    """Execute pinned Hermes context compressor methods to produce oracle cases."""
    sys.path.insert(0, str(source_dir))
    from agent.context_compressor import (
        ContextCompressor,
        _SUMMARY_END_MARKER,
        _MERGED_SUMMARY_DELIMITER,
        _MERGED_PRIOR_CONTEXT_HEADER,
    )

    cases: List[Dict[str, Any]] = []

    # -------------------------------------------------------------
    # 1. Threshold: Below boundary (128K context, 50% cfg -> 75% eff = 96000 limit)
    # -------------------------------------------------------------
    cc_128 = ContextCompressor(model="hermes-3", config_context_length=128000, threshold_percent=0.50)
    thresh_128 = cc_128.threshold_tokens
    fires_below, reason_below = cc_128.should_compress_info(thresh_128 - 1)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "threshold_boundary_below",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:2253-2294",
                "agent/context_compressor.py:2490-2500",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._compute_threshold_tokens at agent/context_compressor.py:2260-2294 "
                "and ContextCompressor.should_compress_info at agent/context_compressor.py:2490-2500. "
                "Context length 128K < 512K floors threshold to 75% (96000). At 95999 (< 96000), should_compress returns False."
            ),
        },
        "input": {
            "messages": [{"role": "user", "content": "ping"}],
            "usage": {"input_tokens": thresh_128 - 1, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": thresh_128 + 9},
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {"threshold": 0.50, "context_length": 128000},
            "summary_responses": [],
        },
        "expect": {
            "trigger": {"fires": fires_below, "tokens": thresh_128 - 1, "limit": thresh_128, "severity": "soft"},
        },
    })

    # -------------------------------------------------------------
    # 2. Threshold: Equal boundary (96000 == 96000) -> Fires
    # -------------------------------------------------------------
    fires_equal, _ = cc_128.should_compress_info(thresh_128)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "threshold_boundary_equal",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:2253-2294",
                "agent/context_compressor.py:2490-2500",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor.should_compress_info at agent/context_compressor.py:2490-2500. "
                "Hermes checks tokens < threshold_tokens; at equality (tokens >= threshold_tokens), it fires."
            ),
        },
        "input": {
            "messages": [{"role": "user", "content": "ping"}],
            "usage": {"input_tokens": thresh_128, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": thresh_128 + 10},
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {"threshold": 0.50, "context_length": 128000},
            "summary_responses": [],
        },
        "expect": {
            "trigger": {"fires": fires_equal, "tokens": thresh_128, "limit": thresh_128, "severity": "soft"},
        },
    })

    # -------------------------------------------------------------
    # 3. Threshold: Large context window (> 512K, no small context floor)
    # -------------------------------------------------------------
    cc_1m = ContextCompressor(model="hermes-3", config_context_length=1000000, threshold_percent=0.50)
    thresh_1m = cc_1m.threshold_tokens
    fires_1m, _ = cc_1m.should_compress_info(thresh_1m)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "threshold_large_context",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:2253-2294",
                "agent/context_compressor.py:2490-2500",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._effective_threshold_percent at agent/context_compressor.py:2253-2258 and "
                "_compute_threshold_tokens at agent/context_compressor.py:2260-2294. "
                "Context length 1,000,000 >= 512K retains 50% threshold without 75% small-context bump. Limit is 500,000."
            ),
        },
        "input": {
            "messages": [{"role": "user", "content": "ping"}],
            "usage": {"input_tokens": thresh_1m, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": thresh_1m + 10},
            "context_window": 1000000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {"threshold": 0.50, "context_length": 1000000},
            "summary_responses": [],
        },
        "expect": {
            "trigger": {"fires": fires_1m, "tokens": thresh_1m, "limit": thresh_1m, "severity": "soft"},
        },
    })

    # -------------------------------------------------------------
    # 4. Threshold: Output reservation (max_tokens subtracted from window)
    # -------------------------------------------------------------
    cc_res = ContextCompressor(model="hermes-3", config_context_length=128000, threshold_percent=0.50, max_tokens=16000)
    thresh_res = cc_res.threshold_tokens
    fires_res, _ = cc_res.should_compress_info(thresh_res)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "threshold_output_reservation",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:2280-2294",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._compute_threshold_tokens at agent/context_compressor.py:2280-2294 with max_tokens=16000. "
                "Usable input budget is (128000 - 16000) = 112000. At 75% small-context threshold, limit is 84000."
            ),
        },
        "input": {
            "messages": [{"role": "user", "content": "ping"}],
            "usage": {"input_tokens": thresh_res, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": thresh_res + 10},
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {"threshold": 0.50, "context_length": 128000, "max_tokens": 16000},
            "summary_responses": [],
        },
        "expect": {
            "trigger": {"fires": fires_res, "tokens": thresh_res, "limit": thresh_res, "severity": "soft"},
        },
    })

    # -------------------------------------------------------------
    # 5. Threshold: Small context 64K capped at 85%
    # -------------------------------------------------------------
    cc_64 = ContextCompressor(model="hermes-3", config_context_length=64000, threshold_percent=0.50)
    thresh_64 = cc_64.threshold_tokens
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "threshold_small_context_capped",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:2284-2294",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._compute_threshold_tokens at agent/context_compressor.py:2284-2294. "
                "64K window with 64K floor would equal 100% of window; clamped to 85% (54400) to ensure reachability."
            ),
        },
        "input": {
            "messages": [{"role": "user", "content": "ping"}],
            "usage": {"input_tokens": thresh_64, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": thresh_64 + 10},
            "context_window": 64000,
            "max_input_tokens": None,
            "max_output_tokens": 4096,
            "reason": "threshold",
            "native_settings": {"threshold": 0.50, "context_length": 64000},
            "summary_responses": [],
        },
        "expect": {
            "trigger": {"fires": True, "tokens": thresh_64, "limit": thresh_64, "severity": "soft"},
        },
    })

    # -------------------------------------------------------------
    # 6. Selection: Fresh session protected head (system + protect_first_n=3)
    # -------------------------------------------------------------
    cc_fresh = ContextCompressor(model="hermes-3", config_context_length=128000, protect_first_n=3)
    msgs_fresh = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "U3"},
        {"role": "assistant", "content": "A3"},
        {"role": "user", "content": "U4"},
        {"role": "assistant", "content": "A4"},
    ]
    start_fresh, end_fresh = cc_fresh._compress_window(msgs_fresh)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "selection_protected_head_fresh",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3929-3937",
                "agent/context_compressor.py:4279-4281",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._protect_head_size at agent/context_compressor.py:3929-3937 and "
                "_compress_window at agent/context_compressor.py:4279-4281. "
                "Fresh session: system (1) + protect_first_n (3) = 4 protected messages in prefix. prefix_end is 4."
            ),
        },
        "input": {
            "messages": msgs_fresh,
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {"protect_first_n": 3},
            "summary_responses": [],
        },
        "expect": {
            "selection": {
                "prefix_end": start_fresh,
                "first_kept_index": end_fresh,
                "summarize": list(range(start_fresh, end_fresh)),
                "turn_prefix": [],
                "replay": [],
                "targets": [],
            },
        },
    })

    # -------------------------------------------------------------
    # 7. Selection: Decayed head after previous compression
    # -------------------------------------------------------------
    cc_decayed = ContextCompressor(model="hermes-3", config_context_length=128000, protect_first_n=3)
    cc_decayed.compression_count = 1
    start_decayed, end_decayed = cc_decayed._compress_window(msgs_fresh)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "selection_protected_head_decayed",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3917-3927",
                "agent/context_compressor.py:3929-3937",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._effective_protect_first_n at agent/context_compressor.py:3917-3927 and "
                "_protect_head_size at agent/context_compressor.py:3929-3937. "
                "Decayed session: compression_count >= 1 causes protect_first_n to decay to 0; only system message remains in prefix_end (1)."
            ),
        },
        "input": {
            "messages": msgs_fresh,
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {"protect_first_n": 3},
            "summary_responses": [],
        },
        "expect": {
            "selection": {
                "prefix_end": start_decayed,
                "first_kept_index": end_decayed,
                "summarize": list(range(start_decayed, end_decayed)),
                "turn_prefix": [],
                "replay": [],
                "targets": [],
            },
        },
    })

    # -------------------------------------------------------------
    # 8. Selection: Tail tool pairing (boundary backward alignment)
    # -------------------------------------------------------------
    cc_pair = ContextCompressor(model="hermes-3", config_context_length=128000, protect_first_n=1)
    msgs_pair = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "U1"},
        {
            "role": "assistant",
            "content": "calling",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "res1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
    ]
    paired_start, paired_end = cc_pair._compress_window(msgs_pair)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "selection_tail_tool_pairing",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3938-3946",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._align_boundary_backward at agent/context_compressor.py:3938-3946. "
                "When cut boundary lands on assistant message following a tool group (idx 4), pulls it back to parent assistant call (idx 2)."
            ),
        },
        "input": {
            "messages": msgs_pair,
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": [],
        },
        "expect": {
            "selection": {
                "prefix_end": paired_start,
                "first_kept_index": paired_end,
            },
        },
    })

    # -------------------------------------------------------------
    # 9. Pruning: Duplicate tool result deduplication
    # -------------------------------------------------------------
    cc_prune = ContextCompressor(model="hermes-3", config_context_length=128000)
    large_payload = "X" * 2500
    msgs_dup = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "call 1", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": large_payload},
        {"role": "assistant", "content": "call 2", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": large_payload},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    pruned_msgs, pruned_count = cc_prune._prune_old_tool_results(msgs_dup, protect_tail_count=2, protect_tail_tokens=1000)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "pruning_duplicate_tool_results",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:1286-1304",
                "agent/context_compressor.py:2755-2787",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._prune_old_tool_results at agent/context_compressor.py:2755-2787 "
                "and _dedupe_tool_results at agent/context_compressor.py:1286-1304. "
                "Duplicate tool result is replaced with backreference stub referencing the SHA-256 hash prefix."
            ),
        },
        "input": {
            "messages": msgs_dup,
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": [],
        },
        "expect": {
            "edits": [
                {"index": 3, "message": pruned_msgs[3]},
            ],
        },
    })

    # -------------------------------------------------------------
    # 10. Summary request: Fresh summary prompt structure
    # -------------------------------------------------------------
    cc_req = ContextCompressor(model="hermes-3", config_context_length=128000)
    turns_text = "User: Fix the bug\nAssistant: Investigating the crash"
    prompt_fresh = cc_req._build_summary_prompt(
        content_to_summarize=turns_text,
        summary_budget=2000,
        focus_topic=None,
        memory_context="",
        has_user_turn=True,
    )
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "summary_request_fresh",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3352-3407",
                "agent/context_compressor.py:3426-3483",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._build_summary_prompt at agent/context_compressor.py:3352-3407. "
                "Hermes summary request is issued as a user-only prompt to call_llm(task='compression') with no tools and no system message."
            ),
        },
        "input": {
            "messages": [
                {"role": "user", "content": "Fix the bug"},
                {"role": "assistant", "content": "Investigating the crash"},
            ],
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": ["Historical Task: Fix the bug\n## Completed Actions\n1. Found crash"],
        },
        "expect": {
            "summary_requests": [
                {
                    "system": None,
                    "messages": [{"role": "user", "content": prompt_fresh}],
                    "max_tokens": None,
                    "tools": [],
                }
            ],
        },
    })

    # -------------------------------------------------------------
    # 11. Iterative summary update: prompt includes PREVIOUS SUMMARY
    # -------------------------------------------------------------
    cc_iter = ContextCompressor(model="hermes-3", config_context_length=128000)
    cc_iter._previous_summary = "Previous summary: investigated the bug."
    prompt_iter = cc_iter._build_summary_prompt(
        content_to_summarize="User: Apply patch\nAssistant: Applied patch",
        summary_budget=2000,
        focus_topic=None,
        memory_context="",
        has_user_turn=True,
    )
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "summary_request_iterative",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3373-3388",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._build_summary_prompt at agent/context_compressor.py:3373-3388 with _previous_summary populated. "
                "Prompt includes PREVIOUS SUMMARY block and instructions to update existing state."
            ),
        },
        "input": {
            "messages": [
                {"role": "user", "content": "Apply patch"},
                {"role": "assistant", "content": "Applied patch"},
            ],
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": ["Updated summary: Patch applied"],
        },
        "expect": {
            "summary_requests": [
                {
                    "system": None,
                    "messages": [{"role": "user", "content": prompt_iter}],
                    "max_tokens": None,
                    "tools": [],
                }
            ],
        },
    })

    # -------------------------------------------------------------
    # 12. Placement: Assistant summary standalone (head has user, tail starts with user)
    # -------------------------------------------------------------
    cc_place1 = ContextCompressor(model="hermes-3", config_context_length=128000)
    head_msgs1 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "U1"}]
    tail_msgs1 = [{"role": "user", "content": "U2"}, {"role": "assistant", "content": "A2"}]
    role1, merge1, force_user1, _ = cc_place1._summary_placement(head_msgs1, tail_msgs1, compress_start=2)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "placement_assistant_standalone",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:4513-4546",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._summary_placement at agent/context_compressor.py:4513-4546. "
                "Last head role is 'user'; tail starts with 'user'. summary_role alternates to 'assistant' and places standalone between head and tail."
            ),
        },
        "input": {
            "messages": [*head_msgs1, {"role": "assistant", "content": "middle"}, *tail_msgs1],
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": ["Compact summary"],
        },
        "expect": {
            "projected_view": [
                *head_msgs1,
                {
                    "role": role1,
                    "content": "Compact summary\n\n" + _SUMMARY_END_MARKER,
                    "_compressed_summary": True,
                    "_compressed_summary_has_user_turn": True,
                },
                *tail_msgs1,
            ],
        },
    })

    # -------------------------------------------------------------
    # 13. Placement: User summary merged into tail (system-only head, tail starts with user)
    # -------------------------------------------------------------
    cc_place2 = ContextCompressor(model="hermes-3", config_context_length=128000)
    head_msgs2 = [{"role": "system", "content": "sys"}]
    tail_msgs2 = [{"role": "user", "content": "U2"}, {"role": "assistant", "content": "A2"}]
    role2, merge2, force_user2, first_tail_idx2 = cc_place2._summary_placement(head_msgs2, tail_msgs2, compress_start=0)
    merged_tail_0 = dict(tail_msgs2[0])
    cc_place2._merge_summary_into_tail_row(merged_tail_0, "Compact summary", role2, force_user2)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "placement_user_merged",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:4528-4546",
                "agent/context_compressor.py:4548-4567",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor._summary_placement at agent/context_compressor.py:4528-4546 and "
                "_merge_summary_into_tail_row at agent/context_compressor.py:4548-4567. "
                "System-only head forces user summary. Tail starts with user, creating collision that merges summary into first tail user message."
            ),
        },
        "input": {
            "messages": [*head_msgs2, {"role": "user", "content": "old_u"}, {"role": "assistant", "content": "old_a"}, *tail_msgs2],
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": ["Compact summary"],
        },
        "expect": {
            "projected_view": [
                *head_msgs2,
                merged_tail_0,
                tail_msgs2[1],
            ],
        },
    })

    # -------------------------------------------------------------
    # 14. Failure guard: Empty summary content rejected
    # -------------------------------------------------------------
    cc_fail1 = ContextCompressor(model="hermes-3", config_context_length=128000)
    empty_exc_msg = ""
    with patch("agent.context_compressor.call_llm", return_value=MockResp("   ")):
        try:
            cc_fail1._call_summary_llm("test prompt", time.time())
        except RuntimeError as exc:
            empty_exc_msg = str(exc)

    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "failure_guard_empty_content",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3276-3277",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                f"Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                f"Calls ContextCompressor._call_summary_llm at agent/context_compressor.py:3220-3277. "
                f"Scripted mock response returning whitespace raises verified upstream RuntimeError: '{empty_exc_msg}'."
            ),
        },
        "input": {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
            ],
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": ["   "],
        },
        "expect": {
            "failure": {
                "kind": "empty_content",
                "message": empty_exc_msg,
            },
        },
    })

    # -------------------------------------------------------------
    # 15. Failure guard: Truncated output (finish_reason = length)
    # -------------------------------------------------------------
    cc_fail2 = ContextCompressor(model="hermes-3", config_context_length=128000)
    length_exc_msg = ""
    with patch("agent.context_compressor.call_llm", return_value=MockResp("Partial...", finish_reason="length")):
        try:
            cc_fail2._call_summary_llm("test prompt", time.time())
        except RuntimeError as exc:
            length_exc_msg = str(exc)

    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "failure_guard_length_truncated",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:3288-3292",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                f"Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                f"Calls ContextCompressor._call_summary_llm at agent/context_compressor.py:3220-3292. "
                f"Scripted mock response with finish_reason='length' raises verified upstream RuntimeError: '{length_exc_msg}'."
            ),
        },
        "input": {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
            ],
            "usage": None,
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": [{"content": "Incomplete...", "finish_reason": "length"}],
        },
        "expect": {
            "failure": {
                "kind": "length_truncated",
                "message": length_exc_msg,
            },
        },
    })

    # -------------------------------------------------------------
    # 16. Failure guard: Summary failure cooldown blocks subsequent auto-compaction
    # -------------------------------------------------------------
    cc_cd = ContextCompressor(model="hermes-3", config_context_length=128000)
    cc_cd._summary_failure_cooldown_until = 130.0
    with patch("time.monotonic", return_value=100.0):
        fires_cd, reason_cd = cc_cd.should_compress_info(cc_cd.threshold_tokens + 5000)
    cases.append({
        "schema": "bb.compaction_oracle_case.v1",
        "preset": PRESET_ID,
        "case": "failure_guard_cooldown_blocked",
        "source": {
            "repo": REPO_URL,
            "commit": COMMIT_SHA,
            "evidence": [
                "agent/context_compressor.py:2490-2509",
                "agent/context_compressor.py:2540-2548",
            ],
        },
        "capture": {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_hermes.py",
            "notes": (
                "Executed via import: ContextCompressor from agent/context_compressor.py:1678-4949. "
                "Calls ContextCompressor.should_compress_info at agent/context_compressor.py:2490-2509. "
                "When failure cooldown is active, should_compress returns False even if tokens > threshold_tokens, blocking automatic retry thrashing."
            ),
        },
        "input": {
            "messages": [{"role": "user", "content": "ping"}],
            "usage": {"input_tokens": cc_cd.threshold_tokens + 5000, "output_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "total_tokens": cc_cd.threshold_tokens + 5010},
            "context_window": 128000,
            "max_input_tokens": None,
            "max_output_tokens": 16000,
            "reason": "threshold",
            "native_settings": {},
            "summary_responses": [],
        },
        "expect": {
            "trigger": {
                "fires": fires_cd,
                "tokens": cc_cd.threshold_tokens + 5000,
                "limit": cc_cd.threshold_tokens,
                "severity": "soft",
            },
        },
    })

    for case in cases:
        inp = case["input"]
        name = case["case"]
        if "selection" in case["expect"]:
            inp["stage"] = "summary"
        if name == "selection_protected_head_decayed":
            inp["prior_compactions"] = cc_decayed.compression_count
        if name == "selection_tail_tool_pairing":
            inp["native_settings"]["protect_first_n"] = cc_pair.protect_first_n
            case["capture"]["notes"] = "Executed _compress_window on the complete paired transcript, including both boundary alignments."
        if name == "pruning_duplicate_tool_results":
            inp["stages"] = ["prune"]
            case["capture"]["notes"] = "Executed _prune_old_tool_results; duplicate detection uses MD5 and preserves the newest copy (agent/context_compressor.py:2641-2656)."
        if name.startswith("summary_request"):
            inp["component"] = "summary_request"
            inp["stage"] = "summary"
            inp["summary_input"] = {
                "conversation": turns_text if name.endswith("fresh") else "User: Apply patch\nAssistant: Applied patch",
                "previous_summary": "" if name.endswith("fresh") else cc_iter._previous_summary,
                "summary_budget": 2000,
                "has_user_turn": True,
            }
        if name.startswith("placement"):
            inp["component"] = "placement"
            inp["stage"] = "summary"
            inp["summary_responses"] = []
            standalone = name.endswith("standalone")
            inp["placement_input"] = {
                "selection": {"prefix_end": len(head_msgs1) if standalone else len(head_msgs2),
                              "first_kept_index": 3},
                "summary": "Compact summary",
                "details": {"has_user_turn": True if standalone else bool(cc_place2._summary_has_user_turn)},
            }
        if name in {"failure_guard_empty_content", "failure_guard_length_truncated"}:
            inp["component"] = "summary_request"
            inp["stage"] = "summary"
            inp["native_settings"]["summary_model"] = cc_fail1.model
            inp["summary_input"] = {"conversation": "test prompt", "previous_summary": "",
                                    "summary_budget": 2000, "has_user_turn": True}
        if name == "failure_guard_cooldown_blocked":
            inp["trigger_blocked"] = cc_cd._summary_failure_cooldown_until > 100.0

    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Hermes compaction oracle cases.")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the Hermes source checkout at commit 939e45c91d751fadd94dcd1b873ac3cb44846213",
    )
    args = parser.parse_args()

    worktrees = [Path(__file__).resolve().parents[2]]

    oracle_dirs = [wt / "tests/compaction/oracles" / PRESET_ID for wt in worktrees]

    print(f"Capturing cases for {PRESET_ID}...")

    print("Executing Hermes functions to generate oracle cases...")
    sys.path.insert(0, str(args.source))
    with patch("agent.context_compressor._today_for_prompt", return_value=""):
        cases = capture_cases(args.source)
    print(f"Generated {len(cases)} oracle cases.")

    for oracle_dir in oracle_dirs:
        oracle_dir.mkdir(parents=True, exist_ok=True)
        for c in cases:
            case_file = oracle_dir / f"{c['case']}.json"
            case_file.write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8")

    executed_count = sum(1 for c in cases if c["capture"]["kind"] == "executed")
    derived_count = sum(1 for c in cases if c["capture"]["kind"] == "source_derived")
    print(f"Captured {len(cases)} cases (Executed: {executed_count}, Derived: {derived_count})")


if __name__ == "__main__":
    main()
