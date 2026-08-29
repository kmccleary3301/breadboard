from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from breadboard_engine.conductor import execution as execution_module
from breadboard_engine.conductor import model_output
from breadboard_engine.conductor.execution import (
    ReplayToolOutputMismatchError,
    _force_failed_verification_final_answer,
    _force_failed_write_final_answer,
    _force_post_receipt_final_answer,
    _implementation_verification_receipt_missing,
    _is_completion_action_result,
    _latest_prompt_requires_implementation_write,
    _latest_prompt_requests_verification,
    _shell_command_write_targets,
)
from breadboard_engine.conductor_execution import (
    ReplayToolOutputMismatchError as compat_replay_error,
    _force_failed_verification_final_answer as compat_force_failed_verification,
    _force_failed_write_final_answer as compat_force_failed_write,
    _force_post_receipt_final_answer as compat_force_post_receipt,
    _implementation_verification_receipt_missing as compat_verification_missing,
    _is_completion_action_result as compat_completion_action,
    _latest_prompt_requires_implementation_write as compat_requires_write,
    _latest_prompt_requests_verification as compat_requests_verification,
    _shell_command_write_targets as compat_shell_targets,
)
from breadboard_engine.provider.runtime import ProviderMessage, ProviderResult, ProviderRuntimeContext, ProviderRuntimeError, ProviderToolCall
from breadboard_engine.state.session_state import SessionState


