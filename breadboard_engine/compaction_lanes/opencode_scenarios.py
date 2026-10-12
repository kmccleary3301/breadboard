"""opencode scenarios responsibility owner."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
import yaml

ROOT = Path(__file__).resolve().parents[2]

STOCK_VERSION = "1.2.17"

PLUGIN_COMMIT = "5137df72d8fab3fec609c82f91387db8e3b13825"

PROFILES = {
    "opencode": "agent_configs/opencode_1-2-17_e4_10-9-2026.yaml",
    "oh-my-opencode": "agent_configs/oh_my_opencode_3-10-0_e4_10-9-2026.yaml",
}

SCENARIOS = ("auto_prune", "overflow", "final_threshold")

PLUGIN_SCENARIOS = ("delegate_prune", "plugin_error", "delegate_idle")

TEST_WINDOW = 64000

LOW_USAGE = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

SOURCE_API_USERNAME = "opencode"

SOURCE_API_PASSWORD = "isolated-compaction-lane"

PARITY_REFERENCE = {
    "report": "docs_tmp/RL_TEAM_HANDOFF_20261007/engine_results/PARITY_V2_REPORT.md:1-19",
    "evidence": "docs_tmp/RL_TEAM_HANDOFF_20261007/engine_results/PARITY_V2_EVIDENCE.json:/request",
    "matching_entry": None,
    "reason": "The referenced v2 campaign compares Pi 0.80.2 with BB Pi 0.57.1, not OpenCode. It cannot admit an OpenCode deviation.",
}

PLUGIN_EVIDENCE = [
    "src/index.ts:82-93 (native compaction context and todo capture)",
    "src/hooks/compaction-context-injector/hook.ts:7-71 (summary contributor)",
    "src/plugin/event.ts:137-159 (preemptive-compaction event is not dispatched)",
    "src/plugin/hooks/create-session-hooks.ts:81-85 (preemptive hook requires experimental setting)",
    "src/plugin/event.ts:149 (Anthropic overflow recovery is dispatched)",
    "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts:26-196 (largest-first recovery)",
    "src/features/background-agent/manager.ts:1255-1287,1359-1367 (completed child autonomously prompts its idle parent)",
]

def production_settings(harness: str) -> dict[str, Any]:
    path = ROOT / PROFILES[harness]
    document = yaml.safe_load(path.read_bytes())
    model_id = document["providers"]["default_model"]
    model = next(m for m in document["providers"]["models"] if m["id"] == model_id)
    from breadboard_engine.compaction.presets import load_preset_document
    preset = load_preset_document(document["compaction"]["preset"])
    recovery_attempts = sum(trigger["max_attempts"] for trigger in preset["triggers"]
                            if trigger["kind"] == "pending_error")
    return {"profile": str(path), "profile_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model_id": model_id, "model": model_id.split("/", 1)[1],
            "max_output_tokens": model["max_output_tokens"], "params": model["params"],
            "preset": document["compaction"]["preset"],
            "deferred_recovery_attempts": recovery_attempts,
            "tools": document["tools"]["registry"]["include"],
            "source_api_authentication": {
                "mode": "HTTP Basic authentication on both source serve processes",
                "username": SOURCE_API_USERNAME,
                "stock_password": "Fixed isolated fixture credential; not a provider credential",
                "bb_password": "Production sidecar's generated isolated credential",
                "configuration_fact_not_deviation": True,
                "source": [
                    "packages/opencode/src/cli/cmd/serve.ts:14-15",
                    "packages/opencode/src/cli/cmd/web.ts:36-37",
                    "src/shared/opencode-server-auth.ts:8-17",
                    "src/shared/opencode-http-api.ts:68-72",
                ],
                "unauthenticated_behavior": "Native aggressive PATCH returns false without a password; the plugin falls through to summarize",
            },
            "overrides": {"contextWindow": {"production": document["compaction"]["contextWindow"], "lane": TEST_WINDOW},
                          "max_input_tokens": {"production": model["max_input_tokens"], "lane": TEST_WINDOW},
                          "lsp": {"stock": False, "bb_stock_executor": False, "reason": "equal local tool isolation; stock owns LSP, not the legacy BB wrapper"},
                          "stock_environment": {"OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}}}

def responses_sse(descriptor: dict[str, Any], ordinal: int) -> dict[str, Any]:
    """Compose full Responses SSE events consumed by both real provider SDKs.

    The shared mock's abbreviated output_item.done-only stream is not sufficient
    for AI SDK 5: text/tool starts and deltas must precede completion. Its raw
    response capability keeps the recorder and error handling unchanged.
    """
    if descriptor["type"] == "error":
        return descriptor
    rid = f"resp_lane_{ordinal}"
    events: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    events.append({"type": "response.created", "response": {"id": rid, "model": "gpt-5.1-codex-mini", "status": "in_progress", "created_at": 1700000000, "output": []}})
    text = descriptor.get("text")
    if text is not None:
        mid = f"msg_lane_{ordinal}"
        index = len(output)
        item = {"type": "message", "id": mid, "role": "assistant", "status": "in_progress", "content": []}
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        part = {"type": "output_text", "text": "", "annotations": []}
        events.append({"type": "response.content_part.added", "item_id": mid, "output_index": index, "content_index": 0, "part": part})
        events.append({"type": "response.output_text.delta", "item_id": mid, "output_index": index, "content_index": 0, "delta": text})
        events.append({"type": "response.output_text.done", "item_id": mid, "output_index": index, "content_index": 0, "text": text})
        part = {**part, "text": text}
        events.append({"type": "response.content_part.done", "item_id": mid, "output_index": index, "content_index": 0, "part": part})
        item = {**item, "status": "completed", "content": [part]}
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
        output.append(item)
    for call in descriptor.get("tool_calls", []):
        index = len(output)
        item = {"type": "function_call", "id": f"fc_lane_{ordinal}_{index}", "call_id": call["call_id"], "name": call["name"], "arguments": "", "status": "in_progress"}
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        events.append({"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": index, "delta": call["arguments"]})
        events.append({"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": index, "arguments": call["arguments"]})
        item = {**item, "arguments": call["arguments"], "status": "completed"}
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
        output.append(item)
    usage = {**descriptor["usage"], "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    events.append({"type": "response.completed", "response": {"id": rid, "model": "gpt-5.1-codex-mini", "status": "completed", "created_at": 1700000000, "output": output, "usage": usage}})
    return {"type": "raw", "status_code": 200, "headers": {"content-type": "text/event-stream"},
            "chunks": [f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events]}

def text_response(text: str, usage: dict[str, int] = LOW_USAGE) -> dict[str, Any]:
    return {"type": "text", "text": text, "usage": dict(usage)}

def tool_response(prefix: str, count: int, chars: int, usage: dict[str, int] = LOW_USAGE) -> dict[str, Any]:
    # Valid formatted model arguments exercise stock's native JSON.stringify
    # replay; pre-minifying these would conceal an argument-codec difference.
    calls = [{"name": "bash", "call_id": f"call_{prefix}_{i}",
              # Stock executes this batch concurrently. Stagger completion at
              # the scenario source so both native executions put the plugin's
              # third-completed-tool reminder on the same actual call.
              "arguments": json.dumps({"command": f"sleep {i * 0.2:.1f}; printf '%0{chars}d' 0", "description": f"Read fixture {prefix} {i}"}, indent=2)} for i in range(count)]
    return {"type": "response", "tool_calls": calls, "usage": dict(usage)}

def scenario_turns(scenario: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    # OpenCode's native input reserve is min(20000, capped output).
    threshold = TEST_WINDOW - min(20000, settings["max_output_tokens"])
    high = {"input_tokens": threshold + 1, "output_tokens": 50, "total_tokens": threshold + 51}
    if scenario == "delegate_idle":
        call = {"name": "task", "call_id": "call_delegated",
                "arguments": json.dumps({"subagent_type": "explore", "load_skills": [],
                                         "description": "Delegated inspection",
                                         "prompt": "Inspect the delegated fixture and report completion.",
                                         "run_in_background": True}, separators=(",", ":"))}
        return [
            {"prompt": "Delegate the inspection and wait for its completion.",
             "responses": [{"type": "response", "tool_calls": [call], "usage": dict(LOW_USAGE)},
                           text_response("Parent waiting for delegated inspection.")],
             "autonomous_responses": [
                 tool_response("idle_high1", 1, 8, high),
                 text_response("Autonomous summary one: delegated inspection complete."),
                 tool_response("idle_high2", 1, 8, high),
                 text_response("Autonomous summary two: delegated inspection complete."),
                 text_response("Autonomous inspection complete.")]},
            {"prompt": "Confirm the delegated inspection is complete.",
             "responses": [text_response("The delegated inspection is complete.")]},
        ]
    if scenario == "delegate_prune":
        turns = scenario_turns("auto_prune", settings)
        call = {"name": "task", "call_id": "call_delegated",
                "arguments": json.dumps({"subagent_type": "explore", "load_skills": [],
                                         "description": "Delegated inspection",
                                         "prompt": "Inspect the delegated fixture while the parent compacts.",
                                         "run_in_background": True}, separators=(",", ":"))}
        turns[-1]["responses"][:0] = [
            {"type": "response", "tool_calls": [call], "usage": dict(LOW_USAGE)}]
        return turns
    if scenario == "auto_prune":
        # 12 * 24000 chars exceed both native 40k protected and 20k minimum
        # savings budgets. Three completed user turns precede the compactions.
        return [
            {"prompt": "Inspect the numbered records.", "responses": [tool_response("seed", 12, 24000), text_response("First inspection complete.")]},
            {"prompt": "Record the first inspection.", "responses": [text_response("Second inspection complete.")]},
            {"prompt": "Record the second inspection.", "responses": [text_response("Third inspection complete.")]},
            {"prompt": "Continue the inspection and finish.", "responses": [tool_response("high1", 1, 8, high), text_response("Summary one: first inspection complete."), tool_response("high2", 1, 8, high), text_response("Summary two: second inspection complete."), text_response("Inspection complete.")]},
        ]
    if scenario == "overflow":
        error = {"type": "error", "status_code": 400, "error": "context_length_exceeded", "message": f"prompt is too long: {TEST_WINDOW + 10000} tokens > {TEST_WINDOW} maximum"}
        responses = [error, text_response("Overflow summary: inspection in progress."), text_response("Inspection complete.")]
        if settings["deferred_recovery_attempts"]:
            # Successful summarize keeps pending state; the source preset
            # names the budget. Both real sides receive this identical script.
            for attempt in range(1, settings["deferred_recovery_attempts"] + 1):
                responses.extend([text_response(f"Deferred recovery summary {attempt}: inspection complete."),
                                  text_response(f"Inspection complete after deferred recovery {attempt}.")])
        turns = [
            {"prompt": "Inspect the numbered records.", "responses": [tool_response("overflow_seed", 12, 24000), text_response("First inspection complete.")]},
        ]
        if settings["deferred_recovery_attempts"]:
            # Native prune protects the two newest user turns. Complete actual
            # acknowledgements before delivering overflow, so its 300 ms hook
            # sees the same six eligible outputs regardless of model latency.
            turns.extend([
                {"prompt": "Record the first inspection.", "responses": [text_response("Second inspection complete.")]},
                {"prompt": "Record the second inspection.", "responses": [text_response("Third inspection complete.")],
                 "native_prune_call_ids": [f"call_overflow_seed_{i}" for i in range(6)]},
            ])
        return [*turns, {"prompt": "Finish the inspection.", "responses": responses}]
    if scenario == "plugin_error":
        if not settings["deferred_recovery_attempts"]:
            raise ValueError("The plugin-only error scenario requires its production pending_error trigger")
        responses = [{"type": "error", "status_code": 400, "error": "invalid_request_error",
                      "message": "too many tokens: 74000 tokens > 64000 maximum"}]
        for attempt in range(1, settings["deferred_recovery_attempts"] + 1):
            responses.extend([text_response(f"Plugin-only recovery summary {attempt}: inspection complete."),
                              text_response(f"Inspection complete after plugin-only recovery {attempt}.")])
        return [{"prompt": "Finish the inspection.", "responses": responses}]
    if scenario == "final_threshold":
        return [{"prompt": "Say the analysis is complete.", "responses": [text_response("Inspection complete above threshold.", high), text_response("Final summary: inspection complete."), text_response("Inspection complete.")]}]
    raise ValueError(scenario)
