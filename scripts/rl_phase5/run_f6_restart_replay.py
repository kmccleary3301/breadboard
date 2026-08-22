from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import os
import shutil
import socket
import stat
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes, canonical_json_loads
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    CompositionRefV1,
    HarnessCompositionManifestV1,
    load_production_composition,
)
from breadboard.rl.harness.evidence import RecoveredEpisodeV2
from breadboard.rl.harness.runners.base import RunnerAdapterRegistry
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2OperationDisposition,
)
from breadboard.rl.phase5.f4_campaign import ImmutableRef

_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_PERMITTED_NONDETERMINISM = (
    "/episode_id",
    "/episode_binding/episode_id",
    "/episode_binding/selection_record_digest",
    "/episode_binding/selection_record_ref_digest",
    "/episode_binding/selection_commit_binding_digest",
    "/episode_binding/selection_commit_binding_ref_digest",
    "/episode_binding/effective_plan_digest",
    "/episode_binding/effective_plan_ref_digest",
    "/episode_binding/create_fingerprint",
    "/episode_binding/run_fingerprint",
    "/episode_binding/policy_binding_digest",
    "/episode_binding/materialization_plan_digest",
    "/episode_binding/result_ref_digest",
    "/episode_binding/evidence_manifest_ref_digest",
    "/episode_binding/evidence_root",
    "/episode_binding/artifact_manifest_ref_digest",
    "/episode_binding/primary_measurement_digest",
    "/episode_binding/verifier_measurement_digest",
    "/episode_binding/verifier_result_digest",
    "/durable/episode_id",
    "/durable/locator_digest",
    "/durable/locator_completed_tombstone_ref_digest",
    "/durable/locator_closed_tombstone_ref_digest",
    "/durable/completed_tombstone_digest",
    "/durable/completed_tombstone_envelope_ref_digest",
    "/durable/completed_tombstone_response_ref_digest",
    "/durable/closed_tombstone_digest",
    "/durable/closed_tombstone_envelope_ref_digest",
    "/durable/closed_tombstone_response_ref_digest",
    "/durable/closed_tombstone_completed_ref_digest",
    "/durable/completed_envelope_digest",
    "/durable/completed_envelope_ref_digest",
    "/durable/completed_envelope_run_response_ref_digest",
    "/durable/closed_envelope_digest",
    "/durable/closed_envelope_ref_digest",
    "/durable/closed_envelope_completed_ref_digest",
    "/durable/cleanup_receipt_digest",
    "/durable/create_fingerprint",
    "/durable/run_fingerprint",
    "/durable/reconciliation_event_head",
)


class F6RestartReplayError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("lowercase sha256 digest required")
    return value


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or value != value.strip()
        or any(character in "\r\n\x00" for character in value)
    ):
        raise ValueError("bounded normalized identifier required")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("path must be absolute and normalized")
    return value


def _wire(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _wire(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(child) for child in value]
    return value


def _canonical_digest(value: Any) -> str:
    return _sha256(canonical_json_bytes(_wire(value)))


def _without_key(value: BaseModel, key: str) -> dict[str, Any]:
    payload = value.model_dump(mode="json")
    del payload[key]
    return payload


class F6FileIdentity(_ExactModel):
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    size_bytes: int = Field(ge=0)
    mtime_ns: str
    ctime_ns: str
    owner_uid: int = Field(ge=0)
    mode: Literal[256]
    nlink: Literal[1]

    @field_validator("mtime_ns", "ctime_ns")
    @classmethod
    def decimal_nanoseconds(cls, value: str) -> str:
        if (
            type(value) is not str
            or not value.isascii()
            or not value.isdecimal()
        ):
            raise ValueError("decimal nanosecond value required")
        return value



def _same_file_identity(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
        left.st_uid,
        stat.S_IMODE(left.st_mode),
        left.st_nlink,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
        right.st_uid,
        stat.S_IMODE(right.st_mode),
        right.st_nlink,
    )


def _same_directory_authority(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and (
            left.st_dev,
            left.st_ino,
            left.st_uid,
            stat.S_IMODE(left.st_mode),
        )
        == (
            right.st_dev,
            right.st_ino,
            right.st_uid,
            stat.S_IMODE(right.st_mode),
        )
    )


def _matches_file_identity(
    metadata: os.stat_result,
    expected: F6FileIdentity,
) -> bool:
    return (
        metadata.st_dev == expected.device
        and metadata.st_ino == expected.inode
        and metadata.st_size == expected.size_bytes
        and metadata.st_mtime_ns == int(expected.mtime_ns)
        and metadata.st_ctime_ns == int(expected.ctime_ns)
        and metadata.st_uid == expected.owner_uid
        and stat.S_IMODE(metadata.st_mode) == expected.mode
        and metadata.st_nlink == expected.nlink
    )


class F6SecretFileRef(_ExactModel):
    path: str
    sha256: str
    identity: F6FileIdentity

    _path = field_validator("path")(_absolute)
    _sha256 = field_validator("sha256")(_digest)


class F6ProductionBinding(_ExactModel):
    composition_ref_path: str
    composition_descriptor_ref: ImmutableRef
    composition_manifest_ref: ImmutableRef
    authority_bundle_ref: ImmutableRef
    secret_files: dict[str, F6SecretFileRef]

    _path = field_validator("composition_ref_path")(_absolute)

    @field_validator("secret_files")
    @classmethod
    def exact_secret_paths(
        cls, value: dict[str, F6SecretFileRef]
    ) -> dict[str, F6SecretFileRef]:
        if not value or any(type(key) is not str or not key for key in value):
            raise ValueError("composition secret handle map must be nonempty")
        if len({source.path for source in value.values()}) != len(value):
            raise ValueError("composition secret file reuse is forbidden")
        return value


class F6TargetIdentity(_ExactModel):
    target_run_id: str
    slurm_job_id: str
    slurm_nodelist: str
    local_hostname: str

    _ids = field_validator(
        "target_run_id", "slurm_job_id", "slurm_nodelist", "local_hostname"
    )(_identifier)


class F6ImmutableIdentity(_ExactModel):
    selection_algorithm: Literal["direct-v1", "weighted-v1"]
    selected_candidate_id: str
    selector_digest: str
    config_set_digest: str | None
    compiled_manifest_digest: str
    config_bundle_digest: str
    dependency_closure_digest: str
    compiler_identity_digest: str
    base_receipt_digest: str
    final_receipt_digest: str
    runner_adapter_id: str
    runner_runtime_abi: str
    runner_implementation_digest: str
    task_binding_digest: str
    task_contract_digest: str
    repository_snapshot_digest: str
    model_digest: str
    tokenizer_digest: str
    checkpoint_digest: str
    primary_image_digest: str
    verifier_image_digest: str
    verifier_implementation_digest: str

    _ids = field_validator(
        "selected_candidate_id", "runner_adapter_id", "runner_runtime_abi"
    )(_identifier)
    _digests = field_validator(
        "selector_digest",
        "compiled_manifest_digest",
        "config_bundle_digest",
        "dependency_closure_digest",
        "compiler_identity_digest",
        "base_receipt_digest",
        "final_receipt_digest",
        "runner_implementation_digest",
        "task_binding_digest",
        "task_contract_digest",
        "repository_snapshot_digest",
        "model_digest",
        "tokenizer_digest",
        "checkpoint_digest",
        "primary_image_digest",
        "verifier_image_digest",
        "verifier_implementation_digest",
    )(_digest)

    @field_validator("config_set_digest")
    @classmethod
    def optional_digest(cls, value: str | None) -> str | None:
        return None if value is None else _digest(value)

    @model_validator(mode="after")
    def selector_shape(self) -> "F6ImmutableIdentity":
        if (self.selection_algorithm == "weighted-v1") != (self.config_set_digest is not None):
            raise ValueError("selection algorithm and config-set identity disagree")
        if self.config_set_digest is not None and self.config_set_digest != self.selector_digest:
            raise ValueError("weighted config-set identity must equal selector identity")
        return self


class F6RestartReplayInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f6-restart-replay-input.v1"]
    production: F6ProductionBinding
    target: F6TargetIdentity
    immutable_identity: F6ImmutableIdentity
    original_request: c.ResolveEpisodeRequest
    fresh_live_request: c.ResolveEpisodeRequest
    task_input: dict[str, Any]
    run_context: dict[str, Any]
    report_path: str

    _report_path = field_validator("report_path")(_absolute)

    @model_validator(mode="after")
    def one_immutable_input(self) -> "F6RestartReplayInput":
        if self.original_request.episode_id == self.fresh_live_request.episode_id:
            raise ValueError("fresh-live replay requires a distinct episode ID")
        original = self.original_request.model_dump(mode="json")
        fresh = self.fresh_live_request.model_dump(mode="json")
        del original["episode_id"]
        del fresh["episode_id"]
        if original != fresh:
            raise ValueError("original and fresh-live requests differ beyond episode ID")
        if self.original_request.task.canonical_digest() != self.immutable_identity.task_contract_digest:
            raise ValueError("request task contract differs from immutable identity")
        if self.original_request.selector.digest != self.immutable_identity.selector_digest:
            raise ValueError("request selector differs from immutable identity")
        canonical_json_bytes(self.task_input)
        canonical_json_bytes(self.run_context)
        return self


class F6CleanupObservation(_ExactModel):
    active_lease_ids: tuple[str, ...]
    orphan_resource_ids: tuple[str, ...]
    leaked_artifact_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]



