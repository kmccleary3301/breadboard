from __future__ import annotations

import asyncio
import copy
import json
import hashlib
from pathlib import Path
from collections.abc import Mapping
from typing import Any

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.runners import conductor as conductor_module
from breadboard.rl.harness.runners.base import (
    PolicyRequestEvent,
    PolicyResponseEvent,
    PolicyRuntimeRequestEvent,
    PolicyRuntimeResponseEvent,
    PolicyRuntimeInvokeResult,
    RunnerCancellationObservedEvent,
    RunnerCancellationRequestedEvent,
    RunnerCancelled,
    RunnerDependencyError,
    RunnerErrorEvent,
    RunnerEvent,
    RunnerEventSinkError,
    RunnerOpenRequest,
    RunnerPlanError,
    RunnerPolicyBindingError,
    RunnerProtocolError,
    RunnerRequestError,
    RunnerStateError,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerToolBinding,
    ToolCallEvent,
    ToolObservationEvent,
    thaw_json,
)
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_IMPLEMENTATION_DIGEST,
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
    ConductorRunRequest,
    PolicyRuntimeBinding,
)
from tests.rl.harness.test_runner_policy_runtime import (
    IMPLEMENTATION_DIGEST,
    BlockingClosePolicyClient,
    InterruptiblePolicyClient,
    RecordingPolicyClient,
    _digest,
    _empty_semantics,
    _independent_digest,
    _observation,
    _plan,
    _policy_capabilities,
    _response,
)


class RecordingToolPort:
    def __init__(
        self,
        bindings: tuple[RunnerToolBinding, ...] = (),
        *,
        results: list[Mapping[str, Any]] | None = None,
    ) -> None:
        self._bindings = bindings
        self.results = list(results or [])
        self.calls: list[tuple[str, dict[str, Any], int]] = []
        self.binding_reads = 0
        self.error: BaseException | None = None

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        self.binding_reads += 1
        return self._bindings

    async def invoke_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]:
        self.calls.append((tool_id, dict(arguments), timeout_ms))
        if self.error is not None:
            raise self.error
        return self.results.pop(0)


class RecordingCancellationProbe:
    def __init__(self, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.checkpoints: list[tuple[str, int | None, str | None]] = []

    def raise_if_cancelled(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        self.checkpoints.append((checkpoint, turn, call_id))
        if checkpoint == self.fail_at:
            raise RuntimeError("external cancellation sentinel")


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[RunnerEvent] = []
        self.reject_type: type[Any] | None = None

    async def emit(self, event: RunnerEvent) -> None:
        if self.reject_type is not None and type(event) is self.reject_type:
            raise RuntimeError("event sink secret sentinel")
        self.events.append(event)


class BlockingEventSink(RecordingEventSink):
    def __init__(self, blocked_type: type[Any]) -> None:
        super().__init__()
        self.blocked_type = blocked_type
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: RunnerEvent) -> None:
        if type(event) is self.blocked_type:
            self.entered.set()
            await _within_timeout(self.release.wait())
        self.events.append(event)


class ReentrantLifecycleSink(RecordingEventSink):
    def __init__(self, trigger: type[RunnerEvent], operation: str) -> None:
        super().__init__()
        self.trigger = trigger
        self.operation = operation
        self.session: Any = None
        self.outcome: Any = None
        self.called = False

    async def emit(self, event: RunnerEvent) -> None:
        self.events.append(event)
        if type(event) is self.trigger and not self.called:
            self.called = True
            if self.operation == "cancel":
                self.outcome = await self.session.cancel("sink-requested-stop")
            elif self.operation == "close":
                self.outcome = await self.session.close()
            else:
                raise AssertionError(self.operation)


class RejectCancellationRequestOnceSink(RecordingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.rejected = False

    async def emit(self, event: RunnerEvent) -> None:
        if type(event) is RunnerCancellationRequestedEvent and not self.rejected:
            self.rejected = True
            raise RuntimeError("Authorization: Bearer cancellation-sink-secret")
        self.events.append(event)


async def _advance_event_loop_once() -> None:
    loop = asyncio.get_running_loop()
    advanced: asyncio.Future[None] = loop.create_future()
    loop.call_soon(advanced.set_result, None)
    await _within_timeout(advanced)


async def _within_timeout(awaitable: Any) -> Any:
    async with asyncio.timeout(2):
        return await awaitable


def _sync_root_semantics(semantic: dict[str, Any]) -> dict[str, Any]:
    root = semantic["config_nodes"][0]["semantic_config"]
    for field_name in tuple(root):
        if field_name == "modes":
            root[field_name] = copy.deepcopy(semantic["modes"])
        elif field_name == "optimizer_mutable_pointers":
            root[field_name] = copy.deepcopy(semantic["optimizer_mutable_pointers"])
        elif field_name == "team":
            root[field_name] = copy.deepcopy(semantic["team"])
        else:
            root[field_name] = copy.deepcopy(semantic[field_name])
    return semantic


def _tool_semantics(
    observation: c.PolicyCapabilityObservation,
    *,
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    tool_id = "read-file"
    variant = semantic["prompts"]["variants"][0]
    variant["effective_tool_ids"] = [tool_id]
    variant["tool_catalog"]["effective_tool_ids"] = [tool_id]
    variant["tool_catalog"]["text"] = "TOOL CATALOG: read_file"
    variant["tool_catalog"]["text_digest"] = _digest(variant["tool_catalog"]["text"])
    variant["tool_set_digest"] = _independent_digest(
        {"schema": "bb.tool-set.v1", "tool_ids": [tool_id]}
    )
    semantic["modes"][0]["enabled_tool_ids"] = [tool_id]
    semantic["loop"]["sequence"] = [
        {"condition": None, "mode_id": "build"},
        {"condition": None, "mode_id": "build"},
    ]
    semantic["tools"] = {
        "aliases": [["read", tool_id]],
        "applied_overlays": [],
        "binding_requests": [
            {
                "binding_id": "binding:read",
                "binding_kind": "server",
                "environment_selector": None,
                "execution_profile": "sandboxed",
                "exposure": "model",
                "fallback_binding_ids": [],
                "placement": "server",
                "support_status": "supported",
                "tool_id": tool_id,
            }
        ],
        "definitions": [
            {
                "dependencies": [],
                "description": "Read an admitted logical path.",
                "execution": {"blocking": False, "max_per_turn": 2},
                "manipulations": ["read"],
                "model_name": "read_file",
                "parameters": (
                    copy.deepcopy(parameters)
                    if parameters is not None
                    else [
                        {
                            "default_value": None,
                            "description": "Logical path",
                            "examples": ["src/main.py"],
                            "has_default": False,
                            "name": "path",
                            "required": True,
                            "schema": {"minLength": 1, "type": "string"},
                            "validation_rules": {},
                        }
                    ]
                ),
                "performance_data": {"latency_class": "low"},
                "preferred_formats": ["json"],
                "provider_routing": {observation.provider_id: {"strict": True}},
                "source_dependency": None,
                "syntax_formats_supported": ["json"],
                "tool_id": tool_id,
                "type_id": "server",
                "use_cases": ["inspection"],
            }
        ],
        "dialect_policy": {},
        "mark_task_complete": False,
        "packs": [],
        "registry_members": [],
        "selected_tool_ids": [tool_id],
    }
    return _sync_root_semantics(semantic)


def _tool_grant(tool_id: str = "read-file") -> c.ToolGrant:
    return c.ToolGrant(
        tool_id=tool_id,
        implementation_digest=_digest(f"{tool_id}-implementation"),
        capability_ids=(),
    )


def _tool_binding(tool_id: str = "read-file") -> RunnerToolBinding:
    grant = _tool_grant(tool_id)
    return RunnerToolBinding(
        tool_id=grant.tool_id,
        implementation_digest=grant.implementation_digest,
        capability_ids=grant.capability_ids,
    )


def _plan_with_tools(
    observation: c.PolicyCapabilityObservation,
    *,
    semantics: Mapping[str, Any] | None = None,
    tool_ids: tuple[str, ...] = ("read-file",),
    policy_slot_ids: tuple[str, ...] | None = None,
    limit_updates: Mapping[str, int] | None = None,
) -> c.EffectiveExecutionPlan:
    return _plan(observation=observation,
    semantics=semantics or _tool_semantics(observation),
    tools=tuple(_tool_grant(tool_id) for tool_id in tool_ids),
    policy_slot_ids=policy_slot_ids,
    limit_updates=limit_updates, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)


async def _open(
    *,
    observation: c.PolicyCapabilityObservation | None = None,
    plan: c.EffectiveExecutionPlan | None = None,
    client: RecordingPolicyClient | None = None,
    tools: RecordingToolPort | None = None,
    cancellation: RecordingCancellationProbe | None = None,
    sink: RecordingEventSink | None = None,
    episode_id: str = "episode-a",
) -> tuple[Any, RecordingPolicyClient, RecordingToolPort, RecordingCancellationProbe, RecordingEventSink, PolicyRuntimeBinding]:
    resolved_observation = observation or _observation()
    resolved_plan = plan or _plan(observation=resolved_observation, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
    resolved_client = client or RecordingPolicyClient(resolved_observation)
    resolved_tools = tools or RecordingToolPort()
    resolved_cancellation = cancellation or RecordingCancellationProbe()
    resolved_sink = sink or RecordingEventSink()
    open_request = RunnerOpenRequest(episode_id=episode_id, effective_plan=resolved_plan)
    binding = PolicyRuntimeBinding(open_request, resolved_client)
    adapter = ConductorAdapter(CONDUCTOR_RUNTIME_ABI)
    session = await adapter.open(
        open_request,
        policy=binding,
        workspace=resolved_tools,
        cancellation=resolved_cancellation,
        events=resolved_sink,
    )
    return (
        session,
        resolved_client,
        resolved_tools,
        resolved_cancellation,
        resolved_sink,
        binding,
    )


def _function_call(*, name: str = "read_file", call_id: str = "call-1", arguments: str = '{"path":"src/main.py"}') -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "call_id": call_id,
        "arguments": arguments,
    }


def _parameter(
    name: str,
    schema: Mapping[str, Any],
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "default_value": None,
        "description": f"Compiled {name} parameter.",
        "examples": [],
        "has_default": False,
        "name": name,
        "required": required,
        "schema": copy.deepcopy(dict(schema)),
        "validation_rules": {},
    }


def _multi_mode_semantics(
    observation: c.PolicyCapabilityObservation,
) -> dict[str, Any]:
    semantic = _tool_semantics(observation)
    read_definition = semantic["tools"]["definitions"][0]
    write_definition = copy.deepcopy(read_definition)
    write_definition.update(
        {
            "description": "Write an admitted logical path.",
            "manipulations": ["write"],
            "model_name": "write_file",
            "tool_id": "write-file",
            "use_cases": ["mutation"],
        }
    )
    semantic["tools"]["definitions"].append(write_definition)
    write_binding = copy.deepcopy(semantic["tools"]["binding_requests"][0])
    write_binding.update({"binding_id": "binding:write", "tool_id": "write-file"})
    semantic["tools"]["binding_requests"].append(write_binding)
    semantic["tools"]["aliases"] = [
        ["read", "read-file"],
        ["inspect", "read-file"],
        ["write", "write-file"],
    ]
    semantic["tools"]["selected_tool_ids"] = ["read-file", "write-file"]

    build_variant = semantic["prompts"]["variants"][0]
    build_variant["effective_tool_ids"] = ["read-file"]
    build_variant["tool_catalog"]["effective_tool_ids"] = ["read-file"]
    build_variant["tool_set_digest"] = _independent_digest(
        {"schema": "bb.tool-set.v1", "tool_ids": ["read-file"]}
    )
    review_variant = copy.deepcopy(build_variant)
    review_variant["mode_id"] = "review"
    review_variant["system"]["text"] = "Review system instruction."
    review_variant["system"]["text_digest"] = _digest("Review system instruction.")
    review_variant["per_turn"]["text"] = "Review per-turn instruction."
    review_variant["per_turn"]["text_digest"] = _digest("Review per-turn instruction.")
    review_variant["effective_tool_ids"] = ["write-file", "read-file"]
    review_variant["tool_catalog"]["effective_tool_ids"] = [
        "write-file",
        "read-file",
    ]
    review_variant["tool_catalog"]["text"] = "TOOL CATALOG: write_file, read_file"
    review_variant["tool_catalog"]["text_digest"] = _digest(
        "TOOL CATALOG: write_file, read_file"
    )
    review_variant["tool_set_digest"] = _independent_digest(
        {
            "schema": "bb.tool-set.v1",
            "tool_ids": ["write-file", "read-file"],
        }
    )
    review_variant["variant_id"] = _digest("review-variant")
    semantic["prompts"]["variants"].append(review_variant)
    semantic["modes"] = [
        {
            "dialect_ids": [],
            "disabled_tool_ids": ["write-file"],
            "enabled": True,
            "enabled_tool_ids": ["read-file"],
            "mode_id": "build",
            "prompt": {"literal": "Per-turn compiled instruction."},
            "prompt_source_id": "mode:build",
        },
        {
            "dialect_ids": [],
            "disabled_tool_ids": [],
            "enabled": True,
            "enabled_tool_ids": ["write-file", "read-file"],
            "mode_id": "review",
            "prompt": {"literal": "Review per-turn instruction."},
            "prompt_source_id": "mode:review",
        },
    ]
    semantic["loop"]["sequence"] = [
        {"condition": None, "mode_id": "build"},
        {"condition": None, "mode_id": "review"},
    ]
    return _sync_root_semantics(semantic)


def _two_model_semantics(
    observation: c.PolicyCapabilityObservation,
) -> dict[str, Any]:
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    default_model = semantic["providers"]["models"][0]
    shadow_model = copy.deepcopy(default_model)
    shadow_model.update(
        {
            "display_name": "Shadow model",
            "model_id": "shadow-model",
            "policy_slot_id": "slot-shadow-model",
        }
    )
    default_slot = semantic["providers"]["policy_slots"][0]
    shadow_slot = copy.deepcopy(default_slot)
    shadow_slot.update(
        {
            "model_id": "shadow-model",
            "slot_id": "slot-shadow-model",
        }
    )
    default_variant = semantic["prompts"]["variants"][0]
    shadow_variant = copy.deepcopy(default_variant)
    shadow_variant.update(
        {
            "model_id": "shadow-model",
            "variant_id": _digest("shadow-variant"),
        }
    )
    shadow_variant["system"]["text"] = "Shadow system instruction."
    shadow_variant["system"]["text_digest"] = _digest("Shadow system instruction.")
    semantic["providers"]["models"] = [shadow_model, default_model]
    semantic["providers"]["policy_slots"] = [default_slot, shadow_slot]
    semantic["prompts"]["variants"] = [shadow_variant, default_variant]
    return _sync_root_semantics(semantic)


def _schema_parameters() -> list[dict[str, Any]]:
    return [
        _parameter("short_code", {"type": "string", "minLength": 2}),
        _parameter("capped", {"type": "string", "maxLength": 4}),
        _parameter("patterned", {"type": "string", "pattern": "^[a-z]+$"}),
        _parameter("enum_value", {"type": "string", "enum": ["alpha", "bravo"]}),
        _parameter("const_value", {"type": "string", "const": "fixed"}),
        _parameter(
            "integer_value",
            {"type": "integer", "minimum": 0, "maximum": 10},
        ),
        _parameter(
            "ratio",
            {
                "type": "number",
                "exclusiveMinimum": 0,
                "exclusiveMaximum": 1,
                "multipleOf": 0.25,
            },
        ),
        _parameter(
            "tags",
            {
                "type": "array",
                "items": {"type": "string", "enum": ["a", "b", "c"]},
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
            },
        ),
        _parameter(
            "nested",
            {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "levels": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["enabled", "levels"],
                "additionalProperties": False,
            },
        ),
        _parameter("nothing", {"type": "null"}),
    ]


def _valid_schema_arguments() -> dict[str, Any]:
    return {
        "short_code": "ok",
        "capped": "four",
        "patterned": "lower",
        "enum_value": "alpha",
        "const_value": "fixed",
        "integer_value": 4,
        "ratio": 0.5,
        "tags": ["a", "b"],
        "nested": {"enabled": True, "levels": [1, 2]},
        "nothing": None,
    }


def _encoded_json_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


async def _assert_open_rejected(
    *,
    observation: c.PolicyCapabilityObservation,
    plan: c.EffectiveExecutionPlan,
    code: str,
    bindings: tuple[RunnerToolBinding, ...] = (),
) -> None:
    client = RecordingPolicyClient(observation)
    tools = RecordingToolPort(bindings)
    probe = RecordingCancellationProbe()
    sink = RecordingEventSink()
    request = RunnerOpenRequest(episode_id="episode-a", effective_plan=plan)
    binding = PolicyRuntimeBinding(request, client)

    with pytest.raises(RunnerPlanError) as captured:
        await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            request,
            policy=binding,
            workspace=tools,
            cancellation=probe,
            events=sink,
        )

    assert captured.value.code == code
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0
    assert tools.binding_reads == 0
    assert tools.calls == []
    assert probe.checkpoints == []
    assert sink.events == []


def test_conductor_request_snapshots_task_data_and_exposes_no_authority_fields() -> None:
    task_input = {"query": "fix it", "nested": [{"value": "original"}]}
    context = {"dataset": "train-a"}
    request = ConductorRunRequest(task_input, context)

    task_input["nested"][0]["value"] = "mutated"
    context["dataset"] = "mutated"

    assert thaw_json(request.task_input) == {
        "query": "fix it",
        "nested": [{"value": "original"}],
    }
    assert thaw_json(request.context) == {"dataset": "train-a"}


@pytest.mark.parametrize(
    "authority_field",
    [
        "model",
        "provider",
        "route_id",
        "credential_handle_id",
        "tools",
        "instructions",
        "policy_slot_id",
        "checkpoint_digest",
        "limits",
        "runtime_abi",
    ],
)
def test_conductor_request_rejects_every_caller_authority_override_keyword(
    authority_field: str,
) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        ConductorRunRequest({"query": "work"}, **{authority_field: "hostile"})


@pytest.mark.parametrize(
    "task_input",
    [
        "string-is-not-the-task-object",
        {"model": object()},
        {"provider": float("nan")},
    ],
)
def test_conductor_request_rejects_open_or_wrong_carriers(task_input: Any) -> None:
    with pytest.raises(RunnerRequestError) as captured:
        ConductorRunRequest(task_input)
    assert captured.value.code == "request_authority_invalid"


async def test_conductor_projects_exact_prompt_model_context_and_trainable_values_and_preserves_response() -> None:
    observation = _observation()
    response = _response()
    client = RecordingPolicyClient(observation, responses=[response])
    session, _, tools, probe, sink, binding = await _open(
        observation=observation,
        client=client,
    )
    task_input = {"query": "repair target", "model": "caller-model-is-data"}
    context = {"dataset": "batch-7"}

    result = await session.run(ConductorRunRequest(task_input, context))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert result.turn_count == 1
    assert thaw_json(result.response) == response
    assert thaw_json(result.original_request) == {
        "task_input": task_input,
        "context": context,
    }
    assert len(client.requests) == 1
    final_request = thaw_json(client.requests[0].request_payload)
    assert final_request == {
        "temperature": 0.25,
        "seed": 17,
        "model": observation.model_id,
        "instructions": "System compiled instruction.\n\nNo tools admitted.",
        "input": [
            {"role": "developer", "content": "Per-turn compiled instruction."},
            {"role": "user", "content": task_input},
        ],
        "tools": [],
        "metadata": {"task_context": context},
    }
    visible_request = json.dumps(final_request, sort_keys=True)
    assert observation.credential_handle_id not in visible_request
    assert observation.credential_handle_version_digest not in visible_request
    assert observation.route_revision_digest not in visible_request
    assert client.requests[0].request_digest == _independent_digest(final_request)
    assert binding.first_request_digest == client.requests[0].request_digest
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerTerminationEvent,
    ]
    runtime_request = sink.events[1]
    assert isinstance(runtime_request, PolicyRuntimeRequestEvent)
    assert thaw_json(runtime_request.trainable_values) == {"/params/temperature": 0.25}
    assert runtime_request.binding_digest == binding.binding_digest
    assert runtime_request.policy_slot_id == "slot-model-a"
    assert runtime_request.request_digest == client.requests[0].request_digest
    assert runtime_request.first_request_digest == binding.first_request_digest
    runtime_response = sink.events[2]
    assert isinstance(runtime_response, PolicyRuntimeResponseEvent)
    assert runtime_response.binding_digest == binding.binding_digest
    assert runtime_response.policy_slot_id == "slot-model-a"
    assert runtime_response.request_digest == client.requests[0].request_digest
    assert runtime_response.response_digest == _independent_digest(response)
    assert tools.calls == []
    assert ("before_policy", 1, None) in probe.checkpoints
    assert ("after_policy", 1, None) in probe.checkpoints
    await session.close()
    assert client.close_calls == 1


async def test_conductor_dispatches_compiled_model_name_to_admitted_tool_id_and_preserves_injection_order() -> None:
    observation = _observation()
    first = _response()
    first["output"] = [_function_call()]
    second = _response()
    client = RecordingPolicyClient(observation, responses=[first, second])
    plan = _plan_with_tools(observation)
    tools = RecordingToolPort((_tool_binding(),), results=[{"content": "file bytes", "ok": True}])
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=plan,
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "inspect"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert result.turn_count == 2
    assert tools.calls == [("read-file", {"path": "src/main.py"}, 9_000)]
    first_request = thaw_json(client.requests[0].request_payload)
    assert first_request["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read an admitted logical path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "minLength": 1,
                        "type": "string",
                        "description": "Logical path",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]
    second_input = thaw_json(client.requests[1].request_payload)["input"]
    assert second_input[:2] == [
        {"role": "developer", "content": "Per-turn compiled instruction."},
        {"role": "user", "content": {"query": "inspect"}},
    ]
    assert second_input[2:] == [
        _function_call(),
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"content":"file bytes","ok":true}',
        },
    ]
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        ToolCallEvent,
        ToolObservationEvent,
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerTerminationEvent,
    ]
    assert sink.events[4].tool_name == "read_file"
    assert sink.events[5].tool_name == "read_file"
    assert thaw_json(sink.events[5].observation) == {"content": "file bytes", "ok": True}
    await session.close()
    assert client.close_calls == 1

