from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from agentic_coder_prototype.compilation.contracts import canonical_sha256
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.runners.base import (
    PolicyRuntimeInvokeRequest,
    PolicyRuntimeInvokeResult,
    RunnerDependencyError,
    RunnerOpenRequest,
    RunnerPolicyBindingError,
    RunnerProtocolError,
    thaw_json,
)
from breadboard.rl.harness.runners.conductor import PolicyRuntimeBinding


RUNTIME_ABI = "breadboard.conductor.v1"
IMPLEMENTATION_DIGEST = "sha256:" + "a" * 64
BINDING_SCHEMA = "bb.rl.policy-runtime-binding.v1"
SECRET_SENTINEL = "Authorization: Bearer wp6-secret-sentinel"
EMPTY_TOOL_SET_DIGEST = "sha256:5029177022904d8fce987dbe06dfd81794acef18f687ebef6221c9afaf0b7d63"


async def _within_timeout(awaitable: Any) -> Any:
    async with asyncio.timeout(2):
        return await awaitable


async def _advance_event_loop_once() -> None:
    loop = asyncio.get_running_loop()
    advanced: asyncio.Future[None] = loop.create_future()
    loop.call_soon(advanced.set_result, None)
    await _within_timeout(advanced)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _independent_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _policy_capabilities(**updates: Any) -> c.PolicyCapabilityVector:
    payload: dict[str, Any] = {
        "responses_protocol": "responses-v1",
        "modalities": ["text"],
        "tool_calling": True,
        "parallel_tool_calls": False,
        "token_ids": True,
        "token_logprobs": True,
        "routing_metadata": True,
        "cancellation": True,
        "max_context_tokens": 32_768,
        "max_output_tokens": 4_096,
        "policy_slot_count": 1,
        "request_features": ["json_mode", "seed"],
    }
    payload.update(updates)
    return c.PolicyCapabilityVector.model_validate(payload)


def _capability_digest(
    *,
    protocol_abi: str,
    model_digest: str,
    tokenizer_digest: str,
    checkpoint_digest: str,
    capabilities: c.PolicyCapabilityVector,
) -> str:
    return _independent_digest(
        {
            "schema_version": "bb.rl.policy-selection-capabilities.v1",
            "protocol_abi": protocol_abi,
            "model_digest": model_digest,
            "tokenizer_digest": tokenizer_digest,
            "checkpoint_digest": checkpoint_digest,
            "capabilities": capabilities.model_dump(mode="json"),
        }
    )


def _observation(label: str = "a", **updates: Any) -> c.PolicyCapabilityObservation:
    capabilities = updates.pop("capabilities", _policy_capabilities())
    payload: dict[str, Any] = {
        "registry_revision_digest": _digest(f"registry-{label}"),
        "route_id": f"route-{label}",
        "route_revision_digest": _digest(f"route-revision-{label}"),
        "provider_id": f"provider-{label}",
        "protocol_abi": "responses-v1",
        "bridge_instance_id": f"bridge-{label}",
        "bridge_build_digest": _digest(f"bridge-build-{label}"),
        "model_id": f"model-{label}",
        "model_digest": _digest(f"model-{label}"),
        "tokenizer_digest": _digest(f"tokenizer-{label}"),
        "checkpoint_digest": _digest(f"checkpoint-{label}"),
        "credential_handle_id": f"credential-{label}",
        "credential_handle_version_digest": _digest(f"credential-version-{label}"),
        "subject_scope_digest": _digest(f"subject-{label}"),
        "capabilities": capabilities.model_dump(mode="json"),
        "provenance": {
            "kind": "startup_probe",
            "issuer_id": "operator-control-plane",
            "signer_key_id": "startup-probe-key",
            "environment_digest": _digest(f"environment-{label}"),
            "evidence_digest": _digest(f"evidence-{label}"),
            "validity": {
                "issued_at": "2026-07-10T11:00:00Z",
                "not_before": "2026-07-10T11:00:00Z",
                "expires_at": "2026-07-10T13:00:00Z",
            },
        },
        "revocation": {
            "scope_digest": _digest(f"subject-{label}"),
            "epoch": 7,
            "state_digest": _digest(f"revocation-{label}"),
        },
    }
    payload.update(updates)
    if "subject_scope_digest" in updates and "revocation" not in updates:
        payload["revocation"]["scope_digest"] = payload["subject_scope_digest"]
    resolved_capabilities = c.PolicyCapabilityVector.model_validate(payload["capabilities"])
    payload.setdefault(
        "capability_digest",
        _capability_digest(
            protocol_abi=payload["protocol_abi"],
            model_digest=payload["model_digest"],
            tokenizer_digest=payload["tokenizer_digest"],
            checkpoint_digest=payload["checkpoint_digest"],
            capabilities=resolved_capabilities,
        ),
    )
    return c.PolicyCapabilityObservation.model_validate(payload)