EXPECTED_EXPORTS = {
    "Any", "Callable", "CheckpointManager", "ConductorContext", "Dict", "HookResult", "List", "MarkdownLogger", "Optional", "Path", "ProviderMessage", "ProviderResult", "ProviderRuntimeContext", "ProviderRuntimeError", "ReplayToolOutputMismatchError", "SessionState", "SimpleNamespace", "ToolDefinition", "Tuple", "TurnContext", "_CLAUDE_BUDGET_LINE_RE", "_OPENCODE_ISO_TIMESTAMP_RE", "_async_result_retrieval_tool_for_activity", "_async_result_task_id_from_activity", "_auto_verify_smoke_command_from_prompt", "_build_post_receipt_final_message", "_build_read_only_observation_final_message", "_coerce_subprocess_text", "_command_tunnels_apply_patch", "_coordination_task_context", "_emit_tool_denial_primitives", "_ensure_tool_completion_final_message", "_extract_tool_result_text", "_failed_requested_write_attempts", "_force_failed_verification_final_answer", "_force_failed_write_final_answer", "_force_post_receipt_final_answer", "_force_read_only_observation_final_answer", "_implementation_prompt_candidates", "_implementation_receipt_missing", "_implementation_receipts_satisfied", "_implementation_task_anchor", "_implementation_verification_receipt_missing", "_implementation_write_guard_config", "_implementation_write_receipt_missing", "_inject_async_result_retrieval", "_is_allowed_async_result_followup", "_is_completion_action_result", "_is_internal_validation_prompt", "_latest_implementation_prompt", "_latest_prompt_forbidden_direct_commands", "_latest_prompt_requests_file_deletion", "_latest_prompt_requests_read_only_answer_after_observation", "_latest_prompt_requests_tool_stop_after_observation", "_latest_prompt_requests_verification", "_latest_prompt_requires_implementation_write", "_latest_requested_exact_shell_command", "_maybe_auto_verify_make_after_write_receipts", "_maybe_block_read_only_implementation_loop", "_maybe_force_post_write_auto_verification_closure", "_maybe_force_read_only_observation_closure", "_maybe_force_requested_shell_command_closure", "_missing_requested_write_targets", "_normalize_claude_system_reminders", "_normalize_codex_apply_patch_output", "_normalize_codex_shell_output", "_normalize_opencode_filetime_timestamps", "_normalize_replay_paths", "_normalize_write_target", "_observed_tool_calls_since_read_only_prompt", "_parsed_call_is_read_only_inspection", "_path_is_user_facing_write_target", "_post_receipt_final_reminder", "_post_receipt_final_targets", "_prompt_requires_implementation_write_text", "_record_validated_signal", "_reject_completion_without_implementation_write", "_replay_tool_output_compare_targets", "_requested_final_answer_terms", "_requested_verification_commands_satisfied", "_requested_write_matches", "_requested_write_targets", "_required_final_answer_marker", "_required_final_answer_reminder", "_run_subprocess_capture_with_group_timeout", "_shell_command_delete_targets", "_shell_command_is_read_only", "_shell_command_write_targets", "_strip_internal_prompt_blocks", "_successful_exact_shell_command_observation", "_successful_patch_result_paths", "_successful_test_commands", "_tool_call_delete_targets", "_tool_call_has_user_facing_write_target", "_tool_call_write_targets", "_write_payload_looks_placeholder", "_write_target_matches_requested", "_requested_write_matches", "_write_target_matches_requested", "_write_payload_looks_placeholder", "_tool_call_has_user_facing_write_target", "_tool_call_write_targets", "_tool_call_delete_targets", "_successful_test_commands", "_successful_patch_result_paths", "_successful_exact_shell_command_observation", "_write_target_matches_requested", "_write_payload_looks_placeholder", "_tool_call_has_user_facing_write_target", "_tool_call_write_targets", "_tool_call_delete_targets", "_shell_command_delete_targets", "_shell_command_write_targets", "_shell_command_is_read_only", "_path_is_user_facing_write_target", "_parsed_call_is_read_only_inspection", "_observed_tool_calls_since_read_only_prompt", "_normalize_write_target", "_requested_write_targets", "_requested_final_answer_terms", "_required_final_answer_marker", "_required_final_answer_reminder", "_is_allowed_async_result_followup", "_async_result_task_id_from_activity", "_async_result_retrieval_tool_for_activity", "_inject_async_result_retrieval", "_coerce_subprocess_text", "_run_subprocess_capture_with_group_timeout", "annotations", "apply_turn_guards", "assistant_is_progress_update", "build_completion_signal_proposal", "build_exec_func", "build_tool_completion_signal_proposal", "build_tool_execution_outcome_record", "build_tool_model_render_record", "build_turn_context", "classify_tool_terminal_state", "emit_turn_snapshot", "execute_agent_calls", "finalize_turn_context_snapshot", "handle_blocked_calls", "handle_native_tool_calls", "handle_text_tool_calls", "hydrate_turn_context_signals", "is_accepted_signal", "json", "latest_real_user_prompt", "legacy_message_view", "log_provider_message", "maybe_transition_plan_mode", "os", "process_model_output", "provider_adapter_manager", "provider_registry", "provider_router", "random", "re", "record_replay_tool_output_mismatches", "resolve_replay_todo_placeholders", "resolve_todo_placeholders", "retry_with_fallback", "session_requires_workspace_tool_usage", "shlex", "signal", "subprocess", "summarize_execution_results", "time", "uuid", "validate_signal_proposal",
}

PRIVATE_NAMES = (
    "_force_failed_write_final_answer",
    "_force_failed_verification_final_answer",
    "_force_post_receipt_final_answer",
    "_latest_prompt_requires_implementation_write",
    "_latest_prompt_requests_verification",
    "_shell_command_write_targets",
    "_is_completion_action_result",
    "_implementation_verification_receipt_missing",
)


def _session(prompt: str = "") -> SessionState:
    state = SessionState(workspace=".", image="test", config={})
    if prompt:
        state.add_message({"role": "user", "content": prompt})
    return state


def _logger() -> Any:
    events: list[tuple[str, Any]] = []
    return SimpleNamespace(
        events=events,
        log_assistant_message=lambda message: events.append(("assistant", message)),
        log_system_message=lambda message: events.append(("system", message)),
    )


def test_execution_export_set_is_exact() -> None:
    assert {name for name in dir(execution_module) if not name.startswith("__")} == EXPECTED_EXPORTS


