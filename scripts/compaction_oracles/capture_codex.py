#!/usr/bin/env python3
"""capture_codex.py

Generates and validates compaction oracle cases for codex@0.139.0.
Source pin: https://github.com/openai/codex commit a7dff904308535e965aee87680c1fc5ef1d19eec.

Cases cover:
1. threshold Total scope 90% of window with >= (below: does not fire)
2. threshold Total scope 90% of window with >= (equal: fires)
3. threshold Total scope 90% of window with >= (above: fires)
4. configured limit clamp (config limit clamped to floor(90% window))
5. usage_plus_trailing accounting (last usage total + bytes/4 estimates of trailing items)
6. bytes/4 estimator incl. image 7373-byte rule
7. local compaction: summary request (history + prompt as user input, no tools)
8. local installed history = retained user messages within 20000 approx tokens with middle truncation + summary
9. local installed history excludes existing summary messages
10. local installed history empty summary -> '(no summary available)'
11. mid-turn only when follow-up needed
12. overflow is terminal for ordinary sampling (no inline compaction)
13. remote v2 retention 64000 (record as native, source_derived)
"""

import json
import os
import sys
from pathlib import Path

CODEX_REPO = "https://github.com/openai/codex"
CODEX_COMMIT = "a7dff904308535e965aee87680c1fc5ef1d19eec"
PRESET_ID = "codex@0.139.0"

SUMMARY_PREFIX = (
    "Another language model started to solve this problem and produced a summary "
    "of its thinking process. You also have access to the state of the tools that "
    "were used by that language model. Use this to build on the work that has already "
    "been done and avoid duplicating work. Here is the summary produced by the other "
    "language model, use the information in this summary to assist with your own analysis:"
)

SUMMARIZATION_PROMPT = (
    "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.\n\n"
    "Include:\n"
    "- Current progress and key decisions made\n"
    "- Important context, constraints, or user preferences\n"
    "- What remains to be done (clear next steps)\n"
    "- Any critical data, examples, or references needed to continue\n\n"
    "Be concise, structured, and focused on helping the next LLM seamlessly continue the work.\n"
)


def approx_token_count(text: str) -> int:
    b = len(text.encode("utf-8"))
    return (b + 3) // 4


def truncate_middle_chars(s: str, max_bytes: int) -> str:
    pass