def _empty_semantics(*, observation: c.PolicyCapabilityObservation) -> dict[str, Any]:
    tool_catalog_text = "No tools admitted."
    model_id = observation.model_id
    slot_id = f"slot-{model_id}"
    node_id = _digest("config-node")
    semantic: dict[str, Any] = {
        "runtime": {
            "runner_adapter_id": "breadboard.conductor.v1",
            "runtime_abi": RUNTIME_ABI,
        },
        "providers": {
            "default_model_id": model_id,
            "models": [
                {
                    "adapter_id": "responses-adapter",
                    "context_length": 32_768,
                    "display_name": "inert display name",
                    "metadata": {"family": "inert-metadata"},
                    "model_id": model_id,
                    "params": {"temperature": 0.25, "seed": 17},
                    "policy_slot_id": slot_id,
                    "provider_id": observation.provider_id,
                    "request_schema_digest": _digest("request-schema"),
                    "request_schema_id": "breadboard.provider-request.responses.v1",
                    "routing": {
                        "disable_native_tools_on_probe_failure": False,
                        "disable_stream_on_probe_failure": False,
                        "fallback_model_ids": [],
                    },
                }
            ],
            "policy_slots": [
                {
                    "adapter_id": "responses-adapter",
                    "binding_state": "operator_resolution_required",
                    "model_id": model_id,
                    "request_schema_id": "breadboard.provider-request.responses.v1",
                    "requested_credential_handle_id": observation.credential_handle_id,
                    "requested_route_handle_id": observation.route_id,
                    "slot_id": slot_id,
                    "trainable_json_pointers": ["/params/temperature"],
                }
            ],
            "provider_tools": {"api_variant": "responses", "use_native": True},
        },
        "prompts": {
            "dedupe": False,
            "dialects": {"default": []},
            "environment": {},
            "injection": {"per_turn_order": [], "system_order": []},
            "packs": [],
            "synthesis": {
                "detail": {},
                "enabled": True,
                "renderer_id": "breadboard.tool-catalog.v1",
                "selection": {},
                "templates": [],
                "tool_catalog_template": None,
            },
            "tool_prompt_mode": "system_once",
            "variants": [
                {
                    "config_node_id": node_id,
                    "dialect_ids": [],
                    "effective_tool_ids": [],
                    "mode_id": "build",
                    "model_id": model_id,
                    "per_turn": {
                        "fragments": [],
                        "renderer_id": "breadboard.prompt-assembly.v1",
                        "template_source_ids": [],
                        "text": "Per-turn compiled instruction.",
                        "text_digest": _digest("Per-turn compiled instruction."),
                    },
                    "system": {
                        "fragments": [],
                        "renderer_id": "breadboard.prompt-assembly.v1",
                        "template_source_ids": [],
                        "text": "System compiled instruction.",
                        "text_digest": _digest("System compiled instruction."),
                    },
                    "tool_catalog": {
                        "effective_tool_ids": [],
                        "renderer_id": "breadboard.tool-catalog.v1",
                        "template_source_ids": [],
                        "text": tool_catalog_text,
                        "text_digest": _digest(tool_catalog_text),
                    },
                    "tool_set_digest": EMPTY_TOOL_SET_DIGEST,
                    "variant_id": _digest("variant"),
                }
            ],
        },
        "modes": [
            {
                "dialect_ids": [],
                "disabled_tool_ids": [],
                "enabled": True,
                "enabled_tool_ids": [],
                "mode_id": "build",
                "prompt": {"literal": "Per-turn compiled instruction."},
                "prompt_source_id": "mode:build",
            }
        ],
        "loop": {"sequence": [{"condition": None, "mode_id": "build"}]},
        "tools": {
            "aliases": [],
            "applied_overlays": [],
            "binding_requests": [],
            "definitions": [],
            "dialect_policy": {},
            "mark_task_complete": False,
            "packs": [],
            "registry_members": [],
            "selected_tool_ids": [],
        },
        "completion": {},
        "concurrency": {},
    }
    object_fields = (
        "metadata",
        "providers",
        "prompts",
        "tools",
        "plugins",
        "guardrails",
        "task",
        "runtime",
        "loop",
        "turn_strategy",
        "features",
        "completion",
        "concurrency",
        "permissions",
        "enhanced_tools",
        "replay",
        "long_running",
        "terminal_sessions",
        "observability",
    )
    for field_name in object_fields:
        semantic.setdefault(field_name, {})
    semantic["root_config_node_id"] = node_id
    semantic["team"] = None
    semantic["optimizer_mutable_pointers"] = []
    root_semantic = {
        field_name: json.loads(json.dumps(semantic[field_name]))
        for field_name in object_fields
    }
    root_semantic["team"] = None
    root_semantic["modes"] = json.loads(json.dumps(semantic["modes"]))
    root_semantic["optimizer_mutable_pointers"] = []
    semantic["config_nodes"] = [
        {"node_id": node_id, "semantic_config": root_semantic}
    ]
    return semantic


