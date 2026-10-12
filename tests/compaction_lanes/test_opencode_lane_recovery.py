"""OpenCode lane recovery behavioral regressions."""
from __future__ import annotations
import pytest
from breadboard_engine.compaction_lanes.opencode_scenarios import production_settings, scenario_turns
from .opencode_test_support import _arm_native_overflow, _finish_pending_recovery, _pending_recovery_setup

def test_pending_error_trigger_without_pending_state_does_nothing():
    controller, state, runtime = _pending_recovery_setup()
    state.add_message({"role": "assistant", "content": "Inspection complete."})
    assert _finish_pending_recovery(controller, state, runtime) is False
    assert runtime.summary_requests == []

def test_pending_error_trigger_fires_each_checkpoint_until_attempts_run_out():
    controller, state, runtime = _pending_recovery_setup()
    _arm_native_overflow(controller, state, runtime)
    state.add_message({"role": "assistant", "content": "Inspection complete."})
    assert _finish_pending_recovery(controller, state, runtime) is True
    assert len(runtime.summary_requests) == 2
    assert state.compaction_state.records[-1].reason == "threshold"
    state.add_message({"role": "assistant", "content": "Inspection complete after deferred recovery."})
    assert _finish_pending_recovery(controller, state, runtime) is True
    assert len(runtime.summary_requests) == 3
    state.add_message({"role": "assistant", "content": "Inspection finally complete."})
    assert _finish_pending_recovery(controller, state, runtime) is False
    assert len(runtime.summary_requests) == 3

def test_pending_error_trigger_summary_last_skip_clears_pending_state():
    controller, state, runtime = _pending_recovery_setup()
    _arm_native_overflow(controller, state, runtime)
    assert _finish_pending_recovery(controller, state, runtime) is False
    state.add_message({"role": "assistant", "content": "A later answer."})
    assert _finish_pending_recovery(controller, state, runtime) is False
    assert len(runtime.summary_requests) == 1

