from __future__ import annotations
import asyncio

from builtins import BaseExceptionGroup
from dataclasses import asdict
import ipaddress
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
import re
from pathlib import Path
import stat
from typing import Any, Literal, Mapping
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile

from . import contracts as c
from .composition import ProductionComposition, load_production_composition
from .policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyResolver,
)
from .runner_identity import measure_module_artifact
from .runners.base import freeze_json_object, thaw_json


_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_GIT_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_HEADLESS_MODULE_IDENTITY = measure_module_artifact(__file__)
_POLICY_PROVIDER_PATH = Path(__file__).with_name("policy_provider.py")
_POLICY_PROVIDER_IDENTITY = measure_module_artifact(str(_POLICY_PROVIDER_PATH))


class HeadlessWorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_snapshot_digest: str | None = Field(
        default=None, pattern=_DIGEST_PATTERN
    )
    base_commit: str | None = Field(default=None, pattern=_GIT_COMMIT_PATTERN)
    task_image_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def _repository_identity_is_complete(self) -> HeadlessWorkspaceInput:
        if (self.repository_snapshot_digest is None) != (self.base_commit is None):
            raise ValueError(
                "repository snapshot digest and base commit must be provided together"
            )
        return self


class HeadlessProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    base_url: str
    credential_handle: str = Field(min_length=1, max_length=256)
    context_window: int
    max_output_tokens: int
    timeout_seconds: float = Field(gt=0, le=3_600)
    sampling: Mapping[str, Any] = Field(default_factory=dict)
    caller_headers: Mapping[str, str] = Field(default_factory=dict)
    capabilities: Mapping[str, Any] = Field(default_factory=dict)
    compatibility: Mapping[str, Any] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def _caller_headers_are_exact(self) -> HeadlessProviderInput:
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

    def load_profile(self, credential: str) -> OpenAICompletionsProviderProfile:
        return OpenAICompletionsProviderProfile(
            model=self.model,
            scoped_credential=credential,
            base_url=self.base_url,
            context_window=self.context_window,
            max_output_tokens=self.max_output_tokens,
            sampling=self.sampling,
            caller_headers=self.caller_headers,
            capabilities=self.capabilities,
            compatibility=self.compatibility,
        )

    def identity_dict(self) -> dict[str, Any]:
        header_items = sorted(
            (name.casefold(), value) for name, value in self.caller_headers.items()
        )
        header_names = [name for name, _value in header_items]
        return {
            "model": self.model,
            "base_url_sha256": hashlib.sha256(
                self.base_url.encode("utf-8")
            ).hexdigest(),
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
            "caller_header_count": len(header_names),
            "caller_header_names_sha256": hashlib.sha256(
                _canonical_bytes(header_names)
            ).hexdigest(),
            "caller_headers_sha256": hashlib.sha256(
                _canonical_bytes(header_items)
            ).hexdigest(),
        }


class HeadlessRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["bb.rl.headless-run-request.v1"] = (
        "bb.rl.headless-run-request.v1"
    )
    target_id: str
    target_overlay_id: str
    target_dynamic_fields: Mapping[str, str]
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

    @field_validator(
        "result_path",
        "event_log_path",
    )
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        if not os.path.isabs(value) or os.path.normpath(value) != value:
            raise ValueError("headless paths must be normalized absolute paths")
        return value

    @model_validator(mode="after")
    def _request_is_closed(self) -> HeadlessRunRequest:
        if self.result_path == self.event_log_path:
            raise ValueError("result_path and event_log_path must differ")
        if not self.target_id or not self.target_overlay_id:
            raise ValueError("target and overlay identities are required")
        if any(
            type(name) is not str or not name or type(value) is not str or not value
            for name, value in self.target_dynamic_fields.items()
        ):
            raise ValueError("target_dynamic_fields must contain non-empty text")
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
    ) -> dict[str, Any]:
        return {
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
            "workspace": self.workspace.model_dump(mode="json"),
            "expected_resources": self.expected_resources.model_dump(mode="json"),
            "expected_limits": self.expected_limits.model_dump(mode="json"),
            "tool_allowlist": list(self.tool_allowlist),
            "expected_sandbox": self.expected_sandbox.model_dump(mode="json"),
            "provider_profile": provider_profile.identity_dict(),
            "provider_timeout_seconds": self.provider.timeout_seconds,
        }


class HeadlessRunFailed(RuntimeError):
    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__("headless episode failed")
        self.result = dict(result)


def load_headless_request(path: str) -> HeadlessRunRequest:
    payload = _read_regular_file(path, max_bytes=_MAX_REQUEST_BYTES)
    return HeadlessRunRequest.model_validate_json(payload, strict=True)