def _plan(
    *,
    label: str = "a",
    observation: c.PolicyCapabilityObservation | None = None,
    authority: c.PolicyCapabilityObservation | None = None,
    semantics: Mapping[str, Any] | None = None,
    tools: tuple[c.ToolGrant, ...] = (),
    policy_slot_ids: tuple[str, ...] | None = None,
    limit_updates: Mapping[str, int] | None = None,
    adapter_id: str = "breadboard.conductor.v1",
    runtime_abi: str = RUNTIME_ABI,
    implementation_digest: str = IMPLEMENTATION_DIGEST,
) -> c.EffectiveExecutionPlan:
    observed = observation or _observation(label)
    granted = authority or observed
    runner = c.RunnerGrant(
        adapter_id=adapter_id,
        runtime_abi=runtime_abi,
        implementation_digest=implementation_digest,
    )
    slots = tuple(
        c.PolicySlotGrant(
            slot_id=slot_id,
            protocol_abi=granted.protocol_abi,
            route_id=granted.route_id,
            secret_handle_id=granted.credential_handle_id,
            model_digest=granted.model_digest,
            tokenizer_digest=granted.tokenizer_digest,
            checkpoint_digest=granted.checkpoint_digest,
            required_policy_capabilities_digest=granted.capability_digest,
        )
        for slot_id in (policy_slot_ids or (f"slot-{granted.model_id}",))
    )
    route = c.RouteGrant(
        route_id=granted.route_id,
        route_revision_digest=granted.route_revision_digest,
        protocol_abi=granted.protocol_abi,
        credential_handle_id=granted.credential_handle_id,
    )
    secret = c.SecretHandleGrant(
        handle_id=granted.credential_handle_id,
        handle_version_digest=granted.credential_handle_version_digest,
        scope_digest=granted.subject_scope_digest,
    )
    limit_payload = {
        "max_turns": 4,
        "action_timeout_ms": 9_000,
        "observation_bytes": 20_000,
        "response_bytes": 100_000,
        "artifact_bytes_each": 10_000,
        "artifact_bytes_total": 20_000,
        "transcript_bytes": 100_000,
        "setup_timeout_ms": 5_000,
        "verifier_timeout_ms": 17_000,
    }
    limit_payload.update(limit_updates or {})
    limits = c.ExecutionLimits.model_validate(limit_payload)
    sandbox = c.SandboxGrant(
        runtime_id="sandbox",
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest=_digest("sandbox-driver"),
        runtime_binary_digest=_digest("runtime-binary"),
        security_policy_digest=_digest("security-policy"),
        image_digest=_digest("image"),
        network_policy_digest=_digest("network-policy"),
        egress_route_ids=(),
        mounts=(),
    )
    resources = c.ResourceLimits(
        cpu_millis=1_000,
        memory_bytes=1_000_000,
        pids=32,
        storage_bytes=1_000_000,
        open_files=128,
        wall_time_ms=60_000,
    )
    task = c.TaskGrant(
        task_contract_digest=_digest("task-contract"),
        task_binding_digest=_digest("task-binding"),
        repository_snapshot_digest=None,
        dataset_digests=(),
        input_artifact_digests=(),
    )
    verifier = c.VerifierGrant(
        verifier_id="verifier",
        implementation_digest=_digest("verifier-implementation"),
        image_digest=_digest("verifier-image"),
        executable_digest=_digest("verifier-executable"),
        code_digest=_digest("verifier-code"),
        input_schema_digest=_digest("verifier-input"),
        result_schema_digest=_digest("verifier-result"),
        network_policy_digest=_digest("verifier-network"),
        secret_handle_ids=(),
    )
    artifacts = c.ArtifactPolicyGrant(
        allowed_roles=(), max_each_bytes=10_000, max_total_bytes=20_000
    )
    evidence = c.PolicyRef(policy_id="evidence", revision_digest=_digest("evidence-policy"))
    retention = c.PolicyRef(
        policy_id="retention", revision_digest=_digest("retention-policy")
    )
    capabilities = c.CapabilityVector(
        runner=runner,
        tools=tools,
        setup_plans=(),
        routes=(route,),
        secret_handles=(secret,),
        sandbox=sandbox,
        resources=resources,
        limits=limits,
        task=task,
        policy_slots=slots,
        verifier=verifier,
        mutable_pointers=(),
        artifacts=artifacts,
        evidence=evidence,
        retention=retention,
    )
    effective_semantics = dict(semantics or _empty_semantics(observation=granted))
    semantic_digest = canonical_sha256(
        {"schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID, "config": effective_semantics}
    )
    compiler = c.CompilerIdentity(
        compiler_id="compiler",
        semantic_version="1.0.0",
        code_digest=_digest("compiler-code"),
        source_schema_id="source.v1",
        source_schema_digest=_digest("source-schema"),
        manifest_schema_digest=_digest("manifest-schema"),
        canonicalizer_id="canonical-json-v1",
        runtime_abi=runtime_abi,
    )
    compiled = c.CompiledArtifactIdentity(
        manifest_digest=_digest("manifest"),
        bundle_digest=_digest("bundle"),
        closure_digest=_digest("closure"),
        compiler_input_digest=_digest("compiler-input"),
        semantic_digest=semantic_digest,
        compiler=compiler,
        provenance_digest=_digest("provenance"),
        diagnostics_digest=_digest("diagnostics"),
    )
    return c.EffectiveExecutionPlan(
        subject_digest=granted.subject_scope_digest,
        base_compiled=compiled,
        base_receipt_digest=_digest("receipt"),
        selector_digest=_digest("selector"),
        config_set_digest=None,
        admitted_set_root=_digest("admitted-set"),
        selection_record_digest=_digest("selection"),
        task_eligibility_digest=_digest("task-eligibility"),
        policy_capability_observation_digest=observed.canonical_digest(),
        policy_capability_digest=granted.capability_digest,
        overlay_applications=(),
        final_receipt_digest=_digest("receipt"),
        final_semantic_digest=semantic_digest,
        effective_semantics=effective_semantics,
        effective_capabilities=capabilities,
        effective_capability_digest=capabilities.canonical_digest(),
        pins=(),
        runner=runner,
        policy_slots=slots,
        sandbox=sandbox,
        verifier=verifier,
        task=task,
        artifacts=artifacts,
        evidence=evidence,
        retention=retention,
        revocation=c.RevocationBinding(
            scope_digest=granted.subject_scope_digest,
            epoch=7,
            state_digest=_digest(f"revocation-{label}"),
        ),
    )


def _open_request(
    *,
    episode_id: str = "episode-a",
    plan: c.EffectiveExecutionPlan | None = None,
) -> RunnerOpenRequest:
    return RunnerOpenRequest(
        episode_id=episode_id,
        effective_plan=plan or _plan(),
    )


def _response(label: str = "a") -> dict[str, Any]:
    return {
        "id": f"response-{label}",
        "model": f"model-{label}",
        "model_version": f"version-{label}",
        "checkpoint_digest": _digest(f"checkpoint-{label}"),
        "token_ids": [11, 12, 13],
        "token_logprobs": [-0.1, -0.2, -0.3],
        "loss_mask": [0, 1, 1],
        "routing": {
            "route_id": f"route-{label}",
            "routed_experts": [2, 5],
            "route_probabilities": [0.25, 0.75],
        },
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": f"done-{label}"}],
            }
        ],
    }


class RecordingPolicyClient:
    def __init__(
        self,
        observation: c.PolicyCapabilityObservation,
        *,
        responses: list[Mapping[str, Any]] | None = None,
    ) -> None:
        self.observation = observation
        self.responses = list(responses or [_response()])
        self.observe_calls = 0
        self.requests: list[PolicyRuntimeInvokeRequest] = []
        self.cancel_reasons: list[str] = []
        self.close_calls = 0
        self.invoke_error: BaseException | None = None
        self.cancel_error: BaseException | None = None
        self.close_error: BaseException | None = None

    def observe(self) -> c.PolicyCapabilityObservation:
        self.observe_calls += 1
        return self.observation

    async def invoke(self, request: PolicyRuntimeInvokeRequest) -> PolicyRuntimeInvokeResult:
        self.requests.append(request)
        if self.invoke_error is not None:
            raise self.invoke_error
        response = self.responses.pop(0)
        return PolicyRuntimeInvokeResult(
            response_payload=response,
            response_digest=_independent_digest(response),
        )

    async def cancel(self, reason: str) -> None:
        self.cancel_reasons.append(reason)
        if self.cancel_error is not None:
            raise self.cancel_error

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class InterruptiblePolicyClient(RecordingPolicyClient):
    def __init__(self, observation: c.PolicyCapabilityObservation) -> None:
        super().__init__(observation)
        self.entered = asyncio.Event()
        self.interrupted = asyncio.Event()

    async def invoke(self, request: PolicyRuntimeInvokeRequest) -> PolicyRuntimeInvokeResult:
        self.requests.append(request)
        self.entered.set()
        await _within_timeout(self.interrupted.wait())
        raise asyncio.CancelledError

    async def cancel(self, reason: str) -> None:
        self.cancel_reasons.append(reason)
        self.interrupted.set()


