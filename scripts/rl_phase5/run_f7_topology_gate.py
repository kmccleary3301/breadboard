from __future__ import annotations

import argparse
import hashlib
from decimal import Decimal
import math
import os
import socket
import sys
from pathlib import Path
from typing import Any, Literal, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes, canonical_json_loads
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MIN_SOAK_SECONDS = 7_200
SOAK_WARMUP_SECONDS = 900
SOAK_MEASURED_SECONDS = 6_300
SOAK_SAMPLE_INTERVAL_SECONDS = 15
MIN_SOAK_ATTEMPTS = 256
MIN_CONFIG_ATTEMPTS = 32
MIN_SWE_ATTEMPTS = 64
MIN_NON_FAULT_COMPLETION_RATE = 0.995
MAX_CACHED_SELECTION_P99_MS = 2.0
MAX_EFFECTIVE_PLAN_P95_MS = 10.0
MAX_COLD_COMPILE_P95_MS = 500.0
MAX_CONTROL_PLANE_OVERHEAD_RATIO = 0.10
MAX_THROUGHPUT_REGRESSION_RATIO = 0.10
MAX_FINAL_TO_FIRST_RSS_RATIO = 1.05
REQUIRED_LOAD_LEVELS = (1, 2, 4, 8, 16, 32)
F4_CONFIG_IDS = (
    "codex-like",
    "claude-like",
    "pi-like",
    "opencode",
    "oh-my-opencode",
    "unknown-name",
)
_REPORT_COMPONENT = "f7_topology_gate"


class F7TopologyGateError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("lowercase sha256 digest required")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("absolute normalized path required")
    return value


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise ValueError("bounded canonical identifier required")
    return value


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class F7ImmutableJSONRef(_ExactModel):
    path: str
    digest: str
    media_type: Literal["application/json"] = "application/json"

    _path = field_validator("path")(_absolute)
    _sha = field_validator("digest")(_digest)


class F7PinnedIdentity(_ExactModel):
    runtime_digest: str
    config_digest: str
    task_digest: str
    model_digest: str
    tokenizer_digest: str
    checkpoint_digest: str
    image_digest: str
    verifier_digest: str
    authority_digest: str

    _digests = field_validator(
        "runtime_digest",
        "config_digest",
        "task_digest",
        "model_digest",
        "tokenizer_digest",
        "checkpoint_digest",
        "image_digest",
        "verifier_digest",
        "authority_digest",
    )(_digest)


class F7EpisodeJoin(_ExactModel):
    episode_id: str
    attempt_id: str
    task_digest: str
    model_digest: str
    config_digest: str
    output_digest: str
    evidence_digest: str
    disposition: Literal["succeeded"]

    _ids = field_validator("episode_id", "attempt_id")(_identifier)
    _digests = field_validator(
        "task_digest",
        "model_digest",
        "config_digest",
        "output_digest",
        "evidence_digest",
    )(_digest)


class F7CleanupObservation(_ExactModel):
    active_lease_ids: tuple[str, ...]
    orphan_resource_ids: tuple[str, ...]
    remaining_process_ids: tuple[str, ...]
    remaining_container_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]

    @model_validator(mode="after")
    def no_orphans(self) -> "F7CleanupObservation":
        if (
            self.active_lease_ids
            or self.orphan_resource_ids
            or self.remaining_process_ids
            or self.remaining_container_ids
            or self.cleanup_errors
        ):
            raise ValueError("cleanup proof contains an active lease, orphan, process, container, or error")
        return self


