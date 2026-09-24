from __future__ import annotations
import asyncio

from builtins import BaseExceptionGroup
from dataclasses import asdict
import ipaddress
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
import os
import re
from pathlib import Path
import stat
from typing import Any, Literal, Mapping
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from breadboard.product.harness.targets import bind_e4_target_inputs, serialize_e4_target_inputs
from breadboard_engine.e4_targets import E4TargetPackage, load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from breadboard_engine.provider.profiles import OpenAICompletionsRequestPolicy, validate_wire_model
from . import contracts as c
from .composition import (
    ManagedPolicyRuntimeClientResolver,
    PinnedServerCompilerAdapter,
    ProductionComposition,
    load_pinned_compiler,
    load_production_composition,
)
from .policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyResolver,
)
from .runner_identity import measure_module_artifact
from .runners.base import freeze_json_object, thaw_json
from .service import EpisodePrimaryDisposition, V2RunResult


_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_GIT_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_HEADLESS_MODULE_IDENTITY = measure_module_artifact(__file__)
_POLICY_PROVIDER_PATH = Path(__file__).with_name("policy_provider.py")
_POLICY_PROVIDER_IDENTITY = measure_module_artifact(str(_POLICY_PROVIDER_PATH))


class HeadlessWorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_mode: Literal["repository", "seeded"] = "repository"
    workspace_directory_mode: int = Field(default=0o700, ge=0, le=0o777, strict=True)
    workspace_seed_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    repository_snapshot_digest: str | None = Field(
        default=None, pattern=_DIGEST_PATTERN
    )
    base_commit: str | None = Field(default=None, pattern=_GIT_COMMIT_PATTERN)
    task_image_digest: str = Field(pattern=_DIGEST_PATTERN)
    outer_isolation: Literal["apptainer"] | None = None

    @model_validator(mode="after")
    def _workspace_authority_is_exact(self) -> HeadlessWorkspaceInput:
        if self.workspace_mode == "repository":
            if self.base_commit is None:
                raise ValueError("repository workspace requires base_commit")
            if self.workspace_seed_digest is not None:
                raise ValueError("repository workspace cannot declare a seed tree")
        else:
            if self.workspace_directory_mode != 0o700:
                raise ValueError(
                    "seeded workspace directory mode must be canonical 0700"
                )
            if (
                self.repository_snapshot_digest is not None
                or self.base_commit is not None
                or self.workspace_seed_digest is None
            ):
                raise ValueError(
                    "seeded workspace requires a seed tree and cannot declare repository authority"
                )
        return self

    def identity_dict(self) -> dict[str, Any]:
        identity = self.model_dump(mode="json")
        if self.workspace_mode == "repository":
            # These defaults were not present in the original request schema.
            identity.pop("workspace_mode", None)
            identity.pop("workspace_directory_mode", None)
            identity.pop("workspace_seed_digest", None)
        return identity


class HeadlessProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    authority_model_id: str = Field(min_length=1, max_length=256)
    credential_handle: str = Field(min_length=1, max_length=256)
    context_window: int = Field(gt=0, le=2**53 - 1, strict=True)
    max_output_tokens: int = Field(gt=0, le=2**53 - 1, strict=True)
    timeout_seconds: float = Field(gt=0, le=3_600)
    sampling: Mapping[str, Any] = Field(default_factory=dict)
    capabilities: Mapping[str, Any] = Field(default_factory=dict)
    compatibility: Mapping[str, Any] = Field(default_factory=dict)
    request_policy: OpenAICompletionsRequestPolicy | None = None

    @field_validator("model", "authority_model_id")
    @classmethod
    def _identifiers_have_no_controls(cls, value: str) -> str:
        return validate_wire_model(value)


    def load_profile(
        self,
        *,
        credential: str,
        route: HeadlessProviderRouteAuthority,
    ) -> OpenAICompletionsProviderProfile:
        return OpenAICompletionsProviderProfile(
            model=self.model,
            scoped_credential=credential,
            base_url=route.base_url,
            context_window=self.context_window,
            max_output_tokens=self.max_output_tokens,
            sampling=self.sampling,
            caller_headers=route.caller_headers,
            capabilities=self.capabilities,
            compatibility=self.compatibility,
            request_policy=(
                OpenAICompletionsRequestPolicy()
                if self.request_policy is None
                else self.request_policy
            ),
        )

    def identity_dict(self) -> dict[str, Any]:
        identity = {
            "model": self.model,
            "authority_model_id": self.authority_model_id,
            "credential_handle": self.credential_handle,
            "context_window": self.context_window,
            "sampling_digest": _digest_bytes(_canonical_bytes(dict(self.sampling))),
            "max_output_tokens": self.max_output_tokens,
            "capabilities_digest": _digest_bytes(
                _canonical_bytes(dict(self.capabilities))
            ),
            "compatibility_digest": _digest_bytes(
                _canonical_bytes(dict(self.compatibility))
            ),
            "timeout_seconds": self.timeout_seconds,
        }
        if self.request_policy is not None:
            identity["request_policy"] = self.request_policy.as_dict()
        return identity


class HeadlessProviderRouteAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["bb.rl.headless-provider-route-authority.v1"] = (
        "bb.rl.headless-provider-route-authority.v1"
    )
    model: str = Field(min_length=1, max_length=256)
    authority_model_id: str = Field(min_length=1, max_length=256)
    base_url: str
    caller_headers: Mapping[str, str] = Field(default_factory=dict)
    policy_observation_digest: str = Field(pattern=_DIGEST_PATTERN)

    @field_validator("base_url")
    @classmethod
    def _base_url_is_loopback(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
            address = parsed.hostname
            literal = ipaddress.ip_address(address or "")
        except ValueError:
            raise ValueError(
                "provider base_url must use an explicit loopback port"
            ) from None
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port is None
            or port < 1
            or address is None
            or not literal.is_loopback
        ):
            raise ValueError("provider base_url must use an explicit loopback port")
        return value

    @field_validator("model", "authority_model_id")
    @classmethod
    def _identifiers_have_no_controls(cls, value: str) -> str:
        return validate_wire_model(value)

    @model_validator(mode="after")
    def _caller_headers_are_exact(self) -> HeadlessProviderRouteAuthority:
        normalized_names = []
        for name, value in self.caller_headers.items():
            if (
                type(name) is not str
                or not name
                or "\r" in name
                or "\n" in name
                or type(value) is not str
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("caller headers must contain valid text")
            normalized_names.append(name.casefold())
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("caller header names must be unique case-insensitively")
        return self

    def identity_dict(self) -> dict[str, Any]:
        header_items = sorted(
            (name.casefold(), value) for name, value in self.caller_headers.items()
        )
        header_names = [name for name, _value in header_items]
        return {
            "schema_version": self.schema_version,
            "model": self.model,
            "authority_model_id": self.authority_model_id,
            "base_url_sha256": _digest_bytes(self.base_url.encode("utf-8")),
            "caller_header_count": len(header_names),
            "caller_header_names_sha256": _digest_bytes(_canonical_bytes(header_names)),
            "caller_headers_sha256": _digest_bytes(_canonical_bytes(header_items)),
            "policy_observation_digest": self.policy_observation_digest,
        }


class HeadlessRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "bb.rl.headless-run-request.v1",
        "bb.rl.headless-run-request.v2",
        "bb.rl.headless-run-request.v3",
    ] = "bb.rl.headless-run-request.v1"
    target_id: str
    target_overlay_id: str
    target_dynamic_fields: Mapping[str, JsonValue]
    resolve_request: c.ResolveEpisodeRequest
    prompt: str = Field(min_length=1, max_length=4 * 1024 * 1024)
    tool_allowlist: tuple[str, ...]
    context: Mapping[str, Any] = Field(default_factory=dict)
    workspace: HeadlessWorkspaceInput
    expected_resources: c.ResourceLimits
    expected_limits: c.ExecutionLimits
    expected_sandbox: c.SandboxGrant
    provider: HeadlessProviderInput
    result_path: str
    event_log_path: str
    patch_path: str | None = None

    @field_validator("result_path", "event_log_path")
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        if not os.path.isabs(value) or os.path.normpath(value) != value:
            raise ValueError("headless paths must be normalized absolute paths")
        return value

    @field_validator("patch_path")
    @classmethod
    def _optional_path_is_absolute(cls, value: str | None) -> str | None:
        if value is not None and (
            not os.path.isabs(value) or os.path.normpath(value) != value
        ):
            raise ValueError("headless paths must be normalized absolute paths")
        return value

    @model_validator(mode="after")
    def _request_is_closed(self) -> HeadlessRunRequest:
        destinations = [self.result_path, self.event_log_path]
        if self.patch_path is not None:
            destinations.append(self.patch_path)
        if len(destinations) != len(set(destinations)):
            raise ValueError("headless output paths must differ")
        if not self.target_id or not self.target_overlay_id:
            raise ValueError("target and overlay identities are required")
        if any(type(name) is not str or not name for name in self.target_dynamic_fields):
            raise ValueError("target_dynamic_fields requires non-empty field names")
        if self.schema_version == "bb.rl.headless-run-request.v3":
            if self.provider.request_policy is None:
                raise ValueError("headless request v3 requires an explicit request policy")
        elif self.provider.request_policy is not None:
            raise ValueError(
                "request policy is only supported by headless request v3"
            )
        inputs = serialize_e4_target_inputs(self.schema_version, self.target_dynamic_fields)
        if len(inputs) > _MAX_REQUEST_BYTES:
            raise ValueError("target input frame exceeds the request byte limit")
        if (
            not self.tool_allowlist
            or len(set(self.tool_allowlist)) != len(self.tool_allowlist)
            or any(type(name) is not str or not name for name in self.tool_allowlist)
        ):
            raise ValueError("tool_allowlist must contain unique tool names")
        if self.provider.timeout_seconds * 1_000 > self.expected_resources.wall_time_ms:
            raise ValueError(
                "provider timeout cannot exceed the episode wall-time limit"
            )
        freeze_json_object(self.context, field_name="headless context")
        return self

    def identity_dict(
        self,
        *,
        composition_manifest_ref: str,
        target: E4TargetPolicyProjection,
        provider_profile: OpenAICompletionsProviderProfile,
        provider_route: HeadlessProviderRouteAuthority,
    ) -> dict[str, Any]:
        provider_profile_identity = provider_profile.identity_dict()
        identity = {
            "schema_version": "bb.rl.headless-run-identity.v1",
            "composition_manifest_ref": composition_manifest_ref,
            "target": target.identity_dict(),
            "resolve_request": self.resolve_request.model_dump(mode="json"),
            "prompt_digest": _digest_bytes(self.prompt.encode("utf-8")),
            "context_digest": _canonical_digest(
                thaw_json(
                    freeze_json_object(self.context, field_name="headless context")
                )
            ),
            "workspace": self.workspace.identity_dict(),
            "expected_resources": self.expected_resources.model_dump(mode="json"),
            "expected_limits": self.expected_limits.model_dump(mode="json"),
            "tool_allowlist": list(self.tool_allowlist),
            "expected_sandbox": self.expected_sandbox.model_dump(mode="json"),
            "provider_profile": provider_profile_identity,
            "provider": self.provider.identity_dict(),
            "provider_route": provider_route.identity_dict(),
            "provider_timeout_seconds": self.provider.timeout_seconds,
        }
        if self.schema_version in {
            "bb.rl.headless-run-request.v2",
            "bb.rl.headless-run-request.v3",
        }:
            identity.update(
                {
                    "schema_version": (
                        "bb.rl.headless-run-identity.v2"
                        if self.schema_version.endswith(".v2")
                        else "bb.rl.headless-run-identity.v3"
                    ),
                    "request_schema_version": self.schema_version,
                    "target_input_digest": target.input_digest,
                    "target_index_digest": target.index_digest,
                    "target_descriptor_bytes_digest": target.descriptor_bytes_digest,
                    "target_renderer_id": target.renderer_id,
                }
            )
        return identity


