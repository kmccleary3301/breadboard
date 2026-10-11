"""pi scenarios responsibility owner."""
from __future__ import annotations
import json
from typing import Any, Dict, List

def build_long_session_scenario(context_window: int = 16000) -> List[Dict[str, Any]]:
    """Build scripted responses that exercise:
    - several tool turns before first compaction
    - overflow 400 -> first compaction
    - retried request with compacted history -> subsequent tool call
    - overflow 400 -> second compaction (carrying previous summary)
    - retried request -> repeated overflow terminating at Pi 1-attempt bound.
    """
    summary_1 = (
        "## Goal\nAnalyze files\n\n## Constraints & Preferences\n- Standard execution\n\n"
        "## Progress\n### Done\n- Steps 1-3 complete\n\n## Key Decisions\n- (none)\n\n## Next Steps\n1. Continue analysis"
    )
    summary_2 = (
        "## Goal\nAnalyze files\n\n## Constraints & Preferences\n- Standard execution\n\n"
        "## Progress\n### Done\n- Steps 1-4 complete\n\n## Key Decisions\n- (none)\n\n## Next Steps\n1. Finish analysis"
    )
    return [
        # Five 18k tool outputs exceed the pinned 20k-token keep budget while
        # each observation stays below the existing 20k-byte test envelope.
        {
            "type": "response",
            "tool_calls": [
                {"name": "bash", "call_id": "call_1a", "arguments": json.dumps({"command": "printf 'a%.0s' {1..18000}; printf '\\n'"})},
                {"name": "bash", "call_id": "call_1b", "arguments": json.dumps({"command": "printf 'b%.0s' {1..18000}; printf '\\n'"})},
            ],
            "usage": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
        },
        {
            "type": "response",
            "tool_calls": [
                {"name": "bash", "call_id": "call_2a", "arguments": json.dumps({"command": "printf 'c%.0s' {1..18000}; printf '\\n'"})},
                {"name": "bash", "call_id": "call_2b", "arguments": json.dumps({"command": "printf 'd%.0s' {1..18000}; printf '\\n'"})},
            ],
            "usage": {"input_tokens": 1200, "output_tokens": 50, "total_tokens": 1250},
        },
        {
            "type": "tool_call", "name": "bash", "call_id": "call_3",
            "arguments": json.dumps({"command": "printf 'e%.0s' {1..18000}; printf '\\n'"}),
            "usage": {"input_tokens": 1400, "output_tokens": 50, "total_tokens": 1450},
        },
        # Req 3: Tool result 3 -> 400 overflow (triggers compaction 1)
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"Context length exceeded ({context_window})",
            "status_code": 400,
        },
        # Req 4: Compaction 1 summary request
        {
            "type": "text",
            "text": summary_1,
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
        # Req 5: Retried request with compacted history -> Tool call 4
        {
            "type": "tool_call",
            "name": "bash",
            "call_id": "call_4",
            "arguments": json.dumps({"command": "echo step 4"}),
            "usage": {"input_tokens": 2500, "output_tokens": 50, "total_tokens": 2550},
        },
        # Req 6: Tool result 4 -> 400 overflow (triggers compaction 2)
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"Context length exceeded ({context_window})",
            "status_code": 400,
        },
        # Req 7: Compaction 2 summary request (carrying summary 1)
        {
            "type": "text",
            "text": summary_2,
            "usage": {"input_tokens": 2200, "output_tokens": 100, "total_tokens": 2300},
        },
        # Req 8: Retried request with compacted history -> 400 overflow (repeated failure terminal)
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"Context length exceeded ({context_window}) second failure",
            "status_code": 400,
        },
    ]

def build_repeated_failure_scenario(context_window: int = 16000) -> List[Dict[str, Any]]:
    """Build scripted responses that force REPEATED FAILURE:

    1. Initial agent request -> overflows (context_length_exceeded).
    2. Summary request -> succeeds.
    3. Retried agent request -> overflows again (second overflow is terminal).
    """
    summary_text = "## Goal\nTest repeated overflow failure.\n\n## Progress\nCompacted once."
    return [
        # Req 1: Initial request overflows
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"This model maximum context length is {context_window} tokens.",
            "status_code": 400,
        },
        # Req 2: Compaction summary
        {
            "type": "text",
            "text": summary_text,
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
        # Req 3: Retried request overflows again -> Terminal failure
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"This model maximum context length is {context_window} tokens. Second failure.",
            "status_code": 400,
        },
    ]

def build_final_over_threshold_scenario(
    context_window: int = 16000,
    reserve_tokens: int = 1000,
) -> List[Dict[str, Any]]:
    """Build scripted responses where final response reports usage over threshold but under window:
    - Req 0: Assistant text response with totalTokens > context_window - reserve_tokens
    - Req 1: If harness compacts at agent_end, provider receives summary request
    """
    threshold = context_window - reserve_tokens
    tokens = threshold + 100
    return [
        {
            "type": "text",
            "text": "Task finished. Here is the complete final report.",
            "usage": {"input_tokens": tokens - 500, "output_tokens": 500, "total_tokens": tokens},
        },
        {
            "type": "text",
            "text": "## Goal\nComplete task\n\n## Progress\n### Done\n- Completed all tasks\n\n## Next Steps\n1. Conclude",
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
    ]