class F6DeterministicResult(_ExactModel):
    immutable_identity_digest: str
    selection_episode_neutral_digest: str
    effective_plan_episode_neutral_digest: str
    create_state: Literal["ready"]
    base_receipt_digest: str
    final_receipt_digest: str
    policy_observation_digest: str
    sandbox_preflight_identity_digest: str
    primary_disposition: Literal["succeeded"]
    response_digest: str
    termination: str
    turn_count: int = Field(ge=0)
    reward_and_components_digest: str
    close_state: Literal["closed"]
    cleanup_disposition: Literal["released"]

    _digests = field_validator(
        "immutable_identity_digest",
        "selection_episode_neutral_digest",
        "effective_plan_episode_neutral_digest",
        "base_receipt_digest",
        "final_receipt_digest",
        "policy_observation_digest",
        "sandbox_preflight_identity_digest",
        "response_digest",
        "reward_and_components_digest",
    )(_digest)
    _termination = field_validator("termination")(_identifier)


class F6EpisodeBinding(_ExactModel):
    episode_id: str
    selection_record_digest: str
    selection_record_ref_digest: str
    selection_commit_binding_digest: str
    selection_commit_binding_ref_digest: str
    effective_plan_digest: str
    effective_plan_ref_digest: str
    create_fingerprint: str
    run_fingerprint: str
    policy_binding_digest: str
    materialization_plan_digest: str
    result_ref_digest: str
    evidence_manifest_ref_digest: str
    evidence_root: str
    artifact_manifest_ref_digest: str
    primary_measurement_digest: str
    verifier_measurement_digest: str
    verifier_result_digest: str

    _episode = field_validator("episode_id")(_identifier)
    _digests = field_validator(
        "selection_record_digest",
        "selection_record_ref_digest",
        "selection_commit_binding_digest",
        "selection_commit_binding_ref_digest",
        "effective_plan_digest",
        "effective_plan_ref_digest",
        "create_fingerprint",
        "run_fingerprint",
        "policy_binding_digest",
        "materialization_plan_digest",
        "result_ref_digest",
        "evidence_manifest_ref_digest",
        "evidence_root",
        "artifact_manifest_ref_digest",
        "primary_measurement_digest",
        "verifier_measurement_digest",
        "verifier_result_digest",
    )(_digest)

    @model_validator(mode="after")
    def exact_refs(self) -> "F6EpisodeBinding":
        if self.selection_record_digest != self.selection_record_ref_digest:
            raise ValueError("selection record digest/ref mismatch")
        if self.selection_commit_binding_digest != self.selection_commit_binding_ref_digest:
            raise ValueError("selection commit binding digest/ref mismatch")
        if self.effective_plan_digest != self.effective_plan_ref_digest:
            raise ValueError("effective plan digest/ref mismatch")
        return self


class F6DurableBinding(_ExactModel):
    episode_id: str
    current_state: Literal["closed"]
    quarantined: Literal[False]
    locator_digest: str
    locator_completed_tombstone_ref_digest: str
    locator_closed_tombstone_ref_digest: str
    completed_tombstone_digest: str
    completed_tombstone_envelope_ref_digest: str
    completed_tombstone_response_ref_digest: str
    closed_tombstone_digest: str
    closed_tombstone_envelope_ref_digest: str
    closed_tombstone_response_ref_digest: str
    closed_tombstone_completed_ref_digest: str
    completed_envelope_digest: str
    completed_envelope_ref_digest: str
    completed_envelope_run_response_ref_digest: str
    closed_envelope_digest: str
    closed_envelope_ref_digest: str
    closed_envelope_completed_ref_digest: str
    cleanup_receipt_digest: str
    create_fingerprint: str
    run_fingerprint: str
    reconciliation_event_head: str

    _episode = field_validator("episode_id")(_identifier)
    _digests = field_validator(
        "locator_digest",
        "locator_completed_tombstone_ref_digest",
        "locator_closed_tombstone_ref_digest",
        "completed_tombstone_digest",
        "completed_tombstone_envelope_ref_digest",
        "completed_tombstone_response_ref_digest",
        "closed_tombstone_digest",
        "closed_tombstone_envelope_ref_digest",
        "closed_tombstone_response_ref_digest",
        "closed_tombstone_completed_ref_digest",
        "completed_envelope_digest",
        "completed_envelope_ref_digest",
        "completed_envelope_run_response_ref_digest",
        "closed_envelope_digest",
        "closed_envelope_ref_digest",
        "closed_envelope_completed_ref_digest",
        "cleanup_receipt_digest",
        "create_fingerprint",
        "run_fingerprint",
        "reconciliation_event_head",
    )(_digest)

    @model_validator(mode="after")
    def exact_durable_chain(self) -> "F6DurableBinding":
        if not (
            self.locator_completed_tombstone_ref_digest == self.completed_tombstone_digest
            and self.locator_closed_tombstone_ref_digest == self.closed_tombstone_digest
            and self.completed_tombstone_envelope_ref_digest == self.completed_envelope_digest
            and self.completed_envelope_ref_digest == self.completed_envelope_digest
            and self.closed_tombstone_envelope_ref_digest == self.closed_envelope_digest
            and self.closed_envelope_ref_digest == self.closed_envelope_digest
            and self.closed_tombstone_completed_ref_digest == self.completed_tombstone_digest
            and self.closed_envelope_completed_ref_digest == self.completed_envelope_digest
            and self.closed_tombstone_response_ref_digest == self.completed_tombstone_response_ref_digest
            and self.completed_envelope_run_response_ref_digest
            == self.completed_tombstone_response_ref_digest
        ):
            raise ValueError("durable locator/tombstone/envelope chain mismatch")
        return self


Phase = Literal["original", "cached", "fresh_live"]


class F6LifecycleObservation(_ExactModel):
    phase: Phase
    episode_id: str
    runtime_generation: int = Field(ge=0)
    service_instance_digest: str
    create_disposition: Literal["fresh", "cached"]
    run_disposition: Literal["fresh", "cached"]
    close_disposition: Literal["fresh", "cached"]
    runner_calls_before: int = Field(ge=0)
    runner_calls_after: int = Field(ge=0)
    deterministic: F6DeterministicResult
    episode_binding: F6EpisodeBinding
    durable: F6DurableBinding
    cleanup: F6CleanupObservation

    _episode = field_validator("episode_id")(_identifier)
    _instance = field_validator("service_instance_digest")(_digest)

    @model_validator(mode="after")
    def internally_joined(self) -> "F6LifecycleObservation":
        if self.runner_calls_after < self.runner_calls_before:
            raise ValueError("runner call counter regressed")
        if self.episode_binding.episode_id != self.episode_id or self.durable.episode_id != self.episode_id:
            raise ValueError("observation episode join mismatch")
        if (
            self.episode_binding.create_fingerprint != self.durable.create_fingerprint
            or self.episode_binding.run_fingerprint != self.durable.run_fingerprint
            or self.episode_binding.result_ref_digest
            != self.durable.completed_tombstone_response_ref_digest
        ):
            raise ValueError("operation and durable evidence identity mismatch")
        return self