class HeadlessRunFailed(RuntimeError):
    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__("headless episode failed")
        self.result = dict(result)


def load_headless_request(path: str) -> HeadlessRunRequest:
    payload = _read_regular_file(path, max_bytes=_MAX_REQUEST_BYTES)
    return HeadlessRunRequest.model_validate_json(payload, strict=True)


def load_headless_provider_route_authority(
    path: str,
) -> HeadlessProviderRouteAuthority:
    payload = _read_regular_file(
        path,
        max_bytes=_MAX_REQUEST_BYTES,
        require_private_mode=True,
    )
    return HeadlessProviderRouteAuthority.model_validate_json(payload, strict=True)


def select_pinned_target_projection(
    request: HeadlessRunRequest,
    compiler: PinnedServerCompilerAdapter,
    package: E4TargetPackage,
) -> E4TargetPolicyProjection:
    """Select an equivalent bootstrap projection without resolving the episode."""
    if package.target_id != request.target_id:
        raise ValueError("headless request and target identities do not match")
    inputs = bind_e4_target_inputs(
        package, request.schema_version, request.target_dynamic_fields
    )
    expected = {
        "target_id": request.target_id,
        "target_schema_version": package.descriptor["schema_version"],
        "request_schema_version": request.schema_version,
        "input_digest": _digest_bytes(inputs),
        "index_digest": _digest_bytes(package.index_bytes),
        "descriptor_bytes_digest": "sha256:" + package.descriptor_sha256,
    }
    selected: E4TargetPolicyProjection | None = None
    for manifest in compiler.pinned_manifests.values():
        binding = manifest.semantic.metadata.get("e4_target")
        if not isinstance(binding, Mapping) or any(
            binding.get(name) != value for name, value in expected.items()
        ):
            continue
        candidate = E4TargetPolicyProjection.from_compiled(manifest)
        if candidate.overlay_id != request.target_overlay_id:
            raise ValueError("selected target overlay does not match the compiled target")
        if candidate.ordered_tool_names != request.tool_allowlist:
            raise ValueError("requested tool allowlist does not match the compiled target")
        if selected is not None and selected != candidate:
            raise ValueError("pinned compilations have divergent target projections")
        selected = candidate
    if selected is None:
        raise ValueError("no pinned compilation matches target bytes and request inputs")
    return selected