async def test_conductor_executes_ordered_modes_with_exact_prompts_and_mode_tool_sets() -> None:
    observation = _observation()
    semantic = _multi_mode_semantics(observation)
    first = _response("build")
    first["output"] = [_function_call(name="read", call_id="read-call")]
    second = _response("review")
    second["output"] = [_function_call(name="write", call_id="write-call")]
    client = RecordingPolicyClient(
        observation,
        responses=[first, second, _response("build-again")],
    )
    tools = RecordingToolPort(
        (_tool_binding("read-file"), _tool_binding("write-file")),
        results=[{"read": True}, {"written": True}],
    )
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(
            observation,
            semantics=semantic,
            tool_ids=("read-file", "write-file"),
        ),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "inspect then repair"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert result.turn_count == 3
    requests = [thaw_json(request.request_payload) for request in client.requests]
    assert [request["instructions"] for request in requests] == [
        "System compiled instruction.\n\nTOOL CATALOG: read_file",
        "Review system instruction.\n\nTOOL CATALOG: write_file, read_file",
        "System compiled instruction.\n\nTOOL CATALOG: read_file",
    ]
    assert [request["input"][0]["content"] for request in requests] == [
        "Per-turn compiled instruction.",
        "Review per-turn instruction.",
        "Per-turn compiled instruction.",
    ]
    assert [[tool["name"] for tool in request["tools"]] for request in requests] == [
        ["read_file"],
        ["write_file", "read_file"],
        ["read_file"],
    ]
    assert tools.calls == [
        ("read-file", {"path": "src/main.py"}, 9_000),
        ("write-file", {"path": "src/main.py"}, 9_000),
    ]
    assert [
        event.tool_name for event in sink.events if isinstance(event, ToolCallEvent)
    ] == ["read", "write"]
    await session.close()
    assert client.close_calls == 1


def test_conductor_implementation_digest_measures_exact_module_artifact() -> None:
    module_path = Path(conductor_module.__file__)
    assert module_path.is_absolute()
    assert CONDUCTOR_IMPLEMENTATION_DIGEST == (
        "sha256:" + hashlib.sha256(module_path.read_bytes()).hexdigest()
    )


def test_conductor_constructor_owns_identity_and_rejects_post_bootstrap_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError):
        ConductorAdapter(CONDUCTOR_RUNTIME_ABI, _digest("caller-supplied"))  # type: ignore[call-arg]
    original = conductor_module._CONDUCTOR_MODULE_IDENTITY
    changed = type(original)(
        _digest("changed-module"),
        original.device,
        original.inode,
        original.size_bytes,
        original.mtime_ns,
    )
    monkeypatch.setattr(conductor_module, "measure_module_artifact", lambda _path: changed)
    with pytest.raises(RuntimeError, match="changed after bootstrap"):
        ConductorAdapter(CONDUCTOR_RUNTIME_ABI)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("adapter_id", "breadboard.terminal-responses.v1", "adapter_mismatch"),
        ("runtime_abi", "foreign-runtime.v1", "runtime_abi_mismatch"),
        ("implementation_digest", _digest("foreign-implementation"), "implementation_digest_mismatch"),
    ],
)
async def test_conductor_rejects_runner_identity_mismatch_before_binding_tool_probe_or_event_effects(
    field: str,
    value: str,
    code: str,
) -> None:
    observation = _observation()
    kwargs = {field: value}
    kwargs.setdefault("implementation_digest", CONDUCTOR_IMPLEMENTATION_DIGEST)
    plan = _plan(observation=observation, **kwargs)
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(RunnerOpenRequest(episode_id="episode-a", effective_plan=plan), client)
    tools = RecordingToolPort()
    probe = RecordingCancellationProbe()
    sink = RecordingEventSink()
    adapter = ConductorAdapter(CONDUCTOR_RUNTIME_ABI)

    with pytest.raises(RunnerPlanError) as captured:
        await adapter.open(
            RunnerOpenRequest(episode_id="episode-a", effective_plan=plan),
            policy=binding,
            workspace=tools,
            cancellation=probe,
            events=sink,
        )

    assert captured.value.code == code
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0
    assert tools.binding_reads == 0
    assert tools.calls == []
    assert probe.checkpoints == []
    assert sink.events == []