class F6RestartObservation(_ExactModel):
    previous_generation: Literal[0]
    new_generation: Literal[1]
    previous_service_instance_digest: str
    new_service_instance_digest: str
    durable_authority_digest_before: str
    durable_authority_digest_after: str
    prior_service_closed: Literal[True]
    process_memory_retained: Literal[False]
    recovered_from_durable_state: Literal[True]

    _digests = field_validator(
        "previous_service_instance_digest",
        "new_service_instance_digest",
        "durable_authority_digest_before",
        "durable_authority_digest_after",
    )(_digest)

    @model_validator(mode="after")
    def real_restart(self) -> "F6RestartObservation":
        if self.previous_service_instance_digest == self.new_service_instance_digest:
            raise ValueError("restart retained the prior service instance")
        if self.durable_authority_digest_before != self.durable_authority_digest_after:
            raise ValueError("restart changed durable authority")
        return self


class F6ProductionReportIdentity(_ExactModel):
    composition_descriptor_digest: str
    composition_manifest_digest: str
    authority_bundle_digest: str

    _digests = field_validator(
        "composition_descriptor_digest", "composition_manifest_digest", "authority_bundle_digest"
    )(_digest)


class F6RestartReplayReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f6-restart-replay-report.v1"]
    input_digest: str
    immutable_input_digest: str
    production: F6ProductionReportIdentity
    target: F6TargetIdentity
    immutable_identity: F6ImmutableIdentity
    restart: F6RestartObservation
    permitted_nondeterminism: tuple[str, ...]
    deterministic_fields_equal: Literal[True]
    original: F6LifecycleObservation
    cached: F6LifecycleObservation
    fresh_live: F6LifecycleObservation
    final_cleanup: F6CleanupObservation
    cache_rehydrated_from_durable_state: Literal[True]
    cached_runner_calls: Literal[0]
    fresh_live_runner_calls: Literal[1]
    cleanup_complete: Literal[True]
    no_orphan_resources: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]

    _digests = field_validator("input_digest", "immutable_input_digest")(_digest)

    @model_validator(mode="after")
    def complete_f6_proof(self) -> "F6RestartReplayReport":
        if self.permitted_nondeterminism != _PERMITTED_NONDETERMINISM:
            raise ValueError("permitted nondeterminism list drifted")
        if (self.original.phase, self.cached.phase, self.fresh_live.phase) != (
            "original",
            "cached",
            "fresh_live",
        ):
            raise ValueError("F6 phase order drifted")
        if (
            self.original.deterministic != self.cached.deterministic
            or self.original.deterministic != self.fresh_live.deterministic
        ):
            raise ValueError("deterministic result fields differ")
        cleanup_observations = (
            self.original.cleanup,
            self.cached.cleanup,
            self.fresh_live.cleanup,
            self.final_cleanup,
        )
        cleanup_complete = not any(
            observation.active_lease_ids
            or observation.leaked_artifact_ids
            or observation.cleanup_errors
            for observation in cleanup_observations
        )
        no_orphans = not any(
            observation.orphan_resource_ids
            for observation in cleanup_observations
        )
        if self.cleanup_complete is not cleanup_complete:
            raise ValueError("cleanup completeness is not observation-derived")
        if self.no_orphan_resources is not no_orphans:
            raise ValueError("orphan-resource conclusion is not observation-derived")
        return self


class F6TargetRuntime(Protocol):
    production_identity: F6ProductionReportIdentity
    target_identity: F6TargetIdentity
    final_cleanup: F6CleanupObservation

    async def start(self) -> None: ...

    async def execute_episode(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        task_input: Mapping[str, Any],
        context: Mapping[str, Any],
        phase: Phase,
        immutable_identity: F6ImmutableIdentity,
    ) -> F6LifecycleObservation: ...

    async def restart(self) -> F6RestartObservation: ...

    async def close(self) -> None: ...


class _RunnerCounter:
    def __init__(self) -> None:
        self.run_calls = 0
        self._next_session_id = 0
        self.active_session_ids: set[str] = set()
        self.cleanup_errors: list[str] = []

    def opened(self) -> str:
        self._next_session_id += 1
        session_id = f"runner-session-{self._next_session_id}"
        self.active_session_ids.add(session_id)
        return session_id

    def closed(self, session_id: str) -> None:
        self.active_session_ids.discard(session_id)


class _CountingSession:
    def __init__(
        self,
        session: Any,
        counter: _RunnerCounter,
        session_id: str,
    ) -> None:
        self._session = session
        self._counter = counter
        self._session_id = session_id
        self._closed = False

    async def run(self, request: Any) -> Any:
        self._counter.run_calls += 1
        return await self._session.run(request)

    async def cancel(self, reason: str) -> Any:
        return await self._session.cancel(reason)

    async def close(self) -> Any:
        if self._closed:
            return await self._session.close()
        try:
            return await self._session.close()
        except BaseException as exc:
            self._counter.cleanup_errors.append(
                f"{self._session_id}:{type(exc).__name__}"
            )
            raise
        finally:
            self._closed = True
            self._counter.closed(self._session_id)


class _CountingAdapter:
    def __init__(self, adapter: Any, counter: _RunnerCounter) -> None:
        self._adapter = adapter
        self._counter = counter

    @property
    def descriptor(self) -> Any:
        return self._adapter.descriptor

    async def open(self, request: Any, **kwargs: Any) -> Any:
        session = await self._adapter.open(request, **kwargs)
        return _CountingSession(session, self._counter, self._counter.opened())


class _CountingRegistry:
    def __init__(self, registry: RunnerAdapterRegistry, counter: _RunnerCounter) -> None:
        self._registry = registry
        self._counter = counter

    def resolve(self, adapter_id: str, runtime_abi: str) -> Any:
        return _CountingAdapter(self._registry.resolve(adapter_id, runtime_abi), self._counter)


def _observed_target() -> F6TargetIdentity:
    return F6TargetIdentity(
        target_run_id=os.environ.get("PHASE3_TARGET_RUN_ID", ""),
        slurm_job_id=os.environ.get("SLURM_JOB_ID", ""),
        slurm_nodelist=os.environ.get("SLURM_JOB_NODELIST", os.environ.get("SLURM_NODELIST", "")),
        local_hostname=os.environ.get("SLURMD_NODENAME", socket.gethostname()),
    )


def _read_composition_binding(production: F6ProductionBinding) -> tuple[CompositionRefV1, HarnessCompositionManifestV1]:
    descriptor_raw = Path(production.composition_ref_path).read_bytes()
    try:
        value = canonical_json_loads(descriptor_raw)
    except Exception as exc:
        raise F6RestartReplayError("production composition descriptor is not JSON") from exc
    if canonical_json_bytes(value) != descriptor_raw:
        raise F6RestartReplayError("production composition descriptor is not canonical JSON")
    if _sha256(descriptor_raw) != production.composition_descriptor_ref.digest:
        raise F6RestartReplayError("production composition descriptor digest mismatch")
    descriptor = CompositionRefV1.model_validate_json(descriptor_raw, strict=True)
    if descriptor.manifest_sha256 != production.composition_manifest_ref.digest:
        raise F6RestartReplayError("production composition manifest ref mismatch")
    manifest_raw = Path(descriptor.manifest_path).read_bytes()
    if _sha256(manifest_raw) != descriptor.manifest_sha256:
        raise F6RestartReplayError("production composition manifest digest mismatch")
    manifest = HarnessCompositionManifestV1.model_validate_json(manifest_raw, strict=True)
    if manifest.authority_bundle_ref.sha256 != production.authority_bundle_ref.digest:
        raise F6RestartReplayError("production authority bundle ref mismatch")
    return descriptor, manifest