def test_private_exports_are_identical_through_compat_wrapper() -> None:
    compat = __import__("breadboard_engine.conductor_execution", fromlist=["*"])
    for name in PRIVATE_NAMES:
        assert getattr(execution_module, name) is getattr(compat, name)
    assert compat.ReplayToolOutputMismatchError is ReplayToolOutputMismatchError
    assert compat_replay_error is ReplayToolOutputMismatchError
    assert compat_force_failed_verification is _force_failed_verification_final_answer
    assert compat_force_failed_write is _force_failed_write_final_answer
    assert compat_force_post_receipt is _force_post_receipt_final_answer
    assert compat_verification_missing is _implementation_verification_receipt_missing
    assert compat_completion_action is _is_completion_action_result
    assert compat_requires_write is _latest_prompt_requires_implementation_write
    assert compat_requests_verification is _latest_prompt_requests_verification
    assert compat_shell_targets is _shell_command_write_targets


def test_process_model_output_preserves_text_and_native_dispatch_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(model_output, "handle_text_tool_calls", lambda *args: events.append(("text", args[1].content)) or False)
    monkeypatch.setattr(model_output, "handle_native_tool_calls", lambda *args: events.append(("native", args[1].tool_calls[0].function.name)) or False)
    conductor = SimpleNamespace(config={})
    state = _session()
    text_message = ProviderMessage(role="assistant", content="describe", tool_calls=[], finish_reason="stop")
    native_message = ProviderMessage(role="assistant", content=None, tool_calls=[ProviderToolCall(id="c1", name="lookup", arguments="{}")], finish_reason="tool_calls")
    assert execution_module.process_model_output(conductor, text_message, object(), [], state, object(), _logger(), object(), False, "mock/model") is False
    assert execution_module.process_model_output(conductor, native_message, object(), [], state, object(), _logger(), object(), False, "mock/model") is False
    assert events == [("text", "describe"), ("native", "lookup")]


def test_receipt_outcomes_and_closure_guards_are_stable(tmp_path: Path) -> None:
    conductor = SimpleNamespace(config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}}, workspace=str(tmp_path))
    state = _session("Implement app.py and verify it with a smoke test")
    assert _latest_prompt_requires_implementation_write(state)
    assert _latest_prompt_requests_verification(state)
    assert execution_module._implementation_write_receipt_missing(conductor, state)
    assert _implementation_verification_receipt_missing(conductor, state) is False
    state.tool_usage_summary["successful_requested_write_targets"] = ["app.py"]
    state.tool_usage_summary["successful_user_facing_writes"] = 1
    assert execution_module._implementation_write_receipt_missing(conductor, state) is False
    assert _implementation_verification_receipt_missing(conductor, state)
    state.tool_usage_summary["successful_tests"] = 1
    state.turn_tool_usage[0] = {"tools": [{"success": True, "meta": {"is_test_command": True, "exit_code": 0, "command": "smoke_test.sh"}}]}
    assert _implementation_verification_receipt_missing(conductor, state) is False
    assert _shell_command_write_targets("printf ok > app.py") == ["app.py"]