class F7NodeMetrics(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-node-metrics.v1"]
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    hostname: str
    task_rank: int = Field(ge=0, le=3)
    identity: F7PinnedIdentity
    throughput_eps: float = Field(gt=0, allow_inf_nan=False)
    p95_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    error_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    rss_start_bytes: int = Field(ge=0)
    rss_peak_bytes: int = Field(ge=0)
    rss_end_bytes: int = Field(ge=0)
    episode_joins: tuple[F7EpisodeJoin, ...] = Field(min_length=1)
    lease_ids: tuple[str, ...]
    cleanup: F7CleanupObservation

    _host = field_validator("hostname")(_identifier)

    @model_validator(mode="after")
    def valid_node_metrics(self) -> "F7NodeMetrics":
        expected_id = "two-node" if self.node_count == 2 else "four-node"
        if self.topology_id != expected_id:
            raise ValueError("topology ID does not match node count")
        if self.task_rank >= self.node_count:
            raise ValueError("task rank is outside the requested topology")
        if self.error_count != 0 or self.failure_count != 0:
            raise ValueError("F7 permits no node errors or failures")
        if self.rss_peak_bytes < max(self.rss_start_bytes, self.rss_end_bytes):
            raise ValueError("peak RSS is lower than an endpoint RSS observation")
        if len(set(self.lease_ids)) != len(self.lease_ids):
            raise ValueError("node lease observations contain a duplicate")
        episode_ids = tuple(item.episode_id for item in self.episode_joins)
        attempt_ids = tuple(item.attempt_id for item in self.episode_joins)
        if len(set(episode_ids)) != len(episode_ids) or len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("node episode joins contain a duplicate episode or attempt")
        for join in self.episode_joins:
            if (
                join.task_digest != self.identity.task_digest
                or join.model_digest != self.identity.model_digest
                or join.config_digest != self.identity.config_digest
            ):
                raise ValueError("episode join identity drift")
        return self


class F7TaskLocalSpec(_ExactModel):
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    requested_hosts: tuple[str, ...]
    target_run_id: str
    command_id: str
    slurm_job_id_source: Literal["SLURM_JOB_ID"]
    expected_identity: F7PinnedIdentity
    node_metrics_by_rank: tuple[F7ImmutableJSONRef, ...]

    _ids = field_validator("target_run_id", "command_id")(_identifier)

    @model_validator(mode="after")
    def complete_task_map(self) -> "F7TaskLocalSpec":
        expected_id = "two-node" if self.node_count == 2 else "four-node"
        if self.topology_id != expected_id:
            raise ValueError("topology ID does not match node count")
        if len(self.requested_hosts) != self.node_count:
            raise ValueError("requested host list is incomplete")
        if len(set(self.requested_hosts)) != self.node_count:
            raise ValueError("requested host list contains a duplicate")
        for host in self.requested_hosts:
            _identifier(host)
        if len(self.node_metrics_by_rank) != self.node_count:
            raise ValueError("one immutable node metrics ref per task is required")
        if len({item.path for item in self.node_metrics_by_rank}) != self.node_count:
            raise ValueError("node metric paths must be unique")
        if len({item.digest for item in self.node_metrics_by_rank}) != self.node_count:
            raise ValueError("node metric digests must be unique")
        return self


class F7LoadLevel(_ExactModel):
    target_sessions: Literal[1, 2, 4, 8, 16, 32]
    status: Literal["passed"]
    completed_sessions: int = Field(ge=1)
    throughput_eps: float = Field(gt=0, allow_inf_nan=False)
    p95_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    error_count: Literal[0]
    failure_count: Literal[0]


class F7MixedConfigLatency(_ExactModel):
    config_id: Literal[
        "codex-like",
        "claude-like",
        "pi-like",
        "opencode",
        "oh-my-opencode",
        "unknown-name",
    ]
    p95_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    declared_row_timeout_ms: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def below_declared_timeout(self) -> "F7MixedConfigLatency":
        if self.p95_latency_ms >= self.declared_row_timeout_ms:
            raise ValueError("mixed-config p95 must be below its declared row timeout")
        return self


class F7SoakTerminalRecord(_ExactModel):
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
    fault_injected: bool
    disposition: Literal["succeeded", "failed", "cancelled"]

    _ids = field_validator("attempt_id", "episode_id", "task_id")(_identifier)


class F7ControlPlaneObservation(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-control-observation.v1"]
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    target_run_id: str
    command_id: str
    slurm_job_id: str
    head_hostname: str
    identity: F7PinnedIdentity
    cached_selection_p99_ms: float = Field(ge=0, allow_inf_nan=False)
    effective_plan_resolution_p95_ms: float = Field(ge=0, allow_inf_nan=False)
    cold_compile_p95_ms: float = Field(ge=0, allow_inf_nan=False)
    baseline_control_plane_p95_ms: float = Field(gt=0, allow_inf_nan=False)
    config_native_control_plane_p95_ms: float = Field(gt=0, allow_inf_nan=False)
    baseline_throughput_eps: float = Field(gt=0, allow_inf_nan=False)
    config_native_throughput_eps: float = Field(gt=0, allow_inf_nan=False)
    evidence_sample_count: int = Field(ge=1)
    evidence_exact_join_count: int = Field(ge=0)
    evidence_sample_ids: tuple[str, ...] = Field(min_length=1)
    evidence_join_sample_ids: tuple[str, ...] = Field(min_length=1)
    identity_mismatch_count: int = Field(ge=0)
    mixed_config_latency: tuple[
        F7MixedConfigLatency,
        F7MixedConfigLatency,
        F7MixedConfigLatency,
        F7MixedConfigLatency,
        F7MixedConfigLatency,
        F7MixedConfigLatency,
    ]
    policy_version_integrity: Literal[True]
    queue_backpressure_integrity: Literal[True]
    load_ladder: tuple[
        F7LoadLevel,
        F7LoadLevel,
        F7LoadLevel,
        F7LoadLevel,
        F7LoadLevel,
        F7LoadLevel,
    ]
    soak_duration_seconds: int = Field(ge=0)
    soak_warmup_seconds: int = Field(ge=0)
    soak_measured_seconds: int = Field(ge=0)
    soak_sample_interval_seconds: int = Field(ge=1)
    soak_rss_sample_count: int = Field(ge=0)
    soak_terminal_records: tuple[F7SoakTerminalRecord, ...]
    first_30m_rss_p95_bytes: int = Field(gt=0)
    final_30m_rss_p95_bytes: int = Field(gt=0)
    five_minute_rss_medians_bytes: tuple[int, ...] = Field(min_length=24)
    integrity_failure_count: int = Field(ge=0)
    identity_failure_count: int = Field(ge=0)
    cleanup_failure_count: int = Field(ge=0)
    secret_leak_failure_count: int = Field(ge=0)
    aggregate_throughput_eps: float = Field(gt=0, allow_inf_nan=False)
    cleanup: F7CleanupObservation

    _ids = field_validator("target_run_id", "command_id", "slurm_job_id", "head_hostname")(
        _identifier
    )

    @model_validator(mode="after")
    def frozen_control_gates(self) -> "F7ControlPlaneObservation":
        expected_id = "two-node" if self.node_count == 2 else "four-node"
        if self.topology_id != expected_id:
            raise ValueError("control topology ID does not match node count")
        if self.cached_selection_p99_ms > MAX_CACHED_SELECTION_P99_MS:
            raise ValueError("cached selection p99 exceeds 2ms")
        for sample_id in self.evidence_sample_ids:
            _identifier(sample_id)
        if self.effective_plan_resolution_p95_ms > MAX_EFFECTIVE_PLAN_P95_MS:
            raise ValueError("effective-plan resolution p95 exceeds 10ms")
        if self.cold_compile_p95_ms > MAX_COLD_COMPILE_P95_MS:
            raise ValueError("cold compile p95 exceeds 500ms")
        control_limit = Decimal(str(self.baseline_control_plane_p95_ms)) * Decimal("1.10")
        if Decimal(str(self.config_native_control_plane_p95_ms)) > control_limit:
            raise ValueError("config-native control-plane overhead exceeds 10% p95")
        throughput_floor = Decimal(str(self.baseline_throughput_eps)) * Decimal("0.90")
        if Decimal(str(self.config_native_throughput_eps)) < throughput_floor:
            raise ValueError("config-native throughput regressed by more than 10%")
        if (
            self.evidence_sample_count != len(self.evidence_sample_ids)
            or self.evidence_exact_join_count != len(self.evidence_join_sample_ids)
            or len(set(self.evidence_sample_ids)) != len(self.evidence_sample_ids)
            or self.evidence_join_sample_ids != self.evidence_sample_ids
        ):
            raise ValueError("evidence joins must be exactly one per sample")
        if self.identity_mismatch_count != 0:
            raise ValueError("identity mismatch count must be zero")
        if tuple(item.config_id for item in self.mixed_config_latency) != F4_CONFIG_IDS:
            raise ValueError("mixed-config latency rows must be the ordered frozen six configs")
        if tuple(item.target_sessions for item in self.load_ladder) != REQUIRED_LOAD_LEVELS:
            raise ValueError("load ladder must be the ordered frozen 1/2/4/8/16/32 ladder")
        if (
            self.soak_duration_seconds < MIN_SOAK_SECONDS
            or self.soak_warmup_seconds != SOAK_WARMUP_SECONDS
            or self.soak_measured_seconds < SOAK_MEASURED_SECONDS
            or self.soak_warmup_seconds + self.soak_measured_seconds
            != self.soak_duration_seconds
        ):
            raise ValueError("canonical soak must include 15m warmup and at least 105m measured")
        if self.soak_sample_interval_seconds != SOAK_SAMPLE_INTERVAL_SECONDS:
            raise ValueError("canonical soak samples must use the frozen 15s interval")
        if self.soak_rss_sample_count < self.soak_duration_seconds // SOAK_SAMPLE_INTERVAL_SECONDS:
            raise ValueError("canonical soak RSS sample coverage is incomplete")
        records = self.soak_terminal_records
        if len(records) < MIN_SOAK_ATTEMPTS:
            raise ValueError("canonical soak requires at least 256 attempts and terminal records")
        attempts = tuple(item.attempt_id for item in records)
        episodes = tuple(item.episode_id for item in records)
        if len(set(attempts)) != len(records) or len(set(episodes)) != len(records):
            raise ValueError("canonical soak requires one unique terminal disposition per attempt")
        for config_id in F4_CONFIG_IDS:
            if sum(item.config_id == config_id for item in records) < MIN_CONFIG_ATTEMPTS:
                raise ValueError("canonical soak requires at least 32 attempts for each frozen config")
        if sum(item.task_id == "R-SWE-001" for item in records) < MIN_SWE_ATTEMPTS:
            raise ValueError("canonical soak requires at least 64 R-SWE-001 attempts")
        non_fault = tuple(item for item in records if not item.fault_injected)
        completed = sum(item.disposition == "succeeded" for item in non_fault)
        if not non_fault or completed / len(non_fault) < MIN_NON_FAULT_COMPLETION_RATE:
            raise ValueError("canonical soak non-fault completion is below 99.5%")
        if self.final_30m_rss_p95_bytes * 100 > self.first_30m_rss_p95_bytes * 105:
            raise ValueError("final-30m RSS p95 exceeds 105% of first-30m RSS p95")
        medians = self.five_minute_rss_medians_bytes
        if any(
            all(left <= right for left, right in zip(window, window[1:]))
            and any(left < right for left, right in zip(window, window[1:]))
            for window in (medians[index : index + 5] for index in range(len(medians) - 4))
        ):
            raise ValueError("RSS grows monotonically across five consecutive 5m medians")
        if any(
            (
                self.integrity_failure_count,
                self.identity_failure_count,
                self.cleanup_failure_count,
                self.secret_leak_failure_count,
            )
        ):
            raise ValueError("canonical soak integrity/identity/cleanup/secret failures must be zero")
        return self


class F7Phase3CommandRow(_ExactModel):
    command_id: str
    argv: tuple[str, ...]
    raw_log_path: str
    raw_log_sha256: str
    slurm_job_id: str
    target_run_id: str
    node: str
    nodes: tuple[str, ...]
    allocated_hosts: tuple[str, ...]
    started_at: str
    completed_at: str
    exit_code: Literal[0]
    status: Literal["passed"]
    blocked_reason: Literal[""]
    component_passed: Literal[True]
    component_failed_count: Literal[0]
    component_blocked_reasons: tuple[()]

    _ids = field_validator("command_id", "slurm_job_id", "target_run_id", "node")(_identifier)
    _raw_digest = field_validator("raw_log_sha256")(_digest)


class F7Phase3Manifest(_ExactModel):
    schema_version: Literal["bb.rl.phase3.command_log_manifest.v1"]
    target_run_id: str
    commands: tuple[F7Phase3CommandRow, ...]

    _target = field_validator("target_run_id")(_identifier)


class F7NodeArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-node-artifact.v1"]
    component: Literal["f7_topology_gate"]
    report_id: str
    passed: Literal[True]
    blocked_reason: Literal[""]
    input_digest: str
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    requested_hosts: tuple[str, ...]
    target_run_id: str
    command_id: str
    slurm_job_id: str
    hostname: str
    task_rank: int
    local_rank: Literal[0]
    identity: F7PinnedIdentity
    throughput_eps: float
    p95_latency_ms: float
    error_count: Literal[0]
    failure_count: Literal[0]
    rss_start_bytes: int
    rss_peak_bytes: int
    rss_end_bytes: int
    episode_joins: tuple[F7EpisodeJoin, ...]
    lease_ids: tuple[str, ...]
    cleanup: F7CleanupObservation
    artifact_paths: dict[str, str]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]

    _digest_value = field_validator("input_digest")(_digest)

    @field_validator("artifact_paths")
    @classmethod
    def exact_runner_artifact_paths(cls, value: dict[str, str]) -> dict[str, str]:
        if "task_local_artifact" not in value:
            raise ValueError("node report must bind its task-local artifact path")
        allowed = {"task_local_artifact", "component_report_json", "command_log"}
        if set(value) - allowed:
            raise ValueError("node report contains an unknown Phase3 runner artifact path")
        for path in value.values():
            _absolute(path)
        return value


class F7TopologyEvidenceSpec(_ExactModel):
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    requested_hosts: tuple[str, ...]
    requested_target_run_id: str
    target_run_id: str
    command_id: str
    slurm_job_id: str
    task_input: F7ImmutableJSONRef
    phase3_manifest: F7ImmutableJSONRef
    control_observation: F7ImmutableJSONRef
    node_artifacts: tuple[F7ImmutableJSONRef, ...]

    _ids = field_validator(
        "requested_target_run_id", "target_run_id", "command_id", "slurm_job_id"
    )(_identifier)

    @model_validator(mode="after")
    def complete_topology_refs(self) -> "F7TopologyEvidenceSpec":
        expected_id = "two-node" if self.node_count == 2 else "four-node"
        if self.topology_id != expected_id:
            raise ValueError("topology ID does not match node count")
        if len(self.requested_hosts) != self.node_count or len(set(self.requested_hosts)) != self.node_count:
            raise ValueError("requested complete host set must contain exactly unique node_count hosts")
        for host in self.requested_hosts:
            _identifier(host)
        if len(self.node_artifacts) != self.node_count:
            raise ValueError("one node artifact per requested task is required")
        paths = [
            self.task_input.path,
            self.phase3_manifest.path,
            self.control_observation.path,
        ]
        paths.extend(item.path for item in self.node_artifacts)
        if len(set(paths)) != len(paths):
            raise ValueError("topology evidence paths must be unique")
        return self


class F7FinalizeSpec(_ExactModel):
    gate_id: str
    expected_identity: F7PinnedIdentity
    topologies: tuple[F7TopologyEvidenceSpec, F7TopologyEvidenceSpec]

    _gate = field_validator("gate_id")(_identifier)

    @model_validator(mode="after")
    def ordered_two_then_four(self) -> "F7FinalizeSpec":
        if tuple((item.topology_id, item.node_count) for item in self.topologies) != (
            ("two-node", 2),
            ("four-node", 4),
        ):
            raise ValueError("F7 evidence must be exactly ordered two-node then four-node")
        return self


class F7TopologyGateInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-topology-gate-input.v1"]
    mode: Literal["task-local", "finalize"]
    task_local: F7TaskLocalSpec | None = None
    finalize: F7FinalizeSpec | None = None

    @model_validator(mode="after")
    def exactly_one_mode_payload(self) -> "F7TopologyGateInput":
        if self.mode == "task-local":
            if self.task_local is None or self.finalize is not None:
                raise ValueError("task-local mode requires only task_local payload")
        elif self.finalize is None or self.task_local is not None:
            raise ValueError("finalize mode requires only finalize payload")
        return self


class F7LeaseTopologyReport(_ExactModel):
    label: Literal["distributed", "head_local"]
    distributed_execution_claim: bool
    lease_hosts: tuple[str, ...]
    lease_ids_by_host: dict[str, tuple[str, ...]]

    @model_validator(mode="after")
    def exact_claim_label(self) -> "F7LeaseTopologyReport":
        distributed = self.label == "distributed"
        if self.distributed_execution_claim is not distributed:
            raise ValueError("lease topology label and distributed execution claim disagree")
        if not distributed and self.label != "head_local":
            raise ValueError("unproven distributed leases must be labeled head_local")
        return self


class F7TopologyReport(_ExactModel):
    topology_id: Literal["two-node", "four-node"]
    predecessor: Literal["none", "two-node:passed"]
    requested_node_count: Literal[2, 4]
    tasks_per_node: Literal[1]
    requested_hosts: tuple[str, ...]
    allocated_hosts: tuple[str, ...]
    observed_hosts: tuple[str, ...]
    task_ranks: tuple[int, ...]
    requested_target_run_id: str
    target_run_id: str
    slurm_job_id: str
    command_id: str
    identity: F7PinnedIdentity
    per_node: tuple[F7NodeArtifact, ...]
    episode_joins: tuple[F7EpisodeJoin, ...]
    aggregate_throughput_eps: float
    control_plane: F7ControlPlaneObservation
    observed_node_rss_peak_bytes: int
    observed_node_rss_max_growth_bytes: int
    error_count: Literal[0]
    failure_count: int = Field(ge=0)
    lease_topology: F7LeaseTopologyReport
    cleanup_complete: Literal[True]
    no_orphan_resources: Literal[True]
    task_input_ref: F7ImmutableJSONRef
    phase3_manifest_ref: F7ImmutableJSONRef
    control_observation_ref: F7ImmutableJSONRef
    node_artifact_refs: tuple[F7ImmutableJSONRef, ...]


class F7TopologyGateReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-topology-gate-report.v1"]
    component: Literal["f7_topology_gate"]
    report_id: str
    passed: Literal[True]
    blocked_reason: Literal[""]
    input_digest: str
    gate_id: str
    topology_order: tuple[Literal["two-node"], Literal["four-node"]]
    thresholds: dict[str, Any]
    topologies: tuple[F7TopologyReport, F7TopologyReport]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]

    _digest_value = field_validator("input_digest")(_digest)

    @model_validator(mode="after")
    def exact_final_projection(self) -> "F7TopologyGateReport":
        if self.topology_order != ("two-node", "four-node"):
            raise ValueError("final topology order drift")
        if tuple(item.topology_id for item in self.topologies) != self.topology_order:
            raise ValueError("final report topology rows drifted")
        if self.topologies[0].predecessor != "none" or self.topologies[1].predecessor != "two-node:passed":
            raise ValueError("four-node report lacks its passing two-node predecessor")
        expected_thresholds = {
            "cached_selection_p99_ms_lte": MAX_CACHED_SELECTION_P99_MS,
            "cold_compile_p95_ms_lte": MAX_COLD_COMPILE_P95_MS,
            "config_native_control_plane_overhead_ratio_lte": MAX_CONTROL_PLANE_OVERHEAD_RATIO,
            "config_native_throughput_regression_ratio_lte": MAX_THROUGHPUT_REGRESSION_RATIO,
            "effective_plan_resolution_p95_ms_lte": MAX_EFFECTIVE_PLAN_P95_MS,
            "evidence_exact_joins_per_sample": 1,
            "f4_attempts_per_config_gte": MIN_CONFIG_ATTEMPTS,
            "final_30m_to_first_30m_rss_p95_ratio_lte": MAX_FINAL_TO_FIRST_RSS_RATIO,
            "identity_mismatch_count": 0,
            "load_ladder_sessions": list(REQUIRED_LOAD_LEVELS),
            "non_fault_completion_rate_gte": MIN_NON_FAULT_COMPLETION_RATE,
            "r_swe_001_attempts_gte": MIN_SWE_ATTEMPTS,
            "soak_attempts_and_terminal_records_gte": MIN_SOAK_ATTEMPTS,
            "soak_measured_seconds_gte": SOAK_MEASURED_SECONDS,
            "soak_sample_interval_seconds": SOAK_SAMPLE_INTERVAL_SECONDS,
            "soak_total_seconds_gte": MIN_SOAK_SECONDS,
            "soak_warmup_seconds": SOAK_WARMUP_SECONDS,
            "zero_integrity_identity_cleanup_secret_failures": True,
        }
        if self.thresholds != expected_thresholds:
            raise ValueError("final report threshold projection drift")
        return self


