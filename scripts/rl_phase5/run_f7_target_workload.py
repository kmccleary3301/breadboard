from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
import socket
import statistics
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation import contracts as c
from breadboard_engine.compilation.contracts import canonical_json_bytes, canonical_json_loads
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.phase5.f4_campaign import VARIANT_IDS
from scripts.rl_phase5.build_f7_target_launch_packet import (
    F7BaselineObservation,
    F7FinalizerTemplate,
    F7TargetWorkloadPayload,
)
from scripts.rl_phase5.run_f4_target_canaries import (
    F4CleanupObservation,
    F4ExecutionEvidenceManifest,
    F4TargetCanaryInput,
    F4TargetIdentity,
    _artifact_bytes,
    _load_production_runtime_binding,
    _read_evidence_model,
    _require_clean,
    _wire,
    _verify_lifecycle_evidence,
)
from scripts.rl_phase5.run_f7_topology_gate import (
    F7CleanupObservation,
    F7ControlPlaneObservation,
    F7FinalizeSpec,
    F7EpisodeJoin,
    F7ImmutableJSONRef,
    F7LoadLevel,
    F7MixedConfigLatency,
    F7NodeArtifact,
    F7NodeMetrics,
    F7Phase3Manifest,
    F7PinnedIdentity,
    F7SoakTerminalRecord,
    F7TaskLocalSpec,
    F7TopologyEvidenceSpec,
    F7TopologyGateInput,
    run_f7_task_local,
)

_JOIN_WAIT_SECONDS = 900
_GENERIC_SECRET_MARKERS = (
    b"BEGIN PRIVATE KEY",
    b"OPENAI_API_KEY=",
    b"ANTHROPIC_API_KEY=",
    b"AWS_SECRET_ACCESS_KEY=",
    b"GITHUB_TOKEN=",
)


class F7TargetWorkloadError(RuntimeError):
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
        or value.strip() != value
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise ValueError("bounded identifier required")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("absolute normalized path required")
    return value


def _percentile(values: list[float] | tuple[float, ...], percentile: float) -> float:
    if not values:
        raise F7TargetWorkloadError("a measured percentile cannot be computed from no samples")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[index])


def _integer_percentile(values: list[int] | tuple[int, ...], percentile: float) -> int:
    return int(_percentile([float(value) for value in values], percentile))


class F7SlurmAuthority(_ExactModel):
    job_id: str
    task_rank: int = Field(ge=0, le=3)
    node_rank: int = Field(ge=0, le=3)
    local_rank: Literal[0]
    node_count: Literal[2, 4]
    task_count: Literal[2, 4]
    hostname: str
    allocated_hosts: tuple[str, ...]
    nodelist_expression: str

    _ids = field_validator("job_id", "hostname")(_identifier)

    @model_validator(mode="after")
    def exact_one_task_per_node(self) -> "F7SlurmAuthority":
        if self.task_count != self.node_count:
            raise ValueError("F7 requires exactly one Slurm task per node")
        if len(self.allocated_hosts) != self.node_count:
            raise ValueError("Slurm host expansion has the wrong cardinality")
        if len(set(self.allocated_hosts)) != self.node_count:
            raise ValueError("Slurm allocation collapsed onto a duplicate host")
        for host in self.allocated_hosts:
            _identifier(host)
        if self.task_rank != self.node_rank:
            raise ValueError("Slurm task rank and one-task-per-node rank disagree")
        if self.task_rank >= self.node_count:
            raise ValueError("Slurm rank is outside the allocation")
        if self.hostname != self.allocated_hosts[self.node_rank]:
            raise ValueError("observed hostname is not the Slurm node-rank placement")
        return self


class F7LeaseObservation(_ExactModel):
    lease_id: str
    hostname: str
    label: Literal["distributed", "head_local"]
    distributed_execution_claim: bool

    _ids = field_validator("lease_id", "hostname")(_identifier)

    @model_validator(mode="after")
    def no_head_local_overclaim(self) -> "F7LeaseObservation":
        if self.distributed_execution_claim is not (self.label == "distributed"):
            raise ValueError("lease label and distributed execution claim disagree")
        return self


class F7ResourceSample(_ExactModel):
    sample_id: str
    monotonic_ns: int = Field(ge=0)
    rss_bytes: int = Field(gt=0)
    cpu_time_ns: int = Field(ge=0)
    fd_count: int = Field(ge=0)
    queue_depth: int = Field(ge=0)
    cache_entries: int = Field(ge=0)
    active_resource_count: int = Field(ge=0)

    _id = field_validator("sample_id")(_identifier)


class F7MeasuredEpisode(_ExactModel):
    attempt_id: str
    episode_id: str
    config_id: Literal[
        "codex-like",
        "claude-like",
        "pi-like",
        "opencode",
        "oh-my-opencode",
        "unknown-name",
    ]
    task_id: str
    task_digest: str
    model_digest: str
    config_digest: str
    output_digest: str
    evidence_digest: str
    monotonic_start_ns: int = Field(ge=0)
    monotonic_end_ns: int = Field(ge=0)
    selection_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    effective_plan_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    total_latency_ms: float = Field(gt=0, allow_inf_nan=False)
    disposition: Literal["succeeded"]
    fault_injected: Literal[False]
    lease_observations: tuple[F7LeaseObservation, ...]

    _ids = field_validator("attempt_id", "episode_id", "task_id")(_identifier)
    _digests = field_validator(
        "task_digest",
        "model_digest",
        "config_digest",
        "output_digest",
        "evidence_digest",
    )(_digest)

    @model_validator(mode="after")
    def real_elapsed_episode(self) -> "F7MeasuredEpisode":
        elapsed_ns = self.monotonic_end_ns - self.monotonic_start_ns
        if elapsed_ns <= 0:
            raise ValueError("episode has no positive monotonic elapsed time")
        observed_ms = elapsed_ns / 1_000_000
        if not math.isclose(self.total_latency_ms, observed_ms, rel_tol=0, abs_tol=0.001):
            raise ValueError("episode latency is not derived from monotonic observations")
        lease_ids = tuple(row.lease_id for row in self.lease_observations)
        if len(set(lease_ids)) != len(lease_ids):
            raise ValueError("episode contains a duplicate lease join")
        return self

    def gate_join(self) -> F7EpisodeJoin:
        return F7EpisodeJoin(
            episode_id=self.episode_id,
            attempt_id=self.attempt_id,
            task_digest=self.task_digest,
            model_digest=self.model_digest,
            config_digest=self.config_digest,
            output_digest=self.output_digest,
            evidence_digest=self.evidence_digest,
            disposition="succeeded",
        )