def test_retry_fallback_sequence_and_provider_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    state = _session()
    state.set_provider_metadata("current_turn_index", 4)
    class Health:
        def record_failure(self, *args): events.append("health.failure")
        def record_success(self, *args): events.append("health.success")
    class Metrics:
        def add_fallback(self, **kwargs): events.append("metrics.fallback")
        def add_call(self, *args, **kwargs): events.append("metrics.call")
    class Primary:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")
        def invoke(self, **kwargs): events.append("primary.invoke"); raise ProviderRuntimeError("primary failed")
    class Fallback:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")
        def create_client(self, *args, **kwargs): events.append("fallback.client"); return object()
        def invoke(self, **kwargs): events.append("fallback.invoke"); return ProviderResult(messages=[ProviderMessage(role="assistant", content="ok", tool_calls=[])], raw_response={}, usage={}, metadata={})
    fallback = Fallback()
    class Router:
        def get_runtime_descriptor(self, model): return fallback.descriptor, model
        def create_client_config(self, model): return {"api_key": "test"}
        @contextmanager
        def execution_client_config(self, model, **_kwargs):
            config = self.create_client_config(model)
            try:
                yield config
            finally:
                config.clear()
    class Registry:
        def create_runtime(self, descriptor): return fallback
    conductor = SimpleNamespace(
        logger_v2=SimpleNamespace(run_dir=None), md_writer=SimpleNamespace(system=lambda text: text), route_health=Health(),
        provider_metrics=Metrics(), _update_health_metadata=lambda *_: None,
        _get_model_routing_preferences=lambda route: {"fallback_models": ["mock/fallback"]},
        _select_fallback_route=lambda *args, **kwargs: ("mock/fallback", {"selected": True}),
        _log_routing_event=lambda *args, **kwargs: events.append("routing.event"),
    )
    logger = _logger()
    monkeypatch.setattr(execution_module.time, "sleep", lambda _: events.append("sleep"))
    result = execution_module.retry_with_fallback(
        conductor, Primary(), object(), "mock/primary", [{"role": "user", "content": "hi"}], None,
        ProviderRuntimeContext(session_state=state, agent_config={}, stream=False), stream_responses=False,
        session_state=state, markdown_logger=logger, attempted=[], last_error=ProviderRuntimeError("initial"),
        provider_router_override=Router(), provider_registry_override=Registry(),
    )
    assert result is not None and result.messages[0].content == "ok"
    assert events[:4] == ["health.failure", "sleep", "primary.invoke", "health.failure"]
    assert "fallback.invoke" in events
    assert state.get_provider_metadata("fallback_route")["to"] == "mock/fallback"

    provider_message = ProviderMessage(role="assistant", content="hello", tool_calls=[], finish_reason="stop")
    session_events: list[Any] = []
    fake_state = SimpleNamespace(add_transcript_entry=lambda payload: session_events.append(("transcript", payload)))
    fake_conductor = SimpleNamespace(logger_v2=SimpleNamespace(run_dir=None))
    markdown = _logger()
    execution_module.log_provider_message(fake_conductor, provider_message, fake_state, markdown, False)
    assert session_events and session_events[0][0] == "transcript"
    assert markdown.events == [("assistant", "hello")]


def test_replay_mismatch_output_is_recorded_and_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _session()
    state.set_provider_metadata("replay_mode", True)
    guard_events: list[Any] = []
    state.record_guardrail_event = lambda name, payload: guard_events.append((name, payload))
    adapter = SimpleNamespace(create_tool_result_message=lambda call_id, name, out: {"content": "actual"})
    monkeypatch.setattr(execution_module.provider_router, "parse_model_id", lambda model: ("mock", "model"))
    monkeypatch.setattr(execution_module.provider_adapter_manager, "get_adapter", lambda provider: adapter)
    conductor = SimpleNamespace(config={"replay": {"compare_tool_outputs": True, "fail_on_tool_output_mismatch": True}}, workspace=".")
    parsed = SimpleNamespace(expected_output="expected", provider_name="shell_command", function="shell_command", call_id="c1")
    with pytest.raises(ReplayToolOutputMismatchError, match="Replay tool output mismatch") as exc_info:
        execution_module.record_replay_tool_output_mismatches(conductor, state, [(parsed, {"ok": True})], model="mock/model")
    assert "EXPECTED:" in str(exc_info.value) and "ACTUAL:" in str(exc_info.value)
    assert guard_events[0][0] == "mvi_tool_output_mismatch"


def test_conductor_compat_import_identity_and_no_facade_back_import() -> None:
    compat = __import__("breadboard_engine.conductor_execution", fromlist=["*"])
    for name in ("build_exec_func", "execute_agent_calls", "process_model_output", "retry_with_fallback"):
        assert getattr(compat, name) is getattr(execution_module, name)
    root = Path(execution_module.__file__).parent
    for path in root.glob("execution_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = ast.unparse(node)
                assert "conductor_execution" not in imported, f"facade import cycle in {path}: {imported}"