def _mutated_semantics(
    observation: c.PolicyCapabilityObservation,
    mutation: str,
) -> dict[str, Any]:
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    variant = semantic["prompts"]["variants"][0]
    if mutation == "prompt_digest":
        variant["system"]["text_digest"] = _digest("wrong-prompt")
    elif mutation == "unknown_mode":
        semantic["loop"]["sequence"][0]["mode_id"] = "foreign-mode"
    elif mutation == "missing_pointer":
        semantic["providers"]["policy_slots"][0]["trainable_json_pointers"] = ["/params/missing"]
    elif mutation == "root_pointer":
        semantic["providers"]["policy_slots"][0]["trainable_json_pointers"] = ["/"]
    elif mutation == "overlap_pointer":
        semantic["providers"]["models"][0]["params"]["nested"] = {"value": 1}
        semantic["providers"]["policy_slots"][0]["trainable_json_pointers"] = [
            "/params/nested",
            "/params/nested/value",
        ]
    elif mutation == "unknown_provider":
        semantic["providers"]["models"][0]["provider_id"] = "foreign-provider"
    elif mutation == "alternate_fallback":
        semantic["providers"]["models"][0]["routing"]["fallback_model_ids"] = ["alternate-model"]
    elif mutation == "unknown_api_variant":
        semantic["providers"]["provider_tools"]["api_variant"] = "foreign-api"
    else:
        raise AssertionError(mutation)
    return _sync_root_semantics(semantic)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("prompt_digest", "prompt_variant_mismatch"),
        ("unknown_mode", "prompt_variant_mismatch"),
        ("missing_pointer", "trainable_pointer_invalid"),
        ("root_pointer", "trainable_pointer_invalid"),
        ("overlap_pointer", "trainable_pointer_invalid"),
        ("unknown_provider", "policy_observation_mismatch"),
        ("alternate_fallback", "compiled_ir_mismatch"),
        ("unknown_api_variant", "compiled_ir_mismatch"),
    ],
)
async def test_conductor_rejects_malformed_or_unbound_ir_before_tool_probe_event_or_policy_effects(
    mutation: str,
    code: str,
) -> None:
    observation = _observation()
    plan = _plan(observation=observation,
    semantics=_mutated_semantics(observation, mutation), implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(RunnerOpenRequest(episode_id="episode-a", effective_plan=plan), client)
    tools = RecordingToolPort()
    probe = RecordingCancellationProbe()
    sink = RecordingEventSink()

    with pytest.raises((RunnerPlanError, RunnerPolicyBindingError)) as captured:
        await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            RunnerOpenRequest(episode_id="episode-a", effective_plan=plan),
            policy=binding,
            workspace=tools,
            cancellation=probe,
            events=sink,
        )

    assert captured.value.code == code
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0
    assert tools.binding_reads == 0
    assert tools.calls == []
    assert probe.checkpoints == []
    assert sink.events == []


@pytest.mark.parametrize("include_admitted", [False, True], ids=["foreign-only", "mixed"])
async def test_conductor_rejects_foreign_provider_tool_policy_before_port_effects(
    include_admitted: bool,
) -> None:
    observation = _observation()
    semantic = _tool_semantics(observation)
    routing = {"foreign-provider": {"strict": True}}
    if include_admitted:
        routing[observation.provider_id] = {"strict": True}
    semantic["tools"]["definitions"][0]["provider_routing"] = routing
    semantic = _sync_root_semantics(semantic)
    plan = _plan_with_tools(observation, semantics=semantic)
    client = RecordingPolicyClient(observation)
    tools = RecordingToolPort((_tool_binding(),))
    binding = PolicyRuntimeBinding(
        RunnerOpenRequest(episode_id="episode-a", effective_plan=plan),
        client,
    )

    with pytest.raises(RunnerPlanError) as captured:
        await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            RunnerOpenRequest(episode_id="episode-a", effective_plan=plan),
            policy=binding,
            workspace=tools,
            cancellation=RecordingCancellationProbe(),
            events=RecordingEventSink(),
        )

    assert captured.value.code == "compiled_ir_mismatch"
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0
    assert tools.binding_reads == 0
    assert tools.calls == []


async def test_conductor_rejects_tool_binding_subclass_even_when_equality_can_spoof() -> None:
    class EqualitySpoofBinding(RunnerToolBinding):
        def __eq__(self, other: object) -> bool:
            return True

    observation = _observation()
    plan = _plan_with_tools(observation)
    grant = _tool_grant()
    tools = RecordingToolPort(
        (
            EqualitySpoofBinding(
                tool_id=grant.tool_id,
                implementation_digest=grant.implementation_digest,
                capability_ids=grant.capability_ids,
            ),
        )
    )
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(RunnerOpenRequest(episode_id="episode-a", effective_plan=plan), client)

    with pytest.raises(RunnerPlanError) as captured:
        await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            RunnerOpenRequest(episode_id="episode-a", effective_plan=plan),
            policy=binding,
            workspace=tools,
            cancellation=RecordingCancellationProbe(),
            events=RecordingEventSink(),
        )

    assert captured.value.code == "tool_grant_mismatch"
    assert client.requests == []
    assert tools.calls == []


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (_function_call(name="foreign_tool"), "policy returned an unbound tool call"),
        (_function_call(arguments="not-json"), "policy tool arguments are malformed"),
        (_function_call(arguments="{}"), "policy omitted a required tool argument"),
        (_function_call(arguments='{"path":7}'), "policy supplied a tool argument of the wrong type"),
        (
            _function_call(arguments='{"path":"src/main.py","escape":true}'),
            "policy supplied an unknown tool argument",
        ),
    ],
)
async def test_conductor_rejects_unknown_or_invalid_policy_tool_calls_without_tool_effect(
    call: dict[str, Any],
    message: str,
) -> None:
    observation = _observation()
    response = _response()
    response["output"] = [call]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "inspect"}))

    assert captured.value.category == "protocol"
    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == message
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerErrorEvent,
    ]
    assert [event.sequence for event in sink.events] == list(range(len(sink.events)))
    error_event = sink.events[-1]
    assert isinstance(error_event, RunnerErrorEvent)
    assert error_event.category == "protocol"
    assert error_event.code == "policy_response_invalid"
    assert error_event.message == message
    assert error_event.turn == 1
    assert error_event.call_id == "call-1"
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()
    assert client.close_calls == 1


async def test_conductor_redacts_provider_and_tool_exceptions_into_fixed_dependency_errors() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    client.invoke_error = RuntimeError("Authorization: Bearer provider-secret")
    session, _, _, _, sink, _ = await _open(observation=observation, client=client)

    with pytest.raises(RunnerDependencyError) as provider_error:
        await session.run(ConductorRunRequest({"query": "work"}))
    assert provider_error.value.code == "policy_invoke_failed"
    assert str(provider_error.value) == "policy runtime invocation failed"
    assert all("provider-secret" not in str(event) for event in sink.events)
    await session.close()

    response = _response()
    response["output"] = [_function_call()]
    tool_client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),))
    tools.error = RuntimeError("Authorization: Bearer tool-secret")
    tool_session, _, _, _, tool_sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=tool_client,
        tools=tools,
    )
    with pytest.raises(RunnerDependencyError) as tool_error:
        await tool_session.run(ConductorRunRequest({"query": "work"}))
    assert tool_error.value.code == "tool_invoke_failed"
    assert str(tool_error.value) == "conductor tool invocation failed"
    assert all("tool-secret" not in str(event) for event in tool_sink.events)
    await tool_session.close()


async def test_policy_dependency_cancelled_error_maps_to_redacted_fixed_failure() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    client.invoke_error = asyncio.CancelledError(
        "Authorization: Bearer policy-cancelled-secret"
    )
    session, _, tools, _, sink, _ = await _open(
        observation=observation,
        client=client,
    )

    with pytest.raises(RunnerDependencyError) as captured:
        await session.run(
            ConductorRunRequest({"query": "dependency cancellation"})
        )

    assert captured.value.code == "policy_invoke_failed"
    assert str(captured.value) == "policy runtime invocation failed"
    visible = (
        repr(captured.value)
        + str(captured.value)
        + repr(tuple(sink.events))
    )
    assert "policy-cancelled-secret" not in visible
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.category == "dependency"
    assert error.code == "policy_invoke_failed"
    assert not any(
        isinstance(event, PolicyRuntimeResponseEvent)
        for event in sink.events
    )
    assert not any(
        isinstance(event, RunnerTerminationEvent)
        for event in sink.events
    )
    await session.close()


class WrongDigestPolicyClient(RecordingPolicyClient):
    async def invoke(self, request: Any) -> PolicyRuntimeInvokeResult:
        self.requests.append(request)
        response = self.responses.pop(0)
        return PolicyRuntimeInvokeResult(
            response_payload=response,
            response_digest=_digest("wrong-response"),
        )


async def test_conductor_rejects_response_digest_mismatch_before_response_or_tool_acceptance() -> None:
    observation = _observation()
    client = WrongDigestPolicyClient(observation, responses=[_response()])
    session, _, tools, _, sink, _ = await _open(observation=observation, client=client)

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "work"}))

    assert captured.value.code == "policy_response_digest_mismatch"
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        RunnerErrorEvent,
    ]
    assert tools.calls == []
    await session.close()
    assert client.close_calls == 1


async def test_conductor_rejects_malformed_policy_output_before_response_acceptance() -> None:
    observation = _observation()
    response = _response()
    response["output"] = "not-a-closed-output-array"
    client = RecordingPolicyClient(observation, responses=[response])
    session, _, tools, _, sink, _ = await _open(observation=observation, client=client)

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "work"}))

    assert captured.value.category == "protocol"
    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == "policy response output is malformed"
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        RunnerErrorEvent,
    ]
    error_event = sink.events[-1]
    assert isinstance(error_event, RunnerErrorEvent)
    assert error_event.message == "policy response output is malformed"
    assert error_event.turn == 1
    assert tools.calls == []
    await session.close()
    assert client.close_calls == 1


async def test_conductor_rejects_oversized_response_before_response_or_tool_acceptance() -> None:
    observation = _observation()
    response = _response()
    response["oversized_carrier"] = "x" * 100_001
    client = RecordingPolicyClient(observation, responses=[response])
    session, _, tools, _, sink, _ = await _open(observation=observation, client=client)

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "work"}))

    assert captured.value.code == "policy_response_invalid"
    assert not any(isinstance(event, PolicyRuntimeResponseEvent) for event in sink.events)
    assert not any(isinstance(event, PolicyResponseEvent) for event in sink.events)
    assert isinstance(sink.events[-1], RunnerErrorEvent)
    assert tools.calls == []
    await session.close()
    assert client.close_calls == 1


async def test_conductor_close_failure_is_fixed_redacted_and_physical_close_occurs_once() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    client.close_error = RuntimeError("Authorization: Bearer close-secret")
    session, _, _, _, _, _ = await _open(observation=observation, client=client)
    await session.run(ConductorRunRequest({"query": "work"}))

    with pytest.raises(RunnerDependencyError) as captured:
        await session.close()

    assert captured.value.code == "policy_close_failed"
    assert str(captured.value) == "policy runtime close failed"
    assert "close-secret" not in str(captured.value)
    assert client.close_calls == 1
    with pytest.raises(RunnerDependencyError) as second:
        await session.close()
    assert second.value is captured.value
    assert client.close_calls == 1


async def test_conductor_cancel_before_run_prevents_policy_and_tool_effects_and_close_is_idempotent() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    session, _, tools, probe, sink, _ = await _open(observation=observation, client=client)

    cancellation = await session.cancel("operator-stop")
    with pytest.raises(RunnerCancelled) as captured:
        await session.run(ConductorRunRequest({"query": "work"}))

    assert cancellation.reason == "operator-stop"
    assert captured.value.cancellation.observed_checkpoint == "before_run"
    assert client.requests == []
    assert client.cancel_reasons == ["operator-stop"]
    assert tools.calls == []
    assert probe.checkpoints == []
    assert [type(event) for event in sink.events] == [
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    first = await session.close()
    second = await session.close()
    assert first.already_closed is False
    assert second.already_closed is True
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("checkpoint", "tool_response", "expected_policy_calls", "expected_tool_calls"),
    [
        pytest.param("before_policy", False, 0, 0, id="before-policy"),
        pytest.param("after_policy", False, 1, 0, id="after-policy"),
        pytest.param("before_action", True, 1, 0, id="before-action"),
        pytest.param("after_action", True, 1, 1, id="after-action"),
        pytest.param("after_loop", False, 1, 0, id="after-loop"),
        pytest.param("before_commit", False, 1, 0, id="before-commit"),
    ],
)
async def test_conductor_observes_external_cancellation_at_every_effect_boundary(
    checkpoint: str,
    tool_response: bool,
    expected_policy_calls: int,
    expected_tool_calls: int,
) -> None:
    observation = _observation()
    response = _response()
    if tool_response:
        response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])
    probe = RecordingCancellationProbe(fail_at=checkpoint)
    tools = RecordingToolPort(
        (_tool_binding(),) if tool_response else (),
        results=[{"ok": True}] if tool_response else [],
    )
    plan = _plan_with_tools(observation) if tool_response else _plan(observation=observation, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=plan,
        client=client,
        tools=tools,
        cancellation=probe,
    )

    with pytest.raises(RunnerCancelled) as captured:
        await session.run(ConductorRunRequest({"query": "work"}))

    assert captured.value.cancellation.observed_checkpoint == checkpoint
    assert len(client.requests) == expected_policy_calls
    assert len(tools.calls) == expected_tool_calls
    assert client.cancel_reasons == ["external cancellation requested"]
    assert isinstance(sink.events[-1], RunnerCancellationObservedEvent)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()
    assert client.close_calls == 1