def _read_ref(ref: F7ImmutableJSONRef, *, canonical: bool) -> tuple[Any, bytes]:
    source = Path(ref.path).resolve(strict=True)
    raw = source.read_bytes()
    if _sha256(raw) != ref.digest:
        raise F7TopologyGateError(f"immutable JSON digest mismatch: {ref.path}")
    value = canonical_json_loads(raw)
    if canonical and canonical_json_bytes(value) != raw:
        raise F7TopologyGateError(f"JSON artifact is not canonical: {ref.path}")
    return value, raw


def _read_input(path: str) -> tuple[F7TopologyGateInput, str]:
    raw = Path(path).resolve(strict=True).read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F7TopologyGateError("F7 input is not canonical JSON")
    return F7TopologyGateInput.model_validate_json(raw, strict=True), _sha256(raw)


def _exclusive_write(path: str, value: BaseModel) -> None:
    destination = Path(_absolute(path))
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        written = 0
        while written < len(raw):
            written += os.write(fd, raw[written:])
        os.fsync(fd)
    finally:
        os.close(fd)


def run_f7_task_local(
    spec: F7TopologyGateInput,
    *,
    input_digest: str,
    output_path: str,
    environment: Mapping[str, str] | None = None,
    observed_hostname: str | None = None,
) -> F7NodeArtifact:
    if spec.mode != "task-local" or spec.task_local is None:
        raise F7TopologyGateError("task-local runner received a non-task-local input")
    _digest(input_digest)
    _absolute(output_path)
    task = spec.task_local
    env = os.environ if environment is None else environment
    try:
        task_rank = int(env["SLURM_PROCID"])
        local_rank = int(env["SLURM_LOCALID"])
    except (KeyError, ValueError) as exc:
        raise F7TopologyGateError("Slurm task rank identity is missing or invalid") from exc
    hostname = socket.gethostname() if observed_hostname is None else observed_hostname
    if local_rank != 0:
        raise F7TopologyGateError("F7 requires exactly one task per node (SLURM_LOCALID=0)")
    if not 0 <= task_rank < task.node_count:
        raise F7TopologyGateError("Slurm task rank is outside the requested topology")
    if hostname not in task.requested_hosts:
        raise F7TopologyGateError("task executed on an unknown host")
    observed_job_id = env.get("SLURM_JOB_ID", "")
    try:
        _identifier(observed_job_id)
    except ValueError as exc:
        raise F7TopologyGateError("Slurm job identity is missing or invalid") from exc
    if env.get("PHASE3_TARGET_RUN_ID") != task.target_run_id:
        raise F7TopologyGateError("Phase3 target run identity drift")
    if env.get("PHASE3_COMMAND_ID") != task.command_id:
        raise F7TopologyGateError("Phase3 command identity drift")

    _, metric_raw = _read_ref(task.node_metrics_by_rank[task_rank], canonical=True)
    metrics = F7NodeMetrics.model_validate_json(metric_raw, strict=True)
    if (
        metrics.topology_id != task.topology_id
        or metrics.node_count != task.node_count
        or metrics.hostname != hostname
        or metrics.task_rank != task_rank
    ):
        raise F7TopologyGateError("task-local metric placement identity drift")
    if metrics.identity != task.expected_identity:
        raise F7TopologyGateError("task-local runtime/config/task/model identity drift")

    report = F7NodeArtifact(
        schema_version="bb.rl.phase5-f7-node-artifact.v1",
        component=_REPORT_COMPONENT,
        report_id=f"f7-{task.topology_id}-rank-{task_rank}",
        passed=True,
        blocked_reason="",
        input_digest=input_digest,
        topology_id=task.topology_id,
        node_count=task.node_count,
        requested_hosts=task.requested_hosts,
        target_run_id=task.target_run_id,
        command_id=task.command_id,
        slurm_job_id=observed_job_id,
        hostname=hostname,
        task_rank=task_rank,
        local_rank=0,
        identity=metrics.identity,
        throughput_eps=metrics.throughput_eps,
        p95_latency_ms=metrics.p95_latency_ms,
        error_count=0,
        failure_count=0,
        rss_start_bytes=metrics.rss_start_bytes,
        rss_peak_bytes=metrics.rss_peak_bytes,
        rss_end_bytes=metrics.rss_end_bytes,
        episode_joins=metrics.episode_joins,
        lease_ids=metrics.lease_ids,
        cleanup=metrics.cleanup,
        artifact_paths={"task_local_artifact": output_path},
        promotion_authority=False,
        scorecard_authority=False,
    )
    _exclusive_write(output_path, report)
    return report


