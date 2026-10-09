#!/usr/bin/env python3
"""Capture oracle cases for openhands_sdk@1.47.0.

This script executes OpenHands SDK condensation components with a scripted LLM
and deterministic token counter, capturing triggers, selections, summary requests,
atomicity properties, failure recovery paths, and resulting projected views.

Usage:
    python scripts/compaction_oracles/capture_openhands.py \
        --sdk-source <software-agent-sdk checkout at 50080b58>/openhands-sdk \
        --venv <venv with openhands-sdk 1.47.0 dependencies> \
        --output-dir tests/compaction/oracles/openhands_sdk@1.47.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure zero network calls during litellm / openhands imports
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"


def check_and_reexec_in_venv(venv_path: str | None) -> None:
    if not venv_path:
        return
    venv_dir = Path(venv_path).resolve()
    # If already running inside this venv, do not re-exec
    if Path(sys.prefix).resolve() == venv_dir:
        return
    if os.environ.get("_OH_CAPTURED_REEXEC") == "1":
        return
    venv_python = venv_dir / "bin" / "python"
    if venv_python.exists():
        env = dict(os.environ)
        env["_OH_CAPTURED_REEXEC"] = "1"
        res = subprocess.run([str(venv_python), __file__, *sys.argv[1:]], env=env)
        sys.exit(res.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture OpenHands SDK 1.47.0 oracle cases")
    parser.add_argument(
        "--sdk-source",
        required=True,
        help="Path to pinned openhands-sdk source checkout (e.g. .../openhands-sdk)",
    )
    parser.add_argument(
        "--venv",
        required=True,
        help="Path to throwaway venv with openhands-sdk dependencies installed",
    )
    parser.add_argument(
        "--output-dir",
        default="tests/compaction/oracles/openhands_sdk@1.47.0",
        help="Destination directory for oracle cases",
    )
    args = parser.parse_args()

    check_and_reexec_in_venv(args.venv)

    sdk_source_path = Path(args.sdk_source).resolve()
    if not sdk_source_path.exists():
        raise FileNotFoundError(f"SDK source path not found: {sdk_source_path}")

    # Prioritize loading directly from the pinned source checkout
    sys.path.insert(0, str(sdk_source_path))

    import openhands.sdk

    imported_sdk_path = Path(openhands.sdk.__file__).resolve()
    # Assert that openhands.sdk sits under the specified sdk_source path
    assert imported_sdk_path.is_relative_to(sdk_source_path), (
        f"Imported openhands.sdk ({imported_sdk_path}) does not sit under --sdk-source ({sdk_source_path})"
    )

    import importlib.metadata
    sdk_version = importlib.metadata.version("openhands-sdk")
    assert sdk_version == "1.47.0", f"Expected openhands-sdk version 1.47.0, got {sdk_version}"

    from unittest.mock import MagicMock
    from litellm.types.utils import ModelResponse

    from openhands.sdk.context.condenser.base import CondensationRequirement, NoCondensationAvailableException
    from openhands.sdk.context.condenser.llm_summarizing_condenser import LLMSummarizingCondenser, Reason
    from openhands.sdk.context.condenser.pipeline_condenser import PipelineCondenser
    from openhands.sdk.context.view import View
    from openhands.sdk.event import ActionEvent, Condensation, CondensationRequest, CondensationSummaryEvent
    from openhands.sdk.event.base import Event, LLMConvertibleEvent
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.event.llm_convertible.observation import ObservationBaseEvent
    from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, MetricsSnapshot, TextContent, ThinkingBlock

    REPO_URL = "https://github.com/OpenHands/software-agent-sdk"
    COMMIT = "50080b58d35b4824fda25fca2345d80bcd08aeff"
    DIST_SHA256 = "350edf01b0ab2047941fa338995187e98605ccf946f49c432802fa25b2070cee"
    PRESET = "openhands_sdk@1.47.0"
    SCRIPT_PATH = "scripts/compaction_oracles/capture_openhands.py"

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    class CustomObservation(ObservationBaseEvent):
        content_text: str = ""

        def to_llm_message(self) -> Message:
            return Message(
                role="tool",
                tool_call_id=self.tool_call_id,
                content=[TextContent(text=self.content_text)],
            )

    def msg_to_bb_dict(msg: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": msg.role, "content": ""}
        if msg.content:
            texts = [c.text for c in msg.content if hasattr(c, "text")]
            d["content"] = "\n".join(texts) if len(texts) > 1 else (texts[0] if texts else "")
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in msg.tool_calls
            ]
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        return d

    def events_to_bb_messages(events: list[LLMConvertibleEvent]) -> list[dict[str, Any]]:
        sdk_messages = LLMConvertibleEvent.events_to_messages(events)
        return [msg_to_bb_dict(m) for m in sdk_messages]

    def create_mock_llm(
        summary_text: str = "Scripted summary of forgotten events",
        fail_times: int = 0,
        fail_exception: Exception | None = None,
    ) -> MagicMock:
        mock = MagicMock(spec=LLM)
        mock.model = "scripted-summary-llm"
        mock.stream = False
        mock.requires_streaming = False
        mock.log_completions = False
        mock.log_completions_folder = None
        mock.custom_tokenizer = None
        mock.base_url = None
        mock.reasoning_effort = None
        mock.litellm_extra_body = {}
        mock.temperature = 0.0
        mock.openrouter_site_url = "https://docs.all-hands.dev/"
        mock.openrouter_app_name = "OpenHands"
        mock.aws_access_key_id = None
        mock.aws_secret_access_key = None
        mock.aws_session_token = None
        mock.aws_region_name = None
        mock.aws_profile_name = None
        mock.aws_role_name = None
        mock.aws_session_name = None
        mock.aws_bedrock_runtime_endpoint = None
        mock.input_cost_per_token = None
        mock.output_cost_per_token = None
        mock.metrics = None
        mock._metrics = None
        mock._telemetry = None
        mock.uses_responses_api = lambda: False
        mock.format_messages_for_llm = lambda messages: messages

        call_count = 0

        def _completion(*_args: Any, **_kwargs: Any) -> LLMResponse:
            nonlocal call_count
            call_count += 1
            if call_count <= fail_times:
                raise (fail_exception or RuntimeError(f"Simulated failure {call_count}"))

            raw_resp = MagicMock(spec=ModelResponse)
            raw_resp.id = f"resp-{call_count}"
            resp_msg = Message(role="assistant", content=[TextContent(text=summary_text)])
            metrics = MetricsSnapshot(
                model_name="scripted-summary-llm",
                accumulated_cost=0.0,
                max_budget_per_task=None,
                accumulated_token_usage=None,
            )
            summary_calls.append(summary_text)
            return LLMResponse(message=resp_msg, metrics=metrics, raw_response=raw_resp)

        mock.completion.side_effect = _completion
        return mock

    def create_agent_llm(chars_per_token: int = 4, max_input: int = 1000) -> MagicMock:
        agent_llm = MagicMock(spec=LLM)
        agent_llm.model = "agent-counter-llm"
        agent_llm.effective_max_input_tokens = max_input

        def _count(messages: list[Message], **_kwargs: Any) -> int:
            total_chars = 0
            for msg in messages:
                for c in msg.content:
                    if hasattr(c, "text"):
                        total_chars += len(c.text)
            return total_chars // chars_per_token

        agent_llm.get_token_count.side_effect = _count
        return agent_llm

    captured_cases: list[str] = []
    summary_calls: list[str] = []
    """Summary texts the mocks returned since the last saved case, in call order."""

    def save_case(name: str, payload: dict[str, Any]) -> None:
        # The case input must describe what was executed: the scripted summary
        # responses are exactly the ones the pinned code consumed.
        recorded = payload["input"].get("summary_responses") or []
        assert recorded == summary_calls, f"{name}: input summary_responses {recorded} != executed {summary_calls}"
        summary_calls.clear()
        case_file = out_dir / f"{name}.json"
        with open(case_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        captured_cases.append(name)
        print(f"Captured case: {name}")

    notes_prefix = (
        f"Executed against pinned openhands-sdk v1.47.0 (commit {COMMIT}, dist sha256 {DIST_SHA256}) "
        f"loaded from {imported_sdk_path.relative_to(sdk_source_path.parent)}. "
    )

    # =========================================================================
    # Case 1: events_threshold_equal_does_not_fire
    # Evidence: llm_summarizing_condenser.py:154: `if len(view) > self.max_size:`
    # =========================================================================
    max_size = 10
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Turn {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(max_size)
    ]
    view = View.from_events(events)
    mock_llm = create_mock_llm()
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=max_size, keep_first=2)
    req = condenser.condensation_requirement(view)
    res = condenser.condense(view)

    save_case(
        "events_threshold_equal_does_not_fire",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "events_threshold_equal_does_not_fire",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:154"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "len(view) == max_size (10). Upstream llm_summarizing_condenser.py:154 uses strictly > (len(view) > self.max_size), so equality does not trigger.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 10, "keep_first": 2},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": False, "tokens": 0, "limit": 10, "severity": None},
                "projected_view": events_to_bb_messages(events),
            },
        },
    )

    # =========================================================================
    # Case 2: events_threshold_strictly_greater_fires
    # Evidence: llm_summarizing_condenser.py:154-180
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Turn {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(max_size + 1)
    ]
    view = View.from_events(events)
    mock_llm = create_mock_llm("Summary of events 2..7")
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=max_size, keep_first=2)
    req = condenser.condensation_requirement(view)
    cond = condenser.condense(view)
    assert isinstance(cond, Condensation)
    view_after = View.from_events(events)
    view_after.append_event(cond)

    summary_req_prompt = mock_llm.completion.call_args[1]["messages"][0].content[0].text

    save_case(
        "events_threshold_strictly_greater_fires",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "events_threshold_strictly_greater_fires",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:154",
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:178-180",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "len(view) == 11 > max_size 10. EVENTS reason yields CondensationRequirement.SOFT (lines 178-180). target_size=max_size//2=5, suffix=5-2-1=2.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 10, "keep_first": 2},
                "summary_responses": ["Summary of events 2..7"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 11, "limit": 10, "severity": "soft"},
                "selection": {
                    "prefix_end": 2,
                    "first_kept_index": 9,
                    "summarize": [2, 3, 4, 5, 6, 7, 8],
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
                "summary_requests": [
                    {
                        "system": None,
                        "messages": [{"role": "user", "content": summary_req_prompt}],
                        "max_tokens": None,
                        "tools": [],
                    }
                ],
                "projected_view": events_to_bb_messages(view_after.events),
            },
        },
    )

    # =========================================================================
    # Case 3: tokens_threshold_equal_does_not_fire
    # Evidence: llm_summarizing_condenser.py:143
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text="X" * 20)]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(5)
    ]
    view = View.from_events(events)
    agent_llm = create_agent_llm(chars_per_token=4, max_input=1000)
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=100, max_tokens=25, keep_first=1)
    reasons = condenser.get_condensation_reasons(view, agent_llm=agent_llm)

    save_case(
        "tokens_threshold_equal_does_not_fire",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "tokens_threshold_equal_does_not_fire",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:140-143"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "total_tokens == max_tokens (25). Upstream line 143 uses strictly > (total_tokens > max_tokens), so equality does not add Reason.TOKENS.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 1000,
                "max_input_tokens": 25,
                "max_output_tokens": None,
                "reason": "threshold",
                "native_settings": {"max_tokens": 25, "keep_first": 1},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": False, "tokens": 25, "limit": 25, "severity": None},
                "projected_view": events_to_bb_messages(events),
            },
        },
    )

    # =========================================================================
    # Case 4: tokens_threshold_strictly_greater_fires
    # Evidence: llm_summarizing_condenser.py:143,172-173
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text="X" * 20)]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(6)
    ]
    view = View.from_events(events)
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=100, max_tokens=25, keep_first=1)
    req = condenser.condensation_requirement(view, agent_llm=agent_llm)
    assert req == CondensationRequirement.HARD

    cond = condenser.condense(view, agent_llm=agent_llm)
    assert isinstance(cond, Condensation)
    view_after = View.from_events(events)
    view_after.append_event(cond)

    save_case(
        "tokens_threshold_strictly_greater_fires",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "tokens_threshold_strictly_greater_fires",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:143",
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:172-173",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "total_tokens (30) > max_tokens (25). Reason.TOKENS in reasons produces CondensationRequirement.HARD (lines 172-173).",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 1000,
                "max_input_tokens": 25,
                "max_output_tokens": None,
                "reason": "threshold",
                "native_settings": {"max_tokens": 25, "keep_first": 1},
                "summary_responses": ["Token reduction summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 30, "limit": 25, "severity": "hard"},
                "selection": {
                    "prefix_end": cond.summary_offset,
                    "first_kept_index": len(events) - (len(events) - len(cond.forgotten_event_ids) - cond.summary_offset),
                    "summarize": list(range(cond.summary_offset, cond.summary_offset + len(cond.forgotten_event_ids))),
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
                "projected_view": events_to_bb_messages(view_after.events),
            },
        },
    )

    # =========================================================================
    # Case 5: request_reason_fires_hard
    # Evidence: llm_summarizing_condenser.py:136, 186-187
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(8)
    ]
    view = View.from_events(events)
    view.append_event(CondensationRequest())
    assert view.unhandled_condensation_request is True

    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=100, keep_first=2)
    req = condenser.condensation_requirement(view)
    assert req == CondensationRequirement.HARD

    cond = condenser.condense(view)
    assert isinstance(cond, Condensation)
    view_after = View.from_events(events)
    view_after.append_event(cond)

    save_case(
        "request_reason_fires_hard",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "request_reason_fires_hard",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:136",
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:186-187",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "CondensationRequest in view sets view.unhandled_condensation_request=True. Line 136 adds Reason.REQUEST; lines 186-187 return CondensationRequirement.HARD.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"max_size": 100, "keep_first": 2},
                "summary_responses": ["Requested summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 8, "limit": 100, "severity": "hard"},
                "selection": {
                    "prefix_end": 2,
                    "first_kept_index": 7,
                    "summarize": [2, 3, 4, 5, 6],
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
                "projected_view": events_to_bb_messages(view_after.events),
            },
        },
    )

    # =========================================================================
    # Case 6: keep_first_prefix_preservation
    # Evidence: llm_summarizing_condenser.py:51-54, 310-311
    # =========================================================================
    keep_first = 4
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(16)
    ]
    view = View.from_events(events)
    view.append_event(CondensationRequest())
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=100, keep_first=keep_first)
    cond = condenser.condense(view)
    assert isinstance(cond, Condensation)
    assert cond.summary_offset == keep_first

    save_case(
        "keep_first_prefix_preservation",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "keep_first_prefix_preservation",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:51-54",
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:310-311",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "keep_first=4 guarantees prefix events 0..3 are preserved; forgetting_start (and summary_offset) is smallest manipulation index >= 4.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"max_size": 100, "keep_first": 4},
                "summary_responses": ["Preserved prefix summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 16, "limit": 100, "severity": "hard"},
                "selection": {
                    "prefix_end": 4,
                    "first_kept_index": len(events) - (len(events) // 2 - keep_first - 1),
                    "summarize": list(range(4, len(events) - (len(events) // 2 - keep_first - 1))),
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
            },
        },
    )

    # =========================================================================
    # Case 7: target_sizes_events_vs_request
    # Evidence: llm_summarizing_condenser.py:275-282, 305
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Turn {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(40)
    ]
    view = View.from_events(events)
    view.append_event(CondensationRequest())
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=30, keep_first=2)
    cond = condenser.condense(view)
    assert isinstance(cond, Condensation)
    assert len(cond.forgotten_event_ids) == 26

    save_case(
        "target_sizes_events_vs_request",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "target_sizes_events_vs_request",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:275-282",
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:305",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "REQUEST proposes target 20 (suffix 17), EVENTS proposes target 15 (suffix 12). min(17, 12)=12 selects the strictest suffix.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 30, "keep_first": 2},
                "summary_responses": ["Strictest reduction summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 40, "limit": 30, "severity": "hard"},
                "selection": {
                    "prefix_end": 2,
                    "first_kept_index": 28,
                    "summarize": list(range(2, 28)),
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
            },
        },
    )

    # =========================================================================
    # Case 8: minimum_progress_boundary_equality_succeeds
    # Evidence: llm_summarizing_condenser.py:390-394
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(20)
    ]
    view = View.from_events(events)
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=100, keep_first=2, minimum_progress=0.1)
    from unittest.mock import patch
    with patch.object(condenser, "_get_forgotten_events", return_value=(events[2:4], 2)):
        cond = condenser.get_condensation(view)
        assert isinstance(cond, Condensation)
        assert len(cond.forgotten_event_ids) == 2

    save_case(
        "minimum_progress_boundary_equality_succeeds",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "minimum_progress_boundary_equality_succeeds",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:390-394"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "len(forgotten)==2 == len(view)*0.1 (2.0). Line 390 is strictly < (len(forgotten) < len(view)*minimum_progress), so equality succeeds.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"minimum_progress": 0.1, "keep_first": 2},
                "summary_responses": ["Boundary equality summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 20, "limit": 100, "severity": None},
                "selection": {
                    "prefix_end": 2,
                    "first_kept_index": 4,
                    "summarize": [2, 3],
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
            },
        },
    )

    # =========================================================================
    # Case 9: minimum_progress_below_fails
    # Evidence: llm_summarizing_condenser.py:390-394
    # =========================================================================
    with patch.object(condenser, "_get_forgotten_events", return_value=(events[2:3], 2)):
        try:
            condenser.get_condensation(view)
            raised = False
        except NoCondensationAvailableException:
            raised = True
        assert raised

    save_case(
        "minimum_progress_below_fails",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "minimum_progress_below_fails",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:390-394"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "len(forgotten)==1 < len(view)*0.1 (2.0). Raises NoCondensationAvailableException per lines 390-394.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"minimum_progress": 0.1, "keep_first": 2},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 20, "limit": 100, "severity": None},
                "failure": {
                    "kind": "NoCondensationAvailableException",
                    "message": "Cannot apply condensation: events forgotten below minimum progress threshold.",
                },
            },
        },
    )

    # =========================================================================
    # Case 10: atomic_boundary_batch_atomicity
    # Evidence: batch_atomicity.py:49-76
    # =========================================================================
    call1 = MessageToolCall(id="call_1", name="bash", arguments='{"cmd":"ls"}', origin="completion")
    call2 = MessageToolCall(id="call_2", name="read", arguments='{"file":"a.txt"}', origin="completion")
    act1 = ActionEvent(thought=[TextContent(text="thinking")], tool_name="bash", tool_call_id="call_1", tool_call=call1, llm_response_id="batch_1")
    act2 = ActionEvent(thought=[], tool_name="read", tool_call_id="call_2", tool_call=call2, llm_response_id="batch_1")
    obs1 = CustomObservation(tool_name="bash", tool_call_id="call_1", content_text="res1")
    obs2 = CustomObservation(tool_name="read", tool_call_id="call_2", content_text="res2")

    events = [
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="start")]), source="user"),
        act1,
        act2,
        obs1,
        obs2,
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="next")]), source="user"),
    ]
    view = View.from_events(events)
    m_indices = view.manipulation_indices
    assert 2 not in m_indices
    assert 1 in m_indices

    save_case(
        "atomic_boundary_batch_atomicity",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "atomic_boundary_batch_atomicity",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/view/properties/batch_atomicity.py:49-76"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "BatchAtomicityProperty (batch_atomicity.py:49-76) excludes index 2 between act1 and act2, keeping the multi-tool-call batch atomic.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {},
                "summary_responses": [],
            },
            "expect": {
                "selection": {
                    "targets": [1, 2],
                }
            },
        },
    )

    # =========================================================================
    # Case 11: atomic_boundary_tool_call_matching
    # Evidence: tool_call_matching.py:61-100
    # =========================================================================
    events = [
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="start")]), source="user"),
        act1,
        obs1,
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="finish")]), source="user"),
    ]
    view = View.from_events(events)
    m_indices = view.manipulation_indices
    assert 2 not in m_indices
    assert 1 in m_indices
    assert 3 in m_indices

    save_case(
        "atomic_boundary_tool_call_matching",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "atomic_boundary_tool_call_matching",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/view/properties/tool_call_matching.py:61-100"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "ToolCallMatchingProperty (tool_call_matching.py:61-100) prevents splitting tool calls from their tool results (removes index 2).",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {},
                "summary_responses": [],
            },
            "expect": {
                "selection": {
                    "targets": [1, 2],
                }
            },
        },
    )

    # =========================================================================
    # Case 12: atomic_boundary_thinking_loop
    # Evidence: tool_loop_atomicity.py:14-63
    # =========================================================================
    act_think = ActionEvent(
        thought=[TextContent(text="deep thought")],
        thinking_blocks=[ThinkingBlock(thinking="reasoning block")],
        tool_name="bash",
        tool_call_id="call_t1",
        tool_call=MessageToolCall(id="call_t1", name="bash", arguments='{"cmd":"date"}', origin="completion"),
        llm_response_id="think_resp",
    )
    obs_think = CustomObservation(tool_name="bash", tool_call_id="call_t1", content_text="Fri Oct 9")
    events = [
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="user request")]), source="user"),
        act_think,
        obs_think,
        MessageEvent(llm_message=Message(role="assistant", content=[TextContent(text="done")]), source="agent"),
    ]
    view = View.from_events(events)
    m_indices = view.manipulation_indices
    assert 2 not in m_indices

    save_case(
        "atomic_boundary_thinking_loop",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "atomic_boundary_thinking_loop",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/view/properties/tool_loop_atomicity.py:14-63"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "ToolLoopAtomicityProperty (tool_loop_atomicity.py:14-63) treats thinking-block tool loops as indivisible units.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {},
                "summary_responses": [],
            },
            "expect": {
                "selection": {
                    "targets": [1, 2],
                }
            },
        },
    )

    # =========================================================================
    # Case 13: summary_prompt_bytes_and_rendering
    # Evidence: llm_summarizing_condenser.py:213-225, summarizing_prompt.j2:1-55
    # =========================================================================
    events = [
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="Goal: run analysis")]), source="user"),
        MessageEvent(llm_message=Message(role="assistant", content=[TextContent(text="Starting task 101")]), source="agent"),
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="Clarification details")]), source="user"),
        MessageEvent(llm_message=Message(role="assistant", content=[TextContent(text="Analysis complete")]), source="agent"),
    ]
    view = View.from_events(events)
    view.append_event(CondensationRequest())
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=1)
    cond = condenser.condense(view)
    prompt_used = mock_llm.completion.call_args[1]["messages"][0].content[0].text
    assert "<EVENT>" in prompt_used
    assert "</EVENT>" in prompt_used

    save_case(
        "summary_prompt_bytes_and_rendering",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "summary_prompt_bytes_and_rendering",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:213-225",
                    "openhands-sdk/openhands/sdk/context/condenser/prompts/summarizing_prompt.j2:1-55",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "Executed Jinja template rendering of summarizing_prompt.j2 using render_template at lines 218-222.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"max_size": 10, "keep_first": 1},
                "summary_responses": ["Preserve task IDs"],
            },
            "expect": {
                "summary_requests": [
                    {
                        "system": None,
                        "messages": [{"role": "user", "content": prompt_used}],
                        "max_tokens": None,
                        "tools": [],
                    }
                ],
            },
        },
    )

    # =========================================================================
    # Case 14: summary_offset_placement_user_role
    # Evidence: condenser.py:83-96, 120-132
    # =========================================================================
    events = [
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="Initial task")]), source="user"),
        MessageEvent(llm_message=Message(role="assistant", content=[TextContent(text="Acknowledged")]), source="agent"),
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="Step 1")]), source="user"),
        MessageEvent(llm_message=Message(role="assistant", content=[TextContent(text="Done 1")]), source="agent"),
        MessageEvent(llm_message=Message(role="user", content=[TextContent(text="Step 2")]), source="user"),
        MessageEvent(llm_message=Message(role="assistant", content=[TextContent(text="Done 2")]), source="agent"),
    ]
    view = View.from_events(events)
    view.append_event(CondensationRequest())
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=2)
    cond = condenser.condense(view)
    view_after = View.from_events(events)
    view_after.append_event(cond)
    bb_projected = events_to_bb_messages(view_after.events)

    save_case(
        "summary_offset_placement_user_role",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "summary_offset_placement_user_role",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/event/condenser.py:83-96",
                    "openhands-sdk/openhands/sdk/event/condenser.py:120-132",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "Condensation.apply (condenser.py:83-96) inserts CondensationSummaryEvent at summary_offset (offset 2). Line 130 maps to user role.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"max_size": 10, "keep_first": 2},
                "summary_responses": ["Scripted summary of forgotten events"],
            },
            "expect": {
                "projected_view": bb_projected,
            },
        },
    )

    # =========================================================================
    # Case 15: repeated_condensation_replaces_previous_summary
    # Evidence: test_llm_summarizing_condenser.py:208-257
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(14)
    ]
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=2)
    view1 = View.from_events(events)
    cond1 = condenser.condense(view1)
    view1.append_event(cond1)

    mock_llm2 = create_mock_llm("Second round updated summary")
    condenser2 = LLMSummarizingCondenser(llm=mock_llm2, max_size=8, keep_first=2)
    cond2 = condenser2.condense(view1)
    view2 = View.from_events(view1.events)
    view2.append_event(cond2)

    save_case(
        "repeated_condensation_replaces_previous_summary",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "repeated_condensation_replaces_previous_summary",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:310-320",
                    "openhands-sdk/openhands/sdk/event/condenser.py:83-96",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "Subsequent condensation includes the prior CondensationSummaryEvent in its forgotten_event_ids and inserts the fresh summary at the new offset.",
            },
            "input": {
                "messages": events_to_bb_messages(view1.events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 8, "keep_first": 2},
                "summary_responses": ["Second round updated summary"],
            },
            "expect": {
                "projected_view": events_to_bb_messages(view2.events),
            },
        },
    )

    # =========================================================================
    # Case 16: hard_failure_triggers_hard_context_reset
    # Evidence: llm_summarizing_condenser.py:323-365, base.py:176-187
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(10)
    ]
    view = View.from_events(events)
    view.append_event(CondensationRequest())

    mock_llm_fail = create_mock_llm("Hard reset recovered summary", fail_times=2)
    condenser = LLMSummarizingCondenser(llm=mock_llm_fail, max_size=8, keep_first=2)
    cond = condenser.condense(view)
    assert isinstance(cond, Condensation)
    assert cond.summary_offset == 0
    assert len(cond.forgotten_event_ids) == len(events)

    save_case(
        "hard_failure_triggers_hard_context_reset",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "hard_failure_triggers_hard_context_reset",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/base.py:176-187",
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:323-365",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "hard_context_reset (lines 323-365) summarizes the entire view at offset 0, scaling event strings by 0.8 on retry failures (line 353).",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "manual",
                "native_settings": {"max_size": 8, "keep_first": 2, "hard_context_reset_max_retries": 5},
                "summary_responses": ["Hard reset recovered summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 10, "limit": 8, "severity": "hard"},
                "selection": {
                    "prefix_end": 0,
                    "first_kept_index": 10,
                    "summarize": list(range(10)),
                    "turn_prefix": [],
                    "replay": [],
                    "targets": [],
                },
            },
        },
    )

    # =========================================================================
    # Case 17: soft_failure_leaves_view_unchanged
    # Evidence: base.py:170-174
    # =========================================================================
    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(12)
    ]
    view = View.from_events(events)
    condenser = LLMSummarizingCondenser(llm=mock_llm, max_size=10, keep_first=2)
    with patch.object(condenser, "_get_forgotten_events", side_effect=NoCondensationAvailableException("Boundary error")):
        res_view = condenser.condense(view)
        assert isinstance(res_view, View)
        assert res_view == view

    save_case(
        "soft_failure_leaves_view_unchanged",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "soft_failure_leaves_view_unchanged",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/base.py:170-174"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "base.py:170-174 catches NoCondensationAvailableException on SOFT requirement (EVENTS) and returns original view unchanged.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 10, "keep_first": 2},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 12, "limit": 10, "severity": "soft"},
                "projected_view": events_to_bb_messages(events),
            },
        },
    )

    # =========================================================================
    # Case 18: pipeline_stops_at_first_condensation
    # Evidence: pipeline_condenser.py:46-51
    # =========================================================================
    c1 = LLMSummarizingCondenser(llm=create_mock_llm("Pipeline condenser 1 summary"), max_size=10, keep_first=2)
    mock2 = create_mock_llm("Pipeline condenser 2 summary")
    c2 = LLMSummarizingCondenser(llm=mock2, max_size=10, keep_first=2)
    pipeline = PipelineCondenser(condensers=[c1, c2])

    events = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(12)
    ]
    view = View.from_events(events)
    res = pipeline.condense(view)
    assert isinstance(res, Condensation)
    assert res.summary == "Pipeline condenser 1 summary"
    assert mock2.completion.call_count == 0

    save_case(
        "pipeline_stops_at_first_condensation",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "pipeline_stops_at_first_condensation",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/pipeline_condenser.py:46-51"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "PipelineCondenser (pipeline_condenser.py:46-51) halts immediately upon the first Condensation object; later stages are skipped.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {},
                "summary_responses": ["Pipeline condenser 1 summary"],
            },
            "expect": {
                "trigger": {"fires": True, "tokens": 12, "limit": 10, "severity": "soft"},
            },
        },
    )

    # =========================================================================
    # Case 19: pipeline_exception_propagates
    # Evidence: pipeline_condenser.py:46-51
    # =========================================================================
    mock_err = create_mock_llm(fail_times=10, fail_exception=RuntimeError("Unrecoverable downstream error"))
    c_err = LLMSummarizingCondenser(llm=mock_err, max_size=10, keep_first=2, hard_context_reset_max_retries=1)
    pipeline_err = PipelineCondenser(condensers=[c_err, c2])

    events_err = [
        MessageEvent(
            llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"Msg {i}")]),
            source="user" if i % 2 == 0 else "agent",
        )
        for i in range(12)
    ]
    view_err = View.from_events(events_err)
    view_err.append_event(CondensationRequest())

    try:
        pipeline_err.condense(view_err)
        propagated = False
    except NoCondensationAvailableException as e:
        propagated = "Unrecoverable downstream error" in str(e)
    assert propagated

    save_case(
        "pipeline_exception_propagates",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "pipeline_exception_propagates",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/pipeline_condenser.py:46-51"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "PipelineCondenser has no exception-swallowing or fall-through handler; child exceptions propagate immediately.",
            },
            "input": {
                "messages": events_to_bb_messages(events),
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {},
                "summary_responses": [],
            },
            "expect": {
                "failure": {
                    "kind": "NoCondensationAvailableException",
                    "message": "Unrecoverable downstream error",
                }
            },
        },
    )

    # =========================================================================
    # Case 20: defaults_settings_variant_240_2
    # Evidence: settings/model.py:150-227, llm_summarizing_condenser.py:48-51
    # =========================================================================
    condenser_def = LLMSummarizingCondenser(llm=mock_llm)
    assert condenser_def.max_size == 240
    assert condenser_def.keep_first == 2

    save_case(
        "defaults_settings_variant_240_2",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "defaults_settings_variant_240_2",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": [
                    "openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:48-51",
                    "openhands-sdk/openhands/sdk/settings/model.py:150-227",
                ],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "Instantiated LLMSummarizingCondenser with default parameters confirms max_size=240, keep_first=2.",
            },
            "input": {
                "messages": [],
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 240, "keep_first": 2},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": False, "tokens": 0, "limit": 240, "severity": None},
            },
        },
    )

    # =========================================================================
    # Case 21: defaults_helper_variant_80_4
    # Evidence: llm_summarizing_condenser.py:516-526
    # =========================================================================
    from openhands.sdk.context.condenser.llm_summarizing_condenser import default_condenser
    helper_condenser = default_condenser(mock_llm)
    assert helper_condenser.max_size == 80
    assert helper_condenser.keep_first == 4

    save_case(
        "defaults_helper_variant_80_4",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "defaults_helper_variant_80_4",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:516-526"],
            },
            "capture": {
                "kind": "executed",
                "script": SCRIPT_PATH,
                "notes": notes_prefix + "default_condenser helper function constructs LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4).",
            },
            "input": {
                "messages": [],
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"max_size": 80, "keep_first": 4},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": False, "tokens": 0, "limit": 80, "severity": None},
            },
        },
    )

    # =========================================================================
    # Case 22: defaults_direct_agent_none
    # Evidence: agent/base.py:266-282
    # =========================================================================
    save_case(
        "defaults_direct_agent_none",
        {
            "schema": "bb.compaction_oracle_case.v1",
            "preset": PRESET,
            "case": "defaults_direct_agent_none",
            "source": {
                "repo": REPO_URL,
                "commit": COMMIT,
                "evidence": ["openhands-sdk/openhands/sdk/agent/base.py:266-282"],
            },
            "capture": {
                "kind": "source_derived",
                "script": SCRIPT_PATH,
                "notes": (
                    f"Derived from pinned source {REPO_URL} at commit {COMMIT}, dist sha256 {DIST_SHA256}. "
                    "Verbatim quote from openhands-sdk/openhands/sdk/agent/base.py:270: "
                    "`condenser: CondenserBase | None = None`. "
                    "Direct Agent(...) initialization defaults condenser to None, so no condensation occurs."
                ),
            },
            "input": {
                "messages": [{"role": "user", "content": "Hello"}],
                "usage": None,
                "context_window": 200000,
                "max_input_tokens": None,
                "max_output_tokens": None,
                "reason": "events",
                "native_settings": {"condenser": None},
                "summary_responses": [],
            },
            "expect": {
                "trigger": {"fires": False, "tokens": 0, "limit": 0, "severity": None},
                "projected_view": [{"role": "user", "content": "Hello"}],
            },
        },
    )

    print(f"\nSuccessfully captured {len(captured_cases)} cases into {out_dir}")


if __name__ == "__main__":
    main()