def build_cases(target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    cases = []

    def emit(case_name: str, payload: dict):
        out_file = target_dir / f"{case_name}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        cases.append(case_name)
        print(f"Wrote {out_file.name}")

    # Case 1: threshold Total scope 90% below
    emit(
        "threshold_total_scope_below",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "threshold_total_scope_below",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/protocol/src/openai_models.rs:430-441",
                    "codex-rs/core/src/session/turn.rs:716-723,744-745",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/protocol/src/openai_models.rs:433 derives automatic limit as floor(context_window * 9 / 10). "
                    "For window=100000, limit is 90000. codex-rs/core/src/session/turn.rs:744-745 evaluates "
                    "auto_compact_scope_tokens >= auto_compact_scope_limit. Tokens 89999 < 90000, so trigger does not fire."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Analyze the codebase."},
                    {"role": "assistant", "content": "Beginning analysis."},
                ],
                "usage": {
                    "input_tokens": 85000,
                    "output_tokens": 4999,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 89999,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "model_auto_compact_token_limit_scope": "total"
                },
            },
            "expect": {
                "trigger": {
                    "fires": False,
                    "tokens": 89999,
                    "limit": 90000,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 2: threshold Total scope 90% equal
    emit(
        "threshold_total_scope_equal",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "threshold_total_scope_equal",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/protocol/src/openai_models.rs:430-441",
                    "codex-rs/core/src/session/turn.rs:716-723,744-745",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/protocol/src/openai_models.rs:433 derives automatic limit as floor(context_window * 9 / 10). "
                    "codex-rs/core/src/session/turn.rs:745 compares with >=: "
                    "'auto_compact_scope_tokens >= auto_compact_scope_limit'. At exact equality (90000 >= 90000), "
                    "token_limit_reached is true and trigger fires."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Analyze the codebase."},
                    {"role": "assistant", "content": "Beginning analysis."},
                ],
                "usage": {
                    "input_tokens": 85000,
                    "output_tokens": 5000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 90000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "model_auto_compact_token_limit_scope": "total"
                },
            },
            "expect": {
                "trigger": {
                    "fires": True,
                    "tokens": 90000,
                    "limit": 90000,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 3: threshold Total scope 90% above
    emit(
        "threshold_total_scope_above",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "threshold_total_scope_above",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/protocol/src/openai_models.rs:430-441",
                    "codex-rs/core/src/session/turn.rs:716-723,744-745",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/protocol/src/openai_models.rs:433 derives limit 90000 for window 100000. "
                    "codex-rs/core/src/session/turn.rs:745 compares auto_compact_scope_tokens (90001) >= auto_compact_scope_limit (90000), "
                    "which evaluates to true."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Analyze the codebase."},
                    {"role": "assistant", "content": "Beginning analysis."},
                ],
                "usage": {
                    "input_tokens": 85000,
                    "output_tokens": 5001,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 90001,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "model_auto_compact_token_limit_scope": "total"
                },
            },
            "expect": {
                "trigger": {
                    "fires": True,
                    "tokens": 90001,
                    "limit": 90000,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 4: configured limit clamp
    emit(
        "threshold_configured_limit_clamp",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "threshold_configured_limit_clamp",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/protocol/src/openai_models.rs:430-441",
                    "codex-rs/core/tests/suite/compact.rs:3113-3171",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "Matches upstream test 'auto_compact_clamps_config_limit_to_context_window' in "
                    "codex-rs/core/tests/suite/compact.rs:3114-3171. Upstream sets context_window = 100, "
                    "config_limit = 200, over_limit_tokens = context_window * 90 / 100 + 1 = 91. "
                    "codex-rs/protocol/src/openai_models.rs:437 clamps: "
                    "'config_limit.map_or(context_limit, |limit| std::cmp::min(limit, context_limit))'. "
                    "min(200, 90) = 90. 91 tokens >= clamped limit 90 triggers auto-compaction."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "OVER_LIMIT_TURN"},
                    {"role": "assistant", "content": "REPLY"},
                ],
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 11,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 91,
                },
                "context_window": 100,
                "max_input_tokens": None,
                "max_output_tokens": 30,
                "reason": "threshold",
                "native_settings": {
                    "model_auto_compact_token_limit": 200,
                    "model_auto_compact_token_limit_scope": "total",
                },
            },
            "expect": {
                "trigger": {
                    "fires": True,
                    "tokens": 91,
                    "limit": 90,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 5: usage_plus_trailing accounting
    emit(
        "accounting_usage_plus_trailing",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "accounting_usage_plus_trailing",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/context_manager/history.rs:285-314",
                    "codex-rs/core/src/context_manager/history_tests.rs:237-278",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/context_manager/history.rs:296-314 get_total_token_usage adds "
                    "last_token_usage.total_tokens plus estimated tokens of items after the last model-generated item. "
                    "Here last usage was 51000. Trailing item is user message 'Follow-up question' (18 UTF-8 bytes). "
                    "codex-rs/utils/string/src/truncate.rs:71-74 approx_token_count computes (18 + 3) / 4 = 5 tokens. "
                    "Active context tokens = 51000 + 5 = 51005 tokens."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Initial prompt"},
                    {"role": "assistant", "content": "Done"},
                    {"role": "user", "content": "Follow-up question"},
                ],
                "usage": {
                    "input_tokens": 50000,
                    "output_tokens": 1000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 51000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "model_auto_compact_token_limit_scope": "total"
                },
            },
            "expect": {
                "trigger": {
                    "fires": False,
                    "tokens": 51005,
                    "limit": 90000,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 6: bytes/4 estimator incl. image 7373-byte rule
    emit(
        "accounting_bytes4_estimator_image_rule",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "accounting_bytes4_estimator_image_rule",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/context_manager/history.rs:484-536",
                    "codex-rs/core/src/context_manager/history_tests.rs:1737-1756",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/context_manager/history.rs:488 defines RESIZED_IMAGE_BYTES_ESTIMATE = 7373. "
                    "Lines 522-535 replace raw base64 payload bytes with 7373 bytes while preserving JSON wrappers. "
                    "7373 bytes ceil-divided by 4 yields 1844 tokens. Without this heuristic, a large raw base64 payload "
                    "(e.g. 100KB) would count as 25000 tokens and trigger compaction prematurely."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Analyze this screenshot"},
                    {"role": "assistant", "content": "Please provide it"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Screenshot:"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64," + "A" * 100000
                                },
                            },
                        ],
                    },
                ],
                "usage": {
                    "input_tokens": 87000,
                    "output_tokens": 1000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 88000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "model_auto_compact_token_limit_scope": "total"
                },
            },
            "expect": {
                "trigger": {
                    "fires": False,
                    "tokens": 89880,
                    "limit": 90000,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 7: local compaction: summary request
    emit(
        "local_summary_request",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "local_summary_request",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/compact.rs:77-83,225-230",
                    "codex-rs/prompts/templates/compact/prompt.md:1-10",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/compact.rs:77-83 builds prompt input containing SUMMARIZATION_PROMPT. "
                    "Lines 225-230 create Prompt { input: turn_input, base_instructions, personality, ..Default::default() }. "
                    "Default tools is empty ([]). Base instructions/system prompt is passed through."
                ),
            },
            "input": {
                "messages": [
                    {"role": "system", "content": "You are a helpful coding assistant."},
                    {"role": "user", "content": "Fix bug in foo.py"},
                    {"role": "assistant", "content": "I fixed the bug in foo.py"},
                ],
                "usage": {
                    "input_tokens": 92000,
                    "output_tokens": 1000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 93000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {},
                "summary_responses": ["Handoff checkpoint: bug fixed in foo.py."],
            },
            "expect": {
                "summary_requests": [
                    {
                        "system": "You are a helpful coding assistant.",
                        "messages": [
                            {"role": "user", "content": "Fix bug in foo.py"},
                            {"role": "assistant", "content": "I fixed the bug in foo.py"},
                            {"role": "user", "content": SUMMARIZATION_PROMPT},
                        ],
                        "max_tokens": None,
                        "tools": [],
                    }
                ]
            },
        },
    )

    # Case 8: local installed history = retained user messages within 20000 approx tokens with middle truncation + summary
    user1 = "Older user request: set up test environment."
    user2 = "Recent user request: run the tests."
    summary_body = "Checkpoint: environment configured, tests running."
    installed_summary = f"{SUMMARY_PREFIX}\n{summary_body}"

    emit(
        "local_installed_history_retained_users_budget",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "local_installed_history_retained_users_budget",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/compact.rs:49,512-575",
                    "codex-rs/core/src/compact_tests.rs:155-216",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/compact.rs:49 defines COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000. "
                    "Lines 525-549 build_compacted_history_with_limit iterates user messages in reverse, "
                    "fitting whole messages into 20,000 approx tokens, middle-truncating the first over-budget message, "
                    "restores chronological order, and appends the user summary message with SUMMARY_PREFIX (lines 568-573)."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": user1},
                    {"role": "assistant", "content": "Environment is set up."},
                    {"role": "user", "content": user2},
                    {"role": "assistant", "content": "Running test suite now."},
                ],
                "usage": {
                    "input_tokens": 92000,
                    "output_tokens": 1000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 93000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {},
                "summary_responses": [summary_body],
            },
            "expect": {
                "projected_view": [
                    {"role": "user", "content": user1},
                    {"role": "user", "content": user2},
                    {"role": "user", "content": installed_summary},
                ]
            },
        },
    )

    # Case 9: local installed history excludes existing summary messages
    emit(
        "local_installed_history_excludes_prior_summaries",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "local_installed_history_excludes_prior_summaries",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/compact.rs:435-453",
                    "codex-rs/core/src/compact_tests.rs:96-132",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/compact.rs:440-444,451-453 checks is_summary_message, which tests: "
                    "message.starts_with(format!('{SUMMARY_PREFIX}\\n').as_str()). Messages matching this prefix "
                    "are excluded from collected user messages, preventing duplicate summary accumulation."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Initial user request."},
                    {"role": "user", "content": f"{SUMMARY_PREFIX}\nPrevious summary text."},
                    {"role": "assistant", "content": "Continuing work after earlier compaction."},
                    {"role": "user", "content": "Follow-up request."},
                ],
                "usage": {
                    "input_tokens": 91000,
                    "output_tokens": 1000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 92000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {},
                "summary_responses": ["New unified summary."],
            },
            "expect": {
                "projected_view": [
                    {"role": "user", "content": "Initial user request."},
                    {"role": "user", "content": "Follow-up request."},
                    {"role": "user", "content": f"{SUMMARY_PREFIX}\nNew unified summary."},
                ]
            },
        },
    )

    # Case 10: local installed history empty summary -> '(no summary available)'
    emit(
        "local_installed_history_empty_summary_fallback",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "local_installed_history_empty_summary_fallback",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/compact.rs:562-566",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/compact.rs:562-566: "
                    "'let summary_text = if summary_text.is_empty() { \"(no summary available)\".to_string() } else { summary_text.to_string() };'. "
                    "An empty assistant response during summarization falls back to '(no summary available)'."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Write tests."},
                    {"role": "assistant", "content": "Writing tests."},
                ],
                "usage": {
                    "input_tokens": 91000,
                    "output_tokens": 1000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 92000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {},
                "summary_responses": [""],
            },
            "expect": {
                "projected_view": [
                    {"role": "user", "content": "Write tests."},
                    {"role": "user", "content": f"{SUMMARY_PREFIX}\n(no summary available)"},
                ]
            },
        },
    )

    # Case 11: mid-turn only when follow-up needed
    emit(
        "mid_turn_requires_follow_up",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "mid_turn_requires_follow_up",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/session/turn.rs:246-273",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/session/turn.rs:246 defines needs_follow_up = model_needs_follow_up || has_pending_input. "
                    "Line 273 checks: 'if token_limit_reached && needs_follow_up'. When a turn finishes with a complete answer "
                    "and no tool calls or pending input (needs_follow_up = false), mid-turn compaction does not fire, "
                    "leaving compaction to pre-turn check of next turn."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Complex query."},
                    {"role": "assistant", "content": "Complete final answer with no further tool calls."},
                ],
                "usage": {
                    "input_tokens": 92000,
                    "output_tokens": 2000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 94000,
                },
                "context_window": 100000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "phase": "mid_turn",
                    "needs_follow_up": False,
                },
            },
            "expect": {
                "trigger": {
                    "fires": False,
                    "tokens": 94000,
                    "limit": 90000,
                    "severity": "soft",
                }
            },
        },
    )

    # Case 12: overflow is terminal for ordinary sampling
    emit(
        "sampling_overflow_terminal",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "sampling_overflow_terminal",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/session/turn.rs:1033-1035,367-378",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/session/turn.rs:1033-1035 handles CodexErr::ContextWindowExceeded: "
                    "'sess.set_total_tokens_full(&turn_context).await; return Err(CodexErr::ContextWindowExceeded);'. "
                    "Lines 367-378 emit turn error lifecycle and break. There is no inline compaction retry for ordinary sampling overflow."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "Process huge input file."},
                ],
                "usage": None,
                "context_window": 128000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "overflow",
                "native_settings": {},
            },
            "expect": {
                "failure": {
                    "kind": "ContextWindowExceeded",
                    "message": "Context window exceeded during sampling; terminal overflow",
                }
            },
        },
    )

    # Case 13: remote v2 retention 64000
    emit(
        "remote_v2_retention_64000",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET_ID,
            "case": "remote_v2_retention_64000",
            "source": {
                "repo": CODEX_REPO,
                "commit": CODEX_COMMIT,
                "evidence": [
                    "codex-rs/core/src/compact_remote_v2.rs:49,421-484,586-640",
                ],
            },
            "capture": {
                "kind": "source_derived",
                "script": "scripts/compaction_oracles/capture_codex.py",
                "notes": (
                    "codex-rs/core/src/compact_remote_v2.rs:49 defines RETAINED_MESSAGE_TOKEN_BUDGET = 64_000. "
                    "Lines 425-439 filter input messages to user/developer/system, truncate to 64,000 approx tokens newest-first, "
                    "and append the single server-returned ResponseItem::Compaction item. Native compaction record."
                ),
            },
            "input": {
                "messages": [
                    {"role": "user", "content": "User prompt retained in remote v2."},
                    {"role": "assistant", "content": "Assistant turn omitted from retained."},
                ],
                "usage": {
                    "input_tokens": 180000,
                    "output_tokens": 5000,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 185000,
                },
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": 4096,
                "reason": "threshold",
                "native_settings": {
                    "features": ["RemoteCompactionV2"],
                },
                "summary_responses": ["enc_summary_v2"],
            },
            "expect": {
                "selection": {
                    "prefix_end": None,
                    "first_kept_index": 0,
                    "summarize": [],
                    "turn_prefix": [],
                    "replay": [0],
                    "targets": [],
                },
                "projected_view": [
                    {"role": "user", "content": "User prompt retained in remote v2."},
                    {
                        "role": "assistant",
                        "content": "",
                        "compaction": {"encrypted_content": "enc_summary_v2"},
                    },
                ],
            },
        },
    )

    return cases


def main():
    target_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "tests/compaction/oracles"
        / PRESET_ID
    )
    cases = build_cases(target_dir)
    print(f"Captured {len(cases)} cases for {PRESET_ID} in {target_dir}")


if __name__ == "__main__":
    main()