def _argv_option(argv: tuple[str, ...], name: str) -> str | None:
    prefix = name + "="
    values = [item[len(prefix) :] for item in argv if item.startswith(prefix)]
    for index, item in enumerate(argv[:-1]):
        if item == name:
            values.append(argv[index + 1])
    if len(values) != 1:
        return None
    return values[0]


def _validate_task_input(
    evidence: F7TopologyEvidenceSpec, expected_identity: F7PinnedIdentity
) -> str:
    _, input_raw = _read_ref(evidence.task_input, canonical=True)
    source = F7TopologyGateInput.model_validate_json(input_raw, strict=True)
    task = source.task_local
    if (
        source.mode != "task-local"
        or task is None
        or task.topology_id != evidence.topology_id
        or task.node_count != evidence.node_count
        or task.requested_hosts != evidence.requested_hosts
        or task.target_run_id != evidence.requested_target_run_id
        or task.command_id != evidence.command_id
        or task.expected_identity != expected_identity
    ):
        raise F7TopologyGateError(
            f"{evidence.topology_id} immutable task input identity drift"
        )
    return evidence.task_input.digest


def _validate_manifest(
    evidence: F7TopologyEvidenceSpec,
) -> tuple[F7Phase3Manifest, F7Phase3CommandRow, str]:
    _, manifest_raw = _read_ref(evidence.phase3_manifest, canonical=False)
    manifest = F7Phase3Manifest.model_validate_json(manifest_raw, strict=True)
    if manifest.target_run_id != evidence.target_run_id:
        raise F7TopologyGateError(f"{evidence.topology_id} manifest target run identity drift")
    matching = tuple(row for row in manifest.commands if row.command_id == evidence.command_id)
    if len(matching) != 1:
        raise F7TopologyGateError(f"{evidence.topology_id} requires exactly one command row")
    row = matching[0]
    if row.target_run_id != evidence.target_run_id or row.slurm_job_id != evidence.slurm_job_id:
        raise F7TopologyGateError(f"{evidence.topology_id} command target/job identity drift")
    if row.node != row.nodes[0] if row.nodes else True:
        raise F7TopologyGateError(f"{evidence.topology_id} command primary node drift")
    requested = tuple(evidence.requested_hosts)
    if len(row.nodes) != evidence.node_count or len(set(row.nodes)) != evidence.node_count:
        raise F7TopologyGateError(f"{evidence.topology_id} observed host list is incomplete or duplicated")
    if len(row.allocated_hosts) != evidence.node_count or len(set(row.allocated_hosts)) != evidence.node_count:
        raise F7TopologyGateError(f"{evidence.topology_id} allocated host list is incomplete or duplicated")
    if set(row.nodes) != set(requested) or set(row.allocated_hosts) != set(requested):
        raise F7TopologyGateError(f"{evidence.topology_id} requested/allocated/observed host sets differ")
    expected_options = {
        "--nodes": str(evidence.node_count),
        "--ntasks": str(evidence.node_count),
        "--ntasks-per-node": "1",
        "--nodelist": ",".join(requested),
        "--target-run-id": evidence.requested_target_run_id,
        "--command-id": evidence.command_id,
    }
    for name, expected in expected_options.items():
        if _argv_option(row.argv, name) != expected:
            raise F7TopologyGateError(
                f"{evidence.topology_id} runner argv does not bind exact {name}={expected}"
            )
    manifest_path = Path(evidence.phase3_manifest.path).resolve(strict=True)
    if Path(row.raw_log_path).is_absolute():
        raise F7TopologyGateError(f"{evidence.topology_id} raw log path must be runner-relative")
    command_log_path = (manifest_path.parent / row.raw_log_path).resolve(strict=True)
    try:
        command_log_path.relative_to(manifest_path.parent)
    except ValueError as exc:
        raise F7TopologyGateError(
            f"{evidence.topology_id} raw log escapes the Phase3 output directory"
        ) from exc
    if _sha256(command_log_path.read_bytes()) != row.raw_log_sha256:
        raise F7TopologyGateError(f"{evidence.topology_id} raw command log digest mismatch")
    return manifest, row, str(command_log_path)