@pytest.mark.parametrize("replacement", [
    {"kind": "pending_error", "phase": "assistant_step_end"},
    {"kind": "pending_error", "phase": "user_turn_end",
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error", "max_attempts": 2},
    {"kind": "pending_error", "phase": "assistant_step_end", "error_parser": "os:system", "max_attempts": 2},
    {"kind": "pending_error", "phase": "assistant_step_end", "max_attempts": 2,
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error", "unknown": True},
    {"kind": "pending_error", "phase": "assistant_step_end", "max_attempts": 0,
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error"},
    {"kind": "pending_error", "phase": "assistant_step_end", "max_attempts": None,
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error"},
    {"kind": "pending_error", "phase": "assistant_step_end",
     "error_parser": "breadboard_engine.compaction.oh_my_opencode:parse_token_limit_error"},
])
def test_pending_error_trigger_rejects_invalid_preset_members(replacement):
    from breadboard_engine.compaction.params import PresetError
    from breadboard_engine.compaction.presets import build_recipe, load_preset_document
    controller, _, _ = _pending_recovery_setup()
    document = load_preset_document("oh-my-opencode@3.10.0")
    document["triggers"][1] = replacement
    with pytest.raises(PresetError):
        build_recipe(controller.config, document)

@pytest.mark.parametrize("error,expected", [
    ("prompt is too long: 74000 tokens > 64000 maximum", {"overflow_tokens": 74000, "overflow_limit": 64000}),
    ("max context 64000 but 74000: context length exceeded", {"overflow_tokens": 74000, "overflow_limit": 64000}),
    ("thinking must be the first block: context length exceeded", None),
    ("unrelated provider failure", None),
    ({"data": {"responseBody": '{"error":{"message":"prompt is too long: 74000 tokens > 64000 maximum"}}'}},
     {"overflow_tokens": 74000, "overflow_limit": 64000}),
])
def test_plugin_pending_error_parser_matches_source_cases(error, expected):
    from breadboard_engine.compaction.oh_my_opencode import parse_token_limit_error
    assert parse_token_limit_error(error) == expected

@pytest.mark.parametrize("preset", ["opencode@1.2.17", "oh-my-opencode@3.10.0"])
def test_opencode_native_classifier_keeps_plugin_only_wording_out_of_native_overflow(preset):
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compaction.overflow import is_context_overflow
    controller = CompactionController({"compaction": {"enabled": True, "preset": preset}})
    message = "too many tokens: 74000 tokens > 64000 maximum"
    assert is_context_overflow(message) is True
    assert controller.recipe.error_classifier(message) is False

@pytest.mark.parametrize("message", [
    "prompt is too long: 74000 tokens > 64000 maximum",
    "input is too long for requested model", "exceeds the context window",
    "input token count 74000 exceeds the maximum", "maximum prompt length is 64000",
    "reduce the length of the messages", "maximum context length is 64000 tokens",
    "exceeds the limit of 64000", "exceeds the available context size",
    "greater than the context length", "context window exceeds limit",
    "exceeded model token limit", "context_length_exceeded", "request entity too large",
    "400 (no body)", "413 status code (no body)",
])
def test_opencode_native_classifier_matches_pinned_regular_patterns(message):
    from breadboard_engine.compaction.opencode import is_context_overflow
    assert is_context_overflow(message)

def test_plugin_only_provider_error_recovers_at_pending_checkpoint_not_native_overflow():
    from breadboard_engine.compaction.overflow import provider_overflow_details
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
    controller, state, runtime = _pending_recovery_setup()
    class SDKError(Exception):
        status_code = 400
        body = {"message": "too many tokens: 74000 tokens > 64000 maximum"}
    try:
        raise SDKError("too many tokens: 74000 tokens > 64000 maximum")
    except SDKError as sdk_error:
        error = ProviderRuntimeError("provider operation failed", details=provider_overflow_details(sdk_error))
        error.__context__ = sdk_error
    assert controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime, client=object(), model="gpt-test")
    assert len(runtime.summary_requests) == 1
    assert all(record.reason == "threshold" for record in state.compaction_state.records)
    assert controller.passes_this_turn(1) == 0
    state.add_message({"role": "assistant", "content": "Recovered."})
    assert _finish_pending_recovery(controller, state, runtime)
    state.add_message({"role": "assistant", "content": "Finally recovered."})
    assert not _finish_pending_recovery(controller, state, runtime)
    assert len(runtime.summary_requests) == 2

@pytest.mark.parametrize("value", [None, False, 42, "os:system",
    "breadboard_engine.compaction..opencode:is_context_overflow",
    "breadboard_engine.compaction.opencode:missing",
    "breadboard_engine.compaction.opencode:_PATTERNS",
])
def test_overflow_error_classifier_is_strictly_admitted(value):
    from breadboard_engine.compaction.params import PresetError
    from breadboard_engine.compaction.presets import build_recipe, load_preset_document
    controller, _, _ = _pending_recovery_setup()
    document = load_preset_document("oh-my-opencode@3.10.0")
    document["overflow"]["error_classifier"] = value
    with pytest.raises(PresetError):
        build_recipe(controller.config, document)

def test_absent_overflow_error_classifier_preserves_generic_default():
    from breadboard_engine.compaction.presets import build_recipe, load_preset_document
    controller, _, _ = _pending_recovery_setup()
    document = load_preset_document("oh-my-opencode@3.10.0")
    del document["overflow"]["error_classifier"]
    assert build_recipe(controller.config, document).error_classifier is None

def test_plugin_only_scenario_uses_exact_error_and_production_attempt_budget():
    settings = production_settings("oh-my-opencode")
    turns = scenario_turns("plugin_error", settings)
    responses = turns[0]["responses"]
    assert responses[0] == {"type": "error", "status_code": 400, "error": "invalid_request_error",
                            "message": "too many tokens: 74000 tokens > 64000 maximum"}
    assert len(responses) == 1 + 2 * settings["deferred_recovery_attempts"]
    assert len(turns) == 1

def test_original_provider_wording_cannot_be_replaced_by_generic_overflow_code():
    from breadboard_engine.compaction.oh_my_opencode import parse_token_limit_error
    from breadboard_engine.compaction.overflow import provider_overflow_details
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
    controller, state, runtime = _pending_recovery_setup()
    class SDKError(Exception):
        status_code = 400
        body = {"message": "requested tokens exceed n_ctx"}
    sdk_error = SDKError("requested tokens exceed n_ctx")
    details = provider_overflow_details(sdk_error)
    assert details["code"] == "context_length_exceeded"
    error = ProviderRuntimeError("provider operation failed", details=details)
    error.__cause__ = sdk_error
    assert parse_token_limit_error(error) is None
    assert controller.recipe.error_classifier(error) is False
    assert not controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime, client=object(), model="gpt-test")
    assert runtime.summary_requests == []

@pytest.mark.parametrize("payload,expected", [
    (b'{"error":{"message":"too many tokens: 74000 tokens > 64000 maximum\xff"}}',
     {"overflow_tokens": 74000, "overflow_limit": 64000}),
    (b'{"error":{"message":"unrelated provider failure\xff"}}', None),
])
def test_plugin_error_response_body_uses_stock_utf8_replacement(payload, expected):
    from types import SimpleNamespace
    from breadboard_engine.compaction.oh_my_opencode import parse_token_limit_error
    assert bytes([255]) in payload
    error = RuntimeError("Public provider operation failed")
    error.response = SimpleNamespace(content=payload)
    assert parse_token_limit_error(error) == expected
