"""Behavioral source-terminal comparison, independent of raw request parity."""
import json
from subprocess import CompletedProcess

import pytest

from breadboard_engine.compaction_lanes.pi_terminal import compare_terminal_states, stock_terminal_evidence


def _states(error):
    reason = "error" if error else "stop"
    assistant = {"role": "assistant", "stopReason": reason, "errorMessage": "stock source text"}
    stock = stock_terminal_evidence(CompletedProcess([], 0, json.dumps({
        "type": "agent_end", "messages": [assistant]}), ""))
    bb = {"termination": "policy_incomplete" if error else "assistant_complete",
          "native_termination": {"kind": "error", "native_stop_reason": "error"} if error else None,
          "last_assistant": {**assistant, "errorMessage": "SDK formatted actor text"},
          "exception_type": None}
    return stock, bb


@pytest.mark.parametrize("error", [False, True])
def test_terminal_semantics_match_without_copying_error_text(error):
    stock, bb = _states(error)
    assert compare_terminal_states(stock, bb, expected_provider_error=error) == []


@pytest.mark.parametrize("variant", ["exception", "wrong_termination", "missing_message", "wrong_native_reason", "wrong_native_kind"])
def test_raw_parity_cannot_admit_wrong_or_unproven_provider_error_state(variant):
    stock, bb = _states(True)
    if variant == "exception":
        bb["exception_type"] = "RunnerDependencyError"
    elif variant == "wrong_termination":
        bb["termination"] = "assistant_complete"
    elif variant == "missing_message":
        bb["last_assistant"] = None
    elif variant == "wrong_native_reason":
        bb["native_termination"]["native_stop_reason"] = "stop"
    else:
        bb["native_termination"]["kind"] = "final_answer"
    assert compare_terminal_states(stock, bb, expected_provider_error=True)


@pytest.mark.parametrize("variant", ["missing_agent_end", "nonzero_exit", "wrong_stop_reason"])
def test_stock_completion_must_be_observed_not_inferred_from_request_counts(variant):
    stock, bb = _states(False)
    if variant == "missing_agent_end":
        stock = stock_terminal_evidence(CompletedProcess([], 0, '{"type":"agent_start"}\n', ""))
    elif variant == "nonzero_exit":
        stock["returncode"] = 1
    else:
        stock["last_assistant"]["stopReason"] = "error"
    assert compare_terminal_states(stock, bb, expected_provider_error=False)


def _owned_sigterm_states(returncode=-15):
    stock, bb = _states(True)
    settlement = {"type": "auto_compaction_end", "willRetry": False}
    cleanup = {
        "pid": 1234, "controller": "pi_stock.run_stock_pi_rpc",
        "settled": True, "agent_ended": True, "reader_error_count": 0,
        "settlement_before_cleanup": True, "settlement_event": settlement,
        "process_running_before_terminate": True, "terminate_requested": True,
        "requested_signal": "SIGTERM", "kill_requested": False,
        "wait_completed": True, "wait_returncode": returncode,
    }
    stdout = json.dumps(stock["last_agent_end"]) + "\n" + json.dumps(settlement) + "\n"
    stock = stock_terminal_evidence(CompletedProcess([], returncode, stdout, ""), rpc_cleanup=cleanup)
    return stock, bb


# -15: signal before stock registers its handler; 143: rpc-mode.js shutdown(143).
@pytest.mark.parametrize("returncode", [-15, 143])
def test_owned_sigterm_after_observed_native_terminal_state_is_recorded_not_hidden(returncode):
    stock, bb = _owned_sigterm_states(returncode)
    assert stock["returncode"] == stock["rpc_cleanup"]["wait_returncode"] == returncode
    assert compare_terminal_states(stock, bb, expected_provider_error=True) == []


@pytest.mark.parametrize("returncode", [1, 129, 137])
def test_other_exit_status_is_not_owned_sigterm_cleanup(returncode):
    stock, bb = _owned_sigterm_states(returncode)
    assert compare_terminal_states(stock, bb, expected_provider_error=True)


@pytest.mark.parametrize("field,value", [
    ("pid", 0), ("controller", "external"),
    ("settled", False), ("agent_ended", False), ("reader_error_count", 1),
    ("settlement_before_cleanup", False), ("process_running_before_terminate", False),
    ("terminate_requested", False), ("requested_signal", "SIGKILL"),
    ("kill_requested", True), ("wait_completed", False), ("wait_returncode", 0),
    ("settlement_event", {"type": "auto_compaction_end", "willRetry": True}),
])
def test_negative_exit_cannot_be_admitted_without_exact_owned_cleanup(field, value):
    stock, bb = _owned_sigterm_states()
    stock["rpc_cleanup"][field] = value
    assert compare_terminal_states(stock, bb, expected_provider_error=True)


@pytest.mark.parametrize("variant", ["sigkill", "unobserved_settlement", "missing_agent_end", "missing_native_terminal"])
def test_cleanup_receipt_cannot_replace_source_and_actor_terminal_evidence(variant):
    stock, bb = _owned_sigterm_states()
    if variant == "sigkill":
        stock["returncode"] = stock["rpc_cleanup"]["wait_returncode"] = -9
    elif variant == "unobserved_settlement":
        stock["settlement_event_is_last_stdout_event"] = False
    elif variant == "missing_agent_end":
        stock["last_agent_end"] = stock["last_assistant"] = None
    else:
        bb["last_assistant"] = None
    assert compare_terminal_states(stock, bb, expected_provider_error=True)
