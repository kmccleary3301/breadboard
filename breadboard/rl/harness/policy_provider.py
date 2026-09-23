from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Iterator, Mapping
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from concurrent.futures import Future
import json
import os
import traceback
import re
import threading
from typing import Any, overload
from urllib.parse import urlsplit, urlunsplit

from breadboard_engine.compilation.contracts import (
    CompiledConfigManifest,
    canonical_sha256,
    require_sha256,
)
from breadboard_engine.compilation.provider_response import (
    CompiledNativeResponseBinding,
    MINI_RESPONSE_CONSUMER_ID,
    PI_RESPONSE_CONSUMER_ID,
    OMP_RESPONSE_CONSUMER_ID,
    OPENHANDS_RESPONSE_CONSUMER_ID,
    admit_native_response_binding,
    is_native_response_consumer_registered,
)
from breadboard_engine.provider.contracts import (
    OpenAICompletionsProviderProfile,
    ProviderContractError,
    ProviderMessage,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.native_response import NativeProviderResponse
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime

from .contracts import EffectiveExecutionPlan, PolicyBindingRef, PolicyCapabilityObservation
from .runners.base import (
    FrozenJsonObject,
    MiniProviderFailure,
    PolicyRuntimeClientPort,
    PolicyRuntimeInvokeRequest,
    PolicyRuntimeInvokeResult,
    RunnerDependencyError,
    RunnerPolicyBindingError,
    RunnerProtocolError,
    freeze_json_object,
    thaw_json,
)
from .service import PolicyRuntimeClientResolver


def _mini_model_response(raw_response: Mapping[str, Any]) -> Any:
    """Re-run LiteLLM's OpenAI response conversion for Mini's native consumer."""
    from openai.types.chat import ChatCompletion
    from litellm import ModelResponse
    from litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response import (
        convert_to_model_response_object,
    )

    sdk_response = ChatCompletion.model_validate(thaw_json(raw_response))
    return convert_to_model_response_object(
        response_object=sdk_response.model_dump(),
        model_response_object=ModelResponse(),
    )


def _mini_provider_exception(exception: Exception, *, model: str) -> MiniProviderFailure:
    """Map an OpenAI SDK failure through the locked LiteLLM exception mapper."""
    import litellm
    from litellm.litellm_core_utils.exception_mapping_utils import exception_type

    previous_suppress_debug_info = litellm.suppress_debug_info
    # The mapper otherwise prints help text to stdout; it does not alter str(e).
    litellm.suppress_debug_info = True
    try:
        exception_type(
            model="openai/" + model,
            original_exception=exception,
            custom_llm_provider="openai",
            completion_kwargs={},
            extra_kwargs={},
        )
    except Exception as mapped:
        return MiniProviderFailure(mapped, traceback.format_exc())
    finally:
        litellm.suppress_debug_info = previous_suppress_debug_info
    raise RuntimeError("LiteLLM exception mapping returned without raising")


def _is_mini_provider_exception(exception: BaseException) -> bool:
    import openai

    return isinstance(exception, (openai.APIStatusError, openai.APIConnectionError))


def _provider_descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id="openai",
        runtime_id="openai_chat",
        default_api_variant="chat",
        supports_native_tools=True,
        supports_streaming=True,
        supports_reasoning_traces=True,
        supports_cache_control=False,
        tool_schema_format="openai",
        base_url=None,
        api_key_env=None,
        default_headers={},
    )
def _project_effective_chat_tool(definition: Mapping[str, Any]) -> dict[str, Any]:
    model_name = definition.get("model_name")
    description = definition.get("description")
    parameters = definition.get("parameters")
    routing = definition.get("provider_routing")
    if (
        type(model_name) is not str
        or not model_name
        or type(description) is not str
        or not isinstance(parameters, tuple)
        or not isinstance(routing, Mapping)
    ):
        raise ValueError("effective target tool definition is malformed")
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in parameters:
        if (
            not isinstance(parameter, Mapping)
            or type(parameter.get("name")) is not str
            or not parameter["name"]
            or parameter["name"] in properties
            or not isinstance(parameter.get("schema"), Mapping)
            or not isinstance(parameter.get("validation_rules"), Mapping)
        ):
            raise ValueError("effective target tool parameter is malformed")
        schema = thaw_json(parameter["schema"])
        schema.update(thaw_json(parameter["validation_rules"]))
        if parameter.get("has_default") is True:
            schema["default"] = thaw_json(parameter.get("default_value"))
        if parameter.get("description") is not None:
            schema["description"] = parameter["description"]
        properties[parameter["name"]] = schema
        if parameter.get("required") is True:
            required.append(parameter["name"])
    required_order = definition.get("required_order")
    if required_order is not None:
        if (
            not isinstance(required_order, tuple)
            or any(type(name) is not str or not name for name in required_order)
            or len(set(required_order)) != len(required_order)
            or set(required_order) != set(required)
        ):
            raise ValueError("effective target tool required order is malformed")
        required = list(required_order)
    parameter_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    openai_routing = routing.get("openai")
    if isinstance(openai_routing, Mapping) and "additionalProperties" in openai_routing:
        additional_properties = openai_routing["additionalProperties"]
        if type(additional_properties) is not bool:
            raise ValueError("effective target tool routing is malformed")
        parameter_schema["additionalProperties"] = additional_properties
    return {
        "type": "function",
        "function": {
            "name": model_name,
            "description": description,
            "parameters": parameter_schema,
        },
    }
