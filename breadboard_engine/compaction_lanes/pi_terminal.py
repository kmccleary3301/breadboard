"""Observed Pi SDK terminal events and canonical runner outcome comparison."""
from __future__ import annotations

import json
import signal
from subprocess import CompletedProcess
from typing import Any

from breadboard.rl.harness.runners.base import RunnerTermination, thaw_json


def stock_terminal_evidence(process: CompletedProcess[str], *, rpc_cleanup: dict[str, Any] | None = None) -> dict[str, Any]:
    events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    final = next((event for event in reversed(events) if event.get("type") == "agent_end"), None)
    assistant = next((message for message in reversed(final.get("messages", []))
                      if message.get("role") == "assistant"), None) if final else None
    return {"returncode": process.returncode, "last_agent_end": final,
            "last_assistant": assistant, "rpc_cleanup": rpc_cleanup,
            "settlement_event_is_last_stdout_event": bool(
                events and rpc_cleanup and events[-1] == rpc_cleanup.get("settlement_event"))}


def bb_terminal_evidence(result: Any) -> dict[str, Any]:
    if isinstance(result, Exception):
        return {"termination": None, "native_termination": None, "last_assistant": None,
                "exception_type": type(result).__module__ + "." + type(result).__qualname__}
    trace = thaw_json(result.response.get("replay_trace", {}))
    assistant = next((message for message in reversed(trace.get("messages", []))
                      if message.get("role") == "assistant"), None)
    return {"termination": result.termination.value,
            "native_termination": trace.get("termination"), "last_assistant": assistant,
            "exception_type": None}


def _stock_exit_is_admitted(stock: dict[str, Any]) -> bool:
    if stock.get("returncode") == 0:
        return True
    # Stock rpc-mode.js:265-277 exits 143 from its SIGTERM handler; -SIGTERM
    # means the signal arrived before that handler was registered.
    if stock.get("returncode") not in (-signal.SIGTERM, 128 + signal.SIGTERM):
        return False
    cleanup = stock.get("rpc_cleanup") or {}
    pid = cleanup.get("pid")
    if (cleanup.get("controller") != "pi_stock.run_stock_pi_rpc"
            or type(pid) is not int or pid <= 0
            or type(cleanup.get("reader_error_count")) is not int
            or cleanup["reader_error_count"] != 0
            or cleanup.get("requested_signal") != "SIGTERM"
            or cleanup.get("wait_returncode") != stock["returncode"]
            or cleanup.get("kill_requested") is not False
            or stock.get("settlement_event_is_last_stdout_event") is not True
            or any(cleanup.get(flag) is not True for flag in (
                "settled", "agent_ended", "settlement_before_cleanup",
                "process_running_before_terminate", "terminate_requested", "wait_completed"))):
        return False
    event = cleanup.get("settlement_event") or {}
    if event.get("type") in ("auto_compaction_end", "compaction_end"):
        return event.get("willRetry") is False
    state = event.get("data") or {}
    return (event.get("type") == "response" and event.get("success") is True
            and isinstance(event.get("id"), str) and event["id"].startswith("settle-")
            and state.get("isStreaming") is False and state.get("isCompacting") is False)


def compare_terminal_states(stock: dict[str, Any], bb: dict[str, Any], *,
                            expected_provider_error: bool) -> list[dict[str, Any]]:
    """Compare source policy-error/completion semantics, never incidental error text."""
    diffs = []
    expected_reason = "error" if expected_provider_error else "stop"
    assistant = stock.get("last_assistant")
    stock_reason = assistant.get("stopReason") if isinstance(assistant, dict) else None
    if not _stock_exit_is_admitted(stock) or stock_reason != expected_reason:
        diffs.append({"type": "stock_terminal_state", "expected_stop_reason": expected_reason,
                      "observed": stock})
    expected_termination = (RunnerTermination.POLICY_INCOMPLETE if expected_provider_error
                            else RunnerTermination.ASSISTANT_COMPLETE).value
    if bb.get("exception_type") is not None or bb.get("termination") != expected_termination:
        diffs.append({"type": "bb_terminal_state", "expected_termination": expected_termination,
                      "observed": bb})
    if expected_provider_error:
        native = bb.get("native_termination") or {}
        assistant = bb.get("last_assistant") or {}
        if (native.get("kind") != "error" or native.get("native_stop_reason") != "error"
                or assistant.get("role") != "assistant" or assistant.get("stopReason") != "error"):
            diffs.append({"type": "bb_native_provider_error_state", "observed": bb})
    return diffs
