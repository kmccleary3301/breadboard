from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from concurrent.futures import Future
import json
import re
import threading
from typing import Any

from agentic_coder_prototype.compilation.contracts import canonical_sha256
import yaml

from breadboard_engine.e4_targets import load_e4_target

from breadboard_engine.provider.contracts import (
    OpenAICompletionsProviderProfile,
    ProviderContractError,
    ProviderMessage,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime

from .contracts import PolicyBindingRef, PolicyCapabilityObservation
from .runners.base import (
    FrozenJsonObject,
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


def _validate_owned_profile_observation(
    *,
    profile: OpenAICompletionsProviderProfile,
    observation: PolicyCapabilityObservation,
    authority_model_id: str,
    credential_handle_id: str,
    episode_id: str,
    effective_plan_digest: str,
) -> None:
    capabilities = observation.capabilities
    if (
        profile.provider_id != observation.provider_id
        or profile.runtime_id != _provider_descriptor().runtime_id
        or authority_model_id != observation.model_id
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


@dataclass(frozen=True, slots=True)
class E4TargetPolicyProjection:
    target_id: str
    overlay_id: str
    descriptor_digest: str
    execution_config_digest: str
    overlay_digest: str
    rendered_prompt_digest: str
    system_prompt: str
    ordered_tool_names: tuple[str, ...]
    chat_tools: tuple[FrozenJsonObject, ...]

    @classmethod
    def load(
        cls,
        target_id: str,
        dynamic_fields: Mapping[str, str],
    ) -> E4TargetPolicyProjection:
        package = load_e4_target(target_id)
        descriptor = dict(package.descriptor)
        execution = descriptor.get("execution")
        overlay = descriptor.get("overlay")
        if not isinstance(execution, Mapping) or not isinstance(overlay, Mapping):
            raise ValueError("E4 target execution and overlay descriptors are required")
        config_asset = execution.get("config_asset")
        prompt_asset = execution.get("system_prompt_asset")
        tool_asset = execution.get("tool_surface_asset")
        if not all(
            type(value) is str and value
            for value in (config_asset, prompt_asset, tool_asset)
        ):
            raise ValueError("E4 target execution assets are invalid")
        harness = yaml.safe_load(package.read_asset_text(config_asset))
        if type(harness) is not dict or harness.get("target_id") != target_id:
            raise ValueError("E4 target harness identity is invalid")
        prompt_config = harness.get("prompt")
        tools_config = harness.get("tools")
        if type(prompt_config) is not dict or type(tools_config) is not dict:
            raise ValueError("E4 target prompt and tool configuration is invalid")
        required_fields = prompt_config.get("dynamic_fields")
        if (
            type(required_fields) is not list
            or not required_fields
            or any(type(value) is not str or not value for value in required_fields)
            or len(set(required_fields)) != len(required_fields)
            or set(dynamic_fields) != set(required_fields)
        ):
            raise ValueError(
                "E4 target dynamic fields do not match the target contract"
            )
        prompt_template = package.read_asset_text(prompt_asset)
        values: dict[str, str] = {}
        for field_name in required_fields:
            value = dynamic_fields[field_name]
            if (
                type(value) is not str
                or not value
                or len(value.encode("utf-8")) > 16_384
            ):
                raise ValueError(f"E4 target dynamic field {field_name!r} is invalid")
            values[field_name] = value
        placeholder = re.compile(
            r"\{\{(" + "|".join(re.escape(name) for name in required_fields) + r")\}\}"
        )
        rendered_prompt = placeholder.sub(
            lambda match: values[match.group(1)],
            prompt_template,
        )
        if (
            re.search(r"\{\{[^{}]+\}\}", rendered_prompt)
            or len(rendered_prompt.encode("utf-8")) > 512 * 1024
        ):
            raise ValueError("E4 target prompt rendering is invalid or too large")
        surface = json.loads(package.read_asset_text(tool_asset))
        ordered_names = tools_config.get("ordered")
        if (
            type(surface) is not dict
            or surface.get("target_id") != target_id
            or type(ordered_names) is not list
            or surface.get("ordered_tools") != ordered_names
            or any(type(name) is not str or not name for name in ordered_names)
        ):
            raise ValueError("E4 target tool ordering is invalid")
        surface_tools = surface.get("tools")
        if type(surface_tools) is not dict or set(surface_tools) != set(ordered_names):
            raise ValueError("E4 target tool surface is incomplete")
        chat_tools: list[FrozenJsonObject] = []
        for name in ordered_names:
            tool = surface_tools[name]
            if (
                type(tool) is not dict
                or type(tool.get("description")) is not str
                or type(tool.get("parameters")) is not dict
            ):
                raise ValueError(f"E4 target tool {name!r} is invalid")
            chat_tools.append(
                freeze_json_object(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool["description"],
                            "parameters": tool["parameters"],
                        },
                    },
                    field_name=f"E4 target tool {name}",
                )
            )
        overlay_id = overlay.get("overlay_id")
        if type(overlay_id) is not str or not overlay_id:
            raise ValueError("E4 target overlay identity is invalid")
        return cls(
            target_id=target_id,
            overlay_id=overlay_id,
            descriptor_digest=canonical_sha256(descriptor),
            execution_config_digest=canonical_sha256(harness),
            overlay_digest=canonical_sha256(overlay),
            rendered_prompt_digest=canonical_sha256({"text": rendered_prompt}),
            system_prompt=rendered_prompt,
            ordered_tool_names=tuple(ordered_names),
            chat_tools=tuple(chat_tools),
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
        }


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
        if (
            target_projection is not None
            and type(target_projection) is not E4TargetPolicyProjection
        ):
            raise TypeError(
                "target_projection must be an exact E4TargetPolicyProjection"
            )
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
        self._worker_retired = False
        self._transport_closed = False
        self._on_close = on_close

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

    async def invoke(
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
            with self._state_lock:
                profile = self._profile
                if self._closed or profile is None:
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

            context = ProviderRuntimeContext(
                None,
                {},
                stream=True,
                session_id=request.episode_id,
                input_id=request.request_digest,
                turn_id=str(request.turn),
                cancel_requested=self._cancelled.is_set,
                provider_profile=profile,
            )

            def run() -> Any:
                return self._runtime.invoke(
                    client=self._transport,
                    model=profile.model,
                    messages=messages,
                    tools=tools,
                    stream=True,
                    context=context,
                )

            with self._state_lock:
                if self._closed or self._cancelled.is_set():
                    raise asyncio.CancelledError
                active: Future[Any] = Future()

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
                result = await asyncio.shield(asyncio.wrap_future(active))
            except asyncio.CancelledError:
                self._cancelled.set()
                raise
            except Exception:
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

    async def close(self) -> None:
        async with self._close_lock:
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
                with self._state_lock:
                    active = self._active
                if active is not None:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(asyncio.wrap_future(active)),
                            timeout=5.0,
                        )
                    except (Exception, asyncio.CancelledError):
                        pass
                    if active.done():
                        with self._state_lock:
                            if self._active is active:
                                self._active = None
                with self._state_lock:
                    if self._active is None:
                        self._profile = None
                        self._target_projection = None
                        self._worker = None
                        self._worker_retired = True
            if transport_failure or not self._worker_retired:
                raise RunnerDependencyError(
                    "episode provider cleanup failed",
                    code="provider_cleanup_failed",
                    episode_id=self._episode_id,
                    effective_plan_digest=self._effective_plan_digest,
                )
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
        target_projections: Mapping[str, E4TargetPolicyProjection] | None = None,
        timeout_seconds: Mapping[str, float] | None = None,
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
        if set(credential_handle_ids) != set(copied) or any(
            type(handle_id) is not str or not handle_id
            for handle_id in credential_handle_ids.values()
        ):
            raise ValueError(
                "credential handles must exactly match episode provider profiles"
            )
        copied_credential_handles = dict(credential_handle_ids)
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
        self._authority_resolver = authority_resolver
        self._profiles = copied
        self._target_projections = copied_projections
        self._credential_handle_ids = copied_credential_handles
        self._timeout_seconds = copied_timeouts
        self._authority_model_ids = copied_model_ids
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
        self._target_projections.clear()
        self._credential_handle_ids.clear()
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
            if credential_handle_id is None or authority_model_id is None:
                raise RunnerPolicyBindingError(
                    "episode has no provider credential or model authority",
                    code="provider_profile_missing",
                    episode_id=episode_id,
                    effective_plan_digest=effective_plan_digest,
                )
            _validate_owned_profile_observation(
                profile=profile,
                observation=observation,
                authority_model_id=authority_model_id,
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
            )
            self._profiles.pop(episode_id)
            self._target_projections.pop(episode_id, None)
            self._timeout_seconds.pop(episode_id, None)
            self._credential_handle_ids.pop(episode_id)
            self._authority_model_ids.pop(episode_id)
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
                self._credential_handle_ids.clear()
                self._authority_model_ids.clear()
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    if type(request) is not dict:
        raise TypeError("policy request must be an exact object")
    if request.get("model") != expected_model_id:
        raise ProviderContractError(
            "policy request model does not match the admitted policy observation"
        )
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
