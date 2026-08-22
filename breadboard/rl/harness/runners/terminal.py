from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
import json
from typing import Any

from breadboard.rl.harness.runner_identity import measure_module_artifact
from breadboard.rl.harness.runners.base import (
    JsonSnapshotError,
    FrozenJsonObject,
    PolicyGeneratePort,
    PolicyRequestEvent,
    PolicyResponseEvent,
    RunnerAdapterDescriptor,
    RunnerCancellation,
    RunnerCancellationObservedEvent,
    RunnerCancellationProbe,
    RunnerCancellationRequestedEvent,
    RunnerCancelled,
    RunnerCloseResult,
    RunnerDependencyError,
    RunnerError,
    RunnerErrorEvent,
    RunnerEvent,
    RunnerEventSink,
    RunnerEventSinkError,
    RunnerOpenRequest,
    RunnerPlanError,
    RunnerProtocolError,
    RunnerRequestError,
    RunnerResult,
    RunnerSession,
    RunnerStateError,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerTurn,
    RunnerToolBinding,
    RunnerWorkspacePort,
    ToolCallEvent,
    ToolObservationEvent,
    freeze_json_object,
    freeze_json_object_with_size,
    freeze_json_with_size,
    thaw_json,
)


TERMINAL_ADAPTER_ID = "breadboard.terminal-responses.v1"
TERMINAL_RUNTIME_ABI = "terminal-responses-abi.v1"



_TERMINAL_MODULE_IDENTITY = measure_module_artifact(__file__)
TERMINAL_IMPLEMENTATION_DIGEST = _TERMINAL_MODULE_IDENTITY.digest

_EVENT_SINK_SESSION: ContextVar[object | None] = ContextVar(
    "terminal_runner_event_sink_session", default=None
)


@dataclass(frozen=True, slots=True)
class TerminalLoopLimits:
    max_turns: int
    action_timeout_seconds: int
    max_observation_chars: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("max_turns", self.max_turns),
            ("action_timeout_seconds", self.action_timeout_seconds),
            ("max_observation_chars", self.max_observation_chars),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class TerminalToolDefinition:
    tool_id: str
    responses_schema: FrozenJsonObject

    def __post_init__(self) -> None:
        if type(self.tool_id) is not str or not self.tool_id or self.tool_id != self.tool_id.strip():
            raise ValueError("tool_id must be a nonempty normalized identifier")
        object.__setattr__(
            self,
            "responses_schema",
            freeze_json_object(self.responses_schema, field_name="terminal tool schema"),
        )