async def run_headless_request(
    request: HeadlessRunRequest,
    *,
    composition_ref_path: str,
    secret_files: Mapping[str, str],
    provider_credentials: Mapping[str, str],
    repository_base_commits: Mapping[str, str],
) -> dict[str, Any]:
    if type(request) is not HeadlessRunRequest:
        raise TypeError("request must be an exact HeadlessRunRequest")
    target: E4TargetPolicyProjection | None = None
    profile: OpenAICompletionsProviderProfile | None = None
    composition: ProductionComposition | None = None
    try:
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
        target = E4TargetPolicyProjection.load(
            request.target_id,
            request.target_dynamic_fields,
        )
        if target.overlay_id != request.target_overlay_id:
            raise ValueError(
                "selected target overlay does not match the packaged target"
            )
        if request.tool_allowlist != target.ordered_tool_names:
            raise ValueError(
                "requested tool allowlist does not match the selected target"
            )
        profile = request.provider.load_profile(
            _read_secret_text(provider_secrets[request.provider.credential_handle])
        )
        episode_id = request.resolve_request.episode_id

        def resolver_factory(authority: Any) -> EpisodeOpenAICompletionsPolicyResolver:
            return EpisodeOpenAICompletionsPolicyResolver(
                authority,
                profiles={episode_id: profile},
                target_projections={episode_id: target},
                timeout_seconds={episode_id: request.provider.timeout_seconds},
            )

        composition = load_production_composition(
            composition_ref_path,
            composition_secrets,
            policy_client_resolver_factory=resolver_factory,
        )
        await composition.service.start()
        config_identity = request.identity_dict(
            composition_manifest_ref=composition.manifest_ref,
            target=target,
            provider_profile=profile,
        )
        config_digest = _canonical_digest(config_identity)
        result = _base_result(
            request,
            config_digest=config_digest,
            config_identity=config_identity,
            target=target,
            profile=profile,
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
            target=target,
            profile=profile,
            failure=failure,
        )
        _atomic_write(request.result_path, _canonical_bytes(result))
        if preflight_cancellation is not None:
            raise preflight_cancellation
        raise HeadlessRunFailed(result) from None
    primary_failure: BaseException | None = None
    cleanup_failure: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None
    created = False
    terminal_unsuccessful = False
    event_bytes: bytes | None = None
    try:
        create_operation = await composition.service.create(request.resolve_request)
        create = create_operation.response
        created = True
        result["create"] = {
            "create_fingerprint": create.create_fingerprint,
            "effective_plan_digest": create.effective_plan_digest,
            "effective_plan_ref": _artifact_ref_projection(create.effective_plan_ref),
            "policy_binding_digest": create.policy_binding_digest,
            "policy_observation_digest": create.policy_observation_digest,
        }
        result["sandbox_identity"] = asdict(create.sandbox_preflight)
        effective_plan = _load_effective_plan(composition, create.effective_plan_ref)
        _validate_effective_plan(request, target, effective_plan)
        run_operation = await composition.service.run(
            episode_id,
            create_fingerprint=create.create_fingerprint,
            task_input={"prompt": request.prompt},
            context=request.context,
        )
        run = run_operation.response
        result["terminal"] = {
            "status": run.primary_disposition.value,
            "reason": run.termination,
            "turn_count": run.turn_count,
            "response": None if run.response is None else dict(run.response),
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
        if run.evidence_manifest_ref is not None:
            evidence_projection, event_bytes = _load_evidence_projection(
                composition,
                run.evidence_manifest_ref,
            )
            result["workspace_evidence"] = evidence_projection
        close_operation = await composition.service.close_episode(episode_id)
        closed = await composition.service.get_closed_envelope(episode_id)
        result["cleanup"] = {
            "disposition": close_operation.response.cleanup_disposition.value,
            "receipt_digest": closed.cleanup_receipt_digest,
            "receipt": (
                None
                if closed.cleanup_receipt is None
                else thaw_json(closed.cleanup_receipt)
            ),
            "closed_envelope_digest": closed.digest,
        }
        terminal_unsuccessful = run.primary_disposition.value != "succeeded"
    except asyncio.CancelledError as exc:
        cancellation = exc
        primary_failure = exc
    except BaseException as exc:
        primary_failure = exc
    finally:
        if created:
            try:
                await composition.service.close_episode(episode_id)
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

    result["event_log"] = _event_log_projection(event_bytes, request.event_log_path)
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
    repository_base_commits: Mapping[str, str],
) -> dict[str, Any]:
    return await run_headless_request(
        load_headless_request(path),
        composition_ref_path=composition_ref_path,
        secret_files=secret_files,
        provider_credentials=provider_credentials,
        repository_base_commits=repository_base_commits,
    )


