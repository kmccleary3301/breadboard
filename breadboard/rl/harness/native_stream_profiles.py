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

from breadboard_engine.compilation.provider_response import PI_RESPONSE_CONSUMER_ID
from breadboard.rl.harness.runners import openclaw_semantics, pi_semantics

@dataclass(frozen=True, slots=True)
class NativeStreamProfile:
    """One admitted native-stream source profile."""

    consumer_id: str
    target_id: str
    target_version: int
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
    state_factory: Callable[[str, str, Mapping[str, Any]], Any]


def _pi_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    del bootstrap
    return pi_semantics.PiSemanticsState(task=task, system_prompt=system_prompt)


def _openclaw_state(task: str, system_prompt: str, bootstrap: Mapping[str, Any]) -> Any:
    return openclaw_semantics.OpenClawSemanticsState(task, system_prompt, bootstrap)

NATIVE_STREAM_PROFILES: Mapping[str, NativeStreamProfile] = MappingProxyType({
    PI_RESPONSE_CONSUMER_ID: NativeStreamProfile(
        consumer_id=PI_RESPONSE_CONSUMER_ID,
        target_id="pi@0.73.1",
        target_version=3,
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
    openclaw_semantics.OPENCLAW_CONSUMER_ID: NativeStreamProfile(
        consumer_id=openclaw_semantics.OPENCLAW_CONSUMER_ID,
        target_id="openclaw@2026.9.4",
        target_version=4,
        phase_schema_version="bb.openclaw-native.v1",
        tool_order=openclaw_semantics.OpenClawSemanticsState.tool_order,
        max_turns=8,
        action_timeout_ms=35_000,
        episode_timeout_seconds=120,
        ack_policy="after_history_commit",
        incomplete_stop_reasons=frozenset({"error", "aborted", "length"}),
        state_module=openclaw_semantics,
        state_factory=_openclaw_state,
    ),
})


__all__ = ["NATIVE_STREAM_PROFILES", "NativeStreamProfile"]
