"""Run the admitted OpenHands native actor against packet scripted responses.

This is the deterministic local replay seam used when a full Apptainer lease is
not available. It still exercises OpenHandsActor, the persistent subprocess
terminal, native per-call dispatch, finish cutoff, and response classification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from breadboard.rl.harness.openhands_worker import OpenHandsActor


def _completion(case_id: str, index: int, step: dict[str, Any]) -> dict[str, Any]:
    calls = [
        {
            "id": item.get("id", f"oh-capture-{case_id}-{index:02d}-{position:02d}"),
            "type": "function",
            "function": {"name": item["name"], "arguments": item.get("arguments", "{}").replace("/workspace", step["_workspace"])},
        }
        for position, item in enumerate(step.get("tool_calls", []))
    ]
    message = {"role": "assistant", "content": step.get("content"), "tool_calls": calls}
    if "reasoning_content" in step:
        message["reasoning_content"] = step["reasoning_content"]
    return {
        "id": f"oh-capture-response-{case_id}-{index:02d}",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": message, "finish_reason": step.get("finish_reason", "tool_calls" if calls else "stop")}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


class _ScriptedChannel:
    def __init__(self, case_id: str, steps: list[dict[str, Any]]) -> None:
        self.case_id = case_id
        self.steps = steps
        self.index = 0
        self.requests: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None

    def respond(self, result: dict[str, Any]) -> None:
        request = result["http_request"]
        body = json.loads(base64.b64decode(request["body_b64"]))
        self.requests.append({"index": len(self.requests), "body": body})
        self.pending = result

    def receive(self) -> dict[str, Any]:
        if self.pending is None or self.index >= len(self.steps):
            raise RuntimeError("scripted provider response is unavailable")
        response = _completion(self.case_id, self.index, self.steps[self.index])
        self.index += 1
        self.requests[-1]["response"] = response
        payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
        self.pending = None
        return {
            "operation": "provider_response",
            "payload": {
                "status_code": 200,
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(payload).decode("ascii"),
            },
        }


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _normalizations(value: dict[str, Any]) -> list[str]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    rules: list[str] = []
    if re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", encoded):
        rules.append("event_uuid:<EVENT_UUID>")
    if "oh-capture-response-" in encoded:
        rules.append("response_id:<RESPONSE_ID>")
    if re.search(r"oh-capture-(?!response-)", encoded):
        rules.append("call_id:<CALL_ID>")
    if '"timestamp"' in encoded or re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", encoded):
        rules.append("timestamp:<TIMESTAMP>")
    if re.search(r"/workspace(?:/|[\"'])", encoded):
        rules.append("workspace:<WORKSPACE>")
    if '"hostname"' in encoded or '"host_name"' in encoded:
        rules.append("hostname:<HOSTNAME>")
    return rules
def run_case(case_id: str, case: dict[str, Any], output_root: Path) -> dict[str, Any]:
    output = output_root / case_id
    shutil.rmtree(output, ignore_errors=True)
    workspace = output / "workspace"
    scratch = output / "scratch"
    workspace.mkdir(parents=True)
    scratch.mkdir(parents=True)
    steps = json.loads(json.dumps(case["steps"]))
    for step in steps:
        step["_workspace"] = str(workspace)
    channel = _ScriptedChannel(case_id, steps)
    actor = OpenHandsActor(channel)
    actor.dispatch(
        "initialize",
        {
            "task": case["task"],
            "model_config": {
                "model_name": "openai/gpt-4o-mini",
                "model_canonical_name": None,
                "max_input_tokens": 128000,
                "base_url": "http://127.0.0.1/v1",
            },
            "workspace": str(workspace),
            "scratch": str(scratch),
        },
    )
    tool_calls: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    file_effects: dict[str, str | None] = {}
    status: str | None = None
    termination = "max_turns"
    try:
        for _ in range(int(case.get("max_iteration_per_run", 16))):
            actor.dispatch("sample", {})
            prepared = actor.dispatch("prepare", {})
            actions = prepared.get("actions", ())
            valid = list(actions)
            response_calls = (
                channel.requests[-1].get("response", {}).get("choices", [{}])[0]
                .get("message", {}).get("tool_calls", ())
            )
            for call in response_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                arguments = function.get("arguments", "{}")
                try:
                    arguments = json.loads(arguments)
                except (TypeError, json.JSONDecodeError):
                    pass
                tool_name = function.get("name")
                match = next(
                    (
                        action for action in valid
                        if action.get("tool_id") == tool_name and action.get("arguments") == arguments
                    ),
                    None,
                )
                if match is not None:
                    valid.remove(match)
                tool_calls.append({
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "security_risk": (match or {}).get("security_risk", "UNKNOWN"),
                })
            for event in prepared.get("event_delta", ()):
                if isinstance(event, dict) and event.get("kind") in {"AgentErrorEvent", "ConversationErrorEvent"}:
                    observations.append({
                        "event_kind": event["kind"],
                        "tool_name": event.get("tool_name"),
                        "is_error": True,
                        "error_text": event.get("error") or event.get("detail") or event.get("code"),
                        "classification": event.get("classification"),
                    })
            for action in actions:
                executed = actor.dispatch("execute", {"index": action["index"], "tool_id": action["tool_id"]})
                events = executed.get("observations", ())
                is_error = any(isinstance(item, dict) and "error" in str(item.get("kind", "")).lower() for item in events)
                result = events[0].get("observation", events[0]) if len(events) == 1 and isinstance(events[0], dict) else events
                observations.append({"event_kind": "ObservationEvent", "tool_name": action["tool_id"], "is_error": is_error, "result": result})
            committed = actor.dispatch("commit", {})
            file_effects.update(committed.get("file_effects", {}))
            status = committed.get("status")
            if status in {"FINISHED", "ERROR", "STUCK"}:
                termination = "finished" if status == "FINISHED" else ("error" if status == "ERROR" else "stuck")
                break
        if status == "RUNNING":
            status = "ERROR"
            termination = "error"
            observations.append({
                "event_kind": "ConversationErrorEvent",
                "tool_name": None,
                "is_error": True,
                "error_text": f"Agent reached maximum iterations limit ({case.get('max_iteration_per_run')}).",
                "classification": {"error_id": None, "kind": "agent_action", "retryable": False, "user_action": "none"},
            })
        for path in case.get("probe_paths", ()):
            file_effects.setdefault(path, None)
    finally:
        actor.close()
    native_stop_reason = None
    if channel.requests:
        choices = channel.requests[-1].get("response", {}).get("choices", ())
        if choices:
            native_stop_reason = choices[-1].get("finish_reason")
    trace = {
        "schema_version": "bb.e4.openhands-sdk-trace.v1",
        "case_id": case_id,
        "requests": channel.requests,
        "tool_calls": tool_calls,
        "observations": observations,
        "file_effects": file_effects,
        "termination": {"kind": termination, "native_stop_reason": native_stop_reason},
        "request_count": len(channel.requests),
    }
    trace["normalizations"] = _normalizations(trace)
    output.mkdir(parents=True, exist_ok=True)
    (output / "bb_replay_trace.json").write_text(json.dumps(trace, separators=(",", ":")) + "\n", encoding="utf-8")
    return trace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"
    cases = json.loads((args.packet_root / "kit" / "openhands_capture_cases.json").read_bytes())["cases"]
    for case_id, case in cases.items():
        trace = run_case(case_id, case, args.output_root)
        print(f"{case_id}: requests={trace['request_count']} termination={trace['termination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
