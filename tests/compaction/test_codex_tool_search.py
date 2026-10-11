"""Client discovery through real Codex 0.139.0 and the production engine."""
import json
from pathlib import Path

import pytest

from breadboard_engine.compaction_lanes.codex_lane import BINARY, EngineMockProvider, EngineResponsesHandler, run_stock
from breadboard_engine.compaction_lanes import codex_lane
from tests.compaction.test_codex_agent_tools import owner

USAGE = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}


class DiscoveryHandler(EngineResponsesHandler):
    def _handle_openai_responses(self, item, request_json):
        if item["type"] != "native_search":
            return super()._handle_openai_responses(item, request_json)
        call = {"type": "tool_search_call", "id": "search-native", "call_id": "search-1",
                "execution": "client", "status": "completed", "arguments": item["arguments"]}
        response = {"id": "resp-search", "object": "response", "status": "completed",
                    "model": request_json["model"], "output": [call], "usage": USAGE}
        if request_json.get("stream"):
            events = [
                {"type": "response.output_item.added", "output_index": 0, "item": call},
                {"type": "response.output_item.done", "output_index": 0, "item": call},
                {"type": "response.completed", "response": response},
            ]
            raw = {"type": "raw", "headers": {"content-type": "text/event-stream"},
                   "chunks": [f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events]}
        else:
            raw = {"type": "raw", "headers": {"content-type": "application/json"},
                   "body": json.dumps(response)}
        return super()._handle_openai_responses(raw, request_json)


def stock_search(tmp_path, monkeypatch, arguments):
    if not BINARY.is_file():
        pytest.skip("pinned Codex 0.139.0 binary not installed")
    workspace = tmp_path / "stock-workspace"
    workspace.mkdir()
    stock = tmp_path / "stock"
    stock.mkdir()
    script = [{"type": "native_search", "arguments": arguments},
              {"type": "text", "text": "Discovery complete", "usage": USAGE}]
    with monkeypatch.context() as patch:
        patch.setattr(codex_lane, "EngineResponsesHandler", DiscoveryHandler)
        with EngineMockProvider(script=script, record_path=stock / "requests.jsonl") as provider:
            result = run_stock("mid_turn_compaction", workspace, stock, provider.base_url,
                               {"model": "gpt-5.5", "window": 272000, "limit": 244800})
            records = list(provider.engine.recorded_requests)
    assert result["runs"][0]["returncode"] == 0, result
    outputs = [item for record in records for item in record["json"].get("input", [])
               if item.get("type") in {"tool_search_output", "function_call_output"}]
    assert len(outputs) == 1, records
    return outputs[0], records


def test_stock_deferred_namespace_capture(tmp_path, monkeypatch):
    output, records = stock_search(tmp_path, monkeypatch, {
        "query": "spawn send resume wait close agent subagent", "limit": 8,
    })
    assert output["execution"] == "client"
    assert output["status"] == "completed"
    assert len(output["tools"]) == 1
    namespace = output["tools"][0]
    assert namespace["name"] == "multi_agent_v1"
    assert {tool["name"] for tool in namespace["tools"]} == {
        "spawn_agent", "send_input", "resume_agent", "wait_agent", "close_agent",
    }
    assert all(tool["defer_loading"] is True and "output_schema" not in tool
               for tool in namespace["tools"])
    assert records[0]["json"]["tools"] == records[-1]["json"]["tools"]
    capture = tmp_path / "discovered_tools.json"
    capture.write_text(json.dumps(output, indent=2))
    print(f"STOCK_DEFERRED_CAPTURE={capture}")


@pytest.mark.parametrize("arguments", [
    {"query": "spawn send resume wait close agent subagent"},
    {"query": "spawn sub-agent delegation model", "limit": 1},
    {"query": "existing agents interrupt queue", "limit": 2},
    {"query": "WAITING timeouts completed results", "limit": 3},
    {"query": "abracadabra", "limit": 8},
    {"query": " \t\n", "limit": 8},
    {"query": "agent", "limit": 0},
    {"query": "agent", "limit": -1},
    {"query": "agent", "limit": 1.5},
    {"limit": 1},
    {"query": None, "limit": 1},
])
def test_bm25_results_match_real_stock(tmp_path, monkeypatch, arguments):
    from breadboard_engine.execution.codex_search import execute_codex_search_tool
    from types import SimpleNamespace

    expected, _ = stock_search(tmp_path, monkeypatch, arguments)
    owner = SimpleNamespace(config={"tools": {"native_semantics": "codex_0_139"}})
    result = execute_codex_search_tool(owner, arguments)
    body = json.loads(result["__mvi_text_output"])
    actual = body if body.get("type") == "function_call_output" else {
        "type": "tool_search_output", "call_id": "search-1", **body}
    assert json.dumps(actual, sort_keys=True, separators=(",", ":")).encode() == (
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode())