TERMINAL_TOOL_DEFINITIONS: tuple[TerminalToolDefinition, ...] = (
    TerminalToolDefinition(
        "shell",
        {
            "type": "function",
            "name": "shell",
            "description": "Run a shell command in the admitted BreadBoard sandbox workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ),
    TerminalToolDefinition(
        "read_file",
        {
            "type": "function",
            "name": "read_file",
            "description": "Read UTF-8 text from a workspace-relative file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 0},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ),
    TerminalToolDefinition(
        "write_file",
        {
            "type": "function",
            "name": "write_file",
            "description": "Write UTF-8 text to a workspace-relative file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ),
    TerminalToolDefinition(
        "list_files",
        {
            "type": "function",
            "name": "list_files",
            "description": "List files below a workspace-relative directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ),
    TerminalToolDefinition(
        "submit",
        {
            "type": "function",
            "name": "submit",
            "description": "Finish the episode after the task is complete.",
            "parameters": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ),
)


@dataclass(frozen=True, slots=True)
class TerminalRunRequest:
    responses_create_params: FrozenJsonObject
    tools: tuple[TerminalToolDefinition, ...]
    limits: TerminalLoopLimits

    def __post_init__(self) -> None:
        try:
            params = freeze_json_object(
                self.responses_create_params, field_name="responses_create_params"
            )
        except (JsonSnapshotError, TypeError) as exc:
            error = RunnerRequestError(
                "runner request must contain only closed JSON values",
                code="invalid_json_value",
            )
            error.__cause__ = exc
            raise error
        object.__setattr__(self, "responses_create_params", params)
        tools = tuple(self.tools)
        if any(type(tool) is not TerminalToolDefinition for tool in tools):
            raise TypeError("tools must contain only TerminalToolDefinition values")
        object.__setattr__(self, "tools", tools)
        if type(self.limits) is not TerminalLoopLimits:
            raise TypeError("limits must be TerminalLoopLimits")


class TerminalResponsesAdapter:
    __slots__ = ("_descriptor",)

    def __init__(self, runtime_abi: str) -> None:
        if runtime_abi != TERMINAL_RUNTIME_ABI:
            raise ValueError("terminal adapter accepts only its exact runtime ABI")
        measured = measure_module_artifact(__file__)
        if measured != _TERMINAL_MODULE_IDENTITY:
            raise RuntimeError("terminal module artifact changed after bootstrap")
        self._descriptor = RunnerAdapterDescriptor(
            adapter_id=TERMINAL_ADAPTER_ID,
            runtime_abi=runtime_abi,
            implementation_digest=TERMINAL_IMPLEMENTATION_DIGEST,
        )

    @property
    def descriptor(self) -> RunnerAdapterDescriptor:
        return self._descriptor

    async def open(
        self,
        request: RunnerOpenRequest,
        *,
        policy: PolicyGeneratePort,
        workspace: RunnerWorkspacePort,
        cancellation: RunnerCancellationProbe,
        events: RunnerEventSink,
    ) -> RunnerSession[TerminalRunRequest]:
        if type(request) is not RunnerOpenRequest:
            raise TypeError("request must be an exact RunnerOpenRequest")
        plan_runner = request.effective_plan.runner
        if plan_runner.adapter_id != self._descriptor.adapter_id:
            raise RunnerPlanError(
                "effective plan runner adapter does not match the selected adapter",
                code="adapter_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        if plan_runner.runtime_abi != self._descriptor.runtime_abi:
            raise RunnerPlanError(
                "effective plan runtime ABI does not match the selected adapter",
                code="runtime_abi_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        if plan_runner.implementation_digest != self._descriptor.implementation_digest:
            raise RunnerPlanError(
                "effective plan implementation digest does not match the installed adapter",
                code="implementation_digest_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        expected_tool_bindings = tuple(
            RunnerToolBinding(
                tool_id=grant.tool_id,
                implementation_digest=grant.implementation_digest,
                capability_ids=tuple(grant.capability_ids),
            )
            for grant in request.effective_plan.effective_capabilities.tools
        )
        try:
            installed_tool_bindings = workspace.tool_bindings
        except Exception:
            installed_tool_bindings = None
        if type(installed_tool_bindings) is tuple and all(
            type(binding) is RunnerToolBinding for binding in installed_tool_bindings
        ):
            installed_snapshot = tuple(
                (
                    binding.tool_id,
                    binding.implementation_digest,
                    binding.capability_ids,
                )
                for binding in installed_tool_bindings
            )
        else:
            installed_snapshot = None
        expected_snapshot = tuple(
            (binding.tool_id, binding.implementation_digest, binding.capability_ids)
            for binding in expected_tool_bindings
        )
        if installed_snapshot != expected_snapshot:
            raise RunnerPlanError(
                "workspace tool bindings do not exactly match the effective plan grants",
                code="tool_grant_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        return _TerminalSession(
            descriptor=self._descriptor,
            open_request=request,
            policy=policy,
            workspace=workspace,
            cancellation=cancellation,
            event_sink=events,
        )


class _TerminalSession:
    __slots__ = (
        "_descriptor",
        "_open_request",
        "_policy",
        "_workspace",
        "_cancellation_probe",
        "_event_sink",
        "_events",
        "_sequence",
        "_state_lock",
        "_emission_lock",
        "_phase",
        "_closed",
        "_cancellation",
        "_pending_cancellation_reason",
        "_pending_cancellation_future",
        "_publication_failure",
        "_transcript_items",
        "_transcript_size",
        "_response_base_size",
        "_response_output_items",
        "_response_output_size",
    )

    def __init__(
        self,
        *,
        descriptor: RunnerAdapterDescriptor,
        open_request: RunnerOpenRequest,
        policy: PolicyGeneratePort,
        workspace: RunnerWorkspacePort,
        cancellation: RunnerCancellationProbe,
        event_sink: RunnerEventSink,
    ) -> None:
        self._descriptor = descriptor
        self._open_request = open_request
        self._policy = policy
        self._workspace = workspace
        self._cancellation_probe = cancellation
        self._event_sink = event_sink
        self._events: list[RunnerEvent] = []
        self._sequence = 0
        self._state_lock = asyncio.Lock()
        self._emission_lock = asyncio.Lock()
        self._phase = "idle"
        self._closed = False
        self._cancellation: RunnerCancellation | None = None
        self._pending_cancellation_reason: str | None = None
        self._pending_cancellation_future: asyncio.Future[RunnerCancellation] | None = None
        self._publication_failure: RunnerEventSinkError | None = None
        self._transcript_items = 0
        self._transcript_size = 2
        self._response_base_size = 0
        self._response_output_items = 0
        self._response_output_size = 2

    async def run(self, request: TerminalRunRequest) -> RunnerResult:
        async with self._state_lock:
            if self._publication_failure is not None:
                raise self._failed_state_error()
            if self._closed:
                raise self._state_error("session_closed", "runner session is closed")
            if self._phase != "idle":
                raise self._state_error(
                    "run_already_started", "runner session permits exactly one run"
                )
            self._phase = "running"
        try:
            await self._validate_request(request)
            if self._cancellation is not None and self._cancellation.requested:
                await self._checkpoint("before_run")
            return await self._run_terminal_loop(request)
        finally:
            async with self._state_lock:
                if self._phase == "running":
                    self._phase = "failed"

    async def cancel(self, reason: str) -> RunnerCancellation:
        normalized_reason = reason.strip() if type(reason) is str else ""
        if not normalized_reason:
            normalized_reason = "runner cancelled"
        return await self._request_cancellation(normalized_reason, close=False)

    async def close(self) -> RunnerCloseResult:
        async with self._state_lock:
            if self._closed:
                cancellation = (
                    self._cancellation
                    if self._cancellation is not None
                    and self._cancellation.requested
                    else None
                )
                return RunnerCloseResult(
                    already_closed=True, cancellation=cancellation
                )
            self._closed = True
            if self._phase != "running":
                cancellation = (
                    self._cancellation
                    if self._cancellation is not None
                    and self._cancellation.requested
                    else None
                )
                return RunnerCloseResult(
                    already_closed=False, cancellation=cancellation
                )
        cancellation = await self._request_cancellation(
            "runner session closed", close=True
        )
        return RunnerCloseResult(already_closed=False, cancellation=cancellation)

    async def _request_cancellation(
        self, reason: str, *, close: bool
    ) -> RunnerCancellation:
        reentrant = _EVENT_SINK_SESSION.get() is self
        async with self._state_lock:
            if close:
                self._closed = True
            if self._publication_failure is not None:
                raise self._failed_state_error()
            if self._cancellation is not None:
                return self._cancellation
            if self._pending_cancellation_reason is not None:
                pending_reason = self._pending_cancellation_reason
                future = self._pending_cancellation_future
                assert future is not None
            elif self._phase not in {"idle", "running"}:
                return RunnerCancellation(reason=reason, requested=False)
            else:
                pending_reason = reason
                future = asyncio.get_running_loop().create_future()
                self._pending_cancellation_reason = pending_reason
                self._pending_cancellation_future = future
                if reentrant:
                    future.add_done_callback(
                        lambda completed: completed.exception()
                        if not completed.cancelled()
                        else None
                    )
        provisional = RunnerCancellation(reason=pending_reason, requested=False)
        if reentrant:
            return provisional
        async with self._emission_lock:
            await self._drain_pending_cancellation_locked()
        return await asyncio.shield(future)

    async def _validate_request(self, request: TerminalRunRequest) -> None:
        if type(request) is not TerminalRunRequest:
            await self._raise_error(
                RunnerRequestError(
                    "terminal runner requires TerminalRunRequest",
                    code="request_type_invalid",
                    **self._error_context(),
                )
            )
        plan = self._open_request.effective_plan
        limits = plan.effective_capabilities.limits
        projected = request.limits
        minimum_observation = len(b'{"truncated":true}')
        if (
            projected.max_turns != limits.max_turns
            or projected.action_timeout_seconds * 1000 != limits.action_timeout_ms
            or projected.max_observation_chars != limits.observation_bytes
            or projected.max_observation_chars < minimum_observation
        ):
            await self._raise_error(
                RunnerPlanError(
                    "terminal loop limits do not match the effective plan",
                    code="limit_projection_mismatch",
                    **self._error_context(),
                )
            )
        request_tool_ids = tuple(tool.tool_id for tool in request.tools)
        plan_tool_ids = tuple(tool.tool_id for tool in plan.effective_capabilities.tools)
        if (
            len(set(request_tool_ids)) != len(request_tool_ids)
            or tuple(sorted(request_tool_ids)) != plan_tool_ids
        ):
            await self._raise_error(
                RunnerPlanError(
                    "terminal tools do not exactly match the effective plan grants",
                    code="tool_grant_mismatch",
                    **self._error_context(),
                )
            )
        if request.tools != TERMINAL_TOOL_DEFINITIONS:
            await self._raise_error(
                RunnerRequestError(
                    "terminal tool schemas do not match the frozen adapter ABI",
                    code="tool_schema_invalid",
                    **self._error_context(),
                )
            )

    async def _run_terminal_loop(self, request: TerminalRunRequest) -> RunnerResult:
        original_input = request.responses_create_params.get("input", ())
        if type(original_input) is str:
            base_input: tuple[Any, ...] = (
                {"role": "user", "content": original_input},
            )
        elif type(original_input) is tuple:
            base_input = original_input
        else:
            await self._raise_error(
                RunnerRequestError(
                    "responses_create_params.input must be a string or list",
                    code="input_root_invalid",
                    **self._error_context(),
                )
            )

        model_params = dict(request.responses_create_params)
        model_params["tools"] = tuple(
            tool.responses_schema for tool in request.tools
        )
        model_params["parallel_tool_calls"] = False
        all_outputs: list[FrozenJsonObject] = []
        turns: list[RunnerTurn] = []
        last_response: FrozenJsonObject | None = None
        termination = RunnerTermination.MAX_TURNS
        turn_count = 0

        for turn in range(1, request.limits.max_turns + 1):
            await self._checkpoint("before_policy", turn=turn)
            turn_count = turn
            turn_request = dict(model_params)
            turn_request["input"] = base_input + tuple(all_outputs)
            await self._charge_transcript(
                {"event": "policy_request", "turn": turn, "payload": turn_request},
                turn=turn,
            )
            await self._emit(
                PolicyRequestEvent(
                    sequence=0,
                    episode_id=self._open_request.episode_id,
                    effective_plan_digest=self._open_request.effective_plan_digest,
                    turn=turn,
                    request_payload=turn_request,
                )
            )
            try:
                policy_carrier = await self._policy.generate(thaw_json(turn_request))
            except Exception as exc:
                error = RunnerDependencyError(
                    "policy invocation failed",
                    code="policy_invoke_failed",
                    **self._error_context(),
                )
                error.__cause__ = exc
                await self._raise_error(error, turn=turn)
            await self._checkpoint("after_policy", turn=turn)

            response_limit = (
                self._open_request.effective_plan.effective_capabilities.limits.response_bytes
            )
            if not isinstance(policy_carrier, Mapping):
                await self._raise_error(
                    RunnerProtocolError(
                        "policy response must be an object",
                        code="policy_response_not_object",
                        **self._error_context(),
                    ),
                    turn=turn,
                )
            try:
                frozen_response, response_size = freeze_json_object_with_size(
                    policy_carrier,
                    field_name="policy response",
                    max_depth=64,
                    max_nodes=response_limit + 1,
                    max_encoded_bytes=response_limit,
                )
            except JsonSnapshotError as exc:
                if exc.code == "encoded_bytes":
                    error = self._response_limit_error()
                else:
                    error = RunnerProtocolError(
                        "policy response is not bounded closed JSON",
                        code="policy_response_invalid",
                        **self._error_context(),
                    )
                error.__cause__ = exc
                await self._raise_error(error, turn=turn)

            raw_outputs = frozen_response.get("output")
            if type(raw_outputs) is not tuple:
                await self._raise_error(
                    RunnerProtocolError(
                        "policy response output must be a list",
                        code="policy_output_not_list",
                        **self._error_context(),
                    ),
                    turn=turn,
                )
            normalized_outputs: list[FrozenJsonObject] = []
            calls: list[FrozenJsonObject] = []
            assistant_messages: list[FrozenJsonObject] = []
            for index, item in enumerate(raw_outputs):
                if not isinstance(item, Mapping):
                    await self._raise_error(
                        RunnerProtocolError(
                            f"policy response output[{index}] must be an object",
                            code="policy_output_item_not_object",
                            **self._error_context(),
                        ),
                        turn=turn,
                    )
                output_type = item.get("type")
                if type(output_type) is not str or output_type not in {
                    "function_call",
                    "message",
                    "reasoning",
                }:
                    await self._raise_error(
                        RunnerProtocolError(
                            "policy response contains an unsupported output type",
                            code="policy_output_type_unsupported",
                            **self._error_context(),
                        ),
                        turn=turn,
                    )
                normalized_outputs.append(item)
                if output_type == "function_call":
                    calls.append(item)
                elif output_type == "message" and item.get("role") == "assistant":
                    assistant_messages.append(item)

            await self._admit_policy_outputs(
                response_size, raw_outputs, turn
            )
            all_outputs.extend(normalized_outputs)
            last_response = frozen_response
            await self._charge_transcript(
                {"event": "policy_response", "turn": turn, "payload": frozen_response},
                turn=turn,
            )
            await self._emit(
                PolicyResponseEvent(
                    sequence=0,
                    episode_id=self._open_request.episode_id,
                    effective_plan_digest=self._open_request.effective_plan_digest,
                    turn=turn,
                    response_payload=frozen_response,
                    normalized_output=tuple(normalized_outputs),
                )
            )

            observations: list[FrozenJsonObject] = []
            if frozen_response.get("incomplete_details"):
                runner_turn = RunnerTurn(
                    turn=turn,
                    policy_output=tuple(normalized_outputs),
                    observations=(),
                )
                await self._charge_turn(runner_turn)
                turns.append(runner_turn)
                termination = RunnerTermination.POLICY_INCOMPLETE
                break
            if not calls:
                runner_turn = RunnerTurn(
                    turn=turn,
                    policy_output=tuple(normalized_outputs),
                    observations=(),
                )
                await self._charge_turn(runner_turn)
                turns.append(runner_turn)
                termination = (
                    RunnerTermination.ASSISTANT_COMPLETE
                    if assistant_messages
                    else RunnerTermination.INVALID_POLICY_OUTPUT
                )
                break

            submitted = False
            for ordinal, call in enumerate(calls):
                raw_call_id = call.get("call_id") or call.get("id")
                call_id = raw_call_id.strip() if type(raw_call_id) is str else ""
                if not call_id:
                    call_id = "missing-call-id"
                await self._checkpoint(
                    "before_action", turn=turn, call_id=call_id
                )
                observation, is_submit = await self._execute_action(
                    request=request,
                    turn=turn,
                    ordinal=ordinal,
                    call=call,
                    call_id=call_id,
                )
                observations.append(observation)
                all_outputs.append(observation)
                submitted = submitted or is_submit
            runner_turn = RunnerTurn(
                turn=turn,
                policy_output=tuple(normalized_outputs),
                observations=tuple(observations),
            )
            await self._charge_turn(runner_turn)
            turns.append(runner_turn)
            if submitted:
                termination = RunnerTermination.SUBMITTED
                break

        if last_response is None:
            await self._raise_error(
                RunnerProtocolError(
                    "episode ended before the policy produced a response",
                    code="no_policy_response",
                    **self._error_context(),
                )
            )
        await self._checkpoint("after_loop", turn=turn_count or None)
        response = thaw_json(last_response)
        response["output"] = thaw_json(all_outputs)
        original_params = thaw_json(request.responses_create_params)
        await self._commit_completion(termination, turn_count)
        return RunnerResult(
            episode_id=self._open_request.episode_id,
            effective_plan_digest=self._open_request.effective_plan_digest,
            original_request=original_params,
            response=response,
            termination=termination,
            turn_count=turn_count,
            turns=tuple(turns),
            events=tuple(self._events),
        )

    async def _execute_action(
        self,
        *,
        request: TerminalRunRequest,
        turn: int,
        ordinal: int,
        call: Mapping[str, Any],
        call_id: str,
    ) -> tuple[FrozenJsonObject, bool]:
        raw_name = call.get("name")
        name = raw_name.strip() if type(raw_name) is str else ""
        raw_arguments = call.get("arguments", "{}")
        arguments_json = raw_arguments if type(raw_arguments) is str else "<invalid>"
        await self._charge_transcript(
            {
                "event": "tool_call",
                "turn": turn,
                "ordinal": ordinal,
                "call_id": call_id,
                "tool": name,
                "arguments": arguments_json,
            },
            turn=turn,
            call_id=call_id,
        )
        await self._emit(
            ToolCallEvent(
                sequence=0,
                episode_id=self._open_request.episode_id,
                effective_plan_digest=self._open_request.effective_plan_digest,
                turn=turn,
                ordinal=ordinal,
                call_id=call_id,
                tool_name=name,
                arguments_json=arguments_json,
            )
        )
        submitted = False
        error_type: str | None = None
        error_code: str | None = None
        workspace_started = False
        try:
            if type(raw_arguments) is not str:
                raise ValueError("arguments must be a JSON string")
            arguments = json.loads(raw_arguments)
            if type(arguments) is not dict:
                raise ValueError("arguments JSON must decode to an object")
            method_name, args, kwargs, submitted = self._prepare_action(
                name, arguments, request.limits.action_timeout_seconds
            )
            if method_name is None:
                result: Any = {"accepted": True, "result": arguments["result"]}
            else:
                workspace_started = True
                try:
                    if method_name == "run_shell":
                        method = self._workspace.run_shell
                    elif method_name == "read_text":
                        method = self._workspace.read_text
                    elif method_name == "write_text":
                        method = self._workspace.write_text
                    else:
                        method = self._workspace.list_files
                    awaitable = method(*args, **kwargs)
                    async with asyncio.timeout(request.limits.action_timeout_seconds):
                        result = await awaitable
                except TimeoutError:
                    error_type = "TimeoutError"
                    error_code = "action_timeout"
                    result = {
                        "code": error_code,
                        "error": "TimeoutError: workspace action timed out",
                    }
                    submitted = False
            if error_code is None:
                try:
                    frozen_result = freeze_json_object(
                        result,
                        field_name="workspace result",
                        max_depth=64,
                        max_nodes=request.limits.max_observation_chars * 4 + 1,
                        max_encoded_bytes=request.limits.max_observation_chars * 12,
                    )
                except (JsonSnapshotError, TypeError) as exc:
                    error_type = "ValueError"
                    error_code = "workspace_result_invalid"
                    result = {
                        "code": error_code,
                        "error": "ValueError: workspace result was invalid",
                    }
                    submitted = False
                else:
                    result = frozen_result
        except Exception as exc:
            if workspace_started:
                error_type = "WorkspaceActionError"
                error_code = "workspace_action_failed"
                public_detail = "workspace action failed"
            else:
                error_type = type(exc).__name__
                error_code = "invalid_arguments"
                public_detail = str(exc)
            result = {
                "code": error_code,
                "error": f"{error_type}: {public_detail}",
            }
            submitted = False

        output = _json_observation(result, request.limits.max_observation_chars)
        observation = {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }
        await self._charge_transcript(
            {
                "event": "tool_observation",
                "turn": turn,
                "ordinal": ordinal,
                "call_id": call_id,
                "tool": name,
                "observation": observation,
                "submitted": submitted,
                "error_type": error_type,
            },
            turn=turn,
            call_id=call_id,
        )
        await self._emit(
            ToolObservationEvent(
                sequence=0,
                episode_id=self._open_request.episode_id,
                effective_plan_digest=self._open_request.effective_plan_digest,
                turn=turn,
                ordinal=ordinal,
                call_id=call_id,
                tool_name=name,
                observation=observation,
                submitted=submitted,
                error_type=error_type,
            )
        )
        frozen_observation = await self._admit_observation(observation, turn)
        return frozen_observation, submitted

    def _prepare_action(
        self, name: str, arguments: dict[str, Any], timeout_seconds: int
    ) -> tuple[str | None, tuple[Any, ...], dict[str, Any], bool]:
        allowed: dict[str, frozenset[str]] = {
            "shell": frozenset({"command", "timeout_seconds"}),
            "read_file": frozenset({"path", "offset", "limit"}),
            "write_file": frozenset({"path", "content"}),
            "list_files": frozenset({"path", "depth"}),
            "submit": frozenset({"result"}),
        }
        if name not in allowed:
            raise ValueError(
                f"tool {name!r} is not admitted by adapter {TERMINAL_ADAPTER_ID!r}"
            )
        unexpected = set(arguments) - allowed[name]
        if unexpected:
            raise ValueError("arguments contain unsupported properties")
        if name == "shell":
            command = _required_text(arguments, "command")
            requested = _optional_integer(arguments, "timeout_seconds", timeout_seconds)
            timeout = max(1, min(requested, timeout_seconds))
            return "run_shell", (command,), {"timeout": timeout}, False
        if name == "read_file":
            path = _required_text(arguments, "path")
            offset = max(0, _optional_integer(arguments, "offset", 0))
            limit = (
                max(0, _required_integer(arguments, "limit"))
                if "limit" in arguments
                else None
            )
            return "read_text", (path,), {"offset": offset, "limit": limit}, False
        if name == "write_file":
            path = _required_text(arguments, "path")
            content = _required_string(arguments, "content")
            return "write_text", (path, content), {}, False
        if name == "list_files":
            path = _required_text(arguments, "path")
            depth = max(1, min(_optional_integer(arguments, "depth", 1), 8))
            return "list_files", (path,), {"depth": depth}, False
        result = _required_string(arguments, "result")
        arguments["result"] = result
        return None, (), {}, True

    async def _checkpoint(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        while True:
            async with self._state_lock:
                if self._publication_failure is not None:
                    raise self._failed_state_error()
                pending = self._pending_cancellation_future
                cancellation = self._cancellation
            if pending is None:
                break
            await asyncio.shield(pending)

        external_error: Exception | None = None
        if cancellation is None or not cancellation.requested:
            try:
                self._cancellation_probe.raise_if_cancelled(
                    checkpoint, turn=turn, call_id=call_id
                )
            except Exception as exc:
                external_error = exc
                cancellation = await self._request_cancellation(
                    "external cancellation requested", close=False
                )
        if cancellation is None or not cancellation.requested:
            return
        reason = cancellation.reason
        observed = RunnerCancellation(
            reason=reason,
            requested=True,
            observed_checkpoint=checkpoint,
            turn=turn,
            call_id=call_id,
        )
        async with self._emission_lock:
            await self._drain_pending_cancellation_locked()
            async with self._state_lock:
                if self._publication_failure is not None:
                    raise self._failed_state_error()
                current = self._cancellation
                if current is None or not current.requested:
                    return
            await self._publish_locked(
                RunnerCancellationObservedEvent(
                    sequence=0,
                    episode_id=self._open_request.episode_id,
                    effective_plan_digest=self._open_request.effective_plan_digest,
                    reason=reason,
                    checkpoint=checkpoint,
                    turn=turn,
                    call_id=call_id,
                )
            )
            async with self._state_lock:
                self._cancellation = observed
                if self._phase == "running":
                    self._phase = "cancelled"
        cancelled = RunnerCancelled(observed, **self._error_context())
        if external_error is not None:
            cancelled.__cause__ = external_error
        raise cancelled

    async def _commit_completion(
        self, termination: RunnerTermination, turn_count: int
    ) -> None:
        async with self._emission_lock:
            await self._drain_pending_cancellation_locked()
            async with self._state_lock:
                if self._publication_failure is not None:
                    raise self._failed_state_error()
                cancellation = self._cancellation
                if cancellation is not None and cancellation.requested:
                    must_cancel = True
                else:
                    self._phase = "completing"
                    must_cancel = False
            if not must_cancel:
                await self._publish_locked(
                    RunnerTerminationEvent(
                        sequence=0,
                        episode_id=self._open_request.episode_id,
                        effective_plan_digest=self._open_request.effective_plan_digest,
                        turns=turn_count,
                        reason=termination,
                    )
                )
                async with self._state_lock:
                    self._phase = "completed"
                return
        await self._checkpoint("after_loop", turn=turn_count or None)

    async def _emit(self, event: RunnerEvent) -> None:
        async with self._emission_lock:
            await self._emit_locked(event)

    async def _emit_locked(self, event: RunnerEvent) -> None:
        await self._publish_locked(event)
        await self._drain_pending_cancellation_locked()

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
                    **self._error_context(),
                )
                await self._poison_publication(error)
                raise error
        finally:
            _EVENT_SINK_SESSION.reset(token)
        self._events.append(sequenced)
        self._sequence += 1

    async def _drain_pending_cancellation_locked(self) -> None:
        async with self._state_lock:
            reason = self._pending_cancellation_reason
            future = self._pending_cancellation_future
        if reason is None:
            return
        assert future is not None
        await self._publish_locked(
            RunnerCancellationRequestedEvent(
                sequence=0,
                episode_id=self._open_request.episode_id,
                effective_plan_digest=self._open_request.effective_plan_digest,
                reason=reason,
            )
        )
        cancellation = RunnerCancellation(reason=reason, requested=True)
        async with self._state_lock:
            if self._pending_cancellation_future is future:
                self._pending_cancellation_reason = None
                self._pending_cancellation_future = None
                self._cancellation = cancellation
        if not future.done():
            future.set_result(cancellation)

    async def _poison_publication(self, error: RunnerEventSinkError) -> None:
        async with self._state_lock:
            future = self._pending_cancellation_future
            self._pending_cancellation_reason = None
            self._pending_cancellation_future = None
            self._cancellation = None
            self._publication_failure = error
            self._phase = "failed"
        if future is not None and not future.done():
            future.set_exception(error)

    async def _raise_error(
        self,
        error: RunnerError,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        await self._emit(
            RunnerErrorEvent(
                sequence=0,
                episode_id=self._open_request.episode_id,
                effective_plan_digest=self._open_request.effective_plan_digest,
                category=error.category,
                code=error.code,
                message=str(error),
                turn=turn,
                call_id=call_id,
            )
        )
        error.events_so_far = tuple(self._events)
        raise error

    async def _charge_transcript(
        self,
        item: Mapping[str, Any],
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        comma_bytes = 1 if self._transcript_items else 0
        remaining = self._transcript_limit - self._transcript_size - comma_bytes
        try:
            if remaining < 1:
                raise JsonSnapshotError(
                    "encoded_bytes",
                    encoded_bytes_examined=self._transcript_limit + 1,
                )
            _, encoded_size = freeze_json_object_with_size(
                item,
                field_name="transcript item",
                max_depth=64,
                max_nodes=self._transcript_limit + 1,
                max_encoded_bytes=remaining,
            )
        except JsonSnapshotError as exc:
            error = RunnerProtocolError(
                "runner transcript exceeded the effective transcript byte limit",
                code="transcript_bytes_exceeded",
                **self._error_context(),
            )
            error.__cause__ = exc
            await self._raise_error(error, turn=turn, call_id=call_id)
        self._transcript_size += comma_bytes + encoded_size
        self._transcript_items += 1

    async def _charge_turn(self, turn: RunnerTurn) -> None:
        await self._charge_transcript(
            {
                "turn": {
                    "turn": turn.turn,
                    "policy_output": turn.policy_output,
                    "observations": turn.observations,
                }
            },
            turn=turn.turn,
        )

    async def _admit_policy_outputs(
        self,
        response_size: int,
        raw_outputs: tuple[Any, ...],
        turn: int,
    ) -> None:
        _, raw_output_size = freeze_json_with_size(
            raw_outputs,
            field_name="policy response output",
            max_depth=64,
            max_nodes=self._response_limit + 1,
            max_encoded_bytes=self._response_limit,
        )
        candidate_output_size = self._response_output_size
        if raw_outputs:
            if self._response_output_items:
                candidate_output_size += 1
            candidate_output_size += raw_output_size - 2
        candidate_base_size = response_size - raw_output_size
        if candidate_base_size + candidate_output_size > self._response_limit:
            error = self._response_limit_error()
            error.__cause__ = JsonSnapshotError(
                "encoded_bytes",
                encoded_bytes_examined=self._response_limit + 1,
            )
            await self._raise_error(error, turn=turn)
        self._response_base_size = candidate_base_size
        self._response_output_size = candidate_output_size
        self._response_output_items += len(raw_outputs)

    async def _admit_observation(
        self, observation: Mapping[str, Any], turn: int
    ) -> FrozenJsonObject:
        comma_bytes = 1 if self._response_output_items else 0
        remaining = (
            self._response_limit
            - self._response_base_size
            - self._response_output_size
            - comma_bytes
        )
        try:
            if remaining < 1:
                raise JsonSnapshotError(
                    "encoded_bytes",
                    encoded_bytes_examined=self._response_limit + 1,
                )
            frozen, observation_size = freeze_json_object_with_size(
                observation,
                field_name="accumulated response observation",
                max_depth=64,
                max_nodes=self._response_limit + 1,
                max_encoded_bytes=remaining,
            )
        except JsonSnapshotError as exc:
            error = self._response_limit_error()
            error.__cause__ = exc
            await self._raise_error(error, turn=turn)
        self._response_output_size += comma_bytes + observation_size
        self._response_output_items += 1
        return frozen

    def _response_limit_error(self) -> RunnerProtocolError:
        return RunnerProtocolError(
            "policy response exceeded the effective response byte limit",
            code="response_bytes_exceeded",
            **self._error_context(),
        )

    @property
    def _response_limit(self) -> int:
        return self._open_request.effective_plan.effective_capabilities.limits.response_bytes

    @property
    def _transcript_limit(self) -> int:
        return self._open_request.effective_plan.effective_capabilities.limits.transcript_bytes

    def _error_context(self) -> dict[str, Any]:
        return {
            "episode_id": self._open_request.episode_id,
            "effective_plan_digest": self._open_request.effective_plan_digest,
            "events_so_far": tuple(self._events),
        }

    def _state_error(self, code: str, message: str) -> RunnerStateError:
        return RunnerStateError(message, code=code, **self._error_context())

    def _failed_state_error(self) -> RunnerStateError:
        error = self._state_error(
            "session_failed", "runner session event publication failed"
        )
        error.__cause__ = self._publication_failure
        return error


def _required_text(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_string(arguments: Mapping[str, Any], key: str) -> str:
    if key not in arguments or type(arguments[key]) is not str:
        raise ValueError(f"{key} must be a string")
    return arguments[key]


def _required_integer(arguments: Mapping[str, Any], key: str) -> int:
    if key not in arguments or type(arguments[key]) is not int:
        raise ValueError(f"{key} must be an integer")
    return arguments[key]


def _optional_integer(
    arguments: Mapping[str, Any], key: str, default: int
) -> int:
    if key not in arguments:
        return default
    return _required_integer(arguments, key)


def _json_observation(result: Mapping[str, Any], limit: int) -> str:
    raw = json.dumps(thaw_json(result), sort_keys=True, separators=(",", ":"))
    # json.dumps uses ensure_ascii=True, so compact output has one byte per character.
    if len(raw) <= limit:
        return raw
    compact = '{"truncated":true}'
    if limit < len(compact):
        raise ValueError("observation limit cannot fit the truncation envelope")
    prefix = '{"truncated":true,"preview":"'
    suffix = '"}'
    if limit < len(prefix) + len(suffix):
        return compact
    raw_preview_budget = max(0, limit - 64)
    escaped_preview_budget = limit - len(prefix) - len(suffix)
    raw_units = 0
    escaped_bytes = 0
    for character in raw:
        encoded_bytes = 2 if character in {'"', "\\"} else 1
        if (
            raw_units + 1 > raw_preview_budget
            or escaped_bytes + encoded_bytes > escaped_preview_budget
        ):
            break
        raw_units += 1
        escaped_bytes += encoded_bytes
    return json.dumps(
        {"truncated": True, "preview": raw[:raw_units]}, separators=(",", ":")
    )