class CancelFailsDuringClosePolicyClient(RecordingPolicyClient):
    def __init__(self, observation: c.PolicyCapabilityObservation) -> None:
        super().__init__(observation)
        self.invoke_entered = asyncio.Event()
        self.cancel_entered = asyncio.Event()

    async def invoke(self, request: PolicyRuntimeInvokeRequest) -> PolicyRuntimeInvokeResult:
        self.requests.append(request)
        self.invoke_entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancel(self, reason: str) -> None:
        self.cancel_reasons.append(reason)
        self.cancel_entered.set()
        raise RuntimeError(SECRET_SENTINEL)


class StubbornPolicyClient(RecordingPolicyClient):
    def __init__(self, observation: c.PolicyCapabilityObservation) -> None:
        super().__init__(observation)
        self.invoke_entered = asyncio.Event()
        self.task_cancelled = asyncio.Event()
        self.release_invoke = asyncio.Event()
        self.physical_close_entered = asyncio.Event()

    async def invoke(self, request: PolicyRuntimeInvokeRequest) -> PolicyRuntimeInvokeResult:
        self.requests.append(request)
        self.invoke_entered.set()
        try:
            await self.release_invoke.wait()
        except asyncio.CancelledError:
            self.task_cancelled.set()
            await self.release_invoke.wait()
            raise
        raise AssertionError("invoke was released without task cancellation")

    async def cancel(self, reason: str) -> None:
        self.cancel_reasons.append(reason)

    async def close(self) -> None:
        self.close_calls += 1
        self.physical_close_entered.set()


class BlockingClosePolicyClient(RecordingPolicyClient):
    def __init__(
        self,
        observation: c.PolicyCapabilityObservation,
        *,
        close_error: BaseException | None = None,
    ) -> None:
        super().__init__(observation)
        self.close_entered = asyncio.Event()
        self.release_close = asyncio.Event()
        self.blocking_close_error = close_error

    async def close(self) -> None:
        self.close_calls += 1
        self.close_entered.set()
        await self.release_close.wait()
        if self.blocking_close_error is not None:
            raise self.blocking_close_error


def _invoke_request(binding: PolicyRuntimeBinding, *, label: str = "one") -> PolicyRuntimeInvokeRequest:
    payload = {"model": "model-a", "input": label}
    return PolicyRuntimeInvokeRequest(
        episode_id=binding.episode_id,
        effective_plan_digest=binding.effective_plan_digest,
        binding_digest=binding.binding_digest,
        policy_slot_id=binding.policy_slot_ids[0],
        request_digest=_independent_digest(payload),
        request_payload=payload,
        turn=1,
        attempt=1,
    )


def _mutate_observation(
    observation: c.PolicyCapabilityObservation,
    **updates: Any,
) -> c.PolicyCapabilityObservation:
    payload = observation.model_dump(mode="json")
    payload.update(updates)
    if any(
        field in updates
        for field in (
            "protocol_abi",
            "model_digest",
            "tokenizer_digest",
            "checkpoint_digest",
            "capabilities",
        )
    ):
        capabilities = c.PolicyCapabilityVector.model_validate(payload["capabilities"])
        payload["capability_digest"] = _capability_digest(
            protocol_abi=payload["protocol_abi"],
            model_digest=payload["model_digest"],
            tokenizer_digest=payload["tokenizer_digest"],
            checkpoint_digest=payload["checkpoint_digest"],
            capabilities=capabilities,
        )
    if "subject_scope_digest" in updates:
        payload["revocation"]["scope_digest"] = payload["subject_scope_digest"]
    return c.PolicyCapabilityObservation.model_validate(payload)


def test_policy_runtime_carriers_recursively_snapshot_complete_payloads() -> None:
    request_payload = {"input": [{"text": "original"}]}
    response_payload = _response()
    request = PolicyRuntimeInvokeRequest(
        episode_id="episode-a",
        effective_plan_digest=_digest("plan"),
        binding_digest=_digest("binding"),
        policy_slot_id="slot-model-a",
        request_digest=_independent_digest(request_payload),
        request_payload=request_payload,
        turn=1,
        attempt=1,
    )
    result = PolicyRuntimeInvokeResult(
        response_payload=response_payload,
        response_digest=_independent_digest(response_payload),
    )

    request_payload["input"][0]["text"] = "mutated"
    response_payload["token_ids"].append(999)
    response_payload["routing"]["routed_experts"].append(999)

    assert thaw_json(request.request_payload) == {"input": [{"text": "original"}]}
    frozen_response = thaw_json(result.response_payload)
    assert frozen_response["token_ids"] == [11, 12, 13]
    assert frozen_response["token_logprobs"] == [-0.1, -0.2, -0.3]
    assert frozen_response["loss_mask"] == [0, 1, 1]
    assert frozen_response["routing"] == {
        "route_id": "route-a",
        "routed_experts": [2, 5],
        "route_probabilities": [0.25, 0.75],
    }


