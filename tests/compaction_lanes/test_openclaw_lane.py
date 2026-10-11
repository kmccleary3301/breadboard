"""Strict differential gates, including declared-deviation negative guards."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from breadboard_engine.compaction_lanes import openclaw_lane as lane


def record(body):
    raw = lane.canonical(body)
    return {"path": "/v1/chat/completions", "json": body, "raw_body_bytes": raw.hex()}


def summary_pair():
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    from breadboard.rl.harness.runners.openclaw_semantics import OPENCLAW_CONSUMER_ID
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    opening = record({"messages": [{"role": "system", "content": "episode"}], "tools": tools})
    stock = {"model": "model-a", "messages": [
        {"role": "system", "content": NATIVE_STREAM_PROFILES[OPENCLAW_CONSUMER_ID].compaction_summary_system_prompt},
        {"role": "user", "content": "Summarize the preceding conversation."}], "max_completion_tokens": 2048}
    bb = deepcopy(stock)
    bb["tools"] = tools
    return opening, stock, bb


@pytest.mark.parametrize("field,side,value", [
    ("tools", "stock", []), ("tools", "bb", []),
    ("max_completion_tokens", "stock", 99), ("max_completion_tokens", "bb", 99),
])
def test_deviation_value_guard(field, side, value):
    opening, stock, bb = summary_pair()
    (stock if side == "stock" else bb)[field] = value
    with pytest.raises(ValueError, match="recorded deviation"):
        lane.compare([opening, record(stock)], [opening, record(bb)])


def test_only_admitted_summary_fields_are_removed():
    opening, stock, bb = summary_pair()
    result = lane.compare([opening, record(stock)], [opening, record(bb)])
    assert result["diffs"] == []
    bb["tool_choice"] = "auto"
    assert len(lane.compare([opening, record(stock)], [opening, record(bb)])["diffs"]) == 1


def clamped_summary_pair():
    opening, stock, bb = summary_pair()
    # ceil(44678 * 1.25 / 4) = 13962; 16000 - 13962 - 1 = 2037.
    text = "x" * (44678 - len(stock["messages"][0]["content"]))
    stock["messages"][1]["content"] = bb["messages"][1]["content"] = text
    stock["max_completion_tokens"] = 2037
    return opening, stock, bb


def test_proxy_clamped_2037_stock_cap_is_admitted():
    opening, stock, bb = clamped_summary_pair()
    assert lane.compare([opening, record(stock)], [opening, record(bb)])["diffs"] == []


@pytest.mark.parametrize("side,value", [("stock", 2038), ("bb", 2037)])
def test_proxy_clamped_cap_guard_rejects_wrong_values(side, value):
    opening, stock, bb = clamped_summary_pair()
    (stock if side == "stock" else bb)["max_completion_tokens"] = value
    with pytest.raises(ValueError, match="recorded deviation"):
        lane.compare([opening, record(stock)], [opening, record(bb)])


@pytest.mark.skipif(not os.environ.get("OPENCLAW_DIST"), reason="pinned stock transport required")
@pytest.mark.parametrize("suffix", ["", "漢𠀀😀"])
def test_proxy_cap_formula_matches_stock_transport(suffix):
    _, body, _ = clamped_summary_pair()
    body["messages"][1]["content"] += suffix
    code = """
import { pathToFileURL } from "node:url";
const { t: build } = await import(pathToFileURL(process.env.OPENCLAW_DIST + "/openai-transport-stream-D950WgL3.mjs"));
const body = JSON.parse(process.argv[1]);
const wire = build({id:"model-a",name:"fixture",api:"openai-completions",provider:"openai",
  baseUrl:"http://127.0.0.1:1/v1",input:["text"],contextWindow:16000,maxTokens:2048},
  {systemPrompt:body.messages[0].content,messages:body.messages.slice(1)}, {maxTokens:3200});