async def test_conductor_cancel_during_invoke_surfaces_typed_after_policy_cancellation_and_no_late_effects() -> None:
    observation = _observation()
    client = InterruptiblePolicyClient(observation)
    session, _, tools, _, sink, _ = await _open(observation=observation, client=client)
    run_task = asyncio.create_task(session.run(ConductorRunRequest({"query": "work"})))
    await _within_timeout(client.entered.wait())

    await session.cancel("operator-stop")

    with pytest.raises(RunnerCancelled) as captured:
        await _within_timeout(run_task)
    assert captured.value.cancellation.observed_checkpoint == "after_policy"
    assert client.cancel_reasons == ["operator-stop"]
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2, 3]
    requested = sink.events[-2]
    observed = sink.events[-1]
    assert isinstance(requested, RunnerCancellationRequestedEvent)
    assert isinstance(observed, RunnerCancellationObservedEvent)
    assert requested.reason == observed.reason == "operator-stop"
    assert observed.checkpoint == "after_policy"
    assert observed.turn == 1
    assert not any(isinstance(event, PolicyResponseEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()
    assert client.close_calls == 1


async def test_parent_task_cancellation_during_policy_is_not_dependency_failure() -> None:
    observation = _observation()
    client = InterruptiblePolicyClient(observation)
    session, _, tools, _, sink, _ = await _open(
        observation=observation,
        client=client,
    )
    run_task = asyncio.create_task(
        session.run(ConductorRunRequest({"query": "parent cancellation"}))
    )
    await _within_timeout(client.entered.wait())

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert tools.calls == []
    assert not any(isinstance(event, RunnerErrorEvent) for event in sink.events)
    assert not any(
        isinstance(event, PolicyRuntimeResponseEvent)
        for event in sink.events
    )
    await session.close()


async def test_conductor_close_during_invoke_cancels_then_closes_without_late_effects() -> None:
    observation = _observation()
    client = InterruptiblePolicyClient(observation)
    session, _, tools, _, sink, _ = await _open(observation=observation, client=client)
    run_task = asyncio.create_task(session.run(ConductorRunRequest({"query": "work"})))
    await _within_timeout(client.entered.wait())

    first_close = await _within_timeout(session.close())
    with pytest.raises(RunnerCancelled) as captured:
        await _within_timeout(run_task)

    assert captured.value.cancellation.reason == "runner session closed"
    assert captured.value.cancellation.observed_checkpoint == "after_policy"
    assert first_close.already_closed is False
    assert first_close.cancellation is not None
    assert first_close.cancellation.reason == "runner session closed"
    assert client.cancel_reasons == ["runner session closed"]
    assert client.close_calls == 1
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2, 3]
    requested = sink.events[-2]
    observed = sink.events[-1]
    assert isinstance(requested, RunnerCancellationRequestedEvent)
    assert isinstance(observed, RunnerCancellationObservedEvent)
    assert requested.reason == observed.reason == "runner session closed"
    assert observed.checkpoint == "after_policy"
    assert observed.turn == 1
    assert not any(
        isinstance(
            event,
            (
                PolicyRuntimeResponseEvent,
                PolicyResponseEvent,
                ToolCallEvent,
                ToolObservationEvent,
                RunnerTerminationEvent,
            ),
        )
        for event in sink.events
    )
    second_close = await session.close()
    assert second_close.already_closed is True
    assert client.cancel_reasons == ["runner session closed"]
    assert client.close_calls == 1


async def test_conductor_cancel_after_policy_before_tool_discards_call_without_tool_or_termination() -> None:
    observation = _observation()
    response = _response()
    response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])
    sink = BlockingEventSink(PolicyResponseEvent)
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
        sink=sink,
    )
    run_task = asyncio.create_task(session.run(ConductorRunRequest({"query": "inspect"})))
    await _within_timeout(sink.entered.wait())

    cancel_task = asyncio.create_task(session.cancel("between-policy-and-tool"))
    await _advance_event_loop_once()
    sink.release.set()
    await _within_timeout(cancel_task)

    with pytest.raises(RunnerCancelled) as captured:
        await _within_timeout(run_task)
    assert captured.value.cancellation.observed_checkpoint == "before_action"
    assert tools.calls == []
    assert not any(isinstance(event, ToolCallEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()
    assert client.close_calls == 1


async def test_conductor_stops_at_compiled_max_turns_with_terminal_ledger() -> None:
    observation = _observation()
    responses: list[dict[str, Any]] = []
    for turn in range(1, 5):
        response = _response(f"turn-{turn}")
        response["output"] = [_function_call(call_id=f"call-{turn}")]
        responses.append(response)
    client = RecordingPolicyClient(observation, responses=responses)
    tools = RecordingToolPort(
        (_tool_binding(),),
        results=[{"turn": turn} for turn in range(1, 5)],
    )
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "inspect"}))

    assert result.termination is RunnerTermination.MAX_TURNS
    assert result.turn_count == 4
    assert len(result.turns) == 4
    assert len(client.requests) == 4
    assert len(tools.calls) == 4
    assert isinstance(sink.events[-1], RunnerTerminationEvent)
    assert sink.events[-1].reason is RunnerTermination.MAX_TURNS
    await session.close()
    assert client.close_calls == 1


async def test_conductor_rejects_tool_calls_above_compiled_per_turn_limit_before_excess_effect() -> None:
    observation = _observation()
    response = _response()
    response["output"] = [
        _function_call(call_id="call-1"),
        _function_call(call_id="call-2"),
        _function_call(call_id="call-3"),
    ]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort(
        (_tool_binding(),),
        results=[{"ordinal": 1}, {"ordinal": 2}],
    )
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "inspect"}))

    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == "policy exceeded the compiled per-turn tool limit"
    assert [call[0] for call in tools.calls] == ["read-file", "read-file"]
    assert len([event for event in sink.events if isinstance(event, ToolCallEvent)]) == 2
    assert len([event for event in sink.events if isinstance(event, ToolObservationEvent)]) == 2
    error_event = sink.events[-1]
    assert isinstance(error_event, RunnerErrorEvent)
    assert error_event.code == "policy_response_invalid"
    assert error_event.message == "policy exceeded the compiled per-turn tool limit"
    assert error_event.turn == 1
    assert error_event.call_id == "call-3"
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()
    assert client.close_calls == 1


async def test_conductor_sink_failure_is_typed_poisoned_and_not_laundered_as_success() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    sink = RecordingEventSink()
    sink.reject_type = PolicyRuntimeRequestEvent
    session, _, _, _, _, _ = await _open(
        observation=observation,
        client=client,
        sink=sink,
    )

    with pytest.raises(RunnerEventSinkError) as captured:
        await session.run(ConductorRunRequest({"query": "work"}))

    assert captured.value.code == "event_sink_failed"
    assert type(captured.value.failed_event) is PolicyRuntimeRequestEvent
    assert [type(event) for event in sink.events] == [PolicyRequestEvent]
    assert client.requests == []
    await session.close()
    assert client.close_calls == 1


async def test_concurrent_conductor_sessions_keep_requests_events_cancellation_and_close_isolated() -> None:
    observation_a = _observation("a")
    observation_b = _observation("b")
    client_a = RecordingPolicyClient(observation_a, responses=[_response("a")])
    client_b = InterruptiblePolicyClient(observation_b)
    sink_a = RecordingEventSink()
    sink_b = RecordingEventSink()
    session_a, _, _, _, _, binding_a = await _open(
        observation=observation_a,
        plan=_plan(label="a", observation=observation_a, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=client_a,
        sink=sink_a,
        episode_id="episode-a",
    )
    session_b, _, tools_b, _, _, binding_b = await _open(
        observation=observation_b,
        plan=_plan(label="b", observation=observation_b, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=client_b,
        sink=sink_b,
        episode_id="episode-b",
    )

    result_a_task = asyncio.create_task(session_a.run(ConductorRunRequest({"query": "a"})))
    result_b_task = asyncio.create_task(session_b.run(ConductorRunRequest({"query": "b"})))
    await _within_timeout(client_b.entered.wait())
    result_a = await _within_timeout(result_a_task)
    await session_b.cancel("cancel-b-only")

    with pytest.raises(RunnerCancelled):
        await _within_timeout(result_b_task)
    assert result_a.episode_id == "episode-a"
    assert thaw_json(result_a.response)["id"] == "response-a"
    assert [request.episode_id for request in client_a.requests] == ["episode-a"]
    assert [request.episode_id for request in client_b.requests] == ["episode-b"]
    assert client_a.cancel_reasons == []
    assert client_b.cancel_reasons == ["cancel-b-only"]
    assert all(event.episode_id == "episode-a" for event in sink_a.events)
    assert all(event.episode_id == "episode-b" for event in sink_b.events)
    assert tools_b.calls == []
    assert binding_a.binding_digest != binding_b.binding_digest
    await asyncio.gather(session_a.close(), session_b.close())
    assert client_a.close_calls == 1
    assert client_b.close_calls == 1


class ConductorRunRequestSubclass(ConductorRunRequest):
    pass


async def test_conductor_rejects_request_subclass_before_policy_or_tool_effects() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    session, _, tools, probe, sink, _ = await _open(observation=observation, client=client)
    request = ConductorRunRequestSubclass({"query": "work"})

    with pytest.raises(RunnerRequestError) as captured:
        await session.run(request)

    assert captured.value.code == "request_type_invalid"
    assert client.requests == []
    assert tools.calls == []
    assert probe.checkpoints == []
    assert [type(event) for event in sink.events] == [RunnerErrorEvent]
    await session.close()
    assert client.close_calls == 1


async def test_conductor_session_is_one_shot_and_closed_session_cannot_run() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    session, _, tools, _, sink, _ = await _open(observation=observation, client=client)
    await session.run(ConductorRunRequest({"query": "first"}))
    effect_snapshot = (len(client.requests), len(tools.calls), tuple(sink.events))

    with pytest.raises(RunnerStateError) as rerun:
        await session.run(ConductorRunRequest({"query": "second"}))
    assert rerun.value.code == "run_already_started"
    assert str(rerun.value) == "runner session permits exactly one run"
    assert (len(client.requests), len(tools.calls), tuple(sink.events)) == effect_snapshot
    await session.close()

    closed_client = RecordingPolicyClient(observation)
    closed_session, _, closed_tools, _, closed_sink, _ = await _open(
        observation=observation,
        client=closed_client,
    )
    await closed_session.close()
    with pytest.raises(RunnerStateError) as closed:
        await closed_session.run(ConductorRunRequest({"query": "never"}))
    assert closed.value.code == "session_closed"
    assert str(closed.value) == "runner session is closed"
    assert closed_client.requests == []
    assert closed_tools.calls == []
    assert closed_sink.events == []


async def test_conductor_resolves_every_compiled_alias_and_model_name_to_the_admitted_tool_id() -> None:
    observation = _observation()
    semantic = _tool_semantics(observation)
    semantic["tools"]["aliases"] = [
        ["read", "read-file"],
        ["inspect", "read-file"],
    ]
    semantic = _sync_root_semantics(semantic)
    responses = []
    for turn, name in enumerate(("read", "inspect", "read_file"), start=1):
        response = _response(f"alias-{turn}")
        response["output"] = [_function_call(name=name, call_id=f"call-{turn}")]
        responses.append(response)
    responses.append(_response("complete"))
    client = RecordingPolicyClient(observation, responses=responses)
    tools = RecordingToolPort(
        (_tool_binding(),),
        results=[{"turn": 1}, {"turn": 2}, {"turn": 3}],
    )
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "resolve aliases"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert [call[0] for call in tools.calls] == [
        "read-file",
        "read-file",
        "read-file",
    ]
    assert [call[1] for call in tools.calls] == [
        {"path": "src/main.py"},
        {"path": "src/main.py"},
        {"path": "src/main.py"},
    ]
    await session.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-alias",
        "dangling-target",
        "model-name-collision",
        "tool-id-collision",
    ],
)
async def test_conductor_rejects_alias_collisions_and_unadmitted_targets_before_effects(
    mutation: str,
) -> None:
    observation = _observation()
    semantic = _tool_semantics(observation)
    if mutation == "duplicate-alias":
        semantic["tools"]["aliases"] = [
            ["read", "read-file"],
            ["read", "read-file"],
        ]
    elif mutation == "dangling-target":
        semantic["tools"]["aliases"] = [["read", "foreign-tool"]]
    elif mutation == "model-name-collision":
        semantic["tools"]["aliases"] = [["read_file", "read-file"]]
    elif mutation == "tool-id-collision":
        semantic["tools"]["aliases"] = [["read-file", "read-file"]]
    else:
        raise AssertionError(mutation)
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        code="tool_grant_mismatch",
        bindings=(_tool_binding(),),
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("disabled-mode", "compiled_ir_mismatch"),
        ("missing-mode", "prompt_variant_mismatch"),
        ("missing-variant", "prompt_variant_mismatch"),
        ("mode-tool-order", "prompt_variant_mismatch"),
        ("unknown-mode-tool", "compiled_ir_mismatch"),
        ("catalog-tool-order", "prompt_variant_mismatch"),
    ],
)
async def test_conductor_rejects_disabled_missing_or_inexact_mode_authority_before_effects(
    mutation: str,
    code: str,
) -> None:
    observation = _observation()
    semantic = _multi_mode_semantics(observation)
    if mutation == "disabled-mode":
        semantic["modes"][1]["enabled"] = False
    elif mutation == "missing-mode":
        semantic["modes"].pop()
    elif mutation == "missing-variant":
        semantic["prompts"]["variants"].pop()
    elif mutation == "mode-tool-order":
        semantic["modes"][1]["enabled_tool_ids"] = ["read-file", "write-file"]
    elif mutation == "unknown-mode-tool":
        semantic["modes"][1]["enabled_tool_ids"].append("foreign-tool")
    elif mutation == "catalog-tool-order":
        semantic["prompts"]["variants"][1]["tool_catalog"][
            "effective_tool_ids"
        ] = ["read-file", "write-file"]
    else:
        raise AssertionError(mutation)
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan_with_tools(
            observation,
            semantics=semantic,
            tool_ids=("read-file", "write-file"),
        ),
        code=code,
        bindings=(_tool_binding("read-file"), _tool_binding("write-file")),
    )