def test_policy_runtime_binding_digest_is_independently_bound_to_episode_plan_observation_and_slots() -> None:
    observation = _observation()
    request = _open_request(plan=_plan(observation=observation))
    client = RecordingPolicyClient(observation)

    binding = PolicyRuntimeBinding(request, client)

    expected = _independent_digest(
        {
            "schema_version": BINDING_SCHEMA,
            "episode_id": request.episode_id,
            "effective_plan_digest": request.effective_plan_digest,
            "policy_capability_observation_digest": observation.canonical_digest(),
            "policy_slot_ids": [f"slot-{observation.model_id}"],
        }
    )
    assert binding.binding_digest == expected
    assert binding.episode_id == "episode-a"
    assert binding.effective_plan_digest == request.effective_plan_digest
    assert binding.policy_slot_ids == ("slot-model-a",)
    assert binding.policy_capability_observation == observation
    assert binding.policy_capability_observation is not observation
    assert binding.first_request_digest is None
    assert client.observe_calls == 1
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("route_id", "foreign-route", "policy_observation_mismatch"),
        ("protocol_abi", "foreign-abi", "policy_observation_mismatch"),
        ("credential_handle_id", "foreign-credential", "policy_observation_mismatch"),
        ("model_digest", _digest("foreign-model"), "policy_observation_mismatch"),
        ("tokenizer_digest", _digest("foreign-tokenizer"), "policy_observation_mismatch"),
        ("checkpoint_digest", _digest("foreign-checkpoint"), "policy_observation_mismatch"),
        ("route_revision_digest", _digest("foreign-route-revision"), "policy_observation_mismatch"),
        ("credential_handle_version_digest", _digest("foreign-credential-version"), "policy_observation_mismatch"),
        ("subject_scope_digest", _digest("foreign-subject"), "policy_observation_mismatch"),
    ],
)
def test_policy_runtime_binding_rejects_every_grant_or_observation_identity_mismatch_before_invoke(
    field: str,
    value: Any,
    code: str,
) -> None:
    authority = _observation()
    observed = _mutate_observation(authority, **{field: value})
    plan = _plan(observation=observed, authority=authority)
    client = RecordingPolicyClient(observed)

    with pytest.raises(RunnerPolicyBindingError) as captured:
        PolicyRuntimeBinding(_open_request(plan=plan), client)

    assert captured.value.category == "policy_binding"
    assert captured.value.code == code
    assert client.observe_calls == 1
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


@pytest.mark.parametrize(
    "field",
    [
        "provider_id",
        "bridge_instance_id",
        "bridge_build_digest",
        "registry_revision_digest",
    ],
)
def test_policy_runtime_binding_rejects_full_observation_drift_not_just_slot_fields(
    field: str,
) -> None:
    observation = _observation()
    plan = _plan(observation=observation)
    value = _digest(f"foreign-{field}") if field.endswith("digest") else f"foreign-{field}"
    drifted = _mutate_observation(observation, **{field: value})
    client = RecordingPolicyClient(drifted)

    with pytest.raises(RunnerPolicyBindingError) as captured:
        PolicyRuntimeBinding(_open_request(plan=plan), client)

    assert captured.value.code == "policy_observation_mismatch"
    assert client.observe_calls == 1
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


@pytest.mark.parametrize(
    "capability_updates",
    [
        {"cancellation": False},
        {"token_ids": False},
        {"token_logprobs": False},
        {"routing_metadata": False},
        {"tool_calling": False},
        {"responses_protocol": "foreign-responses"},
    ],
)
def test_policy_runtime_binding_rejects_capability_drift_before_invoke(
    capability_updates: dict[str, Any],
) -> None:
    authority = _observation()
    capabilities_payload = authority.capabilities.model_dump(mode="json")
    capabilities_payload.update(capability_updates)
    observed = _mutate_observation(authority, capabilities=capabilities_payload)
    plan = _plan(observation=observed, authority=authority)
    client = RecordingPolicyClient(observed)

    with pytest.raises(RunnerPolicyBindingError) as captured:
        PolicyRuntimeBinding(_open_request(plan=plan), client)

    assert captured.value.code == "policy_observation_mismatch"
    assert client.observe_calls == 1
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


class ObservationSubclass(c.PolicyCapabilityObservation):
    pass


class ForeignObservation:
    pass


@pytest.mark.parametrize("foreign", [ForeignObservation(), object()])
def test_policy_runtime_binding_rejects_foreign_observation_carriers_before_invoke(
    foreign: Any,
) -> None:
    client = RecordingPolicyClient(_observation())
    client.observation = foreign

    with pytest.raises(RunnerPolicyBindingError) as captured:
        PolicyRuntimeBinding(_open_request(), client)

    assert captured.value.code == "policy_observation_mismatch"
    assert client.observe_calls == 1
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


def test_policy_runtime_binding_rejects_observation_subclass_instead_of_accepting_spoofable_equality() -> None:
    observation = _observation()
    subclass = ObservationSubclass.model_validate(observation.model_dump(mode="json"))
    client = RecordingPolicyClient(subclass)

    with pytest.raises(RunnerPolicyBindingError) as captured:
        PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)

    assert captured.value.code == "policy_observation_mismatch"
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


async def test_policy_runtime_binding_allows_only_one_episode_owner_claim() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(
        _open_request(plan=_plan(observation=observation)),
        client,
    )

    await binding.claim()
    with pytest.raises(RunnerPolicyBindingError) as captured:
        await binding.claim()

    assert captured.value.code == "binding_already_claimed"
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0
    await binding.close()
    assert client.close_calls == 1


async def test_binding_invoke_records_first_request_once_and_rejects_foreign_identity_before_transport() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation, responses=[_response(), _response()])
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    first = _invoke_request(binding, label="first")

    result = await binding.invoke(first)

    assert thaw_json(result.response_payload) == _response()
    assert binding.first_request_digest == first.request_digest
    assert client.requests == [first]

    second_payload = {"model": "model-a", "input": "second"}
    second = PolicyRuntimeInvokeRequest(
        episode_id=binding.episode_id,
        effective_plan_digest=binding.effective_plan_digest,
        binding_digest=binding.binding_digest,
        policy_slot_id=binding.policy_slot_ids[0],
        request_digest=_independent_digest(second_payload),
        request_payload=second_payload,
        turn=2,
        attempt=1,
    )
    await binding.invoke(second)
    assert binding.first_request_digest == first.request_digest
    assert client.requests == [first, second]

    wrong = PolicyRuntimeInvokeRequest(
        episode_id="episode-foreign",
        effective_plan_digest=binding.effective_plan_digest,
        binding_digest=binding.binding_digest,
        policy_slot_id=binding.policy_slot_ids[0],
        request_digest=second.request_digest,
        request_payload=second.request_payload,
        turn=3,
        attempt=1,
    )
    with pytest.raises(RunnerPolicyBindingError) as captured:
        await binding.invoke(wrong)
    assert captured.value.code == "binding_identity_mismatch"
    assert client.requests == [first, second]