def _read_immutable_secret(source: F6SecretFileRef) -> tuple[bytes, int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(source.path, flags)
    except OSError as exc:
        raise F6RestartReplayError(
            "immutable secret is unavailable or unsafe"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        payload = bytearray()
        while len(payload) <= 8192:
            chunk = os.read(descriptor, min(8193 - len(payload), 8193))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        current = os.stat(source.path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_size != len(payload)
            or not 1 <= opened.st_size <= 8192
            or not _matches_file_identity(opened, source.identity)
            or not _same_file_identity(opened, after)
            or not _same_file_identity(after, current)
            or _sha256(bytes(payload)) != source.sha256
        ):
            raise F6RestartReplayError("immutable secret authority mismatch")
        return bytes(payload), descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _revalidate_open_secret(
    descriptor: int,
    source: F6SecretFileRef,
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = os.stat(source.path, follow_symlinks=False)
    except OSError as exc:
        raise F6RestartReplayError(
            "immutable secret authority changed during production load"
        ) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not _matches_file_identity(opened, source.identity)
        or not _same_file_identity(opened, current)
    ):
        raise F6RestartReplayError(
            "immutable secret authority changed during production load"
        )


def load_f6_production_composition(production: F6ProductionBinding) -> Any:
    """Load one production composition from exact, content-pinned F6 secrets."""
    temporary_root = tempfile.mkdtemp(prefix="breadboard-f6-secrets-")
    materialized: dict[str, str] = {}
    pinned_sources: list[tuple[int, F6SecretFileRef]] = []
    try:
        for index, (handle_id, source) in enumerate(
            sorted(production.secret_files.items())
        ):
            payload, source_descriptor = _read_immutable_secret(source)
            pinned_sources.append((source_descriptor, source))
            path = os.path.join(temporary_root, f"{index:04d}.secret")
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            try:
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written <= 0:
                        raise OSError("short immutable secret write")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            materialized[handle_id] = path
        composition = load_production_composition(
            production.composition_ref_path,
            materialized,
        )
        for source_descriptor, source in pinned_sources:
            _revalidate_open_secret(source_descriptor, source)
        setattr(
            composition,
            "_f6_secret_authority_pins",
            tuple(pinned_sources),
        )
        pinned_sources = []
        return composition
    finally:
        for source_descriptor, _ in reversed(pinned_sources):
            os.close(source_descriptor)
        shutil.rmtree(temporary_root, ignore_errors=True)


def _collect_artifact_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = _wire(value)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        artifact_id = value.get("artifact_id")
        sha256 = value.get("sha256")
        if type(artifact_id) is str and type(sha256) is str:
            found.add(artifact_id)
        for child in value.values():
            found.update(_collect_artifact_ids(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_collect_artifact_ids(child))
    return found


def _cas_artifact_ids(cas: Any) -> set[str]:
    in_memory = getattr(cas, "_refs_by_id", None)
    if isinstance(in_memory, dict):
        return set(in_memory)
    records_fd = getattr(cas, "_records_fd", None)
    if type(records_fd) is not int:
        raise F6RestartReplayError("evidence CAS has no exact ownership census")
    artifact_ids: set[str] = set()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for name in sorted(os.listdir(records_fd)):
        if not name.endswith(".json") or "/" in name:
            continue
        descriptor = os.open(name, flags, dir_fd=records_fd)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise F6RestartReplayError("evidence CAS record is unsafe")
            raw = bytearray()
            while len(raw) <= 1024 * 1024:
                chunk = os.read(descriptor, min(64 * 1024, 1024 * 1024 + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
            if len(raw) > 1024 * 1024 or not _same_file_identity(opened, after):
                raise F6RestartReplayError("evidence CAS record changed during census")
            record = canonical_json_loads(bytes(raw))
            artifact_id = record.get("artifact_id") if isinstance(record, dict) else None
            if type(artifact_id) is not str or not artifact_id:
                raise F6RestartReplayError("evidence CAS record has no artifact identity")
            artifact_ids.add(artifact_id)
        finally:
            os.close(descriptor)
    return artifact_ids


def _reachable_artifact_ids(repository: Any, episode_ids: set[str]) -> set[str]:
    cas = getattr(repository, "_cas", None)
    if cas is None:
        raise F6RestartReplayError("evidence repository has no owned CAS")
    pending: list[str] = []
    for episode_id in sorted(episode_ids):
        recovered = repository.recover(episode_id)
        if recovered is None:
            raise F6RestartReplayError("evidence repository lost a closed episode")
        pending.extend(sorted(_collect_artifact_ids(recovered)))
    reachable: set[str] = set()
    while pending:
        artifact_id = pending.pop()
        if artifact_id in reachable:
            continue
        reachable.add(artifact_id)
        try:
            ref = cas.get_ref(artifact_id)
            raw = cas.get_bytes(ref, max_bytes=_MAX_ARTIFACT_BYTES)
        except (FileNotFoundError, KeyError):
            continue
        try:
            child = canonical_json_loads(raw)
        except Exception:
            continue
        pending.extend(sorted(_collect_artifact_ids(child) - reachable))
    return reachable


@dataclasses.dataclass(slots=True)
class _GenerationCleanupProbe:
    sandbox: Any
    bridge_lifecycle: Any
    socket_fds: dict[str, int]


class BreadBoardF6Runtime:
    """Family-neutral observation adapter over the production BreadBoard V2 service."""

    def __init__(self, spec: F6RestartReplayInput) -> None:
        _, manifest = _read_composition_binding(spec.production)
        observed_target = _observed_target()
        if observed_target != spec.target:
            raise F6RestartReplayError("observed target run/job/node identity mismatch")
        self._spec = spec
        self._lease_root = manifest.stores.lease.path
        self._counter = _RunnerCounter()
        self._generation = 0
        self._composition: Any = None
        self._service: BreadBoardV2EpisodeService | None = None
        self._generation_probe: _GenerationCleanupProbe | None = None
        self._secret_pins: tuple[tuple[int, F6SecretFileRef], ...] = ()
        self._closed = False
        self._episode_ids: set[str] = set()
        self._artifact_baseline: set[str] | None = None
        self._last_reachable_artifact_ids: set[str] = set()
        self._generation_cleanup_observations: list[F6CleanupObservation] = []
        self._store_probes: list[tuple[str, str, int, os.stat_result]] = []
        self.final_cleanup = F6CleanupObservation(
            active_lease_ids=(),
            orphan_resource_ids=(),
            leaked_artifact_ids=(),
            cleanup_errors=(),
        )
        self.target_identity = observed_target
        self.production_identity = F6ProductionReportIdentity(
            composition_descriptor_digest=spec.production.composition_descriptor_ref.digest,
            composition_manifest_digest=spec.production.composition_manifest_ref.digest,
            authority_bundle_digest=spec.production.authority_bundle_ref.digest,
        )
        self._durable_authority_digest = _canonical_digest(
            self.production_identity.model_dump(mode="json")
        )
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            for name, value in sorted(
                manifest.stores.model_dump(mode="json").items()
            ):
                if not isinstance(value, dict) or type(value.get("path")) is not str:
                    continue
                path = value["path"]
                descriptor = os.open(path, directory_flags)
                opened = os.fstat(descriptor)
                current = os.stat(path, follow_symlinks=False)
                if not _same_directory_authority(opened, current):
                    os.close(descriptor)
                    raise F6RestartReplayError(
                        "production store probe identity mismatch"
                    )
                self._store_probes.append((name, path, descriptor, opened))
            self._open_composition()
        except BaseException:
            for descriptor, _ in reversed(self._secret_pins):
                os.close(descriptor)
            self._secret_pins = ()
            for _, _, descriptor, _ in reversed(self._store_probes):
                os.close(descriptor)
            self._store_probes.clear()
            raise

    def _instance_digest(self) -> str:
        return _canonical_digest(
            {
                "schema_version": "bb.rl.phase5-f6-service-instance.v1",
                "target": self.target_identity.model_dump(mode="json"),
                "production": self.production_identity.model_dump(mode="json"),
                "generation": self._generation,
            }
        )

    def _open_composition(self) -> None:
        composition = load_f6_production_composition(self._spec.production)
        self._secret_pins = tuple(
            getattr(composition, "_f6_secret_authority_pins", ())
        )
        service = composition.app.state.episode_service
        if type(service) is not BreadBoardV2EpisodeService:
            raise F6RestartReplayError(
                "composition did not expose the exact BreadBoard V2 service"
            )
        dependencies = service._dependencies
        service._dependencies = dataclasses.replace(
            dependencies,
            runner_registry=_CountingRegistry(
                dependencies.runner_registry,
                self._counter,
            ),
        )
        self._composition = composition
        self._service = service
        bridge_lifecycle = getattr(composition, "_bridge_lifecycle", None)
        self._generation_probe = _GenerationCleanupProbe(
            sandbox=service._dependencies.sandbox_runtime,
            bridge_lifecycle=bridge_lifecycle,
            socket_fds=(
                {}
                if bridge_lifecycle is None
                else dict(getattr(bridge_lifecycle, "_socket_fds", {}))
            ),
        )
        if self._artifact_baseline is None:
            self._artifact_baseline = _cas_artifact_ids(
                service._dependencies.evidence_repository._cas
            )
        if (
            composition.manifest.input_manifest_digest
            != self.production_identity.composition_manifest_digest
            or composition.manifest.authority_bundle_digest
            != self.production_identity.authority_bundle_digest
        ):
            raise F6RestartReplayError(
                "loaded composition or authority identity drifted"
            )

    @property
    def service(self) -> BreadBoardV2EpisodeService:
        if self._service is None:
            raise F6RestartReplayError("production service is unavailable")
        return self._service

    def _revalidate_secret_pins(self) -> None:
        if len(self._secret_pins) != len(self._spec.production.secret_files):
            raise F6RestartReplayError(
                "production secret pin set is incomplete"
            )
        for descriptor, source in self._secret_pins:
            _revalidate_open_secret(descriptor, source)


    async def start(self) -> None:
        self._revalidate_secret_pins()
        await self.service.start()

    def _load_artifact(self, ref: c.ArtifactRef, kind: c.ArtifactKind) -> bytes:
        try:
            raw = self._composition.authority_graph.store.load(
                ref.sha256,
                kind=kind,
                max_bytes=_MAX_ARTIFACT_BYTES,
            )
        except BaseException as exc:
            raise F6RestartReplayError("required selection/config artifact is missing") from exc
        if type(raw) is not bytes or not raw:
            raise F6RestartReplayError("required selection/config artifact is empty")
        return raw

    def _cleanup(self, recovered: RecoveredEpisodeV2) -> F6CleanupObservation:
        repository = self.service._dependencies.evidence_repository
        cas = repository._cas
        self._episode_ids.add(recovered.locator.episode_id)
        current_artifacts = _cas_artifact_ids(cas)
        reachable = _reachable_artifact_ids(repository, self._episode_ids)
        self._last_reachable_artifact_ids = reachable
        baseline = self._artifact_baseline or set()
        leaked = tuple(sorted(current_artifacts - baseline - reachable))
        sandbox = self.service._dependencies.sandbox_runtime
        sandbox_leases = getattr(sandbox, "_leases", {})
        owned_lease_ids = (
            set(sandbox_leases)
            if isinstance(sandbox_leases, dict)
            else {"sandbox-lease-census-unavailable"}
        )
        lease_files = {
            entry.name
            for entry in os.scandir(self._lease_root)
            if entry.name not in {".", ".."}
        }
        active = tuple(sorted(owned_lease_ids | lease_files))
        orphan_resources = set(self._counter.active_session_ids)
        orphan_resources.update(
            f"sandbox-resource:{lease_id}" for lease_id in owned_lease_ids
        )
        return F6CleanupObservation(
            active_lease_ids=active,
            orphan_resource_ids=tuple(sorted(orphan_resources)),
            leaked_artifact_ids=leaked,
            cleanup_errors=tuple(sorted(self._counter.cleanup_errors)),
        )

    def _probe_store_authorities(self) -> set[str]:
        orphan_resources: set[str] = set()
        for name, path, descriptor, expected in self._store_probes:
            try:
                opened = os.fstat(descriptor)
                current = os.stat(path, follow_symlinks=False)
            except OSError:
                orphan_resources.add(f"store:{name}:unavailable")
                continue
            if (
                not _same_directory_authority(opened, expected)
                or not _same_directory_authority(opened, current)
            ):
                orphan_resources.add(f"store:{name}:identity-drift")
        return orphan_resources

    async def _close_generation(self) -> F6CleanupObservation:
        composition = self._composition
        probe = self._generation_probe
        if composition is None or probe is None:
            raise F6RestartReplayError("production generation cleanup probe is missing")
        repository = self.service._dependencies.evidence_repository
        cas = repository._cas
        before_close_artifacts = _cas_artifact_ids(cas)
        errors: set[str] = set(self._counter.cleanup_errors)
        try:
            await composition.close()
        except BaseException as exc:
            errors.add(f"composition-close:{type(exc).__name__}")
        active_lease_ids: set[str] = set()
        sandbox_leases = getattr(probe.sandbox, "_leases", {})
        if isinstance(sandbox_leases, dict):
            active_lease_ids.update(sandbox_leases)
        else:
            errors.add("sandbox-lease-census-unavailable")
        try:
            active_lease_ids.update(
                entry.name
                for entry in os.scandir(self._lease_root)
                if entry.name not in {".", ".."}
            )
        except OSError as exc:
            errors.add(f"lease-census:{type(exc).__name__}")
        orphan_resources = set(self._counter.active_session_ids)
        orphan_resources.update(
            f"sandbox-resource:{lease_id}" for lease_id in active_lease_ids
        )
        for role, descriptor in sorted(probe.socket_fds.items()):
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            orphan_resources.add(f"socket:{role}")
        bridge = probe.bridge_lifecycle
        if bridge is not None and getattr(bridge, "lease", None) is not None:
            receipt = getattr(bridge, "cleanup_receipt", None)
            if (
                receipt is None
                or receipt.lease_id != bridge.lease.lease_id
                or receipt.id_absent is not True
                or receipt.name_absent is not True
            ):
                orphan_resources.add(f"outer-bridge:{bridge.lease.lease_id}")
        orphan_resources.update(self._probe_store_authorities())
        baseline = self._artifact_baseline or set()
        leaked = before_close_artifacts - baseline - self._last_reachable_artifact_ids
        observation = F6CleanupObservation(
            active_lease_ids=tuple(sorted(active_lease_ids)),
            orphan_resource_ids=tuple(sorted(orphan_resources)),
            leaked_artifact_ids=tuple(sorted(leaked)),
            cleanup_errors=tuple(sorted(errors)),
        )
        self._generation_cleanup_observations.append(observation)
        for descriptor, _ in reversed(self._secret_pins):
            os.close(descriptor)
        self._secret_pins = ()
        self._service = None
        self._composition = None
        self._generation_probe = None
        return observation

    def _recover(self, episode_id: str) -> RecoveredEpisodeV2:
        try:
            recovered = self.service._dependencies.evidence_repository.recover(episode_id)
        except BaseException as exc:
            raise F6RestartReplayError("durable episode state is corrupt or unreadable") from exc
        if recovered is None:
            raise F6RestartReplayError("durable episode state is missing")
        return recovered

    async def restart(self) -> F6RestartObservation:
        if self._generation != 0 or self._composition is None:
            raise F6RestartReplayError("F6 requires exactly one service restart")
        previous_digest = self._instance_digest()
        previous_service = self.service
        before = self._durable_authority_digest
        prior_cleanup = await self._close_generation()
        if (
            prior_cleanup.active_lease_ids
            or prior_cleanup.orphan_resource_ids
            or prior_cleanup.leaked_artifact_ids
            or prior_cleanup.cleanup_errors
        ):
            raise F6RestartReplayError(
                "prior production service generation cleanup is incomplete"
            )
        self._generation = 1
        self._open_composition()
        if self.service is previous_service:
            raise F6RestartReplayError("service restart retained the prior process object")
        self._revalidate_secret_pins()
        await self.service.start()
        return F6RestartObservation(
            previous_generation=0,
            new_generation=1,
            previous_service_instance_digest=previous_digest,
            new_service_instance_digest=self._instance_digest(),
            durable_authority_digest_before=before,
            durable_authority_digest_after=self._durable_authority_digest,
            prior_service_closed=True,
            process_memory_retained=False,
            recovered_from_durable_state=True,
        )

    async def execute_episode(
        self,
        request: c.ResolveEpisodeRequest,
        *,
        task_input: Mapping[str, Any],
        context: Mapping[str, Any],
        phase: Phase,
        immutable_identity: F6ImmutableIdentity,
    ) -> F6LifecycleObservation:
        self._revalidate_secret_pins()
        before = self._counter.run_calls
        create_operation = await self.service.create(request)
        create = create_operation.response
        selection_raw = self._load_artifact(
            create.selection_record_ref, c.ArtifactKind.SELECTION_RECORD
        )
        plan_raw = self._load_artifact(
            create.effective_plan_ref, c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN
        )
        try:
            selection = c.SelectionRecord.model_validate_json(selection_raw, strict=True)
            plan = c.EffectiveExecutionPlan.model_validate_json(plan_raw, strict=True)
        except Exception as exc:
            raise F6RestartReplayError("selection/config artifact is corrupt") from exc
        selection_digest = selection.canonical_digest()
        plan_digest = plan.canonical_digest()
        expected_owner_key = _canonical_digest(
            {
                "schema_version": "bb.rl.selection-owner.v1",
                "subject_digest": request.subject.canonical_digest(),
                "episode_id": request.episode_id,
            }
        )
        expected_request_digest = _canonical_digest(
            {
                "schema_version": "bb.rl.selection-request.v1",
                "episode_id": request.episode_id,
                "subject_digest": request.subject.canonical_digest(),
                "selector_digest": selection.selector_digest,
                "config_set_digest": selection.config_set_digest,
                "selection_nonce": request.selection_nonce,
                "task_contract_digest": request.task.canonical_digest(),
                "policy_capability_observation_digest": (
                    selection.policy_capability_observation_digest
                ),
                "policy_capability_digest": selection.policy_capability_digest,
                "admitted_set_root": selection.admitted_set_root,
                "revocation_state_digest": selection.revocation_state_digest,
                "episode_overlays": _wire(request.episode_overlays),
            }
        )
        if (
            selection_digest != create.selection_record_ref.sha256
            or plan_digest != create.effective_plan_ref.sha256
            or plan_digest != create.effective_plan_digest
            or plan.selection_record_digest != selection_digest
            or plan.subject_digest != selection.subject_digest
            or plan.selector_digest != selection.selector_digest
            or plan.config_set_digest != selection.config_set_digest
            or plan.admitted_set_root != selection.admitted_set_root
            or plan.task_eligibility_digest != selection.task_contract_digest
            or plan.policy_capability_observation_digest
            != selection.policy_capability_observation_digest
            or plan.policy_capability_digest != selection.policy_capability_digest
            or selection.selected_receipt_digest != plan.base_receipt_digest
            or selection.episode_id != request.episode_id
            or selection.subject_digest != request.subject.canonical_digest()
            or selection.selector_digest != request.selector.digest
            or selection.task_contract_digest != request.task.canonical_digest()
            or create.episode_id != request.episode_id
            or create.base_receipt_digest != plan.base_receipt_digest
            or create.final_receipt_digest != plan.final_receipt_digest
            or create.policy_observation_digest
            != plan.policy_capability_observation_digest
            or create.selection_commit.binding.selection_record_digest != selection_digest
            or create.selection_commit.binding.owner_key != expected_owner_key
            or create.selection_commit.binding.request_digest != expected_request_digest
            or create.selection_commit.binding_ref.sha256
            != create.selection_commit.binding.canonical_digest()
        ):
            raise F6RestartReplayError("selection/config/create identity chain mismatch")
        slots = plan.policy_slots
        if len(slots) != 1:
            raise F6RestartReplayError("F6 requires one exact model policy slot")
        slot = slots[0]
        observed_identity = F6ImmutableIdentity(
            selection_algorithm=selection.algorithm,
            selected_candidate_id=selection.selected_candidate_id,
            selector_digest=selection.selector_digest,
            config_set_digest=selection.config_set_digest,
            compiled_manifest_digest=plan.base_compiled.manifest_digest,
            config_bundle_digest=plan.base_compiled.bundle_digest,
            dependency_closure_digest=plan.base_compiled.closure_digest,
            compiler_identity_digest=plan.base_compiled.compiler.canonical_digest(),
            base_receipt_digest=plan.base_receipt_digest,
            final_receipt_digest=plan.final_receipt_digest,
            runner_adapter_id=plan.runner.adapter_id,
            runner_runtime_abi=plan.runner.runtime_abi,
            runner_implementation_digest=plan.runner.implementation_digest,
            task_binding_digest=plan.task.task_binding_digest,
            task_contract_digest=plan.task.task_contract_digest,
            repository_snapshot_digest=plan.task.repository_snapshot_digest or "",
            model_digest=slot.model_digest,
            tokenizer_digest=slot.tokenizer_digest,
            checkpoint_digest=slot.checkpoint_digest,
            primary_image_digest=plan.sandbox.image_digest,
            verifier_image_digest=plan.verifier.image_digest,
            verifier_implementation_digest=plan.verifier.implementation_digest,
        )
        if observed_identity != immutable_identity:
            raise F6RestartReplayError("immutable runtime/config/task/model/image identity drift")
        run_operation = await self.service.run(
            request.episode_id,
            create_fingerprint=create.create_fingerprint,
            task_input=canonical_json_loads(canonical_json_bytes(task_input)),
            context=canonical_json_loads(canonical_json_bytes(context)),
        )
        run = run_operation.response
        close_operation = await self.service.close_episode(request.episode_id)
        closed = close_operation.response
        state = await self.service.get_state(request.episode_id)
        recovered = self._recover(request.episode_id)
        cleanup = self._cleanup(recovered)
        after = self._counter.run_calls
        durable = _durable_binding(recovered)
        if (
            state.state is not EpisodeLifecycleState.CLOSED
            or state.cleanup_disposition is not EpisodeCleanupDisposition.RELEASED
            or run.primary_disposition is not EpisodePrimaryDisposition.SUCCEEDED
            or closed.state is not EpisodeLifecycleState.CLOSED
            or closed.cleanup_disposition is not EpisodeCleanupDisposition.RELEASED
            or run.result_ref is None
            or run.evidence_manifest_ref is None
            or run.evidence_root is None
            or run.artifact_manifest_ref is None
            or run.primary_measurement_digest is None
            or run.verifier_measurement_digest is None
            or run.verifier_result_digest is None
        ):
            raise F6RestartReplayError("successful closed V2 result/evidence contract is incomplete")
        selection_neutral = selection.model_dump(mode="json")
        del selection_neutral["episode_id"]
        plan_neutral = plan.model_dump(mode="json")
        del plan_neutral["selection_record_digest"]
        preflight_identity = _wire(create.sandbox_preflight)
        materialization_plan_digest = preflight_identity.pop("materialization_plan_digest")
        deterministic = F6DeterministicResult(
            immutable_identity_digest=_canonical_digest(immutable_identity.model_dump(mode="json")),
            selection_episode_neutral_digest=_canonical_digest(selection_neutral),
            effective_plan_episode_neutral_digest=_canonical_digest(plan_neutral),
            create_state=create.state.value,
            base_receipt_digest=create.base_receipt_digest,
            final_receipt_digest=create.final_receipt_digest,
            policy_observation_digest=create.policy_observation_digest,
            sandbox_preflight_identity_digest=_canonical_digest(preflight_identity),
            primary_disposition=run.primary_disposition.value,
            response_digest=_canonical_digest(run.response),
            termination=str(run.termination),
            turn_count=run.turn_count,
            reward_and_components_digest=_canonical_digest(
                {"reward": run.reward, "reward_components": run.reward_components}
            ),
            close_state=closed.state.value,
            cleanup_disposition=closed.cleanup_disposition.value,
        )
        binding = F6EpisodeBinding(
            episode_id=request.episode_id,
            selection_record_digest=selection_digest,
            selection_record_ref_digest=create.selection_record_ref.sha256,
            selection_commit_binding_digest=create.selection_commit.binding.canonical_digest(),
            selection_commit_binding_ref_digest=create.selection_commit.binding_ref.sha256,
            effective_plan_digest=plan_digest,
            effective_plan_ref_digest=create.effective_plan_ref.sha256,
            create_fingerprint=create.create_fingerprint,
            run_fingerprint=run.run_fingerprint,
            policy_binding_digest=create.policy_binding_digest,
            materialization_plan_digest=materialization_plan_digest,
            result_ref_digest=run.result_ref.sha256,
            evidence_manifest_ref_digest=run.evidence_manifest_ref.sha256,
            evidence_root=run.evidence_root,
            artifact_manifest_ref_digest=run.artifact_manifest_ref.sha256,
            primary_measurement_digest=run.primary_measurement_digest,
            verifier_measurement_digest=run.verifier_measurement_digest,
            verifier_result_digest=run.verifier_result_digest,
        )
        return F6LifecycleObservation(
            phase=phase,
            episode_id=request.episode_id,
            runtime_generation=self._generation,
            service_instance_digest=self._instance_digest(),
            create_disposition=_wire(create_operation.disposition),
            run_disposition=_wire(run_operation.disposition),
            close_disposition=_wire(close_operation.disposition),
            runner_calls_before=before,
            runner_calls_after=after,
            deterministic=deterministic,
            episode_binding=binding,
            durable=durable,
            cleanup=cleanup,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._composition is not None:
                self.final_cleanup = await self._close_generation()
            elif self._generation_cleanup_observations:
                self.final_cleanup = self._generation_cleanup_observations[-1]
        finally:
            for _, _, descriptor, _ in reversed(self._store_probes):
                os.close(descriptor)
            self._store_probes.clear()


def _durable_binding(recovered: RecoveredEpisodeV2) -> F6DurableBinding:
    locator = recovered.locator
    completed_tombstone = recovered.completed_tombstone
    closed_tombstone = recovered.closed_tombstone
    completed = recovered.completed_envelope
    closed = recovered.closed_envelope
    if (
        recovered.quarantined
        or completed_tombstone is None
        or closed_tombstone is None
        or completed is None
        or closed is None
        or locator.completed_tombstone_ref is None
        or locator.closed_tombstone_ref is None
    ):
        raise F6RestartReplayError("durable closed envelope/tombstone state is missing or quarantined")
    episode_ids = {
        locator.episode_id,
        completed_tombstone.episode_id,
        closed_tombstone.episode_id,
        completed.episode_id,
        closed.episode_id,
    }
    if len(episode_ids) != 1:
        raise F6RestartReplayError("durable state episode identity drift")
    return F6DurableBinding(
        episode_id=locator.episode_id,
        current_state=locator.current_state,
        quarantined=False,
        locator_digest=locator.digest,
        locator_completed_tombstone_ref_digest=locator.completed_tombstone_ref.sha256,
        locator_closed_tombstone_ref_digest=locator.closed_tombstone_ref.sha256,
        completed_tombstone_digest=completed_tombstone.digest,
        completed_tombstone_envelope_ref_digest=completed_tombstone.envelope_ref.sha256,
        completed_tombstone_response_ref_digest=completed_tombstone.response_ref.sha256,
        closed_tombstone_digest=closed_tombstone.digest,
        closed_tombstone_envelope_ref_digest=closed_tombstone.envelope_ref.sha256,
        closed_tombstone_response_ref_digest=closed_tombstone.response_ref.sha256,
        closed_tombstone_completed_ref_digest=closed_tombstone.completed_tombstone_ref.sha256,
        completed_envelope_digest=completed.digest,
        completed_envelope_ref_digest=completed_tombstone.envelope_ref.sha256,
        completed_envelope_run_response_ref_digest=completed.run_response_ref.sha256,
        closed_envelope_digest=closed.digest,
        closed_envelope_ref_digest=closed_tombstone.envelope_ref.sha256,
        closed_envelope_completed_ref_digest=closed.completed_envelope_ref.sha256,
        cleanup_receipt_digest=closed.cleanup_receipt_digest,
        create_fingerprint=completed.create_fingerprint,
        run_fingerprint=completed.run_fingerprint,
        reconciliation_event_head=closed.reconciliation_event_head,
    )


def _immutable_input_digest(spec: F6RestartReplayInput) -> str:
    request = spec.original_request.model_dump(mode="json")
    request["episode_id"] = "<episode-id>"
    return _canonical_digest(
        {
            "schema_version": "bb.rl.phase5-f6-immutable-input.v1",
            "request": request,
            "task_input": spec.task_input,
            "run_context": spec.run_context,
            "immutable_identity": spec.immutable_identity.model_dump(mode="json"),
        }
    )


def _validate_observation(
    observation: F6LifecycleObservation,
    *,
    phase: Phase,
    request: c.ResolveEpisodeRequest,
    generation: int,
    immutable_identity_digest: str,
    expected_runner_calls_before: int,
    expected_runner_calls_after: int,
) -> int:
    if type(observation) is not F6LifecycleObservation:
        raise TypeError("runtime must return exact F6LifecycleObservation values")
    if (
        observation.phase != phase
        or observation.episode_id != request.episode_id
        or observation.runtime_generation != generation
        or observation.deterministic.immutable_identity_digest
        != immutable_identity_digest
    ):
        raise F6RestartReplayError(f"{phase} observation identity drift")
    cleanup = observation.cleanup
    if (
        cleanup.active_lease_ids
        or cleanup.orphan_resource_ids
        or cleanup.leaked_artifact_ids
        or cleanup.cleanup_errors
    ):
        raise F6RestartReplayError(
            f"{phase} cleanup contains a leaked resource"
        )
    expected_dispositions = (
        ("cached", "cached", "cached")
        if phase == "cached"
        else ("fresh", "fresh", "cached")
    )
    observed_dispositions = (
        observation.create_disposition,
        observation.run_disposition,
        observation.close_disposition,
    )
    if observed_dispositions != expected_dispositions:
        raise F6RestartReplayError(
            f"{phase} lifecycle disposition is mislabeled"
        )
    if (
        observation.runner_calls_before != expected_runner_calls_before
        or observation.runner_calls_after != expected_runner_calls_after
    ):
        raise F6RestartReplayError(
            f"{phase} runner counter continuity is not exact"
        )
    return expected_runner_calls_after - expected_runner_calls_before


async def _validate_f6_restart_replay(
    spec: F6RestartReplayInput,
    *,
    input_digest: str,
    runtime: F6TargetRuntime,
) -> F6RestartReplayReport:
    if runtime.production_identity != F6ProductionReportIdentity(
        composition_descriptor_digest=spec.production.composition_descriptor_ref.digest,
        composition_manifest_digest=spec.production.composition_manifest_ref.digest,
        authority_bundle_digest=spec.production.authority_bundle_ref.digest,
    ):
        raise F6RestartReplayError(
            "runtime production composition identity mismatch"
        )
    if runtime.target_identity != spec.target:
        raise F6RestartReplayError(
            "runtime target run/job/node identity mismatch"
        )
    immutable_identity_digest = _canonical_digest(
        spec.immutable_identity.model_dump(mode="json")
    )
    try:
        await runtime.start()
        original = await runtime.execute_episode(
            spec.original_request,
            task_input=spec.task_input,
            context=spec.run_context,
            phase="original",
            immutable_identity=spec.immutable_identity,
        )
        _validate_observation(
            original,
            phase="original",
            request=spec.original_request,
            generation=0,
            immutable_identity_digest=immutable_identity_digest,
            expected_runner_calls_before=0,
            expected_runner_calls_after=1,
        )
        restart = await runtime.restart()
        if type(restart) is not F6RestartObservation:
            raise TypeError(
                "runtime must return an exact F6RestartObservation"
            )
        if (
            restart.previous_service_instance_digest
            != original.service_instance_digest
            or restart.new_service_instance_digest
            == original.service_instance_digest
        ):
            raise F6RestartReplayError(
                "restart service-instance proof does not join original execution"
            )
        cached = await runtime.execute_episode(
            spec.original_request,
            task_input=spec.task_input,
            context=spec.run_context,
            phase="cached",
            immutable_identity=spec.immutable_identity,
        )
        cached_delta = _validate_observation(
            cached,
            phase="cached",
            request=spec.original_request,
            generation=1,
            immutable_identity_digest=immutable_identity_digest,
            expected_runner_calls_before=1,
            expected_runner_calls_after=1,
        )
        if cached.service_instance_digest != restart.new_service_instance_digest:
            raise F6RestartReplayError(
                "cached retrieval did not use the restarted service"
            )
        if (
            cached.episode_binding != original.episode_binding
            or cached.durable != original.durable
        ):
            raise F6RestartReplayError(
                "cached replay differs from original durable result"
            )
        fresh = await runtime.execute_episode(
            spec.fresh_live_request,
            task_input=spec.task_input,
            context=spec.run_context,
            phase="fresh_live",
            immutable_identity=spec.immutable_identity,
        )
        fresh_delta = _validate_observation(
            fresh,
            phase="fresh_live",
            request=spec.fresh_live_request,
            generation=1,
            immutable_identity_digest=immutable_identity_digest,
            expected_runner_calls_before=1,
            expected_runner_calls_after=2,
        )
        if fresh.service_instance_digest != cached.service_instance_digest:
            raise F6RestartReplayError(
                "fresh-live execution left the restarted service"
            )
        if fresh.episode_id == original.episode_id:
            raise F6RestartReplayError(
                "fresh-live execution reused the cached episode ID"
            )
        if (
            original.deterministic != cached.deterministic
            or original.deterministic != fresh.deterministic
        ):
            raise F6RestartReplayError(
                "deterministic result fields differ across replay/live execution"
            )
    finally:
        await runtime.close()
    final_cleanup = runtime.final_cleanup
    cleanup_observations = (
        original.cleanup,
        cached.cleanup,
        fresh.cleanup,
        final_cleanup,
    )
    cleanup_complete = not any(
        observation.active_lease_ids
        or observation.leaked_artifact_ids
        or observation.cleanup_errors
        for observation in cleanup_observations
    )
    no_orphans = not any(
        observation.orphan_resource_ids
        for observation in cleanup_observations
    )
    if not cleanup_complete or not no_orphans:
        raise F6RestartReplayError(
            "final production cleanup census is not empty"
        )
    return F6RestartReplayReport(
        schema_version="bb.rl.phase5-f6-restart-replay-report.v1",
        input_digest=input_digest,
        immutable_input_digest=_immutable_input_digest(spec),
        production=runtime.production_identity,
        target=spec.target,
        immutable_identity=spec.immutable_identity,
        restart=restart,
        permitted_nondeterminism=_PERMITTED_NONDETERMINISM,
        deterministic_fields_equal=(
            original.deterministic
            == cached.deterministic
            == fresh.deterministic
        ),
        original=original,
        cached=cached,
        fresh_live=fresh,
        final_cleanup=final_cleanup,
        cache_rehydrated_from_durable_state=(
            restart.recovered_from_durable_state
        ),
        cached_runner_calls=cached_delta,
        fresh_live_runner_calls=fresh_delta,
        cleanup_complete=cleanup_complete,
        no_orphan_resources=no_orphans,
        promotion_authority=False,
        scorecard_authority=False,
    )


def _publish_report(path: str, report: F6RestartReplayReport) -> None:
    normalized = _absolute(path)
    parent, name = os.path.split(normalized)
    if not name:
        raise F6RestartReplayError("F6 report path has no filename")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_descriptor = os.open(parent, directory_flags)
    descriptor = -1
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o440,
            dir_fd=parent_descriptor,
        )
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short F6 report write")
            view = view[written:]
        os.fsync(descriptor)
        persisted = os.fstat(descriptor)
        if (
            not stat.S_ISREG(persisted.st_mode)
            or persisted.st_uid != os.geteuid()
            or persisted.st_nlink != 1
            or stat.S_IMODE(persisted.st_mode) != 0o440
            or persisted.st_size != len(raw)
        ):
            raise F6RestartReplayError(
                "persisted F6 report identity is unsafe"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        reread = bytearray()
        while len(reread) <= len(raw):
            chunk = os.read(
                descriptor,
                min(64 * 1024, len(raw) + 1 - len(reread)),
            )
            if not chunk:
                break
            reread.extend(chunk)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            bytes(reread) != raw
            or not _same_file_identity(persisted, after)
            or not _same_file_identity(after, current)
        ):
            raise F6RestartReplayError(
                "persisted F6 report readback or identity mismatch"
            )
        os.fsync(parent_descriptor)
        current_after_fsync = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not _same_file_identity(after, current_after_fsync):
            raise F6RestartReplayError(
                "persisted F6 report changed during directory fsync"
            )
    except BaseException:
        if descriptor >= 0:
            opened = os.fstat(descriptor)
            try:
                current = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                current = None
            if (
                current is not None
                and (opened.st_dev, opened.st_ino)
                == (current.st_dev, current.st_ino)
            ):
                os.unlink(name, dir_fd=parent_descriptor)
                try:
                    os.fsync(parent_descriptor)
                except OSError:
                    pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _read_input(
    path: str,
    *,
    expected_sha256: str,
    expected_identity: F6FileIdentity,
) -> tuple[F6RestartReplayInput, str]:
    normalized = _absolute(path)
    expected_sha256 = _digest(expected_sha256)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(normalized, flags)
    except OSError as exc:
        raise F6RestartReplayError(
            "F6 input is unavailable or unsafe"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or not _matches_file_identity(before, expected_identity)
            or before.st_size > _MAX_ARTIFACT_BYTES
        ):
            raise F6RestartReplayError("F6 input file identity mismatch")
        raw = bytearray()
        while len(raw) <= before.st_size:
            chunk = os.read(
                descriptor,
                min(64 * 1024, before.st_size + 1 - len(raw)),
            )
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        current = os.stat(normalized, follow_symlinks=False)
        if (
            len(raw) != before.st_size
            or not _same_file_identity(before, after)
            or not _same_file_identity(after, current)
            or not _matches_file_identity(after, expected_identity)
            or _sha256(bytes(raw)) != expected_sha256
        ):
            raise F6RestartReplayError(
                "F6 input changed or digest/identity handoff mismatched"
            )
    finally:
        os.close(descriptor)
    try:
        value = canonical_json_loads(bytes(raw))
    except Exception as exc:
        raise F6RestartReplayError("F6 input is not JSON") from exc
    if canonical_json_bytes(value) != bytes(raw):
        raise F6RestartReplayError("F6 input is not canonical JSON")
    return (
        F6RestartReplayInput.model_validate_json(bytes(raw), strict=True),
        expected_sha256,
    )


def run_f6_restart_replay(
    input_path: str,
    *,
    expected_input_sha256: str,
    expected_input_identity: F6FileIdentity,
) -> F6RestartReplayReport:
    spec, input_digest = _read_input(
        input_path,
        expected_sha256=expected_input_sha256,
        expected_identity=expected_input_identity,
    )
    runtime = BreadBoardF6Runtime(spec)
    if type(runtime) is not BreadBoardF6Runtime:
        raise TypeError("exact internally constructed BreadBoardF6Runtime required")
    report = asyncio.run(
        _validate_f6_restart_replay(
            spec,
            input_digest=input_digest,
            runtime=runtime,
        )
    )
    _publish_report(spec.report_path, report)
    return report


def _component_report_line(report: F6RestartReplayReport) -> bytes:
    return b"PHASE3_COMPONENT_REPORT_JSON=" + canonical_json_bytes(
        report.model_dump(mode="json")
    ) + b"\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Execute the frozen F6 restart/cache/fresh-live BreadBoard V2 gate"
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--expected-input-device", required=True, type=int)
    parser.add_argument("--expected-input-inode", required=True, type=int)
    parser.add_argument("--expected-input-size-bytes", required=True, type=int)
    parser.add_argument("--expected-input-mtime-ns", required=True)
    parser.add_argument("--expected-input-ctime-ns", required=True)
    parser.add_argument("--expected-input-owner-uid", required=True, type=int)
    parser.add_argument("--expected-input-mode", required=True, type=int)
    parser.add_argument("--expected-input-nlink", required=True, type=int)
    args = parser.parse_args()
    identity = F6FileIdentity(
        device=args.expected_input_device,
        inode=args.expected_input_inode,
        size_bytes=args.expected_input_size_bytes,
        mtime_ns=args.expected_input_mtime_ns,
        ctime_ns=args.expected_input_ctime_ns,
        owner_uid=args.expected_input_owner_uid,
        mode=args.expected_input_mode,
        nlink=args.expected_input_nlink,
    )
    report = run_f6_restart_replay(
        args.input,
        expected_input_sha256=args.expected_input_sha256,
        expected_input_identity=identity,
    )
    os.write(1, _component_report_line(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BreadBoardF6Runtime",
    "F6CleanupObservation",
    "F6FileIdentity",
    "F6DeterministicResult",
    "F6DurableBinding",
    "F6EpisodeBinding",
    "F6ImmutableIdentity",
    "F6LifecycleObservation",
    "F6ProductionBinding",
    "F6ProductionReportIdentity",
    "F6RestartObservation",
    "F6RestartReplayError",
    "F6RestartReplayInput",
    "F6RestartReplayReport",
    "F6SecretFileRef",
    "F6TargetIdentity",
    "run_f6_restart_replay",
    "load_f6_production_composition",
]