def _load_node_artifacts(
    evidence: F7TopologyEvidenceSpec,
    expected_identity: F7PinnedIdentity,
    command_log_path: str,
    task_input_digest: str,
) -> tuple[F7NodeArtifact, ...]:
    artifacts: list[F7NodeArtifact] = []
    for ref in evidence.node_artifacts:
        _, artifact_raw = _read_ref(ref, canonical=False)
        artifact = F7NodeArtifact.model_validate_json(artifact_raw, strict=True)
        if (
            artifact.topology_id != evidence.topology_id
            or artifact.node_count != evidence.node_count
            or artifact.requested_hosts != evidence.requested_hosts
            or artifact.target_run_id != evidence.target_run_id
            or artifact.command_id != evidence.command_id
            or artifact.slurm_job_id != evidence.slurm_job_id
        ):
            raise F7TopologyGateError(f"{evidence.topology_id} node artifact target identity drift")
        if artifact.input_digest != task_input_digest:
            raise F7TopologyGateError(
                f"{evidence.topology_id} node artifact immutable input digest drift"
            )
        if artifact.identity != expected_identity:
            raise F7TopologyGateError(f"{evidence.topology_id} node artifact pinned identity drift")
        expected_paths = {
            "task_local_artifact",
            "component_report_json",
            "command_log",
        }
        if set(artifact.artifact_paths) != expected_paths:
            raise F7TopologyGateError(
                f"{evidence.topology_id} node report lacks complete Phase3 runner artifact paths"
            )
        if (
            Path(artifact.artifact_paths["component_report_json"]).resolve()
            != Path(ref.path).resolve()
            or Path(artifact.artifact_paths["command_log"]).resolve()
            != Path(command_log_path).resolve()
        ):
            raise F7TopologyGateError(
                f"{evidence.topology_id} node report Phase3 artifact path identity drift"
            )
        artifacts.append(artifact)
    artifacts.sort(key=lambda item: item.task_rank)
    expected_ranks = tuple(range(evidence.node_count))
    if tuple(item.task_rank for item in artifacts) != expected_ranks:
        raise F7TopologyGateError(f"{evidence.topology_id} task rank coverage is incomplete or duplicated")
    hosts = tuple(item.hostname for item in artifacts)
    if len(set(hosts)) != evidence.node_count or set(hosts) != set(evidence.requested_hosts):
        raise F7TopologyGateError(f"{evidence.topology_id} has hidden single-node or unknown-host placement")
    return tuple(artifacts)


