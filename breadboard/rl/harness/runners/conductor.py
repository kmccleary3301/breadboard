from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from decimal import Decimal
import json
import math
import re
import time
from typing import Any, Protocol, runtime_checkable

from breadboard_engine.compilation.contracts import (
    bytes_sha256,
    canonical_sha256,
)
from breadboard_engine.compilation.provider_response import (
    MINI_RESPONSE_CONSUMER_ID,
    OPENHANDS_RESPONSE_CONSUMER_ID,
    NativeResponsePolicy,
)
from breadboard.rl.harness.contracts import PolicyCapabilityObservation
from breadboard.rl.harness.runner_identity import measure_module_artifact
from breadboard.rl.harness import native_stream_consumers, native_stream_profiles
from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
from breadboard.rl.harness.runners import mini_semantics
from breadboard.rl.harness.runners.base import (
    ConductorToolPort,
    CompiledPolicyRuntimeClientPort,
    NativeStreamPolicyRuntimeClientPort,
    MiniTemplateFramePort,
    NativeHTTPPolicyRuntimeClientPort,
    NativeSourceSessionPort,
    NativeRuntimeInputPort,
    NativeWorkspaceEffectsPort,
    FrozenJsonObject,
    JsonSnapshotError,
    PolicyRequestEvent,
    PolicyResponseEvent,
    PolicyRuntimeBindingPort,
    PolicyRuntimeClientPort,
    PolicyRuntimeInvokeRequest,
    PolicyRuntimeInvokeResult,
    PolicyRuntimeRequestEvent,
    PolicyRuntimeResponseEvent,
    RunnerAdapterDescriptor,
    RunnerCancellation,
    RunnerCancellationObservedEvent,
    RunnerCancellationProbe,
    RunnerCancellationRequestedEvent,
    RunnerCancelled,
    MiniProviderFailure,
    RunnerCloseResult,
    RunnerDependencyError,
    RunnerError,
    RunnerErrorEvent,
    RunnerEvent,
    RunnerEventSink,
    RunnerEventSinkError,
    RunnerOpenRequest,
    RunnerPlanError,
    RunnerPolicyBindingError,
    RunnerProtocolError,
    RunnerRequestError,
    RunnerResult,
    RunnerSession,
    RunnerStateError,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerToolBinding,
    RunnerTurn,
    ToolCallEvent,
    ToolObservationEvent,
    SourceHistoryCommitEvent,
    SourceEventCommitEvent,
    freeze_json_object,
    freeze_json_object_with_size,
    thaw_json,
)


_EVENT_SINK_SESSION: ContextVar[object | None] = ContextVar(
    "conductor_runner_event_sink_session", default=None
)




_CONDUCTOR_MODULE_IDENTITY = measure_module_artifact(__file__)
_MINI_MODULE_IDENTITY = measure_module_artifact(mini_semantics.__file__)
_STREAM_MODULE_IDENTITY = measure_module_artifact(native_stream_consumers.__file__)
_PROFILE_MODULE_IDENTITIES = (
    measure_module_artifact(native_stream_profiles.__file__),
    *(
        measure_module_artifact(profile.state_module.__file__)
        for profile in NATIVE_STREAM_PROFILES.values()
    ),
)
CONDUCTOR_IMPLEMENTATION_DIGEST = canonical_sha256({
    "conductor": _CONDUCTOR_MODULE_IDENTITY.digest,
    "mini_semantics": _MINI_MODULE_IDENTITY.digest,
    "native_stream_consumers": _STREAM_MODULE_IDENTITY.digest,
    "native_stream_profiles": [identity.digest for identity in _PROFILE_MODULE_IDENTITIES],
})


CONDUCTOR_ADAPTER_ID = "breadboard.conductor.v1"
CONDUCTOR_RUNTIME_ABI = "breadboard.conductor.v1"
POLICY_RUNTIME_BINDING_SCHEMA_VERSION = "bb.rl.policy-runtime-binding.v1"


@dataclass(frozen=True, slots=True)
class NativeCleanupOutcome:
    attempted: bool
    all_dead: bool | None
    error_code: str | None
    binding_close_error_code: str | None