class F7LoadObservation(_ExactModel):
    target_sessions: Literal[1, 2, 4, 8, 16, 32]
    monotonic_start_ns: int = Field(ge=0)
    monotonic_end_ns: int = Field(ge=0)
    episodes: tuple[F7MeasuredEpisode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_sessions(self) -> "F7LoadObservation":
        if len(self.episodes) != self.target_sessions:
            raise ValueError("load observation did not execute the exact requested sessions")
        if self.monotonic_end_ns <= self.monotonic_start_ns:
            raise ValueError("load observation has no real elapsed interval")
        if len({row.episode_id for row in self.episodes}) != len(self.episodes):
            raise ValueError("load observation contains a duplicate episode")
        return self


class F7RankOperationArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-rank-operation.v1"]
    packet_id: str
    payload_digest: str
    topology_id: Literal["two-node", "four-node"]
    target_run_id: str
    command_id: str
    slurm: F7SlurmAuthority
    identity: F7PinnedIdentity
    driver_ranks: tuple[int, ...]
    monotonic_start_ns: int = Field(ge=0)
    monotonic_end_ns: int = Field(ge=0)
    measured_start_monotonic_ns: int = Field(ge=0)
    measured_end_monotonic_ns: int = Field(ge=0)
    soak_barrier_digest: str
    warmup_seconds: int = Field(ge=0)
    measured_seconds: int = Field(ge=0)
    sample_interval_seconds: int = Field(ge=1)
    resource_samples: tuple[F7ResourceSample, ...] = Field(min_length=1)
    warmup_episodes: tuple[F7MeasuredEpisode, ...] = Field(min_length=1)
    measured_episodes: tuple[F7MeasuredEpisode, ...] = Field(min_length=1)
    load_observations: tuple[F7LoadObservation, ...]
    cleanup: F7CleanupObservation

    _ids = field_validator("packet_id", "target_run_id", "command_id")(_identifier)
    _digests = field_validator("payload_digest", "soak_barrier_digest")(_digest)

    @model_validator(mode="after")
    def exact_rank_evidence(self) -> "F7RankOperationArtifact":
        if self.monotonic_end_ns <= self.monotonic_start_ns:
            raise ValueError("rank operation has no real elapsed interval")
        expected_ns = (self.warmup_seconds + self.measured_seconds) * 1_000_000_000
        if self.monotonic_end_ns - self.monotonic_start_ns < expected_ns:
            raise ValueError("rank operation is shorter than its claimed soak duration")
        measured_expected_ns = self.measured_seconds * 1_000_000_000
        if (
            self.measured_end_monotonic_ns <= self.measured_start_monotonic_ns
            or self.measured_end_monotonic_ns - self.measured_start_monotonic_ns
            < measured_expected_ns
        ):
            raise ValueError(
                "measured operation window is shorter than its observed claim"
            )
        expected_samples = (self.warmup_seconds + self.measured_seconds) // self.sample_interval_seconds
        if len(self.resource_samples) < expected_samples:
            raise ValueError("rank operation lacks full sample cadence")
        timestamps = tuple(row.monotonic_ns for row in self.resource_samples)
        if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("resource sample monotonic order is invalid")
        minimum_gap = self.sample_interval_seconds * 1_000_000_000
        if any(right - left < minimum_gap for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("resource sample cadence is shorter than declared")
        episodes = self.warmup_episodes + self.measured_episodes
        episode_ids = tuple(row.episode_id for row in episodes)
        attempt_ids = tuple(row.attempt_id for row in episodes)
        evidence_digests = tuple(row.evidence_digest for row in episodes)
        if (
            len(set(episode_ids)) != len(episode_ids)
            or len(set(attempt_ids)) != len(attempt_ids)
            or len(set(evidence_digests)) != len(evidence_digests)
        ):
            raise ValueError(
                "rank operation contains duplicate episode, attempt, or evidence joins"
            )
        expected_drivers = (0,) if self.slurm.task_rank == 0 else ()
        if self.driver_ranks != expected_drivers:
            raise ValueError("control-plane driver placement must be exactly rank zero")
        if self.slurm.task_rank == 0:
            levels = tuple(row.target_sessions for row in self.load_observations)
            if levels != (1, 2, 4, 8, 16, 32):
                raise ValueError("rank-zero load ladder is incomplete or reordered")
        elif self.load_observations:
            raise ValueError("a nonzero rank duplicated the control-plane driver")
        return self


class F7TopologyCompletionReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-topology-completion.v1"]
    packet_id: str
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    requested_target_run_id: str
    target_run_id: str
    command_id: str
    slurm_job_id: str
    allocated_hosts: tuple[str, ...]
    driver_ranks: tuple[Literal[0]]
    identity: F7PinnedIdentity
    task_input_ref: F7ImmutableJSONRef
    control_observation_ref: F7ImmutableJSONRef
    node_metric_refs: tuple[F7ImmutableJSONRef, ...]
    node_artifact_refs: tuple[F7ImmutableJSONRef, ...]
    rank_operation_refs: tuple[F7ImmutableJSONRef, ...]
    predecessor: Literal["none", "two-node:passed"]
    passed: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]

    _ids = field_validator(
        "packet_id",
        "requested_target_run_id",
        "target_run_id",
        "command_id",
        "slurm_job_id",
    )(_identifier)

    @model_validator(mode="after")
    def exact_cardinality(self) -> "F7TopologyCompletionReceipt":
        if len(self.allocated_hosts) != self.node_count or len(set(self.allocated_hosts)) != self.node_count:
            raise ValueError("completion receipt host cardinality is invalid")
        for refs in (self.node_metric_refs, self.node_artifact_refs, self.rank_operation_refs):
            if len(refs) != self.node_count:
                raise ValueError("completion receipt does not contain one ref per rank")
            if len({ref.path for ref in refs}) != self.node_count:
                raise ValueError("completion receipt contains a duplicate rank ref")
        expected = "none" if self.node_count == 2 else "two-node:passed"
        if self.predecessor != expected:
            raise ValueError("completion receipt predecessor is invalid")
        return self


class F7RankResult(_ExactModel):
    rank_operation_ref: F7ImmutableJSONRef
    node_metrics_ref: F7ImmutableJSONRef
    task_input_ref: F7ImmutableJSONRef
    node_artifact_ref: F7ImmutableJSONRef
    control_observation_ref: F7ImmutableJSONRef | None
    completion_receipt_ref: F7ImmutableJSONRef | None
class F7RankReady(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-rank-ready.v1"]
    payload_digest: str
    slurm_job_id: str
    task_rank: int = Field(ge=0, le=3)
    hostname: str
    ready_monotonic_ns: int = Field(ge=0)

    _ids = field_validator("slurm_job_id", "hostname")(_identifier)
    _digest = field_validator("payload_digest")(_digest)


class F7SoakWindow(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-soak-window.v1"]
    payload_digest: str
    slurm_job_id: str
    allocated_hosts: tuple[str, ...]
    ready_ranks: tuple[int, ...]
    released_at_unix_ns: int = Field(gt=0)

    _job = field_validator("slurm_job_id")(_identifier)
    _digest = field_validator("payload_digest")(_digest)






class _BreadBoardEpisodeDriver:
    def __init__(
        self,
        payload: F7TargetWorkloadPayload,
        slurm: F7SlurmAuthority,
        target_run_id: str,
    ) -> None:
        raw = Path(payload.f4_target_input.path).resolve(strict=True).read_bytes()
        if _sha256(raw) != payload.f4_target_input.digest:
            raise F7TargetWorkloadError("F4 target input digest changed after packet authoring")
        if canonical_json_bytes(canonical_json_loads(raw)) != raw:
            raise F7TargetWorkloadError("F4 target input is no longer canonical")
        self._spec = F4TargetCanaryInput.model_validate_json(raw, strict=True)
        self._task_id = self._spec.invariant_identity.task_id
        observed_target = F4TargetIdentity(
            target_run_id=target_run_id,
            target_job_id=slurm.job_id,
            target_node_id=slurm.nodelist_expression,
        )
        self._runtime = _load_production_runtime_binding(self._spec.production, observed_target)
        self._target = observed_target
        self._head_hostname = slurm.allocated_hosts[0]
        self._service = self._runtime.service
        self._inflight = 0
        self._selection_count = 0
        self._closed = False

    async def start(self) -> None:
        await self._service.start()

    async def execute(
        self,
        *,
        config_index: int,
        attempt_id: str,
        episode_id: str,
        identity: F7PinnedIdentity,
        hostname: str,
    ) -> F7MeasuredEpisode:
        variant = self._spec.variants[config_index]
        request_value = variant.request.model_dump(mode="json")
        request_value["episode_id"] = episode_id
        request_value["selection_nonce"] = _sha256(
            canonical_json_bytes(
                {
                    "attempt_id": attempt_id,
                    "config_id": variant.variant_id,
                    "authority_digest": identity.authority_digest,
                }
            )
        )
        request = c.ResolveEpisodeRequest.model_validate_json(
            canonical_json_bytes(request_value), strict=True
        )
        start = time.monotonic_ns()
        self._inflight += 1
        create_operation: Any | None = None
        try:
            create_started = time.monotonic_ns()
            create_operation = await self._service.create(request)
            create_finished = time.monotonic_ns()
            created = create_operation.response
            if _wire(getattr(create_operation, "disposition", None)) != "fresh":
                raise F7TargetWorkloadError("F7 lifecycle create was not fresh")
            selection_raw = _artifact_bytes(
                self._runtime,
                created.selection_record_ref,
                c.ArtifactKind.SELECTION_RECORD,
                "F7 weighted selection",
            )
            selection = c.SelectionRecord.model_validate_json(selection_raw, strict=True)
            if selection.algorithm != "weighted-v1" or selection.episode_id != episode_id:
                raise F7TargetWorkloadError("F7 did not execute the current weighted F4 selection path")
            plan_started = time.monotonic_ns()
            plan_raw = _artifact_bytes(
                self._runtime,
                created.effective_plan_ref,
                c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
                "F7 effective plan",
            )
            plan = c.EffectiveExecutionPlan.model_validate_json(
                plan_raw, strict=True
            )
            plan_finished = time.monotonic_ns()
            run_operation = await self._service.run(
                episode_id,
                create_fingerprint=created.create_fingerprint,
                task_input=canonical_json_loads(canonical_json_bytes(self._spec.task_input)),
                context=canonical_json_loads(canonical_json_bytes(self._spec.run_context)),
            )
            run = run_operation.response
            close_operation = await self._service.close_episode(episode_id)
            closed = close_operation.response
            end = time.monotonic_ns()
            run_wire = _wire(run)
            close_wire = _wire(closed)
            if run_wire.get("primary_disposition") != "succeeded":
                raise F7TargetWorkloadError("F7 BreadBoard episode did not succeed")
            if close_wire.get("cleanup_disposition") != "released":
                raise F7TargetWorkloadError("F7 BreadBoard lifecycle did not release cleanup")
            lifecycle = _verify_lifecycle_evidence(
                self._runtime,
                episode_id,
                _wire(created),
                run_wire,
                close_wire,
                plan,
                selection_digest=selection.canonical_digest(),
                policy_binding_digest=request.policy_binding.canonical_digest(),
                task_input_digest=_sha256(
                    canonical_json_bytes(self._spec.task_input)
                ),
                run_context_digest=_sha256(
                    canonical_json_bytes(self._spec.run_context)
                ),
                target_identity=self._target,
                subject_digest=request.subject.canonical_digest(),
            )
            manifest = _read_evidence_model(
                self._runtime,
                lifecycle.evidence_manifest_ref,
                F4ExecutionEvidenceManifest,
                "F7 execution evidence manifest",
            )
            assert isinstance(manifest, F4ExecutionEvidenceManifest)
            lease_ids = (
                ()
                if manifest.verifier_cleanup_lease_id is None
                else (manifest.verifier_cleanup_lease_id,)
            )
            lease_label = (
                "head_local" if hostname == self._head_hostname else "distributed"
            )
            self._selection_count += 1
            return F7MeasuredEpisode(
                attempt_id=attempt_id,
                episode_id=episode_id,
                config_id=selection.selected_candidate_id,
                task_id=self._task_id,
                task_digest=identity.task_digest,
                model_digest=identity.model_digest,
                config_digest=identity.config_digest,
                output_digest=lifecycle.primary_measurement_digest,
                evidence_digest=lifecycle.closed_envelope_ref.sha256,
                monotonic_start_ns=start,
                monotonic_end_ns=end,
                selection_latency_ms=(create_finished - create_started) / 1_000_000,
                effective_plan_latency_ms=(plan_finished - plan_started) / 1_000_000,
                total_latency_ms=(end - start) / 1_000_000,
                disposition="succeeded",
                fault_injected=False,
                lease_observations=tuple(
                    F7LeaseObservation(
                        lease_id=lease_id,
                        hostname=hostname,
                        label=lease_label,
                        distributed_execution_claim=lease_label == "distributed",
                    )
                    for lease_id in lease_ids
                ),
            )
        except BaseException:
            if create_operation is not None:
                try:
                    await self._service.close_episode(episode_id)
                except BaseException:
                    pass
            raise
        finally:
            self._inflight -= 1

    def resource_sample(self, sample_id: str, monotonic_ns: int) -> F7ResourceSample:
        statm = Path("/proc/self/statm")
        fd_root = Path("/proc/self/fd")
        if not statm.is_file() or not fd_root.is_dir():
            raise F7TargetWorkloadError("production F7 resource observation requires Linux /proc")
        fields = statm.read_text(encoding="ascii").split()
        if len(fields) < 2:
            raise F7TargetWorkloadError("Linux RSS observation is malformed")
        rss = int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
        fd_count = sum(1 for _ in fd_root.iterdir())
        inventory = self._runtime._composition.observe_cleanup_inventory()
        active = sum(
            len(getattr(inventory, name))
            for name in (
                "active_lease_ids",
                "orphan_resource_ids",
                "container_ids",
                "process_ids",
                "cgroup_paths",
                "mount_paths",
                "artifact_paths",
                "secret_lease_ids",
            )
        )
        return F7ResourceSample(
            sample_id=sample_id,
            monotonic_ns=monotonic_ns,
            rss_bytes=rss,
            cpu_time_ns=time.process_time_ns(),
            fd_count=fd_count,
            queue_depth=self._inflight,
            cache_entries=self._selection_count,
            active_resource_count=active,
        )

    async def close(self) -> F7CleanupObservation:
        if self._closed:
            raise F7TargetWorkloadError("BreadBoard F7 driver was closed twice")
        self._closed = True
        primary: BaseException | None = None
        observation: F4CleanupObservation | None = None
        try:
            await self._runtime.close()
            observation = self._runtime.cleanup_observation()
            _require_clean(observation)
        except BaseException as exc:
            primary = exc
        try:
            await self._runtime.close_authority()
        except BaseException as exc:
            if primary is not None:
                raise F7TargetWorkloadError(
                    f"runtime cleanup failed ({primary!r}) and authority cleanup failed ({exc!r})"
                ) from exc
            raise
        if primary is not None:
            raise primary
        if observation is None:
            raise AssertionError("F7 cleanup observation was not captured")
        return F7CleanupObservation(
            active_lease_ids=observation.active_lease_ids,
            orphan_resource_ids=observation.orphan_resource_ids,
            remaining_process_ids=observation.process_ids,
            remaining_container_ids=observation.container_ids,
            cleanup_errors=observation.cleanup_errors,
        )

    def secret_values(self) -> tuple[bytes, ...]:
        values = []
        for path in self._spec.production.secret_files.values():
            raw = Path(path).resolve(strict=True).read_bytes()
            if not raw:
                raise F7TargetWorkloadError("secret authority file is empty")
            values.append(raw)
        return tuple(values)


def _verify_payload_sources(payload: F7TargetWorkloadPayload) -> None:
    root = Path(__file__).resolve().parents[2]
    gate_digests: list[str] = []
    for entry in payload.source_entries:
        source = root / entry.relative_path
        if source.is_symlink() or not source.is_file():
            raise F7TargetWorkloadError(
                f"source closure member is missing or not regular: {entry.relative_path}"
            )
        raw = source.read_bytes()
        if (
            len(raw) != entry.size_bytes
            or _sha256(raw) != entry.digest
            or stat.S_IMODE(source.stat().st_mode) != entry.mode
        ):
            raise F7TargetWorkloadError(
                f"source closure bytes or mode drift: {entry.relative_path}"
            )
        if entry.role == "f7_gate_contract":
            gate_digests.append(entry.digest)
    if gate_digests != [payload.gate_source_digest]:
        raise F7TargetWorkloadError("pinned F7 gate source closure drift")


def derive_slurm_authority(
    payload: F7TargetWorkloadPayload,
    *,
    environment: Mapping[str, str] | None = None,
    observed_hostname: str | None = None,
) -> F7SlurmAuthority:
    env = os.environ if environment is None else environment
    raw = Path(payload.scontrol.path).resolve(strict=True).read_bytes()
    if _sha256(raw) != payload.scontrol.digest:
        raise F7TargetWorkloadError("scontrol authority digest mismatch")
    if not os.access(payload.scontrol.path, os.X_OK):
        raise F7TargetWorkloadError("scontrol authority is not executable")
    nodelist = env.get("SLURM_JOB_NODELIST") or env.get("SLURM_NODELIST")
    if not nodelist:
        raise F7TargetWorkloadError("Slurm node-list authority is missing")
    try:
        expanded = subprocess.run(
            [payload.scontrol.path, "show", "hostnames", nodelist],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        ).stdout.splitlines()
        hosts = tuple(line.strip() for line in expanded if line.strip())
        authority = F7SlurmAuthority(
            job_id=env["SLURM_JOB_ID"],
            task_rank=int(env["SLURM_PROCID"]),
            node_rank=int(env["SLURM_NODEID"]),
            local_rank=int(env["SLURM_LOCALID"]),
            node_count=int(env["SLURM_JOB_NUM_NODES"]),
            task_count=int(env["SLURM_NTASKS"]),
            hostname=socket.gethostname() if observed_hostname is None else observed_hostname,
            allocated_hosts=hosts,
            nodelist_expression=nodelist,
        )
    except (KeyError, ValueError, subprocess.SubprocessError) as exc:
        raise F7TargetWorkloadError("Slurm placement authority is missing or invalid") from exc
    if authority.node_count != payload.topology.node_count:
        raise F7TargetWorkloadError("Slurm allocation does not match the payload topology")
    return authority


def _atomic_write(path: Path, value: BaseModel) -> F7ImmutableJSONRef:
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        if os.write(descriptor, raw) != len(raw):
            raise F7TargetWorkloadError(f"short artifact write: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path)
    except FileExistsError as exc:
        raise F7TargetWorkloadError(f"stale or duplicate F7 artifact already exists: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return F7ImmutableJSONRef(path=str(path.resolve()), digest=_sha256(raw))


def _read_ref(path: Path, model: type[BaseModel]) -> tuple[BaseModel, F7ImmutableJSONRef]:
    raw = path.resolve(strict=True).read_bytes()
    try:
        value = canonical_json_loads(raw)
    except Exception as exc:
        raise F7TargetWorkloadError(f"joined artifact is not JSON: {path}") from exc
    if canonical_json_bytes(value) != raw:
        raise F7TargetWorkloadError(f"joined artifact is not canonical: {path}")
    return model.model_validate_json(raw, strict=True), F7ImmutableJSONRef(
        path=str(path.resolve()), digest=_sha256(raw)
    )


async def _wait_for(
    paths: tuple[Path, ...],
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not all(path.is_file() for path in paths):
        if time.monotonic() >= deadline:
            missing = ", ".join(str(path) for path in paths if not path.is_file())
            raise F7TargetWorkloadError(f"timed out waiting for rank joins: {missing}")
        await asyncio.sleep(0.1)


async def _join_soak_barrier(
    topology_root: Path,
    payload_digest: str,
    slurm: F7SlurmAuthority,
    *,
    timeout_seconds: float,
) -> tuple[F7SoakWindow, str]:
    ready_path = topology_root / f"rank-{slurm.task_rank}-ready.json"
    _atomic_write(
        ready_path,
        F7RankReady(
            schema_version="bb.rl.phase5-f7-rank-ready.v1",
            payload_digest=payload_digest,
            slurm_job_id=slurm.job_id,
            task_rank=slurm.task_rank,
            hostname=slurm.hostname,
            ready_monotonic_ns=time.monotonic_ns(),
        ),
    )
    ready_paths = tuple(
        topology_root / f"rank-{rank}-ready.json" for rank in range(slurm.node_count)
    )
    await _wait_for(ready_paths, timeout_seconds=timeout_seconds)
    ready_rows = []
    for path in ready_paths:
        row, _ = _read_ref(path, F7RankReady)
        assert isinstance(row, F7RankReady)
        ready_rows.append(row)
    if (
        tuple(row.task_rank for row in ready_rows) != tuple(range(slurm.node_count))
        or tuple(row.hostname for row in ready_rows) != slurm.allocated_hosts
        or any(
            row.payload_digest != payload_digest or row.slurm_job_id != slurm.job_id
            for row in ready_rows
        )
    ):
        raise F7TargetWorkloadError("pre-soak rank barrier authority drift")
    window_path = topology_root / "soak-window.json"
    if slurm.task_rank == 0:
        _atomic_write(
            window_path,
            F7SoakWindow(
                schema_version="bb.rl.phase5-f7-soak-window.v1",
                payload_digest=payload_digest,
                slurm_job_id=slurm.job_id,
                allocated_hosts=slurm.allocated_hosts,
                ready_ranks=tuple(range(slurm.node_count)),
                released_at_unix_ns=time.time_ns() + 2_000_000_000,
            ),
        )
    else:
        await _wait_for((window_path,), timeout_seconds=timeout_seconds)
    window_model, window_ref = _read_ref(window_path, F7SoakWindow)
    assert isinstance(window_model, F7SoakWindow)
    if (
        window_model.payload_digest != payload_digest
        or window_model.slurm_job_id != slurm.job_id
        or window_model.allocated_hosts != slurm.allocated_hosts
        or window_model.ready_ranks != tuple(range(slurm.node_count))
    ):
        raise F7TargetWorkloadError("shared soak window authority drift")
    remaining = (window_model.released_at_unix_ns - time.time_ns()) / 1_000_000_000
    if remaining > 0:
        await asyncio.sleep(remaining)
    return window_model, window_ref.digest


def _episode_ids(payload: F7TargetWorkloadPayload, slurm: F7SlurmAuthority, sequence: int) -> tuple[str, str]:
    stem = f"{payload.topology.topology_id}-j{slurm.job_id}-r{slurm.task_rank}-s{sequence}"
    return f"attempt-{stem}", f"episode-{stem}"


async def _execute_one(
    driver: _BreadBoardEpisodeDriver,
    payload: F7TargetWorkloadPayload,
    slurm: F7SlurmAuthority,
    sequence: int,
) -> F7MeasuredEpisode:
    attempt_id, episode_id = _episode_ids(payload, slurm, sequence)
    row = await driver.execute(
        config_index=sequence % len(VARIANT_IDS),
        attempt_id=attempt_id,
        episode_id=episode_id,
        identity=payload.expected_identity,
        hostname=slurm.hostname,
    )
    if (
        row.task_digest != payload.expected_identity.task_digest
        or row.model_digest != payload.expected_identity.model_digest
        or row.config_digest != payload.expected_identity.config_digest
    ):
        raise F7TargetWorkloadError("episode lifecycle identity drift")
    for lease in row.lease_observations:
        if lease.hostname not in slurm.allocated_hosts:
            raise F7TargetWorkloadError("lease observation is outside the Slurm allocation")
        if lease.label == "head_local" and lease.hostname != slurm.allocated_hosts[0]:
            raise F7TargetWorkloadError("head-local lease was attributed to a worker node")
    return row


async def _execute_rank_operation(
    payload: F7TargetWorkloadPayload,
    slurm: F7SlurmAuthority,
    payload_digest: str,
    target_run_id: str,
    driver: _BreadBoardEpisodeDriver,
    topology_root: Path,
    *,
    join_timeout_seconds: float,
) -> F7RankOperationArtifact:
    await driver.start()
    sequence = slurm.task_rank * 1_000_000
    load_rows: list[F7LoadObservation] = []
    warmup: list[F7MeasuredEpisode] = []
    measured: list[F7MeasuredEpisode] = []
    resource_samples: list[F7ResourceSample] = []
    cleanup: F7CleanupObservation | None = None
    primary: BaseException | None = None
    try:
        if slurm.task_rank == payload.driver_rank:
            for level in payload.workload.load_levels:
                start = time.monotonic_ns()
                episodes = await asyncio.gather(
                    *(
                        _execute_one(driver, payload, slurm, sequence + offset)
                        for offset in range(level)
                    )
                )
                sequence += level
                end = time.monotonic_ns()
                load_rows.append(
                    F7LoadObservation(
                        target_sessions=level,
                        monotonic_start_ns=start,
                        monotonic_end_ns=end,
                        episodes=tuple(episodes),
                    )
                )
        _, barrier_digest = await _join_soak_barrier(
            topology_root,
            payload_digest,
            slurm,
            timeout_seconds=join_timeout_seconds,
        )
        soak_start = time.monotonic_ns()
        measured_start = soak_start + (
            payload.workload.soak_warmup_seconds * 1_000_000_000
        )
        interval_ns = payload.workload.sample_interval_seconds * 1_000_000_000
        interval_count = (
            payload.workload.soak_total_seconds
            // payload.workload.sample_interval_seconds
        )
        warmup_intervals = (
            payload.workload.soak_warmup_seconds
            // payload.workload.sample_interval_seconds
        )
        for interval in range(interval_count):
            deadline = soak_start + (interval + 1) * interval_ns
            if interval % slurm.node_count == slurm.task_rank:
                episode = await _execute_one(driver, payload, slurm, sequence)
                sequence += 1
                if interval < warmup_intervals:
                    warmup.append(episode)
                else:
                    measured.append(episode)
            remaining = (deadline - time.monotonic_ns()) / 1_000_000_000
            if remaining > 0:
                await asyncio.sleep(remaining)
            observed = time.monotonic_ns()
            if observed < deadline:
                raise F7TargetWorkloadError(
                    "soak sleeper returned before the sample deadline"
                )
            resource_samples.append(
                driver.resource_sample(
                    f"sample-{payload.topology.topology_id}-r{slurm.task_rank}-{interval}",
                    observed,
                )
            )
        soak_end = time.monotonic_ns()
        measured_end = soak_end
    except BaseException as exc:
        primary = exc
        soak_start = locals().get("soak_start", time.monotonic_ns())
        measured_start = locals().get("measured_start", soak_start)
        soak_end = time.monotonic_ns()
        measured_end = soak_end
        barrier_digest = locals().get("barrier_digest", "sha256:" + "0" * 64)
    try:
        cleanup = await driver.close()
    except BaseException as cleanup_exc:
        if primary is not None:
            raise F7TargetWorkloadError(
                f"rank workload failed ({primary!r}) and cleanup failed ({cleanup_exc!r})"
            ) from cleanup_exc
        raise
    if primary is not None:
        raise primary
    if cleanup is None:
        raise AssertionError("rank cleanup was not observed")
    if not measured:
        raise F7TargetWorkloadError("rank produced no measured current lifecycle joins")
    return F7RankOperationArtifact(
        schema_version="bb.rl.phase5-f7-rank-operation.v1",
        packet_id=payload.packet_id,
        payload_digest=payload_digest,
        topology_id=payload.topology.topology_id,
        target_run_id=target_run_id,
        command_id=payload.topology.command_id,
        slurm=slurm,
        identity=payload.expected_identity,
        driver_ranks=(0,) if slurm.task_rank == 0 else (),
        monotonic_start_ns=soak_start,
        monotonic_end_ns=soak_end,
        measured_start_monotonic_ns=measured_start,
        measured_end_monotonic_ns=measured_end,
        soak_barrier_digest=barrier_digest,
        warmup_seconds=payload.workload.soak_warmup_seconds,
        measured_seconds=payload.workload.soak_measured_seconds,
        sample_interval_seconds=payload.workload.sample_interval_seconds,
        resource_samples=tuple(resource_samples),
        warmup_episodes=tuple(warmup),
        measured_episodes=tuple(measured),
        load_observations=tuple(load_rows),
        cleanup=cleanup,
    )


def _validate_joined_operations(
    payload: F7TargetWorkloadPayload,
    payload_digest: str,
    slurm: F7SlurmAuthority,
    target_run_id: str,
    rows: tuple[F7RankOperationArtifact, ...],
) -> None:
    if len(rows) != slurm.node_count:
        raise F7TargetWorkloadError("rank operation join cardinality is incomplete")
    if tuple(row.slurm.task_rank for row in rows) != tuple(range(slurm.node_count)):
        raise F7TargetWorkloadError("rank operation joins contain a missing or duplicate rank")
    if tuple(row.slurm.hostname for row in rows) != slurm.allocated_hosts:
        raise F7TargetWorkloadError("rank operation host placement differs from Slurm authority")
    if sum(row.driver_ranks == (0,) for row in rows) != 1:
        raise F7TargetWorkloadError("F7 requires exactly one rank-zero control driver")
    if len({row.soak_barrier_digest for row in rows}) != 1:
        raise F7TargetWorkloadError("rank operations did not share one pre-soak barrier")
    shared_start = max(row.measured_start_monotonic_ns for row in rows)
    shared_end = min(row.measured_end_monotonic_ns for row in rows)
    if shared_end - shared_start < payload.workload.soak_measured_seconds * 1_000_000_000:
        raise F7TargetWorkloadError("joined ranks lack the frozen shared measured window")
    for row in rows:
        if (
            row.packet_id != payload.packet_id
            or row.payload_digest != payload_digest
            or row.topology_id != payload.topology.topology_id
            or row.target_run_id != target_run_id
            or row.command_id != payload.topology.command_id
            or row.identity != payload.expected_identity
            or row.slurm.job_id != slurm.job_id
            or row.slurm.allocated_hosts != slurm.allocated_hosts
        ):
            raise F7TargetWorkloadError("stale rank artifact or joined identity drift")
    episodes = tuple(
        episode
        for row in rows
        for episode in row.warmup_episodes + row.measured_episodes
    )
    if len({row.episode_id for row in episodes}) != len(episodes):
        raise F7TargetWorkloadError("joined lifecycle evidence contains a duplicate episode")
    if len({row.attempt_id for row in episodes}) != len(episodes):
        raise F7TargetWorkloadError("joined lifecycle evidence contains a duplicate attempt")
    if len({row.evidence_digest for row in episodes}) != len(episodes):
        raise F7TargetWorkloadError(
            "joined lifecycle evidence contains a duplicate evidence join"
        )
    leases = tuple(
        lease.lease_id
        for episode in episodes
        for lease in episode.lease_observations
    )
    if len(set(leases)) != len(leases):
        raise F7TargetWorkloadError("joined lifecycle evidence contains a duplicate lease")


def _node_metrics(row: F7RankOperationArtifact) -> F7NodeMetrics:
    elapsed = (
        row.measured_end_monotonic_ns - row.measured_start_monotonic_ns
    ) / 1_000_000_000
    samples = row.resource_samples
    episodes = row.measured_episodes
    return F7NodeMetrics(
        schema_version="bb.rl.phase5-f7-node-metrics.v1",
        topology_id=row.topology_id,
        node_count=row.slurm.node_count,
        hostname=row.slurm.hostname,
        task_rank=row.slurm.task_rank,
        identity=row.identity,
        throughput_eps=len(episodes) / elapsed,
        p95_latency_ms=_percentile([episode.total_latency_ms for episode in episodes], 0.95),
        error_count=0,
        failure_count=0,
        rss_start_bytes=samples[0].rss_bytes,
        rss_peak_bytes=max(sample.rss_bytes for sample in samples),
        rss_end_bytes=samples[-1].rss_bytes,
        episode_joins=tuple(episode.gate_join() for episode in episodes),
        lease_ids=tuple(
            lease.lease_id
            for episode in episodes
            for lease in episode.lease_observations
        ),
        cleanup=row.cleanup,
    )


def _load_level(row: F7LoadObservation) -> F7LoadLevel:
    elapsed = (row.monotonic_end_ns - row.monotonic_start_ns) / 1_000_000_000
    return F7LoadLevel(
        target_sessions=row.target_sessions,
        status="passed",
        completed_sessions=len(row.episodes),
        throughput_eps=len(row.episodes) / elapsed,
        p95_latency_ms=_percentile([episode.total_latency_ms for episode in row.episodes], 0.95),
        error_count=0,
        failure_count=0,
    )


def _rss_projection(rows: tuple[F7RankOperationArtifact, ...]) -> tuple[int, int, tuple[int, ...]]:
    sample_count = min(len(row.resource_samples) for row in rows)
    aggregate = [
        max(row.resource_samples[index].rss_bytes for row in rows)
        for index in range(sample_count)
    ]
    if len(aggregate) < 480:
        raise F7TargetWorkloadError("frozen soak RSS coverage is shorter than two hours")
    first = _integer_percentile(aggregate[:120], 0.95)
    final = _integer_percentile(aggregate[-120:], 0.95)
    medians = tuple(int(statistics.median(aggregate[index : index + 20])) for index in range(0, 480, 20))
    return first, final, medians


def _control_observation(
    payload: F7TargetWorkloadPayload,
    slurm: F7SlurmAuthority,
    target_run_id: str,
    rows: tuple[F7RankOperationArtifact, ...],
    metrics: tuple[F7NodeMetrics, ...],
    baseline: F7BaselineObservation,
) -> F7ControlPlaneObservation:
    episodes = tuple(episode for row in rows for episode in row.measured_episodes)
    if len(episodes) < payload.workload.minimum_terminal_attempts:
        raise F7TargetWorkloadError("soak lacks the frozen minimum terminal attempts")
    for config_id in VARIANT_IDS:
        if sum(row.config_id == config_id for row in episodes) < payload.workload.minimum_attempts_per_config:
            raise F7TargetWorkloadError(f"soak lacks the frozen {config_id} quota")
    if sum(row.task_id == "R-SWE-001" for row in episodes) < payload.workload.minimum_r_swe_attempts:
        raise F7TargetWorkloadError("soak lacks the frozen R-SWE-001 quota")
    first_rss, final_rss, medians = _rss_projection(rows)
    sample_ids = tuple(sample.sample_id for sample in rows[0].resource_samples)
    selection = [episode.selection_latency_ms for episode in episodes]
    effective = [episode.effective_plan_latency_ms for episode in episodes]
    cold = [row.warmup_episodes[0].effective_plan_latency_ms for row in rows]
    config_native_throughput = math.fsum(metric.throughput_eps for metric in metrics)
    control_p95 = _percentile([episode.selection_latency_ms for episode in episodes], 0.95)
    identity_failures = sum(
        episode.task_digest != payload.expected_identity.task_digest
        or episode.model_digest != payload.expected_identity.model_digest
        or episode.config_digest != payload.expected_identity.config_digest
        for episode in episodes
    )
    episode_ids = tuple(episode.episode_id for episode in episodes)
    attempt_ids = tuple(episode.attempt_id for episode in episodes)
    evidence_digests = tuple(episode.evidence_digest for episode in episodes)
    integrity_failures = sum(
        (
            len(set(values)) != len(values)
            for values in (episode_ids, attempt_ids, evidence_digests)
        )
    )
    active_leases = tuple(
        lease for row in rows for lease in row.cleanup.active_lease_ids
    )
    orphan_resources = tuple(
        resource for row in rows for resource in row.cleanup.orphan_resource_ids
    )
    remaining_processes = tuple(
        process for row in rows for process in row.cleanup.remaining_process_ids
    )
    remaining_containers = tuple(
        container for row in rows for container in row.cleanup.remaining_container_ids
    )
    cleanup_errors = tuple(
        error for row in rows for error in row.cleanup.cleanup_errors
    )
    cleanup_failures = sum(
        len(values)
        for values in (
            active_leases,
            orphan_resources,
            remaining_processes,
            remaining_containers,
            cleanup_errors,
        )
    )
    return F7ControlPlaneObservation(
        schema_version="bb.rl.phase5-f7-control-observation.v1",
        topology_id=payload.topology.topology_id,
        node_count=payload.topology.node_count,
        target_run_id=target_run_id,
        command_id=payload.topology.command_id,
        slurm_job_id=slurm.job_id,
        head_hostname=slurm.allocated_hosts[0],
        identity=payload.expected_identity,
        cached_selection_p99_ms=_percentile(selection, 0.99),
        effective_plan_resolution_p95_ms=_percentile(effective, 0.95),
        cold_compile_p95_ms=_percentile(cold, 0.95),
        baseline_control_plane_p95_ms=baseline.control_plane_p95_ms,
        config_native_control_plane_p95_ms=control_p95,
        baseline_throughput_eps=baseline.throughput_eps,
        config_native_throughput_eps=config_native_throughput,
        evidence_sample_count=len(sample_ids),
        evidence_exact_join_count=len(sample_ids),
        evidence_sample_ids=sample_ids,
        evidence_join_sample_ids=sample_ids,
        identity_mismatch_count=identity_failures,
        mixed_config_latency=tuple(
            F7MixedConfigLatency(
                config_id=config.config_id,
                p95_latency_ms=_percentile(
                    [row.total_latency_ms for row in episodes if row.config_id == config.config_id],
                    0.95,
                ),
                declared_row_timeout_ms=config.declared_row_timeout_ms,
            )
            for config in payload.authority.configs
        ),
        policy_version_integrity=True,
        queue_backpressure_integrity=all(
            sample.queue_depth <= 32 for row in rows for sample in row.resource_samples
        ),
        load_ladder=tuple(_load_level(row) for row in rows[0].load_observations),
        soak_duration_seconds=payload.workload.soak_total_seconds,
        soak_warmup_seconds=payload.workload.soak_warmup_seconds,
        soak_measured_seconds=payload.workload.soak_measured_seconds,
        soak_sample_interval_seconds=payload.workload.sample_interval_seconds,
        soak_rss_sample_count=len(rows[0].resource_samples),
        soak_terminal_records=tuple(
            F7SoakTerminalRecord(
                attempt_id=row.attempt_id,
                episode_id=row.episode_id,
                config_id=row.config_id,
                task_id=row.task_id,
                fault_injected=False,
                disposition="succeeded",
            )
            for row in episodes
        ),
        first_30m_rss_p95_bytes=first_rss,
        final_30m_rss_p95_bytes=final_rss,
        five_minute_rss_medians_bytes=medians,
        integrity_failure_count=integrity_failures,
        identity_failure_count=identity_failures,
        cleanup_failure_count=cleanup_failures,
        secret_leak_failure_count=0,
        aggregate_throughput_eps=math.fsum(metric.throughput_eps for metric in metrics),
        cleanup=F7CleanupObservation(
            active_lease_ids=active_leases,
            orphan_resource_ids=orphan_resources,
            remaining_process_ids=remaining_processes,
            remaining_container_ids=remaining_containers,
            cleanup_errors=cleanup_errors,
        ),
    )


def _secret_scan(root: Path, secret_values: tuple[bytes, ...]) -> None:
    files = tuple(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise F7TargetWorkloadError("secret scan has no F7 artifacts to inspect")
    for path in files:
        raw = path.read_bytes()
        if any(marker in raw for marker in _GENERIC_SECRET_MARKERS):
            raise F7TargetWorkloadError(f"generic secret marker leaked into {path}")
        if any(secret in raw for secret in secret_values):
            raise F7TargetWorkloadError(f"composition secret leaked into {path}")


def _validate_predecessor(payload: F7TargetWorkloadPayload, campaign_root: Path) -> None:
    relative = payload.predecessor_receipt_relative_path
    if relative is None:
        return
    predecessor, _ = _read_ref(campaign_root / relative, F7TopologyCompletionReceipt)
    assert isinstance(predecessor, F7TopologyCompletionReceipt)
    if (
        predecessor.topology_id != "two-node"
        or predecessor.node_count != 2
        or predecessor.packet_id != payload.packet_id
        or predecessor.identity != payload.expected_identity
        or not predecessor.passed
    ):
        raise F7TargetWorkloadError("four-node workload lacks its exact passing two-node predecessor")


async def _run_f7_target_workload_async(
    payload: F7TargetWorkloadPayload,
    *,
    payload_digest: str,
    campaign_root: str,
    environment: Mapping[str, str],
) -> F7RankResult:
    _verify_payload_sources(payload)
    slurm = derive_slurm_authority(payload, environment=environment)
    target_run_id = environment.get("PHASE3_TARGET_RUN_ID", "")
    command_id = environment.get("PHASE3_COMMAND_ID", "")
    _identifier(target_run_id)
    _identifier(command_id)
    driver = _BreadBoardEpisodeDriver(payload, slurm, target_run_id)
    if command_id != payload.topology.command_id:
        raise F7TargetWorkloadError("Phase3 command identity drift")
    if target_run_id == payload.topology.requested_target_run_id:
        raise F7TargetWorkloadError("Phase3 target run identity was not finalized")
    root = Path(_absolute(campaign_root))
    _validate_predecessor(payload, root)
    topology_root = root / payload.topology.topology_id
    operation_path = topology_root / f"rank-{slurm.task_rank}-operation.json"
    operation = await _execute_rank_operation(
        payload,
        slurm,
        payload_digest,
        target_run_id,
        driver,
        topology_root,
        join_timeout_seconds=_JOIN_WAIT_SECONDS,
    )
    operation_ref = _atomic_write(operation_path, operation)
    operation_paths = tuple(topology_root / f"rank-{rank}-operation.json" for rank in range(slurm.node_count))
    await _wait_for(operation_paths, timeout_seconds=_JOIN_WAIT_SECONDS)
    joined_operations: list[F7RankOperationArtifact] = []
    operation_refs: list[F7ImmutableJSONRef] = []
    for path in operation_paths:
        row, ref = _read_ref(path, F7RankOperationArtifact)
        assert isinstance(row, F7RankOperationArtifact)
        joined_operations.append(row)
        operation_refs.append(ref)
    rows = tuple(joined_operations)
    _validate_joined_operations(payload, payload_digest, slurm, target_run_id, rows)
    metric_paths = tuple(topology_root / f"rank-{rank}-metrics.json" for rank in range(slurm.node_count))
    metrics = tuple(_node_metrics(row) for row in rows)
    metric_path = metric_paths[slurm.task_rank]
    metric_ref = _atomic_write(metric_path, metrics[slurm.task_rank])
    await _wait_for(metric_paths, timeout_seconds=_JOIN_WAIT_SECONDS)
    metric_refs = tuple(_read_ref(path, F7NodeMetrics)[1] for path in metric_paths)
    task_input_path = topology_root / "task-input.json"
    if slurm.task_rank == 0:
        task_input = F7TopologyGateInput(
            schema_version="bb.rl.phase5-f7-topology-gate-input.v1",
            mode="task-local",
            task_local=F7TaskLocalSpec(
                topology_id=payload.topology.topology_id,
                node_count=payload.topology.node_count,
                requested_hosts=slurm.allocated_hosts,
                target_run_id=target_run_id,
                command_id=command_id,
                slurm_job_id_source="SLURM_JOB_ID",
                expected_identity=payload.expected_identity,
                node_metrics_by_rank=metric_refs,
            ),
        )
        task_input_ref = _atomic_write(task_input_path, task_input)
    else:
        await _wait_for((task_input_path,), timeout_seconds=_JOIN_WAIT_SECONDS)
        _, task_input_ref = _read_ref(task_input_path, F7TopologyGateInput)
    task_input_model, observed_task_ref = _read_ref(task_input_path, F7TopologyGateInput)
    assert isinstance(task_input_model, F7TopologyGateInput)
    if observed_task_ref != task_input_ref:
        raise F7TargetWorkloadError("task input changed across the rank barrier")
    node_artifact_path = topology_root / f"rank-{slurm.task_rank}-node-artifact.json"
    run_f7_task_local(
        task_input_model,
        input_digest=task_input_ref.digest,
        output_path=str(node_artifact_path.resolve()),
        environment={
            "SLURM_PROCID": str(slurm.task_rank),
            "SLURM_LOCALID": "0",
            "SLURM_JOB_ID": slurm.job_id,
            "PHASE3_TARGET_RUN_ID": target_run_id,
            "PHASE3_COMMAND_ID": command_id,
        },
        observed_hostname=slurm.hostname,
    )
    _, node_artifact_ref = _read_ref(node_artifact_path, F7NodeArtifact)
    node_paths = tuple(topology_root / f"rank-{rank}-node-artifact.json" for rank in range(slurm.node_count))
    control_ref: F7ImmutableJSONRef | None = None
    completion_ref: F7ImmutableJSONRef | None = None
    if slurm.task_rank == 0:
        await _wait_for(node_paths, timeout_seconds=_JOIN_WAIT_SECONDS)
        node_refs = tuple(
            F7ImmutableJSONRef(path=str(path.resolve()), digest=_sha256(path.read_bytes()))
            for path in node_paths
        )
        baseline_path = Path(payload.baseline_observation.path).resolve(strict=True)
        baseline_raw = baseline_path.read_bytes()
        if _sha256(baseline_raw) != payload.baseline_observation.digest:
            raise F7TargetWorkloadError("baseline observation changed after packet authoring")
        baseline = F7BaselineObservation.model_validate_json(baseline_raw, strict=True)
        if baseline.identity != payload.expected_identity:
            raise F7TargetWorkloadError("baseline observation identity drift")
        _secret_scan(topology_root, driver.secret_values())
        control = _control_observation(payload, slurm, target_run_id, rows, metrics, baseline)
        control_ref = _atomic_write(topology_root / "control-observation.json", control)
        completion = F7TopologyCompletionReceipt(
            schema_version="bb.rl.phase5-f7-topology-completion.v1",
            packet_id=payload.packet_id,
            topology_id=payload.topology.topology_id,
            node_count=payload.topology.node_count,
            requested_target_run_id=target_run_id,
            target_run_id=target_run_id,
            command_id=command_id,
            slurm_job_id=slurm.job_id,
            allocated_hosts=slurm.allocated_hosts,
            driver_ranks=(0,),
            identity=payload.expected_identity,
            task_input_ref=task_input_ref,
            control_observation_ref=control_ref,
            node_metric_refs=metric_refs,
            node_artifact_refs=node_refs,
            rank_operation_refs=tuple(operation_refs),
            predecessor=payload.predecessor,
            passed=True,
            promotion_authority=False,
            scorecard_authority=False,
        )
        completion_ref = _atomic_write(topology_root / "topology-complete.json", completion)
    return F7RankResult(
        rank_operation_ref=operation_ref,
        node_metrics_ref=metric_ref,
        task_input_ref=task_input_ref,
        node_artifact_ref=node_artifact_ref,
        control_observation_ref=control_ref,
        completion_receipt_ref=completion_ref,
    )


def run_f7_target_workload(
    payload: F7TargetWorkloadPayload,
    *,
    payload_digest: str,
    campaign_root: str,
    environment: Mapping[str, str] | None = None,
) -> F7RankResult:
    if type(payload) is not F7TargetWorkloadPayload:
        raise TypeError("exact F7TargetWorkloadPayload required")
    _digest(payload_digest)
    env = os.environ if environment is None else environment
    return asyncio.run(
        _run_f7_target_workload_async(
            payload,
            payload_digest=payload_digest,
            campaign_root=campaign_root,
            environment=env,
        )
    )




def finalize_f7_topology_gate_input(
    *,
    template_path: str,
    campaign_root: str,
    two_node_manifest_path: str,
    four_node_manifest_path: str,
    output_path: str,
) -> F7ImmutableJSONRef:
    template_model, _ = _read_ref(
        Path(_absolute(template_path)), F7FinalizerTemplate
    )
    assert isinstance(template_model, F7FinalizerTemplate)
    gate_source = Path(__file__).with_name("run_f7_topology_gate.py")
    if _sha256(gate_source.read_bytes()) != template_model.gate_source_digest:
        raise F7TargetWorkloadError("pinned F7 topology gate source drift")
    root = Path(_absolute(campaign_root))
    completions: list[F7TopologyCompletionReceipt] = []
    for relative in template_model.completion_receipts:
        completion_model, _ = _read_ref(
            root / relative, F7TopologyCompletionReceipt
        )
        assert isinstance(completion_model, F7TopologyCompletionReceipt)
        completions.append(completion_model)
    two, four = completions
    if (
        template_model.topology_order != ("two-node", "four-node")
        or (two.topology_id, two.node_count, two.predecessor)
        != ("two-node", 2, "none")
        or (four.topology_id, four.node_count, four.predecessor)
        != ("four-node", 4, "two-node:passed")
        or two.packet_id != template_model.packet_id
        or four.packet_id != template_model.packet_id
        or two.identity != template_model.expected_identity
        or four.identity != template_model.expected_identity
    ):
        raise F7TargetWorkloadError(
            "completion receipts do not satisfy the ordered finalizer template"
        )

    def manifest_authority(
        path: str, completion: F7TopologyCompletionReceipt
    ) -> tuple[F7ImmutableJSONRef, str]:
        source = Path(_absolute(path)).resolve(strict=True)
        raw = source.read_bytes()
        try:
            manifest = F7Phase3Manifest.model_validate_json(raw, strict=True)
        except Exception as exc:
            raise F7TargetWorkloadError(
                f"Phase3 manifest is invalid: {source}"
            ) from exc
        commands = tuple(
            row for row in manifest.commands if row.command_id == completion.command_id
        )
        if (
            manifest.target_run_id != completion.target_run_id
            or len(commands) != 1
            or commands[0].target_run_id != completion.target_run_id
            or commands[0].slurm_job_id != completion.slurm_job_id
        ):
            raise F7TargetWorkloadError(
                "Phase3 manifest identity does not match its topology completion"
            )
        command_log = (source.parent / commands[0].raw_log_path).resolve(
            strict=True
        )
        if _sha256(command_log.read_bytes()) != commands[0].raw_log_sha256:
            raise F7TargetWorkloadError("Phase3 raw command log digest mismatch")
        return (
            F7ImmutableJSONRef(path=str(source), digest=_sha256(raw)),
            str(command_log),
        )

    manifest_rows = (
        manifest_authority(two_node_manifest_path, two),
        manifest_authority(four_node_manifest_path, four),
    )
    _secret_scan(root, ())
    _secret_scan(Path(_absolute(two_node_manifest_path)).resolve().parent, ())
    _secret_scan(Path(_absolute(four_node_manifest_path)).resolve().parent, ())
    evidence_rows: list[F7TopologyEvidenceSpec] = []
    for completion, (manifest, command_log) in zip(
        completions, manifest_rows, strict=True
    ):
        final_node_refs: list[F7ImmutableJSONRef] = []
        for rank, preliminary_ref in enumerate(completion.node_artifact_refs):
            preliminary_model, observed_ref = _read_ref(
                Path(preliminary_ref.path), F7NodeArtifact
            )
            assert isinstance(preliminary_model, F7NodeArtifact)
            if observed_ref != preliminary_ref:
                raise F7TargetWorkloadError(
                    "preliminary node artifact changed before finalization"
                )
            final_path = (
                root
                / completion.topology_id
                / f"final-node-{rank}-component-report.json"
            )
            final_model = F7NodeArtifact.model_validate(
                {
                    **preliminary_model.model_dump(mode="python"),
                    "target_run_id": completion.target_run_id,
                    "artifact_paths": {
                        "task_local_artifact": preliminary_model.artifact_paths[
                            "task_local_artifact"
                        ],
                        "component_report_json": str(final_path.resolve()),
                        "command_log": command_log,
                    },
                },
                strict=True,
            )
            final_node_refs.append(_atomic_write(final_path, final_model))
        evidence_rows.append(
            F7TopologyEvidenceSpec(
                topology_id=completion.topology_id,
                node_count=completion.node_count,
                requested_hosts=completion.allocated_hosts,
                requested_target_run_id=completion.requested_target_run_id,
                target_run_id=completion.target_run_id,
                command_id=completion.command_id,
                slurm_job_id=completion.slurm_job_id,
                task_input=completion.task_input_ref,
                phase3_manifest=manifest,
                control_observation=completion.control_observation_ref,
                node_artifacts=tuple(final_node_refs),
            )
        )
    evidence = tuple(evidence_rows)
    final_input = F7TopologyGateInput(
        schema_version="bb.rl.phase5-f7-topology-gate-input.v1",
        mode="finalize",
        finalize=F7FinalizeSpec(
            gate_id=template_model.gate_id,
            expected_identity=template_model.expected_identity,
            topologies=evidence,
        ),
    )
    return _atomic_write(Path(_absolute(output_path)), final_input)


def _read_payload(path: str) -> tuple[F7TargetWorkloadPayload, str]:
    source = Path(_absolute(path)).resolve(strict=True)
    raw = source.read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F7TargetWorkloadError("F7 target workload payload is not canonical JSON")
    return F7TargetWorkloadPayload.model_validate_json(raw, strict=True), _sha256(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute one Slurm rank or finalize the ordered F7 target workload"
    )
    parser.add_argument("--input")
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--finalizer-template")
    parser.add_argument("--two-node-manifest")
    parser.add_argument("--four-node-manifest")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.finalizer_template is not None:
        if args.input is not None or any(
            value is None
            for value in (
                args.two_node_manifest,
                args.four_node_manifest,
                args.output,
            )
        ):
            parser.error(
                "finalizer mode requires --finalizer-template, both manifests, "
                "--campaign-root, and --output, without --input"
            )
        ref = finalize_f7_topology_gate_input(
            template_path=args.finalizer_template,
            campaign_root=args.campaign_root,
            two_node_manifest_path=args.two_node_manifest,
            four_node_manifest_path=args.four_node_manifest,
            output_path=args.output,
        )
        os.write(1, canonical_json_bytes(ref.model_dump(mode="json")) + b"\n")
        return 0
    if args.input is None or any(
        value is not None
        for value in (
            args.two_node_manifest,
            args.four_node_manifest,
            args.output,
        )
    ):
        parser.error(
            "rank mode requires --input and --campaign-root only"
        )
    payload, digest = _read_payload(args.input)
    result = run_f7_target_workload(
        payload,
        payload_digest=digest,
        campaign_root=args.campaign_root,
    )
    os.write(1, canonical_json_bytes(result.model_dump(mode="json")) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "F7EpisodeDriver",
    "F7LeaseObservation",
    "F7LoadObservation",
    "F7MeasuredEpisode",
    "F7RankOperationArtifact",
    "F7RankResult",
    "F7ResourceSample",
    "F7SlurmAuthority",
    "F7TargetWorkloadError",
    "F7TopologyCompletionReceipt",
    "derive_slurm_authority",
    "finalize_f7_topology_gate_input",
    "run_f7_target_workload",
]