def _lease_topology(
    artifacts: tuple[F7NodeArtifact, ...], head_hostname: str
) -> F7LeaseTopologyReport:
    by_host = {item.hostname: tuple(item.lease_ids) for item in artifacts}
    all_ids = [lease_id for lease_ids in by_host.values() for lease_id in lease_ids]
    if len(set(all_ids)) != len(all_ids):
        raise F7TopologyGateError("lease identity appears on more than one node")
    distributed = bool(all_ids) and all(by_host[host] for host in by_host)
    if distributed:
        lease_hosts = tuple(sorted(host for host, lease_ids in by_host.items() if lease_ids))
        return F7LeaseTopologyReport(
            label="distributed",
            distributed_execution_claim=True,
            lease_hosts=lease_hosts,
            lease_ids_by_host=by_host,
        )
    head_ids = by_host.get(head_hostname, ())
    if any(lease_ids for host, lease_ids in by_host.items() if host != head_hostname):
        raise F7TopologyGateError("partial non-head lease placement cannot be labeled head_local")
    if all_ids and tuple(all_ids) != head_ids:
        raise F7TopologyGateError("head-local lease identity projection drift")
    return F7LeaseTopologyReport(
        label="head_local",
        distributed_execution_claim=False,
        lease_hosts=(head_hostname,) if head_ids else (),
        lease_ids_by_host=by_host,
    )


