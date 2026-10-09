#!/usr/bin/env python3
"""Capture deterministic, replayable cases from OpenHands SDK 1.47.0.

The input records event metadata, prior boundaries, the exact condenser settings,
and every scripted completion, including errors. No private selection methods are
patched: the SDK selects and condenses the recorded view itself.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"

REPO = "https://github.com/OpenHands/software-agent-sdk"
COMMIT = "50080b58d35b4824fda25fca2345d80bcd08aeff"
PRESET = "openhands_sdk@1.47.0"
SCRIPT = "scripts/compaction_oracles/capture_openhands.py"
DROPPED = (
    "pipeline_stops_at_first_condensation",
    "pipeline_exception_propagates",
    "defaults_direct_agent_none",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-source", required=True)
    parser.add_argument("--venv", required=True)
    parser.add_argument("--output-dir", default=f"tests/compaction/oracles/{PRESET}")
    args = parser.parse_args()
    if Path(sys.prefix).resolve() != Path(args.venv).resolve():
        os.execv(str(Path(args.venv) / "bin/python"), [str(Path(args.venv) / "bin/python"), __file__, *sys.argv[1:]])
    sdk_source = Path(args.sdk_source).resolve()
    sys.path.insert(0, str(sdk_source))
    import openhands.sdk
    assert Path(openhands.sdk.__file__).resolve().is_relative_to(sdk_source)
    assert importlib.metadata.version("openhands-sdk") == "1.47.0"
    from litellm.types.utils import ModelResponse
    from openhands.sdk.context.condenser.base import NoCondensationAvailableException
    from openhands.sdk.context.condenser.llm_summarizing_condenser import LLMSummarizingCondenser, default_condenser
    from openhands.sdk.context.view import View
    from openhands.sdk.event import ActionEvent, Condensation, CondensationRequest
    from openhands.sdk.event.base import LLMConvertibleEvent
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.event.llm_convertible.observation import ObservationBaseEvent
    from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, MetricsSnapshot, TextContent, ThinkingBlock

    class Observation(ObservationBaseEvent):
        content_text: str = ""

        def to_llm_message(self):
            return Message(role="tool", tool_call_id=self.tool_call_id, content=[TextContent(text=self.content_text)])

    def message_dict(message):
        result = {"role": message.role, "content": "\n".join(p.text for p in message.content if hasattr(p, "text"))}
        if message.tool_calls:
            result["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            result["tool_call_id"] = message.tool_call_id
        return result

    def event_dict(event):
        result = message_dict(event.to_llm_message())
        result["bb_event"] = {"display": str(event), "type": type(event).__name__}
        if isinstance(event, ActionEvent):
            result["bb_event"]["batch"] = event.llm_response_id
            result["bb_event"]["thinking"] = bool(event.thinking_blocks)
        return result

    def wire(events):
        return [message_dict(m) for m in LLMConvertibleEvent.events_to_messages(events)]

    def turns(n, text="Msg"):
        return [MessageEvent(llm_message=Message(role="user" if i % 2 == 0 else "assistant", content=[TextContent(text=f"{text} {i}")]), source="user" if i % 2 == 0 else "agent") for i in range(n)]

    class ScriptedLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.consumed = []
            self.requests = []
            self.llm = MagicMock(spec=LLM)
            self.llm.model = "scripted-summary-llm"
            self.llm.stream = False
            self.llm.requires_streaming = False
            for attr in ("_metrics", "_telemetry", "metrics", "base_url", "custom_tokenizer", "reasoning_effort", "log_completions_folder", "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "aws_region_name", "aws_profile_name", "aws_role_name", "aws_session_name", "aws_bedrock_runtime_endpoint", "input_cost_per_token", "output_cost_per_token"):
                setattr(self.llm, attr, None)
            self.llm.log_completions = False
            self.llm.litellm_extra_body = {}
            self.llm.temperature = 0.0
            self.llm.format_messages_for_llm = lambda messages: messages
            self.llm.uses_responses_api = lambda: False
            self.llm.completion.side_effect = self.complete

        def complete(self, **kwargs):
            self.requests.append({"system": None, "messages": [message_dict(m) for m in kwargs["messages"]], "max_tokens": None, "tools": []})
            assert self.responses, "capture called summary model more times than scripted"
            response = self.responses.pop(0)
            self.consumed.append(response)
            if isinstance(response, dict):
                raise RuntimeError(response["error"]["message"])
            raw = MagicMock(spec=ModelResponse)
            raw.id = f"response-{len(self.consumed)}"
            metrics = MetricsSnapshot(model_name="scripted-summary-llm", accumulated_cost=0.0, max_budget_per_task=None, accumulated_token_usage=None)
            return LLMResponse(message=Message(role="assistant", content=[TextContent(text=response)]), metrics=metrics, raw_response=raw)

    keys = ("max_size", "keep_first", "max_tokens", "minimum_progress", "hard_context_reset_max_retries", "hard_context_reset_context_scaling")
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = set()

    def capture(name, events, *, settings=None, manual=False, responses=(), token_limit=None, ledger=(), previous_events=None, selection=True, helper=False):
        scripted = ScriptedLLM(responses)
        condenser = default_condenser(scripted.llm) if helper else LLMSummarizingCondenser(llm=scripted.llm, **(settings or {}))
        native = {key: getattr(condenser, key) for key in keys}
        view = View.from_events(previous_events if previous_events is not None else events)
        if manual:
            view.append_event(CondensationRequest())
        agent = None
        if token_limit is not None:
            agent = MagicMock(spec=LLM)
            agent.effective_max_input_tokens = token_limit
            agent.get_token_count.side_effect = lambda messages, **kw: sum(len(p.text) for m in messages for p in m.content if hasattr(p, "text")) // 4
        reasons = condenser.get_condensation_reasons(view, agent_llm=agent)
        requirement = condenser.condensation_requirement(view, agent_llm=agent)
        cap = condenser._effective_max_tokens(agent)
        tokens = 0 if not reasons else len(view)
        if agent is not None:
            tokens = agent.get_token_count(LLMConvertibleEvent.events_to_messages(view.events))
        pressure = {"fires": bool(reasons), "tokens": tokens, "limit": cap if agent is not None else condenser.max_size, "severity": requirement.value.lower() if requirement else None}
        expect = {"trigger": pressure}
        if selection and reasons:
            try:
                forgotten, offset = condenser._get_forgotten_events(view, agent_llm=agent)
                if len(forgotten) < len(view) * condenser.minimum_progress:
                    raise NoCondensationAvailableException("Cannot apply condensation: events forgotten below minimum progress threshold.")
                if not ledger:
                    expect["selection"] = {"prefix_end": offset, "first_kept_index": offset + len(forgotten), "summarize": list(range(offset, offset + len(forgotten))), "turn_prefix": [], "replay": [], "targets": []}
            except (ValueError, NoCondensationAvailableException):
                pass
        result = condenser.condense(view, agent_llm=agent)
        after = View.from_events(view.events)
        if isinstance(result, Condensation):
            after.append_event(result)
            expect["summary"] = result.summary
        else:
            # The ledger remains unchanged on a soft failed stage.
            if reasons:
                try:
                    condenser.get_condensation(view, agent_llm=agent)
                except NoCondensationAvailableException as error:
                    expect["failure"] = {"kind": type(error).__name__, "message": str(error)}
        expect["projected_view"] = wire(after.events)
        if scripted.requests:
            expect["summary_requests"] = scripted.requests
        assert scripted.consumed == list(responses), (name, scripted.consumed, responses)
        assert not scripted.responses
        payload = {
            "schema": "bb.compaction_oracle_case.v1", "preset": PRESET, "case": name,
            "source": {"repo": REPO, "commit": COMMIT, "evidence": ["openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py:120-399", "openhands-sdk/openhands/sdk/context/view/view.py:38-51"]},
            "capture": {"kind": "executed", "script": SCRIPT, "notes": f"Executed pinned SDK 1.47.0 at {COMMIT}. Reasons: {','.join(sorted(r.value for r in reasons)) or 'none'}. Event payloads preserve display, response batches and thinking boundaries. Deterministic scripted completions and floor(text characters/4) token counter."},
            "input": {"messages": [event_dict(e) for e in events], "usage": None, "context_window": 200000, "max_input_tokens": token_limit, "max_output_tokens": None, "reason": "manual" if manual else "threshold", "native_settings": native, "summary_responses": list(responses)},
            "expect": expect,
        }
        if token_limit is not None:
            payload["input"]["token_counter"] = {"kind": "text_chars_floor", "divisor": 4}
        if ledger:
            payload["input"]["ledger"] = list(ledger)
        (out / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        names.add(name)
        print(f"Captured case: {name}")
        return condenser, result, after

    capture("events_threshold_equal_does_not_fire", turns(10, "Turn"), settings={"max_size": 10}, selection=False)
    capture("events_threshold_strictly_greater_fires", turns(11, "Turn"), settings={"max_size": 10}, responses=["Summary of events 2..8"])
    for n, name in [(5, "tokens_threshold_equal_does_not_fire"), (6, "tokens_threshold_strictly_greater_fires")]:
        events = turns(n)
        events = [e.model_copy(update={"llm_message": Message(role=e.llm_message.role, content=[TextContent(text="X" * 20)])}) for e in events]
        capture(name, events, settings={"max_size": 100, "max_tokens": 25, "keep_first": 1}, token_limit=1000, responses=["Token reduction summary"] if n == 6 else [])
    capture("request_reason_fires_hard", turns(8), settings={"max_size": 100}, manual=True, responses=["Requested summary"])
    capture("keep_first_prefix_preservation", turns(16), settings={"max_size": 100, "keep_first": 4}, manual=True, responses=["Preserved prefix summary"])
    capture("target_sizes_events_vs_request", turns(40, "Turn"), settings={"max_size": 30}, manual=True, responses=["Strictest reduction summary"])
    capture("minimum_progress_boundary_equality_succeeds", turns(20), settings={"max_size": 10, "minimum_progress": 0.8}, responses=["Boundary equality summary"])
    capture("minimum_progress_below_fails", turns(20), settings={"max_size": 10, "minimum_progress": 0.81}, responses=[])

    def action(call_id, batch, thinking=False):
        return ActionEvent(thought=[TextContent(text="thinking")], thinking_blocks=[ThinkingBlock(thinking="reasoning")] if thinking else [], tool_name="bash", tool_call_id=call_id, tool_call=MessageToolCall(id=call_id, name="bash", arguments='{"cmd":"ls"}', origin="completion"), llm_response_id=batch)

    a1, a2 = action("call_1", "batch"), action("call_2", "batch")
    a2 = a2.model_copy(update={"thought": []})
    o1, o2 = Observation(tool_name="bash", tool_call_id="call_1", content_text="res1"), Observation(tool_name="bash", tool_call_id="call_2", content_text="res2")
    capture("atomic_boundary_batch_atomicity", [*turns(1), a1, a2, o1, o2, *turns(5)], settings={"max_size": 10, "keep_first": 2}, manual=True, responses=["Batch-safe summary"])
    capture("atomic_boundary_tool_call_matching", [*turns(1), a1, o1, *turns(5)], settings={"max_size": 10, "keep_first": 2}, manual=True, responses=["Pair-safe summary"])
    a3, a4 = action("call_3", "think1", True), action("call_4", "think2")
    o3, o4 = Observation(tool_name="bash", tool_call_id="call_3", content_text="res3"), Observation(tool_name="bash", tool_call_id="call_4", content_text="res4")
    capture("atomic_boundary_thinking_loop", [*turns(1), a3, o3, a4, o4, *turns(5)], settings={"max_size": 10, "keep_first": 2}, manual=True, responses=["Thinking-loop-safe summary"])
    capture("summary_prompt_bytes_and_rendering", turns(4), settings={"max_size": 10, "keep_first": 1}, manual=True, responses=["Preserve task IDs"])
    capture("summary_offset_placement_user_role", turns(6), settings={"max_size": 10}, manual=True, responses=["Scripted summary of forgotten events"])

    events = turns(14)
    initial_script = ScriptedLLM(["First round summary"])
    initial = LLMSummarizingCondenser(llm=initial_script.llm, max_size=10)
    view1 = View.from_events(events)
    first = initial.condense(view1)
    view1.append_event(first)
    # Append new history so a second threshold condensation really runs.
    tail = turns(8, "New")
    for event in tail:
        view1.append_event(event)
    ledger = [{"history_length": len(events), "prefix_end": first.summary_offset, "first_kept_index": first.summary_offset + len(first.forgotten_event_ids), "summary": first.summary, "summary_messages": [event_dict(first.summary_event)], "details": {}, "coalesce_user": True}]
    capture("repeated_condensation_replaces_previous_summary", events + tail, settings={"max_size": 8}, previous_events=view1.events, ledger=ledger, responses=["Second round updated summary"], selection=False)
    capture("hard_failure_triggers_hard_context_reset", turns(10), settings={"max_size": 8}, manual=True, responses=[{"error": {"kind": "RuntimeError", "message": "Simulated failure 1"}}, {"error": {"kind": "RuntimeError", "message": "Simulated failure 2"}}, "Hard reset recovered summary"])
    capture("soft_failure_leaves_view_unchanged", turns(12), settings={"max_size": 10, "minimum_progress": 0.9}, responses=[])
    capture("defaults_settings_variant_240_2", [], selection=False)
    capture("defaults_helper_variant_80_4", [], helper=True, selection=False)
    # Only delete cases intentionally removed from this capture, never arbitrary files.
    for name in DROPPED:
        (out / f"{name}.json").unlink(missing_ok=True)
    print(f"Successfully captured {len(names)} cases; dropped {', '.join(DROPPED)}.")


if __name__ == "__main__":
    main()