def test_search_executor_rejects_other_profiles():
    from breadboard_engine.execution.codex_search import execute_codex_search_tool
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="requires the native Codex profile"):
        execute_codex_search_tool(SimpleNamespace(config={}), {"query": "spawn"})


class SearchSpawnHandler(DiscoveryHandler):
    def _handle_openai_responses(self, item, request_json):
        inputs = request_json.get("input", [])
        outputs = {entry["call_id"]: entry for entry in inputs
                   if entry.get("type") in {"function_call_output", "tool_search_output"}}
        child_turn = any(block.get("text") == "Write discovery child evidence"
                         for entry in inputs if entry.get("role") == "user"
                         for block in entry.get("content", []) if isinstance(block, dict))
        if child_turn:
            if "child-proof" in outputs:
                item = {"type": "text", "text": "child finished", "usage": USAGE}
            else:
                item = {"type": "tool_call", "name": "exec_command", "call_id": "child-proof",
                        "arguments": json.dumps({"cmd": "printf discovered > discovery-child.txt",
                                                 "yield_time_ms": 1000}), "usage": USAGE}
        elif "wait-discovered" in outputs:
            item = {"type": "text", "text": "root finished", "usage": USAGE}
        elif "spawn-discovered" in outputs:
            id = json.loads(outputs["spawn-discovered"]["output"])["agent_id"]
            item = {"type": "tool_call", "name": "wait_agent", "namespace": "multi_agent_v1",
                    "call_id": "wait-discovered", "arguments": json.dumps({"targets": [id]}), "usage": USAGE}
        elif "search-1" in outputs:
            item = {"type": "tool_call", "name": "spawn_agent", "namespace": "multi_agent_v1",
                    "call_id": "spawn-discovered", "arguments": json.dumps({
                        "message": "Write discovery child evidence", "agent_type": "worker",
                    }), "usage": USAGE}
        else:
            item = {"type": "native_search", "arguments": {"query": "spawn send resume wait close agent subagent"}}
        return super()._handle_openai_responses(item, request_json)


def test_search_then_real_spawn_matches_stock_protocol(owner, tmp_path, monkeypatch):
    from copy import deepcopy
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    if not BINARY.is_file():
        pytest.skip("pinned Codex 0.139.0 binary not installed")
    config = deepcopy(owner.config)
    config["tools"]["registry"]["include"] = [
        "exec_command", "write_stdin", "update_plan", "request_user_input", "apply_patch",
        "view_image", "get_goal", "create_goal", "update_goal", "tool_search", "web_search", "multi_agent_v1",
    ]
    config["provider_tools"]["openai"].update({
        "responses_namespaces": True, "responses_tool_search": True,
        "responses_deferred_tools": ["multi_agent_v1"],
        "responses_custom_tools": ["apply_patch"],
        "responses_hosted_outputs": ["web_search_call"],
    })
    cls = OpenAIConductor.__ray_metadata__.modified_class
    engine = cls(workspace=owner.workspace, image=owner.image, config=config, local_mode=True)
    stock_dir = tmp_path / "stock"
    stock_dir.mkdir()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(codex_lane, "EngineResponsesHandler", SearchSpawnHandler)
            # Four root exchanges and two child exchanges, regardless of their
            # interleaving. An extra request still fails script exhaustion.
            script = [{"type": "text", "text": "unused", "usage": USAGE} for _ in range(6)]
            with EngineMockProvider(script=script,
                                    record_path=stock_dir / "requests.jsonl") as provider:
                stock = run_stock("mid_turn_compaction", Path(owner.workspace), stock_dir, provider.base_url,
                                  {"model": "gpt-5.5", "window": 272000, "limit": 244800})
                stock_records = list(provider.engine.recorded_requests)
            assert stock["runs"][0]["returncode"] == 0, stock
            path = Path(owner.workspace) / "discovery-child.txt"
            assert path.read_text() == "discovered"
            path.unlink()
            with EngineMockProvider(script=script,
                                    record_path=tmp_path / "bb-requests.jsonl") as provider:
                monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
                monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
                result = engine.run_agentic_loop(
                    "", "Execute mid-turn test prompt", "gpt-5.5", max_steps=8,
                    stream_responses=False, tool_prompt_mode="system_once", completion_sentinel=None,
                    completion_config=config["completion"], context={
                        "session_id": "11111111-1111-7111-8111-111111111111",
                        "input_id": "22222222-2222-7222-8222-222222222222",
                        "turn_id": "33333333-3333-7333-8333-333333333333",
                    },
                )
                bb_records = list(provider.engine.recorded_requests)
            assert not result.get("error"), result
            assert path.read_text() == "discovered"

        def discovery_wire(records):
            return next([item for item in record["json"]["input"]
                         if item.get("type") in {"tool_search_call", "tool_search_output"}]
                        for record in records if any(
                            item.get("type") == "tool_search_output" for item in record["json"]["input"]))

        expected, actual = discovery_wire(stock_records), discovery_wire(bb_records)
        assert json.dumps(actual, sort_keys=True, separators=(",", ":")).encode() == (
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode())
        def child_request(record):
            return any(
                item.get("role") == "user" and isinstance(item.get("content"), list)
                and any(part.get("type") == "input_text" and part.get("text") == "Write discovery child evidence"
                        for part in item["content"] if isinstance(part, dict))
                for item in record["json"]["input"])

        stock_children = [record for record in stock_records if child_request(record)]
        bb_children = [record for record in bb_records if child_request(record)]
        assert len(stock_children) == len(bb_children) == 2
        child_catalog = stock_children[0]["json"]["tools"]
        assert len(child_catalog) == 10
        assert all(tool.get("type") != "tool_search" for tool in child_catalog)
        assert all(record["json"]["tools"] == child_catalog for record in stock_children + bb_children)
        assert all(record["json"]["tools"] == stock_records[0]["json"]["tools"]
                   for record in stock_records + bb_records if not child_request(record))
        assert len(engine._codex_agent_registry.children) == 1
        child = next(iter(engine._codex_agent_registry.children.values()))
        assert child.status == {"completed": "child finished"}
        assert child.config["tools"] == engine.config["tools"]
        assert child.route == "gpt-5.5"
    finally:
        registry = getattr(engine, "_codex_agent_registry", None)
        if registry:
            from breadboard_engine.execution.codex_agents import execute_codex_agent_tool
            for child in registry.children.values():
                if not child.closed:
                    execute_codex_agent_tool(engine, "multi_agent_v1.close_agent", {"target": child.id})