def _evaluate_topology(
    evidence: F7TopologyEvidenceSpec,
    *,
    expected_identity: F7PinnedIdentity,
    predecessor: Literal["none", "two-node:passed"],
) -> F7TopologyReport:
    task_input_digest = _validate_task_input(evidence, expected_identity)
    _, command, command_log_path = _validate_manifest(evidence)
    artifacts = _load_node_artifacts(
        evidence, expected_identity, command_log_path, task_input_digest
    )
    _, control_raw = _read_ref(evidence.control_observation, canonical=True)
    control = F7ControlPlaneObservation.model_validate_json(control_raw, strict=True)
    if (
        control.topology_id != evidence.topology_id
        or control.node_count != evidence.node_count
        or control.target_run_id != evidence.target_run_id
        or control.command_id != evidence.command_id
        or control.slurm_job_id != evidence.slurm_job_id
        or control.identity != expected_identity
    ):
        raise F7TopologyGateError(f"{evidence.topology_id} control-plane identity drift")
    if control.head_hostname not in evidence.requested_hosts:
        raise F7TopologyGateError(f"{evidence.topology_id} head host is outside the allocation")

    aggregate = math.fsum(item.throughput_eps for item in artifacts)
    if aggregate != control.aggregate_throughput_eps:
        raise F7TopologyGateError(
            f"{evidence.topology_id} aggregate throughput is not the per-node sum"
        )
    episode_joins = tuple(join for item in artifacts for join in item.episode_joins)
    episode_ids = tuple(item.episode_id for item in episode_joins)
    attempt_ids = tuple(item.attempt_id for item in episode_joins)
    if len(set(episode_ids)) != len(episode_ids) or len(set(attempt_ids)) != len(attempt_ids):
        raise F7TopologyGateError(f"{evidence.topology_id} episode joins are not globally unique")
    error_count = sum(item.error_count for item in artifacts)
    if error_count:
        raise F7TopologyGateError(f"{evidence.topology_id} per-node error gate did not pass")
    failure_count = sum(
        item.disposition != "succeeded" for item in control.soak_terminal_records
    )
    lease_topology = _lease_topology(artifacts, control.head_hostname)

    return F7TopologyReport(
        topology_id=evidence.topology_id,
        predecessor=predecessor,
        requested_node_count=evidence.node_count,
        tasks_per_node=1,
        requested_hosts=evidence.requested_hosts,
        allocated_hosts=command.allocated_hosts,
        observed_hosts=command.nodes,
        task_ranks=tuple(item.task_rank for item in artifacts),
        requested_target_run_id=evidence.requested_target_run_id,
        target_run_id=evidence.target_run_id,
        slurm_job_id=evidence.slurm_job_id,
        command_id=evidence.command_id,
        identity=expected_identity,
        per_node=artifacts,
        episode_joins=episode_joins,
        aggregate_throughput_eps=aggregate,
        control_plane=control,
        observed_node_rss_peak_bytes=max(item.rss_peak_bytes for item in artifacts),
        observed_node_rss_max_growth_bytes=max(
            item.rss_end_bytes - item.rss_start_bytes for item in artifacts
        ),
        error_count=0,
        failure_count=failure_count,
        lease_topology=lease_topology,
        cleanup_complete=True,
        no_orphan_resources=True,
        task_input_ref=evidence.task_input,
        phase3_manifest_ref=evidence.phase3_manifest,
        control_observation_ref=evidence.control_observation,
        node_artifact_refs=evidence.node_artifacts,
    )