async def run_headless_request(
    request: HeadlessRunRequest,
    *,
    composition_ref_path: str,
    secret_files: Mapping[str, str],
    provider_credentials: Mapping[str, str],
    provider_routes: Mapping[str, HeadlessProviderRouteAuthority],
    repository_base_commits: Mapping[str, str],
    composition_ref_data: bytes | None = None,
) -> dict[str, Any]:
    if type(request) is not HeadlessRunRequest:
        raise TypeError("request must be an exact HeadlessRunRequest")
    if composition_ref_data is not None:
        if type(composition_ref_data) is not bytes:
            raise TypeError("composition_ref_data must be exact bytes")
        if len(composition_ref_data) > _MAX_REQUEST_BYTES:
            raise ValueError("composition_ref_data exceeds size bound")
    target: E4TargetPolicyProjection | None = None
    profile: OpenAICompletionsProviderProfile | None = None
    route: HeadlessProviderRouteAuthority | None = None
    composition: ProductionComposition | None = None
    try:
        if (
            request.expected_sandbox.runtime_class is c.RuntimeClass.TRUSTED_PROCESS
            and request.workspace.outer_isolation != "apptainer"
        ):
            raise ValueError(
                "headless trusted-process execution requires outer Apptainer isolation"
            )
        composition_secrets = _secret_file_bindings(
            secret_files,
            field_name="composition secret files",
        )
        provider_secrets = _secret_file_bindings(
            provider_credentials,
            field_name="provider credentials",
        )
        _validate_repository_base_commit_binding(
            request,
            repository_base_commits,
        )
        if set(provider_secrets) != {request.provider.credential_handle}:
            raise ValueError(
                "provider credential handles do not match the headless request"
            )
        if set(provider_routes) != {request.provider.credential_handle} or any(
            type(value) is not HeadlessProviderRouteAuthority
            for value in provider_routes.values()
        ):
            raise ValueError(
                "provider route authorities do not match the headless request"
            )
        route = provider_routes[request.provider.credential_handle]
        if (
            request.provider.model != route.model
            or request.provider.authority_model_id != route.authority_model_id
        ):
            raise ValueError(
                "provider model identities do not match launcher route authority"
            )
        target_package = load_e4_target(request.target_id)
        if composition_ref_data is None:
            composition_ref_data = _read_regular_file(
                composition_ref_path, max_bytes=_MAX_REQUEST_BYTES
            )
        preflight_compiler = load_pinned_compiler(
            composition_ref_path, composition_ref_data=composition_ref_data
        )
        preflight_target = select_pinned_target_projection(
            request, preflight_compiler, target_package
        )
        target = preflight_target
        profile = request.provider.load_profile(
            credential=_read_secret_text(
                provider_secrets[request.provider.credential_handle]
            ),
            route=route,
        )
        episode_id = request.resolve_request.episode_id

        def resolver_factory(
            authority: ManagedPolicyRuntimeClientResolver,
            compiler: PinnedServerCompilerAdapter,
        ) -> EpisodeOpenAICompletionsPolicyResolver:
            if (
                compiler.pinned_manifests.keys()
                != preflight_compiler.pinned_manifests.keys()
            ):
                raise ValueError("composition compiler changed after target admission")
            return EpisodeOpenAICompletionsPolicyResolver(
                authority,
                profiles={episode_id: profile},
                credential_handle_ids={episode_id: request.provider.credential_handle},
                target_projections={episode_id: preflight_target},
                authority_model_ids={episode_id: request.provider.authority_model_id},
                authority_wire_models={episode_id: route.model},
                expected_observation_digests={
                    episode_id: route.policy_observation_digest
                },
                timeout_seconds={episode_id: request.provider.timeout_seconds},
                request_limits={episode_id: request.expected_limits.max_turns},
            )

        composition = load_production_composition(
            composition_ref_path,
            composition_secrets,
            composition_ref_data=composition_ref_data,
            policy_client_resolver_factory=resolver_factory,
        )
        await composition.service.start()
        config_identity = request.identity_dict(
            composition_manifest_ref=composition.manifest_ref,
            target=target,
            provider_profile=profile,
            provider_route=route,
        )
        config_digest = _canonical_digest(config_identity)
        result = _base_result(
            request,
            config_digest=config_digest,
            config_identity=config_identity,
            target=target,
            profile=profile,
            route=route,
            composition=composition,
        )
    except BaseException as exc:
        preflight_cancellation = (
            exc if isinstance(exc, asyncio.CancelledError) else None
        )
        failure: BaseException = exc
        if composition is not None:
            try:
                await composition.close()
            except BaseException as cleanup_exc:
                failure = BaseExceptionGroup(
                    "headless preflight and cleanup failed",
                    [exc, cleanup_exc],
                )
        result = _preflight_failure_result(
            request,
            composition_ref_path=composition_ref_path,
            composition_ref_data=composition_ref_data,
            target=target,
            profile=profile,
            route=route,
            failure=failure,
        )
        _atomic_write(request.result_path, _canonical_bytes(result))
        if preflight_cancellation is not None:
            raise preflight_cancellation
        raise HeadlessRunFailed(result) from None
    loop = asyncio.get_running_loop()
    episode_deadline = loop.time() + request.expected_resources.wall_time_ms / 1_000
    primary_failure: BaseException | None = None
    cleanup_failure: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None
    created = False
    run_started = False
    terminal_unsuccessful = False
    event_bytes: bytes | None = None
    patch_bytes: bytes | None = None
    try:
        async with asyncio.timeout_at(episode_deadline):
            create_operation = await composition.service.create(request.resolve_request)
            create = create_operation.response
            created = True
            result["create"] = {
                "create_fingerprint": create.create_fingerprint,
                "effective_plan_digest": create.effective_plan_digest,
                "effective_plan_ref": _artifact_ref_projection(
                    create.effective_plan_ref
                ),
                "policy_binding_digest": create.policy_binding_digest,
                "policy_observation_digest": create.policy_observation_digest,
            }
            result["sandbox_identity"] = asdict(create.sandbox_preflight)
            effective_plan = _load_effective_plan(
                composition, create.effective_plan_ref
            )
            _validate_effective_plan(request, target, effective_plan)
            run_started = True
            run_operation = await composition.service.run(
                episode_id,
                create_fingerprint=create.create_fingerprint,
                task_input={"prompt": request.prompt},
                context=request.context,
            )
            run = run_operation.response
            event_bytes, patch_bytes = _project_headless_run(
                result,
                run,
                composition,
                expected_base_commit=(
                    request.workspace.base_commit
                    if request.workspace.workspace_mode == "repository"
                    else request.workspace.workspace_seed_digest
                ),
            )
            close_operation = await composition.service.close_episode(episode_id)
            closed = await composition.service.get_closed_envelope(episode_id)
            _project_headless_cleanup(result, close_operation.response, closed)
            terminal_unsuccessful = run.primary_disposition.value != "succeeded"
    except asyncio.CancelledError as exc:
        cancellation = exc
        primary_failure = exc
    except BaseException as exc:
        primary_failure = exc
    finally:
        if created:
            try:
                close_operation = await composition.service.close_episode(episode_id)
                closed = await composition.service.get_closed_envelope(episode_id)
                _project_headless_cleanup(result, close_operation.response, closed)
                if run_started and primary_failure is not None:
                    replay = await composition.service.run(
                        episode_id,
                        create_fingerprint=create.create_fingerprint,
                        task_input={"prompt": request.prompt},
                        context=request.context,
                    )
                    run = replay.response
                    event_bytes, patch_bytes = _project_headless_run(
                        result,
                        run,
                        composition,
                        expected_base_commit=(
                            request.workspace.base_commit
                            if request.workspace.workspace_mode == "repository"
                            else request.workspace.workspace_seed_digest
                        ),
                    )
                    terminal_unsuccessful = run.primary_disposition.value != "succeeded"
            except BaseException as exc:
                cleanup_failure = exc
        try:
            await composition.close()
        except BaseException as exc:
            cleanup_failure = cleanup_failure or exc
        try:
            inventory = composition.observe_cleanup_inventory()
            bridge_receipt = composition.outer_bridge_cleanup_receipt
            bridge_projection = (
                None
                if bridge_receipt is None
                else bridge_receipt.model_dump(mode="json")
            )
            result["cleanup_inventory"] = inventory.canonical_projection(
                bridge_projection
            )
            result["cleanup_inventory_digest"] = inventory.canonical_digest(
                bridge_projection
            )
            if not _inventory_is_empty(inventory):
                cleanup_failure = cleanup_failure or RuntimeError(
                    "runtime cleanup inventory is not empty"
                )
        except BaseException as exc:
            cleanup_failure = cleanup_failure or exc
    if loop.time() >= episode_deadline and primary_failure is None:
        primary_failure = TimeoutError("aggregate episode wall-time limit exceeded")

    result["event_log"] = _event_log_projection(event_bytes, request.event_log_path)
    result["patch"] = _patch_projection(patch_bytes, request.patch_path)
    publication_failure: BaseException | None = None
    if event_bytes is not None:
        try:
            _atomic_write(request.event_log_path, event_bytes)
        except Exception as exc:
            publication_failure = exc
            result["event_log"] = {
                **result["event_log"],
                "publication_failure": _safe_failure_projection(exc),
            }
    if request.patch_path is not None and patch_bytes is not None:
        try:
            _atomic_write(request.patch_path, patch_bytes)
        except Exception as exc:
            publication_failure = publication_failure or exc
            result["patch"] = {
                **result["patch"],
                "publication_failure": _safe_failure_projection(exc),
            }
    deadline_exceeded = loop.time() >= episode_deadline
    if deadline_exceeded and primary_failure is None:
        primary_failure = TimeoutError("aggregate episode wall-time limit exceeded")
    result["episode_timing"] = {
        "wall_time_ms": request.expected_resources.wall_time_ms,
        "deadline_exceeded": deadline_exceeded,
    }
    if (
        primary_failure is not None
        or cleanup_failure is not None
        or publication_failure is not None
    ):
        result["terminal"] = {
            **result.get("terminal", {}),
            "status": "failed",
            "failure": _safe_failure_projection(
                publication_failure
                or cleanup_failure
                or primary_failure
                or RuntimeError()
            ),
            "primary_failure": (
                None
                if primary_failure is None
                else _safe_failure_projection(primary_failure)
            ),
            "cleanup_failure": (
                None
                if cleanup_failure is None
                else _safe_failure_projection(cleanup_failure)
            ),
            "publication_failure": (
                None
                if publication_failure is None
                else _safe_failure_projection(publication_failure)
            ),
        }
    result_bytes = _canonical_bytes(result)
    _atomic_write(request.result_path, result_bytes)
    if cancellation is not None:
        raise cancellation
    if (
        primary_failure is not None
        or cleanup_failure is not None
        or publication_failure is not None
        or terminal_unsuccessful
    ):
        raise HeadlessRunFailed(result)
    return result