console.log(JSON.stringify(wire.max_completion_tokens));
"""
    proc = subprocess.run(["node", "--input-type=module", "-e", code, json.dumps(body)],
                          check=True, capture_output=True, text=True)
    assert lane.stock_summary_cap(body) == json.loads(proc.stdout)


def test_string_whitespace_and_array_order_remain_strict():
    assert lane.canonical({"a": "a b"}) != lane.canonical({"a": "a  b"})
    assert lane.canonical({"a": [1, 2]}) != lane.canonical({"a": [2, 1]})
    assert lane.canonical({"a": 1, "b": 2}) == lane.canonical({"b": 2, "a": 1})


@pytest.mark.skipif(not os.environ.get("OPENCLAW_DIST"), reason="pinned stock formatter required")
def test_one_millisecond_timestamp_difference_is_not_masked():
    # Stock renders minutes, so probe adjacent milliseconds spanning its boundary.
    assert lane.EPISODE_TIMESTAMP_MS % 60000 == 59999
    code = """
import { normalizeMessagesForLlmBoundary } from "openclaw:pinned-attempt-prompt";
const timestamps = JSON.parse(process.argv[1]);
console.log(JSON.stringify(timestamps.map(timestamp =>
  normalizeMessagesForLlmBoundary([{role:"user",content:"clock probe",timestamp}],
    {timezone:"UTC",includeTimestamp:true})[0].content)));