def run_f7_topology_gate(
    spec: F7TopologyGateInput,
    *,
    input_digest: str,
    output_path: str,
) -> F7TopologyGateReport:
    if spec.mode != "finalize" or spec.finalize is None:
        raise F7TopologyGateError("finalizer received a non-finalize input")
    _digest(input_digest)
    _absolute(output_path)
    final = spec.finalize
    two = _evaluate_topology(
        final.topologies[0], expected_identity=final.expected_identity, predecessor="none"
    )
    four = _evaluate_topology(
        final.topologies[1],
        expected_identity=final.expected_identity,
        predecessor="two-node:passed",
    )
    report = F7TopologyGateReport(
        schema_version="bb.rl.phase5-f7-topology-gate-report.v1",
        component=_REPORT_COMPONENT,
        report_id=f"{final.gate_id}-report",
        passed=True,
        blocked_reason="",
        input_digest=input_digest,
        gate_id=final.gate_id,
        topology_order=("two-node", "four-node"),
        thresholds={
            "cached_selection_p99_ms_lte": MAX_CACHED_SELECTION_P99_MS,
            "cold_compile_p95_ms_lte": MAX_COLD_COMPILE_P95_MS,
            "config_native_control_plane_overhead_ratio_lte": MAX_CONTROL_PLANE_OVERHEAD_RATIO,
            "config_native_throughput_regression_ratio_lte": MAX_THROUGHPUT_REGRESSION_RATIO,
            "effective_plan_resolution_p95_ms_lte": MAX_EFFECTIVE_PLAN_P95_MS,
            "evidence_exact_joins_per_sample": 1,
            "f4_attempts_per_config_gte": MIN_CONFIG_ATTEMPTS,
            "final_30m_to_first_30m_rss_p95_ratio_lte": MAX_FINAL_TO_FIRST_RSS_RATIO,
            "identity_mismatch_count": 0,
            "load_ladder_sessions": list(REQUIRED_LOAD_LEVELS),
            "non_fault_completion_rate_gte": MIN_NON_FAULT_COMPLETION_RATE,
            "r_swe_001_attempts_gte": MIN_SWE_ATTEMPTS,
            "soak_attempts_and_terminal_records_gte": MIN_SOAK_ATTEMPTS,
            "soak_measured_seconds_gte": SOAK_MEASURED_SECONDS,
            "soak_sample_interval_seconds": SOAK_SAMPLE_INTERVAL_SECONDS,
            "soak_total_seconds_gte": MIN_SOAK_SECONDS,
            "soak_warmup_seconds": SOAK_WARMUP_SECONDS,
            "zero_integrity_identity_cleanup_secret_failures": True,
        },
        topologies=(two, four),
        promotion_authority=False,
        scorecard_authority=False,
    )
    _exclusive_write(output_path, report)
    return report


def _emit_component(report: BaseModel) -> None:
    os.write(
        1,
        b"PHASE3_COMPONENT_REPORT_JSON="
        + canonical_json_bytes(report.model_dump(mode="json"))
        + b"\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture or finalize the ordered frozen F7 two-/four-node topology gate"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    spec, input_digest = _read_input(args.input)
    if spec.mode == "task-local":
        report: BaseModel = run_f7_task_local(
            spec, input_digest=input_digest, output_path=args.output
        )
    else:
        report = run_f7_topology_gate(spec, input_digest=input_digest, output_path=args.output)
    _emit_component(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "F7CleanupObservation",
    "F7ControlPlaneObservation",
    "F7EpisodeJoin",
    "F7FinalizeSpec",
    "F7ImmutableJSONRef",
    "F7LoadLevel",
    "F7MixedConfigLatency",
    "F7NodeArtifact",
    "F7NodeMetrics",
    "F7PinnedIdentity",
    "F7SoakTerminalRecord",
    "F7TaskLocalSpec",
    "F7TopologyEvidenceSpec",
    "F7TopologyGateError",
    "F7TopologyGateInput",
    "F7TopologyGateReport",
    "run_f7_task_local",
    "run_f7_topology_gate",
]