@dataclass(frozen=True, slots=True)
class ConductorRunRequest:
    task_input: FrozenJsonObject
    context: FrozenJsonObject

    def __init__(
        self,
        task_input: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            frozen_input = freeze_json_object(task_input, field_name="task input")
            frozen_context = freeze_json_object(
                {} if context is None else context, field_name="task context"
            )
        except (JsonSnapshotError, TypeError) as exc:
            error = RunnerRequestError(
                "conductor request must contain only closed JSON objects",
                code="request_authority_invalid",
            )
            error.__cause__ = exc
            raise error
        object.__setattr__(self, "task_input", frozen_input)
        object.__setattr__(self, "context", frozen_context)


class PolicyRuntimeBinding:
    __slots__ = (
        "_episode_id",
        "_effective_plan_digest",
        "_binding_digest",
        "_observation",
        "_observation_digest",
        "_policy_slot_ids",
        "_client",
        "_lock",
        "_state",
        "_first_request_digest",
        "_cancel_reason",
        "_cancel_task",
        "_close_task",
        "_active_task",
        "_generated_turn",
        "_source_model_config",
        "_source_consumer_id",
    )

    def __init__(
        self,
        request: RunnerOpenRequest,
        client: PolicyRuntimeClientPort,
    ) -> None:
        if type(request) is not RunnerOpenRequest:
            raise TypeError("request must be an exact RunnerOpenRequest")
        try:
            observed = client.observe()
        except (asyncio.CancelledError, Exception) as exc:
            error = RunnerPolicyBindingError(
                "policy capability observation could not be obtained",
                code="policy_observation_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
            error.__cause__ = exc
            raise error
        if type(observed) is not PolicyCapabilityObservation:
            raise RunnerPolicyBindingError(
                "policy client returned an invalid capability observation",
                code="policy_observation_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        observation = PolicyCapabilityObservation.model_validate(
            observed.model_dump(mode="python")
        )
        observation_digest = observation.canonical_digest()
        plan = request.effective_plan
        if (
            observation_digest != plan.policy_capability_observation_digest
            or observation.capability_digest != plan.policy_capability_digest
        ):
            raise RunnerPolicyBindingError(
                "policy capability observation does not match the effective plan",
                code="policy_observation_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        routes = tuple(
            route
            for route in plan.effective_capabilities.routes
            if route.route_id == observation.route_id
        )
        secrets = tuple(
            secret
            for secret in plan.effective_capabilities.secret_handles
            if secret.handle_id == observation.credential_handle_id
        )
        if (
            len(routes) != 1
            or routes[0].route_revision_digest
            != observation.route_revision_digest
            or routes[0].protocol_abi != observation.protocol_abi
            or routes[0].credential_handle_id
            != observation.credential_handle_id
            or len(secrets) != 1
            or secrets[0].handle_version_digest
            != observation.credential_handle_version_digest
            or secrets[0].scope_digest != observation.subject_scope_digest
            or observation.revocation.scope_digest
            != observation.subject_scope_digest
            or plan.revocation.scope_digest != observation.subject_scope_digest
        ):
            raise RunnerPolicyBindingError(
                "policy runtime authority does not match the effective plan",
                code="policy_observation_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        slot_ids: list[str] = []
        for grant in plan.policy_slots:
            if (
                grant.route_id != observation.route_id
                or grant.protocol_abi != observation.protocol_abi
                or grant.secret_handle_id != observation.credential_handle_id
                or grant.model_digest != observation.model_digest
                or grant.tokenizer_digest != observation.tokenizer_digest
                or grant.checkpoint_digest != observation.checkpoint_digest
                or grant.required_policy_capabilities_digest
                != observation.capability_digest
            ):
                raise RunnerPolicyBindingError(
                    "policy slot grant does not match the observed runtime",
                    code="policy_slot_mismatch",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
            slot_ids.append(grant.slot_id)
        ordered_slot_ids = tuple(sorted(slot_ids))
        if not ordered_slot_ids or len(set(ordered_slot_ids)) != len(ordered_slot_ids):
            raise RunnerPolicyBindingError(
                "effective plan policy slots are invalid",
                code="policy_slot_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        self._episode_id = request.episode_id
        self._effective_plan_digest = request.effective_plan_digest
        self._observation = observation
        self._observation_digest = observation_digest
        self._policy_slot_ids = ordered_slot_ids
        self._binding_digest = canonical_sha256(
            {
                "schema_version": POLICY_RUNTIME_BINDING_SCHEMA_VERSION,
                "episode_id": request.episode_id,
                "effective_plan_digest": request.effective_plan_digest,
                "policy_capability_observation_digest": observation_digest,
                "policy_slot_ids": list(ordered_slot_ids),
            }
        )
        self._client = client
        self._lock = asyncio.Lock()
        self._state = "ready"
        self._first_request_digest: str | None = None
        self._cancel_reason: str | None = None
        self._cancel_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._active_task: asyncio.Task[PolicyRuntimeInvokeResult] | None = None
        self._generated_turn = 0
        self._source_model_config: FrozenJsonObject | None = None
        self._source_consumer_id: str | None = None
        metadata = plan.effective_semantics.get("metadata")
        target = metadata.get("e4_target") if isinstance(metadata, Mapping) else None
        if isinstance(target, Mapping) and (
            target.get("renderer_id") in {MINI_RESPONSE_CONSUMER_ID, OPENHANDS_RESPONSE_CONSUMER_ID}
            or target.get("renderer_id") in NATIVE_STREAM_PROFILES
        ):
            if not isinstance(client, CompiledPolicyRuntimeClientPort):
                raise RunnerPolicyBindingError(
                    "source-native target requires a compiled native provider client",
                    code="native_response_consumer_mismatch",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
            self._source_model_config = freeze_json_object(
                client.bind_compiled_plan(plan), field_name="source model configuration"
            )
            self._source_consumer_id = target["renderer_id"]

    @property
    def source_model_config(self) -> FrozenJsonObject | None:
        return self._source_model_config

    def bind_native_tools(self, tools: tuple[Mapping[str, Any], ...]) -> None:
        if (
            self._source_consumer_id != OPENHANDS_RESPONSE_CONSUMER_ID
            or self._state != "claimed"
            or not isinstance(self._client, NativeHTTPPolicyRuntimeClientPort)
        ):
            raise RunnerPolicyBindingError(
                "native tools require an active compiled OpenHands binding",
                code="native_response_binding_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        self._client.bind_native_tools(tools)

    def bind_native_stream(
        self, system_prompt: str, tools: tuple[Mapping[str, Any], ...],
    ) -> None:
        if (
            self._source_consumer_id not in NATIVE_STREAM_PROFILES
            or self._state != "claimed"
            or not isinstance(self._client, NativeStreamPolicyRuntimeClientPort)
        ):
            raise RunnerPolicyBindingError(
                "native stream bootstrap requires an active compiled stream binding",
                code="native_response_binding_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        self._client.bind_native_stream(system_prompt, tools)

    def stage_native_http_request(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if (
            self._source_consumer_id != OPENHANDS_RESPONSE_CONSUMER_ID
            or self._state != "claimed"
            or not isinstance(self._client, NativeHTTPPolicyRuntimeClientPort)
        ):
            raise RunnerPolicyBindingError(
                "native HTTP request requires an active compiled OpenHands binding",
                code="native_response_binding_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        return self._client.stage_native_http_request(request)

    def take_native_http_response(self, response_digest: str) -> Mapping[str, Any]:
        if (
            self._source_consumer_id != OPENHANDS_RESPONSE_CONSUMER_ID
            or self._state != "claimed"
            or not isinstance(self._client, NativeHTTPPolicyRuntimeClientPort)
        ):
            raise RunnerPolicyBindingError(
                "native HTTP response requires an active compiled OpenHands binding",
                code="native_response_binding_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        return self._client.take_native_http_response(response_digest)

    @property
    def episode_id(self) -> str:
        return self._episode_id

    @property
    def effective_plan_digest(self) -> str:
        return self._effective_plan_digest

    @property
    def binding_digest(self) -> str:
        return self._binding_digest

    @property
    def policy_capability_observation(self) -> PolicyCapabilityObservation:
        return self._observation

    @property
    def policy_capability_observation_digest(self) -> str:
        return self._observation_digest

    @property
    def policy_slot_ids(self) -> tuple[str, ...]:
        return self._policy_slot_ids

    @property
    def first_request_digest(self) -> str | None:
        return self._first_request_digest

    async def claim(self) -> None:
        async with self._lock:
            if self._state != "ready":
                raise RunnerPolicyBindingError(
                    "policy runtime binding has already been claimed",
                    code="binding_already_claimed",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            self._state = "claimed"

    async def invoke(
        self, request: PolicyRuntimeInvokeRequest
    ) -> PolicyRuntimeInvokeResult:
        if type(request) is not PolicyRuntimeInvokeRequest:
            raise TypeError("request must be an exact PolicyRuntimeInvokeRequest")
        if (
            request.episode_id != self._episode_id
            or request.effective_plan_digest != self._effective_plan_digest
            or request.binding_digest != self._binding_digest
        ):
            raise RunnerPolicyBindingError(
                "policy invocation identity does not match the binding",
                code="binding_identity_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        if request.policy_slot_id not in self._policy_slot_ids:
            raise RunnerPolicyBindingError(
                "policy invocation slot is not bound",
                code="policy_slot_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        if canonical_sha256(request.request_payload) != request.request_digest:
            raise RunnerPolicyBindingError(
                "policy invocation request digest does not match its payload",
                code="binding_identity_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        async with self._lock:
            if self._state == "ready":
                self._state = "claimed"
            if self._state not in {"claimed", "running"} or self._active_task is not None:
                raise RunnerPolicyBindingError(
                    "policy runtime binding is unavailable",
                    code="binding_already_claimed",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            if self._cancel_reason is not None:
                raise RunnerCancelled(
                    RunnerCancellation(reason=self._cancel_reason, requested=True),
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            if self._first_request_digest is None:
                self._first_request_digest = request.request_digest
            task = asyncio.create_task(self._client.invoke(request))
            self._active_task = task
            self._state = "running"
        try:
            result = await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = RunnerDependencyError(
                "policy runtime invocation failed",
                code="policy_invoke_failed",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
            error.__cause__ = exc
            raise error
        else:
            if type(result) is not PolicyRuntimeInvokeResult:
                raise RunnerProtocolError(
                    "policy runtime returned an invalid response",
                    code="policy_response_invalid",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            return result
        finally:
            async with self._lock:
                if self._active_task is task:
                    self._active_task = None
                if self._state == "running":
                    self._state = "cancelled" if self._cancel_reason else "claimed"

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        """Adapt the bound policy runtime to the terminal runner's generate port."""
        if len(self._policy_slot_ids) != 1:
            raise RunnerPolicyBindingError(
                "terminal policy generation requires exactly one bound policy slot",
                code="policy_slot_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        turn = self._generated_turn + 1
        request_digest = canonical_sha256(request_payload)
        result = await self.invoke(
            PolicyRuntimeInvokeRequest(
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
                binding_digest=self._binding_digest,
                policy_slot_id=self._policy_slot_ids[0],
                request_digest=request_digest,
                request_payload=request_payload,
                turn=turn,
                attempt=1,
            )
        )
        response = thaw_json(result.response_payload)
        if type(response) is not dict:
            raise RunnerProtocolError(
                "policy runtime returned a non-object response",
                code="policy_response_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        if canonical_sha256(response) != result.response_digest:
            raise RunnerProtocolError(
                "policy response digest does not match the response payload",
                code="policy_response_digest_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        self._generated_turn = turn
        return response

    async def cancel(self, reason: str) -> None:
        normalized = reason.strip() if type(reason) is str else ""
        if not normalized:
            normalized = "runner cancelled"
        async with self._lock:
            if self._cancel_reason is None:
                self._cancel_reason = normalized
            if self._state == "closed":
                return
            task = self._cancel_task
            if task is not None and task.done():
                return
            if task is None:
                task = asyncio.create_task(self._cancel_once())
                self._cancel_task = task
        await asyncio.shield(task)

    async def _cancel_once(self) -> None:
        async with self._lock:
            active = self._active_task
            reason = self._cancel_reason
        dependency_error: RunnerDependencyError | None = None
        try:
            await self._client.cancel(reason or "runner cancelled")
        except (asyncio.CancelledError, Exception) as exc:
            dependency_error = RunnerDependencyError(
                "policy runtime cancellation failed",
                code="policy_cancel_failed",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
            dependency_error.__cause__ = exc
        if active is not None and not active.done():
            active.cancel()
        if active is not None:
            try:
                await active
            except (asyncio.CancelledError, Exception):
                pass
        async with self._lock:
            if self._state not in {"closed", "failed"}:
                self._state = "cancelled"
        if dependency_error is not None:
            raise dependency_error

    async def close(self) -> None:
        async with self._lock:
            task = self._close_task
            if task is None:
                task = asyncio.create_task(self._close_once())
                self._close_task = task
        await asyncio.shield(task)

    async def _close_once(self) -> None:
        async with self._lock:
            active = self._active_task
        primary: BaseException | None = None
        if active is not None and not active.done():
            try:
                await self.cancel("runner session closed")
            except BaseException as exc:
                primary = exc
        physical_close_failed = False
        try:
            await self._client.close()
        except (asyncio.CancelledError, Exception) as exc:
            physical_close_failed = True
            close_error = RunnerDependencyError(
                "policy runtime close failed",
                code="policy_close_failed",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
            close_error.__cause__ = exc
            if primary is None:
                primary = close_error
        async with self._lock:
            self._state = "failed" if physical_close_failed else "closed"
        if primary is not None:
            raise primary


@dataclass(frozen=True, slots=True)
class _ToolProjection:
    tool_id: str
    model_name: str
    aliases: tuple[str, ...]
    schema: FrozenJsonObject
    timeout_ms: int
    max_per_turn: int | None


@dataclass(frozen=True, slots=True)
class _ModelProjection:
    model_id: str
    provider_id: str
    policy_slot_id: str
    params: FrozenJsonObject
    trainable_values: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class _ModeProjection:
    mode_id: str
    model_id: str
    variant: FrozenJsonObject
    tool_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RuntimeProjection:
    models: tuple[_ModelProjection, ...]
    modes: tuple[_ModeProjection, ...]
    mode_sequence: tuple[str, ...]
    tools: tuple[_ToolProjection, ...]
    responses_use_developer_role: bool
    tool_prompt_mode: str
    source_profile: FrozenJsonObject | None = None
    source_consumer_id: str | None = None




def _plan_error(request: RunnerOpenRequest, message: str, code: str) -> RunnerPlanError:
    return RunnerPlanError(
        message,
        code=code,
        episode_id=request.episode_id,
        effective_plan_digest=request.effective_plan_digest,
    )

def _note_cleanup_failure(primary: BaseException, cleanup: BaseException) -> None:
    """Keep a cleanup failure visible without replacing the primary error."""
    primary.add_note(
        "runner cleanup failed: "
        f"{type(cleanup).__name__}: {str(cleanup)[:256]}"
    )


def _project_ir(request: RunnerOpenRequest) -> _RuntimeProjection:
    semantic = freeze_json_object(
        thaw_json(request.effective_plan.effective_semantics),
        field_name="compiled semantic IR",
    )
    runtime = semantic.get("runtime")
    if not isinstance(runtime, Mapping) or (
        runtime.get("runner_adapter_id") != request.effective_plan.runner.adapter_id
        or runtime.get("runtime_abi") != request.effective_plan.runner.runtime_abi
    ):
        raise _plan_error(request, "compiled runtime identity is invalid", "compiled_ir_mismatch")
    canonical_disabled_families = {
        "plugins": {
            "enabled": False,
            "plugins": (),
            "untrusted_hook_tool_ids": (),
        },
        "guardrails": {
            "definitions": (),
            "plan_bootstrap": None,
        },
        "observability": {
            "logging": {},
            "telemetry": {},
        },
    }
    unsupported_families = (
        "features", "turn_strategy", "completion", "concurrency", "permissions",
        "enhanced_tools", "plugins", "guardrails", "team", "replay",
        "long_running", "terminal_sessions", "observability",
    )
    if any(
        semantic.get(name)
        not in ({}, None, (), canonical_disabled_families.get(name))
        for name in unsupported_families
    ):
        raise _plan_error(request, "compiled semantic family is unsupported", "compiled_ir_mismatch")

    metadata = semantic.get("metadata")
    target = metadata.get("e4_target") if isinstance(metadata, Mapping) else None
    source_profile = None
    source_consumer_id = None
    if isinstance(target, Mapping) and (
        target.get("renderer_id") in {MINI_RESPONSE_CONSUMER_ID, OPENHANDS_RESPONSE_CONSUMER_ID}
        or target.get("renderer_id") in NATIVE_STREAM_PROFILES
    ):
        source_consumer_id = target["renderer_id"]
        stream_profile = NATIVE_STREAM_PROFILES.get(source_consumer_id)
        expected_target, expected_version = (
            (stream_profile.target_id, stream_profile.target_version)
            if stream_profile is not None
            else {
                MINI_RESPONSE_CONSUMER_ID: ("mini-swe-agent@2.4.6", 2),
                OPENHANDS_RESPONSE_CONSUMER_ID: ("openhands-sdk@1.47.0", 3),
            }[source_consumer_id]
        )
        if (
            target.get("target_id") != expected_target
            or target.get("version") != expected_version
            or not isinstance(target.get("runtime_profile"), Mapping)
        ):
            raise _plan_error(request, "source-native profile is invalid", "compiled_ir_mismatch")
        source_profile = target["runtime_profile"]
    providers = semantic.get("providers")
    if not isinstance(providers, Mapping):
        raise _plan_error(request, "compiled provider IR is invalid", "compiled_ir_mismatch")
    provider_tools = providers.get("provider_tools")
    if not isinstance(provider_tools, Mapping):
        raise _plan_error(request, "compiled provider controls are invalid", "compiled_ir_mismatch")
    allowed_provider_controls = {
        "api_variant", "use_native", "responses_use_developer_role",
        "suppress_prompts", "responses_stateful",
    }
    if (
        any(
            key not in allowed_provider_controls
            and value not in (False, None, "", (), {})
            for key, value in provider_tools.items()
        )
        or provider_tools.get("api_variant") != (
            "chat" if source_consumer_id == OPENHANDS_RESPONSE_CONSUMER_ID else "responses"
        )
        or provider_tools.get("use_native") is not True
        or provider_tools.get("suppress_prompts", False) is not False
        or provider_tools.get("responses_stateful", False) is not False
        or type(provider_tools.get("responses_use_developer_role", True)) is not bool
    ):
        raise _plan_error(request, "compiled provider authority is unsupported", "compiled_ir_mismatch")
    responses_use_developer_role = provider_tools.get("responses_use_developer_role", True)

    models = providers.get("models")
    slots = providers.get("policy_slots")
    if not isinstance(models, tuple) or not models or not isinstance(slots, tuple):
        raise _plan_error(request, "compiled provider IR is invalid", "compiled_ir_mismatch")
    semantic_slot_ids = tuple(item.get("slot_id") for item in slots if isinstance(item, Mapping))
    plan_slot_ids = tuple(grant.slot_id for grant in request.effective_plan.policy_slots)
    if (
        len(semantic_slot_ids) != len(slots)
        or len(set(semantic_slot_ids)) != len(semantic_slot_ids)
        or semantic_slot_ids != plan_slot_ids
    ):
        raise _plan_error(request, "compiled policy slots do not match plan grants", "compiled_ir_mismatch")
    slots_by_id = {item["slot_id"]: item for item in slots}
    grants_by_slot = {grant.slot_id: grant for grant in request.effective_plan.policy_slots}
    for slot_id in semantic_slot_ids:
        slot = slots_by_id[slot_id]
        grant = grants_by_slot[slot_id]
        if (
            slot.get("requested_route_handle_id") != grant.route_id
            or slot.get("requested_credential_handle_id") != grant.secret_handle_id
            or type(slot.get("model_id")) is not str
            or type(slot.get("adapter_id")) is not str
            or type(slot.get("request_schema_id")) is not str
        ):
            raise _plan_error(request, "compiled policy slot authority is invalid", "compiled_ir_mismatch")

    model_ids = tuple(item.get("model_id") for item in models if isinstance(item, Mapping))
    if len(model_ids) != len(models) or len(set(model_ids)) != len(model_ids):
        raise _plan_error(request, "compiled model identities are invalid", "compiled_ir_mismatch")
    default_model_id = providers.get("default_model_id")
    if default_model_id not in model_ids:
        raise _plan_error(request, "compiled default model is missing", "compiled_ir_mismatch")
    projected_models: list[_ModelProjection] = []
    for model in models:
        if source_profile is not None:
            try:
                native_policy = NativeResponsePolicy.from_dict(model.get("response_policy"))
            except ValueError as exc:
                raise _plan_error(request, "source-native response policy is invalid", "native_response_consumer_mismatch") from exc
            if native_policy.consumer_id != source_consumer_id:
                raise _plan_error(request, "source-native response consumer differs", "native_response_consumer_mismatch")
        elif "response_policy" in model:
            raise _plan_error(
                request,
                "compiled native response policy requires its recording consumer",
                "native_response_consumer_mismatch",
            )
        model_id = model["model_id"]
        slot_id = model.get("policy_slot_id")
        slot = slots_by_id.get(slot_id)
        routing = model.get("routing")
        if (
            type(model_id) is not str
            or type(model.get("provider_id")) is not str
            or not isinstance(model.get("params", {}), Mapping)
            or not isinstance(routing, Mapping)
            or routing.get("fallback_model_ids") != ()
            or slot is None
            or slot.get("model_id") != model_id
            or slot.get("adapter_id") != model.get("adapter_id")
            or slot.get("request_schema_id") != model.get("request_schema_id")
        ):
            raise _plan_error(request, "compiled model authority is invalid", "compiled_ir_mismatch")
        pointers = slot.get("trainable_json_pointers")
        if not isinstance(pointers, tuple) or tuple(sorted(set(pointers))) != pointers:
            raise _plan_error(request, "trainable pointers are invalid", "trainable_pointer_invalid")
        for index, pointer in enumerate(pointers):
            if type(pointer) is not str or not pointer.startswith("/") or pointer == "/":
                raise _plan_error(request, "trainable pointer is invalid", "trainable_pointer_invalid")
            if any(other.startswith(pointer + "/") for other in pointers[index + 1:]):
                raise _plan_error(request, "trainable pointers overlap", "trainable_pointer_invalid")
            _resolve_pointer(model, pointer, request)
        projected_models.append(
            _ModelProjection(
                model_id=model_id,
                provider_id=model["provider_id"],
                policy_slot_id=slot_id,
                params=freeze_json_object(model.get("params", {}), field_name="model params"),
                trainable_values=freeze_json_object(
                    {pointer: thaw_json(_resolve_pointer(model, pointer, request)) for pointer in pointers},
                    field_name="trainable values",
                ),
            )
        )
    default_provider_id = next(
        model.provider_id for model in projected_models
        if model.model_id == default_model_id
    )
    if any(model.provider_id != default_provider_id for model in projected_models):
        raise _plan_error(request, "compiled model provider authority is inconsistent", "compiled_ir_mismatch")

    tool_grant_ids = tuple(tool.tool_id for tool in request.effective_plan.effective_capabilities.tools)
    tools = semantic.get("tools")
    definitions = tools.get("definitions") if isinstance(tools, Mapping) else None
    aliases_raw = tools.get("aliases") if isinstance(tools, Mapping) else None
    selected_tool_ids = tools.get("selected_tool_ids") if isinstance(tools, Mapping) else None
    if (
        not isinstance(definitions, tuple)
        or not isinstance(aliases_raw, tuple)
        or not isinstance(selected_tool_ids, tuple)
        or any(type(tool_id) is not str for tool_id in selected_tool_ids)
        or tuple(sorted(selected_tool_ids)) != tool_grant_ids
    ):
        raise _plan_error(request, "compiled tool authority is invalid", "tool_grant_mismatch")
    by_id = {item.get("tool_id"): item for item in definitions if isinstance(item, Mapping)}
    if len(by_id) != len(definitions) or tuple(by_id) != selected_tool_ids:
        raise _plan_error(request, "compiled tools do not match grants", "tool_grant_mismatch")
    aliases_by_id: dict[str, list[str]] = {tool_id: [] for tool_id in tool_grant_ids}
    seen_names: set[str] = set(tool_grant_ids)
    for alias_record in aliases_raw:
        if (
            not isinstance(alias_record, tuple)
            or len(alias_record) != 2
            or type(alias_record[0]) is not str
            or alias_record[1] not in aliases_by_id
            or alias_record[0] in seen_names
        ):
            raise _plan_error(request, "compiled tool alias is invalid", "tool_grant_mismatch")
        seen_names.add(alias_record[0])
        aliases_by_id[alias_record[1]].append(alias_record[0])
    projected_tools: list[_ToolProjection] = []
    admitted_provider_ids = {model.provider_id for model in projected_models}
    for tool_id in selected_tool_ids:
        definition = by_id[tool_id]
        model_name = definition.get("model_name")
        if (
            type(model_name) is not str
            or not model_name
            or model_name in seen_names and model_name != tool_id
        ):
            raise _plan_error(request, "compiled tool model name is invalid", "tool_grant_mismatch")
        seen_names.add(model_name)
        parameters = definition.get("parameters")
        if not isinstance(parameters, tuple):
            raise _plan_error(request, "compiled tool parameters are invalid", "compiled_ir_mismatch")
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in parameters:
            if (
                not isinstance(parameter, Mapping)
                or type(parameter.get("name")) is not str
                or parameter["name"] in properties
                or not isinstance(parameter.get("schema"), Mapping)
            ):
                raise _plan_error(request, "compiled tool parameter is invalid", "compiled_ir_mismatch")
            schema = thaw_json(parameter["schema"])
            rules = parameter.get("validation_rules")
            if not isinstance(rules, Mapping):
                raise _plan_error(request, "compiled tool validation rules are invalid", "compiled_ir_mismatch")
            schema.update(thaw_json(rules))
            if parameter.get("has_default") is True:
                schema["default"] = thaw_json(parameter.get("default_value"))
            if parameter.get("description") is not None:
                schema["description"] = parameter["description"]
            _admit_schema(schema, request)
            properties[parameter["name"]] = schema
            if parameter.get("required") is True:
                required.append(parameter["name"])
        routing = definition.get("provider_routing")
        if not isinstance(routing, Mapping) or any(provider_id not in admitted_provider_ids for provider_id in routing):
            raise _plan_error(request, "compiled tool provider routing is invalid", "compiled_ir_mismatch")
        strict = any(isinstance(policy, Mapping) and policy.get("strict") is True for policy in routing.values())
        additional = any(
            isinstance(policy, Mapping) and policy.get("additionalProperties") is True
            for policy in routing.values()
        )
        parameter_schema = {
            "type": "object", "properties": properties,
            "required": required, "additionalProperties": additional,
        }
        _admit_schema(parameter_schema, request)
        schema = freeze_json_object(
            {
                "type": "function", "name": model_name,
                "description": definition.get("description"),
                "parameters": parameter_schema, "strict": strict,
            },
            field_name="compiled tool schema",
        )
        execution = definition.get("execution")
        max_per_turn = execution.get("max_per_turn") if isinstance(execution, Mapping) else None
        if not isinstance(execution, Mapping) or (
            max_per_turn is not None and (type(max_per_turn) is not int or max_per_turn < 1)
        ):
            raise _plan_error(request, "compiled tool limit is invalid", "compiled_ir_mismatch")
        projected_tools.append(
            _ToolProjection(
                tool_id, model_name, tuple(aliases_by_id[tool_id]), schema,
                request.effective_plan.effective_capabilities.limits.action_timeout_ms,
                max_per_turn,
            )
        )

    prompts = semantic.get("prompts")
    variants = prompts.get("variants") if isinstance(prompts, Mapping) else None
    synthesis = prompts.get("synthesis") if isinstance(prompts, Mapping) else None
    injection = prompts.get("injection") if isinstance(prompts, Mapping) else None
    dialects = prompts.get("dialects") if isinstance(prompts, Mapping) else None
    if (
        not isinstance(variants, tuple)
        or prompts.get("tool_prompt_mode") not in {"system_once", "per_turn_append", "native_only"}
        or prompts.get("environment") not in ({}, None)
        or prompts.get("dedupe", False) is not False
        or prompts.get("packs", ()) != ()
        or not isinstance(dialects, Mapping)
        or dialects.get("default", ()) != ()
        or not isinstance(injection, Mapping)
        or injection.get("system_order", ()) not in ((), ("mode_specific",))
        or injection.get("per_turn_order", ()) != ()
        or not isinstance(synthesis, Mapping)
        or synthesis.get("enabled") is not True
        or synthesis.get("renderer_id") != "breadboard.tool-catalog.v1"
        or synthesis.get("detail", {}) != {}
        or synthesis.get("selection", {}) != {}
        or synthesis.get("templates", ()) != ()
        or synthesis.get("tool_catalog_template") is not None
    ):
        raise _plan_error(request, "compiled prompt controls are unsupported", "prompt_variant_mismatch")
    variant_map: dict[tuple[str, str, str], FrozenJsonObject] = {}
    for variant in variants:
        if not isinstance(variant, Mapping):
            raise _plan_error(request, "compiled prompt variant is invalid", "prompt_variant_mismatch")
        key = (variant.get("config_node_id"), variant.get("mode_id"), variant.get("model_id"))
        effective_tool_ids = variant.get("effective_tool_ids")
        if (
            any(type(item) is not str for item in key)
            or key in variant_map
            or key[2] not in model_ids
            or not isinstance(effective_tool_ids, tuple)
            or len(set(effective_tool_ids)) != len(effective_tool_ids)
            or any(tool_id not in tool_grant_ids for tool_id in effective_tool_ids)
            or canonical_sha256({"schema": "bb.tool-set.v1", "tool_ids": list(effective_tool_ids)})
            != variant.get("tool_set_digest")
        ):
            raise _plan_error(request, "compiled prompt variant is invalid", "prompt_variant_mismatch")
        for prompt_part in ("system", "per_turn", "tool_catalog"):
            part = variant.get(prompt_part)
            if (
                not isinstance(part, Mapping)
                or type(part.get("text")) is not str
                or bytes_sha256(part["text"].encode()) != part.get("text_digest")
            ):
                raise _plan_error(request, "compiled prompt text digest is invalid", "prompt_variant_mismatch")
        catalog = variant["tool_catalog"]
        if catalog.get("effective_tool_ids") != effective_tool_ids:
            raise _plan_error(
                request,
                "compiled prompt catalog tool order is invalid",
                "prompt_variant_mismatch",
            )
        variant_map[key] = freeze_json_object(variant, field_name="prompt variant")

    root_id = semantic.get("root_config_node_id")
    modes = semantic.get("modes")
    if type(root_id) is not str or not isinstance(modes, tuple) or not modes:
        raise _plan_error(request, "compiled mode IR is invalid", "compiled_ir_mismatch")
    mode_ids: set[str] = set()
    projected_modes: list[_ModeProjection] = []
    for mode in modes:
        mode_id = mode.get("mode_id") if isinstance(mode, Mapping) else None
        enabled_tool_ids = mode.get("enabled_tool_ids") if isinstance(mode, Mapping) else None
        if (
            type(mode_id) is not str or mode_id in mode_ids or mode.get("enabled") is not True
            or not isinstance(enabled_tool_ids, tuple)
            or len(set(enabled_tool_ids)) != len(enabled_tool_ids)
            or any(tool_id not in tool_grant_ids for tool_id in enabled_tool_ids)
        ):
            raise _plan_error(request, "compiled mode is invalid or disabled", "compiled_ir_mismatch")
        mode_ids.add(mode_id)
        variant = variant_map.get((root_id, mode_id, default_model_id))
        if variant is None or variant.get("effective_tool_ids") != enabled_tool_ids:
            raise _plan_error(request, "compiled mode prompt variant is not exact", "prompt_variant_mismatch")
        projected_modes.append(_ModeProjection(mode_id, default_model_id, variant, enabled_tool_ids))
    if any(key[1] not in mode_ids for key in variant_map):
        raise _plan_error(request, "compiled prompt variant mode is invalid", "prompt_variant_mismatch")
    for mode in projected_modes:
        for model_id in model_ids:
            variant = variant_map.get((root_id, mode.mode_id, model_id))
            if variant is None or variant.get("effective_tool_ids") != mode.tool_ids:
                raise _plan_error(
                    request,
                    "compiled model prompt variant is missing or inconsistent",
                    "prompt_variant_mismatch",
                )

    loop = semantic.get("loop")
    sequence_raw = loop.get("sequence") if isinstance(loop, Mapping) else None
    if not isinstance(sequence_raw, tuple) or not sequence_raw:
        raise _plan_error(request, "compiled loop sequence is invalid", "compiled_ir_mismatch")
    sequence: list[str] = []
    for step in sequence_raw:
        if not isinstance(step, Mapping) or step.get("mode_id") not in mode_ids:
            raise _plan_error(request, "compiled loop step has no prompt variant", "prompt_variant_mismatch")
        condition = step.get("condition")
        if condition is None:
            sequence.append(step["mode_id"])
        elif (
            isinstance(condition, Mapping)
            and type(condition.get("evaluated_value")) is bool
            and type(condition.get("expected_truthy")) is bool
        ):
            if condition["evaluated_value"] is condition["expected_truthy"]:
                sequence.append(step["mode_id"])
        else:
            raise _plan_error(request, "compiled loop condition is invalid", "compiled_ir_mismatch")
    if not sequence:
        raise _plan_error(request, "compiled loop has no eligible mode", "compiled_ir_mismatch")
    return _RuntimeProjection(
        tuple(projected_models), tuple(projected_modes), tuple(sequence),
        tuple(projected_tools), responses_use_developer_role,
        prompts["tool_prompt_mode"], source_profile, source_consumer_id,
    )


def _resolve_pointer(value: Mapping[str, Any], pointer: str, request: RunnerOpenRequest) -> Any:
    current: Any = value
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if "~" in raw_token and raw_token.replace("~0", "").replace("~1", "").find("~") >= 0:
            raise _plan_error(request, "trainable pointer escape is invalid", "trainable_pointer_invalid")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, tuple) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise _plan_error(request, "trainable pointer target is missing", "trainable_pointer_invalid")
    return current


_SCHEMA_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "const", "minLength", "maxLength", "pattern", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems",
    "maxItems", "uniqueItems", "description", "default", "examples", "title",
})
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def _admit_schema(schema: Mapping[str, Any], request: RunnerOpenRequest) -> None:
    if any(type(key) is not str or key not in _SCHEMA_KEYWORDS for key in schema):
        raise _plan_error(request, "compiled tool schema keyword is unsupported", "compiled_ir_mismatch")
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SCHEMA_TYPES:
        raise _plan_error(request, "compiled tool schema type is unsupported", "compiled_ir_mismatch")
    if "enum" in schema and (
        not isinstance(schema["enum"], (list, tuple)) or not schema["enum"]
    ):
        raise _plan_error(request, "compiled tool schema enum is invalid", "compiled_ir_mismatch")
    if "enum" in schema:
        enum_values = schema["enum"]
        if any(
            _json_equal(value, other)
            for index, value in enumerate(enum_values)
            for other in enum_values[index + 1:]
        ):
            raise _plan_error(request, "compiled tool schema enum is invalid", "compiled_ir_mismatch")

    object_keywords = {"properties", "required", "additionalProperties"} & set(schema)
    array_keywords = {"items", "minItems", "maxItems", "uniqueItems"} & set(schema)
    string_keywords = {"minLength", "maxLength", "pattern"} & set(schema)
    numeric_keywords = {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"
    } & set(schema)
    if (
        (object_keywords and schema_type not in (None, "object"))
        or (array_keywords and schema_type not in (None, "array"))
        or (string_keywords and schema_type not in (None, "string"))
        or (numeric_keywords and schema_type not in (None, "number", "integer"))
    ):
        raise _plan_error(request, "compiled schema keywords conflict with its type", "compiled_ir_mismatch")

    if object_keywords or schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        additional = schema.get("additionalProperties", False)
        if (
            not isinstance(properties, Mapping)
            or not isinstance(required, (list, tuple))
            or any(type(name) is not str or name not in properties for name in required)
            or len(set(required)) != len(required)
            or type(additional) is not bool
        ):
            raise _plan_error(request, "compiled object schema is invalid", "compiled_ir_mismatch")
        for child in properties.values():
            if not isinstance(child, Mapping):
                raise _plan_error(request, "compiled object property schema is invalid", "compiled_ir_mismatch")
            _admit_schema(child, request)
    if "items" in schema:
        if not isinstance(schema["items"], Mapping):
            raise _plan_error(request, "compiled array item schema is invalid", "compiled_ir_mismatch")
        _admit_schema(schema["items"], request)
    if "pattern" in schema:
        if type(schema["pattern"]) is not str:
            raise _plan_error(request, "compiled schema pattern is invalid", "compiled_ir_mismatch")
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            error = _plan_error(request, "compiled schema pattern is invalid", "compiled_ir_mismatch")
            error.__cause__ = exc
            raise error
    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        if name in schema and (type(schema[name]) is not int or schema[name] < 0):
            raise _plan_error(request, "compiled schema bound is invalid", "compiled_ir_mismatch")
    if (
        "minLength" in schema and "maxLength" in schema
        and schema["minLength"] > schema["maxLength"]
    ) or (
        "minItems" in schema and "maxItems" in schema
        and schema["minItems"] > schema["maxItems"]
    ):
        raise _plan_error(request, "compiled schema bounds are inconsistent", "compiled_ir_mismatch")
    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if name in schema and (
            type(schema[name]) not in (int, float)
            or not math.isfinite(schema[name])
            or (name == "multipleOf" and schema[name] <= 0)
        ):
            raise _plan_error(request, "compiled numeric schema bound is invalid", "compiled_ir_mismatch")
    inconsistent_numeric_bounds = (
        (
            "minimum" in schema
            and "maximum" in schema
            and schema["minimum"] > schema["maximum"]
        )
        or (
            "exclusiveMinimum" in schema
            and "exclusiveMaximum" in schema
            and schema["exclusiveMinimum"] >= schema["exclusiveMaximum"]
        )
        or (
            "exclusiveMinimum" in schema
            and "maximum" in schema
            and schema["exclusiveMinimum"] >= schema["maximum"]
        )
        or (
            "minimum" in schema
            and "exclusiveMaximum" in schema
            and schema["minimum"] >= schema["exclusiveMaximum"]
        )
    )
    if inconsistent_numeric_bounds:
        raise _plan_error(request, "compiled schema bounds are inconsistent", "compiled_ir_mismatch")
    if "uniqueItems" in schema and type(schema["uniqueItems"]) is not bool:
        raise _plan_error(request, "compiled array uniqueness control is invalid", "compiled_ir_mismatch")


class ConductorAdapter:
    __slots__ = ("_descriptor",)

    def __init__(self, runtime_abi: str) -> None:
        if runtime_abi != CONDUCTOR_RUNTIME_ABI:
            raise ValueError("conductor adapter accepts only its exact runtime ABI")
        measured = measure_module_artifact(__file__)
        if (
            measured != _CONDUCTOR_MODULE_IDENTITY
            or measure_module_artifact(mini_semantics.__file__) != _MINI_MODULE_IDENTITY
            or measure_module_artifact(native_stream_consumers.__file__) != _STREAM_MODULE_IDENTITY
            or (
                measure_module_artifact(native_stream_profiles.__file__),
                *(
                    measure_module_artifact(profile.state_module.__file__)
                    for profile in NATIVE_STREAM_PROFILES.values()
                ),
            ) != _PROFILE_MODULE_IDENTITIES
        ):
            raise RuntimeError("conductor module artifact changed after bootstrap")
        self._descriptor = RunnerAdapterDescriptor(
            adapter_id=CONDUCTOR_ADAPTER_ID,
            runtime_abi=runtime_abi,
            implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST,
        )

    @property
    def descriptor(self) -> RunnerAdapterDescriptor:
        return self._descriptor

    async def open(
        self,
        request: RunnerOpenRequest,
        *,
        policy: PolicyRuntimeBindingPort,
        workspace: ConductorToolPort,
        cancellation: RunnerCancellationProbe,
        events: RunnerEventSink,
    ) -> RunnerSession[ConductorRunRequest]:
        if type(request) is not RunnerOpenRequest:
            raise TypeError("request must be an exact RunnerOpenRequest")
        runner = request.effective_plan.runner
        if runner.adapter_id != self._descriptor.adapter_id:
            raise _plan_error(request, "effective plan runner adapter does not match the selected adapter", "adapter_mismatch")
        if runner.runtime_abi != self._descriptor.runtime_abi:
            raise _plan_error(request, "effective plan runtime ABI does not match the selected adapter", "runtime_abi_mismatch")
        if runner.implementation_digest != self._descriptor.implementation_digest:
            raise _plan_error(request, "effective plan implementation digest does not match the installed adapter", "implementation_digest_mismatch")
        if type(policy) is not PolicyRuntimeBinding:
            raise RunnerPolicyBindingError(
                "conductor requires an exact policy runtime binding",
                code="binding_identity_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        if (
            policy.episode_id != request.episode_id
            or policy.effective_plan_digest != request.effective_plan_digest
            or tuple(policy.policy_slot_ids) != tuple(sorted(slot.slot_id for slot in request.effective_plan.policy_slots))
        ):
            raise RunnerPolicyBindingError(
                "policy binding identity does not match the runner request",
                code="binding_identity_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        projection = _project_ir(request)
        observation = policy.policy_capability_observation
        selected_model = next(
            model for model in projection.models
            if model.model_id == projection.modes[0].model_id
        )
        if (
            selected_model.model_id != observation.model_id
            or selected_model.provider_id != observation.provider_id
            or request.effective_plan.policy_capability_observation_digest
            != observation.canonical_digest()
            or request.effective_plan.policy_capability_digest
            != observation.capability_digest
        ):
            raise RunnerPolicyBindingError(
                "policy binding observation does not match compiled model authority",
                code="policy_observation_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        expected = tuple(
            (tool.tool_id, tool.implementation_digest, tuple(tool.capability_ids))
            for tool in request.effective_plan.effective_capabilities.tools
        )
        try:
            installed_bindings = workspace.tool_bindings
            installed = tuple(
                (binding.tool_id, binding.implementation_digest, binding.capability_ids)
                for binding in installed_bindings
            ) if type(installed_bindings) is tuple and all(type(item) is RunnerToolBinding for item in installed_bindings) else None
        except Exception:
            installed = None
        if installed != expected:
            raise _plan_error(request, "tool port bindings do not exactly match plan grants", "tool_grant_mismatch")
        await policy.claim()
        return _ConductorSession(
            open_request=request,
            binding=policy,
            tools=workspace,
            cancellation=cancellation,
            events=events,
            projection=projection,
        )


class _ConductorSession:
    __slots__ = (
        "_open_request", "_binding", "_tools", "_cancellation_probe", "_event_sink",
        "_projection", "_events", "_sequence", "_lock", "_emit_lock", "_phase",
        "_cancellation", "_turns", "_cancellation_published", "_binding_cancel_task",
        "_close_task", "_poison", "_terminal_committing",
        "_native_stream_close_callback", "_native_stream_close_started",
        "_native_cleanup_outcome",
    )

    def __init__(
        self,
        *,
        open_request: RunnerOpenRequest,
        binding: PolicyRuntimeBinding,
        tools: ConductorToolPort,
        cancellation: RunnerCancellationProbe,
        events: RunnerEventSink,
        projection: _RuntimeProjection,
    ) -> None:
        self._open_request = open_request
        self._binding = binding
        self._tools = tools
        self._cancellation_probe = cancellation
        self._event_sink = events
        self._projection = projection
        self._events: list[RunnerEvent] = []
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._emit_lock = asyncio.Lock()
        self._phase = "idle"
        self._cancellation: RunnerCancellation | None = None
        self._turns: list[RunnerTurn] = []
        self._cancellation_published = False
        self._binding_cancel_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._poison: RunnerEventSinkError | None = None
        self._terminal_committing = False
        self._native_stream_close_callback: Any = None
        self._native_stream_close_started = False
        self._native_cleanup_outcome = NativeCleanupOutcome(False, None, None, None)

    @property
    def native_cleanup_outcome(self) -> NativeCleanupOutcome | None:
        if self._projection.source_consumer_id not in NATIVE_STREAM_PROFILES:
            return None
        return self._native_cleanup_outcome

    def _set_native_cleanup_outcome(
        self, *, attempted: bool, all_dead: bool | None, error_code: str | None,
        binding_close_error_code: str | None = None,
    ) -> None:
        prior = self._native_cleanup_outcome
        self._native_cleanup_outcome = NativeCleanupOutcome(
            attempted, all_dead, error_code,
            prior.binding_close_error_code
            if binding_close_error_code is None
            else binding_close_error_code,
        )

    def _record_native_cleanup_failure(
        self, primary: BaseException | None, cleanup: BaseException,
    ) -> None:
        error_code = getattr(cleanup, "code", None)
        if not isinstance(error_code, str) or not error_code:
            error_code = type(cleanup).__name__.lower()
        self._set_native_cleanup_outcome(
            attempted=True, all_dead=False, error_code=error_code,
        )
        if primary is not None:
            _note_cleanup_failure(primary, cleanup)

    def _record_binding_close_failure(self, cleanup: BaseException) -> None:
        if self._projection.source_consumer_id not in NATIVE_STREAM_PROFILES:
            return
        error_code = getattr(cleanup, "code", None)
        if not isinstance(error_code, str) or not error_code:
            error_code = type(cleanup).__name__.lower()
        outcome = self._native_cleanup_outcome
        self._set_native_cleanup_outcome(
            attempted=outcome.attempted,
            all_dead=outcome.all_dead,
            error_code=outcome.error_code,
            binding_close_error_code=error_code,
        )

    async def run(self, request: ConductorRunRequest) -> RunnerResult:
        async with self._lock:
            if self._poison is not None:
                raise self._state_error("session_failed", "runner session has failed")
            if self._close_task is not None:
                raise self._state_error("session_closed", "runner session is closed")
            if self._phase != "idle":
                raise self._state_error("run_already_started", "runner session permits exactly one run")
            self._phase = "running"
        try:
            if type(request) is not ConductorRunRequest:
                await self._raise_error(
                    RunnerRequestError(
                        "conductor runner requires ConductorRunRequest",
                        code="request_type_invalid",
                        **self._context(),
                    )
                )
            await self._checkpoint("before_run")
            return await self._loop(request)
        finally:
            async with self._lock:
                if self._phase == "running":
                    self._phase = "failed"

    async def cancel(self, reason: str) -> RunnerCancellation:
        normalized = reason.strip() if type(reason) is str else ""
        if not normalized:
            normalized = "runner cancelled"
        async with self._lock:
            if self._poison is not None:
                raise self._state_error("session_failed", "runner session has failed")
            if self._cancellation is not None:
                cancellation = self._cancellation
                binding_task = self._binding_cancel_task
                first = False
            elif self._phase not in {"idle", "running"} or self._terminal_committing:
                return RunnerCancellation(reason=normalized, requested=False)
            else:
                cancellation = RunnerCancellation(reason=normalized, requested=True)
                self._cancellation = cancellation
                binding_task = asyncio.create_task(self._binding.cancel(normalized))
                self._binding_cancel_task = binding_task
                first = True
        if first and _EVENT_SINK_SESSION.get() is not self:
            try:
                await self._publish_cancellation_request()
            finally:
                assert binding_task is not None
                await binding_task
        elif binding_task is not None:
            await binding_task
        return cancellation

    async def close(self) -> RunnerCloseResult:
        async with self._lock:
            task = self._close_task
            owner = task is None
            if task is None:
                task = asyncio.create_task(self._close_once())
                self._close_task = task
        await asyncio.shield(task)
        return RunnerCloseResult(already_closed=not owner, cancellation=self._cancellation)

    async def _close_once(self) -> None:
        async with self._lock:
            running = self._phase == "running" and not self._terminal_committing

        primary: BaseException | None = None
        if running:
            try:
                await self.cancel("runner session closed")
            except BaseException as exc:
                primary = exc
        try:
            await self._binding.close()
        except BaseException as exc:
            if primary is None:
                primary = exc
            else:
                self._record_binding_close_failure(exc)
                _note_cleanup_failure(primary, exc)
        if primary is not None:
            raise primary
    async def _loop(self, request: ConductorRunRequest) -> RunnerResult:
        if self._projection.source_consumer_id == OPENHANDS_RESPONSE_CONSUMER_ID:
            async with asyncio.timeout(180):
                return await self._loop_openhands(request)
        stream_profile = NATIVE_STREAM_PROFILES.get(self._projection.source_consumer_id)
        if stream_profile is not None:
            async with asyncio.timeout(stream_profile.episode_timeout_seconds):
                return await self._loop_native_stream(request, stream_profile)
        transcript: list[Any] = []
        limits = self._open_request.effective_plan.effective_capabilities.limits
        transcript_size = _encoded_json_size(request.task_input) + _encoded_json_size(request.context)
        if transcript_size > limits.transcript_bytes:
            await self._raise_error(
                RunnerProtocolError(
                    "compiled transcript byte limit exceeded",
                    code="transcript_limit_exceeded",
                    **self._context(),
                )
            )
        last_response: FrozenJsonObject = freeze_json_object({}, field_name="empty response")
        termination = RunnerTermination.MAX_TURNS
        models = {model.model_id: model for model in self._projection.models}
        modes = {mode.mode_id: mode for mode in self._projection.modes}
        tools_by_id = {tool.tool_id: tool for tool in self._projection.tools}
        mini = None
        if self._projection.source_profile is not None:
            if (
                not isinstance(self._tools, MiniTemplateFramePort)
                or self._binding.source_model_config is None
                or limits.max_turns != 8
                or limits.action_timeout_ms != 35_000
                or len(models) != 1
                or any(model.params for model in models.values())
                or set(tools_by_id) != {"bash"}
            ):
                raise _plan_error(self._open_request, "Mini source runtime controls differ", "compiled_ir_mismatch")
            task = request.task_input.get("prompt")
            if set(request.task_input) != {"prompt"} or type(task) is not str:
                raise RunnerRequestError("Mini requires the owned headless prompt", code="request_authority_invalid")
            frame = self._tools.mini_template_frame()
            profile = self._projection.source_profile
            agent = profile["agent"]
            model_config = self._binding.source_model_config
            mini = mini_semantics.MiniSemanticsState(
                task=task,
                system_template=agent["system_template"],
                instance_template=agent["instance_template"],
                observation_template=model_config["observation_template"],
                format_error_template=model_config["format_error_template"],
                runtime_template_vars=mini_semantics.recursive_merge(frame, model_config),
                step_limit=agent["step_limit"],
                cost_limit=agent["cost_limit"],
                wall_time_limit_seconds=agent["wall_time_limit_seconds"],
                max_consecutive_format_errors=agent["max_consecutive_format_errors"],
            )
            await self._source_history_commit(
                mini, 0, "initial", turn=None, runtime_frame={"environment": frame, "model": model_config}
            )
        for turn in range(1, limits.max_turns + (2 if mini is not None else 1)):
            if mini is not None:
                before = len(mini.messages)
                try:
                    mini.begin_query()
                except mini_semantics.LimitsExceeded:
                    await self._source_history_commit(mini, before, "exit", turn=len(self._turns))
                    termination = RunnerTermination.LIMITS_EXCEEDED
                    break
            mode_id = self._projection.mode_sequence[(turn - 1) % len(self._projection.mode_sequence)]
            mode = modes[mode_id]
            model = models[mode.model_id]
            variant = mode.variant
            mode_tools = tuple(tools_by_id[tool_id] for tool_id in mode.tool_ids)
            tool_by_name: dict[str, _ToolProjection] = {}
            for tool in mode_tools:
                tool_by_name[tool.model_name] = tool
                for alias in tool.aliases:
                    tool_by_name[alias] = tool
            catalog_text = variant["tool_catalog"]["text"]
            system_text = variant["system"]["text"]
            per_turn_text = variant["per_turn"]["text"]
            if self._projection.tool_prompt_mode == "system_once":
                system_text = _join_prompt_parts(system_text, catalog_text)
            elif self._projection.tool_prompt_mode == "per_turn_append":
                per_turn_text = _join_prompt_parts(per_turn_text, catalog_text)
            final_request: dict[str, Any] = thaw_json(model.params)
            final_request.update(
                {
                    "model": model.model_id,
                    "instructions": system_text,
                    "input": [
                        {
                            "role": (
                                "developer"
                                if self._projection.responses_use_developer_role
                                else "system"
                            ),
                            "content": per_turn_text,
                        },
                        {"role": "user", "content": thaw_json(request.task_input)},
                        *thaw_json(tuple(transcript)),
                    ],
                    "tools": [thaw_json(tool.schema) for tool in mode_tools],
                }
            )
            if request.context:
                final_request["metadata"] = {"task_context": thaw_json(request.context)}
            if mini is not None:
                chat_tools = []
                for tool in mode_tools:
                    function = thaw_json(tool.schema)
                    function.pop("type")
                    function.pop("strict")
                    function["parameters"].pop("additionalProperties")
                    chat_tools.append({"type": "function", "function": function})
                final_request = {
                    "model": model.model_id,
                    "messages": mini.prepare_request_history(),
                    "tools": chat_tools,
                }
            frozen_request = freeze_json_object(final_request, field_name="final policy request")
            request_digest = canonical_sha256(frozen_request)
            await self._checkpoint("before_policy", turn=turn)
            await self._emit(
                PolicyRequestEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, frozen_request,
                )
            )
            await self._checkpoint("before_policy", turn=turn)
            invoke_request = PolicyRuntimeInvokeRequest(
                episode_id=self._open_request.episode_id,
                effective_plan_digest=self._open_request.effective_plan_digest,
                binding_digest=self._binding.binding_digest,
                policy_slot_id=model.policy_slot_id,
                request_digest=request_digest,
                request_payload=frozen_request,
                turn=turn,
                attempt=1,
            )
            first_digest = self._binding.first_request_digest or request_digest
            await self._emit(
                PolicyRuntimeRequestEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, 1,
                    self._binding.binding_digest,
                    self._binding.policy_capability_observation_digest,
                    model.policy_slot_id, request_digest, first_digest,
                    model.trainable_values,
                )
            )
            await self._checkpoint("before_policy", turn=turn)
            try:
                result = await self._binding.invoke(invoke_request)
            except asyncio.CancelledError:
                await self._checkpoint("after_policy", turn=turn)
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    raise
                error = RunnerDependencyError(
                    "policy runtime invocation failed",
                    code="policy_invoke_failed",
                    **self._context(),
                )
                await self._raise_error(error, turn=turn)
            except Exception as exc:
                failure = None if mini is None else _find_mini_provider_failure(exc)
                if failure is not None:
                    # Native DefaultAgent.handle_uncaught_exception, then the
                    # typed outer failure propagates unchanged.
                    committed = len(mini.messages)
                    mapped = failure.exception
                    mini.commit_native_exit(
                        type(mapped).__name__,
                        exception_str=str(mapped),
                        traceback_text=failure.traceback_text,
                    )
                    await self._source_history_commit(mini, committed, "exit", turn=turn)
                raise
            await self._checkpoint("after_policy", turn=turn)
            try:
                response, _ = freeze_json_object_with_size(
                    result.response_payload,
                    field_name="policy response",
                    max_encoded_bytes=limits.response_bytes,
                    max_nodes=limits.response_bytes + 1,
                )
            except JsonSnapshotError as exc:
                error = RunnerProtocolError(
                    "policy runtime returned an invalid response",
                    code="policy_response_invalid",
                    **self._context(),
                )
                error.__cause__ = exc
                await self._raise_error(error, turn=turn)
            response_digest = canonical_sha256(response)
            if response_digest != result.response_digest:
                await self._raise_error(
                    RunnerProtocolError(
                        "policy response digest does not match the response payload",
                        code="policy_response_digest_mismatch",
                        **self._context(),
                    ),
                    turn=turn,
                )
            output = response.get("output", ()) if mini is None else ()
            if not isinstance(output, tuple):
                await self._raise_error(
                    RunnerProtocolError(
                        "policy response output is malformed",
                        code="policy_response_invalid",
                        **self._context(),
                    ),
                    turn=turn,
                )
            normalized = tuple(
                freeze_json_object(item, field_name="policy output")
                for item in output
                if isinstance(item, Mapping)
            )
            if len(normalized) != len(output) or any(
                not _supported_output_item(item) for item in normalized
            ):
                await self._raise_error(
                    RunnerProtocolError(
                        "policy response output item is unsupported",
                        code="policy_response_invalid",
                        **self._context(),
                    ),
                    turn=turn,
                )
            await self._emit(
                PolicyRuntimeResponseEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, 1,
                    self._binding.binding_digest, model.policy_slot_id,
                    request_digest, response_digest,
                )
            )
            await self._checkpoint("after_policy", turn=turn)
            await self._emit(
                PolicyResponseEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn,
                    response, normalized,
                )
            )
            observations: list[FrozenJsonObject] = []
            calls = tuple(item for item in normalized if item.get("type") == "function_call")
            parsed = None
            if mini is not None:
                native = response.get("native_response")
                converted = response.get("mini_response")
                if (
                    not isinstance(native, Mapping)
                    or not isinstance(native.get("raw_response"), Mapping)
                    or not isinstance(converted, Mapping)
                    or not isinstance(converted.get("response"), Mapping)
                    or not isinstance(converted.get("message"), Mapping)
                ):
                    await self._raise_error(
                        RunnerProtocolError("Mini native sample is missing", code="native_response_invalid", **self._context()),
                        turn=turn,
                    )
                before = len(mini.messages)
                parsed = mini.parse_and_commit_response(
                    converted["response"],
                    response_json=converted.get("response_json"),
                    cost=response["cost"], timestamp=time.time()
                )
                await self._source_history_commit(
                    mini, before, "format_error" if parsed.is_format_error else "assistant", turn=turn
                )
                if parsed.is_format_error:
                    self._turns.append(RunnerTurn(turn, (), ()))
                    if mini.is_exited:
                        termination = RunnerTermination.REPEATED_FORMAT_ERROR
                        break
                    continue
                raw_calls = converted["message"].get("tool_calls") or ()
                calls = tuple({
                    "name": call["function"]["name"],
                    "call_id": call["id"],
                    "arguments": call["function"]["arguments"],
                } for call in raw_calls)
            await self._checkpoint(
                "before_action" if calls else "after_policy", turn=turn
            )
            counts: dict[str, int] = {}
            for ordinal, call in enumerate(calls):
                name = call.get("name")
                call_id = call.get("call_id")
                if type(name) is not str or type(call_id) is not str or name not in tool_by_name:
                    await self._raise_error(
                        RunnerProtocolError(
                            "policy returned an unbound tool call",
                            code="policy_response_invalid",
                            **self._context(),
                        ),
                        turn=turn,
                        call_id=call_id if type(call_id) is str else None,
                    )
                tool = tool_by_name[name]
                counts[tool.tool_id] = counts.get(tool.tool_id, 0) + 1
                if tool.max_per_turn is not None and counts[tool.tool_id] > tool.max_per_turn:
                    await self._raise_error(
                        RunnerProtocolError(
                            "policy exceeded the compiled per-turn tool limit",
                            code="policy_response_invalid",
                            **self._context(),
                        ),
                        turn=turn,
                        call_id=call_id,
                    )
                raw_arguments = call["arguments"]
                try:
                    arguments = (
                        {"command": parsed.actions[ordinal].command}
                        if mini is not None else json.loads(raw_arguments)
                    )
                    if type(arguments) is not dict:
                        raise ValueError
                except Exception as exc:
                    error = RunnerProtocolError(
                        "policy tool arguments are malformed",
                        code="policy_response_invalid",
                        **self._context(),
                    )
                    error.__cause__ = exc
                    await self._raise_error(error, turn=turn, call_id=call_id)
                try:
                    if mini is None:
                        _validate_arguments(arguments, tool.schema["parameters"])
                except RunnerProtocolError as error:
                    error.episode_id = self._open_request.episode_id
                    error.effective_plan_digest = self._open_request.effective_plan_digest
                    await self._raise_error(error, turn=turn, call_id=call_id)
                await self._checkpoint("before_action", turn=turn, call_id=call_id)
                await self._emit(
                    ToolCallEvent(
                        0, self._open_request.episode_id,
                        self._open_request.effective_plan_digest, turn,
                        ordinal, call_id, name, raw_arguments,
                    )
                )
                await self._checkpoint("before_action", turn=turn, call_id=call_id)
                try:
                    observation_raw = await self._tools.invoke_tool(
                        tool.tool_id, arguments, timeout_ms=tool.timeout_ms
                    )
                    observation, _ = freeze_json_object_with_size(
                        observation_raw,
                        field_name="tool observation",
                        max_encoded_bytes=limits.observation_bytes,
                        max_nodes=limits.observation_bytes + 1,
                    )
                except asyncio.CancelledError as exc:
                    await self._checkpoint(
                        "after_action",
                        turn=turn,
                        call_id=call_id,
                    )
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
                        raise
                    error = RunnerDependencyError(
                        "conductor tool invocation failed",
                        code="tool_invoke_failed",
                        **self._context(),
                    )
                    error.__cause__ = exc
                    await self._raise_error(
                        error,
                        turn=turn,
                        call_id=call_id,
                    )
                except Exception as exc:
                    error = RunnerDependencyError(
                        "conductor tool invocation failed",
                        code=(
                            "native_output_limit_exceeded"
                            if getattr(exc, "code", None) == "native_output_limit_exceeded"
                            else "tool_invoke_failed"
                        ),
                        **self._context(),
                    )
                    error.__cause__ = exc
                    await self._raise_error(error, turn=turn, call_id=call_id)
                if mini is not None:
                    observations.append(observation)
                    native_exit = observation.get("exit")
                    await self._emit(
                        ToolObservationEvent(
                            0, self._open_request.episode_id,
                            self._open_request.effective_plan_digest, turn,
                            ordinal, call_id, name, observation, native_exit is not None,
                        )
                    )
                    await self._checkpoint("after_action", turn=turn, call_id=call_id)
                    if native_exit is not None:
                        if (
                            not isinstance(native_exit, Mapping)
                            or native_exit.get("role") != "exit"
                            or native_exit.get("extra", {}).get("exit_status") != "Submitted"
                        ):
                            await self._raise_error(
                                RunnerProtocolError("Mini native exit is invalid", code="tool_result_invalid", **self._context()),
                                turn=turn, call_id=call_id,
                            )
                        before = len(mini.messages)
                        mini.commit_native_exit("Submitted", native_exit["content"])
                        await self._source_history_commit(mini, before, "exit", turn=turn)
                        termination = RunnerTermination.SUBMITTED
                        break
                    continue
                await self._checkpoint("after_action", turn=turn, call_id=call_id)
                output_item = {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(
                        thaw_json(observation),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
                added_size = _encoded_json_size(call) + _encoded_json_size(output_item)
                if transcript_size + added_size > limits.transcript_bytes:
                    await self._raise_error(
                        RunnerProtocolError(
                            "compiled transcript byte limit exceeded",
                            code="transcript_limit_exceeded",
                            **self._context(),
                        ),
                        turn=turn,
                        call_id=call_id,
                    )
                observations.append(observation)
                await self._emit(
                    ToolObservationEvent(
                        0, self._open_request.episode_id,
                        self._open_request.effective_plan_digest, turn,
                        ordinal, call_id, name, observation, False,
                    )
                )
                await self._checkpoint("after_action", turn=turn, call_id=call_id)
                transcript.extend((thaw_json(call), output_item))
                transcript_size += added_size
            if mini is not None and not mini.is_exited:
                before = len(mini.messages)
                mini.commit_whole_batch_observations(observations, timestamp=time.time())
                await self._source_history_commit(mini, before, "observation_batch", turn=turn)
            self._turns.append(
                RunnerTurn(
                    turn=turn,
                    policy_output=normalized,
                    observations=tuple(observations),
                )
            )
            last_response = response
            if mini is not None:
                if mini.is_exited:
                    break
                continue
            if not calls:
                termination = RunnerTermination.ASSISTANT_COMPLETE
                break
        if mini is not None:
            last_response = freeze_json_object({
                "trajectory_format": "mini-swe-agent-1.1",
                "messages": mini.messages,
                "info": {
                    "mini_version": "2.4.6",
                    "exit_status": mini.exit_status,
                    "submission": mini.submission,
                    "model_stats": {"instance_cost": mini.cost, "api_calls": mini.n_calls},
                },
            }, field_name="Mini source trajectory")
        await self._checkpoint("after_loop", turn=len(self._turns))
        await self._checkpoint("before_commit", turn=len(self._turns))
        await self._commit_termination(termination)
        return RunnerResult(
            episode_id=self._open_request.episode_id,
            effective_plan_digest=self._open_request.effective_plan_digest,
            original_request={
                "task_input": request.task_input,
                "context": request.context,
            },
            response=last_response,
            termination=termination,
            turn_count=len(self._turns),
            turns=tuple(self._turns),
            events=tuple(self._events),
        )

    async def _loop_native_stream(
        self, request: ConductorRunRequest, profile: native_stream_profiles.NativeStreamProfile,
    ) -> RunnerResult:
        self._native_stream_close_callback = None
        self._native_stream_close_started = False
        try:
            return await self._loop_native_stream_body(request, profile)
        except BaseException as primary:
            callback = self._native_stream_close_callback
            if callback is not None and not self._native_stream_close_started:
                try:
                    await callback()
                except BaseException as cleanup:
                    if not isinstance(cleanup, asyncio.CancelledError):
                        self._record_native_cleanup_failure(primary, cleanup)
            raise
        finally:
            self._native_stream_close_callback = None
            self._native_stream_close_started = False

    async def _loop_native_stream_body(
        self, request: ConductorRunRequest, profile: native_stream_profiles.NativeStreamProfile,
    ) -> RunnerResult:
        """Drive native streamed source phases through the admitted lease."""
        limits = self._open_request.effective_plan.effective_capabilities.limits
        consumer_id = self._projection.source_consumer_id
        tools = self._tools
        source_profile = self._projection.source_profile
        advertisement = (
            source_profile.get("advertisement") if isinstance(source_profile, Mapping) else None
        )
        if (
            not isinstance(tools, NativeSourceSessionPort)
            or not isinstance(tools, NativeRuntimeInputPort)
            or not isinstance(tools, NativeWorkspaceEffectsPort)
            or self._binding.source_model_config is None
            or not isinstance(advertisement, Mapping)
            or limits.max_turns != profile.max_turns
            or limits.action_timeout_ms != profile.action_timeout_ms
            or len(self._projection.models) != 1
            or len(self._projection.modes) != 1
            or self._projection.models[0].params
            or tuple(self._projection.modes[0].tool_ids) != profile.tool_order
            or consumer_id != profile.consumer_id
        ):
            raise _plan_error(self._open_request, "native stream runtime controls differ", "compiled_ir_mismatch")
        task = request.task_input.get("prompt")
        if set(request.task_input) != {"prompt"} or type(task) is not str or request.context:
            raise RunnerRequestError(
                "native stream requires the owned headless prompt without caller context",
                code="request_authority_invalid",
            )
        await tools.begin_native_workspace_effects()
        model = self._projection.models[0]

        async def phase(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
            raw = await tools.invoke_native_phase(
                operation,
                payload,
                timeout_ms=limits.action_timeout_ms,
                package_subpath=(
                    profile.package_subpath if operation == "initialize" else None
                ),
            )
            frozen, _ = freeze_json_object_with_size(
                raw, field_name="native stream phase",
                max_encoded_bytes=16 * 1024 * 1024,
                max_nodes=16 * 1024 * 1024 + 1,
            )
            if frozen.get("schema_version") != profile.phase_schema_version:
                raise RunnerProtocolError(
                    "native stream phase revision is invalid",
                    code="native_response_invalid", **self._context(),
                )
            return thaw_json(frozen)

        close_task: asyncio.Task[dict[str, Any]] | None = None

        async def close_once() -> dict[str, Any]:
            nonlocal close_task
            self._native_stream_close_started = True
            if close_task is None:
                async def close_phase() -> dict[str, Any]:
                    self._set_native_cleanup_outcome(
                        attempted=True, all_dead=None, error_code=None,
                    )
                    try:
                        async with asyncio.timeout(limits.action_timeout_ms / 1000):
                            closed = await phase("close", {})
                        # The worker only knows its own process groups. Retire the
                        # native runtime through the lease authority, as OpenHands
                        # does, before any effect measurement.
                        retired = await tools.close_native_runtime()
                    except RunnerError as exc:
                        self._record_native_cleanup_failure(None, exc)
                        raise
                    except asyncio.CancelledError as exc:
                        self._record_native_cleanup_failure(None, exc)
                        raise
                    except BaseException as exc:
                        error = RunnerDependencyError(
                            "native stream close failed",
                            code="native_close_failed",
                            **self._context(),
                        )
                        error.__cause__ = exc
                        self._record_native_cleanup_failure(None, error)
                        raise error
                    cleanup = closed.get("cleanup")
                    retired_cleanup = (
                        retired.get("cleanup") if isinstance(retired, Mapping) else None
                    )
                    if (
                        closed.get("kind") != "closed"
                        or type(cleanup) is not dict
                        or cleanup.get("all_dead") is not True
                        or not isinstance(retired, Mapping)
                        or retired.get("kind") != "closed"
                        or not isinstance(retired_cleanup, Mapping)
                        or retired_cleanup.get("all_dead") is not True
                    ):
                        error = RunnerProtocolError(
                            "native worker cleanup is not verified",
                            code="native_response_invalid", **self._context(),
                        )
                        self._record_native_cleanup_failure(None, error)
                        raise error
                    self._set_native_cleanup_outcome(
                        attempted=True, all_dead=True, error_code=None,
                    )
                    return closed
                close_task = asyncio.create_task(close_phase())
            try:
                return await asyncio.shield(close_task)
            except asyncio.CancelledError as cancellation:
                try:
                    await close_task
                except BaseException as close_error:
                    if not isinstance(close_error, asyncio.CancelledError):
                        self._record_native_cleanup_failure(None, close_error)
                raise cancellation


        declared_runtime_inputs = tools.native_runtime_inputs(
            input_names=profile.runtime_input_names,
            package_subpath=profile.package_subpath,
        )
        if (
            type(declared_runtime_inputs) is not dict
            or set(declared_runtime_inputs) != set(profile.runtime_input_names)
            or any(
                type(declared_runtime_inputs[name]) is not str
                or not declared_runtime_inputs[name]
                for name in profile.runtime_input_names
            )
        ):
            raise RunnerProtocolError(
                "native stream runtime inputs are malformed",
                code="native_response_binding_invalid", **self._context(),
            )
        runtime_inputs = {
            name: declared_runtime_inputs[name] for name in profile.runtime_input_names
        }
        initialized = await phase("initialize", {
            "task": task,
            "model_config": thaw_json(self._binding.source_model_config),
            "advertisement": thaw_json(advertisement),
            "runtime_inputs": runtime_inputs,
        })
        self._native_stream_close_callback = close_once
        bootstrap = initialized.get("bootstrap")
        system_prompt = initialized.get("system_prompt")
        tool_schemas = initialized.get("tool_schemas")
        if (
            initialized.get("kind") != "initialized"
            or type(system_prompt) is not str
            or type(tool_schemas) is not list
            or type(bootstrap) is not dict
        ):
            raise RunnerProtocolError(
                "native stream bootstrap is malformed",
                code="native_response_binding_invalid", **self._context(),
            )
        if runtime_inputs and any(
            bootstrap.get(name) != value for name, value in runtime_inputs.items()
        ):
            raise RunnerProtocolError(
                "native stream bootstrap runtime inputs differ from declared inputs",
                code="native_response_binding_invalid", **self._context(),
            )
        self._binding.bind_native_stream(system_prompt, tuple(tool_schemas))
        state = profile.state_factory(task, system_prompt, bootstrap)
        trace_requests: list[dict[str, Any]] = []

        async def commit(
            start: int, phase_name: str, turn: int | None,
            events: list[Mapping[str, Any]] | None = None,
        ) -> None:
            if _encoded_json_size(state.messages) > limits.transcript_bytes:
                raise RunnerProtocolError(
                    "source transcript limit exceeded",
                    code="transcript_limit_exceeded", **self._context(),
                )
            await self._emit(SourceEventCommitEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                turn, consumer_id, phase_name,
                tuple(state.messages[start:] if events is None else events),
                canonical_sha256(state.messages),
                {
                    "request_count": state.request_count,
                    "stream_fn_issued": state.stream_fn_issued,
                    "native_stop_reason": state.native_stop_reason,
                    "public_stop": state.exit_status,
                },
            ))

        await commit(0, "initial", None)
        termination = RunnerTermination.POLICY_INCOMPLETE
        while not state.is_exited:
            turn = len(self._turns) + 1
            await self._checkpoint("before_policy", turn=turn)
            before = len(state.messages)
            if state.begin_query() is not None:
                # Pi's streamFn seam refuses the ninth query before any HTTP.
                await commit(before, "exit", len(self._turns) or None)
                termination = RunnerTermination.LIMITS_EXCEEDED
                break
            projected = await phase("project_request", {"messages": state.messages})
            if projected.get("kind") != "request":
                raise RunnerProtocolError(
                    "native request projection failed",
                    code="policy_request_invalid", **self._context(),
                )
            frozen_request = freeze_json_object({
                "model": model.model_id,
                "messages": projected.get("messages"),
                "tools": projected.get("tools"),
            }, field_name="native policy request")
            request_digest = canonical_sha256(frozen_request)
            await self._emit(PolicyRequestEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                turn, frozen_request,
            ))
            await self._emit(PolicyRuntimeRequestEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                turn, 1, self._binding.binding_digest,
                self._binding.policy_capability_observation_digest, model.policy_slot_id,
                request_digest, self._binding.first_request_digest or request_digest,
                model.trainable_values,
            ))
            await self._checkpoint("before_policy", turn=turn)
            result = await self._binding.invoke(PolicyRuntimeInvokeRequest(
                episode_id=self._open_request.episode_id,
                effective_plan_digest=self._open_request.effective_plan_digest,
                binding_digest=self._binding.binding_digest, policy_slot_id=model.policy_slot_id,
                request_digest=request_digest, request_payload=frozen_request, turn=turn, attempt=1,
            ))
            await self._checkpoint("after_policy", turn=turn)
            response, _ = freeze_json_object_with_size(
                result.response_payload, field_name="native policy response",
                max_encoded_bytes=16 * 1024 * 1024, max_nodes=16 * 1024 * 1024 + 1,
            )
            if canonical_sha256(response) != result.response_digest:
                raise RunnerProtocolError(
                    "policy response digest does not match the response payload",
                    code="policy_response_digest_mismatch", **self._context(),
                )
            native_receipt = thaw_json(response.get("native_response"))
            request_body = (
                native_receipt.get("request_body")
                if isinstance(native_receipt, Mapping)
                else None
            )
            native_request_digest = (
                native_receipt.get("request_digest")
                if isinstance(native_receipt, Mapping)
                else None
            )
            if (
                not isinstance(request_body, Mapping)
                or type(native_request_digest) is not str
                or canonical_sha256(request_body).removeprefix("sha256:") != native_request_digest
            ):
                raise RunnerProtocolError(
                    "native provider receipt lacks the exact sent request body",
                    code="native_response_invalid", **self._context(),
                )
            trace_requests.append(dict(request_body))
            await self._emit(PolicyRuntimeResponseEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                turn, 1, self._binding.binding_digest, model.policy_slot_id,
                request_digest, result.response_digest,
            ))
            await self._emit(PolicyResponseEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                turn, response, (),
            ))
            native = native_stream_consumers.native_response_from_dict(
                thaw_json(response["native_response"])
            )
            before = len(state.messages)
            parsed = state.prepare_response(native)
            await commit(before, "assistant", turn)
            observations: list[FrozenJsonObject] = []
            if parsed.calls:
                # parsed.calls is the ordered 1:1 projection of native.tool_calls;
                # join by ordinal so duplicate provider IDs keep their own arguments.
                raw_calls = native.tool_calls
                if [raw.id for raw in raw_calls] != [call.id for call in parsed.calls]:
                    raise RunnerProtocolError(
                        "native tool-call projection changed call identity",
                        code="native_response_invalid", **self._context(),
                    )
                for ordinal, (call, raw) in enumerate(zip(parsed.calls, raw_calls, strict=True)):
                    await self._checkpoint("before_action", turn=turn, call_id=call.id)
                    await self._emit(ToolCallEvent(
                        0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                        turn, ordinal, call.id, call.name, raw.arguments,
                    ))
                prepared = await phase("prepare_tools", {"calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in parsed.calls
                ]})
                if prepared.get("kind") != "prepared":
                    raise RunnerProtocolError(
                        "native batch preparation failed",
                        code="native_response_invalid", **self._context(),
                    )
                history_calls = prepared.get("history_calls")
                if history_calls is not None:
                    if (
                        type(history_calls) is not list
                        or any(type(item) is not dict for item in history_calls)
                        or [(item.get("id"), item.get("name")) for item in history_calls]
                        != [(call.id, call.name) for call in parsed.calls]
                    ):
                        raise RunnerProtocolError(
                            "native prepared history identity changed",
                            code="native_response_invalid", **self._context(),
                        )
                    blocks = [block for block in parsed.assistant["content"] if block["type"] == "toolCall"]
                    for block, prepared_call in zip(blocks, history_calls, strict=True):
                        block["arguments"] = prepared_call["arguments"]
                    await commit(len(state.messages), "assistant", turn, events=[
                        {"kind": "assistant_prepared", "message": parsed.assistant},
                    ])
                await self._checkpoint("before_action", turn=turn)
                completed = await phase("execute_batch", {})
                raw_results = completed.get("results")
                if (
                    completed.get("kind") != "tool_results"
                    or type(raw_results) is not list
                    or len(raw_results) != len(parsed.calls)
                    or any(type(item) is not dict for item in raw_results)
                    or [item.get("id") for item in raw_results] != [call.id for call in parsed.calls]
                    or sorted(
                        item.get("completion_index")
                        if type(item.get("completion_index")) is int else -1
                        for item in raw_results
                    ) != list(range(len(raw_results)))
                ):
                    raise RunnerProtocolError(
                        "native tool batch result is malformed",
                        code="native_response_invalid", **self._context(),
                    )
                # Results arrive in source order; observations follow the
                # worker's measured completion order.
                for ordinal in sorted(
                    range(len(raw_results)), key=lambda index: raw_results[index]["completion_index"],
                ):
                    call, raw = parsed.calls[ordinal], raw_results[ordinal]
                    observation, _ = freeze_json_object_with_size(
                        raw, field_name="native tool observation",
                        max_encoded_bytes=limits.observation_bytes,
                        max_nodes=limits.observation_bytes + 1,
                    )
                    observations.append(observation)
                    await self._emit(ToolObservationEvent(
                        0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                        turn, ordinal, call.id, call.name, observation, False,
                    ))
                    await self._checkpoint("after_action", turn=turn, call_id=call.id)
                before = len(state.messages)
                state.commit_tool_results(parsed.calls, raw_results)
                await commit(before, "observation_batch", turn)
                if profile.ack_policy == "after_history_commit":
                    history_digest = canonical_sha256(state.messages)
                    for raw in raw_results:
                        if "delivery_id" not in raw:
                            continue
                        acked = await phase("ack", {
                            "delivery_id": raw["delivery_id"], "history_digest": history_digest,
                        })
                        if acked.get("kind") != "acked" or acked.get("delivery_id") != raw["delivery_id"]:
                            raise RunnerProtocolError(
                                "native delivery acknowledgement failed",
                                code="native_response_invalid", **self._context(),
                            )
            self._turns.append(RunnerTurn(turn, (), tuple(observations)))
            if state.is_exited:
                termination = (
                    RunnerTermination.POLICY_INCOMPLETE
                    if state.native_stop_reason in profile.incomplete_stop_reasons
                    else RunnerTermination.ASSISTANT_COMPLETE
                )
        await self._checkpoint("after_loop", turn=len(self._turns))
        closed = await close_once()
        cleanup = closed["cleanup"]
        effects = await tools.measure_workspace_effects()
        if not isinstance(effects, Mapping):
            raise RunnerProtocolError(
                "native workspace effects are malformed",
                code="native_response_invalid", **self._context(),
            )
        await self._checkpoint("before_commit", turn=len(self._turns))
        await self._commit_termination(termination)
        replay_trace = state.to_trace(
            requests=trace_requests,
            runtime_inputs=runtime_inputs,
            effects=effects,
        )
        return RunnerResult(
            episode_id=self._open_request.episode_id,
            effective_plan_digest=self._open_request.effective_plan_digest,
            original_request={"task_input": request.task_input, "context": request.context},
            response={
                "source_id": consumer_id,
                "replay_trace": replay_trace,
                "bootstrap": bootstrap,
                "cleanup": cleanup,
            },
            termination=termination, turn_count=len(self._turns),
            turns=tuple(self._turns), events=tuple(self._events),
        )

    async def _loop_openhands(self, request: ConductorRunRequest) -> RunnerResult:
        limits = self._open_request.effective_plan.effective_capabilities.limits
        profile = self._projection.source_profile
        model_config = self._binding.source_model_config
        tools = self._tools
        tool_order = ("terminal", "file_editor", "task_tracker", "finish", "think")
        if (
            not isinstance(tools, NativeSourceSessionPort)
            or not isinstance(tools, NativeWorkspaceEffectsPort)
            or profile is None
            or model_config is None
            or limits.max_turns < 1
            or limits.action_timeout_ms != 90_000
            or len(self._projection.models) != 1
            or len(self._projection.modes) != 1
            or tuple(self._projection.modes[0].tool_ids) != tool_order
            or self._projection.models[0].params
        ):
            raise _plan_error(
                self._open_request, "OpenHands source runtime controls differ",
                "compiled_ir_mismatch",
            )
        task = request.task_input.get("prompt")
        if set(request.task_input) != {"prompt"} or type(task) is not str or request.context:
            raise RunnerRequestError(
                "OpenHands requires the owned headless prompt without caller context",
                code="request_authority_invalid",
            )
        await tools.begin_native_workspace_effects()
        model = self._projection.models[0]
        history: list[FrozenJsonObject] = []
        history_bytes = 2
        state: FrozenJsonObject = freeze_json_object({}, field_name="source state")
        trace_requests: list[dict[str, Any]] = []
        trace_tool_calls: list[dict[str, Any]] = []
        trace_observations: list[dict[str, Any]] = []
        def decode_json_body(body_b64: Any) -> Any:
            if type(body_b64) is not str:
                return None
            try:
                return json.loads(base64.b64decode(body_b64, validate=True).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return None

        async def phase(
            operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
        ) -> FrozenJsonObject:
            raw = await tools.invoke_native_phase(operation, payload, timeout_ms=timeout_ms)
            value, _ = freeze_json_object_with_size(
                raw, field_name="OpenHands phase result",
                max_encoded_bytes=16 * 1024 * 1024,
                max_nodes=16 * 1024 * 1024 + 1,
            )
            if value.get("schema_version") != "bb.openhands-native.v1":
                raise RunnerProtocolError(
                    "native phase result revision is invalid",
                    code="native_response_invalid", **self._context(),
                )
            return value

        native_runtime_close_task: asyncio.Task[Mapping[str, Any]] | None = None

        async def close_once() -> Mapping[str, Any]:
            nonlocal native_runtime_close_task
            if native_runtime_close_task is None:
                native_runtime_close_task = asyncio.create_task(tools.close_native_runtime())
            try:
                return await asyncio.shield(native_runtime_close_task)
            except asyncio.CancelledError as cancellation:
                try:
                    await native_runtime_close_task
                except BaseException:
                    pass
                raise cancellation

        async def commit_events(
            value: Mapping[str, Any], phase_name: str, turn: int | None,
        ) -> None:
            nonlocal history_bytes, state
            delta = value.get("event_delta")
            if not isinstance(delta, tuple) or any(not isinstance(event, Mapping) for event in delta):
                raise RunnerProtocolError(
                    "native event delta is invalid",
                    code="native_response_invalid", **self._context(),
                )
            for event in delta:
                kind = event.get("kind")
                if kind == "ObservationEvent":
                    observation_value = event.get("observation")
                    if not isinstance(observation_value, Mapping):
                        observation_value = {"value": observation_value}
                    trace_observations.append({
                        "event_kind": kind,
                        "tool_name": event.get("tool_name"),
                        "is_error": bool(observation_value.get("is_error", False)),
                        "result": observation_value,
                    })
                elif kind in {"AgentErrorEvent", "ConversationErrorEvent"}:
                    trace_observations.append({
                        "event_kind": kind,
                        "tool_name": event.get("tool_name"),
                        "is_error": True,
                        "error_text": event.get("error") or event.get("detail") or event.get("code"),
                        "classification": event.get("classification"),
                    })
            for event in delta:
                history_bytes += _encoded_json_size(event) + (1 if history else 0)
                if history_bytes > limits.transcript_bytes:
                    raise RunnerProtocolError(
                        "source transcript limit exceeded",
                        code="transcript_limit_exceeded", **self._context(),
                    )
                history.append(event)
            status, iteration = value.get("status"), value.get("iteration")
            if status is not None or iteration is not None:
                if (
                    status not in {"IDLE", "RUNNING", "PAUSED", "FINISHED", "STUCK", "ERROR"}
                    or type(iteration) is not int or not 0 <= iteration <= limits.max_turns
                ):
                    raise RunnerProtocolError(
                        "native execution state is invalid",
                        code="native_response_invalid", **self._context(),
                    )
                state = freeze_json_object({
                    "status": status, "iteration": iteration,
                    **({"source_runtime": value["source_runtime"]} if "source_runtime" in value else {}),
                    **({"source_error": value["source_error"]} if "source_error" in value else {}),
                }, field_name="source state")
            await self._emit(SourceEventCommitEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                turn, OPENHANDS_RESPONSE_CONSUMER_ID, phase_name, delta,
                canonical_sha256(history), state,
            ))

        initialized = await phase(
            "initialize",
            {
                "task": task,
                "model_config": thaw_json(model_config),
                "max_iteration_per_run": limits.max_turns,
                "native_config": thaw_json(self._projection.source_profile),
            },
            timeout_ms=60_000,
        )
        if (
            initialized.get("kind") != "initialized"
            or not isinstance(initialized.get("tool_schemas"), tuple)
        ):
            raise RunnerProtocolError(
                "native tools differ from the compiled source recipe",
                code="native_response_binding_invalid", **self._context(),
            )
        self._binding.bind_native_tools(initialized["tool_schemas"])
        await commit_events(initialized, "initial", None)
        termination = RunnerTermination.MAX_TURNS
        for turn in range(1, limits.max_turns + 1):
            await self._checkpoint("before_policy", turn=turn)
            sampled = await phase("sample", {}, timeout_ms=60_000)
            await commit_events(sampled, "before_policy", turn)
            if sampled.get("kind") == "provider_request":
                http_request = sampled.get("http_request")
                if not isinstance(http_request, Mapping):
                    raise RunnerProtocolError(
                        "native provider request is missing",
                        code="native_response_invalid", **self._context(),
                    )
                request_body = decode_json_body(http_request.get("body_b64"))
                if not isinstance(request_body, Mapping):
                    raise RunnerProtocolError(
                        "native provider request body is not JSON",
                        code="native_response_invalid", **self._context(),
                    )
                trace_requests.append({
                    "index": len(trace_requests),
                    "body": request_body,
                })
                frozen_request = freeze_json_object(
                    self._binding.stage_native_http_request(http_request),
                    field_name="native policy request",
                )
                request_digest = canonical_sha256(frozen_request)
                await self._emit(PolicyRequestEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, frozen_request,
                ))
                await self._emit(PolicyRuntimeRequestEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, 1,
                    self._binding.binding_digest,
                    self._binding.policy_capability_observation_digest,
                    model.policy_slot_id, request_digest,
                    self._binding.first_request_digest or request_digest,
                    model.trainable_values,
                ))
                await self._checkpoint("before_policy", turn=turn)
                result = await self._binding.invoke(PolicyRuntimeInvokeRequest(
                    episode_id=self._open_request.episode_id,
                    effective_plan_digest=self._open_request.effective_plan_digest,
                    binding_digest=self._binding.binding_digest,
                    policy_slot_id=model.policy_slot_id,
                    request_digest=request_digest, request_payload=frozen_request,
                    turn=turn, attempt=1,
                ))
                await self._checkpoint("after_policy", turn=turn)
                # The public receipt includes base64 wire bytes. The provider
                # independently bounds the decoded response at four MiB.
                response, _ = freeze_json_object_with_size(
                    result.response_payload, field_name="native policy response",
                    max_encoded_bytes=16 * 1024 * 1024,
                    max_nodes=16 * 1024 * 1024 + 1,
                )
                response_digest = canonical_sha256(response)
                if response_digest != result.response_digest:
                    raise RunnerProtocolError(
                        "policy response digest does not match the response payload",
                        code="policy_response_digest_mismatch", **self._context(),
                    )
                public_response = response.get("native_http_response")
                if isinstance(public_response, Mapping):
                    decoded_response = decode_json_body(public_response.get("body_b64"))
                    if isinstance(decoded_response, Mapping) and trace_requests:
                        trace_requests[-1]["response"] = decoded_response
                await self._emit(PolicyRuntimeResponseEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, 1,
                    self._binding.binding_digest, model.policy_slot_id,
                    request_digest, response_digest,
                ))
                await self._emit(PolicyResponseEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn, response, (),
                ))
                sampled = await phase(
                    "provider_response", self._binding.take_native_http_response(response_digest),
                    timeout_ms=60_000,
                )
                await commit_events(sampled, "before_policy", turn)
            if sampled.get("kind") != "sample_ready":
                raise RunnerProtocolError(
                    "native sample did not complete one provider exchange",
                    code="native_response_invalid", **self._context(),
                )
            prepared = await phase("prepare", {}, timeout_ms=60_000)
            if prepared.get("kind") != "prepared":
                raise RunnerProtocolError(
                    "native response preparation failed",
                    code="native_response_invalid", **self._context(),
                )
            await commit_events(prepared, "assistant", turn)
            actions = prepared.get("actions")
            if not isinstance(actions, tuple):
                raise RunnerProtocolError(
                    "native prepared action list is invalid",
                    code="native_response_invalid", **self._context(),
                )
            prepared_all = prepared.get("prepared_actions", actions)
            if not isinstance(prepared_all, tuple):
                raise RunnerProtocolError(
                    "native prepared action trace is invalid",
                    code="native_response_invalid", **self._context(),
                )
            for action in prepared_all:
                if isinstance(action, Mapping):
                    trace_tool_calls.append({
                        "tool_name": action.get("tool_id"),
                        "arguments": action.get("arguments"),
                        "security_risk": action.get("security_risk", "UNKNOWN"),
                    })
            observations: list[FrozenJsonObject] = []
            finished = False
            for ordinal, action in enumerate(actions):
                if (
                    not isinstance(action, Mapping)
                    or action.get("index") != ordinal
                    or type(action.get("index")) is not int
                    or action.get("tool_id") not in tool_order
                    or type(action.get("call_id")) is not str
                    or not action["call_id"]
                    or not isinstance(action.get("arguments"), Mapping)
                    or finished
                ):
                    raise RunnerProtocolError(
                        "native action does not match the admitted executable prefix",
                        code="native_response_invalid", **self._context(),
                    )
                tool_id, call_id = action["tool_id"], action["call_id"]
                await self._checkpoint("before_action", turn=turn, call_id=call_id)
                await self._emit(ToolCallEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn,
                    ordinal, call_id, tool_id,
                    json.dumps(thaw_json(action["arguments"]), separators=(",", ":")),
                ))
                await self._checkpoint("before_action", turn=turn, call_id=call_id)
                executed = await phase(
                    "execute", {"index": ordinal, "tool_id": tool_id},
                    timeout_ms=limits.action_timeout_ms,
                )
                if (
                    executed.get("kind") != "executed"
                    or executed.get("index") != ordinal
                    or executed.get("tool_id") != tool_id
                    or not isinstance(executed.get("observations"), tuple)
                ):
                    raise RunnerProtocolError(
                        "native tool result identity is invalid",
                        code="native_response_invalid", **self._context(),
                    )
                observation, _ = freeze_json_object_with_size(
                    {"native_observations": executed["observations"]},
                    field_name="native tool observation",
                    max_encoded_bytes=limits.observation_bytes,
                    max_nodes=limits.observation_bytes + 1,
                )
                observations.append(observation)
                finished = tool_id == "finish"
                await self._emit(ToolObservationEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest, turn,
                    ordinal, call_id, tool_id, observation, finished,
                ))
                await self._checkpoint("after_action", turn=turn, call_id=call_id)
            committed = await phase("commit", {}, timeout_ms=60_000)
            if committed.get("kind") != "committed":
                raise RunnerProtocolError(
                    "native observation commit failed",
                    code="native_response_invalid", **self._context(),
                )
            await commit_events(committed, "observation_batch", turn)
            self._turns.append(RunnerTurn(turn, actions, tuple(observations)))
            if state["status"] == "FINISHED":
                termination = RunnerTermination.SUBMITTED if finished else RunnerTermination.ASSISTANT_COMPLETE
                break
            if state["status"] in {"ERROR", "STUCK"}:
                await commit_events({
                    "event_delta": (), "status": state["status"], "iteration": state["iteration"],
                }, "exit", turn)
                # A native source failure is a terminal incomplete policy, not
                # a malformed BB protocol response. Keep the source status in
                # the replay trace while committing the existing incomplete
                # runner termination.
                termination = RunnerTermination.POLICY_INCOMPLETE
                break
        await self._checkpoint("after_loop", turn=len(self._turns))
        closed = await close_once()
        cleanup = closed.get("cleanup") if isinstance(closed, Mapping) else None
        if (
            not isinstance(closed, Mapping)
            or closed.get("kind") != "closed"
            or not isinstance(cleanup, Mapping)
            or cleanup.get("all_dead") is not True
        ):
            raise RunnerProtocolError(
                "native worker cleanup is not verified",
                code="native_response_invalid", **self._context(),
            )
        effects = await tools.measure_workspace_effects()
        if not isinstance(effects, Mapping):
            raise RunnerProtocolError(
                "native workspace effects are malformed",
                code="native_response_invalid", **self._context(),
            )
        await self._checkpoint("before_commit", turn=len(self._turns))
        await self._commit_termination(termination)
        trace_kind = (
            "finished"
            if termination in {RunnerTermination.SUBMITTED, RunnerTermination.ASSISTANT_COMPLETE}
            else str(state.get("status", "")).lower()
            if state.get("status") in {"ERROR", "STUCK"}
            else termination.value
        )
        native_stop_reason = None
        if trace_requests:
            choices = trace_requests[-1].get("response", {}).get("choices", ())
            if choices:
                native_stop_reason = choices[-1].get("finish_reason")
        replay_trace = {
            "schema_version": "bb.e4.openhands-sdk-trace.v1",
            "case_id": self._open_request.episode_id,
            "requests": trace_requests,
            "tool_calls": trace_tool_calls,
            "observations": trace_observations,
            "file_effects": effects,
            "request_count": len(trace_requests),
            "termination": {
                "kind": trace_kind,
                "native_stop_reason": native_stop_reason,
            },
        }
        return RunnerResult(
            episode_id=self._open_request.episode_id,
            effective_plan_digest=self._open_request.effective_plan_digest,
            original_request={"task_input": request.task_input, "context": request.context},
            response={
                "source_id": OPENHANDS_RESPONSE_CONSUMER_ID,
                "events": history,
                "state": state,
                "cleanup": cleanup,
                "replay_trace": replay_trace,
            },
            termination=termination, turn_count=len(self._turns),
            turns=tuple(self._turns), events=tuple(self._events),
        )

    async def _source_history_commit(
        self,
        state: mini_semantics.MiniSemanticsState,
        start: int,
        phase: str,
        *,
        turn: int | None,
        runtime_frame: Mapping[str, Any] | None = None,
    ) -> None:
        limits = self._open_request.effective_plan.effective_capabilities.limits
        if _encoded_json_size(state.messages) > limits.transcript_bytes:
            await self._raise_error(
                RunnerProtocolError("source transcript limit exceeded", code="transcript_limit_exceeded", **self._context()),
                turn=turn,
            )
        await self._emit(SourceHistoryCommitEvent(
            0, self._open_request.episode_id, self._open_request.effective_plan_digest,
            turn, phase, tuple(state.messages[start:]), canonical_sha256(state.messages),
            state.n_calls, state.cost, runtime_frame,
        ))

    async def _checkpoint(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        async with self._lock:
            if self._poison is not None:
                raise self._state_error("session_failed", "runner session has failed")
            cancellation = self._cancellation
        external: Exception | None = None
        if cancellation is None:
            try:
                self._cancellation_probe.raise_if_cancelled(
                    checkpoint, turn=turn, call_id=call_id
                )
            except Exception as exc:
                external = exc
                cancellation = await self.cancel("external cancellation requested")
        if cancellation is None or not cancellation.requested:
            return
        await self._publish_cancellation_request()
        observed = RunnerCancellation(
            cancellation.reason, True, checkpoint, turn, call_id
        )
        await self._emit(
            RunnerCancellationObservedEvent(
                0, self._open_request.episode_id,
                self._open_request.effective_plan_digest,
                cancellation.reason, checkpoint, turn, call_id,
            )
        )
        async with self._lock:
            self._cancellation = observed
            self._phase = "cancelled"
        error = RunnerCancelled(observed, **self._context())
        if external is not None:
            error.__cause__ = external
        raise error

    async def _commit_termination(self, termination: RunnerTermination) -> None:
        async with self._lock:
            if self._cancellation is not None:
                cancellation = self._cancellation
            else:
                cancellation = None
                self._terminal_committing = True
        if cancellation is not None:
            await self._checkpoint("before_commit", turn=len(self._turns))
        try:
            await self._emit(
                RunnerTerminationEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest,
                    len(self._turns), termination,


                )
            )
            async with self._lock:
                self._phase = "completed"
        finally:
            async with self._lock:
                self._terminal_committing = False

    async def _publish_cancellation_request(self) -> None:
        async with self._emit_lock:
            if self._cancellation_published:
                return
            cancellation = self._cancellation
            if cancellation is None or not cancellation.requested:
                return
            await self._publish_locked(
                RunnerCancellationRequestedEvent(
                    0, self._open_request.episode_id,
                    self._open_request.effective_plan_digest,
                    cancellation.reason,
                )
            )
            self._cancellation_published = True

    async def _emit(self, event: RunnerEvent) -> None:
        async with self._emit_lock:
            if self._poison is not None:
                raise self._state_error("session_failed", "runner session has failed")
            await self._publish_locked(event)
            if (
                not self._cancellation_published
                and type(event) is not RunnerCancellationRequestedEvent
                and self._cancellation is not None
                and self._cancellation.requested
            ):
                await self._publish_locked(
                    RunnerCancellationRequestedEvent(
                        0, self._open_request.episode_id,
                        self._open_request.effective_plan_digest,
                        self._cancellation.reason,
                    )
                )
                self._cancellation_published = True

    async def _publish_locked(self, event: RunnerEvent) -> None:
        sequenced = replace(event, sequence=self._sequence)
        token = _EVENT_SINK_SESSION.set(self)
        try:
            try:
                await self._event_sink.emit(sequenced)
            except Exception as exc:
                error = RunnerEventSinkError(
                    "runner event sink rejected an event",
                    failed_event=sequenced,
                    cause=exc,
                    **self._context(),
                )
                async with self._lock:
                    self._phase = "failed"
                    self._poison = error
                raise error
        finally:
            _EVENT_SINK_SESSION.reset(token)
        self._events.append(sequenced)
        self._sequence += 1

    async def _raise_error(
        self,
        error: RunnerError,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        await self._emit(
            RunnerErrorEvent(
                0, self._open_request.episode_id,
                self._open_request.effective_plan_digest,
                error.category, error.code, str(error), turn, call_id,
            )
        )
        error.events_so_far = tuple(self._events)
        raise error

    def _context(self) -> dict[str, Any]:
        return {
            "episode_id": self._open_request.episode_id,
            "effective_plan_digest": self._open_request.effective_plan_digest,
            "events_so_far": tuple(self._events),
        }
    def _state_error(self, code: str, message: str) -> RunnerStateError:
        return RunnerStateError(message, code=code, **self._context())



def _find_mini_provider_failure(error: BaseException) -> MiniProviderFailure | None:
    """Return the explicitly chained Mini provider failure, if any."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, MiniProviderFailure):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


def _join_prompt_parts(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def _encoded_json_size(value: Any) -> int:
    return len(
        json.dumps(
            thaw_json(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _supported_output_item(item: Mapping[str, Any]) -> bool:
    item_type = item.get("type")
    if item_type == "function_call":
        return (
            type(item.get("name")) is str
            and bool(item["name"])
            and type(item.get("call_id")) is str
            and bool(item["call_id"])
            and type(item.get("arguments")) is str
        )
    if item_type != "message" or item.get("role") != "assistant":
        return False
    content = item.get("content")
    if not isinstance(content, tuple):
        return False
    for part in content:
        if not isinstance(part, Mapping):
            return False
        if part.get("type") == "output_text":
            if type(part.get("text")) is not str:
                return False
        elif part.get("type") == "refusal":
            if type(part.get("refusal")) is not str:
                return False
        else:
            return False
    return True


def _protocol_argument_error(message: str) -> RunnerProtocolError:
    return RunnerProtocolError(message, code="policy_response_invalid")


def _json_equal(left: Any, right: Any) -> bool:
    if type(left) is bool or type(right) is bool:
        return type(left) is bool and type(right) is bool and left is right
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return (
            isinstance(left, Mapping)
            and isinstance(right, Mapping)
            and set(left) == set(right)
            and all(_json_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)) or isinstance(right, (list, tuple)):
        return (
            isinstance(left, (list, tuple))
            and isinstance(right, (list, tuple))
            and len(left) == len(right)
            and all(
                _json_equal(a, b)
                for a, b in zip(left, right, strict=True)
            )
        )
    return type(left) is type(right) and left == right


def _validate_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    _validate_schema_value(arguments, schema)


def _validate_schema_value(value: Any, schema: Mapping[str, Any]) -> None:
    expected = schema.get("type")
    valid_type = (
        expected is None
        or (expected == "string" and type(value) is str)
        or (expected == "integer" and type(value) is int)
        or (
            expected == "number"
            and type(value) in (int, float)
            and math.isfinite(value)
        )
        or (expected == "boolean" and type(value) is bool)
        or (expected == "null" and value is None)
        or (expected == "object" and isinstance(value, Mapping))
        or (expected == "array" and isinstance(value, (list, tuple)))
    )
    if type(value) is float and not math.isfinite(value):
        valid_type = False
    if not valid_type:
        raise _protocol_argument_error(
            "policy supplied a tool argument of the wrong type"
        )
    if "enum" in schema and not any(
        _json_equal(value, candidate) for candidate in schema["enum"]
    ):
        raise _protocol_argument_error("policy supplied a tool argument outside its enum")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise _protocol_argument_error("policy supplied a tool argument outside its const")
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if any(name not in value for name in required):
            raise _protocol_argument_error("policy omitted a required tool argument")
        if schema.get("additionalProperties") is False and any(
            name not in properties for name in value
        ):
            raise _protocol_argument_error("policy supplied an unknown tool argument")
        for name, child in value.items():
            if name in properties:
                _validate_schema_value(child, properties[name])
    elif isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise _protocol_argument_error("policy supplied too few tool array items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise _protocol_argument_error("policy supplied too many tool array items")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(_json_equal(item, other) for other in value[index + 1:]):
                    raise _protocol_argument_error("policy supplied duplicate tool array items")
        if "items" in schema:
            for item in value:
                _validate_schema_value(item, schema["items"])
    elif type(value) is str:
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise _protocol_argument_error("policy supplied a tool string below its minimum length")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise _protocol_argument_error("policy supplied a tool string above its maximum length")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise _protocol_argument_error("policy supplied a tool string outside its pattern")
    elif type(value) in (int, float) and type(value) is not bool:
        if "minimum" in schema and value < schema["minimum"]:
            raise _protocol_argument_error("policy supplied a tool number below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise _protocol_argument_error("policy supplied a tool number above its maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise _protocol_argument_error("policy supplied a tool number below its exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise _protocol_argument_error("policy supplied a tool number above its exclusive maximum")
        if "multipleOf" in schema and not _is_multiple_of(
            value, schema["multipleOf"]
        ):
            raise _protocol_argument_error(
                "policy supplied a tool number outside its multiple"
            )


def _is_multiple_of(value: int | float, divisor: int | float) -> bool:
    if type(value) is int and type(divisor) is int:
        return value % divisor == 0
    left = Decimal(value) if type(value) is int else Decimal(str(value))
    right = Decimal(divisor) if type(divisor) is int else Decimal(str(divisor))
    left_tuple = left.as_tuple()
    right_tuple = right.as_tuple()
    left_coefficient = 0
    for digit in left_tuple.digits:
        left_coefficient = left_coefficient * 10 + digit
    right_coefficient = 0
    for digit in right_tuple.digits:
        right_coefficient = right_coefficient * 10 + digit
    exponent_delta = left_tuple.exponent - right_tuple.exponent
    if exponent_delta >= 0:
        return (
            left_coefficient * (10 ** exponent_delta)
        ) % right_coefficient == 0
    return left_coefficient % (
        right_coefficient * (10 ** (-exponent_delta))
    ) == 0