def _join_prompt_parts(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def _target_mode_projections(
    semantics: Mapping[str, Any],
) -> Iterator[tuple[str, str, tuple[dict[str, Any], ...]]]:
    prompts = semantics.get("prompts")
    providers = semantics.get("providers")
    tools = semantics.get("tools")
    modes = semantics.get("modes")
    root_id = semantics.get("root_config_node_id")
    if not all(isinstance(value, Mapping) for value in (prompts, providers, tools)):
        raise ValueError("effective semantics cannot bind the selected E4 target")
    variants = prompts.get("variants")
    definitions = tools.get("definitions")
    if (
        not isinstance(variants, tuple)
        or not isinstance(definitions, tuple)
        or not isinstance(modes, tuple)
        or not modes
    ):
        raise ValueError("effective semantics cannot bind the selected E4 target")
    definitions_by_id = {
        item.get("tool_id"): item for item in definitions if isinstance(item, Mapping)
    }
    variant_by_key = {
        (item.get("config_node_id"), item.get("mode_id"), item.get("model_id")): item
        for item in variants
        if isinstance(item, Mapping)
    }
    if len(definitions_by_id) != len(definitions) or len(variant_by_key) != len(variants):
        raise ValueError("effective target semantics contain duplicate identities")
    for mode in modes:
        if not isinstance(mode, Mapping) or mode.get("enabled") is not True:
            raise ValueError("effective target mode is invalid")
        variant = variant_by_key.get(
            (root_id, mode.get("mode_id"), providers.get("default_model_id"))
        )
        enabled_ids = mode.get("enabled_tool_ids")
        if not isinstance(variant, Mapping) or not isinstance(enabled_ids, tuple):
            raise ValueError("effective target prompt variant is missing")
        system = variant.get("system")
        per_turn = variant.get("per_turn")
        catalog = variant.get("tool_catalog")
        if not all(isinstance(value, Mapping) for value in (system, per_turn, catalog)):
            raise ValueError("effective target prompt variant is malformed")
        system_text, per_turn_text, catalog_text = (
            system.get("text"), per_turn.get("text"), catalog.get("text")
        )
        if not all(type(value) is str for value in (system_text, per_turn_text, catalog_text)):
            raise ValueError("effective target prompt text is malformed")
        tool_prompt_mode = prompts.get("tool_prompt_mode")
        if tool_prompt_mode == "system_once":
            system_text = _join_prompt_parts(system_text, catalog_text)
        elif tool_prompt_mode == "per_turn_append":
            per_turn_text = _join_prompt_parts(per_turn_text, catalog_text)
        elif tool_prompt_mode != "native_only":
            raise ValueError("effective target tool prompt mode is unsupported")
        if any(tool_id not in definitions_by_id for tool_id in enabled_ids):
            raise ValueError("effective target mode references an undeclared tool")
        yield system_text, per_turn_text, tuple(
            _project_effective_chat_tool(definitions_by_id[tool_id])
            for tool_id in enabled_ids
        )


_TARGET_BINDING_FIELDS = (
    "target_id", "overlay_id", "descriptor_digest", "execution_config_digest",
    "overlay_digest", "rendered_prompt_digest", "ordered_tool_names",
    "input_digest", "index_digest", "descriptor_bytes_digest",
    "target_schema_version", "request_schema_version", "renderer_id",
)
_TARGET_BINDING_DIGESTS = (
    "descriptor_digest", "execution_config_digest", "overlay_digest",
    "rendered_prompt_digest", "input_digest", "index_digest",
    "descriptor_bytes_digest", "tool_surface_digest", "harness_lock_digest",
)
def _checked_target_binding(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = metadata.get("e4_target")
    if not isinstance(binding, Mapping) or type(binding.get("version")) is not int:
        raise ValueError("compiled semantics lack a supported E4 target binding")
    version = binding["version"]
    extra_fields = {"runtime_profile"} if version in (2, 3) else set()
    expected_fields = {
        *_TARGET_BINDING_FIELDS, "version", "tool_surface_digest", "harness_lock_digest", *extra_fields
    }
    renderer_id = binding.get("renderer_id")
    deferred_targets = {
        OPENHANDS_RESPONSE_CONSUMER_ID: "openhands-sdk@1.47.0",
        PI_RESPONSE_CONSUMER_ID: "pi@0.73.1",
        OMP_RESPONSE_CONSUMER_ID: "oh-my-pi@18.1.17",
    }
    if (
        version not in (1, 2, 3)
        or set(binding) != expected_fields
        or version == 1
        and renderer_id != "breadboard.e4.legacy-string-template.v1"
        or version == 2
        and (
            renderer_id != MINI_RESPONSE_CONSUMER_ID
            or binding.get("target_id") != "mini-swe-agent@2.4.6"
            or not isinstance(binding.get("runtime_profile"), Mapping)
        )
        or version == 3
        and (
            renderer_id not in deferred_targets
            or binding.get("target_id") != deferred_targets.get(renderer_id)
            or not isinstance(binding.get("runtime_profile"), Mapping)
            or binding.get("rendered_prompt_digest") is not None
        )
    ):
        raise ValueError("compiled semantics lack a supported E4 target binding")
    for name in _TARGET_BINDING_FIELDS:
        if name == "ordered_tool_names":
            continue
        value = binding[name]
        if name == "rendered_prompt_digest" and version == 3:
            if value is not None:
                raise ValueError("deferred native prompt digest must be null")
            continue
        if type(value) is not str or not value:
            raise ValueError("compiled target identity is malformed")
    names = binding["ordered_tool_names"]
    if (
        not isinstance(names, tuple)
        or any(type(name) is not str or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("compiled target tool order is malformed")
    for name in _TARGET_BINDING_DIGESTS:
        if name == "rendered_prompt_digest" and version == 3:
            continue
        require_sha256(binding[name], name)
    return binding


def _validate_request_features(
    profile: OpenAICompletionsProviderProfile,
    observation: PolicyCapabilityObservation,
    *,
    tools: bool,
    target_projection: E4TargetPolicyProjection | None,
    episode_id: str,
    effective_plan_digest: str,
) -> set[str]:
    required = set(profile.required_request_features(tools=tools))
    if (
        target_projection is not None
        and target_projection.renderer_id == OPENHANDS_RESPONSE_CONSUMER_ID
    ):
        # The SDK supplies raw HTTP instead of profile.chat_request(), which
        # inserts n=1. The pinned SDK omits n; native admission rejects that key.
        required.remove("n")
    elif (
        target_projection is not None
        and target_projection.renderer_id == PI_RESPONSE_CONSUMER_ID
    ):
        # Pi's buildParams removes n and adds store=false before transport.
        required.remove("n")
        required.add("store")
    missing = required.difference(observation.capabilities.request_features)
    unsupported_tools = tools and (
        not observation.capabilities.tool_calling
        or not profile.capabilities.supports_tools
        or (
            profile.request_policy.strict_tools is not None
            and not profile.capabilities.supports_strict_tools
        )
    )
    if missing or unsupported_tools:
        raise RunnerPolicyBindingError(
            "admitted provider does not support the selected request features",
            code="provider_request_features_unsupported",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
        )
    return required


def _validate_owned_profile_observation(
    *,
    profile: OpenAICompletionsProviderProfile,
    target_projection: E4TargetPolicyProjection | None,
    observation: PolicyCapabilityObservation,
    authority_model_id: str,
    authority_wire_model: str,
    credential_handle_id: str,
    episode_id: str,
    effective_plan_digest: str,
) -> None:
    capabilities = observation.capabilities
    if (
        profile.provider_id != observation.provider_id
        or profile.runtime_id != _provider_descriptor().runtime_id
        or authority_model_id != observation.model_id
        or profile.model != authority_wire_model
        or credential_handle_id != observation.credential_handle_id
        or profile.context_window != capabilities.max_context_tokens
        or profile.max_output_tokens != capabilities.max_output_tokens
        or profile.capabilities.supports_tools != capabilities.tool_calling
        or not capabilities.cancellation
        or "text" not in capabilities.modalities
    ):
        raise RunnerPolicyBindingError(
            "episode provider profile does not match the admitted observation",
            code="provider_profile_mismatch",
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
        )
    _validate_request_features(
        profile,
        observation,
        tools=False,
        target_projection=target_projection,
        episode_id=episode_id,
        effective_plan_digest=effective_plan_digest,
    )


@dataclass(frozen=True, slots=True)
class E4TargetPolicyProjection:
    target_id: str
    overlay_id: str
    descriptor_digest: str
    execution_config_digest: str
    overlay_digest: str
    rendered_prompt_digest: str | None
    system_prompt: str | None
    ordered_tool_names: tuple[str, ...]
    chat_tools: tuple[FrozenJsonObject, ...]
    input_digest: str
    index_digest: str
    descriptor_bytes_digest: str
    target_schema_version: str
    request_schema_version: str
    renderer_id: str
    runtime_profile: FrozenJsonObject | None = None
    source_manifest: CompiledConfigManifest | None = None

    @classmethod
    def from_compiled(
        cls, manifest: CompiledConfigManifest,
    ) -> E4TargetPolicyProjection:
        """Project verified compiler output; callers retain pin/admission authority."""
        if type(manifest) is not CompiledConfigManifest:
            raise TypeError("target projection requires a CompiledConfigManifest")
        semantic = manifest.semantic
        binding = _checked_target_binding(semantic.metadata)
        view = {
            "root_config_node_id": semantic.root_config_node_id,
            "providers": semantic.providers,
            "prompts": semantic.prompts,
            "tools": semantic.tools,
            "modes": semantic.modes,
        }
        system_prompt: str | None = None
        chat_tools: tuple[dict[str, Any], ...] | None = None
        for system_text, per_turn_text, projected_tools in _target_mode_projections(view):
            if per_turn_text or (
                system_prompt is not None
                and (system_text != system_prompt or projected_tools != chat_tools)
            ):
                raise ValueError("compiled target modes have divergent projections")
            system_prompt, chat_tools = system_text, projected_tools
        if chat_tools is None:
            raise ValueError("compiled target has no model-visible tool projection")
        deferred_prompt = binding["version"] == 3
        if deferred_prompt:
            if system_prompt != "":
                raise ValueError("OpenHands target must defer its static system prompt")
            system_prompt_value: str | None = None
        else:
            if system_prompt is None:
                raise ValueError("compiled target has no model-visible projection")
            system_prompt_value = system_prompt
        prompt_digest = binding["rendered_prompt_digest"]
        if (
            (
                deferred_prompt
                and prompt_digest is not None
            )
            or (
                not deferred_prompt
                and canonical_sha256({"text": system_prompt}) != prompt_digest
            )
            or canonical_sha256(chat_tools) != binding["tool_surface_digest"]
            or tuple(tool["function"]["name"] for tool in chat_tools)
            != binding["ordered_tool_names"]
        ):
            raise ValueError("compiled target outputs differ from their source binding")
        return cls(
            **{name: binding[name] for name in _TARGET_BINDING_FIELDS},
            system_prompt=system_prompt_value,
            chat_tools=tuple(
                freeze_json_object(tool, field_name="compiled E4 target tool")
                for tool in chat_tools
            ),
            runtime_profile=(
                freeze_json_object(
                    binding["runtime_profile"],
                    field_name="compiled E4 native profile",
                )
                if binding["version"] in (2, 3) else None
            ),
            source_manifest=manifest if binding["version"] in (2, 3) else None,
        )

    def identity_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "overlay_id": self.overlay_id,
            "descriptor_digest": self.descriptor_digest,
            "execution_config_digest": self.execution_config_digest,
            "overlay_digest": self.overlay_digest,
            "rendered_prompt_digest": self.rendered_prompt_digest,
            "ordered_tool_names": list(self.ordered_tool_names),
            "tool_surface_digest": canonical_sha256(
                [thaw_json(tool) for tool in self.chat_tools]
            ),
            **(
                {"runtime_profile_digest": canonical_sha256(self.runtime_profile)}
                if self.runtime_profile is not None else {}
            ),
        }

    def validate_semantics(self, semantics: Mapping[str, Any]) -> None:
        metadata = semantics.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("effective semantics lack target identity")
        binding = _checked_target_binding(metadata)
        if any(binding[name] != getattr(self, name) for name in _TARGET_BINDING_FIELDS):
            raise ValueError("effective target binding differs from the requested target")
        if binding.get("runtime_profile") != self.runtime_profile:
            raise ValueError("effective runtime profile differs from the compiled target")
        # The selected plan owns its complete lock, including runtime configuration.
        # Bootstrap candidates may differ there while sharing this target projection.
        target_tools = tuple(thaw_json(tool) for tool in self.chat_tools)
        if canonical_sha256(target_tools) != binding["tool_surface_digest"]:
            raise ValueError("effective target tool identity differs from the projection")
        deferred_prompt = binding["version"] == 3
        for system_text, per_turn_text, projected_tools in _target_mode_projections(semantics):
            if (
                (
                    deferred_prompt
                    and (system_text != "" or per_turn_text != "")
                )
                or (
                    not deferred_prompt
                    and system_text != self.system_prompt
                )
                or per_turn_text != ""
                or projected_tools != target_tools
            ):
                raise ValueError("effective semantics do not match the selected E4 target")

_MAX_NATIVE_HTTP_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_NATIVE_HTTP_RESPONSE_BYTES = 4 * 1024 * 1024
_NATIVE_HTTP_SECRET_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}
)


@dataclass(frozen=True, slots=True)
class _PendingNativeHTTPRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    request_payload: Mapping[str, Any]
    request_digest: str


class EpisodeOpenAICompletionsPolicyClient:
    """One policy client, transport, and worker owned by one episode."""

    def __init__(
        self,
        *,
        episode_id: str,
        effective_plan_digest: str,
        observation: PolicyCapabilityObservation,
        profile: OpenAICompletionsProviderProfile,
        timeout_seconds: float = 600.0,
        target_projection: E4TargetPolicyProjection | None = None,
        max_requests: int | None = None,
        on_close: Callable[[EpisodeOpenAICompletionsPolicyClient], Awaitable[None]]
        | None = None,
    ) -> None:
        if type(observation) is not PolicyCapabilityObservation:
            raise TypeError("observation must be an exact PolicyCapabilityObservation")
        if type(profile) is not OpenAICompletionsProviderProfile:
            raise TypeError("profile must be an exact OpenAICompletionsProviderProfile")
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 3_600
        ):
            raise ValueError("timeout_seconds must be within (0, 3600]")
        if max_requests is not None and (
            type(max_requests) is not int or not 0 < max_requests <= 2**53 - 1
        ):
            raise ValueError("max_requests must be a positive safe integer")
        if (
            target_projection is not None
            and type(target_projection) is not E4TargetPolicyProjection
        ):
            raise TypeError(
                "target_projection must be an exact E4TargetPolicyProjection"
            )
        if target_projection is not None and target_projection.runtime_profile is not None:
            if timeout_seconds != 45:
                raise ValueError("source-native targets require their 45-second provider timeout")
        self._episode_id = episode_id
        self._effective_plan_digest = effective_plan_digest
        self._observation = observation
        self._profile: OpenAICompletionsProviderProfile | None = profile
        self._runtime = OpenAIChatRuntime(_provider_descriptor())
        self._transport = self._runtime.create_client_from_profile(
            profile,
            timeout_seconds=timeout_seconds,
        )
        self._target_projection = target_projection
        self._worker: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._active: Future[Any] | None = None
        self._state_lock = threading.Lock()
        self._invoke_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._closing = False
        self._worker_retired = False
        self._transport_closed = False
        self._on_close = on_close
        self._max_requests = max_requests
        self._request_attempts = 0
        self._native_binding: CompiledNativeResponseBinding | None = None
        self._native_plan: EffectiveExecutionPlan | None = None
        self._native_cost: Callable[[Mapping[str, Any]], float] | None = None
        self._native_pending: _PendingNativeHTTPRequest | None = None
        self._native_private_responses: dict[str, Mapping[str, Any]] = {}
        self._native_tool_schemas: tuple[Mapping[str, Any], ...] | None = None
        self._native_stream_prompt: str | None = None

    def bind_compiled_plan(self, plan: EffectiveExecutionPlan) -> Mapping[str, Any]:
        """Join a source-native client to the actual selected compiled plan."""
        target = self._target_projection
        profile = self._profile
        if (
            self._native_binding is not None
            or target is None
            or target.renderer_id not in {
                MINI_RESPONSE_CONSUMER_ID,
                OPENHANDS_RESPONSE_CONSUMER_ID,
                PI_RESPONSE_CONSUMER_ID,
                OMP_RESPONSE_CONSUMER_ID,
            }
            or target.source_manifest is None
            or profile is None
        ):
            raise ValueError("native client lacks a fresh compiled target")
        target.validate_semantics(plan.effective_semantics)
        manifest = target.source_manifest
        binding = admit_native_response_binding(
            manifest.canonical_bytes(),
            expected_compiler_input_digest=manifest.inputs.compiler_input_digest,
            authority_model_id=self._observation.model_id,
            profile=profile,
            capability_observation_digest=self._observation.canonical_digest(),
            episode_id=self._episode_id,
            effective_plan_digest=self._effective_plan_digest,
        )
        if (
            plan.canonical_digest() != self._effective_plan_digest
            or plan.base_compiled.manifest_digest != binding.compiled_manifest_digest
        ):
            raise ValueError("native client source manifest differs from the effective plan")

        is_mini = target.renderer_id == MINI_RESPONSE_CONSUMER_ID
        if is_mini:
            # This is the pinned source's pricing hook, not a zero-cost assertion.
            # Installation is a producer responsibility; missing dependencies fail admission.
            from importlib.metadata import version

            if version("litellm") != "1.101.0":
                raise ValueError("Mini pricing requires the sealed LiteLLM 1.101.0 assembly")
            if os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP", "").lower() != "true":
                raise ValueError("Mini requires the installed local-only pricing catalog")
            import litellm
            from litellm.litellm_core_utils.get_model_cost_map import (
                get_model_cost_map_source_info,
            )

            if get_model_cost_map_source_info() != {
                "source": "local",
                "url": None,
                "is_env_forced": True,
                "fallback_reason": None,
            }:
                raise ValueError("Mini pricing catalog was not loaded from its sealed assembly")

            def native_cost(raw: Mapping[str, Any]) -> float:
                try:
                    cost = litellm.cost_calculator.completion_cost(
                        litellm.ModelResponse(**thaw_json(raw)),
                        model="openai/" + profile.model,
                    )
                    if cost <= 0.0:
                        raise ValueError(f"Cost must be > 0.0, got {cost}")
                except Exception:
                    # Exact source cost_tracking=ignore_errors behavior.
                    cost = 0.0
                return cost

            self._native_cost = native_cost
            runtime_profile = thaw_json(target.runtime_profile)
            if not isinstance(runtime_profile, Mapping):
                raise ValueError("Mini target runtime profile is malformed")
            model_config = runtime_profile.get("model")
            if not isinstance(model_config, Mapping):
                raise ValueError("Mini target model configuration is malformed")
            public_config = {
                **dict(model_config),
                "model_name": "openai/" + profile.model,
            }
        elif target.renderer_id == PI_RESPONSE_CONSUMER_ID:
            public_config = {
                "id": profile.model,
                "name": profile.model,
                "api": "openai-completions",
                "provider": "openai",
                "baseUrl": profile.base_url,
                "reasoning": False,
                "input": ["text"],
                "contextWindow": profile.context_window,
                "maxTokens": profile.max_output_tokens,
                "compat": {
                    "supportsStore": True,
                    "supportsDeveloperRole": True,
                    "supportsUsageInStreaming": True,
                    "maxTokensField": "max_tokens",
                    "supportsStrictMode": False,
                },
            }
        elif target.renderer_id == OMP_RESPONSE_CONSUMER_ID:
            public_config = {
                "id": profile.model,
                "name": profile.model,
                "api": "openai-completions",
                "provider": "openai",
                "baseUrl": profile.base_url,
                "reasoning": False,
                "input": ["text"],
                "contextWindow": profile.context_window,
                "maxTokens": profile.max_output_tokens,
                "compat": {
                    "supportsStore": True,
                    "supportsDeveloperRole": True,
                    "supportsUsageInStreaming": True,
                    "maxTokensField": "max_completion_tokens",
                    "supportsStrictMode": False,
                },
            }
        else:
            self._native_cost = None
            runtime_profile = thaw_json(target.runtime_profile)
            if not isinstance(runtime_profile, Mapping):
                raise ValueError("OpenHands target runtime profile is malformed")
            public_config = {
                "model_name": "openai/" + profile.model,
                "model_canonical_name": None,
                "base_url": profile.base_url,
                "max_input_tokens": self._observation.capabilities.max_context_tokens,
            }

        self._native_binding = binding
        self._native_plan = plan
        return public_config

    def bind_native_tools(self, tools: tuple[Mapping[str, Any], ...]) -> None:
        """Bind the measured worker's once-rendered, workspace-dependent tools."""
        target = self._target_projection
        if (
            target is None or target.renderer_id != OPENHANDS_RESPONSE_CONSUMER_ID
            or self._native_binding is None or self._native_tool_schemas is not None
            or not isinstance(target.runtime_profile, Mapping)
            or type(tools) is not tuple
        ):
            raise RunnerPolicyBindingError(
                "native tools require a fresh compiled OpenHands binding",
                code="native_http_binding_invalid",
                episode_id=self._episode_id, effective_plan_digest=self._effective_plan_digest,
            )
        expected = target.runtime_profile["tool_schemas"]
        if len(tools) != len(expected):
            raise ValueError("native tool count differs from the compiled recipe")
        snapshots = []
        for actual, reference in zip(tools, expected, strict=True):
            snapshot = freeze_json_object(actual, field_name="native tool")
            comparison = thaw_json(snapshot)
            # FileEditorTool.create appends the actual conversation workspace.
            # The measured worker owns that rendering; every other field is fixed.
            if reference["function"]["name"] == "file_editor":
                description = comparison["function"].get("description")
                if type(description) is not str or not description:
                    raise ValueError("native editor description is invalid")
                comparison["function"]["description"] = reference["function"]["description"]
            if canonical_sha256(comparison) != canonical_sha256(reference):
                raise ValueError("native tools differ from the compiled source recipe")
            snapshots.append(snapshot)
        self._native_tool_schemas = tuple(snapshots)

    def bind_native_stream(
        self, system_prompt: str, tools: tuple[Mapping[str, Any], ...],
    ) -> None:
        """Seal the admitted worker's bootstrap before the first stream."""
        target = self._target_projection
        if (
            target is None or target.renderer_id not in {PI_RESPONSE_CONSUMER_ID, OMP_RESPONSE_CONSUMER_ID}
            or self._native_binding is None
            or self._native_stream_prompt is not None
            or self._request_attempts
            or type(system_prompt) is not str or not system_prompt
            or type(tools) is not tuple
            or canonical_sha256(tools) != canonical_sha256(target.chat_tools)
        ):
            raise RunnerPolicyBindingError(
                "native stream bootstrap differs from its compiled source binding",
                code="native_response_binding_invalid",
                episode_id=self._episode_id, effective_plan_digest=self._effective_plan_digest,
            )
        self._native_stream_prompt = system_prompt

    def stage_native_http_request(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Admit exactly one source SDK HTTP request without exposing its headers."""
        target = self._target_projection
        profile = self._profile
        if (
            target is None
            or target.renderer_id != OPENHANDS_RESPONSE_CONSUMER_ID
            or self._native_binding is None
            or profile is None
        ):
            raise RunnerPolicyBindingError(
                "OpenHands native HTTP request has no compiled binding",
                code="native_http_binding_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        candidate: Mapping[str, Any] = request
        if isinstance(request, Mapping) and set(request) == {"http_request"}:
            nested = request["http_request"]
            if not isinstance(nested, Mapping):
                raise RunnerProtocolError(
                    "native HTTP request wrapper is malformed",
                    code="native_http_request_invalid",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            candidate = nested
        if not isinstance(candidate, Mapping):
            raise RunnerProtocolError(
                "native HTTP request must be an object",
                code="native_http_request_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        allowed = {"method", "url", "headers", "body_b64", "declared_capabilities"}
        if set(candidate) - allowed or not {"method", "url", "headers", "body_b64"} <= set(candidate):
            raise RunnerProtocolError(
                "native HTTP request fields are invalid",
                code="native_http_request_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        method = candidate["method"]
        url = candidate["url"]
        headers = candidate["headers"]
        body_b64 = candidate["body_b64"]
        if method != "POST" or type(url) is not str or not url:
            raise RunnerProtocolError(
                "native HTTP request must be a POST",
                code="native_http_request_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        try:
            actual_url = urlsplit(url)
            base_url = urlsplit(profile.base_url)
            expected_path = base_url.path.rstrip("/") + "/chat/completions"
            expected_url = urlunsplit(
                (base_url.scheme, base_url.netloc, expected_path, "", "")
            )
            if (
                actual_url.scheme.lower() != base_url.scheme.lower()
                or actual_url.netloc.lower() != base_url.netloc.lower()
                or actual_url.path != expected_path
                or actual_url.query
                or actual_url.fragment
                or urlunsplit(
                    (
                        actual_url.scheme,
                        actual_url.netloc,
                        actual_url.path,
                        "",
                        "",
                    )
                )
                != expected_url
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise RunnerPolicyBindingError(
                "native HTTP request origin or path is not admitted",
                code="native_http_route_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            ) from None
        if (
            not isinstance(headers, (list, tuple))
            or any(
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not str
                or not pair[0]
                or "\r" in pair[0]
                or "\n" in pair[0]
                or "\r" in pair[1]
                or "\n" in pair[1]
                for pair in headers
            )
            or len(headers) > 256
        ):
            raise RunnerProtocolError(
                "native HTTP request headers are invalid",
                code="native_http_request_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        try:
            encoded = body_b64.encode("ascii") if type(body_b64) is str else b""
            if (
                not encoded
                or len(encoded) > ((_MAX_NATIVE_HTTP_REQUEST_BYTES + 2) // 3) * 4
            ):
                raise ValueError
            body = base64.b64decode(encoded, validate=True)
            if (
                not body
                or len(body) > _MAX_NATIVE_HTTP_REQUEST_BYTES
                or base64.b64encode(body) != encoded
            ):
                raise ValueError
            body_object = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise RunnerProtocolError(
                "native HTTP request body is invalid or oversized",
                code="native_http_request_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            ) from None
        if (
            not isinstance(body_object, dict)
            or body_object.get("model") != profile.model
            or body_object.get("stream", False) is not False
            or set(body_object) - {
                "model", "messages", "tools", "stream", "temperature",
                "max_tokens", "max_completion_tokens", "reasoning_effort",
            }
        ):
            raise RunnerPolicyBindingError(
                "native HTTP request model or streaming mode is not admitted",
                code="native_http_capability_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        messages = body_object.get("messages")
        if (
            type(messages) is not list
            or not messages
            or any(type(message) is not dict for message in messages)
        ):
            raise RunnerProtocolError(
                "native HTTP messages must be a nonempty object array",
                code="native_http_request_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        tools_present = "tools" in body_object
        if (
            self._native_tool_schemas is None
            or canonical_sha256(body_object.get("tools"))
            != canonical_sha256(self._native_tool_schemas)
        ):
            raise RunnerPolicyBindingError(
                "native HTTP tools differ from the compiled source recipe",
                code="native_http_capability_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        token_fields = [name for name in ("max_tokens", "max_completion_tokens") if name in body_object]
        if (
            token_fields != [profile.request_policy.max_token_field]
            or type(body_object[token_fields[0]]) is not int
            or body_object[token_fields[0]] != 2048
            or "temperature" in body_object and (
                type(body_object["temperature"]) not in (int, float)
                or body_object["temperature"] != 0
            )
            or "reasoning_effort" in body_object and body_object["reasoning_effort"] != "none"
        ):
            raise RunnerPolicyBindingError(
                "native HTTP sampling controls differ from the admitted profile",
                code="native_http_capability_mismatch",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        required_features = _validate_request_features(
            profile,
            self._observation,
            tools=tools_present,
            target_projection=target,
            episode_id=self._episode_id,
            effective_plan_digest=self._effective_plan_digest,
        )
        declared = candidate.get("declared_capabilities")
        if declared is not None:
            if (
                type(declared) is not list
                or any(type(item) is not str or not item for item in declared)
                or len(set(declared)) != len(declared)
            ):
                raise RunnerProtocolError(
                    "native HTTP declared capabilities are malformed",
                    code="native_http_request_invalid",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            declared_set = set(declared)
            supported = set(self._observation.capabilities.request_features)
            if not declared_set <= supported or not required_features <= declared_set:
                raise RunnerPolicyBindingError(
                    "native HTTP declared capabilities are not admitted",
                    code="native_http_capability_mismatch",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
        with self._state_lock:
            if self._native_pending is not None:
                raise RunnerProtocolError(
                    "native HTTP request is already staged",
                    code="native_http_request_duplicate",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            if self._closed or self._closing or self._cancelled.is_set():
                raise RunnerDependencyError(
                    "episode provider client is closed",
                    code="provider_client_closed",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            header_pairs = tuple((pair[0], pair[1]) for pair in headers)
            public_request = {
                "model": self._observation.model_id,
                "native_http_request": {
                    "method": method,
                    "url": url,
                    "body_b64": body_b64,
                    "headers_digest": canonical_sha256(headers),
                },
            }
            request_digest = canonical_sha256(public_request)
            self._native_pending = _PendingNativeHTTPRequest(
                method=method,
                url=url,
                headers=header_pairs,
                body=body,
                request_payload=public_request,
                request_digest=request_digest,
            )
        return public_request

    def take_native_http_response(self, response_digest: str) -> Mapping[str, Any]:
        """Consume one private native response receipt bound to its public digest."""
        try:
            require_sha256(response_digest, "native HTTP response digest")
        except (TypeError, ValueError):
            raise RunnerProtocolError(
                "native HTTP response digest is invalid",
                code="native_http_response_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            ) from None
        with self._state_lock:
            response = self._native_private_responses.pop(response_digest, None)
        if response is None:
            raise RunnerProtocolError(
                "native HTTP response is missing or already consumed",
                code="native_http_response_consumed",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        return dict(response)

    async def _invoke_openhands_http(
        self, request: PolicyRuntimeInvokeRequest
    ) -> PolicyRuntimeInvokeResult:
        if type(request) is not PolicyRuntimeInvokeRequest:
            raise TypeError("request must be an exact PolicyRuntimeInvokeRequest")
        if (
            request.episode_id != self._episode_id
            or request.effective_plan_digest != self._effective_plan_digest
        ):
            raise RunnerPolicyBindingError(
                "episode provider invocation does not match its policy binding",
                code="policy_binding_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        async with self._invoke_lock:
            if not await self._retire_worker():
                raise RunnerDependencyError(
                    "previous provider worker has not retired",
                    code="provider_cleanup_failed",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
            with self._state_lock:
                profile = self._profile
                pending = self._native_pending
                if self._closing or self._closed or profile is None:
                    raise RunnerDependencyError(
                        "episode provider client is closed",
                        code="provider_client_closed",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                if self._cancelled.is_set():
                    raise asyncio.CancelledError
                if pending is None or pending.request_digest != request.request_digest:
                    raise RunnerPolicyBindingError(
                        "native HTTP request does not match the policy invocation",
                        code="native_http_request_mismatch",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                if self._native_private_responses:
                    raise RunnerProtocolError(
                        "previous native HTTP response was not consumed",
                        code="native_http_response_unconsumed",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                if self._max_requests is not None and self._request_attempts >= self._max_requests:
                    raise RunnerDependencyError(
                        "episode provider request budget exhausted",
                        code="provider_request_budget_exhausted",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                self._native_pending = None
                active: Future[Any] = Future()

                def run() -> Any:
                    with self._state_lock:
                        self._request_attempts += 1
                    return self._runtime.send_native_http_request(
                        client=self._transport,
                        method=pending.method,
                        url=pending.url,
                        headers=pending.headers,
                        body=pending.body,
                        max_response_bytes=_MAX_NATIVE_HTTP_RESPONSE_BYTES,
                    )

                def worker() -> None:
                    try:
                        outcome = run()
                    except BaseException as exc:
                        active.set_exception(exc)
                    else:
                        active.set_result(outcome)

                thread = threading.Thread(
                    target=worker,
                    name=f"bb-policy-{self._episode_id}",
                    daemon=True,
                )
                self._active = active
                self._worker = thread
                try:
                    thread.start()
                except BaseException:
                    self._active = None
                    self._worker = None
                    raise
            try:
                raw = await asyncio.shield(asyncio.wrap_future(active))
            except asyncio.CancelledError:
                self._cancelled.set()
                raise
            except RunnerDependencyError:
                raise
            except Exception:
                if self._cancelled.is_set():
                    raise asyncio.CancelledError
                raise RunnerDependencyError(
                    "episode provider invocation failed",
                    code="provider_invocation_failed",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                ) from None
            finally:
                if active.done():
                    with self._state_lock:
                        if self._active is active:
                            self._active = None
            if not isinstance(raw, Mapping):
                raise RunnerProtocolError(
                    "native HTTP transport returned an invalid result",
                    code="native_http_response_invalid",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
            if "error" in raw:
                error = raw["error"]
                if (
                    not isinstance(error, Mapping)
                    or type(error.get("type")) is not str
                    or type(error.get("message")) is not str
                ):
                    raise RunnerProtocolError(
                        "native HTTP transport error is malformed",
                        code="native_http_response_invalid",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                private_response: dict[str, Any] = {
                    "error": {
                        "type": error["type"],
                        "message": error["message"],
                    }
                }
                public_response = private_response
            else:
                status_code = raw.get("status_code")
                response_headers = raw.get("headers")
                response_body = raw.get("body")
                if (
                    type(status_code) is not int
                    or not 100 <= status_code <= 599
                    or type(response_headers) is not list
                    or any(
                        type(pair) is not list
                        or len(pair) != 2
                        or type(pair[0]) is not str
                        or type(pair[1]) is not str
                        for pair in response_headers
                    )
                    or type(response_body) is not bytes
                    or len(response_body) > _MAX_NATIVE_HTTP_RESPONSE_BYTES
                ):
                    raise RunnerProtocolError(
                        "native HTTP response is invalid or oversized",
                        code="native_http_response_invalid",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                encoded_body = base64.b64encode(response_body).decode("ascii")
                private_response = {
                    "status_code": status_code,
                    "headers": response_headers,
                    "body_b64": encoded_body,
                }
                public_response = {
                    "status_code": status_code,
                    "body_b64": encoded_body,
                    "headers_digest": canonical_sha256(response_headers),
                }
            response_payload = {"native_http_response": public_response}
            response_digest = canonical_sha256(response_payload)
            with self._state_lock:
                self._native_private_responses[response_digest] = private_response
            return PolicyRuntimeInvokeResult(
                response_payload=response_payload,
                response_digest=response_digest,
            )

    @property
    def profile_identity(self) -> Mapping[str, Any]:
        profile = self._profile
        if profile is None:
            raise RuntimeError("episode provider profile is closed")
        return profile.identity_dict()

    @property
    def target_identity(self) -> Mapping[str, Any] | None:
        projection = self._target_projection
        return None if projection is None else projection.identity_dict()

    def observe(self) -> PolicyCapabilityObservation:
        return self._observation

    @property
    def request_attempts(self) -> int:
        with self._state_lock:
            return self._request_attempts

    async def invoke(
        self, request: PolicyRuntimeInvokeRequest
    ) -> PolicyRuntimeInvokeResult:
        target = self._target_projection
        if target is not None and target.renderer_id == OPENHANDS_RESPONSE_CONSUMER_ID:
            if self._native_binding is None or self._native_plan is None:
                raise RunnerPolicyBindingError(
                    "OpenHands provider has no compiled-plan binding",
                    code="native_http_binding_invalid",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            return await self._invoke_openhands_http(request)
        if target is not None and target.runtime_profile is not None:
            if self._native_binding is None or self._native_plan is None:
                raise RunnerPolicyBindingError(
                    "source-native provider client has no compiled-plan binding",
                    code="native_response_binding_invalid",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            result = await self.invoke_native(
                request, binding=self._native_binding, effective_plan=self._native_plan
            )
            if target.renderer_id in {PI_RESPONSE_CONSUMER_ID, OMP_RESPONSE_CONSUMER_ID}:
                payload = {"native_response": result.as_dict()}
                return PolicyRuntimeInvokeResult(
                    response_payload=payload, response_digest=canonical_sha256(payload)
                )
            if self._native_cost is None:
                raise RunnerPolicyBindingError(
                    "Mini provider client has no pricing hook",
                    code="native_response_binding_invalid",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            if result.raw_response is None:
                raise RunnerProtocolError("Mini requires the original provider sample", code="native_response_invalid")
            response = _mini_model_response(result.raw_response)
            mini_response = {
                "message": response.choices[0].message.model_dump(),
                "response": response.model_dump(),
                "response_json": response.model_dump(mode="json"),
            }
            payload = {
                "native_response": result.as_dict(),
                "mini_response": mini_response,
                "cost": self._native_cost(response),
            }
            return PolicyRuntimeInvokeResult(
                response_payload=payload, response_digest=canonical_sha256(payload)
            )
        return await self._invoke(request, native_binding=None)

    async def invoke_native(
        self,
        request: PolicyRuntimeInvokeRequest,
        *,
        binding: CompiledNativeResponseBinding,
        effective_plan: EffectiveExecutionPlan,
    ) -> NativeProviderResponse:
        if (
            type(binding) is not CompiledNativeResponseBinding
            or type(effective_plan) is not EffectiveExecutionPlan
        ):
            raise TypeError("native recording requires an exact binding and execution plan")
        providers = effective_plan.effective_semantics.get("providers")
        models = providers.get("models") if isinstance(providers, Mapping) else None
        selected = None
        if isinstance(models, tuple):
            selected = next(
                (
                    model for model in models
                    if isinstance(model, Mapping)
                    and model.get("model_id") == binding.authority_model_id
                ),
                None,
            )
        if (
            effective_plan.canonical_digest() != self._effective_plan_digest
            or effective_plan.base_compiled.manifest_digest != binding.compiled_manifest_digest
            or effective_plan.policy_capability_observation_digest
            != self._observation.canonical_digest()
            or selected is None
            or canonical_sha256(thaw_json(selected)) != binding.compiled_model_digest
        ):
            raise RunnerPolicyBindingError(
                "native response policy is not bound to the owned execution plan",
                code="native_response_binding_invalid",
                episode_id=self._episode_id,
                effective_plan_digest=self._effective_plan_digest,
            )
        return await self._invoke(request, native_binding=binding)

    @overload
    async def _invoke(
        self, request: PolicyRuntimeInvokeRequest, *, native_binding: None
    ) -> PolicyRuntimeInvokeResult: ...

    @overload
    async def _invoke(
        self,
        request: PolicyRuntimeInvokeRequest,
        *,
        native_binding: CompiledNativeResponseBinding,
    ) -> NativeProviderResponse: ...

    async def _invoke(
        self,
        request: PolicyRuntimeInvokeRequest,
        *,
        native_binding: CompiledNativeResponseBinding | None,
    ) -> PolicyRuntimeInvokeResult | NativeProviderResponse:
        if type(request) is not PolicyRuntimeInvokeRequest:
            raise TypeError("request must be an exact PolicyRuntimeInvokeRequest")
        if (
            request.episode_id != self._episode_id
            or request.effective_plan_digest != self._effective_plan_digest
        ):
            raise RunnerPolicyBindingError(
                "episode provider invocation does not match its policy binding",
                code="policy_binding_mismatch",
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
        async with self._invoke_lock:
            if not await self._retire_worker():
                raise RunnerDependencyError(
                    "previous provider worker has not retired",
                    code="provider_cleanup_failed",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
            with self._state_lock:
                profile = self._profile
                if self._closing or self._closed or profile is None:
                    raise RunnerDependencyError(
                        "episode provider client is closed",
                        code="provider_client_closed",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                if self._cancelled.is_set():
                    raise asyncio.CancelledError
            try:
                messages, tools = _responses_request_to_chat(
                    thaw_json(request.request_payload),
                    expected_model_id=self._observation.model_id,
                    target_projection=self._target_projection,
                    native_system_prompt=self._native_stream_prompt,
                )
            except (ProviderContractError, TypeError, ValueError) as exc:
                error = RunnerProtocolError(
                    "policy request cannot be projected to Chat Completions",
                    code="policy_request_invalid",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
                error.__cause__ = exc
                raise error
            _validate_request_features(
                profile,
                self._observation,
                tools=bool(tools),
                target_projection=self._target_projection,
                episode_id=request.episode_id,
                effective_plan_digest=request.effective_plan_digest,
            )
            if native_binding is not None:
                if (
                    type(native_binding) is not CompiledNativeResponseBinding
                    or (
                        self._target_projection is not None
                        and (
                            native_binding.policy.consumer_id
                            != self._target_projection.renderer_id
                            or not is_native_response_consumer_registered(
                                self._target_projection.renderer_id
                            )
                            or native_binding != self._native_binding
                        )
                    )
                    or native_binding.authority_model_id != self._observation.model_id
                ):
                    raise RunnerPolicyBindingError(
                        "native recording requires its compiled standalone binding",
                        code="native_response_binding_invalid",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                native_binding.validate_invocation(
                    profile,
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                    capability_observation_digest=self._observation.canonical_digest(),
                )

            stream = profile.request_policy.mode == "streaming"
            context = ProviderRuntimeContext(
                None,
                {},
                stream=stream,
                extra=(
                    {"response_consumer_id": native_binding.policy.consumer_id}
                    if native_binding is not None
                    and is_native_response_consumer_registered(
                        native_binding.policy.consumer_id
                    )
                    else {}
                ),
                session_id=request.episode_id,
                input_id=request.request_digest,
                turn_id=str(request.turn),
                cancel_requested=self._cancelled.is_set,
                provider_profile=profile,
                effective_plan_digest=request.effective_plan_digest,
                capability_observation_digest=self._observation.canonical_digest(),
            )

            def run() -> Any:
                context.raise_if_cancelled()
                with self._state_lock:
                    self._request_attempts += 1
                if native_binding is not None:
                    return self._runtime.invoke_native(
                        client=self._transport,
                        model=profile.model,
                        messages=messages,
                        tools=tools,
                        stream=stream,
                        context=context,
                        binding=native_binding,
                    )
                return self._runtime.invoke(
                    client=self._transport,
                    model=profile.model,
                    messages=messages,
                    tools=tools,
                    stream=stream,
                    context=context,
                )

            with self._state_lock:
                if self._closed or self._cancelled.is_set():
                    raise asyncio.CancelledError
                if self._max_requests is not None and self._request_attempts >= self._max_requests:
                    raise RunnerDependencyError(
                        "episode provider request budget exhausted",
                        code="provider_request_budget_exhausted",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                active: Future[Any] = Future()

                def worker() -> None:
                    try:
                        outcome = run()
                    except BaseException as exc:
                        if (
                            native_binding is not None
                            and native_binding.policy.consumer_id == MINI_RESPONSE_CONSUMER_ID
                            and isinstance(exc, Exception)
                            and _is_mini_provider_exception(exc)
                        ):
                            exc = _mini_provider_exception(exc, model=profile.model)
                        active.set_exception(exc)
                    else:
                        active.set_result(outcome)

                thread = threading.Thread(
                    target=worker,
                    name=f"bb-policy-{self._episode_id}",
                    daemon=True,
                )
                self._active = active
                self._worker = thread
                try:
                    thread.start()
                except BaseException:
                    self._active = None
                    self._worker = None
                    raise
            try:
                result = await asyncio.shield(asyncio.wrap_future(active))
            except asyncio.CancelledError:
                self._cancelled.set()
                raise
            except RunnerDependencyError:
                raise
            except Exception as exc:
                if self._cancelled.is_set():
                    raise asyncio.CancelledError
                error = RunnerDependencyError(
                    "episode provider invocation failed",
                    code="provider_invocation_failed",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
                if isinstance(exc, MiniProviderFailure):
                    raise error from exc
                raise error from None
            finally:
                if active.done():
                    with self._state_lock:
                        if self._active is active:
                            self._active = None
            if native_binding is not None:
                if not isinstance(result, NativeProviderResponse):
                    raise RunnerProtocolError(
                        "native provider returned an invalid response",
                        code="native_response_invalid",
                        episode_id=request.episode_id,
                        effective_plan_digest=request.effective_plan_digest,
                    )
                return result
            try:
                payload = _provider_result_to_responses(result)
            except (ProviderContractError, TypeError, ValueError) as exc:
                error = RunnerProtocolError(
                    "episode provider returned an invalid Chat Completions result",
                    code="policy_response_invalid",
                    episode_id=request.episode_id,
                    effective_plan_digest=request.effective_plan_digest,
                )
                error.__cause__ = exc
                raise error
            return PolicyRuntimeInvokeResult(
                response_payload=payload,
                response_digest=canonical_sha256(payload),
            )

    async def cancel(self, reason: str) -> None:
        del reason
        self._cancelled.set()
        if not self._transport_closed:
            close = getattr(self._transport, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    return
            self._transport_closed = True

    async def _retire_worker(self) -> bool:
        with self._state_lock:
            worker = self._worker
        if worker is None:
            return True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while worker.is_alive():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.01, remaining))
        worker.join(timeout=0)
        with self._state_lock:
            if self._worker is worker:
                self._worker = None
                self._active = None
        return True

    async def close(self) -> None:
        async with self._close_lock:
            with self._state_lock:
                self._closing = True
            self._cancelled.set()
            transport_failure = False
            if not self._transport_closed:
                close = getattr(self._transport, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        transport_failure = True
                    else:
                        self._transport_closed = True
                else:
                    self._transport_closed = True
            if not self._worker_retired:
                if await self._retire_worker():
                    with self._state_lock:
                        self._worker_retired = True
            if transport_failure or not self._worker_retired:
                raise RunnerDependencyError(
                    "episode provider cleanup failed",
                    code="provider_cleanup_failed",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
            with self._state_lock:
                self._profile = None
                self._target_projection = None
                self._native_pending = None
                self._native_private_responses.clear()
                self._closed = True
            if self._on_close is not None:
                await self._on_close(self)
                self._on_close = None


class EpisodeOpenAICompletionsPolicyResolver:
    """Preserves production policy admission while replacing only transport."""

    def __init__(
        self,
        authority_resolver: PolicyRuntimeClientResolver,
        profiles: Mapping[str, OpenAICompletionsProviderProfile],
        credential_handle_ids: Mapping[str, str],
        authority_model_ids: Mapping[str, str],
        authority_wire_models: Mapping[str, str],
        expected_observation_digests: Mapping[str, str],
        target_projections: Mapping[str, E4TargetPolicyProjection] | None = None,
        timeout_seconds: Mapping[str, float] | None = None,
        request_limits: Mapping[str, int] | None = None,
    ) -> None:
        if not profiles:
            raise ValueError("at least one episode provider profile is required")
        copied: dict[str, OpenAICompletionsProviderProfile] = {}
        for episode_id, profile in profiles.items():
            if type(episode_id) is not str or not episode_id:
                raise TypeError("provider profile episode ids must be non-empty text")
            if type(profile) is not OpenAICompletionsProviderProfile:
                raise TypeError(
                    "provider profiles must be exact OpenAICompletionsProviderProfile values"
                )
            copied[episode_id] = profile
        if set(authority_model_ids) != set(copied) or any(
            type(model_id) is not str or not model_id
            for model_id in authority_model_ids.values()
        ):
            raise ValueError(
                "authority model identities must exactly match provider profiles"
            )
        copied_model_ids = dict(authority_model_ids)
        if set(authority_wire_models) != set(copied) or any(
            type(model) is not str or not model
            for model in authority_wire_models.values()
        ):
            raise ValueError(
                "authority wire models must exactly match provider profiles"
            )
        copied_wire_models = dict(authority_wire_models)
        if set(credential_handle_ids) != set(copied) or any(
            type(handle_id) is not str or not handle_id
            for handle_id in credential_handle_ids.values()
        ):
            raise ValueError(
                "credential handles must exactly match episode provider profiles"
            )
        copied_credential_handles = dict(credential_handle_ids)
        if set(expected_observation_digests) != set(copied) or any(
            type(digest) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            for digest in expected_observation_digests.values()
        ):
            raise ValueError(
                "policy observation digests must exactly match provider profiles"
            )
        copied_observation_digests = dict(expected_observation_digests)
        copied_projections: dict[str, E4TargetPolicyProjection] = {}
        for episode_id, projection in (target_projections or {}).items():
            if episode_id not in copied:
                raise ValueError(
                    "target projection has no matching episode provider profile"
                )
            if type(projection) is not E4TargetPolicyProjection:
                raise TypeError(
                    "target projections must be exact E4TargetPolicyProjection values"
                )
            copied_projections[episode_id] = projection
        copied_timeouts: dict[str, float] = {}
        for episode_id, value in (timeout_seconds or {}).items():
            if episode_id not in copied:
                raise ValueError(
                    "provider timeout has no matching episode provider profile"
                )
            if type(value) not in (int, float) or not 0 < value <= 3_600:
                raise ValueError("provider timeout must be within (0, 3600]")
            copied_timeouts[episode_id] = float(value)
        copied_request_limits: dict[str, int] = {}
        if request_limits is not None:
            if set(request_limits) != set(copied) or any(
                type(value) is not int or not 0 < value <= 2**53 - 1
                for value in request_limits.values()
            ):
                raise ValueError("request limits must positively bound each provider profile")
            copied_request_limits = dict(request_limits)
        self._authority_resolver = authority_resolver
        self._profiles = copied
        self._target_projections = copied_projections
        self._credential_handle_ids = copied_credential_handles
        self._timeout_seconds = copied_timeouts
        self._request_limits = copied_request_limits
        self._authority_model_ids = copied_model_ids
        self._authority_wire_models = copied_wire_models
        self._expected_observation_digests = copied_observation_digests
        self._clients: set[EpisodeOpenAICompletionsPolicyClient] = set()
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closing = False
        self._closed = False

    def abort_bootstrap(self) -> None:
        if self._clients or self._closing:
            raise RuntimeError("cannot abort provider resolver after runtime admission")
        self._profiles.clear()
        self._timeout_seconds.clear()
        self._request_limits.clear()
        self._target_projections.clear()
        self._credential_handle_ids.clear()
        self._expected_observation_digests.clear()
        self._authority_wire_models.clear()
        abort = getattr(self._authority_resolver, "abort_bootstrap", None)
        self._authority_model_ids.clear()
        if not callable(abort):
            raise TypeError("authority resolver has no bootstrap cleanup")
        abort()

    async def resolve(
        self,
        policy_binding: PolicyBindingRef,
        *,
        episode_id: str,
        effective_plan_digest: str,
    ) -> PolicyRuntimeClientPort:
        async with self._lock:
            if self._closing:
                raise RunnerDependencyError(
                    "episode provider resolver is closed",
                    code="provider_resolver_closed",
                    episode_id=episode_id,
                    effective_plan_digest=effective_plan_digest,
                )
            profile = self._profiles.get(episode_id)
            target_projection = self._target_projections.get(episode_id)
            timeout_seconds = self._timeout_seconds.get(episode_id, 600.0)
            credential_handle_id = self._credential_handle_ids.get(episode_id)
            authority_model_id = self._authority_model_ids.get(episode_id)
            authority_wire_model = self._authority_wire_models.get(episode_id)
            expected_observation_digest = self._expected_observation_digests.get(
                episode_id
            )
            if profile is None:
                raise RunnerPolicyBindingError(
                    "episode has no provider profile",
                    code="provider_profile_missing",
                    episode_id=episode_id,
                    effective_plan_digest=effective_plan_digest,
                )
            admitted = await self._authority_resolver.resolve(
                policy_binding,
                episode_id=episode_id,
                effective_plan_digest=effective_plan_digest,
            )
            try:
                observation = admitted.observe()
            finally:
                await admitted.close()
            if (
                credential_handle_id is None
                or authority_model_id is None
                or authority_wire_model is None
                or expected_observation_digest is None
            ):
                raise RunnerPolicyBindingError(
                    "episode has no provider route authority",
                    code="provider_profile_missing",
                    episode_id=episode_id,
                    effective_plan_digest=effective_plan_digest,
                )
            if observation.canonical_digest() != expected_observation_digest:
                raise RunnerPolicyBindingError(
                    "admitted provider route does not match launcher authority",
                    code="provider_route_authority_mismatch",
                    episode_id=episode_id,
                    effective_plan_digest=effective_plan_digest,
                )
            _validate_owned_profile_observation(
                profile=profile,
                target_projection=target_projection,
                observation=observation,
                authority_model_id=authority_model_id,
                authority_wire_model=authority_wire_model,
                credential_handle_id=credential_handle_id,
                episode_id=episode_id,
                effective_plan_digest=effective_plan_digest,
            )
            client = EpisodeOpenAICompletionsPolicyClient(
                episode_id=episode_id,
                effective_plan_digest=effective_plan_digest,
                observation=observation,
                profile=profile,
                timeout_seconds=timeout_seconds,
                target_projection=target_projection,
                on_close=self._deregister,
                max_requests=self._request_limits.get(episode_id),
            )
            self._profiles.pop(episode_id)
            self._target_projections.pop(episode_id, None)
            self._timeout_seconds.pop(episode_id, None)
            self._request_limits.pop(episode_id, None)
            self._credential_handle_ids.pop(episode_id)
            self._authority_model_ids.pop(episode_id)
            self._authority_wire_models.pop(episode_id)
            self._expected_observation_digests.pop(episode_id)
            self._clients.add(client)
            return client

    async def _deregister(self, client: EpisodeOpenAICompletionsPolicyClient) -> None:
        async with self._lock:
            self._clients.discard(client)

    async def close(self) -> None:
        async with self._close_lock:
            async with self._lock:
                if self._closed:
                    return
                self._closing = True
                clients = tuple(self._clients)
                self._profiles.clear()
                self._target_projections.clear()
                self._timeout_seconds.clear()
                self._request_limits.clear()
                self._credential_handle_ids.clear()
                self._authority_model_ids.clear()
                self._authority_wire_models.clear()
                self._expected_observation_digests.clear()
            failures: list[BaseException] = []
            results = await asyncio.gather(
                *(client.close() for client in clients),
                return_exceptions=True,
            )
            failures.extend(
                result for result in results if isinstance(result, BaseException)
            )
            close_authority = getattr(self._authority_resolver, "close", None)
            if not callable(close_authority):
                failures.append(TypeError("authority resolver has no runtime cleanup"))
            else:
                try:
                    await close_authority()
                except BaseException as exc:
                    failures.append(exc)
            if failures:
                raise BaseExceptionGroup(
                    "episode provider resolver cleanup failed", failures
                )
            async with self._lock:
                self._closed = True


def _responses_request_to_chat(
    request: Mapping[str, Any],
    *,
    expected_model_id: str,
    target_projection: E4TargetPolicyProjection | None = None,
    native_system_prompt: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    if type(request) is not dict:
        raise TypeError("policy request must be an exact object")
    if request.get("model") != expected_model_id:
        raise ProviderContractError(
            "policy request model does not match the admitted policy observation"
        )
    if target_projection is not None and target_projection.runtime_profile is not None:
        if set(request) != {"model", "messages", "tools"}:
            raise ProviderContractError("source-native request fields differ from its source protocol")
        messages = request["messages"]
        tools = request["tools"]
        system_prompt = target_projection.system_prompt
        target_binding = (
            target_projection.source_manifest.semantic.metadata.get("e4_target")
            if target_projection.source_manifest is not None
            else None
        )
        profile_version = (
            target_binding.get("version")
            if isinstance(target_binding, Mapping)
            else None
        )
        deferred_native_prompt = (
            profile_version == 3
            and target_projection.rendered_prompt_digest is None
        )
        if deferred_native_prompt:
            if native_system_prompt is None:
                raise ProviderContractError("native stream bootstrap has not been bound")
            system_prompt = native_system_prompt
        elif native_system_prompt is not None:
            raise ProviderContractError("native stream bootstrap is not admitted for this target")
        if (
            type(messages) is not list
            or len(messages) < 2
            or any(type(message) is not dict or "extra" in message for message in messages)
            or messages[0] != {"role": "system", "content": system_prompt}
            or messages[1].get("role") != "user"
            or any(message.get("role") not in {"system", "user", "assistant", "tool"} for message in messages)
            or tools != [thaw_json(tool) for tool in target_projection.chat_tools]
        ):
            raise ProviderContractError("source-native request does not match its compiled source surface")
        if target_projection.renderer_id != MINI_RESPONSE_CONSUMER_ID:
            return messages, tools
        # No assistant splitting, argument decoding, null coercion or reordering: Mini's
        # LitellmModel sends through litellm.completion, whose message validation drops
        # only top-level null fields and keeps every other key verbatim.
        from litellm.utils import validate_and_fix_openai_messages

        return validate_and_fix_openai_messages(messages=messages), tools
    instructions = request.get("instructions")
    if type(instructions) is not str:
        raise ProviderContractError("policy request instructions must be text")
    if (
        target_projection is not None
        and instructions != target_projection.system_prompt
    ):
        raise ProviderContractError(
            "policy instructions do not match the selected E4 target"
        )
    raw_input = request.get("input")
    if type(raw_input) is not list:
        raise ProviderContractError("policy request input must be an exact array")
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for item in raw_input:
        if type(item) is not dict:
            raise ProviderContractError("policy input items must be exact objects")
        item_type = item.get("type")
        if item_type == "function_call":
            name = item.get("name")
            call_id = item.get("call_id")
            arguments = item.get("arguments")
            if (
                not all(type(value) is str and value for value in (name, call_id))
                or type(arguments) is not str
            ):
                raise ProviderContractError("policy function call is malformed")
            json.loads(arguments)
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            )
            continue
        if item_type == "function_call_output":
            call_id = item.get("call_id")
            output = item.get("output")
            if type(call_id) is not str or not call_id or type(output) is not str:
                raise ProviderContractError("policy function call output is malformed")
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": output}
            )
            continue
        role = item.get("role")
        if role not in {"system", "developer", "user", "assistant"}:
            raise ProviderContractError("policy message role is unsupported")
        content = item.get("content")
        if target_projection is not None and role in {"system", "developer"}:
            if content != "":
                raise ProviderContractError(
                    "selected E4 target forbids per-turn system instructions"
                )
            continue
        if target_projection is not None and role == "user" and type(content) is dict:
            if set(content) != {"prompt"} or type(content["prompt"]) is not str:
                raise ProviderContractError(
                    "selected E4 target requires an exact prompt task input"
                )
            projected_content = content["prompt"]
        elif isinstance(content, str):
            projected_content = content
        elif role == "assistant" and type(content) is list:
            parts: list[str] = []
            for block in content:
                if (
                    type(block) is not dict
                    or set(block) != {"type", "text"}
                    or block["type"] != "output_text"
                    or type(block["text"]) is not str
                ):
                    raise ProviderContractError(
                        "policy assistant message content is malformed"
                    )
                parts.append(block["text"])
            projected_content = "".join(parts)
        elif content is None or isinstance(content, (bool, int, float, list, dict)):
            projected_content = json.dumps(
                content,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        else:
            raise ProviderContractError("policy message content is unsupported")
        messages.append({"role": role, "content": projected_content})

    raw_tools = request.get("tools")
    if type(raw_tools) is not list:
        raise ProviderContractError("policy request tools must be an exact array")
    tools: list[dict[str, Any]] = []
    tool_names: list[str] = []
    for tool in raw_tools:
        if type(tool) is not dict or tool.get("type") != "function":
            raise ProviderContractError("policy tools must be exact function objects")
        if set(tool) - {"type", "name", "description", "parameters", "strict"}:
            raise ProviderContractError("policy tool contains unsupported fields")
        name = tool.get("name")
        parameters = tool.get("parameters")
        if type(name) is not str or not name or type(parameters) is not dict:
            raise ProviderContractError("policy function tool is malformed")
        tool_names.append(name)
        function: dict[str, Any] = {"name": name, "parameters": parameters}
        description = tool.get("description")
        if description is not None:
            if type(description) is not str:
                raise ProviderContractError("policy tool description must be text")
            function["description"] = description
        if "strict" in tool:
            if type(tool["strict"]) is not bool:
                raise ProviderContractError("policy tool strict value must be boolean")
            function["strict"] = tool["strict"]
        tools.append({"type": "function", "function": function})
    if target_projection is not None:
        if tuple(tool_names) != target_projection.ordered_tool_names:
            raise ProviderContractError(
                "policy tools do not match the selected E4 target"
            )
        tools = [thaw_json(tool) for tool in target_projection.chat_tools]
    return messages, tools or None


def _provider_result_to_responses(result: Any) -> dict[str, Any]:
    messages = getattr(result, "messages", None)
    if type(messages) is not list or len(messages) != 1:
        raise ProviderContractError(
            "provider must return exactly one assistant message"
        )
    message = messages[0]
    if type(message) is not ProviderMessage or message.role != "assistant":
        raise ProviderContractError(
            "provider result message must be an exact assistant message"
        )
    output: list[dict[str, Any]] = []
    content = message.content
    if isinstance(content, str):
        text = content
    elif type(content) is list:
        parts: list[str] = []
        for block in content:
            if (
                type(block) is not dict
                or block.get("type") != "text"
                or type(block.get("text")) is not str
            ):
                raise ProviderContractError("provider assistant content is unsupported")
            parts.append(block["text"])
        text = "".join(parts)
    elif content is None and message.tool_calls:
        text = ""
    else:
        raise ProviderContractError("provider assistant content is unsupported")
    if text or not message.tool_calls:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        )
    seen_call_ids: set[str] = set()
    for call in message.tool_calls:
        call_data = call.as_dict()
        call_id = call_data["call_id"]
        if call_id in seen_call_ids:
            raise ProviderContractError(
                "provider returned duplicate tool call identifiers"
            )
        seen_call_ids.add(call_id)
        output.append(
            {
                "type": "function_call",
                "name": call_data["name"],
                "call_id": call_id,
                "arguments": call_data["arguments_json"],
            }
        )
    return {"output": output}


__all__ = [
    "E4TargetPolicyProjection",
    "EpisodeOpenAICompletionsPolicyClient",
    "EpisodeOpenAICompletionsPolicyResolver",
]