def _validate_repository_base_commit_binding(
    request: HeadlessRunRequest,
    bindings: Mapping[str, str],
) -> None:
    if any(
        type(digest) is not str
        or re.fullmatch(_DIGEST_PATTERN, digest) is None
        or type(commit) is not str
        or re.fullmatch(_GIT_COMMIT_PATTERN, commit) is None
        for digest, commit in bindings.items()
    ):
        raise ValueError("repository base-commit bindings are invalid")
    expected_digest = request.workspace.repository_snapshot_digest
    expected_commit = request.workspace.base_commit
    if expected_digest is None:
        if bindings:
            raise ValueError(
                "repository base-commit bindings require a repository snapshot"
            )
        return
    if set(bindings) != {expected_digest} or bindings[expected_digest] != expected_commit:
        raise ValueError(
            "repository base commit is not bound to the admitted repository snapshot"
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
    if plan.sandbox.image_digest != request.workspace.task_image_digest:
        raise ValueError("effective sandbox image does not match the workspace input")
    if (
        plan.task.repository_snapshot_digest
        != request.workspace.repository_snapshot_digest
    ):
        raise ValueError(
            "effective repository snapshot does not match the workspace input"
        )
    _validate_target_semantics(target, plan.effective_semantics)


def _validate_target_semantics(
    target: E4TargetPolicyProjection,
    semantics: Mapping[str, Any],
) -> None:
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
    ):
        raise ValueError("effective semantics cannot bind the selected E4 target")
    default_model_id = providers.get("default_model_id")
    definitions_by_id = {
        item.get("tool_id"): item for item in definitions if isinstance(item, Mapping)
    }
    variant_by_key = {
        (item.get("config_node_id"), item.get("mode_id"), item.get("model_id")): item
        for item in variants
        if isinstance(item, Mapping)
    }
    if len(definitions_by_id) != len(definitions) or len(variant_by_key) != len(
        variants
    ):
        raise ValueError("effective target semantics contain duplicate identities")
    tool_prompt_mode = prompts.get("tool_prompt_mode")
    for mode in modes:
        if not isinstance(mode, Mapping) or mode.get("enabled") is not True:
            raise ValueError("effective target mode is invalid")
        variant = variant_by_key.get((root_id, mode.get("mode_id"), default_model_id))
        enabled_ids = mode.get("enabled_tool_ids")
        if not isinstance(variant, Mapping) or not isinstance(enabled_ids, tuple):
            raise ValueError("effective target prompt variant is missing")
        system = variant.get("system")
        per_turn = variant.get("per_turn")
        catalog = variant.get("tool_catalog")
        if not all(isinstance(value, Mapping) for value in (system, per_turn, catalog)):
            raise ValueError("effective target prompt variant is malformed")
        system_text = system.get("text")
        per_turn_text = per_turn.get("text")
        catalog_text = catalog.get("text")
        if not all(
            type(value) is str for value in (system_text, per_turn_text, catalog_text)
        ):
            raise ValueError("effective target prompt text is malformed")
        if tool_prompt_mode == "system_once":
            system_text = _join_prompt_parts(system_text, catalog_text)
        elif tool_prompt_mode == "per_turn_append":
            per_turn_text = _join_prompt_parts(per_turn_text, catalog_text)
        else:
            raise ValueError("effective target tool prompt mode is unsupported")
        model_tool_names = tuple(
            definitions_by_id[tool_id].get("model_name")
            for tool_id in enabled_ids
            if tool_id in definitions_by_id
        )
        if (
            system_text != target.system_prompt
            or per_turn_text != ""
            or model_tool_names != target.ordered_tool_names
        ):
            raise ValueError("effective semantics do not match the selected E4 target")


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
    artifact_manifest = json.loads(
        _cas_bytes(
            composition,
            artifact_manifest_ref,
            max_bytes=_MAX_EVIDENCE_BYTES,
        )
    )
    patch_digest = None
    for item in artifact_manifest.get("objects", []):
        if item.get("role") in {"patch", "git_patch", "workspace_patch"}:
            patch_digest = item["artifact_ref"]["sha256"]
            break
    return (
        {
            "materialization_digest": manifest.get("materialization_digest"),
            "final_workspace_snapshot_digest": manifest.get("verifier_snapshot_digest"),
            "patch_digest": patch_digest,
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
    target: E4TargetPolicyProjection | None,
    profile: OpenAICompletionsProviderProfile | None,
    failure: BaseException,
) -> dict[str, Any]:
    dynamic_field_digests = {
        name: _digest_bytes(value.encode("utf-8"))
        for name, value in sorted(request.target_dynamic_fields.items())
    }
    config_identity = {
        "schema_version": "bb.rl.headless-preflight-identity.v1",
        "composition_ref_digest": _optional_regular_file_digest(
            composition_ref_path,
            max_bytes=_MAX_REQUEST_BYTES,
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
        "workspace": request.workspace.model_dump(mode="json"),
        "tool_allowlist": list(request.tool_allowlist),
        "expected_resources": request.expected_resources.model_dump(mode="json"),
        "expected_limits": request.expected_limits.model_dump(mode="json"),
        "expected_sandbox": request.expected_sandbox.model_dump(mode="json"),
        "provider": (
            request.provider.identity_dict()
            if profile is None
            else profile.identity_dict()
        ),
    }
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
    }


def _base_result(
    request: HeadlessRunRequest,
    *,
    config_digest: str,
    config_identity: Mapping[str, Any],
    target: E4TargetPolicyProjection,
    profile: OpenAICompletionsProviderProfile,
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


def _join_prompt_parts(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


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
    "HeadlessRunFailed",
    "HeadlessRunRequest",
    "HeadlessWorkspaceInput",
    "load_headless_request",
    "run_headless_request",
    "run_headless_request_file",
]