"""
    proc = subprocess.run(["node", "--import", str(lane.ROOT / "breadboard/rl/harness/openclaw_classifier_loader.mjs"),
                           "--input-type=module", "-e", code,
                           json.dumps([lane.EPISODE_TIMESTAMP_MS, lane.EPISODE_TIMESTAMP_MS + 1])],
                          check=True, capture_output=True, text=True)
    left, right = json.loads(proc.stdout)
    opening, _, _ = summary_pair()
    result = lane.compare([opening, record({"messages": [{"role": "user", "content": left}]})],
                          [opening, record({"messages": [{"role": "user", "content": right}]})])
    assert len(result["diffs"]) == 1


@pytest.fixture(scope="module", params=lane.SCENARIOS)
def report(tmp_path_factory, request):
    if not os.environ.get("OPENCLAW_DIST"):
        pytest.skip("OPENCLAW_DIST is required for pinned stock execution")
    out = Path(os.environ.get("BB_OPENCLAW_LANE_REPORT_DIR", str(tmp_path_factory.mktemp("openclaw-lane"))))
    return lane.run_lane(out / request.param, (request.param,))


def test_real_stock_and_production_request_parity(report):
    scenario, result = next(iter(report["lane_results"].items()))
    assert "execution_error" not in result["stock_result"]
    assert result["stock_requests"] > 0
    assert result["bb_requests"] > 0
    assert result["diffs"] == []


def test_compaction_lifecycle_and_recovery_bound(report):
    scenario, result = next(iter(report["lane_results"].items()))
    assert result["stock_compactions"] == result["bb_compactions"]
    assert result["bb_overflow_attempts"] == len(result["stock_overflow_attempts"])
    if scenario == "threshold":
        assert result["stock_compactions"] == 1
        assert result["stock_requests"] - result["stock_summary_requests"] == 4
    elif scenario in {"split_turn", "length_stop"}:
        assert result["stock_compactions"] >= 2
        assert result["stock_turn_prefix_requests"] > 0
        assert result["bb_turn_prefix_requests"] > 0
    else:
        assert result["stock_compactions"] == result["bb_compactions"] == 3
        assert result["stock_overflow_attempts"] == [1, 2, 3]
        assert result["stock_overflow_exhausted"] is True
        assert result["bb_overflow_attempts"] == 3
        assert result["bb_overflow_bound_reached"] is True


def test_main_and_summary_tool_choice_are_source_owned(report):
    for scenario in report["lane_results"]:
        folder = Path(report["artifact_root"]) / scenario
        for side in ("stock", "bb"):
            requests = [json.loads(line) for line in (folder / f"{side}_requests.jsonl").read_text().splitlines()]
            for request in requests:
                body = request["json"]
                if request["path"] != "/v1/chat/completions":
                    continue
                if lane.is_summary(body):
                    assert "tool_choice" not in body
                else:
                    assert body["tool_choice"] == "auto"


def test_replay_trace_records_stock_transport_cap(report):
    scenario, result = next(iter(report["lane_results"].items()))
    folder = Path(report["artifact_root"]) / scenario
    stock = [json.loads(line)["json"] for line in (folder / "stock_requests.jsonl").read_text().splitlines()]
    wire_caps = [body["max_completion_tokens"] for body in stock if lane.is_summary(body)]
    trace_caps = [
        request["max_tokens"]
        for phase in result["bb_result"]["phases"]
        if phase["result"]["kind"] == "compaction_prepared"
        for request in (phase["result"]["summary_request"], phase["result"]["turn_prefix_request"])
        if request is not None
    ]
    assert trace_caps == wire_caps


def test_primary_and_summary_payload_parity(report):
    # This isolates compaction defects; the full-request gate above still includes
    # embeddings and fails on any unmatched auxiliary request.
    scenario = next(iter(report["lane_results"]))
    folder = Path(report["artifact_root"]) / scenario
    requests = {
        side: [json.loads(line) for line in (folder / f"{side}_requests.jsonl").read_text().splitlines()]
        for side in ("stock", "bb")
    }
    chat = {side: [request for request in values if request["path"] == "/v1/chat/completions"]
            for side, values in requests.items()}
    assert lane.compare(chat["stock"], chat["bb"])["diffs"] == []


def test_caller_continuation_is_source_exact_and_not_persisted(report):
    scenario, result = next(iter(report["lane_results"].items()))
    assert result["clock_timestamps"]["stock"] == result["clock_timestamps"]["bb"]
    assert result["clock_timestamps"]["stock"][0] == lane.EPISODE_TIMESTAMP_MS
    folder = Path(report["artifact_root"]) / scenario
    marker = "Continue the current task from the existing transcript"
    guards = {}
    for side in ("stock", "bb"):
        guards[side] = []
        for line in (folder / f"{side}_requests.jsonl").read_text().splitlines():
            body = json.loads(line)["json"]
            if lane.is_summary(body):
                assert marker not in json.dumps(body)
            else:
                guards[side].extend(message["content"].encode("utf-8") for message in body.get("messages", [])
                                    if message["role"] == "user" and isinstance(message["content"], str)
                                    and marker in message["content"])
    assert guards["stock"] == guards["bb"]
    if scenario == "threshold":
        assert guards["stock"] == []
    else:
        assert guards["stock"]
    for phase in result["bb_result"]["phases"]:
        if phase["result"]["kind"] == "compaction_finalized":
            assert marker not in json.dumps(phase["result"]["messages"])


def test_memory_transcript_and_header_proof(report):
    _, result = next(iter(report["lane_results"].items()))
    assert result["logical_transcript_comparison"]["diffs"] == []
    assert result["logical_transcript_comparison"]["checkpoints"]
    assert result["logical_transcript_comparison"]["excluded_bookkeeping_columns"] == ["session_id", "seq", "created_at"]
    for checkpoint in result["stock_result"]["lane_transcript_checkpoints"]:
        assert checkpoint["columns"] == ["session_id", "seq", "event_json", "created_at"]
    assert result["embeddings_header_names"]["stock"] == result["embeddings_header_names"]["bb"]
    assert "fixture-key" not in json.dumps(result["bb_result"])
    for names in result["embeddings_header_names"]["stock"]:
        assert names == ["authorization", "content-type"]
    if result["stock_overflow_attempts"]:
        assert result["auxiliary_requests"]["stock"]
        assert result["auxiliary_requests"]["stock"] == result["auxiliary_requests"]["bb"]


@pytest.mark.parametrize("field,value", [
    ("method", "GET"), ("path", "/v1/not-embeddings"),
    ("raw_body_bytes", b'{"input": ["a"], "model":"x"}'.hex()),
    ("source_header_names", ["content-type"]),
])
def test_embeddings_comparison_remains_byte_strict(field, value):
    stock = {"method": "POST", "path": "/v1/embeddings",
             "json": {"model": "x", "input": ["a"]},
             "raw_body_bytes": b'{"model":"x","input":["a"]}'.hex(),
             "source_header_names": ["authorization", "content-type"]}
    bb = deepcopy(stock)
    bb[field] = value
    assert len(lane.compare([stock], [bb])["diffs"]) == 1


def test_logical_transcript_preserves_content_and_parent_graph():
    rows = [
        {"id": "session", "type": "session"},
        {"id": "user", "parentId": None, "type": "message", "timestamp": "2026-10-09T00:00:00Z",
         "message": {"role": "user", "content": "a", "timestamp": 1}},
        {"id": "meta", "parentId": "user", "type": "custom", "customType": "model-snapshot"},
        {"id": "assistant", "parentId": "meta", "type": "message", "timestamp": "2026-10-09T00:00:00Z",
         "message": {"role": "assistant", "content": "b", "timestamp": 2}},
    ]
    expected = lane.logical_transcript(rows)["messages"]
    assert expected[1]["parent"] == 0
    changed = deepcopy(rows)
    changed[-1]["message"]["content"] = "b "
    assert lane.logical_transcript(changed)["messages"] != expected
    changed = deepcopy(rows)
    changed[-1]["parentId"] = None
    assert lane.logical_transcript(changed)["messages"] != expected
    changed[-1]["parentId"] = "assistant"
    with pytest.raises(ValueError, match="precede"):
        lane.logical_transcript(changed)


@pytest.mark.skipif(not os.environ.get("OPENCLAW_DIST"), reason="pinned stock indexing required")
def test_embeddings_404_returns_to_stock_and_keeps_compaction(tmp_path):
    report = lane.run_lane(tmp_path / "embeddings-404", ("split_turn",), embeddings_status_code=404)
    result = report["lane_results"]["split_turn"]
    assert result["diffs"] == []
    assert result["logical_transcript_comparison"]["diffs"] == []
    assert result["stock_compactions"] == result["bb_compactions"] >= 2
    exchanges = [phase["result"]["request"] for phase in result["bb_result"]["phases"]
                 if phase["result"]["kind"] == "provider_exchange"]
    assert exchanges
    logs = (tmp_path / "embeddings-404/split_turn/stock_stderr.txt").read_text()
    assert "memory" in logs.lower()
    assert "404" in logs


def provider_client(tmp_path, *, caller_headers=None):
    from tests.rl.harness import openclaw_native_stream_test_support as h
    import hashlib
    profile = h.OpenAICompletionsProviderProfile(
        model="model-a", scoped_credential="private-policy-key",
        base_url="http://127.0.0.1:1/v1", context_window=lane.WINDOW,
        max_output_tokens=2048, caller_headers=caller_headers or {},
        request_policy={"mode": "streaming", "include_usage": True,
                        "strict_tools": None, "enable_thinking": None,
                        **h._SUPPLIER_WIRE_POLICY["request_policy"]},
        capabilities=dict(h._SUPPLIER_WIRE_POLICY["capabilities"]))
    projection, semantics, manifest = h._compile_target(
        tmp_path, profile_digest=h.profile_identity_digest(profile),
        target_id=lane.TARGET_ID, context_window=lane.WINDOW)
    observation = h._observation(
        provider_id="openai", model_id="model-a", route_id="route-a",
        credential_handle_id="credential-a", protocol_abi="responses-v1",
        capabilities=h._policy_capabilities(request_features=sorted([
            "json_mode", "seed", "stream_options", "streaming", *h._SUPPLIER_WIRE_POLICY["request_features"]])))
    plan = h._plan(
        observation=observation, semantics=semantics,
        tools=tuple(h._tool_grant(name) for name in lane.configuration()[0]["tools"]["ordered"]),
        policy_slot_ids=("model:model-a",), limit_updates={"max_turns": 8, "action_timeout_ms": 35000},
        implementation_digest=h.CONDUCTOR_IMPLEMENTATION_DIGEST)
    payload = plan.model_dump(mode="python")
    identity = plan.base_compiled.model_dump(mode="python")
    identity.update(manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
                    compiler_input_digest=manifest.inputs.compiler_input_digest)
    payload["base_compiled"] = h.c.CompiledArtifactIdentity.model_validate(identity)
    plan = h.c.EffectiveExecutionPlan.model_validate(payload)
    client = h.EpisodeOpenAICompletionsPolicyClient(
        episode_id="provider-exchange-test", effective_plan_digest=plan.canonical_digest(),
        observation=observation, profile=profile, target_projection=projection, timeout_seconds=45)
    client.bind_compiled_plan(plan)
    assert b"private-policy-key" not in manifest.canonical_bytes()
    return client, plan


def embeddings_request(*, caller=False):
    import base64
    return {"provider_exchange": "embeddings", "method": "POST",
            "url": "http://127.0.0.1:1/v1/embeddings",
            "header_names": ["authorization", "content-type"] + (["x-policy-caller"] if caller else []),
            "body_b64": base64.b64encode(
                b'{"model":"text-embedding-3-small","input":["User: fixture"]}').decode()}


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("url", "https://wrong.example/v1/embeddings"),
    ("header_names", ["content-type"]),
    ("header_names", [1, "authorization", "content-type"]),
    ("body_b64", "not base64"),
    ("body_b64", "e30="),
])
async def test_provider_exchange_rejects_unbound_values(tmp_path, field, value):
    from breadboard.rl.harness.runners.base import RunnerProtocolError, RunnerPolicyBindingError
    client, _ = provider_client(tmp_path)
    request = embeddings_request()
    request[field] = value
    try:
        with pytest.raises((RunnerProtocolError, RunnerPolicyBindingError)):
            client.stage_native_http_request(request)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 404])
async def test_provider_exchange_uses_bound_transport_headers_and_private_response(tmp_path, monkeypatch, status):
    import base64
    from contextlib import contextmanager
    import httpx
    from breadboard_engine.compilation.contracts import canonical_sha256
    from breadboard.rl.harness.runners.base import PolicyRuntimeInvokeRequest, freeze_json_object, thaw_json
    from breadboard.rl.harness.runners.native_compaction import drain_provider_exchanges
    client, plan = provider_client(tmp_path, caller_headers={"X-Policy-Caller": "private-caller"})
    sent = []
    body = b'{"error":{"message":"unsupported"}}' if status == 404 else b'{"data":[]}'

    @contextmanager
    def stream(method, url, **kwargs):
        sent.append({"method": method, "url": url, **kwargs})
        response = httpx.Response(status, headers={
            "content-type": "application/json", "set-cookie": "private-cookie",
            "authorization": "Bearer private-response-key",
        }, stream=httpx.ByteStream(body), request=httpx.Request(method, url))
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr(client._transport.http_client, "stream", stream)
    try:
        private = client.native_provider_configuration()
        assert private["headers"] == {"X-Policy-Caller": "private-caller",
                                      "Authorization": "Bearer private-policy-key"}
        public = client.stage_native_http_request(embeddings_request(caller=True))
        result = await client.invoke(PolicyRuntimeInvokeRequest(
            episode_id="provider-exchange-test", effective_plan_digest=plan.canonical_digest(),
            binding_digest="sha256:" + "1" * 64, policy_slot_id="model:model-a",
            request_digest=canonical_sha256(public),
            request_payload=freeze_json_object(public, field_name="native request"),
            turn=1, attempt=1))
        response = client.take_native_http_response(result.response_digest)
        assert response["status_code"] == status
        assert base64.b64decode(response["body_b64"]) == body
        resumed = []

        async def phase(operation, payload):
            assert operation == "drain_provider_exchanges"
            if "response" in payload:
                resumed.append(payload["response"])
                return {"kind": "provider_exchanges_drained"}
            return {"kind": "provider_exchange", "request": embeddings_request(caller=True)}

        async def exchange(request):
            assert request == embeddings_request(caller=True)
            return response

        trace = []
        await drain_provider_exchanges(
            phase=phase, exchange=exchange, trace_requests=trace, checkpoint="agent_end",
        )
        assert resumed == [response]
        assert ["set-cookie", "private-cookie"] in resumed[0]["headers"]
        assert ["authorization", "Bearer private-response-key"] in resumed[0]["headers"]
        assert trace[0]["response"] == thaw_json(result.response_payload)["native_http_response"]
        assert "headers" not in trace[0]["response"]
        assert sent[0]["headers"] == [
            ("X-Policy-Caller", "private-caller"),
            ("Content-Type", "application/json"),
            ("Authorization", "Bearer private-policy-key")]
        assert sent[0]["content"] == base64.b64decode(embeddings_request()["body_b64"])
        for secret in ("private-caller", "private-policy-key", "private-cookie", "private-response-key"):
            assert secret not in json.dumps(public)
            assert secret not in str(result)
            assert secret not in json.dumps(trace)
    finally:
        await client.close()


@pytest.mark.parametrize("target_id", ["openclaw@2026.9.4", lane.TARGET_ID])
def test_runtime_exchange_capability_keeps_compiled_identity_byte_identical(tmp_path, monkeypatch, target_id):
    from dataclasses import replace
    from breadboard.rl.harness import native_stream_profiles
    from tests.rl.harness import openclaw_native_stream_test_support as h
    digest = "sha256:" + "1" * 64
    _, _, before = h._compile_target(tmp_path, profile_digest=digest, target_id=target_id)
    profiles = dict(native_stream_profiles.NATIVE_STREAM_PROFILES)
    profiles[h.OPENCLAW_RESPONSE_CONSUMER_ID] = replace(
        profiles[h.OPENCLAW_RESPONSE_CONSUMER_ID], provider_exchanges=frozenset())
    monkeypatch.setattr(native_stream_profiles, "NATIVE_STREAM_PROFILES", profiles)
    _, _, after = h._compile_target(tmp_path, profile_digest=digest, target_id=target_id)
    assert before.canonical_bytes() == after.canonical_bytes()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("OPENCLAW_DIST"), reason="pinned stock delta timer required")
async def test_stock_session_delta_timer_drains_on_final_numeric_turn(tmp_path, monkeypatch):
    import asyncio
    from tests.rl.harness import openclaw_native_stream_test_support as h
    timer = tmp_path / "delta-timer.mjs"
    proof = tmp_path / "delta-timer-proof.json"
    # Only the real source session-delta timer is held. At classified-result
    # delivery invoke its unchanged callback, and release the frame once the
    # unchanged sync has begun. No sleeps, replacement indexer, or fake exchange.
    timer.write_text(r'''
import { pathToFileURL } from "node:url";
import { writeFileSync } from "node:fs";
const { MemoryIndexManager } = await import(pathToFileURL(
  process.env.OPENCLAW_DIST + "/extensions/memory-core/manager-runtime.js"));
const set = globalThis.setTimeout;
const clear = globalThis.clearTimeout;
let pending;
const started = Promise.withResolvers();
globalThis.setTimeout = function(callback, ms, ...args) {
  if (ms === 5000 && callback.toString().includes("processSessionUpdateBatch")) {
    const handle = set(() => {}, 2147483647);
    handle.unref();
    pending = { callback, args, handle, ms };
    return handle;
  }
  return set(callback, ms, ...args);
};
const sync = MemoryIndexManager.prototype.sync;
MemoryIndexManager.prototype.sync = function(params) {
  const work = sync.call(this, params);
  if (params?.reason === "session-delta") {
    writeFileSync(process.env.BB_DELTA_TIMER_PROOF,
      JSON.stringify({ delay_ms: pending.ms, reason: params.reason }));
    started.resolve();
  }
  return work;
};
const write = process.stdout.write.bind(process.stdout);
let fired = false;
process.stdout.write = function(chunk, ...args) {
  if (!fired && pending && chunk.toString().includes('"kind":"classified_result"')) {
    fired = true;
    clear(pending.handle);
    pending.callback(...pending.args);
    started.promise.then(() => write(chunk, ...args));
    return true;
  }
  return write(chunk, ...args);
};
''')
    create = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        if args[0] == "node" and args[-1] == str(h._WORKER):
            args = (args[0], "--import", str(timer), *args[1:])
            kwargs["env"] = {**kwargs["env"], "BB_DELTA_TIMER_PROOF": str(proof)}
        return await create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    invoke = h.EpisodeOpenAICompletionsPolicyClient.invoke
    exchange_turns = []

    async def observe(client, request):
        if h.thaw_json(request.request_payload).get("native_http_request", {}).get("provider_exchange") == "embeddings":
            exchange_turns.append(request.turn)
        return await invoke(client, request)

    monkeypatch.setattr(h.EpisodeOpenAICompletionsPolicyClient, "invoke", observe)
    workspace = tmp_path / "workspace"
    lane.seed(workspace)
    provider = lane.provider_for("split_turn", tmp_path / "requests.jsonl")
    base = provider.start()
    try:
        result = await lane.run_bb(workspace, tmp_path / "compiled", base)
    finally:
        provider.stop()
    assert json.loads(proof.read_text()) == {"delay_ms": 5000, "reason": "session-delta"}
    final = [phase for phase in result["phases"] if phase["checkpoint"] == "agent_end"
             and phase["result"]["kind"] == "provider_exchange"]
    assert len(final) == 1
    # The overflow retry stays inside turn4 (conductor's inner request loop);
    # six main requests therefore end on the fifth recorded source turn.
    assert exchange_turns[-1] == 5
    assert all(type(turn) is int and turn > 0 for turn in exchange_turns)
    trace = result["response"]["replay_trace"]["requests"]
    assert any(item.get("_provider_exchange") == "embeddings"
               and item["checkpoint"] == "agent_end" for item in trace)