async def test_conductor_projects_default_model_after_nondefault_model_and_slot_records() -> None:
    observation = _observation(
        capabilities=_policy_capabilities(policy_slot_count=2)
    )
    semantic = _two_model_semantics(observation)
    plan = _plan(observation=observation,
    semantics=semantic,
    policy_slot_ids=(f"slot-{observation.model_id}", "slot-shadow-model"), implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
    client = RecordingPolicyClient(observation)
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=plan,
        client=client,
    )

    result = await session.run(ConductorRunRequest({"query": "use selected model"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    request = thaw_json(client.requests[0].request_payload)
    assert request["model"] == observation.model_id
    assert request["instructions"] == (
        "System compiled instruction.\n\nNo tools admitted."
    )
    runtime_request = next(
        event for event in sink.events if isinstance(event, PolicyRuntimeRequestEvent)
    )
    assert runtime_request.policy_slot_id == f"slot-{observation.model_id}"
    assert thaw_json(runtime_request.trainable_values) == {
        "/params/temperature": 0.25
    }
    await session.close()


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("model-provider", "compiled_ir_mismatch"),
        ("model-adapter", "compiled_ir_mismatch"),
        ("model-request-schema", "compiled_ir_mismatch"),
        ("model-slot", "compiled_ir_mismatch"),
        ("model-fallback", "compiled_ir_mismatch"),
        ("slot-model", "compiled_ir_mismatch"),
        ("slot-adapter", "compiled_ir_mismatch"),
        ("slot-request-schema", "compiled_ir_mismatch"),
        ("slot-route", "compiled_ir_mismatch"),
        ("slot-credential", "compiled_ir_mismatch"),
        ("slot-pointer", "trainable_pointer_invalid"),
        ("prompt-model", "prompt_variant_mismatch"),
        ("prompt-mode", "prompt_variant_mismatch"),
        ("prompt-config-node", "prompt_variant_mismatch"),
        ("prompt-digest", "prompt_variant_mismatch"),
        ("prompt-duplicate", "prompt_variant_mismatch"),
    ],
)
async def test_conductor_validates_every_nondefault_model_slot_and_prompt_cross_reference(
    mutation: str,
    code: str,
) -> None:
    observation = _observation(
        capabilities=_policy_capabilities(policy_slot_count=2)
    )
    semantic = _two_model_semantics(observation)
    model = semantic["providers"]["models"][0]
    slot = semantic["providers"]["policy_slots"][1]
    variant = semantic["prompts"]["variants"][0]
    if mutation == "model-provider":
        model["provider_id"] = "foreign-provider"
    elif mutation == "model-adapter":
        model["adapter_id"] = "foreign-adapter"
    elif mutation == "model-request-schema":
        model["request_schema_id"] = "foreign-request-schema"
    elif mutation == "model-slot":
        model["policy_slot_id"] = "missing-slot"
    elif mutation == "model-fallback":
        model["routing"]["fallback_model_ids"] = [observation.model_id]
    elif mutation == "slot-model":
        slot["model_id"] = observation.model_id
    elif mutation == "slot-adapter":
        slot["adapter_id"] = "foreign-adapter"
    elif mutation == "slot-request-schema":
        slot["request_schema_id"] = "foreign-request-schema"
    elif mutation == "slot-route":
        slot["requested_route_handle_id"] = "foreign-route"
    elif mutation == "slot-credential":
        slot["requested_credential_handle_id"] = "foreign-credential"
    elif mutation == "slot-pointer":
        slot["trainable_json_pointers"] = ["/params/missing"]
    elif mutation == "prompt-model":
        variant["model_id"] = "foreign-model"
    elif mutation == "prompt-mode":
        variant["mode_id"] = "foreign-mode"
    elif mutation == "prompt-config-node":
        variant["config_node_id"] = _digest("foreign-node")
    elif mutation == "prompt-digest":
        variant["system"]["text_digest"] = _digest("wrong-text")
    elif mutation == "prompt-duplicate":
        semantic["prompts"]["variants"].append(copy.deepcopy(variant))
    else:
        raise AssertionError(mutation)
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan(observation=observation,
        semantics=semantic,
        policy_slot_ids=(f"slot-{observation.model_id}", "slot-shadow-model"), implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        code=code,
    )


async def test_conductor_rejects_reversed_semantic_slot_order_against_plan_grants() -> None:
    observation = _observation(
        capabilities=_policy_capabilities(policy_slot_count=2)
    )
    semantic = _two_model_semantics(observation)
    semantic["providers"]["policy_slots"].reverse()
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan(observation=observation,
        semantics=semantic,
        policy_slot_ids=(f"slot-{observation.model_id}", "slot-shadow-model"), implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        code="compiled_ir_mismatch",
    )


UNSUPPORTED_SEMANTIC_FAMILIES = (
    "features",
    "turn_strategy",
    "completion",
    "concurrency",
    "permissions",
    "enhanced_tools",
    "plugins",
    "guardrails",
    "team",
    "replay",
    "long_running",
    "terminal_sessions",
    "observability",
)


async def test_conductor_accepts_compiler_emitted_empty_unsupported_families() -> None:
    observation = _observation()
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    semantic["plugins"] = {
        "enabled": False,
        "plugins": [],
        "untrusted_hook_tool_ids": [],
    }
    semantic["guardrails"] = {"definitions": [], "plan_bootstrap": None}
    semantic["observability"] = {"logging": {}, "telemetry": {}}
    semantic = _sync_root_semantics(semantic)
    client = RecordingPolicyClient(observation)
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan(observation=observation, semantics=semantic, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=client,
    )

    result = await session.run(ConductorRunRequest({"query": "empty families"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert len(client.requests) == 1
    await session.close()


@pytest.mark.parametrize("family", UNSUPPORTED_SEMANTIC_FAMILIES)
async def test_conductor_rejects_each_nonempty_unsupported_semantic_family(
    family: str,
) -> None:
    observation = _observation()
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    semantic[family] = {"enabled": True}
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan(observation=observation, semantics=semantic, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        code="compiled_ir_mismatch",
    )


@pytest.mark.parametrize(
    ("control", "value"),
    [
        ("api_variant", "anthropic_messages"),
        ("use_native", False),
        ("suppress_prompts", True),
        ("responses_stateful", True),
        ("responses_use_developer_role", "false"),
        ("terminal_tool_protocol", True),
    ],
)
async def test_conductor_rejects_every_unsupported_provider_control_value(
    control: str,
    value: Any,
) -> None:
    observation = _observation()
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    semantic["providers"]["provider_tools"][control] = value
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan(observation=observation, semantics=semantic, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        code="compiled_ir_mismatch",
    )


async def test_conductor_honors_supported_responses_role_control_and_accepts_supported_provider_controls() -> None:
    observation = _observation()
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    semantic["providers"]["provider_tools"] = {
        "api_variant": "responses",
        "use_native": True,
        "responses_use_developer_role": False,
        "suppress_prompts": False,
        "responses_stateful": False,
    }
    semantic = _sync_root_semantics(semantic)
    client = RecordingPolicyClient(observation)
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan(observation=observation, semantics=semantic, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=client,
    )

    await session.run(ConductorRunRequest({"query": "provider controls"}))

    assert thaw_json(client.requests[0].request_payload)["input"][0] == {
        "role": "system",
        "content": "Per-turn compiled instruction.",
    }
    await session.close()


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("tool_prompt_mode", "system_compiled_and_persistent_per_turn"),
        ("environment", {"USER": "ambient"}),
        ("dedupe", True),
        ("dialects", {"default": ["foreign"]}),
        ("system_order", ["pack:foreign"]),
        ("per_turn_order", ["pack:foreign"]),
        ("packs", [{"id": "foreign"}]),
        ("synthesis_enabled", False),
        ("synthesis_detail", {"foreign": True}),
        ("synthesis_renderer", "foreign-renderer"),
        ("synthesis_selection", {"foreign": True}),
        ("synthesis_templates", ["foreign"]),
        ("synthesis_catalog_template", "foreign"),
    ],
)
async def test_conductor_rejects_every_unsupported_prompt_control_value(
    mutation: str,
    value: Any,
) -> None:
    observation = _observation()
    semantic = copy.deepcopy(_empty_semantics(observation=observation))
    prompts = semantic["prompts"]
    if mutation in {"tool_prompt_mode", "environment", "dedupe", "dialects", "packs"}:
        prompts[mutation] = value
    elif mutation in {"system_order", "per_turn_order"}:
        prompts["injection"][mutation] = value
    elif mutation == "synthesis_enabled":
        prompts["synthesis"]["enabled"] = value
    elif mutation == "synthesis_detail":
        prompts["synthesis"]["detail"] = value
    elif mutation == "synthesis_renderer":
        prompts["synthesis"]["renderer_id"] = value
    elif mutation == "synthesis_selection":
        prompts["synthesis"]["selection"] = value
    elif mutation == "synthesis_templates":
        prompts["synthesis"]["templates"] = value
    elif mutation == "synthesis_catalog_template":
        prompts["synthesis"]["tool_catalog_template"] = value
    else:
        raise AssertionError(mutation)
    semantic = _sync_root_semantics(semantic)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan(observation=observation, semantics=semantic, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        code="prompt_variant_mismatch",
    )


@pytest.mark.parametrize(
    (
        "tool_prompt_mode",
        "system_text",
        "per_turn_text",
        "catalog_text",
        "expected_instructions",
        "expected_per_turn",
    ),
    [
        (
            "system_once",
            "System compiled instruction.",
            "Per-turn compiled instruction.",
            "TOOL CATALOG: read_file",
            "System compiled instruction.\n\nTOOL CATALOG: read_file",
            "Per-turn compiled instruction.",
        ),
        (
            "per_turn_append",
            "System compiled instruction.",
            "Per-turn compiled instruction.",
            "TOOL CATALOG: read_file",
            "System compiled instruction.",
            "Per-turn compiled instruction.\n\nTOOL CATALOG: read_file",
        ),
    ],
    ids=["system-once-placement", "per-turn-placement"],
)
async def test_conductor_places_compiled_tool_catalog_for_each_supported_prompt_mode(
    tool_prompt_mode: str,
    system_text: str,
    per_turn_text: str,
    catalog_text: str,
    expected_instructions: str,
    expected_per_turn: str,
) -> None:
    observation = _observation()
    semantic = _tool_semantics(observation)
    semantic["prompts"]["tool_prompt_mode"] = tool_prompt_mode
    variant = semantic["prompts"]["variants"][0]
    variant["system"]["text"] = system_text
    variant["system"]["text_digest"] = _digest(system_text)
    variant["per_turn"]["text"] = per_turn_text
    variant["per_turn"]["text_digest"] = _digest(per_turn_text)
    variant["tool_catalog"]["text"] = catalog_text
    variant["tool_catalog"]["text_digest"] = _digest(catalog_text)
    semantic = _sync_root_semantics(semantic)
    client = RecordingPolicyClient(observation)
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=RecordingToolPort((_tool_binding(),)),
    )

    result = await session.run(
        ConductorRunRequest({"query": f"use {tool_prompt_mode}"})
    )

    request = thaw_json(client.requests[0].request_payload)
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert request["instructions"] == expected_instructions
    assert request["input"][0] == {
        "role": "developer",
        "content": expected_per_turn,
    }
    await session.close()


async def test_conductor_accepts_nested_compiler_schema_and_invokes_only_valid_arguments() -> None:
    observation = _observation()
    semantic = _tool_semantics(observation, parameters=_schema_parameters())
    arguments = _valid_schema_arguments()
    response = _response()
    response["output"] = [
        _function_call(arguments=json.dumps(arguments, separators=(",", ":")))
    ]
    client = RecordingPolicyClient(observation, responses=[response, _response("done")])
    tools = RecordingToolPort((_tool_binding(),), results=[{"validated": True}])
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "validate schema"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert tools.calls == [("read-file", arguments, 9_000)]
    await session.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("string-type", "policy supplied a tool argument of the wrong type"),
        ("min-length", "policy supplied a tool string below its minimum length"),
        ("max-length", "policy supplied a tool string above its maximum length"),
        ("pattern", "policy supplied a tool string outside its pattern"),
        ("enum", "policy supplied a tool argument outside its enum"),
        ("const", "policy supplied a tool argument outside its const"),
        ("integer-bool", "policy supplied a tool argument of the wrong type"),
        ("minimum", "policy supplied a tool number below its minimum"),
        ("maximum", "policy supplied a tool number above its maximum"),
        ("exclusive-minimum", "policy supplied a tool number below its exclusive minimum"),
        ("exclusive-maximum", "policy supplied a tool number above its exclusive maximum"),
        ("multiple-of", "policy supplied a tool number outside its multiple"),
        ("min-items", "policy supplied too few tool array items"),
        ("max-items", "policy supplied too many tool array items"),
        ("unique-items", "policy supplied duplicate tool array items"),
        ("array-item", "policy supplied a tool argument of the wrong type"),
        ("nested-required", "policy omitted a required tool argument"),
        ("nested-additional", "policy supplied an unknown tool argument"),
        ("nested-array-item", "policy supplied a tool argument of the wrong type"),
        ("null-type", "policy supplied a tool argument of the wrong type"),
        ("root-additional", "policy supplied an unknown tool argument"),
    ],
)
async def test_conductor_enforces_every_compiler_schema_constraint_before_tool_effect(
    mutation: str,
    message: str,
) -> None:
    observation = _observation()
    semantic = _tool_semantics(observation, parameters=_schema_parameters())
    arguments = _valid_schema_arguments()
    if mutation == "string-type":
        arguments["short_code"] = 2
    elif mutation == "min-length":
        arguments["short_code"] = "x"
    elif mutation == "max-length":
        arguments["capped"] = "abcde"
    elif mutation == "pattern":
        arguments["patterned"] = "UPPER"
    elif mutation == "enum":
        arguments["enum_value"] = "charlie"
    elif mutation == "const":
        arguments["const_value"] = "moving"
    elif mutation == "integer-bool":
        arguments["integer_value"] = True
    elif mutation == "minimum":
        arguments["integer_value"] = -1
    elif mutation == "maximum":
        arguments["integer_value"] = 11
    elif mutation == "exclusive-minimum":
        arguments["ratio"] = 0
    elif mutation == "exclusive-maximum":
        arguments["ratio"] = 1
    elif mutation == "multiple-of":
        arguments["ratio"] = 0.3
    elif mutation == "min-items":
        arguments["tags"] = []
    elif mutation == "max-items":
        arguments["tags"] = ["a", "b", "c", "a"]
    elif mutation == "unique-items":
        arguments["tags"] = ["a", "a"]
    elif mutation == "array-item":
        arguments["tags"] = ["a", 7]
    elif mutation == "nested-required":
        arguments["nested"] = {"enabled": True}
    elif mutation == "nested-additional":
        arguments["nested"]["foreign"] = True
    elif mutation == "nested-array-item":
        arguments["nested"]["levels"] = [1, False]
    elif mutation == "null-type":
        arguments["nothing"] = False
    elif mutation == "root-additional":
        arguments["foreign"] = True
    else:
        raise AssertionError(mutation)
    response = _response()
    response["output"] = [
        _function_call(arguments=json.dumps(arguments, separators=(",", ":")))
    ]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "invalid schema value"}))

    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == message
    assert captured.value.events_so_far == tuple(sink.events)
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.code == "policy_response_invalid"
    assert error.message == message
    assert error.turn == 1
    assert error.call_id == "call-1"
    assert not any(isinstance(event, ToolCallEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


@pytest.mark.parametrize(
    "keyword",
    ["oneOf", "anyOf", "allOf", "not", "$ref", "format", "contains", "minProperties"],
)
async def test_conductor_rejects_unsupported_compiled_schema_keywords_at_open(
    keyword: str,
) -> None:
    observation = _observation()
    parameters = [_parameter("value", {"type": "string", keyword: []})]
    semantic = _tool_semantics(observation, parameters=parameters)
    await _assert_open_rejected(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        code="compiled_ir_mismatch",
        bindings=(_tool_binding(),),
    )


async def test_conductor_requires_exact_policy_binding_and_claims_it_only_once() -> None:
    class PolicyRuntimeBindingSubclass(PolicyRuntimeBinding):
        pass

    class ForeignPolicyBinding:
        def __init__(self, binding: PolicyRuntimeBinding) -> None:
            self._binding = binding

        @property
        def episode_id(self) -> str:
            return self._binding.episode_id

        @property
        def effective_plan_digest(self) -> str:
            return self._binding.effective_plan_digest

        @property
        def binding_digest(self) -> str:
            return self._binding.binding_digest

        @property
        def policy_capability_observation(self) -> c.PolicyCapabilityObservation:
            return self._binding.policy_capability_observation

        @property
        def policy_capability_observation_digest(self) -> str:
            return self._binding.policy_capability_observation_digest

        @property
        def policy_slot_ids(self) -> tuple[str, ...]:
            return self._binding.policy_slot_ids

        @property
        def first_request_digest(self) -> str | None:
            return self._binding.first_request_digest

        async def invoke(self, request: Any) -> Any:
            return await self._binding.invoke(request)

        async def cancel(self, reason: str) -> None:
            await self._binding.cancel(reason)

        async def close(self) -> None:
            await self._binding.close()

    observation = _observation()
    plan = _plan(observation=observation, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
    request = RunnerOpenRequest(episode_id="episode-a", effective_plan=plan)
    adapter = ConductorAdapter(CONDUCTOR_RUNTIME_ABI)

    for candidate_factory in (
        lambda client: PolicyRuntimeBindingSubclass(request, client),
        lambda client: ForeignPolicyBinding(PolicyRuntimeBinding(request, client)),
    ):
        client = RecordingPolicyClient(observation)
        tools = RecordingToolPort()
        probe = RecordingCancellationProbe()
        sink = RecordingEventSink()
        with pytest.raises(RunnerPolicyBindingError) as captured:
            await adapter.open(
                request,
                policy=candidate_factory(client),
                workspace=tools,
                cancellation=probe,
                events=sink,
            )
        assert captured.value.code == "binding_identity_mismatch"
        assert client.requests == []
        assert client.cancel_reasons == []
        assert client.close_calls == 0
        assert tools.binding_reads == 0
        assert tools.calls == []
        assert probe.checkpoints == []
        assert sink.events == []

    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(request, client)
    first_session = await adapter.open(
        request,
        policy=binding,
        workspace=RecordingToolPort(),
        cancellation=RecordingCancellationProbe(),
        events=RecordingEventSink(),
    )
    second_tools = RecordingToolPort()
    second_sink = RecordingEventSink()
    with pytest.raises(RunnerPolicyBindingError) as double_open:
        await adapter.open(
            request,
            policy=binding,
            workspace=second_tools,
            cancellation=RecordingCancellationProbe(),
            events=second_sink,
        )
    assert double_open.value.code == "binding_already_claimed"
    assert second_tools.binding_reads == 1
    assert second_tools.calls == []
    assert second_sink.events == []
    assert client.requests == []
    await first_session.close()
    assert client.close_calls == 1


@pytest.mark.parametrize("mixed", [False, True], ids=["unknown-only", "mixed"])
async def test_conductor_rejects_unknown_and_mixed_policy_outputs_atomically(
    mixed: bool,
) -> None:
    observation = _observation()
    unsupported = {
        "type": "computer_call",
        "id": "computer-1",
        "action": {"type": "click", "x": 7, "y": 11},
    }
    response = _response()
    response["output"] = (
        [unsupported, _function_call()] if mixed else [unsupported]
    )
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort(
        (_tool_binding(),) if mixed else (),
        results=[{"must": "not run"}] if mixed else [],
    )
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation) if mixed else _plan(observation=observation, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=client,
        tools=tools,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "unsupported output"}))

    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == "policy response output item is unsupported"
    assert captured.value.events_so_far == tuple(sink.events)
    assert len(client.requests) == 1
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.code == "policy_response_invalid"
    assert error.message == "policy response output item is unsupported"
    assert error.turn == 1
    assert not any(isinstance(event, PolicyResponseEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_conductor_enforces_exact_initial_transcript_boundary_before_policy_effects() -> None:
    observation = _observation()
    task_input = {"query": "é"}
    context = {"dataset": ["a", "b"]}
    exact_limit = _encoded_json_size(task_input) + _encoded_json_size(context)

    accepted_client = RecordingPolicyClient(observation)
    accepted_session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan(observation=observation,
        limit_updates={"transcript_bytes": exact_limit}, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=accepted_client,
    )
    accepted = await accepted_session.run(ConductorRunRequest(task_input, context))
    assert accepted.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert len(accepted_client.requests) == 1
    await accepted_session.close()

    rejected_client = RecordingPolicyClient(observation)
    rejected_sink = RecordingEventSink()
    rejected_session, _, tools, _, _, _ = await _open(
        observation=observation,
        plan=_plan(observation=observation,
        limit_updates={"transcript_bytes": exact_limit - 1}, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST),
        client=rejected_client,
        sink=rejected_sink,
    )
    with pytest.raises(RunnerProtocolError) as captured:
        await rejected_session.run(ConductorRunRequest(task_input, context))
    assert captured.value.code == "transcript_limit_exceeded"
    assert str(captured.value) == "compiled transcript byte limit exceeded"
    assert captured.value.events_so_far == tuple(rejected_sink.events)
    assert rejected_client.requests == []
    assert tools.calls == []
    assert [type(event) for event in rejected_sink.events] == [RunnerErrorEvent]
    error = rejected_sink.events[0]
    assert isinstance(error, RunnerErrorEvent)
    assert error.code == "transcript_limit_exceeded"
    assert error.turn is None
    await rejected_session.close()


async def test_conductor_enforces_cumulative_transcript_boundary_without_later_effects() -> None:
    observation = _observation()
    task_input = {"query": "two tool turns"}
    context: dict[str, Any] = {}
    calls = [
        _function_call(call_id="call-1"),
        _function_call(call_id="call-2"),
    ]
    observations = [{"turn": 1}, {"turn": 2}]
    additions = []
    for call, tool_observation in zip(calls, observations, strict=True):
        output_item = {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": json.dumps(
                tool_observation,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        additions.append(_encoded_json_size(call) + _encoded_json_size(output_item))
    exact_limit = (
        _encoded_json_size(task_input)
        + _encoded_json_size(context)
        + sum(additions)
    )

    async def run_with_limit(limit: int) -> tuple[Any, RecordingPolicyClient, RecordingToolPort, RecordingEventSink]:
        responses = []
        for turn, call in enumerate(calls, start=1):
            response = _response(f"turn-{turn}")
            response["output"] = [call]
            responses.append(response)
        responses.append(_response("complete"))
        client = RecordingPolicyClient(observation, responses=responses)
        tools = RecordingToolPort((_tool_binding(),), results=copy.deepcopy(observations))
        sink = RecordingEventSink()
        session, _, _, _, _, _ = await _open(
            observation=observation,
            plan=_plan_with_tools(
                observation,
                limit_updates={"transcript_bytes": limit},
            ),
            client=client,
            tools=tools,
            sink=sink,
        )
        return session, client, tools, sink

    accepted_session, accepted_client, accepted_tools, _ = await run_with_limit(
        exact_limit
    )
    accepted = await accepted_session.run(ConductorRunRequest(task_input, context))
    assert accepted.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert len(accepted_client.requests) == 3
    assert len(accepted_tools.calls) == 2
    await accepted_session.close()

    rejected_session, rejected_client, rejected_tools, rejected_sink = (
        await run_with_limit(exact_limit - 1)
    )
    with pytest.raises(RunnerProtocolError) as captured:
        await rejected_session.run(ConductorRunRequest(task_input, context))
    assert captured.value.code == "transcript_limit_exceeded"
    assert str(captured.value) == "compiled transcript byte limit exceeded"
    assert captured.value.events_so_far == tuple(rejected_sink.events)
    assert len(rejected_client.requests) == 2
    assert [call[0] for call in rejected_tools.calls] == ["read-file", "read-file"]
    assert [
        event.call_id
        for event in rejected_sink.events
        if isinstance(event, ToolObservationEvent)
    ] == ["call-1"]
    assert isinstance(rejected_sink.events[-1], RunnerErrorEvent)
    assert rejected_sink.events[-1].turn == 2
    assert rejected_sink.events[-1].call_id == "call-2"
    assert not any(
        isinstance(event, PolicyRequestEvent) and event.turn == 3
        for event in rejected_sink.events
    )
    assert not any(isinstance(event, RunnerTerminationEvent) for event in rejected_sink.events)
    await rejected_session.close()


async def test_conductor_redacts_malicious_runner_error_from_tool_boundary_with_exact_ledger() -> None:
    observation = _observation()
    response = _response()
    response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),))
    malicious_type = type(
        "Authorization_Bearer_wp6_tool_runner_error_class",
        (RunnerDependencyError,),
        {},
    )
    tools.error = malicious_type(
        "Authorization: Bearer tool-runner-secret",
        code="tool_secret_code",
    )
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
    )

    with pytest.raises(RunnerDependencyError) as captured:
        await session.run(ConductorRunRequest({"query": "invoke hostile tool"}))

    assert captured.value.code == "tool_invoke_failed"
    assert str(captured.value) == "conductor tool invocation failed"
    assert captured.value.__cause__ is tools.error
    visible = repr(captured.value) + str(captured.value) + repr(tuple(sink.events))
    assert "tool-runner-secret" not in visible
    assert "tool_secret_code" not in visible
    assert "Authorization_Bearer_wp6_tool_runner_error_class" not in visible
    assert captured.value.events_so_far == tuple(sink.events)
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        ToolCallEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.category == "dependency"
    assert error.code == "tool_invoke_failed"
    assert error.message == "conductor tool invocation failed"
    assert error.turn == 1
    assert error.call_id == "call-1"
    assert len(tools.calls) == 1
    assert not any(isinstance(event, ToolObservationEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_conductor_terminal_commit_wins_over_late_cancel_without_lifecycle_reordering() -> None:
    observation = _observation()
    sink = BlockingEventSink(RunnerTerminationEvent)
    client = RecordingPolicyClient(observation)
    session, _, _, _, _, _ = await _open(
        observation=observation,
        client=client,
        sink=sink,
    )
    run_task = asyncio.create_task(
        session.run(ConductorRunRequest({"query": "complete"}))
    )
    await _within_timeout(sink.entered.wait())

    late = await _within_timeout(session.cancel("too-late"))
    assert late.requested is False
    assert late.reason == "too-late"
    sink.release.set()
    result = await _within_timeout(run_task)

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert result.events == tuple(sink.events)
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerTerminationEvent,
    ]
    assert [event.sequence for event in sink.events] == list(range(5))
    assert client.cancel_reasons == []
    close_result = await session.close()
    assert close_result.cancellation is None


async def test_conductor_duplicate_cancel_race_publishes_one_request_and_cancellation_wins() -> None:
    observation = _observation()
    sink = BlockingEventSink(PolicyResponseEvent)
    client = RecordingPolicyClient(observation)
    session, _, tools, _, _, _ = await _open(
        observation=observation,
        client=client,
        sink=sink,
    )
    run_task = asyncio.create_task(
        session.run(ConductorRunRequest({"query": "cancel before commit"}))
    )
    await _within_timeout(sink.entered.wait())

    first = asyncio.create_task(session.cancel("first-reason"))
    second = asyncio.create_task(session.cancel("second-reason"))
    await _advance_event_loop_once()
    sink.release.set()
    first_result, second_result = await _within_timeout(
        asyncio.gather(first, second)
    )

    with pytest.raises(RunnerCancelled) as captured:
        await _within_timeout(run_task)
    assert first_result == second_result
    assert first_result.reason == "first-reason"
    assert first_result.requested is True
    assert captured.value.cancellation.reason == "first-reason"
    assert captured.value.cancellation.observed_checkpoint == "after_policy"
    assert client.cancel_reasons == ["first-reason"]
    assert tools.calls == []
    assert [
        type(event) for event in sink.events
    ] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert len(
        [
            event
            for event in sink.events
            if isinstance(event, RunnerCancellationRequestedEvent)
        ]
    ) == 1
    assert [event.sequence for event in sink.events] == list(range(6))
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


@pytest.mark.parametrize(
    ("trigger", "operation", "expected_checkpoint"),
    [
        (PolicyRequestEvent, "cancel", "before_policy"),
        (PolicyRequestEvent, "close", "before_policy"),
        (ToolCallEvent, "cancel", "before_action"),
        (ToolCallEvent, "close", "before_action"),
    ],
    ids=[
        "policy-request-cancel",
        "policy-request-close",
        "tool-call-cancel",
        "tool-call-close",
    ],
)
async def test_conductor_reentrant_sink_lifecycle_stops_before_later_policy_or_tool_effects(
    trigger: type[RunnerEvent],
    operation: str,
    expected_checkpoint: str,
) -> None:
    observation = _observation()
    sink = ReentrantLifecycleSink(trigger, operation)
    response = _response()
    if trigger is ToolCallEvent:
        response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort(
        (_tool_binding(),) if trigger is ToolCallEvent else (),
        results=[{"must": "not run"}] if trigger is ToolCallEvent else [],
    )
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=(
            _plan_with_tools(observation)
            if trigger is ToolCallEvent
            else _plan(observation=observation, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
        ),
        client=client,
        tools=tools,
        sink=sink,
    )
    sink.session = session

    with pytest.raises(RunnerCancelled) as captured:
        await _within_timeout(
            session.run(ConductorRunRequest({"query": "reentrant lifecycle"}))
        )

    assert sink.called
    assert sink.outcome is not None
    provisional = (
        sink.outcome
        if operation == "cancel"
        else sink.outcome.cancellation
    )
    expected_reason = (
        "sink-requested-stop" if operation == "cancel" else "runner session closed"
    )
    assert provisional.reason == expected_reason
    assert captured.value.cancellation.observed_checkpoint == expected_checkpoint
    assert tools.calls == []
    if trigger is PolicyRequestEvent:
        assert client.requests == []
        prefix = [PolicyRequestEvent]
    else:
        assert len(client.requests) == 1
        prefix = [
            PolicyRequestEvent,
            PolicyRuntimeRequestEvent,
            PolicyRuntimeResponseEvent,
            PolicyResponseEvent,
            ToolCallEvent,
        ]
    assert [type(event) for event in sink.events] == [
        *prefix,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == list(range(len(sink.events)))
    assert not any(isinstance(event, ToolObservationEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_conductor_cancellation_sink_rejection_permanently_poisons_session_but_allows_cleanup() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    sink = RejectCancellationRequestOnceSink()
    session, _, tools, probe, _, _ = await _open(
        observation=observation,
        client=client,
        sink=sink,
    )

    with pytest.raises(RunnerEventSinkError) as captured:
        await session.cancel("operator-stop")

    assert captured.value.code == "event_sink_failed"
    assert type(captured.value.failed_event) is RunnerCancellationRequestedEvent
    assert captured.value.events_so_far == ()
    assert sink.events == []
    effect_snapshot = (
        tuple(client.requests),
        tuple(client.cancel_reasons),
        tuple(tools.calls),
        tuple(probe.checkpoints),
        tuple(sink.events),
    )
    with pytest.raises(RunnerStateError) as retry_cancel:
        await session.cancel("retry")
    with pytest.raises(RunnerStateError) as retry_run:
        await session.run(ConductorRunRequest({"query": "must not run"}))
    assert retry_cancel.value.code == retry_run.value.code == "session_failed"
    assert (
        tuple(client.requests),
        tuple(client.cancel_reasons),
        tuple(tools.calls),
        tuple(probe.checkpoints),
        tuple(sink.events),
    ) == effect_snapshot
    await session.close()
    assert client.close_calls == 1


async def test_conductor_event_sink_poison_rejects_later_run_and_cancel_without_effects() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    sink = RecordingEventSink()
    sink.reject_type = PolicyRuntimeRequestEvent
    session, _, tools, probe, _, _ = await _open(
        observation=observation,
        client=client,
        sink=sink,
    )
    with pytest.raises(RunnerEventSinkError) as captured:
        await session.run(ConductorRunRequest({"query": "poison"}))
    assert captured.value.events_so_far == tuple(sink.events)
    effect_snapshot = (
        tuple(client.requests),
        tuple(client.cancel_reasons),
        tuple(tools.calls),
        tuple(probe.checkpoints),
        tuple(sink.events),
    )

    with pytest.raises(RunnerStateError) as rerun:
        await session.run(ConductorRunRequest({"query": "retry"}))
    with pytest.raises(RunnerStateError) as cancel:
        await session.cancel("retry")

    assert rerun.value.code == cancel.value.code == "session_failed"
    assert (
        tuple(client.requests),
        tuple(client.cancel_reasons),
        tuple(tools.calls),
        tuple(probe.checkpoints),
        tuple(sink.events),
    ) == effect_snapshot
    await session.close()
    assert client.close_calls == 1


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
async def test_concurrent_session_close_callers_share_physical_cleanup_outcome(
    fails: bool,
) -> None:
    observation = _observation()
    close_error = (
        RuntimeError("Authorization: Bearer shared-session-close-secret")
        if fails
        else None
    )
    client = BlockingClosePolicyClient(observation, close_error=close_error)
    session, _, _, _, sink, _ = await _open(
        observation=observation,
        client=client,
    )

    first = asyncio.create_task(session.close())
    await _within_timeout(client.close_entered.wait())
    second = asyncio.create_task(session.close())
    await _advance_event_loop_once()
    assert first.done() is False
    assert second.done() is False
    client.release_close.set()
    outcomes = await _within_timeout(
        asyncio.gather(first, second, return_exceptions=True)
    )

    assert client.close_calls == 1
    assert sink.events == []
    if fails:
        assert all(type(outcome) is RunnerDependencyError for outcome in outcomes)
        assert {outcome.code for outcome in outcomes} == {"policy_close_failed"}
        assert {str(outcome) for outcome in outcomes} == {"policy runtime close failed"}
        assert all(
            "shared-session-close-secret" not in repr(outcome) + str(outcome)
            for outcome in outcomes
        )
        with pytest.raises(RunnerDependencyError) as later:
            await session.close()
        assert later.value is outcomes[0]
    else:
        owner, follower = outcomes
        assert owner.already_closed is False
        assert follower.already_closed is True
        assert owner.cancellation is follower.cancellation is None
        later = await session.close()
        assert later.already_closed is True
    assert client.close_calls == 1


async def test_conductor_cancel_after_tool_call_event_prevents_tool_effect_and_termination() -> None:
    observation = _observation()
    response = _response()
    response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    sink = BlockingEventSink(ToolCallEvent)
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
        sink=sink,
    )
    run_task = asyncio.create_task(
        session.run(ConductorRunRequest({"query": "cancel at tool boundary"}))
    )
    await _within_timeout(sink.entered.wait())

    cancel_task = asyncio.create_task(session.cancel("after-tool-call"))
    await _advance_event_loop_once()
    sink.release.set()
    cancellation = await _within_timeout(cancel_task)
    with pytest.raises(RunnerCancelled) as captured:
        await _within_timeout(run_task)

    assert cancellation.reason == "after-tool-call"
    assert cancellation.requested is True
    assert captured.value.cancellation.observed_checkpoint == "before_action"
    assert captured.value.cancellation.turn == 1
    assert captured.value.cancellation.call_id == "call-1"
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        ToolCallEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == list(range(7))
    assert not any(isinstance(event, ToolObservationEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_parent_task_cancellation_during_tool_is_not_dependency_failure() -> None:
    observation = _observation()
    response = _response()
    response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])

    class BlockingToolPort(RecordingToolPort):
        def __init__(self) -> None:
            super().__init__((_tool_binding(),))
            self.entered = asyncio.Event()

        async def invoke_tool(
            self,
            tool_id: str,
            arguments: Mapping[str, Any],
            *,
            timeout_ms: int,
        ) -> Mapping[str, Any]:
            self.calls.append((tool_id, dict(arguments), timeout_ms))
            self.entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    tools = BlockingToolPort()
    sink = RecordingEventSink()
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
        sink=sink,
    )
    run_task = asyncio.create_task(
        session.run(ConductorRunRequest({"query": "cancel active tool"}))
    )
    await _within_timeout(tools.entered.wait())

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert len(tools.calls) == 1
    assert not any(
        isinstance(event, ToolObservationEvent)
        for event in sink.events
    )
    assert not any(isinstance(event, RunnerErrorEvent) for event in sink.events)
    await session.close()


async def test_conductor_maps_tool_cancelled_error_to_fixed_dependency_ledger() -> None:
    observation = _observation()
    response = _response()
    response["output"] = [_function_call()]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),))
    tools.error = asyncio.CancelledError(
        "Authorization: Bearer tool-cancelled-error-secret"
    )
    sink = RecordingEventSink()
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation),
        client=client,
        tools=tools,
        sink=sink,
    )

    with pytest.raises(RunnerDependencyError) as captured:
        await session.run(ConductorRunRequest({"query": "cancelled tool boundary"}))

    assert captured.value.code == "tool_invoke_failed"
    assert str(captured.value) == "conductor tool invocation failed"
    assert type(captured.value.__cause__) is asyncio.CancelledError
    assert "tool-cancelled-error-secret" not in (
        repr(captured.value) + str(captured.value) + repr(tuple(sink.events))
    )
    assert captured.value.events_so_far == tuple(sink.events)
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        ToolCallEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.category == "dependency"
    assert error.code == "tool_invoke_failed"
    assert error.message == "conductor tool invocation failed"
    assert error.turn == 1
    assert error.call_id == "call-1"
    assert len(tools.calls) == 1
    assert not any(isinstance(event, ToolObservationEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_session_close_preserves_sink_failure_but_still_cancels_invoke_and_closes_binding() -> None:
    observation = _observation()
    client = InterruptiblePolicyClient(observation)
    sink = RejectCancellationRequestOnceSink()
    tools = RecordingToolPort()
    session, _, _, _, _, _ = await _open(
        observation=observation,
        client=client,
        tools=tools,
        sink=sink,
    )
    run_task = asyncio.create_task(
        session.run(ConductorRunRequest({"query": "close with poisoned sink"}))
    )
    await _within_timeout(client.entered.wait())

    with pytest.raises(RunnerEventSinkError) as captured:
        await _within_timeout(session.close())

    assert captured.value.code == "event_sink_failed"
    assert type(captured.value.failed_event) is RunnerCancellationRequestedEvent
    assert captured.value.events_so_far == tuple(sink.events)
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
    ]
    assert client.cancel_reasons == ["runner session closed"]
    assert client.close_calls == 1
    assert tools.calls == []
    with pytest.raises(RunnerStateError) as run_error:
        await _within_timeout(run_task)
    assert run_error.value.code == "session_failed"
    assert run_error.value.events_so_far == tuple(sink.events)
    with pytest.raises(RunnerEventSinkError) as later:
        await session.close()
    assert later.value is captured.value
    assert client.close_calls == 1


def _no_type_schema_parameters() -> list[dict[str, Any]]:
    return [
        _parameter(
            "number_value",
            {"minimum": 1, "maximum": 10, "multipleOf": 2},
        ),
        _parameter(
            "string_value",
            {"minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"},
        ),
        _parameter(
            "array_value",
            {
                "items": {"type": "integer"},
                "uniqueItems": True,
                "minItems": 1,
                "maxItems": 3,
            },
        ),
        _parameter(
            "object_value",
            {
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
                "additionalProperties": False,
            },
        ),
    ]


def _valid_no_type_schema_arguments() -> dict[str, Any]:
    return {
        "number_value": 4,
        "string_value": "good",
        "array_value": [1, 2],
        "object_value": {"enabled": True},
    }


async def test_conductor_applies_compiler_schema_keywords_without_type_by_instance_kind() -> None:
    observation = _observation()
    semantic = _tool_semantics(
        observation,
        parameters=_no_type_schema_parameters(),
    )
    arguments = _valid_no_type_schema_arguments()
    response = _response()
    response["output"] = [
        _function_call(arguments=json.dumps(arguments, separators=(",", ":")))
    ]
    client = RecordingPolicyClient(observation, responses=[response, _response("done")])
    tools = RecordingToolPort((_tool_binding(),), results=[{"validated": True}])
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "validate untyped schema"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert tools.calls == [("read-file", arguments, 9_000)]
    await session.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("minimum", "policy supplied a tool number below its minimum"),
        ("maximum", "policy supplied a tool number above its maximum"),
        ("multiple", "policy supplied a tool number outside its multiple"),
        ("min-length", "policy supplied a tool string below its minimum length"),
        ("max-length", "policy supplied a tool string above its maximum length"),
        ("pattern", "policy supplied a tool string outside its pattern"),
        ("array-items", "policy supplied a tool argument of the wrong type"),
        ("array-unique", "policy supplied duplicate tool array items"),
        ("array-min", "policy supplied too few tool array items"),
        ("array-max", "policy supplied too many tool array items"),
        ("object-property", "policy supplied a tool argument of the wrong type"),
        ("object-required", "policy omitted a required tool argument"),
        ("object-additional", "policy supplied an unknown tool argument"),
    ],
)
async def test_conductor_enforces_no_type_schema_keywords_before_tool_effect(
    mutation: str,
    message: str,
) -> None:
    observation = _observation()
    semantic = _tool_semantics(
        observation,
        parameters=_no_type_schema_parameters(),
    )
    arguments = _valid_no_type_schema_arguments()
    if mutation == "minimum":
        arguments["number_value"] = 0
    elif mutation == "maximum":
        arguments["number_value"] = 12
    elif mutation == "multiple":
        arguments["number_value"] = 3
    elif mutation == "min-length":
        arguments["string_value"] = "a"
    elif mutation == "max-length":
        arguments["string_value"] = "abcde"
    elif mutation == "pattern":
        arguments["string_value"] = "BAD"
    elif mutation == "array-items":
        arguments["array_value"] = [1, False]
    elif mutation == "array-unique":
        arguments["array_value"] = [1, 1]
    elif mutation == "array-min":
        arguments["array_value"] = []
    elif mutation == "array-max":
        arguments["array_value"] = [1, 2, 3, 4]
    elif mutation == "object-property":
        arguments["object_value"] = {"enabled": "yes"}
    elif mutation == "object-required":
        arguments["object_value"] = {}
    elif mutation == "object-additional":
        arguments["object_value"] = {"enabled": True, "foreign": True}
    else:
        raise AssertionError(mutation)
    response = _response()
    response["output"] = [
        _function_call(arguments=json.dumps(arguments, separators=(",", ":")))
    ]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    sink = RecordingEventSink()
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
        sink=sink,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "reject invalid untyped value"}))

    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == message
    assert captured.value.events_so_far == tuple(sink.events)
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.code == "policy_response_invalid"
    assert error.message == message
    assert error.turn == 1
    assert error.call_id == "call-1"
    assert not any(isinstance(event, ToolCallEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


@pytest.mark.parametrize(
    "schema",
    [
        {"minLength": -1},
        {"minLength": "1"},
        {"pattern": 7},
        {"pattern": "["},
        {"minimum": True},
        {"multipleOf": 0},
        {"minItems": -1},
        {"uniqueItems": "true"},
        {"items": []},
        {"properties": []},
        {"required": "value"},
        {
            "properties": {"value": {"type": "string"}},
            "required": ["missing"],
        },
        {"additionalProperties": "false"},
        {"minLength": 3, "maxLength": 2},
        {"minItems": 3, "maxItems": 2},
        {"minimum": 2, "maximum": 1},
        {"exclusiveMinimum": 2, "exclusiveMaximum": 2},
        {"enum": []},
        {"enum": ["duplicate", "duplicate"]},
    ],
    ids=[
        "negative-min-length",
        "noninteger-min-length",
        "nonstring-pattern",
        "invalid-pattern",
        "boolean-minimum",
        "zero-multiple",
        "negative-min-items",
        "nonboolean-unique-items",
        "nonobject-items",
        "nonobject-properties",
        "nonarray-required",
        "unknown-required-property",
        "nonboolean-additional-properties",
        "reversed-string-bounds",
        "reversed-array-bounds",
        "reversed-numeric-bounds",
        "empty-exclusive-range",
        "empty-enum",
        "duplicate-enum",
    ],
)
async def test_conductor_rejects_invalid_no_type_schema_values_and_combinations_at_open(
    schema: Mapping[str, Any],
) -> None:
    observation = _observation()
    semantic = _tool_semantics(
        observation,
        parameters=[_parameter("value", schema)],
    )
    await _assert_open_rejected(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        code="compiled_ir_mismatch",
        bindings=(_tool_binding(),),
    )


async def test_conductor_uses_json_equality_for_enum_const_and_unique_items() -> None:
    observation = _observation()
    mapping_literal = {
        "first": 1,
        "nested": {"items": [1, 2], "enabled": True},
    }
    parameters = [
        _parameter("enum_numeric", {"enum": [1]}),
        _parameter("const_numeric", {"const": 1}),
        _parameter("enum_mapping", {"enum": [mapping_literal]}),
        _parameter("const_mapping", {"const": mapping_literal}),
        _parameter(
            "ordered_arrays",
            {
                "type": "array",
                "items": {"type": "array", "items": {"type": "integer"}},
                "uniqueItems": True,
            },
        ),
    ]
    arguments = {
        "enum_numeric": 1.0,
        "const_numeric": 1.0,
        "enum_mapping": {
            "nested": {"enabled": True, "items": [1.0, 2.0]},
            "first": 1.0,
        },
        "const_mapping": {
            "nested": {"enabled": True, "items": [1.0, 2.0]},
            "first": 1.0,
        },
        "ordered_arrays": [[1, 2], [2, 1]],
    }
    semantic = _tool_semantics(observation, parameters=parameters)
    response = _response()
    response["output"] = [
        _function_call(arguments=json.dumps(arguments, separators=(",", ":")))
    ]
    client = RecordingPolicyClient(observation, responses=[response, _response("done")])
    tools = RecordingToolPort((_tool_binding(),), results=[{"accepted": True}])
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "json equality"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert tools.calls == [("read-file", arguments, 9_000)]
    await session.close()


@pytest.mark.parametrize(
    ("schema", "value", "message"),
    [
        (
            {"type": "array", "items": {}, "uniqueItems": True},
            [1, 1.0],
            "policy supplied duplicate tool array items",
        ),
        (
            {"type": "array", "items": {}, "uniqueItems": True},
            [
                {"first": 1, "nested": {"items": [1, 2]}},
                {"nested": {"items": [1.0, 2.0]}, "first": 1.0},
            ],
            "policy supplied duplicate tool array items",
        ),
        (
            {"enum": [1]},
            True,
            "policy supplied a tool argument outside its enum",
        ),
        (
            {"const": 1},
            True,
            "policy supplied a tool argument outside its const",
        ),
        (
            {"enum": [[1, 2]]},
            [2, 1],
            "policy supplied a tool argument outside its enum",
        ),
        (
            {"const": [1, 2]},
            [2, 1],
            "policy supplied a tool argument outside its const",
        ),
    ],
    ids=[
        "unique-numeric-equivalence",
        "unique-recursive-mapping-order",
        "enum-bool-distinct-from-number",
        "const-bool-distinct-from-number",
        "enum-array-order-sensitive",
        "const-array-order-sensitive",
    ],
)
async def test_conductor_rejects_json_equality_duplicates_and_nonmatches_before_tool_effect(
    schema: Mapping[str, Any],
    value: Any,
    message: str,
) -> None:
    observation = _observation()
    semantic = _tool_semantics(
        observation,
        parameters=[_parameter("value", schema)],
    )
    response = _response()
    response["output"] = [
        _function_call(
            arguments=json.dumps({"value": value}, separators=(",", ":"))
        )
    ]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    sink = RecordingEventSink()
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
        sink=sink,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "reject JSON nonmatch"}))

    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == message
    assert captured.value.events_so_far == tuple(sink.events)
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.code == "policy_response_invalid"
    assert error.message == message
    assert error.turn == 1
    assert error.call_id == "call-1"
    assert not any(isinstance(event, ToolCallEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_conductor_validates_huge_integer_and_decimal_multiple_of_without_float_overflow() -> None:
    observation = _observation()
    huge_integer = 10**400
    parameters = [
        _parameter("huge_integer", {"multipleOf": 1}),
        _parameter("decimal_value", {"multipleOf": 0.1}),
    ]
    arguments = {
        "huge_integer": huge_integer,
        "decimal_value": 0.3,
    }
    semantic = _tool_semantics(observation, parameters=parameters)
    response = _response()
    response["output"] = [
        _function_call(arguments=json.dumps(arguments, separators=(",", ":")))
    ]
    client = RecordingPolicyClient(observation, responses=[response, _response("done")])
    tools = RecordingToolPort((_tool_binding(),), results=[{"validated": True}])
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
    )

    result = await session.run(ConductorRunRequest({"query": "exact multiples"}))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert tools.calls == [("read-file", arguments, 9_000)]
    await session.close()


async def test_conductor_rejects_huge_integer_nonmultiple_without_tool_effect() -> None:
    observation = _observation()
    huge_integer = 10**400
    semantic = _tool_semantics(
        observation,
        parameters=[_parameter("huge_integer", {"multipleOf": 3})],
    )
    response = _response()
    response["output"] = [
        _function_call(
            arguments=json.dumps(
                {"huge_integer": huge_integer},
                separators=(",", ":"),
            )
        )
    ]
    client = RecordingPolicyClient(observation, responses=[response])
    tools = RecordingToolPort((_tool_binding(),), results=[{"must": "not run"}])
    sink = RecordingEventSink()
    session, _, _, _, _, _ = await _open(
        observation=observation,
        plan=_plan_with_tools(observation, semantics=semantic),
        client=client,
        tools=tools,
        sink=sink,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(ConductorRunRequest({"query": "reject nonmultiple"}))

    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == (
        "policy supplied a tool number outside its multiple"
    )
    assert captured.value.events_so_far == tuple(sink.events)
    assert tools.calls == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyRuntimeRequestEvent,
        PolicyRuntimeResponseEvent,
        PolicyResponseEvent,
        RunnerErrorEvent,
    ]
    error = sink.events[-1]
    assert isinstance(error, RunnerErrorEvent)
    assert error.code == "policy_response_invalid"
    assert error.message == "policy supplied a tool number outside its multiple"
    assert error.turn == 1
    assert error.call_id == "call-1"
    assert not any(isinstance(event, ToolCallEvent) for event in sink.events)
    assert not any(isinstance(event, RunnerTerminationEvent) for event in sink.events)
    await session.close()


async def test_session_close_shares_redacted_cancelled_error_subclass_and_remains_closed() -> None:
    observation = _observation()
    malicious_type = type(
        "Authorization_Bearer_wp6_session_close_cancelled_class",
        (asyncio.CancelledError,),
        {},
    )
    close_error = malicious_type(
        "Authorization: Bearer session-close-cancelled-secret"
    )
    client = BlockingClosePolicyClient(observation, close_error=close_error)
    sink = RecordingEventSink()
    session, _, tools, probe, _, _ = await _open(
        observation=observation,
        client=client,
        sink=sink,
    )

    first = asyncio.create_task(session.close())
    await _within_timeout(client.close_entered.wait())
    second = asyncio.create_task(session.close())
    await _advance_event_loop_once()
    assert first.done() is False
    assert second.done() is False
    client.release_close.set()
    outcomes = await _within_timeout(
        asyncio.gather(first, second, return_exceptions=True)
    )

    assert all(type(outcome) is RunnerDependencyError for outcome in outcomes)
    assert outcomes[0] is outcomes[1]
    shared = outcomes[0]
    assert shared.code == "policy_close_failed"
    assert str(shared) == "policy runtime close failed"
    assert shared.__cause__ is close_error
    visible = repr(shared) + str(shared)
    assert "session-close-cancelled-secret" not in visible
    assert "Authorization_Bearer_wp6_session_close_cancelled_class" not in visible
    assert client.close_calls == 1
    assert sink.events == []
    assert tools.calls == []
    assert probe.checkpoints == []
    with pytest.raises(RunnerDependencyError) as later:
        await session.close()
    assert later.value is shared
    with pytest.raises(RunnerStateError) as closed:
        await session.run(ConductorRunRequest({"query": "must remain closed"}))
    assert closed.value.code == "session_closed"
    assert client.requests == []
    assert client.close_calls == 1