async def run_headless_request_file(
    path: str,
    *,
    composition_ref_path: str,
    secret_files: Mapping[str, str],
    provider_credentials: Mapping[str, str],
    provider_route_files: Mapping[str, str],
    repository_base_commits: Mapping[str, str],
) -> dict[str, Any]:
    return await run_headless_request(
        load_headless_request(path),
        composition_ref_path=composition_ref_path,
        secret_files=secret_files,
        provider_credentials=provider_credentials,
        provider_routes={
            handle: load_headless_provider_route_authority(route_path)
            for handle, route_path in _secret_file_bindings(
                provider_route_files,
                field_name="provider route files",
            ).items()
        },
        repository_base_commits=repository_base_commits,
    )


def _validate_repository_base_commit_binding(
    request: HeadlessRunRequest,
    bindings: Mapping[str, str],
) -> None:
    if request.workspace.workspace_mode == "seeded":
        if bindings:
            raise ValueError("seeded workspace cannot have repository base bindings")
        return
    if any(
        type(digest) is not str
        or re.fullmatch(_DIGEST_PATTERN, digest) is None
        or type(commit) is not str
        or re.fullmatch(_GIT_COMMIT_PATTERN, commit) is None
        for digest, commit in bindings.items()
    ):
        raise ValueError("repository base-commit bindings are invalid")
    expected_digest = (
        request.workspace.repository_snapshot_digest
        or request.workspace.task_image_digest
    )
    expected_commit = request.workspace.base_commit
    if (
        expected_commit is None
        or set(bindings) != {expected_digest}
        or bindings[expected_digest] != expected_commit
    ):
        raise ValueError(
            "repository base commit is not bound to the admitted workspace authority"
        )


def _validate_effective_plan(
    request: HeadlessRunRequest,
    target: E4TargetPolicyProjection,
    plan: c.EffectiveExecutionPlan,
) -> None:
    if plan.effective_capabilities.resources != request.expected_resources:
        raise ValueError("effective resource limits do not match the headless request")
    if plan.effective_capabilities.limits != request.expected_limits:
        raise ValueError("effective execution limits do not match the headless request")
    if plan.sandbox != request.expected_sandbox:
        raise ValueError("effective sandbox grant does not match the headless request")
    if request.workspace.workspace_mode == "seeded":
        root_mounts = tuple(
            mount for mount in plan.sandbox.mounts if mount.target_logical_path == "."
        )
        if (
            request.workspace.workspace_seed_digest is None
            or len(root_mounts) != 1
            or root_mounts[0].source_artifact_digest
            != request.workspace.workspace_seed_digest
        ):
            raise ValueError("seeded workspace root is not bound to its seed artifact")
    if plan.sandbox.image_digest != request.workspace.task_image_digest:
        raise ValueError("effective sandbox image does not match the workspace input")
    if (
        plan.task.repository_snapshot_digest
        != request.workspace.repository_snapshot_digest
    ):
        raise ValueError(
            "effective repository snapshot does not match the workspace input"
        )
    target.validate_semantics(plan.effective_semantics)






