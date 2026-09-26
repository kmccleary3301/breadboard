"""Admitted source profiles for the Conductor's one native-stream loop.

Each profile contributes a phase schema, a Python semantics state, and a
supplier-native worker behind the admitted lease. The Conductor owns every
model request, history commit, and termination; profiles only declare how
their source state is constructed and which native stops are incomplete.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import Any, Literal

from breadboard_engine.compilation.provider_response import (
    HERMES_RESPONSE_CONSUMER_ID,
    OMP_16_2_13_RESPONSE_CONSUMER_ID,
    PI_0_57_1_RESPONSE_CONSUMER_ID,
    PI_RESPONSE_CONSUMER_ID,
)
from breadboard.rl.harness import hermes_worker
from breadboard.rl.harness.runners import (
    omp_16_2_13_semantics,
    omp_semantics,
    openclaw_semantics,
    pi_0_57_1_semantics,
    pi_semantics,
)


@dataclass(frozen=True, slots=True)
class NativeStreamProfile:
    """One admitted native-stream source profile."""

    consumer_id: str
    target_id: str
    target_version: int
    api_variant: Literal["responses", "chat", "chat_completions"]
    phase_schema_version: str
    tool_order: tuple[str, ...]
    max_turns: int
    action_timeout_ms: int
    episode_timeout_seconds: int
    ack_policy: Literal["none", "after_history_commit"]
    incomplete_stop_reasons: frozenset[str]
    runtime_input_names: tuple[str, ...]
    package_subpath: str
    state_module: ModuleType
    # (task, system_prompt, bootstrap) -> profile semantics state.
    state_factory: Callable[[str, str, Mapping[str, Any]], Any] | None
    phase_mode: Literal["streaming", "checkpointed"] = "streaming"
    trace_schema_version: str = ""
    trace_profile_name: str = ""
    trace_controls: Mapping[str, Any] | None = None
    journal_byte_limit: bool = False
    sealed_initialize_fields: tuple[str, ...] = ()
    checkpoint_state_fields: tuple[str, ...] = ()
    phase_watchdog_seconds: int = 40
    provider_timeout_seconds: int = 45
    limit_stop_reasons: frozenset[str] = frozenset()
    # A begun stream ending without a finish_reason reaches the semantics as a
    # typed termination instead of failing in the decoder.
    accepts_truncated_stream: bool = False
    classify_result_phase: str | None = None
    finalize_result_phase: str | None = None
    # The source ends its episode on a provider's refusal of a sent request
    # (no retry) and still classifies and finalizes it; the state commits the
    # failure through ``commit_provider_failure``.
    provider_failure_terminates: bool = False
    # Worker phase that projects the source's own assistant message for an
    # HTTP status failure; None keeps the Conductor's ``str(failure)`` commit.
    provider_failure_phase: str | None = None
    # Worker phase that parses the response's tool-call argument strings; the
    # parsed list is handed to ``state.prepare_response(native, parsed)``.
    parse_arguments_phase: str | None = None


def _pi_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    model_config = bootstrap.get("model_config")
    if not isinstance(model_config, Mapping):
        raise ValueError("native stream bootstrap missing model_config")
    model_id = model_config.get("id", model_config.get("model"))
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("native stream bootstrap model_config missing model id")
    provider = model_config.get("provider")
    if not isinstance(provider, str) or not provider:
        raise ValueError("native stream bootstrap model_config missing provider")
    api = model_config.get("api", "openai-completions")
    if not isinstance(api, str) or not api:
        raise ValueError("native stream bootstrap model_config missing api")
    return pi_semantics.PiSemanticsState(
        task=task,
        system_prompt=system_prompt,
        model_id=model_id,
        provider=provider,
        api=api,
    )


def _pi_0_57_1_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    for name in ("model_config", "current_date_time"):
        if name not in bootstrap:
            raise ValueError(f"native stream bootstrap missing {name}")
    model_config = bootstrap["model_config"]
    if not isinstance(model_config, Mapping) or not {"id", "provider", "api", "cost"} <= set(model_config):
        raise ValueError("native stream bootstrap model_config is malformed")
    return pi_0_57_1_semantics.Pi0571SemanticsState(
        task=task,
        system_prompt=system_prompt,
        model_id=model_config["id"],
        provider=model_config["provider"],
        api=model_config["api"],
        cost=model_config["cost"],
        current_date_time=bootstrap["current_date_time"],
    )


def _omp_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    return omp_semantics.OMPSemanticsState(
        task=task,
        system_prompt=system_prompt,
        tool_schemas=bootstrap.get("tool_schemas", ()),
        worker=bootstrap.get("worker"),
        capability_denials=bootstrap.get("capability_denials"),
        cwd=bootstrap.get("cwd"),
    )


def _openclaw_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    return openclaw_semantics.OpenClawSemanticsState(task, system_prompt, bootstrap)

def _omp_16_2_13_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    for name in ("model_config", "runtime_inputs", "length_aborted_message"):
        if name not in bootstrap:
            raise ValueError(f"native stream bootstrap missing {name}")
    model_config = bootstrap["model_config"]
    if not isinstance(model_config, Mapping) or not {"id", "provider", "api", "cost"} <= set(model_config):
        raise ValueError("native stream bootstrap model_config is malformed")
    runtime_inputs = bootstrap["runtime_inputs"]
    if not isinstance(runtime_inputs, Mapping) or not {"cwd", "home", "current_date", "package_dir"} <= set(runtime_inputs):
        raise ValueError("native stream bootstrap runtime_inputs is malformed")
    if "current_date_time" not in bootstrap or not isinstance(bootstrap["current_date_time"], str) or not bootstrap["current_date_time"]:
        raise ValueError("native stream bootstrap missing or invalid 'current_date_time'")
    current_date_time = bootstrap["current_date_time"]
    return omp_16_2_13_semantics.Omp16213SemanticsState(
        task=task,
        system_prompt=system_prompt,
        model_id=model_config["id"],
        provider=model_config["provider"],
        api=model_config["api"],
        cost=model_config["cost"],
        current_date_time=current_date_time,
        length_aborted_message=bootstrap["length_aborted_message"],
        runtime_inputs=runtime_inputs,
    )


NATIVE_STREAM_PROFILES: Mapping[str, NativeStreamProfile] = MappingProxyType({
    PI_RESPONSE_CONSUMER_ID: NativeStreamProfile(
        consumer_id=PI_RESPONSE_CONSUMER_ID,
        target_id="pi@0.73.1",
        target_version=3,
        api_variant="responses",
        phase_schema_version="bb.pi-native.v1",
        tool_order=pi_semantics.TOOL_NAMES,
        max_turns=8,
        action_timeout_ms=35_000,
        episode_timeout_seconds=120,
        ack_policy="none",
        incomplete_stop_reasons=frozenset({"error", "aborted", "length"}),
        runtime_input_names=("cwd", "home", "current_date", "package_dir"),
        package_subpath="node_modules/@mariozechner/pi-coding-agent",
        state_module=pi_semantics,
        state_factory=_pi_state,
    ),
    PI_0_57_1_RESPONSE_CONSUMER_ID: NativeStreamProfile(
        consumer_id=PI_0_57_1_RESPONSE_CONSUMER_ID,
        target_id="pi-r3@0.57.1",
        target_version=3,
        api_variant="chat_completions",
        phase_schema_version="bb.pi-native.v0.57.1",
        tool_order=pi_0_57_1_semantics.TOOL_NAMES,
        max_turns=8,
        action_timeout_ms=35_000,
        episode_timeout_seconds=120,
        ack_policy="none",
        incomplete_stop_reasons=frozenset({"error", "aborted", "length"}),
        runtime_input_names=("cwd", "home", "package_dir"),
        package_subpath="node_modules/@mariozechner/pi-coding-agent",
        state_module=pi_0_57_1_semantics,
        state_factory=_pi_0_57_1_state,
        # Pinned openai-completions.js:45 keeps "stop" for a stream without a
        # finish_reason; BB rejects it (divergence truncated_stream_rejected).
        accepts_truncated_stream=False,
        provider_failure_terminates=True,
        provider_failure_phase="project_provider_failure",
        parse_arguments_phase="parse_streaming_json_batch",
    ),
    HERMES_RESPONSE_CONSUMER_ID: NativeStreamProfile(
        consumer_id=HERMES_RESPONSE_CONSUMER_ID,
        target_id="hermes-agent@2026.9.11",
        target_version=3,
        api_variant="chat",
        phase_schema_version="bb.hermes-native.v1",
        tool_order=(
            "patch", "read_file", "search_files", "skill_view",
            "skills_list", "terminal", "write_file",
        ),
        max_turns=8,
        action_timeout_ms=40_000,
        episode_timeout_seconds=120,
        ack_policy="after_history_commit",
        incomplete_stop_reasons=frozenset({"error", "stopped"}),
        runtime_input_names=(),
        package_subpath="",
        state_module=hermes_worker,
        state_factory=None,
        phase_mode="checkpointed",
        trace_schema_version="bb.e4.hermes-agent-trace.v1",
        trace_profile_name="hermes",
        trace_controls=MappingProxyType({
            "api_mode": "chat_completions", "streaming": False,
            "max_iterations": 8, "max_tokens": 2048, "http_attempts": None,
            "provider_deadline": 45, "provider_timeout": 45,
            "native_deadline": 35, "tool_deadline": 35,
            "watchdog_deadline": 40, "watchdog": 40,
            "terminal_deadline": 30, "terminal_timeout": 30,
            "retry": True, "api_max_retries": 1, "fallback": False,
            "advertised_tools": None,
        }),
        journal_byte_limit=True,
        sealed_initialize_fields=("schema_overlay",),
        checkpoint_state_fields=(
            "source_exit", "public_stop", "source_runtime", "source_error",
            "phase", "native_counters", "proposal", "segment_index",
            "action_index", "source_result_metadata", "resource_facts",
        ),
        limit_stop_reasons=frozenset({"request_cap"}),
    ),
    omp_semantics.CONSUMER_ID: NativeStreamProfile(
        consumer_id=omp_semantics.CONSUMER_ID,
        target_id="oh-my-pi@18.1.17",
        target_version=3,
        api_variant="chat_completions",
        phase_schema_version=omp_semantics.PHASE_SCHEMA_VERSION,
        tool_order=omp_semantics.ALLOWED_TOOLS,
        max_turns=8,
        action_timeout_ms=40_000,
        episode_timeout_seconds=120,
        ack_policy="none",
        incomplete_stop_reasons=frozenset({"error", "aborted"}),
        runtime_input_names=("cwd", "home", "current_date", "package_dir"),
        package_subpath="node_modules/@oh-my-pi/pi-coding-agent",
        state_module=omp_semantics,
        state_factory=_omp_state,
        accepts_truncated_stream=True,
        sealed_initialize_fields=("route_classifier",),
    ),
    openclaw_semantics.OPENCLAW_CONSUMER_ID: NativeStreamProfile(
        consumer_id=openclaw_semantics.OPENCLAW_CONSUMER_ID,
        target_id="openclaw@2026.9.4",
        target_version=3,
        api_variant="responses",
        phase_schema_version="bb.openclaw-native.v1",
        tool_order=openclaw_semantics.OpenClawSemanticsState.tool_order,
        max_turns=8,
        action_timeout_ms=35_000,
        episode_timeout_seconds=120,
        ack_policy="after_history_commit",
        incomplete_stop_reasons=frozenset({"error", "aborted", "length"}),
        runtime_input_names=("cwd", "home", "current_date", "message_timestamp_ms", "package_dir", "session_id"),
        package_subpath=".",
        state_module=openclaw_semantics,
        state_factory=_openclaw_state,
        classify_result_phase="classify_result",
        finalize_result_phase="finalize_command_result",
        provider_failure_terminates=True,
    ),
    OMP_16_2_13_RESPONSE_CONSUMER_ID: NativeStreamProfile(
        consumer_id=OMP_16_2_13_RESPONSE_CONSUMER_ID,
        target_id=omp_16_2_13_semantics.TARGET_ID,  # "oh-my-pi-r2@16.2.13"
        target_version=3,  # policy_provider.py:304,320 deferred target version 3
        api_variant="chat_completions",  # openai-completions.ts:350
        phase_schema_version=omp_16_2_13_semantics.PHASE_SCHEMA_VERSION,  # "bb.omp-native.v16.2.13"
        tool_order=omp_16_2_13_semantics.TOOL_NAMES,  # main.ts:908-912, sdk.ts:1778-1782 (site table row 12-13)
        max_turns=8,  # scenario.json:3, kit receiver request cap (site table row 29)
        action_timeout_ms=40_000,  # harness.yaml:48 single_tool_wall_seconds: 40
        episode_timeout_seconds=120,  # harness.yaml:47 episode_wall_seconds: 120
        ack_policy="none",  # 16.2.13 framed worker requires no delivery ack
        incomplete_stop_reasons=frozenset({"error", "aborted"}),  # agent-loop.ts:1015-1017 continues on length (site table row 20)
        runtime_input_names=("cwd", "home", "current_date", "package_dir"),  # system-prompt.ts:486-773 (site table row 15)
        package_subpath="node_modules/@oh-my-pi/pi-coding-agent",  # dist/cli.js:1 (site table row 1)
        state_module=omp_16_2_13_semantics,
        state_factory=_omp_16_2_13_state,
        accepts_truncated_stream=True,  # openai-completions.ts:239-250, agent-loop.ts:1530-1555 (site table row 21, o10)
        provider_failure_terminates=True,  # settings.ts:210-250, agent-session.ts:9800 (site table row 22, o5)
        provider_failure_phase="project_provider_failure",  # pinned assistant error message projection
        parse_arguments_phase="parse_streaming_json_batch",  # pinned stream argument parsing
    ),
})


__all__ = ["NATIVE_STREAM_PROFILES", "NativeStreamProfile"]