@pytest.mark.parametrize("arguments", [
    {"query": "agent", "limit": -1}, {"query": "agent", "limit": 1.5},
    {"limit": 1}, {"query": None, "limit": 1},
])
def test_decoding_failure_replay_matches_stock_wire(owner, tmp_path, monkeypatch, arguments):
    from copy import deepcopy
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    expected, stock_records = stock_search(tmp_path, monkeypatch, arguments)
    config = deepcopy(owner.config)
    config["tools"]["registry"]["include"] = [
        "exec_command", "write_stdin", "update_plan", "request_user_input", "apply_patch",
        "view_image", "get_goal", "create_goal", "update_goal", "tool_search", "web_search", "multi_agent_v1",
    ]
    config["provider_tools"]["openai"].update({
        "responses_namespaces": True, "responses_tool_search": True,
        "responses_deferred_tools": ["multi_agent_v1"],
        "responses_custom_tools": ["apply_patch"],
        "responses_hosted_outputs": ["web_search_call"],
    })
    engine = OpenAIConductor.__ray_metadata__.modified_class(
        workspace=owner.workspace, image=owner.image, config=config, local_mode=True)
    script = [{"type": "native_search", "arguments": arguments},
              {"type": "text", "text": "Discovery complete", "usage": USAGE}]
    with monkeypatch.context() as patch:
        patch.setattr(codex_lane, "EngineResponsesHandler", DiscoveryHandler)
        with EngineMockProvider(script=script, record_path=tmp_path / "bb-invalid-requests.jsonl") as provider:
            patch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
            patch.setenv("OPENAI_API_KEY", "lane-key")
            result = engine.run_agentic_loop(
                "", "Search invalid argument", "gpt-5.5", max_steps=3,
                stream_responses=False, tool_prompt_mode="system_once", completion_sentinel=None,
                completion_config=config["completion"], context={
                    "session_id": "11111111-1111-7111-8111-111111111111",
                    "input_id": "22222222-2222-7222-8222-222222222222",
                    "turn_id": "33333333-3333-7333-8333-333333333333",
                })
            records = list(provider.engine.recorded_requests)
    assert not result.get("error"), result
    assert not (result.get("completion_summary") or {}).get("error"), result
    actual = next(item for record in records for item in record["json"]["input"]
                  if item.get("type") == "tool_search_output")
    assert json.dumps(actual, sort_keys=True, separators=(",", ":")).encode() == (
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode())
    assert actual["call_id"] == "search-1"
    assert actual["tools"] == []
    expected_call = next(item for record in stock_records for item in record["json"]["input"]
                         if item.get("type") == "tool_search_call")
    actual_call = next(item for record in records for item in record["json"]["input"]
                       if item.get("type") == "tool_search_call")
    assert actual_call == expected_call
