from __future__ import annotations
from pathlib import Path
import json
import pytest
from breadboard.rl.harness.omp_native_tools import NativeToolWorker

from tests.rl.harness.omp_18_1_17_test_support import (
    _authority_payload,
    _description_policy,
    _lease_model,
    _model_registry,
    _run_stock_compaction_oracle,
    _runtime_spec,
    _sample_conversation_messages,
    _test_denials,
)

@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_forced_off_for_existing_target(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase(
            "initialize",
            {
                "task": "test compaction forced off",
                "model_config": _lease_model(),
                "advertisement": {
                    "bounded_description_policy": _description_policy(),
                    "model_registry": _model_registry(),
                    "capability_denials": _test_denials(),
                },
                **_authority_payload(tmp_path),
            },
        )
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "threshold",
                "checkpoint": "before_request",
                "context_window": 131072,
            },
        )
        assert res["kind"] == "compaction_unavailable"
        assert res["reason"] == "compaction_disabled"
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_phases_threshold_not_triggered(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test threshold not triggered",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "context_window": 131072,
            },
        )
        assert res["kind"] == "compaction_unavailable"
        assert res["reason"] == "not_triggered"
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_handoff_on_threshold(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        init_res = worker.phase("initialize", {
            "task": "test threshold handoff",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = _sample_conversation_messages()
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "handoff"

        # Compare against stock oracle
        stock = _run_stock_compaction_oracle({
            "action": "handoff",
            "cwd": str(tmp_path),
            "model_config": _lease_model(),
            "system_prompt": [init_res["system_prompt"]],
            "messages": messages,
            "settings": {"keepRecentTokens": 10},
            "summaries": ["Stock handoff document text."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]
        assert res["summary_request"]["tool_choice"] == stock["wireRequests"][0]["tool_choice"] == "none"
        summary_request = res["summary_request"]
        assert json.loads(summary_request["request_body_json"]) == summary_request["request_body"]
        assert "model" in summary_request["request_body"]

        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Stock handoff document text.",
                "preparation": prep,
            },
        )
        assert fin["kind"] == "compaction_finalized"
        assert fin["summary"] == stock["summary"]
        assert fin["first_kept_index"] == stock["firstKeptIndex"]
        assert fin["compaction_message"]["details"] == stock["compactionMessage"]["details"]
        assert fin["messages"] == stock["finalMessages"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_shake_sufficient(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test shake sufficient",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "detail " * 100},
            {"role": "assistant", "content": [{"type": "text", "text": "Inspecting files."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": json.loads("{\"path\": \"db.py\"}")}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass\n" * 2000}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "I see the functions."}]},
            {"role": "user", "content": "Also add logging: " + "more words " * 200},
        ]
        settings = {"methodOrder": ["shake", "soft"], "shake": {"protectTokens": 50, "minSavings": 50}}
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 11000, "completion_tokens": 100, "total_tokens": 11100},
                "context_window": 13000,
                "settings": settings,
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "shake"
        assert res["summary_request"] is None

        fin = worker.phase(
            "finalize_compaction",
            {
                "preparation": prep,
            },
        )
        assert fin["kind"] == "compaction_finalized"
        stock = _run_stock_compaction_oracle({
            "action": "shake",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": settings,
        })
        assert fin["messages"] == stock["finalMessages"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_shake_insufficient_falls_through(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        init_res = worker.phase("initialize", {
            "task": "test shake insufficient fallthrough",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        # Messages with NO toolResult (shake reclaims 0 tokens)
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "words " * 400},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, I can help."}]},
            {"role": "user", "content": "Also add logging: " + "detail " * 400},
            {"role": "assistant", "content": [{"type": "text", "text": "Will do."}]},
            {"role": "user", "content": "Proceed."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["shake", "soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"
        assert prep["fallbackFromShake"] is True

        stock = _run_stock_compaction_oracle({
            "action": "soft",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": {"keepRecentTokens": 10},
            "summaries": ["Stock fallback summary.", "Short summary."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]

        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Stock fallback summary.",
                "preparation": prep,
            },
        )
        if fin["kind"] == "compaction_followup_request":
            assert fin["request"]["messages"] == stock["wireRequests"][1]["messages"]
            fin = worker.phase(
                "finalize_compaction",
                {
                    "summary": "Stock fallback summary.",
                    "preparation": prep,
                    "followup_summaries": ["Short summary."],
                },
            )
        assert fin["kind"] == "compaction_finalized"
        assert fin["summary"] == stock["summary"]
        assert fin["first_kept_index"] == stock["firstKeptIndex"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_soft_on_overflow(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test soft on overflow",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "detail " * 50},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, inspecting."}]},
            {"role": "user", "content": "Proceed with implementation: " + "task " * 50},
            {"role": "assistant", "content": [{"type": "text", "text": "Implementation done."}]},
            {"role": "user", "content": "Next step."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 50},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        stock = _run_stock_compaction_oracle({
            "action": "soft",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": {"keepRecentTokens": 50},
            "summaries": ["Stock overflow summary.", "Short summary."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]
        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Stock overflow summary.",
                "preparation": prep,
            },
        )
        if fin["kind"] == "compaction_followup_request":
            fin = worker.phase(
                "finalize_compaction",
                {
                    "summary": "Stock overflow summary.",
                    "preparation": prep,
                    "followup_summaries": ["Short summary."],
                },
            )
        assert fin["kind"] == "compaction_finalized"
        assert fin["retry"] is True
        assert fin["summary"] == stock["summary"]
        assert fin["first_kept_index"] == stock["firstKeptIndex"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_split_turn(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test split turn",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Initial conversation turn: " + "detail " * 50},
            {"role": "assistant", "content": [{"type": "text", "text": "Initial answer."}]},
            {"role": "user", "content": "Help me refactor the database module."},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, let us inspect files."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": json.loads(json.dumps({"path": "db.py"}))}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass"}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "I see functions."}]},
            {"role": "user", "content": "Also add logging."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        stock = _run_stock_compaction_oracle({
            "action": "soft",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": {"keepRecentTokens": 10},
            "summaries": ["Split turn summary.", "Turn prefix summary.", "Short summary."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]
        assert res["turn_prefix_request"]["messages"] == stock["wireRequests"][1]["messages"]

        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Split turn summary.",
                "turn_prefix_summary": "Turn prefix summary.",
                "preparation": prep,
            },
        )
        assert fin["kind"] == "compaction_followup_request"
        assert fin["request"]["messages"] == stock["wireRequests"][2]["messages"]

        fin2 = worker.phase(
            "finalize_compaction",
            {
                "summary": "Split turn summary.",
                "turn_prefix_summary": "Turn prefix summary.",
                "preparation": prep,
                "followup_summaries": ["Short summary."],
            },
        )
        assert fin2["kind"] == "compaction_finalized"
        assert fin2["summary"] == stock["summary"]
        assert fin2["first_kept_index"] == stock["firstKeptIndex"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
@pytest.mark.parametrize("method", ["handoff", "soft"])
def test_stock_equality_update_previous_summary(tmp_path: Path, method: str) -> None:
    import copy
    from breadboard.rl.harness.runners.omp_semantics import OMPSemanticsState
    from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall

    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        initialized = worker.phase("initialize", {
            "task": "retain file journal",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        state = OMPSemanticsState(task="retain file journal", worker=worker, request_cap=8)
        state.begin_query()
        response = state.prepare_response(NativeProviderResponse(
            binding_digest="binding", request_digest="request", response_id="read-response", model="capture",
            content="Inspecting.", finish_reason="toolUse",
            tool_calls=(NativeToolCall("read-1", "read", '{"path":"journal.py"}'),),
            usage={"prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050},
        ), usage=worker.phase("parse_usage", {"usage": {
            "prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050,
        }})["usage"])
        prepared = state.prepare_tools(response.calls)
        response.assistant["content"][-1]["arguments"] = prepared["history_calls"][0]["arguments"]
        state.commit_tool_results(response.calls, [{"content": [{"type": "text", "text": "file contents"}]}])
        state.messages.append({"role": "user", "content": [{"type": "text", "text": "Finish the journal."}]})
        first_messages = copy.deepcopy(state.messages)
        settings = {"methodOrder": [method, "soft"], "keepRecentTokens": 10}
        summaries = []
        passes = []
        for index in range(2):
            if index:
                state.exit_status = None
                state.begin_query()
                state.prepare_response(NativeProviderResponse(
                    binding_digest="binding", request_digest="request", response_id="second-response", model="capture",
                    content="Continuing the implementation.", finish_reason="stop",
                    usage={"prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050},
                ), usage=worker.phase("parse_usage", {"usage": {
                    "prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050,
                }})["usage"])
                state.messages.append({"role": "user", "content": [{"type": "text", "text": "Run the remaining tests."}]})
            added = first_messages if not index else copy.deepcopy(state.messages[len(first_finalized):])
            passes.append({"messages": added, "action": method})
            prepared = worker.phase("prepare_compaction", {
                "messages": state.messages, "reason": "threshold", "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000},
                "context_window": 131072, "settings": settings,
            })
            assert prepared["kind"] == "compaction_prepared"
            payload = {"preparation": prepared["preparation"], "summary": f"Summary {index}."}
            summaries.append(payload["summary"])
            finalized = worker.phase("finalize_compaction", payload)
            if finalized["kind"] == "compaction_followup_request":
                payload["followup_summaries"] = [f"Short {index}."]
                summaries.extend(payload["followup_summaries"])
                finalized = worker.phase("finalize_compaction", payload)
            assert finalized["kind"] == "compaction_finalized"
            state.messages = copy.deepcopy(finalized["messages"])
            if not index:
                first_finalized = copy.deepcopy(state.messages)
            else:
                second_request = prepared["summary_request"]
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": _lease_model(), "messages": first_messages,
            "settings": settings, "summaries": summaries, "passes": passes,
        })
        assert second_request["messages"] == stock["wireRequests"][1 if method == "handoff" else 2]["messages"]
        assert finalized["messages"] == stock["finalMessages"]
        assert "journal.py" in finalized["summary"]
        assert "Summary 0." in json.dumps(second_request["messages"])
        # Stock conversion excludes usage; preserve the main request bytes.
        with_usage = worker.phase("project_request", {"messages": state.messages})
        no_usage = [{key: value for key, value in message.items() if key != "usage"} for message in state.messages]
        assert with_usage == worker.phase("project_request", {"messages": no_usage})
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_rescue_path(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        lease = _lease_model()
        worker.start()
        worker.phase("initialize", {
            "task": "test rescue path",
            "model_config": lease,
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "prefix context " * 600},
            {"role": "assistant", "content": [{"type": "text", "text": "Inspecting files."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": json.loads("{\"path\": \"db.py\"}")}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass\n" * 50}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "Here is big output."}, {"type": "toolCall", "id": "tc2", "name": "bash", "arguments": json.loads("{\"command\": \"cat output.log\"}")}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc2", "toolName": "bash", "content": [{"type": "text", "text": "log line output " * 200}], "isError": False},
            {"role": "user", "content": "Please proceed."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 3000, "completion_tokens": 100, "total_tokens": 3100},
                "context_window": 131072,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 1000},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        # Context window configured so unshaken tail exceeds fit budget, but shaken tail fits
        stock = _run_stock_compaction_oracle({
            "action": "rescue",
            "model_config": lease,
            "messages": messages,
            "settings": {"keepRecentTokens": 1000},
            "summaries": ["Rescue summary text.", "Short summary."],
        })
        fin_payload = {
            "summary": "Rescue summary text.",
            "preparation": prep,
        }
        if res.get("turn_prefix_request"):
            fin_payload["turn_prefix_summary"] = "Rescue turn prefix."
        fin = worker.phase("finalize_compaction", fin_payload)
        followup_index = 0
        followup_answers = ["Short summary."]
        while fin["kind"] == "compaction_followup_request":
            fin = worker.phase(
                "finalize_compaction",
                {
                    **fin_payload,
                    "followup_summaries": followup_answers[:followup_index + 1],
                },
            )
            followup_index += 1
        assert fin["kind"] == "compaction_finalized"
        assert fin["retry"] is True
        assert fin["messages"] == stock["finalMessages"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_dead_end_path(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        # Model with tiny context window so history cannot possibly fit
        lease = _lease_model()
        tiny_lease = {**lease, "contextWindow": 50}
        worker.start()
        worker.phase("initialize", {
            "task": "test dead-end path",
            "model_config": tiny_lease,
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = _sample_conversation_messages()
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 50,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        fin_payload = {
            "summary": "Dead-end summary.",
            "preparation": prep,
        }
        if res.get("turn_prefix_request"):
            fin_payload["turn_prefix_summary"] = "Dead-end turn prefix."
        fin = worker.phase("finalize_compaction", fin_payload)
        followup_index = 0
        followup_answers = ["Short summary."]
        while fin["kind"] == "compaction_followup_request":
            fin = worker.phase(
                "finalize_compaction",
                {
                    **fin_payload,
                    "followup_summaries": followup_answers[:followup_index + 1],
                },
            )
            followup_index += 1
        assert fin["kind"] == "compaction_finalized"
        # Due to tiny context window, retryFits is False, resulting in dead-end: retry=False
        assert fin["retry"] is False
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_phases_overflow(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test overflow compaction",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        # Reason overflow must fire regardless of usage
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
                "context_window": 131072,
                "settings": {"keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] in ("soft", "shake")
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_text_only_skips_snapcompact(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test text-only skips snapcompact",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        # Even with methodOrder explicitly starting with snapcompact:
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["snapcompact", "soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        # snapcompact was skipped because model.input does not contain "image"; soft was selected
        assert res["preparation"]["method"] == "soft"
    finally:
        worker.stop()