def _load_effective_plan(
    composition: ProductionComposition,
    artifact_ref: Any,
) -> c.EffectiveExecutionPlan:
    payload = _cas_bytes(composition, artifact_ref, max_bytes=_MAX_EVIDENCE_BYTES)
    return c.EffectiveExecutionPlan.model_validate_json(payload, strict=True)


def _load_evidence_projection(
    composition: ProductionComposition,
    evidence_manifest_ref: Any,
) -> tuple[dict[str, Any], bytes]:
    manifest_bytes = _cas_bytes(
        composition,
        evidence_manifest_ref,
        max_bytes=_MAX_EVIDENCE_BYTES,
    )
    manifest = json.loads(manifest_bytes)
    runner_ref = manifest["runner_ledger_ref"]
    event_bytes = _cas_bytes(
        composition,
        runner_ref,
        max_bytes=_MAX_EVIDENCE_BYTES,
    )
    artifact_manifest_ref = manifest["artifact_manifest_ref"]
    return (
        {
            "materialization_digest": manifest.get("materialization_digest"),
            "final_workspace_snapshot_digest": manifest.get("verifier_snapshot_digest"),
            "runner_event_ledger_ref": runner_ref,
            "runner_event_ledger_digest": _digest_bytes(event_bytes),
            "artifact_manifest_ref": artifact_manifest_ref,
        },
        event_bytes,
    )


def _cas_bytes(
    composition: ProductionComposition,
    artifact_ref: Any,
    *,
    max_bytes: int,
) -> bytes:
    if isinstance(artifact_ref, Mapping):
        artifact_id = artifact_ref["artifact_id"]
        expected_digest = artifact_ref["sha256"]
    else:
        artifact_id = artifact_ref.artifact_id
        expected_digest = artifact_ref.sha256
    stored = composition.authority_graph.cas.get_ref(artifact_id)
    if stored.sha256 != expected_digest:
        raise ValueError("CAS artifact reference digest mismatch")
    return composition.authority_graph.cas.get_bytes(stored, max_bytes=max_bytes)


def _preflight_failure_result(
    request: HeadlessRunRequest,
    *,
    composition_ref_path: str,
    composition_ref_data: bytes | None,
    target: E4TargetPolicyProjection | None,
    profile: OpenAICompletionsProviderProfile | None,
    route: HeadlessProviderRouteAuthority | None,
    failure: BaseException,
) -> dict[str, Any]:
    dynamic_field_digests: dict[str, str] = {}
    for name, value in sorted(request.target_dynamic_fields.items()):
        if request.schema_version == "bb.rl.headless-run-request.v1":
            if type(value) is not str:
                raise ValueError("v1 target inputs require text")
            encoded = value.encode("utf-8")
        else:
            encoded = serialize_e4_target_inputs(request.schema_version, {name: value})
        dynamic_field_digests[name] = _digest_bytes(encoded)
    profile_identity = None if profile is None else profile.identity_dict()
    config_identity = {
        "schema_version": (
            "bb.rl.headless-preflight-identity.v1"
            if request.schema_version == "bb.rl.headless-run-request.v1"
            else (
                "bb.rl.headless-preflight-identity.v2"
                if request.schema_version == "bb.rl.headless-run-request.v2"
                else "bb.rl.headless-preflight-identity.v3"
            )
        ),
        "composition_ref_digest": (
            _digest_bytes(composition_ref_data)
            if composition_ref_data is not None
            else _optional_regular_file_digest(
                composition_ref_path,
                max_bytes=_MAX_REQUEST_BYTES,
            )
        ),
        "target": (
            {
                "target_id": request.target_id,
                "overlay_id": request.target_overlay_id,
                "dynamic_field_digests": dynamic_field_digests,
            }
            if target is None
            else target.identity_dict()
        ),
        "resolve_request": request.resolve_request.model_dump(mode="json"),
        "prompt_digest": _digest_bytes(request.prompt.encode("utf-8")),
        "context_digest": _canonical_digest(
            thaw_json(
                freeze_json_object(request.context, field_name="headless context")
            )
        ),
        "workspace": request.workspace.identity_dict(),
        "tool_allowlist": list(request.tool_allowlist),
        "expected_resources": request.expected_resources.model_dump(mode="json"),
        "expected_limits": request.expected_limits.model_dump(mode="json"),
        "expected_sandbox": request.expected_sandbox.model_dump(mode="json"),
        "provider_profile": profile_identity,
        "provider_route": None if route is None else route.identity_dict(),
    }
    if request.schema_version == "bb.rl.headless-run-request.v3":
        config_identity["request_schema_version"] = request.schema_version
    try:
        distribution_version = version("breadboard-harness-cli")
    except PackageNotFoundError:
        distribution_version = "uninstalled"
    return {
        "schema_version": "bb.rl.headless-result.v1",
        "episode_id": request.resolve_request.episode_id,
        "config_digest": _canonical_digest(config_identity),
        "config_identity": config_identity,
        "engine_identity": {
            "distribution": "breadboard-harness-cli",
            "version": distribution_version,
            "headless_module_digest": _HEADLESS_MODULE_IDENTITY.digest,
            "policy_provider_module_digest": _POLICY_PROVIDER_IDENTITY.digest,
            "composition_manifest_ref": None,
        },
        "provider_profile_identity": (
            request.provider.identity_dict()
            if profile is None
            else profile.identity_dict()
        ),
        "provider_input_identity": request.provider.identity_dict(),
        "provider_route_identity": (None if route is None else route.identity_dict()),
        "target_identity": config_identity["target"],
        "workspace_input": request.workspace.model_dump(mode="json"),
        "terminal": {
            "status": "failed",
            "reason": None,
            "turn_count": 0,
            "response": None,
            "failure": _safe_failure_projection(failure),
            "primary_failure": _safe_failure_projection(failure),
            "cleanup_failure": None,
        },
        "create": None,
        "sandbox_identity": None,
        "workspace_evidence": None,
        "evidence": None,
        "cleanup": None,
        "cleanup_inventory": None,
        "cleanup_inventory_digest": None,
        "event_log": _event_log_projection(None, request.event_log_path),
        "patch": _patch_projection(None, request.patch_path),
    }