async def test_binding_generate_adapts_terminal_policy_requests_to_the_bound_runtime() -> None:
    observation = _observation()
    response = _response("terminal")
    client = RecordingPolicyClient(observation, responses=[response])
    binding = PolicyRuntimeBinding(
        _open_request(plan=_plan(observation=observation)),
        client,
    )
    request_payload = {"model": "model-a", "input": "repair the workspace"}

    result = await binding.generate(request_payload)

    assert result == response
    assert len(client.requests) == 1
    request = client.requests[0]
    assert thaw_json(request.request_payload) == request_payload
    assert request.request_digest == _independent_digest(request_payload)
    assert request.policy_slot_id == binding.policy_slot_ids[0]
    assert request.turn == 1
    assert request.attempt == 1
    assert binding.first_request_digest == request.request_digest


async def test_binding_generate_rejects_a_response_digest_mismatch() -> None:
    observation = _observation()

    class DigestMismatchClient(RecordingPolicyClient):
        async def invoke(
            self, request: PolicyRuntimeInvokeRequest
        ) -> PolicyRuntimeInvokeResult:
            result = await super().invoke(request)
            return replace(result, response_digest=_digest("mismatch"))

    client = DigestMismatchClient(observation)
    binding = PolicyRuntimeBinding(
        _open_request(plan=_plan(observation=observation)),
        client,
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await binding.generate({"model": "model-a", "input": "repair"})

    assert captured.value.code == "policy_response_digest_mismatch"
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("field", "value", "code", "message"),
    [
        pytest.param(
            "episode_id",
            "episode-foreign",
            "binding_identity_mismatch",
            "policy invocation identity does not match the binding",
            id="episode",
        ),
        pytest.param(
            "effective_plan_digest",
            _digest("foreign-plan"),
            "binding_identity_mismatch",
            "policy invocation identity does not match the binding",
            id="plan",
        ),
        pytest.param(
            "binding_digest",
            _digest("foreign-binding"),
            "binding_identity_mismatch",
            "policy invocation identity does not match the binding",
            id="binding",
        ),
        pytest.param(
            "policy_slot_id",
            "slot-foreign",
            "policy_slot_mismatch",
            "policy invocation slot is not bound",
            id="slot",
        ),
    ],
)
async def test_binding_rejects_every_foreign_invoke_identity_before_transport(
    field: str,
    value: str,
    code: str,
    message: str,
) -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    request = replace(_invoke_request(binding), **{field: value})

    with pytest.raises(RunnerPolicyBindingError) as captured:
        await binding.invoke(request)

    assert captured.value.category == "policy_binding"
    assert captured.value.code == code
    assert str(captured.value) == message
    assert client.requests == []


async def test_binding_rejects_foreign_invoke_request_and_result_carriers() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)

    with pytest.raises(TypeError, match="request must be an exact PolicyRuntimeInvokeRequest"):
        await binding.invoke(object())
    assert client.requests == []

    class ForeignResultClient(RecordingPolicyClient):
        async def invoke(self, request: PolicyRuntimeInvokeRequest) -> Any:
            self.requests.append(request)
            return {"response_payload": _response(), "response_digest": _digest("spoof")}

    foreign_client = ForeignResultClient(observation)
    foreign_binding = PolicyRuntimeBinding(
        _open_request(plan=_plan(observation=observation)),
        foreign_client,
    )
    request = _invoke_request(foreign_binding)
    with pytest.raises(RunnerProtocolError) as captured:
        await foreign_binding.invoke(request)
    assert captured.value.code == "policy_response_invalid"
    assert str(captured.value) == "policy runtime returned an invalid response"
    assert foreign_client.requests == [request]


async def test_binding_maps_provider_exception_to_fixed_redacted_dependency_failure() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    malicious_type = type("Authorization_Bearer_wp6_secret_class", (RuntimeError,), {})
    client.invoke_error = malicious_type(SECRET_SENTINEL)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)

    with pytest.raises(RunnerDependencyError) as captured:
        await binding.invoke(_invoke_request(binding))

    assert captured.value.category == "dependency"
    assert captured.value.code == "policy_invoke_failed"
    assert str(captured.value) == "policy runtime invocation failed"
    assert captured.value.__cause__ is client.invoke_error
    visible = repr(captured.value) + str(captured.value)
    assert SECRET_SENTINEL not in visible
    assert "Authorization_Bearer_wp6_secret_class" not in visible


async def test_binding_redacts_malicious_runner_error_from_policy_boundary() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    malicious_type = type(
        "Authorization_Bearer_wp6_runner_error_class",
        (RunnerDependencyError,),
        {},
    )
    client.invoke_error = malicious_type(SECRET_SENTINEL, code="secret_dependency_code")
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    request = _invoke_request(binding)

    with pytest.raises(RunnerDependencyError) as captured:
        await binding.invoke(request)

    assert captured.value.category == "dependency"
    assert captured.value.code == "policy_invoke_failed"
    assert str(captured.value) == "policy runtime invocation failed"
    assert captured.value.__cause__ is client.invoke_error
    assert client.requests == [request]
    visible = repr(captured.value) + str(captured.value)
    assert SECRET_SENTINEL not in visible
    assert "secret_dependency_code" not in visible
    assert "Authorization_Bearer_wp6_runner_error_class" not in visible


async def test_binding_cancel_failure_is_fixed_redacted_and_first_reason_wins() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    malicious_type = type("Authorization_Bearer_wp6_cancel_secret_class", (RuntimeError,), {})
    client.cancel_error = malicious_type(SECRET_SENTINEL)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)

    with pytest.raises(RunnerDependencyError) as captured:
        await binding.cancel(" operator-stop ")

    assert captured.value.category == "dependency"
    assert captured.value.code == "policy_cancel_failed"
    assert str(captured.value) == "policy runtime cancellation failed"
    assert captured.value.__cause__ is client.cancel_error
    visible = repr(captured.value) + str(captured.value)
    assert SECRET_SENTINEL not in visible
    assert "Authorization_Bearer_wp6_cancel_secret_class" not in visible
    await binding.cancel("replacement-reason")
    assert client.cancel_reasons == ["operator-stop"]


async def test_binding_cancel_failure_during_close_still_cancels_active_task_and_physically_closes_once() -> None:
    observation = _observation()
    client = CancelFailsDuringClosePolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    invoke_task = asyncio.create_task(binding.invoke(_invoke_request(binding)))
    await _within_timeout(client.invoke_entered.wait())

    close_task = asyncio.create_task(binding.close())
    await _within_timeout(client.cancel_entered.wait())
    with pytest.raises(RunnerDependencyError) as captured:
        await _within_timeout(close_task)

    assert captured.value.code == "policy_cancel_failed"
    assert str(captured.value) == "policy runtime cancellation failed"
    assert captured.value.__cause__ is not None
    assert SECRET_SENTINEL not in repr(captured.value) + str(captured.value)
    assert client.cancel_reasons == ["runner session closed"]
    assert client.close_calls == 1
    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(invoke_task)
    with pytest.raises(RunnerDependencyError) as later:
        await _within_timeout(binding.close())
    assert later.value is captured.value
    assert client.close_calls == 1


async def test_binding_cancel_joins_stubborn_active_invoke_before_physical_close() -> None:
    observation = _observation()
    client = StubbornPolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    invoke_task = asyncio.create_task(binding.invoke(_invoke_request(binding)))
    await _within_timeout(client.invoke_entered.wait())

    close_task = asyncio.create_task(binding.close())
    await _within_timeout(client.task_cancelled.wait())
    assert close_task.done() is False
    assert client.physical_close_entered.is_set() is False
    client.release_invoke.set()

    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(invoke_task)
    await _within_timeout(close_task)
    assert client.cancel_reasons == ["runner session closed"]
    assert client.close_calls == 1
    assert client.physical_close_entered.is_set()


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
async def test_concurrent_binding_close_callers_share_one_physical_outcome(fails: bool) -> None:
    observation = _observation()
    malicious_type = type("Authorization_Bearer_wp6_close_class", (RuntimeError,), {})
    close_error = malicious_type(SECRET_SENTINEL) if fails else None
    client = BlockingClosePolicyClient(observation, close_error=close_error)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)

    first = asyncio.create_task(binding.close())
    await _within_timeout(client.close_entered.wait())
    second = asyncio.create_task(binding.close())
    await _advance_event_loop_once()
    assert first.done() is False
    assert second.done() is False
    client.release_close.set()
    outcomes = await _within_timeout(
        asyncio.gather(first, second, return_exceptions=True)
    )

    assert client.close_calls == 1
    if fails:
        assert all(type(outcome) is RunnerDependencyError for outcome in outcomes)
        assert {outcome.code for outcome in outcomes} == {"policy_close_failed"}
        assert {str(outcome) for outcome in outcomes} == {"policy runtime close failed"}
        assert all(
            SECRET_SENTINEL not in repr(outcome) + str(outcome)
            and "Authorization_Bearer_wp6_close_class" not in repr(outcome) + str(outcome)
            for outcome in outcomes
        )
    else:
        assert outcomes == [None, None]
    later = await _within_timeout(
        asyncio.gather(binding.close(), return_exceptions=True)
    )
    if fails:
        assert len(later) == 1
        assert type(later[0]) is RunnerDependencyError
        assert later[0].code == "policy_close_failed"
    else:
        assert later == [None]
    assert client.close_calls == 1


async def test_binding_cancel_interrupts_active_invoke_without_external_release_and_close_is_once() -> None:
    observation = _observation()
    client = InterruptiblePolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    invoke_task = asyncio.create_task(binding.invoke(_invoke_request(binding)))
    await _within_timeout(client.entered.wait())

    await binding.cancel("operator-stop")

    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(invoke_task)
    assert client.cancel_reasons == ["operator-stop"]
    assert client.interrupted.is_set()

    first_close, second_close = await asyncio.gather(binding.close(), binding.close())
    assert first_close is None
    assert second_close is None
    await binding.close()
    assert client.cancel_reasons == ["operator-stop"]
    assert client.close_calls == 1


async def test_binding_rejects_concurrent_second_invoke_without_crossing_transport() -> None:
    observation = _observation()
    client = InterruptiblePolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    first = _invoke_request(binding, label="first")
    second = _invoke_request(binding, label="second")
    first_task = asyncio.create_task(binding.invoke(first))
    await _within_timeout(client.entered.wait())

    with pytest.raises(RunnerPolicyBindingError) as captured:
        await binding.invoke(second)

    assert captured.value.code == "binding_already_claimed"
    assert str(captured.value) == "policy runtime binding is unavailable"
    assert client.requests == [first]
    assert binding.first_request_digest == first.request_digest
    await binding.cancel("finish-first")
    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(first_task)
    await binding.close()
    assert client.close_calls == 1


async def test_concurrent_bindings_keep_client_request_cancel_and_close_state_episode_local() -> None:
    observation_a = _observation("a")
    observation_b = _observation("b")
    client_a = InterruptiblePolicyClient(observation_a)
    client_b = InterruptiblePolicyClient(observation_b)
    binding_a = PolicyRuntimeBinding(
        _open_request(episode_id="episode-a", plan=_plan(label="a", observation=observation_a)),
        client_a,
    )
    binding_b = PolicyRuntimeBinding(
        _open_request(episode_id="episode-b", plan=_plan(label="b", observation=observation_b)),
        client_b,
    )
    task_a = asyncio.create_task(binding_a.invoke(_invoke_request(binding_a, label="a")))
    task_b = asyncio.create_task(binding_b.invoke(_invoke_request(binding_b, label="b")))
    await _within_timeout(asyncio.gather(client_a.entered.wait(), client_b.entered.wait()))

    await binding_a.cancel("cancel-a")

    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(task_a)
    assert not task_b.done()
    assert client_a.cancel_reasons == ["cancel-a"]
    assert client_b.cancel_reasons == []
    assert [request.episode_id for request in client_a.requests] == ["episode-a"]
    assert [request.episode_id for request in client_b.requests] == ["episode-b"]

    await binding_b.cancel("cancel-b")
    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(task_b)
    await asyncio.gather(binding_a.close(), binding_b.close())
    assert client_a.cancel_reasons == ["cancel-a"]
    assert client_b.cancel_reasons == ["cancel-b"]
    assert client_a.close_calls == 1
    assert client_b.close_calls == 1
    assert client_a is not client_b
    assert binding_a.policy_capability_observation != binding_b.policy_capability_observation