def _base_result(
    request: HeadlessRunRequest,
    *,
    config_digest: str,
    config_identity: Mapping[str, Any],
    target: E4TargetPolicyProjection,
    profile: OpenAICompletionsProviderProfile,
    route: HeadlessProviderRouteAuthority,
    composition: ProductionComposition,
) -> dict[str, Any]:
    try:
        distribution_version = version("breadboard-harness-cli")
    except PackageNotFoundError:
        distribution_version = "uninstalled"
    return {
        "schema_version": "bb.rl.headless-result.v1",
        "episode_id": request.resolve_request.episode_id,
        "config_digest": config_digest,
        "config_identity": dict(config_identity),
        "engine_identity": {
            "distribution": "breadboard-harness-cli",
            "version": distribution_version,
            "headless_module_digest": _HEADLESS_MODULE_IDENTITY.digest,
            "policy_provider_module_digest": _POLICY_PROVIDER_IDENTITY.digest,
            "composition_manifest_ref": composition.manifest_ref,
        },
        "provider_profile_identity": profile.identity_dict(),
        "provider_input_identity": request.provider.identity_dict(),
        "provider_route_identity": route.identity_dict(),
        "target_identity": target.identity_dict(),
        "workspace_input": request.workspace.model_dump(mode="json"),
        "terminal": {
            "status": "initializing",
            "reason": None,
            "turn_count": 0,
            "response": None,
        },
        "create": None,
        "sandbox_identity": None,
        "workspace_evidence": None,
        "evidence": None,
        "cleanup": None,
        "cleanup_inventory": None,
        "cleanup_inventory_digest": None,
        "event_log": None,
        "patch": None,
    }


def _project_headless_run(
    result: dict[str, Any],
    run: V2RunResult,
    composition: ProductionComposition,
    *,
    expected_base_commit: str,
) -> tuple[bytes | None, bytes | None]:
    result["terminal"] = {
        "status": run.primary_disposition.value,
        "reason": run.termination,
        "turn_count": run.turn_count,
        "response": None if run.response is None else thaw_json(run.response),
        "run_failure": (
            None
            if run.primary_failure is None
            else {
                "category": run.primary_failure.category,
                "code": run.primary_failure.code,
            }
        ),
    }
    result["evidence"] = {
        "completed_envelope_ref": _optional_ref(run.completed_envelope_ref),
        "closed_envelope_ref": _optional_ref(run.closed_envelope_ref),
        "result_ref": _optional_ref(run.result_ref),
        "evidence_manifest_ref": _optional_ref(run.evidence_manifest_ref),
        "evidence_root": run.evidence_root,
        "artifact_manifest_ref": _optional_ref(run.artifact_manifest_ref),
        "primary_measurement_digest": run.primary_measurement_digest,
        "verifier_measurement_digest": run.verifier_measurement_digest,
        "verifier_result_digest": run.verifier_result_digest,
        "reward": run.reward,
        "reward_components": dict(run.reward_components),
    }
    if run.evidence_manifest_ref is None:
        raise ValueError("headless evidence manifest is unavailable")
    evidence_projection, event_bytes = _load_evidence_projection(
        composition,
        run.evidence_manifest_ref,
    )
    workspace_diff = run.workspace_diff
    if workspace_diff is None:
        if run.primary_disposition is not EpisodePrimaryDisposition.SUCCEEDED:
            result["workspace_evidence"] = evidence_projection
            return event_bytes, None
        raise ValueError("canonical workspace diff is unavailable")
    expected_keys = {
        "returncode", "stdout", "stderr", "base_commit",
        "git_executable_digest", "patch_digest", "snapshot_root_digest",
    }
    if (
        not isinstance(workspace_diff, Mapping)
        or set(workspace_diff) != expected_keys
        or workspace_diff.get("returncode") != 0
        or type(workspace_diff.get("stdout")) is not str
        or workspace_diff.get("stderr") != ""
        or type(workspace_diff.get("base_commit")) is not str
        or type(workspace_diff.get("git_executable_digest")) is not str
        or type(workspace_diff.get("patch_digest")) is not str
        or type(workspace_diff.get("snapshot_root_digest")) is not str
    ):
        raise ValueError("canonical workspace diff is unavailable")
    if workspace_diff["base_commit"] != expected_base_commit:
        raise ValueError("canonical workspace patch base commit mismatch")
    patch_bytes = workspace_diff["stdout"].encode("utf-8")
    if workspace_diff["patch_digest"] != _digest_bytes(patch_bytes):
        raise ValueError("canonical workspace patch digest mismatch")
    result["workspace_evidence"] = {
        **evidence_projection,
        "patch_digest": workspace_diff["patch_digest"],
        "patch_base_commit": workspace_diff["base_commit"],
        "patch_git_executable_digest": workspace_diff["git_executable_digest"],
        "patch_snapshot_root_digest": workspace_diff["snapshot_root_digest"],
    }
    return event_bytes, patch_bytes