async def test_binding_rejects_request_digest_mismatch_before_claim_or_transport() -> None:
    observation = _observation()
    client = RecordingPolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    valid = _invoke_request(binding)
    forged = replace(valid, request_digest=_digest("forged-request-digest"))

    with pytest.raises(RunnerPolicyBindingError) as captured:
        await binding.invoke(forged)

    assert captured.value.code == "binding_identity_mismatch"
    assert str(captured.value) == (
        "policy invocation request digest does not match its payload"
    )
    assert binding.first_request_digest is None
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0
    await binding.close()
    assert client.close_calls == 1


async def test_binding_maps_cancelled_error_from_cancel_boundary_and_still_closes_active_invoke() -> None:
    class CancelledCancelPolicyClient(CancelFailsDuringClosePolicyClient):
        async def cancel(self, reason: str) -> None:
            self.cancel_reasons.append(reason)
            self.cancel_entered.set()
            raise asyncio.CancelledError(SECRET_SENTINEL)

    observation = _observation()
    client = CancelledCancelPolicyClient(observation)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)
    invoke_task = asyncio.create_task(binding.invoke(_invoke_request(binding)))
    await _within_timeout(client.invoke_entered.wait())

    close_task = asyncio.create_task(binding.close())
    await _within_timeout(client.cancel_entered.wait())
    with pytest.raises(RunnerDependencyError) as captured:
        await _within_timeout(close_task)

    assert captured.value.code == "policy_cancel_failed"
    assert str(captured.value) == "policy runtime cancellation failed"
    assert type(captured.value.__cause__) is asyncio.CancelledError
    assert SECRET_SENTINEL not in repr(captured.value) + str(captured.value)
    assert client.cancel_reasons == ["runner session closed"]
    assert client.close_calls == 1
    with pytest.raises(asyncio.CancelledError):
        await _within_timeout(invoke_task)
    with pytest.raises(RunnerDependencyError) as later:
        await binding.close()
    assert later.value is captured.value
    assert client.close_calls == 1


def test_binding_maps_cancelled_error_from_observation_boundary_to_fixed_redacted_failure() -> None:
    class CancelledObservationPolicyClient(RecordingPolicyClient):
        def __init__(self, observation: c.PolicyCapabilityObservation) -> None:
            super().__init__(observation)
            self.observe_error = asyncio.CancelledError(SECRET_SENTINEL)

        def observe(self) -> c.PolicyCapabilityObservation:
            self.observe_calls += 1
            raise self.observe_error

    observation = _observation()
    client = CancelledObservationPolicyClient(observation)

    with pytest.raises(RunnerPolicyBindingError) as captured:
        PolicyRuntimeBinding(
            _open_request(plan=_plan(observation=observation)),
            client,
        )

    assert captured.value.code == "policy_observation_mismatch"
    assert str(captured.value) == (
        "policy capability observation could not be obtained"
    )
    assert captured.value.__cause__ is client.observe_error
    assert SECRET_SENTINEL not in repr(captured.value) + str(captured.value)
    assert client.observe_calls == 1
    assert client.requests == []
    assert client.cancel_reasons == []
    assert client.close_calls == 0


async def test_binding_maps_cancelled_error_from_close_boundary_to_one_cached_shared_failure() -> None:
    observation = _observation()
    malicious_type = type(
        "Authorization_Bearer_wp6_close_cancelled_class",
        (asyncio.CancelledError,),
        {},
    )
    close_error = malicious_type(SECRET_SENTINEL)
    client = BlockingClosePolicyClient(observation, close_error=close_error)
    binding = PolicyRuntimeBinding(_open_request(plan=_plan(observation=observation)), client)

    first = asyncio.create_task(binding.close())
    await _within_timeout(client.close_entered.wait())
    second = asyncio.create_task(binding.close())
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
    assert SECRET_SENTINEL not in visible
    assert "Authorization_Bearer_wp6_close_cancelled_class" not in visible
    assert client.close_calls == 1
    with pytest.raises(RunnerDependencyError) as later:
        await binding.close()
    assert later.value is shared
    assert client.close_calls == 1
    with pytest.raises(RunnerPolicyBindingError) as unavailable:
        await binding.invoke(_invoke_request(binding))
    assert unavailable.value.code == "binding_already_claimed"
    assert client.requests == []


def test_effective_semantics_accept_empty_values_without_loosening_keys() -> None:
    observation = _observation()
    semantics = copy.deepcopy(_empty_semantics(observation=observation))
    semantics["prompts"]["variants"][0]["system"]["text"] = ""
    semantics["prompts"]["variants"][0]["per_turn"]["text"] = ""
    semantics["prompts"]["variants"][0]["tool_catalog"]["text"] = ""
    semantics["prompts"]["variants"][0]["tool_catalog"]["text"] = (
        "shell:\n\tcommand: write task output\r\n"
    )

    plan = _plan(observation=observation, semantics=semantics)

    assert plan.effective_semantics["prompts"]["variants"][0]["system"]["text"] == ""
    assert plan.effective_semantics["prompts"]["variants"][0]["tool_catalog"]["text"] == (
        "shell:\n\tcommand: write task output\r\n"
    )
    assert plan.final_semantic_digest == canonical_sha256(
        {
            "schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
            "config": semantics,
        }
    )

    malformed = copy.deepcopy(semantics)
    malformed[""] = "still forbidden"
    with pytest.raises(ValueError, match="object key"):
        _plan(observation=observation, semantics=malformed)


def test_effective_semantics_empty_value_boundary_retains_text_and_number_guards() -> None:
    observation = _observation()
    for invalid in ("\x00", "\x01", "\x7f", "\x85", "e\u0301", "\ud800"):
        semantics = copy.deepcopy(_empty_semantics(observation=observation))
        semantics["prompts"]["variants"][0]["system"]["text"] = invalid
        with pytest.raises(ValueError):
            _plan(observation=observation, semantics=semantics)
    semantics = copy.deepcopy(_empty_semantics(observation=observation))
    semantics["limits"] = {"unsafe": 2**53 + 1}
    with pytest.raises(ValueError, match="finite binary64"):
        _plan(observation=observation, semantics=semantics)