def _project_headless_cleanup(
    result: dict[str, Any],
    close_response: Any,
    closed: Any,
) -> None:
    result["cleanup"] = {
        "disposition": close_response.cleanup_disposition.value,
        "receipt_digest": closed.cleanup_receipt_digest,
        "receipt": (
            None
            if closed.cleanup_receipt is None
            else thaw_json(closed.cleanup_receipt)
        ),
        "closed_envelope_digest": closed.digest,
    }


def _event_log_projection(
    payload: bytes | None,
    destination: str,
) -> dict[str, Any]:
    return {
        "destination": destination,
        "digest": None if payload is None else _digest_bytes(payload),
        "size_bytes": None if payload is None else len(payload),
        "available": payload is not None,
    }


def _patch_projection(
    payload: bytes | None,
    destination: str | None,
) -> dict[str, Any]:
    return {
        "requested": destination is not None,
        "destination": destination,
        "digest": None if payload is None else _digest_bytes(payload),
        "size_bytes": None if payload is None else len(payload),
        "available": payload is not None,
    }


def _artifact_ref_projection(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError("artifact reference is not serializable")


def _optional_ref(value: Any) -> dict[str, Any] | None:
    return None if value is None else _artifact_ref_projection(value)


def _inventory_is_empty(value: Any) -> bool:
    return not any(
        (
            value.active_lease_ids,
            value.orphan_resource_ids,
            value.leaked_artifact_ids,
            value.cleanup_errors,
            value.container_ids,
            value.process_ids,
            value.cgroup_paths,
            value.mount_paths,
            value.workspace_paths,
            value.artifact_paths,
            value.secret_lease_ids,
            value.broker_descriptor_count,
        )
    )


def _safe_failure_projection(exc: BaseException) -> dict[str, str]:
    failure = getattr(exc, "failure", None)
    code = getattr(failure, "code", None) or getattr(exc, "code", None)
    category = getattr(failure, "category", None)
    if type(code) is not str or not code or len(code) > 128:
        code = type(exc).__name__
    if type(category) is not str or not category or len(category) > 128:
        category = type(exc).__name__
    return {"code": code, "category": category}


def _secret_file_bindings(
    bindings: Mapping[str, str],
    *,
    field_name: str,
) -> dict[str, str]:
    if not isinstance(bindings, Mapping) or not bindings:
        raise ValueError(f"{field_name} must contain launcher-supplied bindings")
    copied: dict[str, str] = {}
    for handle, path in bindings.items():
        if (
            type(handle) is not str
            or not handle
            or len(handle) > 256
            or type(path) is not str
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
        ):
            raise ValueError(f"{field_name} contains an invalid binding")
        copied[handle] = path
    return copied


def _read_secret_text(path: str) -> str:
    payload = _read_regular_file(path, max_bytes=8192, require_private_mode=True)
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("provider credential file must be UTF-8") from None
    value = value.rstrip("\r\n")
    if not value:
        raise ValueError("provider credential file is empty")
    return value


def _optional_regular_file_digest(path: str, *, max_bytes: int) -> str | None:
    try:
        return _digest_bytes(_read_regular_file(path, max_bytes=max_bytes))
    except Exception:
        return None


def _read_regular_file(
    path: str,
    *,
    max_bytes: int,
    require_private_mode: bool = False,
) -> bytes:
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise ValueError("input file path must be normalized and absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1:
            raise ValueError("input must be a private regular file")
        if require_private_mode and (
            identity.st_uid != os.geteuid() or stat.S_IMODE(identity.st_mode) & 0o077
        ):
            raise ValueError("secret input must be owner-held and private")
        if identity.st_size > max_bytes:
            raise ValueError("input file exceeds its byte limit")
        payload = bytearray()
        while len(payload) < identity.st_size:
            chunk = os.read(
                descriptor, min(identity.st_size - len(payload), 1024 * 1024)
            )
            if not chunk:
                raise ValueError("input file changed while reading")
            payload.extend(chunk)
        if os.read(descriptor, 1):
            raise ValueError("input file changed while reading")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            identity.st_dev,
            identity.st_ino,
            identity.st_size,
            identity.st_mtime_ns,
        ):
            raise ValueError("input file changed while reading")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _atomic_write(path: str, payload: bytes) -> None:
    target = Path(path)
    if not target.is_absolute() or target.name in {"", ".", ".."}:
        raise ValueError("output path must be an absolute file path")
    directory = os.open(
        str(target.parent),
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = f".{target.name}.tmp-{uuid.uuid4().hex}"
    descriptor = -1
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(
            temporary,
            target.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=directory)
        published = True
        os.fsync(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)




def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_digest(value: Any) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "HeadlessProviderInput",
    "HeadlessProviderRouteAuthority",
    "HeadlessRunFailed",
    "HeadlessRunRequest",
    "HeadlessWorkspaceInput",
    "load_headless_request",
    "load_headless_provider_route_authority",
    "run_headless_request",
    "select_pinned_target_projection",
    "run_headless_request_file",
]
