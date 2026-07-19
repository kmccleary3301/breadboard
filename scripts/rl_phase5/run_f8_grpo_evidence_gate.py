from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Literal

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_coder_prototype.compilation.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_OPTIMIZER_STEPS = 3
MIN_GENERATED_SAMPLES = 64
MAX_ACTOR_GRADIENT_NORM = 100.0
MAX_ACTOR_PPO_KL = 0.01
MAX_ACTOR_K3_KL = 0.0001
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class F8GRPOEvidenceGateError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("expected a lowercase sha256 digest")
    return value


def _metric_from_raw_line(line: str, name: str) -> float:
    match = re.search(rf"(?:^| - ){re.escape(name)}:([-+0-9.eE]+)(?: - |$)", line)
    if match is None:
        raise F8GRPOEvidenceGateError(f"required live VeRL metric is absent: {name}")
    value = float(match.group(1))
    if not math.isfinite(value):
        raise F8GRPOEvidenceGateError(f"nonfinite live VeRL metric: {name}")
    return value


def _parse_trainer_step_line(line: str) -> dict[str, int | float]:
    step_match = re.search(r"(?:^| )step:([0-9]+) - ", line)
    if step_match is None:
        raise F8GRPOEvidenceGateError("raw VeRL step marker is absent")
    return {
        "optimizer_step": int(step_match.group(1)),
        "training_global_step": int(
            _metric_from_raw_line(line, "training/global_step")
        ),
        "actor_gradient_norm": _metric_from_raw_line(line, "actor/grad_norm"),
        "learning_rate": _metric_from_raw_line(line, "actor/lr"),
        "advantage_min": _metric_from_raw_line(line, "critic/advantages/min"),
        "advantage_max": _metric_from_raw_line(line, "critic/advantages/max"),
        "aborted_ratio": _metric_from_raw_line(line, "response/aborted_ratio"),
        "actor_ppo_kl": abs(_metric_from_raw_line(line, "actor/ppo_kl")),
        "actor_k3_kl": abs(_metric_from_raw_line(line, "actor/kl_loss")),
    }


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 256
    ):
        raise ValueError("expected a bounded nonblank identifier")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("expected an absolute normalized path")
    return value


def _relative(value: str) -> str:
    if (
        type(value) is not str
        or not value
        or value.startswith("/")
        or os.path.normpath(value) != value
        or value == "."
        or value.startswith("../")
    ):
        raise ValueError("expected a normalized relative path")
    return value


def _utc(value: str) -> str:
    if type(value) is not str or _UTC_RE.fullmatch(value) is None:
        raise ValueError("expected a UTC timestamp without fractional seconds")
    return value


class F8ImmutableJSONRef(_ExactModel):
    path: str
    digest: str
    _path = field_validator("path")(_absolute)
    _sha = field_validator("digest")(_digest)


F8ImmutableBlobRef = F8ImmutableJSONRef


class F8TargetIdentity(_ExactModel):
    target_run_id: str
    command_id: str
    job_id: str
    _ids = field_validator("target_run_id", "command_id", "job_id")(_identifier)


class F8FileEntry(_ExactModel):
    relative_path: str
    size: int = Field(ge=0)
    digest: str
    _path = field_validator("relative_path")(_relative)
    _sha = field_validator("digest")(_digest)


class F8ConfigArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-config.v2"]
    trainer_entrypoint: Literal["verl.trainer.main_ppo"]
    rollout_name: Literal["vllm"]
    rollout_mode: Literal["sync"]
    rollout_n: int = Field(ge=2, le=64)
    train_batch_size: int = Field(ge=1, le=4096)
    val_batch_size: int = Field(ge=1, le=4096)
    total_training_steps: int = Field(ge=MIN_OPTIMIZER_STEPS, le=10_000)
    n_gpus_per_node: int = Field(ge=1, le=64)
    actor_learning_rate: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    timeout_seconds: int = Field(ge=1, le=86_400)
    terminate_grace_seconds: int = Field(ge=1, le=60)
    kill_grace_seconds: int = Field(ge=1, le=60)
    reward_num_workers: int = Field(ge=2, le=64)
    max_prompt_length: int = Field(ge=1, le=131_072)
    max_response_length: int = Field(ge=1, le=131_072)
    hf_home: str
    working_directory: str
    payload_manifest: F8ImmutableJSONRef
    trainer_module_relative_path: str
    trainer_module_digest: str
    reward_loader_relative_path: str
    reward_loader_digest: str
    output_parameter_relative_path: str
    changed_parameter_name: str
    _paths = field_validator("hf_home", "working_directory")(_absolute)
    _rel = field_validator(
        "output_parameter_relative_path",
        "trainer_module_relative_path",
        "reward_loader_relative_path",
    )(_relative)
    _digests = field_validator("trainer_module_digest", "reward_loader_digest")(_digest)
    _name = field_validator("changed_parameter_name")(_identifier)


class F8PromptMessage(_ExactModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=131_072)


class F8TaskRow(_ExactModel):
    row_id: str
    data_source: str
    prompt: tuple[F8PromptMessage, ...] = Field(min_length=1)
    ground_truth: str = Field(min_length=1, max_length=131_072)
    _ids = field_validator("row_id", "data_source")(_identifier)


class F8TaskArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-task.v1"]
    train_rows: tuple[F8TaskRow, ...] = Field(min_length=1)
    val_rows: tuple[F8TaskRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_rows(self) -> "F8TaskArtifact":
        ids = tuple(row.row_id for row in (*self.train_rows, *self.val_rows))
        if len(set(ids)) != len(ids):
            raise ValueError("task row IDs must be unique")
        return self


class F8TreeArtifact(_ExactModel):
    schema_version: Literal[
        "bb.rl.phase5-f8-model-tree.v1",
        "bb.rl.phase5-f8-tokenizer-tree.v1",
        "bb.rl.phase5-f8-checkpoint-tree.v1",
    ]
    root: str
    entries: tuple[F8FileEntry, ...] = Field(min_length=1)
    exact_tree: bool
    format: Literal["transformers", "numpy_npz"] = "transformers"
    parameter_files: dict[str, str] = Field(default_factory=dict)
    _root = field_validator("root")(_absolute)

    @model_validator(mode="after")
    def valid_entries(self) -> "F8TreeArtifact":
        paths = tuple(item.relative_path for item in self.entries)
        if len(set(paths)) != len(paths):
            raise ValueError("tree manifest paths must be unique")
        for value in self.parameter_files.values():
            _relative(value)
            if value not in paths:
                raise ValueError("named parameter file is absent from tree entries")
        return self


class F8PayloadArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-verl-payload-tree.v1"]
    root: str
    distribution: Literal["verl"]
    distribution_version: str = Field(min_length=1, max_length=256)
    provenance: str = Field(min_length=1, max_length=4096)
    entries: tuple[F8FileEntry, ...] = Field(min_length=1)
    exact_tree: Literal[True]
    entrypoint_relative_path: str
    reward_loader_relative_path: str
    _root = field_validator("root")(_absolute)
    _relative_paths = field_validator(
        "entrypoint_relative_path", "reward_loader_relative_path"
    )(_relative)

    @model_validator(mode="after")
    def complete_exact_payload(self) -> "F8PayloadArtifact":
        paths = tuple(entry.relative_path for entry in self.entries)
        if len(paths) != len(set(paths)):
            raise ValueError("payload manifest paths must be unique")
        if (
            self.entrypoint_relative_path not in paths
            or self.reward_loader_relative_path not in paths
        ):
            raise ValueError("payload omits a load-bearing VeRL module")
        return self


class F8ReloadHarnessManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-reload-harness.v1"]
    root: str
    entries: tuple[F8FileEntry, ...] = Field(min_length=8)
    exact_tree: Literal[True]
    target_script_relative_path: Literal["scripts/rl_phase3/target_verl_smoke_train.py"]
    _root = field_validator("root")(_absolute)

    @model_validator(mode="after")
    def unique_entries(self) -> "F8ReloadHarnessManifest":
        paths = tuple(entry.relative_path for entry in self.entries)
        if len(paths) != len(set(paths)):
            raise ValueError("reload harness entries are duplicated")
        if self.target_script_relative_path not in paths:
            raise ValueError("reload harness omits target script")
        return self


class F8VerifierArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-verifier.v1"]
    source: F8ImmutableBlobRef
    function_name: str
    _function = field_validator("function_name")(_identifier)


class F8ImageArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-image.v2"]
    immutable_image_digest: str
    image_reference: str
    container_runtime_executable: str
    container_runtime_digest: str
    container_python_executable: str
    _sha = field_validator("immutable_image_digest", "container_runtime_digest")(
        _digest
    )
    _paths = field_validator(
        "image_reference",
        "container_runtime_executable",
        "container_python_executable",
    )(_absolute)


_ALLOWED_OBSERVED_ENV = frozenset(
    {
        "SLURM_JOB_ID",
        "CUDA_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "ROCR_VISIBLE_DEVICES",
    }
)


class F8PreflightArtifact(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-preflight.v2"]
    passed: Literal[True]
    container_runtime_executable: str
    container_runtime_digest: str
    container_python_executable: str
    trainer_module: Literal["verl.trainer.main_ppo"]
    accelerator_mode: Literal["rocm"]
    payload_digest: str
    image_reference: str
    image_digest: str
    observed_environment: dict[str, str]
    _paths = field_validator(
        "container_runtime_executable",
        "container_python_executable",
        "image_reference",
    )(_absolute)
    _digests = field_validator(
        "container_runtime_digest", "payload_digest", "image_digest"
    )(_digest)

    @model_validator(mode="after")
    def environment_allowlist(self) -> "F8PreflightArtifact":
        if not set(self.observed_environment).issubset(_ALLOWED_OBSERVED_ENV):
            raise ValueError("preflight contains a non-observational environment key")
        if any(
            type(value) is not str or len(value) > 4096
            for value in self.observed_environment.values()
        ):
            raise ValueError("preflight environment values must be bounded strings")
        return self


class F8IdentityArtifactRefs(_ExactModel):
    config: F8ImmutableJSONRef
    task: F8ImmutableJSONRef
    model: F8ImmutableJSONRef
    tokenizer: F8ImmutableJSONRef
    input_checkpoint: F8ImmutableJSONRef
    verifier: F8ImmutableJSONRef
    image: F8ImmutableJSONRef
    preflight: F8ImmutableJSONRef


class F8TrainingIdentities(_ExactModel):
    config_digest: str
    task_digest: str
    model_digest: str
    tokenizer_digest: str
    input_checkpoint_digest: str
    output_checkpoint_digest: str
    verifier_digest: str
    image_digest: str
    preflight_digest: str
    _digests = field_validator(
        "config_digest",
        "task_digest",
        "model_digest",
        "tokenizer_digest",
        "input_checkpoint_digest",
        "output_checkpoint_digest",
        "verifier_digest",
        "image_digest",
        "preflight_digest",
    )(_digest)

    @model_validator(mode="after")
    def changed_checkpoint(self) -> "F8TrainingIdentities":
        if self.input_checkpoint_digest == self.output_checkpoint_digest:
            raise ValueError("input and output checkpoint manifests must differ")
        return self


class F8InputHashes(_ExactModel):
    config_input_sha256: str
    task_input_sha256: str
    model_input_sha256: str
    tokenizer_input_sha256: str
    checkpoint_input_sha256: str
    verifier_input_sha256: str
    image_input_sha256: str
    preflight_input_sha256: str
    _digests = field_validator(
        "config_input_sha256",
        "task_input_sha256",
        "model_input_sha256",
        "tokenizer_input_sha256",
        "checkpoint_input_sha256",
        "verifier_input_sha256",
        "image_input_sha256",
        "preflight_input_sha256",
    )(_digest)

    def require_identity_join(self, identities: F8TrainingIdentities) -> None:
        if (
            self.config_input_sha256,
            self.task_input_sha256,
            self.model_input_sha256,
            self.tokenizer_input_sha256,
            self.checkpoint_input_sha256,
            self.verifier_input_sha256,
            self.image_input_sha256,
            self.preflight_input_sha256,
        ) != (
            identities.config_digest,
            identities.task_digest,
            identities.model_digest,
            identities.tokenizer_digest,
            identities.input_checkpoint_digest,
            identities.verifier_digest,
            identities.image_digest,
            identities.preflight_digest,
        ):
            raise ValueError("input hashes do not exactly join identities")


class F8RolloutCarrier(_ExactModel):
    target_run_id: str
    episode_id: str
    attempt_id: str
    optimizer_step: int = Field(ge=1)
    task_row_id: str
    rollout_index: int = Field(ge=0)
    config_digest: str
    task_digest: str
    model_digest: str
    tokenizer_digest: str
    checkpoint_digest: str
    verifier_digest: str
    image_digest: str
    preflight_digest: str
    carrier_digest: str
    _ids = field_validator("target_run_id", "episode_id", "attempt_id", "task_row_id")(
        _identifier
    )
    _digests = field_validator(
        "config_digest",
        "task_digest",
        "model_digest",
        "tokenizer_digest",
        "checkpoint_digest",
        "verifier_digest",
        "image_digest",
        "preflight_digest",
        "carrier_digest",
    )(_digest)

    def require_join(
        self, target: F8TargetIdentity, identities: F8TrainingIdentities
    ) -> None:
        if (
            self.target_run_id,
            self.config_digest,
            self.task_digest,
            self.model_digest,
            self.tokenizer_digest,
            self.checkpoint_digest,
            self.verifier_digest,
            self.image_digest,
            self.preflight_digest,
        ) != (
            target.target_run_id,
            identities.config_digest,
            identities.task_digest,
            identities.model_digest,
            identities.tokenizer_digest,
            identities.input_checkpoint_digest,
            identities.verifier_digest,
            identities.image_digest,
            identities.preflight_digest,
        ):
            raise ValueError("carrier identity closure mismatch")


class F8CarrierManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-carrier-manifest.v2"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    rollout_n: int = Field(ge=2)
    expected_sample_count: int = Field(ge=MIN_GENERATED_SAMPLES)
    carriers: tuple[F8ImmutableJSONRef, ...] = Field(min_length=MIN_GENERATED_SAMPLES)


class F8RolloutSampleRecord(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-rollout-sample.v2"]
    sample_id: str
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    rollout_carrier: F8RolloutCarrier
    carrier_ref: F8ImmutableJSONRef
    claim_ref: F8ImmutableJSONRef
    reward: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    solution_sha256: str
    verifier_digest: str
    _sample = field_validator("sample_id")(_identifier)
    _digests = field_validator("solution_sha256", "verifier_digest")(_digest)


class F8CarrierClaim(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-carrier-claim.v2"]
    carrier_ref: F8ImmutableJSONRef
    carrier_digest: str
    claimant_pid: int = Field(ge=1)
    claimant_thread_id: int = Field(ge=1)
    _sha = field_validator("carrier_digest")(_digest)


class F8CarrierDisposition(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-carrier-disposition.v1"]
    carrier_ref: F8ImmutableJSONRef
    claim_ref: F8ImmutableJSONRef
    state: Literal["recorded"]
    record_ref: F8ImmutableJSONRef


class F8RolloutEvidenceManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-rollout-evidence-manifest.v2"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    carrier_manifest: F8ImmutableJSONRef
    generated_sample_count: int = Field(ge=MIN_GENERATED_SAMPLES)
    records: tuple[F8ImmutableJSONRef, ...] = Field(min_length=MIN_GENERATED_SAMPLES)
    dispositions: tuple[F8ImmutableJSONRef, ...] = Field(
        min_length=MIN_GENERATED_SAMPLES
    )


class F8TrainerStepRecord(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-trainer-step.v3"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    optimizer_step: int = Field(ge=1)
    training_global_step: int = Field(ge=1)
    raw_line_sha256: str
    raw_line: str = Field(min_length=1)
    actor_gradient_norm: float = Field(
        gt=0.0, le=MAX_ACTOR_GRADIENT_NORM, allow_inf_nan=False
    )
    learning_rate: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    advantage_min: float = Field(allow_inf_nan=False)
    advantage_max: float = Field(allow_inf_nan=False)
    aborted_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    actor_ppo_kl: float = Field(ge=0.0, le=MAX_ACTOR_PPO_KL, allow_inf_nan=False)
    actor_k3_kl: float = Field(ge=0.0, le=MAX_ACTOR_K3_KL, allow_inf_nan=False)
    _raw = field_validator("raw_line_sha256")(_digest)

    @model_validator(mode="after")
    def exact_raw_step(self) -> "F8TrainerStepRecord":
        if self.optimizer_step != self.training_global_step:
            raise ValueError("optimizer and global step identities differ")
        if _sha256(self.raw_line.encode()) != self.raw_line_sha256:
            raise ValueError("raw trainer line digest mismatch")
        return self


class F8OptimizerStepReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-optimizer-step-receipt.v1"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    optimizer_step: int = Field(ge=1)
    checkpoint_ref: F8ImmutableJSONRef
    optimizer_state_refs: tuple[F8ImmutableBlobRef, ...] = Field(min_length=1)
    parameter_name: str
    parameter_digest: str
    parameter_all_finite: Literal[True]
    _name = field_validator("parameter_name")(_identifier)
    _sha = field_validator("parameter_digest")(_digest)


class F8OptimizerStepsManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-optimizer-steps-manifest.v1"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    records: tuple[F8ImmutableJSONRef, ...] = Field(min_length=MIN_OPTIMIZER_STEPS)


class F8TrainerMetricsManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-trainer-metrics-manifest.v2"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    rollout_mode: Literal["sync"]
    stdout_ref: F8ImmutableBlobRef
    records: tuple[F8ImmutableJSONRef, ...] = Field(min_length=MIN_OPTIMIZER_STEPS)


class F8ContainerObservationReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-container-observation.v1"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    probe_process_id: int = Field(ge=1)
    parent_process_id: int = Field(ge=1)
    command: tuple[str, ...] = Field(min_length=10)
    container_runtime_executable: str
    container_runtime_digest: str
    container_python_executable: str
    observed_image_reference: str
    observed_image_digest: str
    _paths = field_validator(
        "container_runtime_executable",
        "container_python_executable",
        "observed_image_reference",
    )(_absolute)
    _digests = field_validator("container_runtime_digest", "observed_image_digest")(
        _digest
    )


class F8ObservedRuntimeManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-observed-runtime.v2"]
    target: F8TargetIdentity
    input_ref: F8ImmutableJSONRef
    identity_artifacts: F8IdentityArtifactRefs
    container_runtime_executable: str
    container_runtime_digest: str
    container_python_executable: str
    command: tuple[str, ...] = Field(min_length=10)
    child_environment: dict[str, str]
    working_directory: str
    payload_manifest: F8ImmutableJSONRef
    payload_digest: str
    container_observation_ref: F8ImmutableJSONRef
    requested_image_reference: str
    effective_image_reference: str
    observed_image_reference: str
    requested_image_digest: str
    effective_image_digest: str
    observed_image_digest: str
    train_dataset: F8ImmutableBlobRef
    val_dataset: F8ImmutableBlobRef
    reward_adapter: F8ImmutableBlobRef
    claim_root: str
    record_root: str
    disposition_root: str
    train_projection_digest: str
    val_projection_digest: str
    output_checkpoint_root: str
    controller_pid: int = Field(ge=1)
    trainer_pid: int = Field(ge=1)
    trainer_pgid: int = Field(ge=1)
    _paths = field_validator(
        "container_runtime_executable",
        "container_python_executable",
        "working_directory",
        "output_checkpoint_root",
        "claim_root",
        "record_root",
        "disposition_root",
        "requested_image_reference",
        "effective_image_reference",
        "observed_image_reference",
    )(_absolute)
    _digests = field_validator(
        "container_runtime_digest",
        "payload_digest",
        "requested_image_digest",
        "effective_image_digest",
        "observed_image_digest",
        "train_projection_digest",
        "val_projection_digest",
    )(_digest)


class F8CheckpointReloadEvidence(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-checkpoint-reload-evidence.v2"]
    target: F8TargetIdentity
    input_hashes: F8InputHashes
    checkpoint_before_ref: F8ImmutableJSONRef
    checkpoint_after_ref: F8ImmutableJSONRef
    changed_parameter_name: str
    parameter_before_digest: str
    parameter_after_digest: str
    optimizer_update_digest: str
    reload_request_ref: F8ImmutableJSONRef
    reload_receipt_ref: F8ImmutableJSONRef
    reload_pid: int = Field(ge=1)
    trainer_pid: int = Field(ge=1)
    reload_returncode: Literal[0]
    reload_command: tuple[str, ...] = Field(min_length=10)
    reload_harness_ref: F8ImmutableJSONRef
    deterministic_inference_digest: str
    verifier_reward: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    _name = field_validator("changed_parameter_name")(_identifier)
    _sha = field_validator(
        "parameter_before_digest",
        "parameter_after_digest",
        "optimizer_update_digest",
        "deterministic_inference_digest",
    )(_digest)

    @model_validator(mode="after")
    def process_and_parameter_change(self) -> "F8CheckpointReloadEvidence":
        if self.reload_pid == self.trainer_pid:
            raise ValueError("reload did not execute in a fresh process")
        if self.parameter_before_digest == self.parameter_after_digest:
            raise ValueError("named parameter did not change")
        return self


class F8ReloadRequest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-reload-request.v1"]
    output_path: str
    checkpoint_ref: F8ImmutableJSONRef
    model_ref: F8ImmutableJSONRef
    tokenizer_ref: F8ImmutableJSONRef
    config_ref: F8ImmutableJSONRef
    verifier_ref: F8ImmutableJSONRef
    parameter_name: str
    prompt: str
    ground_truth: str
    _output = field_validator("output_path")(_absolute)
    _parameter = field_validator("parameter_name")(_identifier)


class F8ReloadReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-reload-receipt.v1"]
    request_ref: F8ImmutableJSONRef
    process_id: int = Field(ge=1)
    parent_process_id: int = Field(ge=1)
    checkpoint_ref: F8ImmutableJSONRef
    model_ref: F8ImmutableJSONRef
    tokenizer_ref: F8ImmutableJSONRef
    config_ref: F8ImmutableJSONRef
    parameter_name: str
    parameter_digest: str
    deterministic: Literal[True]
    inference_output_sha256: str
    verifier_reward: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    _name = field_validator("parameter_name")(_identifier)
    _sha = field_validator("parameter_digest", "inference_output_sha256")(_digest)


class F8TerminalLifecycleRecord(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-terminal-lifecycle.v1"]
    target: F8TargetIdentity
    terminal_state: Literal["closed", "failed_quarantined"]
    trainer_pid: int = Field(ge=0)
    trainer_pgid: int = Field(ge=0)
    trainer_returncode: int
    timed_out: bool
    termination_signals: tuple[Literal["SIGTERM", "SIGKILL"], ...]
    process_group_reaped: bool
    stdout_reader_joined: bool
    remaining_process_ids: tuple[int, ...]
    remaining_container_ids: tuple[str, ...]
    active_lease_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]
    retained_checkpoint_ref: F8ImmutableJSONRef | None
    quarantined_artifacts: tuple[F8ImmutableJSONRef, ...]

    @model_validator(mode="after")
    def terminal_is_consistent(self) -> "F8TerminalLifecycleRecord":
        if self.terminal_state == "closed":
            if (
                self.timed_out
                or self.trainer_returncode != 0
                or not self.process_group_reaped
                or not self.stdout_reader_joined
                or self.remaining_process_ids
                or self.remaining_container_ids
                or self.active_lease_ids
                or self.cleanup_errors
                or self.retained_checkpoint_ref is None
                or self.quarantined_artifacts
            ):
                raise ValueError("successful lifecycle receipt is inconsistent")
        elif self.retained_checkpoint_ref is not None or not self.quarantined_artifacts:
            raise ValueError("failed lifecycle did not quarantine partial output")
        return self


class F8TargetRunnerReceipt(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-target-runner-receipt.v1"]
    component: Literal["f8_canonical_target_runner"]
    execution_scope: Literal["ibm_slurm_apptainer"]
    target: F8TargetIdentity
    input_ref: F8ImmutableJSONRef
    input_hashes: F8InputHashes
    source_report_ref: F8ImmutableJSONRef
    authority_key_id: str
    authority_key_digest: str
    authority_signature: str
    slurm_job_id: str
    runtime_ref: F8ImmutableJSONRef
    wrapper_source_digest: str
    target_source_digest: str
    gate_source_digest: str
    reload_harness_manifest_digest: str
    container_observation_ref: F8ImmutableJSONRef
    callback_record_refs: tuple[F8ImmutableJSONRef, ...] = Field(
        min_length=MIN_GENERATED_SAMPLES
    )
    callback_disposition_refs: tuple[F8ImmutableJSONRef, ...] = Field(
        min_length=MIN_GENERATED_SAMPLES
    )
    trainer_step_refs: tuple[F8ImmutableJSONRef, ...] = Field(
        min_length=MIN_OPTIMIZER_STEPS
    )
    optimizer_step_refs: tuple[F8ImmutableJSONRef, ...] = Field(
        min_length=MIN_OPTIMIZER_STEPS
    )
    checkpoint_reload_ref: F8ImmutableJSONRef
    terminal_lifecycle_ref: F8ImmutableJSONRef
    trainer_pid: int = Field(ge=1)
    trainer_pgid: int = Field(ge=1)
    reload_pid: int = Field(ge=1)
    trainer_returncode: Literal[0]
    command: tuple[str, ...] = Field(min_length=10)
    completed_at: str
    _job = field_validator("slurm_job_id")(_identifier)
    _time = field_validator("completed_at")(_utc)
    _authority_id = field_validator("authority_key_id")(_identifier)
    _authority_digests = field_validator("authority_key_digest", "authority_signature")(
        _digest
    )
    _producer_digests = field_validator(
        "wrapper_source_digest",
        "target_source_digest",
        "gate_source_digest",
        "reload_harness_manifest_digest",
    )(_digest)

    @model_validator(mode="after")
    def fresh_reload_process(self) -> "F8TargetRunnerReceipt":
        if self.reload_pid in (self.trainer_pid, self.trainer_pgid):
            raise ValueError("target runner receipt lacks a fresh reload process")
        return self


class F8TargetSourceArtifacts(_ExactModel):
    observed_runtime: F8ImmutableJSONRef
    container_observation: F8ImmutableJSONRef
    carrier_manifest: F8ImmutableJSONRef
    reload_harness: F8ImmutableJSONRef
    rollout_manifest: F8ImmutableJSONRef
    trainer_metrics_manifest: F8ImmutableJSONRef
    optimizer_steps_manifest: F8ImmutableJSONRef
    checkpoint_reload: F8ImmutableJSONRef
    terminal_lifecycle: F8ImmutableJSONRef
    stdout: F8ImmutableBlobRef
    stderr: F8ImmutableBlobRef


class F8TargetSourceReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-target-source-report.v4"]
    component: Literal["f8_verl_grpo_target_source"]
    report_id: str
    report_path: str
    passed: bool
    blocked_reason: str
    target: F8TargetIdentity
    requested_target: F8TargetIdentity
    input_ref: F8ImmutableJSONRef
    identity_artifacts: F8IdentityArtifactRefs
    identities: F8TrainingIdentities | None
    input_hashes: F8InputHashes
    expected_sample_count: int = Field(ge=MIN_GENERATED_SAMPLES)
    observed_sample_record_count: int = Field(ge=0)
    observed_optimizer_metric_record_count: int = Field(ge=0)
    artifacts: F8TargetSourceArtifacts
    completed_at: str
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    _report = field_validator("report_id")(_identifier)
    _path = field_validator("report_path")(_absolute)
    _completed = field_validator("completed_at")(_utc)

    @model_validator(mode="after")
    def result_consistency(self) -> "F8TargetSourceReport":
        if self.passed:
            if self.blocked_reason or self.identities is None:
                raise ValueError("passing source report lacks authoritative identities")
        elif not self.blocked_reason:
            raise ValueError("failed source report lacks a blocked reason")
        return self


class F8ExpectedEpisodeJoin(_ExactModel):
    episode_id: str
    attempt_id: str
    rollout_carrier_digest: str
    _ids = field_validator("episode_id", "attempt_id")(_identifier)
    _sha = field_validator("rollout_carrier_digest")(_digest)


class F8EpisodeJoin(_ExactModel):
    episode_id: str
    attempt_id: str
    identities: F8TrainingIdentities
    rollout_carrier: F8RolloutCarrier
    generated_sample_count: Literal[1]
    joined_sample_count: Literal[1]
    reward_min: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reward_max: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    evidence_digest: str
    _ids = field_validator("episode_id", "attempt_id")(_identifier)
    _sha = field_validator("evidence_digest")(_digest)


class F8LearningEvidence(_ExactModel):
    run_kind: Literal["bounded"]
    optimizer_step_count: int = Field(ge=MIN_OPTIMIZER_STEPS)
    generated_sample_count: int = Field(ge=MIN_GENERATED_SAMPLES)
    reward_min: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reward_max: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    advantage_abs_max: float = Field(gt=0.0, allow_inf_nan=False)
    actor_gradient_norm: float = Field(
        gt=0.0, le=MAX_ACTOR_GRADIENT_NORM, allow_inf_nan=False
    )
    learning_rate: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    optimizer_step_skipped: Literal[False]
    optimizer_update_finite: Literal[True]
    aborted_ratio: Literal[0.0]
    dropped_stale_samples: Literal[0]
    actor_ppo_kl: float = Field(ge=0.0, le=MAX_ACTOR_PPO_KL, allow_inf_nan=False)
    actor_k3_kl: float = Field(ge=0.0, le=MAX_ACTOR_K3_KL, allow_inf_nan=False)
    required_kl_metrics_present: Literal[True]

    @model_validator(mode="after")
    def nondegenerate(self) -> "F8LearningEvidence":
        if self.reward_max <= self.reward_min:
            raise ValueError("reward range is degenerate")
        return self


class F8CheckpointUpdate(_ExactModel):
    checkpoint_before_digest: str
    checkpoint_after_digest: str
    changed_parameter_name: str
    parameter_before_digest: str
    parameter_after_digest: str
    optimizer_update_digest: str
    retained_checkpoint_digest: str
    fresh_process_reload: Literal[True]
    reload_model_digest: str
    reload_config_digest: str
    reload_tokenizer_digest: str
    changed_parameter_reverified: Literal[True]
    bounded_inference_or_preflight_passed: Literal[True]
    _name = field_validator("changed_parameter_name")(_identifier)
    _sha = field_validator(
        "checkpoint_before_digest",
        "checkpoint_after_digest",
        "parameter_before_digest",
        "parameter_after_digest",
        "optimizer_update_digest",
        "retained_checkpoint_digest",
        "reload_model_digest",
        "reload_config_digest",
        "reload_tokenizer_digest",
    )(_digest)


class F8EvidenceJoin(_ExactModel):
    generated_sample_count: int = Field(ge=MIN_GENERATED_SAMPLES)
    joined_sample_count: int = Field(ge=MIN_GENERATED_SAMPLES)
    unmatched_sample_count: Literal[0]
    duplicate_join_count: Literal[0]
    carrier_alignment_exact: Literal[True]
    episode_attempt_alignment_exact: Literal[True]
    evidence_manifest_digest: str
    _sha = field_validator("evidence_manifest_digest")(_digest)


class F8CleanupEvidence(_ExactModel):
    terminal_state: Literal["closed"]
    active_lease_ids: tuple[str, ...]
    remaining_process_ids: tuple[int, ...]
    remaining_container_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]
    failed_outputs_quarantined: Literal[True]
    failed_checkpoints_quarantined: Literal[True]
    retained_checkpoint_present: Literal[True]


class F8GRPOEvidenceGateInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-grpo-evidence-gate-input.v3"]
    gate_id: str
    target: F8TargetIdentity
    expected_episode_joins: tuple[F8ExpectedEpisodeJoin, ...] = Field(
        min_length=MIN_GENERATED_SAMPLES
    )
    target_source_report: F8ImmutableJSONRef
    target_runner_receipt: F8ImmutableJSONRef
    _gate = field_validator("gate_id")(_identifier)

    @model_validator(mode="after")
    def expected_unique(self) -> "F8GRPOEvidenceGateInput":
        keys = tuple(
            (row.episode_id, row.attempt_id) for row in self.expected_episode_joins
        )
        carriers = tuple(
            row.rollout_carrier_digest for row in self.expected_episode_joins
        )
        if len(set(keys)) != len(keys) or len(set(carriers)) != len(carriers):
            raise ValueError("expected episode/carrier joins are duplicated")
        return self


class F8GRPOEvidenceGateReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-grpo-evidence-gate-report.v3"]
    component: Literal["f8_grpo_evidence_gate"]
    report_id: str
    passed: Literal[True]
    blocked_reason: Literal[""]
    input_digest: str
    gate_id: str
    target: F8TargetIdentity
    identities: F8TrainingIdentities
    input_hashes: F8InputHashes
    trainer_backend: Literal["verl_grpo"]
    algorithm_adv_estimator: Literal["grpo"]
    estimator_label: Literal["grpo"]
    rollout_n: int = Field(ge=2)
    learning_evidence: F8LearningEvidence
    episode_joins: tuple[F8EpisodeJoin, ...] = Field(min_length=MIN_GENERATED_SAMPLES)
    evidence_join: F8EvidenceJoin
    checkpoint_update: F8CheckpointUpdate
    cleanup: F8CleanupEvidence
    target_source_report: F8ImmutableJSONRef
    completed_at: str
    claim_scope: Literal[
        "finite_step_optimizer_signal_not_convergence_or_benchmark_gain"
    ]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    _ids = field_validator("report_id", "gate_id")(_identifier)
    _sha = field_validator("input_digest")(_digest)
    _time = field_validator("completed_at")(_utc)


def _read_blob(ref: F8ImmutableJSONRef, label: str) -> bytes:
    try:
        raw = Path(ref.path).resolve(strict=True).read_bytes()
    except (OSError, RuntimeError) as exc:
        raise F8GRPOEvidenceGateError(f"F8 {label} cannot be read") from exc
    if _sha256(raw) != ref.digest:
        raise F8GRPOEvidenceGateError(f"F8 {label} digest mismatch")
    return raw


def _read_model(
    ref: F8ImmutableJSONRef, model: type[BaseModel], label: str
) -> BaseModel:
    raw = _read_blob(ref, label)
    try:
        value = canonical_json_loads(raw)
        if canonical_json_bytes(value) != raw:
            raise ValueError("noncanonical")
        return model.model_validate_json(raw, strict=True)
    except Exception as exc:
        raise F8GRPOEvidenceGateError(
            f"F8 {label} failed its canonical strict contract"
        ) from exc


def _validate_tree(manifest: F8TreeArtifact, label: str) -> None:
    root = Path(manifest.root).resolve(strict=True)
    observed: set[str] = set()
    for entry in manifest.entries:
        path = (root / entry.relative_path).resolve(strict=True)
        if root not in path.parents:
            raise F8GRPOEvidenceGateError(f"F8 {label} entry escapes its root")
        raw = path.read_bytes()
        if len(raw) != entry.size or _sha256(raw) != entry.digest:
            raise F8GRPOEvidenceGateError(f"F8 {label} tree entry mismatch")
        observed.add(entry.relative_path)
    if manifest.exact_tree:
        actual = {
            str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
        }
        if actual != observed:
            raise F8GRPOEvidenceGateError(f"F8 {label} exact tree has undeclared files")


def _validate_payload(manifest: F8PayloadArtifact, label: str) -> None:
    root = Path(manifest.root).resolve(strict=True)
    observed: set[str] = set()
    for entry in manifest.entries:
        path = (root / entry.relative_path).resolve(strict=True)
        if root not in path.parents:
            raise F8GRPOEvidenceGateError(f"F8 {label} entry escapes its root")
        raw = path.read_bytes()
        if len(raw) != entry.size or _sha256(raw) != entry.digest:
            raise F8GRPOEvidenceGateError(f"F8 {label} tree entry mismatch")
        observed.add(entry.relative_path)
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != observed:
        raise F8GRPOEvidenceGateError(f"F8 {label} exact tree has undeclared files")


def _validate_image_and_runtime(image: F8ImageArtifact) -> None:
    image_raw = Path(image.image_reference).resolve(strict=True).read_bytes()
    if _sha256(image_raw) != image.immutable_image_digest:
        raise F8GRPOEvidenceGateError("F8 immutable container image bytes changed")
    runtime_path = Path(image.container_runtime_executable).resolve(strict=True)
    runtime_raw = runtime_path.read_bytes()
    if _sha256(runtime_raw) != image.container_runtime_digest:
        raise F8GRPOEvidenceGateError("F8 container runtime executable bytes changed")
    native_magics = (
        b"\x7fELF",
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    )
    if not runtime_raw.startswith(native_magics) or not os.access(
        runtime_path, os.X_OK
    ):
        raise F8GRPOEvidenceGateError(
            "F8 container runtime must be a pinned native executable, not a script"
        )


def _parameter_tensor_evidence(
    manifest: F8TreeArtifact, parameter_name: str
) -> tuple[str, bool]:
    relative = manifest.parameter_files.get(parameter_name)
    if relative is None:
        raise F8GRPOEvidenceGateError(
            "F8 named parameter is absent from checkpoint manifest"
        )
    path = Path(manifest.root) / relative
    try:
        import numpy as np

        if manifest.format == "numpy_npz":
            with np.load(path, allow_pickle=False) as archive:
                value = archive[parameter_name]
        else:
            from safetensors import safe_open

            with safe_open(path, framework="np", device="cpu") as handle:
                value = handle.get_tensor(parameter_name)
        all_finite = bool(np.isfinite(value).all())
        raw = canonical_json_bytes(
            {
                "dtype": str(value.dtype),
                "shape": list(value.shape),
                "bytes": value.tobytes().hex(),
            }
        )
    except F8GRPOEvidenceGateError:
        raise
    except Exception as exc:
        raise F8GRPOEvidenceGateError("F8 named parameter cannot be decoded") from exc
    return _sha256(raw), all_finite


def _parameter_digest(manifest: F8TreeArtifact, parameter_name: str) -> str:
    digest, all_finite = _parameter_tensor_evidence(manifest, parameter_name)
    if not all_finite:
        raise F8GRPOEvidenceGateError("F8 named parameter contains nonfinite values")
    return digest


def _parquet_projection(path: str) -> tuple[list[dict[str, Any]], str]:
    try:
        import pandas as pd

        rows = json.loads(pd.read_parquet(path).to_json(orient="records"))
    except Exception as exc:
        raise F8GRPOEvidenceGateError(
            "F8 materialized dataset cannot be decoded"
        ) from exc
    raw = canonical_json_bytes(rows)
    return rows, _sha256(raw)


def _expected_train_rows(
    task: F8TaskArtifact,
    carrier_pairs: list[tuple[F8ImmutableJSONRef, F8RolloutCarrier]],
) -> list[dict[str, Any]]:
    by_task: dict[str, list[tuple[F8ImmutableJSONRef, F8RolloutCarrier]]] = {}
    for pair in carrier_pairs:
        by_task.setdefault(pair[1].task_row_id, []).append(pair)
    rows: list[dict[str, Any]] = []
    for task_row in task.train_rows:
        pairs = sorted(
            by_task.get(task_row.row_id, ()), key=lambda pair: pair[1].rollout_index
        )
        if not pairs:
            raise F8GRPOEvidenceGateError(
                "F8 task row has no expanded rollout carriers"
            )
        rows.append(
            {
                "data_source": task_row.data_source,
                "prompt": [item.model_dump(mode="json") for item in task_row.prompt],
                "reward_model": {
                    "style": "f8_pinned",
                    "ground_truth": task_row.ground_truth,
                },
                "extra_info": {
                    "task_row_id": task_row.row_id,
                    "f8_carrier_refs": [
                        ref.model_dump(mode="json") for ref, _ in pairs
                    ],
                    "f8_carrier_digests": [
                        carrier.carrier_digest for _, carrier in pairs
                    ],
                    "f8_optimizer_step": pairs[0][1].optimizer_step,
                },
            }
        )
    return rows


def _expected_val_rows(task: F8TaskArtifact) -> list[dict[str, Any]]:
    return [
        {
            "data_source": row.data_source,
            "prompt": [item.model_dump(mode="json") for item in row.prompt],
            "reward_model": {"style": "f8_pinned", "ground_truth": row.ground_truth},
            "extra_info": {"task_row_id": row.row_id, "split": "val"},
        }
        for row in task.val_rows
    ]


def build_container_probe_command(
    image: F8ImageArtifact,
    preflight: F8PreflightArtifact,
    config: F8ConfigArtifact,
) -> tuple[str, ...]:
    probe = (
        "import json,os,sys;"
        "print(json.dumps({"
        "'container_python_executable':os.path.realpath(sys.executable),"
        "'observed_image_reference':os.path.realpath("
        "os.environ.get('APPTAINER_CONTAINER') or "
        "os.environ.get('SINGULARITY_CONTAINER') or '')"
        "},sort_keys=True,separators=(',',':')))"
    )
    return (
        image.container_runtime_executable,
        "exec",
        "--cleanenv",
        "--containall",
        "--rocm",
        "--pwd",
        config.working_directory,
        "--bind",
        f"{config.working_directory}:{config.working_directory}:ro",
        image.image_reference,
        preflight.container_python_executable,
        "-c",
        probe,
    )


def build_trainer_command(
    image: F8ImageArtifact,
    preflight: F8PreflightArtifact,
    config: F8ConfigArtifact,
    task_artifact_path: str,
    verifier_artifact_path: str,
    verifier_source_path: str,
    checkpoint_root: str,
    train_path: str,
    val_path: str,
    reward_path: str,
    checkpoint_output_root: str,
    report_id: str,
) -> tuple[str, ...]:
    return (
        image.container_runtime_executable,
        "exec",
        "--cleanenv",
        "--containall",
        "--rocm",
        "--pwd",
        config.working_directory,
        "--bind",
        f"{config.working_directory}:{config.working_directory}:ro",
        "--bind",
        f"{checkpoint_root}:{checkpoint_root}:ro",
        "--bind",
        f"{task_artifact_path}:{task_artifact_path}:ro",
        "--bind",
        f"{verifier_artifact_path}:{verifier_artifact_path}:ro",
        "--bind",
        f"{verifier_source_path}:{verifier_source_path}:ro",
        "--bind",
        f"{Path(train_path).parent}:{Path(train_path).parent}:rw",
        "--bind",
        f"{Path(val_path).parent}:{Path(val_path).parent}:rw",
        "--bind",
        f"{Path(reward_path).parent}:{Path(reward_path).parent}:rw",
        "--bind",
        f"{checkpoint_output_root}:{checkpoint_output_root}:rw",
        "--env",
        f"HF_HOME={config.hf_home},PYTHONNOUSERSITE=1,PYTHONHASHSEED=0,TOKENIZERS_PARALLELISM=false,LC_ALL=C.UTF-8",
        image.image_reference,
        preflight.container_python_executable,
        "-m",
        config.trainer_entrypoint,
        f"data.train_files={train_path}",
        f"data.val_files={val_path}",
        f"data.train_batch_size={config.train_batch_size}",
        f"data.val_batch_size={config.val_batch_size}",
        f"data.max_prompt_length={config.max_prompt_length}",
        f"data.max_response_length={config.max_response_length}",
        "data.dataloader_num_workers=0",
        "data.filter_overlong_prompts=False",
        "data.truncation=right",
        f"actor_rollout_ref.model.path={checkpoint_root}",
        "+actor_rollout_ref.model.override_config.attn_implementation=eager",
        "actor_rollout_ref.model.trust_remote_code=False",
        f"actor_rollout_ref.rollout.name={config.rollout_name}",
        f"actor_rollout_ref.rollout.n={config.rollout_n}",
        "actor_rollout_ref.rollout.tensor_model_parallel_size=1",
        f"actor_rollout_ref.rollout.mode={config.rollout_mode}",
        "actor_rollout_ref.rollout.calculate_log_probs=True",
        "actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu=1",
        "actor_rollout_ref.ref.log_prob_micro_batch_size_per_gpu=1",
        f"actor_rollout_ref.actor.ppo_mini_batch_size={config.train_batch_size}",
        "actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1",
        "actor_rollout_ref.actor.fsdp_config.use_torch_compile=False",
        f"actor_rollout_ref.actor.optim.lr={config.actor_learning_rate}",
        "actor_rollout_ref.actor.use_kl_loss=True",
        "actor_rollout_ref.actor.kl_loss_type=k3",
        "reward_model.reward_manager=batch",
        f"custom_reward_function.path={reward_path}",
        "custom_reward_function.name=compute_score",
        f"reward_model.reward_kwargs.num_workers={config.reward_num_workers}",
        "algorithm.adv_estimator=grpo",
        "trainer.project_name=bb_phase5",
        f"trainer.experiment_name={report_id}",
        "trainer.nnodes=1",
        f"trainer.n_gpus_per_node={config.n_gpus_per_node}",
        f"trainer.total_epochs={config.total_training_steps}",
        f"trainer.total_training_steps={config.total_training_steps}",
        "actor_rollout_ref.actor.checkpoint.save_contents=[model,optimizer,extra,hf_model]",
        "trainer.save_freq=1",
        "trainer.test_freq=-1",
        "trainer.val_before_train=False",
        "trainer.logger=console",
        f"trainer.default_local_dir={checkpoint_output_root}",
    )


def _validate_authoritative_source(
    spec: F8GRPOEvidenceGateInput,
    runner_authority_key: bytes,
    runner_authority_key_path: str,
    expected_runner_authority_key_id: str,
    expected_runner_authority_key_digest: str,
) -> tuple[
    F8TargetSourceReport,
    F8TrainingIdentities,
    F8InputHashes,
    F8ConfigArtifact,
    tuple[F8EpisodeJoin, ...],
    F8LearningEvidence,
    F8EvidenceJoin,
    F8CheckpointUpdate,
    F8CleanupEvidence,
]:
    source = _read_model(
        spec.target_source_report, F8TargetSourceReport, "target source report"
    )
    assert isinstance(source, F8TargetSourceReport)
    if not source.passed or source.target != spec.target or source.identities is None:
        raise F8GRPOEvidenceGateError(
            "F8 target source report is not a passing report for this target"
        )
    if source.report_path != spec.target_source_report.path:
        raise F8GRPOEvidenceGateError(
            "F8 target source report path is not self-identical"
        )
    try:
        from scripts.rl_phase3.target_verl_smoke_train import F8TargetTrainingInput
    except Exception as exc:
        raise F8GRPOEvidenceGateError(
            "F8 canonical target input contract is unavailable"
        ) from exc
    target_input = _read_model(
        source.input_ref, F8TargetTrainingInput, "canonical target input"
    )
    if (
        target_input.target.model_dump(mode="json")
        != source.requested_target.model_dump(mode="json")
        or target_input.identity_artifacts.model_dump(mode="json")
        != source.identity_artifacts.model_dump(mode="json")
        or target_input.report_id != source.report_id
        or target_input.runner_authority_key_id != expected_runner_authority_key_id
        or target_input.runner_authority_key_digest
        != expected_runner_authority_key_digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 source report is not derived from its canonical target input"
        )
    identities = source.identities
    source.input_hashes.require_identity_join(identities)
    refs = source.identity_artifacts
    ref_values = (
        refs.config,
        refs.task,
        refs.model,
        refs.tokenizer,
        refs.input_checkpoint,
        refs.verifier,
        refs.image,
        refs.preflight,
    )
    if tuple(ref.digest for ref in ref_values) != (
        identities.config_digest,
        identities.task_digest,
        identities.model_digest,
        identities.tokenizer_digest,
        identities.input_checkpoint_digest,
        identities.verifier_digest,
        identities.image_digest,
        identities.preflight_digest,
    ):
        raise F8GRPOEvidenceGateError("F8 source identity refs do not join identities")
    config = _read_model(refs.config, F8ConfigArtifact, "config artifact")
    task = _read_model(refs.task, F8TaskArtifact, "task artifact")
    model = _read_model(refs.model, F8TreeArtifact, "model tree artifact")
    tokenizer = _read_model(refs.tokenizer, F8TreeArtifact, "tokenizer tree artifact")
    before = _read_model(
        refs.input_checkpoint, F8TreeArtifact, "input checkpoint tree artifact"
    )
    verifier = _read_model(refs.verifier, F8VerifierArtifact, "verifier artifact")
    image = _read_model(refs.image, F8ImageArtifact, "image artifact")
    payload = _read_model(
        config.payload_manifest, F8PayloadArtifact, "VeRL payload manifest"
    )
    preflight = _read_model(refs.preflight, F8PreflightArtifact, "preflight artifact")
    assert isinstance(config, F8ConfigArtifact)
    assert isinstance(task, F8TaskArtifact)
    assert isinstance(model, F8TreeArtifact)
    assert isinstance(tokenizer, F8TreeArtifact)
    assert isinstance(before, F8TreeArtifact)
    assert isinstance(verifier, F8VerifierArtifact)
    assert isinstance(image, F8ImageArtifact)
    assert isinstance(payload, F8PayloadArtifact)
    assert isinstance(preflight, F8PreflightArtifact)
    if len(task.train_rows) != config.train_batch_size * config.total_training_steps:
        raise F8GRPOEvidenceGateError(
            "F8 task cardinality does not equal batches consumed by bounded VeRL steps"
        )
    _validate_payload(payload, "VeRL payload")
    if (
        Path(config.working_directory).resolve(strict=True)
        != Path(payload.root).resolve(strict=True)
        or payload.entrypoint_relative_path != config.trainer_module_relative_path
        or payload.reward_loader_relative_path != config.reward_loader_relative_path
    ):
        raise F8GRPOEvidenceGateError(
            "F8 launched VeRL module is outside the exact pinned payload"
        )
    module_path = (Path(payload.root) / config.trainer_module_relative_path).resolve(
        strict=True
    )
    reward_loader_path = (
        Path(payload.root) / config.reward_loader_relative_path
    ).resolve(strict=True)
    if (
        Path(payload.root).resolve(strict=True) not in module_path.parents
        or Path(payload.root).resolve(strict=True) not in reward_loader_path.parents
        or _sha256(module_path.read_bytes()) != config.trainer_module_digest
        or _sha256(reward_loader_path.read_bytes()) != config.reward_loader_digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 VeRL entrypoint/reward-loader bytes differ from pinned payload"
        )
    for manifest, label in (
        (model, "model"),
        (tokenizer, "tokenizer"),
        (before, "input checkpoint"),
    ):
        _validate_tree(manifest, label)
    if (
        model.root != before.root
        or tokenizer.root != before.root
        or before.format != "transformers"
    ):
        raise F8GRPOEvidenceGateError(
            "F8 model/tokenizer/checkpoint paths are not the VeRL checkpoint root"
        )
    before_entries = {entry.relative_path: entry.digest for entry in before.entries}
    for manifest in (model, tokenizer):
        if any(
            before_entries.get(entry.relative_path) != entry.digest
            for entry in manifest.entries
        ):
            raise F8GRPOEvidenceGateError(
                "F8 model/tokenizer bytes are not contained in input checkpoint"
            )
    _read_blob(verifier.source, "verifier source")
    _validate_image_and_runtime(image)
    if (
        preflight.container_runtime_executable != image.container_runtime_executable
        or preflight.container_runtime_digest != image.container_runtime_digest
        or preflight.container_python_executable != image.container_python_executable
        or preflight.payload_digest != config.payload_manifest.digest
        or preflight.image_reference != image.image_reference
        or preflight.image_digest != image.immutable_image_digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 preflight runtime/payload/image identity mismatch"
        )
    if preflight.observed_environment.get("SLURM_JOB_ID") != source.target.job_id:
        raise F8GRPOEvidenceGateError(
            "F8 source lacks authoritative Slurm target observation"
        )

    runtime = _read_model(
        source.artifacts.observed_runtime,
        F8ObservedRuntimeManifest,
        "observed runtime manifest",
    )
    assert isinstance(runtime, F8ObservedRuntimeManifest)
    if (
        runtime.target != source.target
        or runtime.input_ref != source.input_ref
        or runtime.identity_artifacts != refs
    ):
        raise F8GRPOEvidenceGateError("F8 observed runtime identity closure mismatch")
    container_observation = _read_model(
        source.artifacts.container_observation,
        F8ContainerObservationReceipt,
        "container observation receipt",
    )
    assert isinstance(container_observation, F8ContainerObservationReceipt)
    expected_probe_command = build_container_probe_command(image, preflight, config)
    if (
        runtime.container_observation_ref != source.artifacts.container_observation
        or container_observation.target != source.target
        or container_observation.input_hashes != source.input_hashes
        or container_observation.parent_process_id != runtime.controller_pid
        or container_observation.probe_process_id == runtime.trainer_pid
        or container_observation.command != expected_probe_command
        or container_observation.container_runtime_executable
        != image.container_runtime_executable
        or container_observation.container_runtime_digest
        != image.container_runtime_digest
        or container_observation.container_python_executable
        != image.container_python_executable
        or container_observation.observed_image_reference != image.image_reference
        or container_observation.observed_image_digest != image.immutable_image_digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 executable container observation receipt mismatch"
        )
    if (
        runtime.container_runtime_executable != image.container_runtime_executable
        or runtime.container_runtime_digest != image.container_runtime_digest
        or runtime.container_python_executable != image.container_python_executable
        or runtime.working_directory != config.working_directory
        or runtime.payload_manifest != config.payload_manifest
        or runtime.payload_digest != config.payload_manifest.digest
        or runtime.requested_image_reference != image.image_reference
        or runtime.effective_image_reference != image.image_reference
        or runtime.observed_image_reference != image.image_reference
        or runtime.requested_image_digest != image.immutable_image_digest
        or runtime.effective_image_digest != image.immutable_image_digest
        or runtime.observed_image_digest != image.immutable_image_digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 requested/effective/observed runtime payload or image differs from authority"
        )
    expected_env = dict(preflight.observed_environment)
    if runtime.child_environment != expected_env:
        raise F8GRPOEvidenceGateError(
            "F8 child environment is not the minimal scheduler/GPU allowlist"
        )
    expected_command = build_trainer_command(
        image,
        preflight,
        config,
        refs.task.path,
        refs.verifier.path,
        verifier.source.path,
        before.root,
        runtime.train_dataset.path,
        runtime.val_dataset.path,
        runtime.reward_adapter.path,
        runtime.output_checkpoint_root,
        source.report_id,
    )
    if runtime.command != expected_command:
        raise F8GRPOEvidenceGateError(
            "F8 launched command does not derive exactly from pinned artifacts"
        )
    for ref, label in (
        (runtime.train_dataset, "materialized train dataset"),
        (runtime.val_dataset, "materialized validation dataset"),
        (runtime.reward_adapter, "reward adapter"),
        (source.artifacts.stdout, "trainer stdout"),
        (source.artifacts.stderr, "trainer stderr"),
    ):
        _read_blob(ref, label)

    expected_carrier_count = len(task.train_rows) * config.rollout_n
    if (
        len(task.train_rows) != config.train_batch_size * config.total_training_steps
        or source.expected_sample_count != expected_carrier_count
    ):
        raise F8GRPOEvidenceGateError(
            "F8 task/batch/rollout cardinality does not match actual VeRL expansion"
        )
    carrier_manifest = _read_model(
        source.artifacts.carrier_manifest, F8CarrierManifest, "carrier manifest"
    )
    rollout = _read_model(
        source.artifacts.rollout_manifest, F8RolloutEvidenceManifest, "rollout manifest"
    )
    assert isinstance(carrier_manifest, F8CarrierManifest)
    assert isinstance(rollout, F8RolloutEvidenceManifest)
    if (
        carrier_manifest.target != source.target
        or rollout.target != source.target
        or carrier_manifest.input_hashes != source.input_hashes
        or rollout.input_hashes != source.input_hashes
        or rollout.carrier_manifest != source.artifacts.carrier_manifest
        or carrier_manifest.rollout_n != config.rollout_n
        or carrier_manifest.expected_sample_count != source.expected_sample_count
        or rollout.generated_sample_count != source.expected_sample_count
        or len(carrier_manifest.carriers) != source.expected_sample_count
        or len(rollout.records) != source.expected_sample_count
        or len(rollout.dispositions) != source.expected_sample_count
    ):
        raise F8GRPOEvidenceGateError("F8 carrier/rollout manifest mismatch")
    task_ids = {row.row_id for row in task.train_rows}
    carrier_by_ref: dict[tuple[str, str], F8RolloutCarrier] = {}
    carrier_digests: set[str] = set()
    carrier_pairs: list[tuple[F8ImmutableJSONRef, F8RolloutCarrier]] = []
    expected_carrier_slots = {
        (
            row.row_id,
            row_index // config.train_batch_size + 1,
            rollout_index,
        )
        for row_index, row in enumerate(task.train_rows)
        for rollout_index in range(config.rollout_n)
    }
    observed_carrier_slots: set[tuple[str, int, int]] = set()
    for index, ref in enumerate(carrier_manifest.carriers):
        carrier = _read_model(ref, F8RolloutCarrier, f"carrier {index}")
        assert isinstance(carrier, F8RolloutCarrier)
        carrier.require_join(source.target, identities)
        payload = carrier.model_dump(mode="json", exclude={"carrier_digest"})
        if (
            _sha256(canonical_json_bytes(payload)) != carrier.carrier_digest
            or carrier.task_row_id not in task_ids
        ):
            raise F8GRPOEvidenceGateError(
                "F8 carrier is not derived from a pinned task sample"
            )
        key = (ref.path, ref.digest)
        if key in carrier_by_ref or carrier.carrier_digest in carrier_digests:
            raise F8GRPOEvidenceGateError("F8 carrier is duplicated")
        carrier_by_ref[key] = carrier
        carrier_digests.add(carrier.carrier_digest)
        carrier_pairs.append((ref, carrier))
        slot = (
            carrier.task_row_id,
            carrier.optimizer_step,
            carrier.rollout_index,
        )
        if slot in observed_carrier_slots:
            raise F8GRPOEvidenceGateError("F8 expanded carrier slot is duplicated")
        observed_carrier_slots.add(slot)
    if observed_carrier_slots != expected_carrier_slots:
        raise F8GRPOEvidenceGateError(
            "F8 carriers do not exactly cover VeRL rollout.n expansion"
        )
    train_rows, train_projection = _parquet_projection(runtime.train_dataset.path)
    val_rows, val_projection = _parquet_projection(runtime.val_dataset.path)
    if (
        train_rows != _expected_train_rows(task, carrier_pairs)
        or val_rows != _expected_val_rows(task)
        or runtime.train_projection_digest != train_projection
        or runtime.val_projection_digest != val_projection
    ):
        raise F8GRPOEvidenceGateError(
            "F8 materialized dataset is not exactly derived from task/carrier bytes"
        )
    try:
        from scripts.rl_phase3.target_verl_smoke_train import (
            _build_reload_command,
            _reload_harness_sources,
            _reward_source,
        )
    except Exception as exc:
        raise F8GRPOEvidenceGateError(
            "F8 reward adapter constructor is unavailable"
        ) from exc
    expected_reward = _reward_source(
        refs.task,
        source.artifacts.carrier_manifest,
        verifier,
        Path(runtime.record_root),
        Path(runtime.claim_root),
        Path(runtime.disposition_root),
    ).encode()
    if _read_blob(runtime.reward_adapter, "reward adapter") != expected_reward:
        raise F8GRPOEvidenceGateError(
            "F8 reward adapter is not exactly derived from verifier/carrier bytes"
        )

    records: list[F8RolloutSampleRecord] = []
    episode_rows: list[F8EpisodeJoin] = []
    claimed: set[tuple[str, str]] = set()
    claimant_threads: set[tuple[int, int]] = set()
    rewards: list[float] = []
    if len(rollout.dispositions) != len(rollout.records):
        raise F8GRPOEvidenceGateError("F8 rollout dispositions are incomplete")
    for index, (ref, disposition_ref) in enumerate(
        zip(rollout.records, rollout.dispositions, strict=True)
    ):
        record = _read_model(ref, F8RolloutSampleRecord, f"rollout record {index}")
        assert isinstance(record, F8RolloutSampleRecord)
        key = (record.carrier_ref.path, record.carrier_ref.digest)
        carrier = carrier_by_ref.get(key)
        claim = _read_model(record.claim_ref, F8CarrierClaim, f"carrier claim {index}")
        disposition = _read_model(
            disposition_ref, F8CarrierDisposition, f"carrier disposition {index}"
        )
        assert isinstance(claim, F8CarrierClaim)
        assert isinstance(disposition, F8CarrierDisposition)
        if (
            carrier is None
            or key in claimed
            or record.rollout_carrier != carrier
            or record.target != source.target
            or record.input_hashes != source.input_hashes
            or record.verifier_digest != identities.verifier_digest
            or claim.carrier_ref != record.carrier_ref
            or claim.carrier_digest != carrier.carrier_digest
            or disposition.carrier_ref != record.carrier_ref
            or disposition.claim_ref != record.claim_ref
            or disposition.record_ref != ref
        ):
            raise F8GRPOEvidenceGateError(
                "F8 rollout record does not have an exact durable carrier transition"
            )
        claimed.add(key)
        claimant_threads.add((claim.claimant_pid, claim.claimant_thread_id))
        records.append(record)
        rewards.append(record.reward)
        episode_rows.append(
            F8EpisodeJoin(
                episode_id=carrier.episode_id,
                attempt_id=carrier.attempt_id,
                identities=identities,
                rollout_carrier=carrier,
                generated_sample_count=1,
                joined_sample_count=1,
                reward_min=record.reward,
                reward_max=record.reward,
                evidence_digest=ref.digest,
            )
        )
    if claimed != set(carrier_by_ref):
        raise F8GRPOEvidenceGateError(
            "F8 missing or partial carrier claims are unusable"
        )
    if len(claimant_threads) < config.reward_num_workers:
        raise F8GRPOEvidenceGateError(
            "F8 configured parallel reward workers were not observed claiming carriers"
        )
    expected = tuple(
        F8ExpectedEpisodeJoin(
            episode_id=row.episode_id,
            attempt_id=row.attempt_id,
            rollout_carrier_digest=row.rollout_carrier.carrier_digest,
        )
        for row in episode_rows
    )
    if expected != spec.expected_episode_joins:
        raise F8GRPOEvidenceGateError(
            "F8 expected episode/carrier identity join mismatch"
        )

    metrics = _read_model(
        source.artifacts.trainer_metrics_manifest,
        F8TrainerMetricsManifest,
        "metrics manifest",
    )
    assert isinstance(metrics, F8TrainerMetricsManifest)
    stdout = _read_blob(metrics.stdout_ref, "raw trainer stdout")
    if (
        metrics.stdout_ref != source.artifacts.stdout
        or metrics.target != source.target
        or metrics.input_hashes != source.input_hashes
    ):
        raise F8GRPOEvidenceGateError("F8 trainer metrics lineage mismatch")
    raw_step_lines = [
        line
        for line in stdout.decode("utf-8", errors="strict").splitlines()
        if re.search(r"(?:^| )step:[0-9]+ - ", line)
    ]
    if len(raw_step_lines) != len(metrics.records):
        raise F8GRPOEvidenceGateError("F8 raw stdout step cardinality mismatch")
    steps: list[F8TrainerStepRecord] = []
    for index, (ref, raw_line) in enumerate(
        zip(metrics.records, raw_step_lines, strict=True)
    ):
        step = _read_model(ref, F8TrainerStepRecord, f"trainer step {index}")
        assert isinstance(step, F8TrainerStepRecord)
        parsed = _parse_trainer_step_line(raw_line)
        observed = {key: getattr(step, key) for key in parsed}
        if (
            step.target != source.target
            or step.input_hashes != source.input_hashes
            or step.raw_line != raw_line
            or observed != parsed
        ):
            raise F8GRPOEvidenceGateError(
                "F8 trainer step metrics do not exactly reparse from raw VeRL stdout"
            )
        steps.append(step)
    expected_steps = tuple(range(1, config.total_training_steps + 1))
    if tuple(step.optimizer_step for step in steps) != expected_steps:
        raise F8GRPOEvidenceGateError("F8 optimizer steps are incomplete or reordered")
    optimizer_manifest = _read_model(
        source.artifacts.optimizer_steps_manifest,
        F8OptimizerStepsManifest,
        "optimizer steps manifest",
    )
    assert isinstance(optimizer_manifest, F8OptimizerStepsManifest)
    if (
        optimizer_manifest.target != source.target
        or optimizer_manifest.input_hashes != source.input_hashes
        or len(optimizer_manifest.records) != len(steps)
    ):
        raise F8GRPOEvidenceGateError("F8 optimizer step receipt lineage mismatch")
    optimizer_receipts: list[F8OptimizerStepReceipt] = []
    previous_parameter = _parameter_digest(before, config.changed_parameter_name)
    optimizer_state_digests: set[str] = set()
    for index, ref in enumerate(optimizer_manifest.records, 1):
        receipt_step = _read_model(
            ref, F8OptimizerStepReceipt, f"optimizer checkpoint receipt {index}"
        )
        assert isinstance(receipt_step, F8OptimizerStepReceipt)
        checkpoint_step = _read_model(
            receipt_step.checkpoint_ref,
            F8TreeArtifact,
            f"optimizer checkpoint tree {index}",
        )
        assert isinstance(checkpoint_step, F8TreeArtifact)
        _validate_tree(checkpoint_step, f"optimizer checkpoint {index}")
        checkpoint_step_root = Path(checkpoint_step.root).resolve(strict=True)
        runtime_checkpoint_root = Path(runtime.output_checkpoint_root).resolve(
            strict=True
        )
        if (
            checkpoint_step_root != runtime_checkpoint_root
            and runtime_checkpoint_root not in checkpoint_step_root.parents
        ):
            raise F8GRPOEvidenceGateError(
                "F8 optimizer checkpoint is outside the launched VeRL output root"
            )
        parameter = _parameter_digest(checkpoint_step, config.changed_parameter_name)
        state_digest = _sha256(
            canonical_json_bytes(
                sorted(
                    _read_blob(state_ref, f"optimizer state {index}").hex()
                    for state_ref in receipt_step.optimizer_state_refs
                )
            )
        )
        if (
            receipt_step.target != source.target
            or receipt_step.input_hashes != source.input_hashes
            or receipt_step.optimizer_step != index
            or receipt_step.parameter_name != config.changed_parameter_name
            or receipt_step.parameter_digest != parameter
            or parameter == previous_parameter
            or state_digest in optimizer_state_digests
        ):
            raise F8GRPOEvidenceGateError(
                "F8 optimizer step is skipped, duplicated, or not checkpoint-derived"
            )
        previous_parameter = parameter
        optimizer_state_digests.add(state_digest)
        optimizer_receipts.append(receipt_step)
    learning = F8LearningEvidence(
        run_kind="bounded",
        optimizer_step_count=len(steps),
        generated_sample_count=len(records),
        reward_min=min(rewards),
        reward_max=max(rewards),
        advantage_abs_max=max(
            max(abs(step.advantage_min), abs(step.advantage_max)) for step in steps
        ),
        actor_gradient_norm=max(step.actor_gradient_norm for step in steps),
        learning_rate=max(step.learning_rate for step in steps),
        optimizer_step_skipped=False,
        optimizer_update_finite=True,
        aborted_ratio=max(step.aborted_ratio for step in steps),
        dropped_stale_samples=len(carrier_by_ref) - len(claimed),
        actor_ppo_kl=max(step.actor_ppo_kl for step in steps),
        actor_k3_kl=max(step.actor_k3_kl for step in steps),
        required_kl_metrics_present=True,
    )

    reload = _read_model(
        source.artifacts.checkpoint_reload,
        F8CheckpointReloadEvidence,
        "checkpoint reload evidence",
    )
    assert isinstance(reload, F8CheckpointReloadEvidence)
    after = _read_model(
        reload.checkpoint_after_ref, F8TreeArtifact, "output checkpoint tree artifact"
    )
    receipt = _read_model(reload.reload_receipt_ref, F8ReloadReceipt, "reload receipt")
    request = _read_model(reload.reload_request_ref, F8ReloadRequest, "reload request")
    assert isinstance(after, F8TreeArtifact)
    assert isinstance(receipt, F8ReloadReceipt)
    assert isinstance(request, F8ReloadRequest)
    _validate_tree(after, "output checkpoint")
    before_parameter = _parameter_digest(before, config.changed_parameter_name)
    after_parameter = _parameter_digest(after, config.changed_parameter_name)
    reload_harness = _read_model(
        source.artifacts.reload_harness,
        F8ReloadHarnessManifest,
        "reload harness manifest",
    )
    assert isinstance(reload_harness, F8ReloadHarnessManifest)
    harness_root = Path(reload_harness.root).resolve(strict=True)
    expected_harness_sources = _reload_harness_sources()
    expected_harness_entries = {
        relative: (len(raw), _sha256(raw))
        for relative, raw in expected_harness_sources.items()
    }
    observed_harness_entries = {
        entry.relative_path: (entry.size, entry.digest)
        for entry in reload_harness.entries
    }
    actual_harness_paths = {
        str(path.relative_to(harness_root))
        for path in harness_root.rglob("*")
        if path.is_file()
    }
    if (
        observed_harness_entries != expected_harness_entries
        or actual_harness_paths != set(expected_harness_entries)
        or any(
            (harness_root / relative).read_bytes() != raw
            for relative, raw in expected_harness_sources.items()
        )
    ):
        raise F8GRPOEvidenceGateError(
            "F8 reload harness/import tree differs from reviewed bytes"
        )
    reload_target_script = str(
        harness_root / reload_harness.target_script_relative_path
    )
    expected_reload_command = _build_reload_command(
        image,
        preflight,
        config,
        target_script=reload_target_script,
        run_root=str(Path(source.report_path).resolve().parent),
        config_ref_path=refs.config.path,
        verifier_ref_path=refs.verifier.path,
        verifier_source_path=verifier.source.path,
        reload_request_path=reload.reload_request_ref.path,
    )
    if (
        reload.target != source.target
        or reload.input_hashes != source.input_hashes
        or reload.checkpoint_before_ref != refs.input_checkpoint
        or reload.checkpoint_after_ref.digest != identities.output_checkpoint_digest
        or reload.changed_parameter_name != config.changed_parameter_name
        or reload.parameter_before_digest != before_parameter
        or reload.parameter_after_digest != after_parameter
        or reload.trainer_pid != runtime.trainer_pid
        or receipt.request_ref != reload.reload_request_ref
        or request.checkpoint_ref != reload.checkpoint_after_ref
        or request.model_ref != refs.model
        or request.tokenizer_ref != refs.tokenizer
        or request.config_ref != refs.config
        or request.verifier_ref != refs.verifier
        or request.parameter_name != reload.changed_parameter_name
        or request.output_path != reload.reload_receipt_ref.path
        or receipt.process_id != reload.reload_pid
        or receipt.parent_process_id != runtime.controller_pid
        or receipt.checkpoint_ref != reload.checkpoint_after_ref
        or receipt.model_ref != refs.model
        or receipt.tokenizer_ref != refs.tokenizer
        or receipt.config_ref != refs.config
        or receipt.parameter_name != reload.changed_parameter_name
        or receipt.parameter_digest != after_parameter
        or reload.reload_command != expected_reload_command
        or reload.reload_harness_ref != source.artifacts.reload_harness
        or receipt.inference_output_sha256 != reload.deterministic_inference_digest
        or receipt.verifier_reward != reload.verifier_reward
        or reload.optimizer_update_digest
        != source.artifacts.optimizer_steps_manifest.digest
        or optimizer_receipts[-1].checkpoint_ref != reload.checkpoint_after_ref
        or optimizer_receipts[-1].parameter_digest != after_parameter
    ):
        raise F8GRPOEvidenceGateError(
            "F8 fresh reload/tensor/inference receipt mismatch"
        )
    checkpoint_update = F8CheckpointUpdate(
        checkpoint_before_digest=refs.input_checkpoint.digest,
        checkpoint_after_digest=reload.checkpoint_after_ref.digest,
        changed_parameter_name=reload.changed_parameter_name,
        parameter_before_digest=before_parameter,
        parameter_after_digest=after_parameter,
        optimizer_update_digest=reload.optimizer_update_digest,
        retained_checkpoint_digest=reload.checkpoint_after_ref.digest,
        fresh_process_reload=True,
        reload_model_digest=refs.model.digest,
        reload_config_digest=refs.config.digest,
        reload_tokenizer_digest=refs.tokenizer.digest,
        changed_parameter_reverified=True,
        bounded_inference_or_preflight_passed=True,
    )

    terminal = _read_model(
        source.artifacts.terminal_lifecycle,
        F8TerminalLifecycleRecord,
        "terminal lifecycle",
    )
    assert isinstance(terminal, F8TerminalLifecycleRecord)
    if (
        terminal.target != source.target
        or terminal.terminal_state != "closed"
        or terminal.trainer_pid != runtime.trainer_pid
        or terminal.trainer_pgid != runtime.trainer_pgid
        or terminal.retained_checkpoint_ref != reload.checkpoint_after_ref
    ):
        raise F8GRPOEvidenceGateError("F8 executable cleanup receipt mismatch")
    cleanup = F8CleanupEvidence(
        terminal_state="closed",
        active_lease_ids=terminal.active_lease_ids,
        remaining_process_ids=terminal.remaining_process_ids,
        remaining_container_ids=terminal.remaining_container_ids,
        cleanup_errors=terminal.cleanup_errors,
        failed_outputs_quarantined=True,
        failed_checkpoints_quarantined=True,
        retained_checkpoint_present=True,
    )
    evidence_join = F8EvidenceJoin(
        generated_sample_count=len(records),
        joined_sample_count=len(records),
        unmatched_sample_count=0,
        duplicate_join_count=0,
        carrier_alignment_exact=True,
        episode_attempt_alignment_exact=True,
        evidence_manifest_digest=source.artifacts.rollout_manifest.digest,
    )
    target_runner = _read_model(
        spec.target_runner_receipt,
        F8TargetRunnerReceipt,
        "external target runner execution receipt",
    )
    assert isinstance(target_runner, F8TargetRunnerReceipt)
    authority_key_path = Path(_absolute(runner_authority_key_path)).resolve(strict=True)
    protected_roots = (
        Path(source.report_path).resolve().parent,
        Path(config.working_directory).resolve(),
        Path(before.root).resolve(),
        Path(runtime.output_checkpoint_root).resolve(),
    )
    if any(
        authority_key_path == root or root in authority_key_path.parents
        for root in protected_roots
    ):
        raise F8GRPOEvidenceGateError(
            "F8 runner authority key is inside target-visible storage"
        )
    if target_runner.authority_key_id != _identifier(
        expected_runner_authority_key_id
    ) or target_runner.authority_key_digest != _digest(
        expected_runner_authority_key_digest
    ):
        raise F8GRPOEvidenceGateError("F8 runner receipt uses an unapproved authority")
    if (
        len(runner_authority_key) < 32
        or _sha256(runner_authority_key) != expected_runner_authority_key_digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 external target runner authority key mismatch"
        )
    runner_payload = target_runner.model_dump(
        mode="json", exclude={"authority_signature"}
    )
    expected_runner_signature = (
        "sha256:"
        + hmac.new(
            runner_authority_key,
            canonical_json_bytes(runner_payload),
            hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(
        expected_runner_signature, target_runner.authority_signature
    ):
        raise F8GRPOEvidenceGateError("F8 external target runner signature is invalid")
    if (
        target_runner.source_report_ref != spec.target_source_report
        or target_runner.target != source.target
        or target_runner.input_ref != source.input_ref
        or target_runner.input_hashes != source.input_hashes
        or target_runner.slurm_job_id != source.target.job_id
        or target_runner.runtime_ref != source.artifacts.observed_runtime
        or target_runner.container_observation_ref
        != source.artifacts.container_observation
        or target_runner.callback_record_refs != rollout.records
        or target_runner.callback_disposition_refs != rollout.dispositions
        or target_runner.trainer_step_refs != metrics.records
        or target_runner.optimizer_step_refs != optimizer_manifest.records
        or target_runner.checkpoint_reload_ref != source.artifacts.checkpoint_reload
        or target_runner.terminal_lifecycle_ref != source.artifacts.terminal_lifecycle
        or target_runner.trainer_pid != runtime.trainer_pid
        or target_runner.trainer_pgid != runtime.trainer_pgid
        or target_runner.reload_pid != reload.reload_pid
        or target_runner.trainer_returncode != terminal.trainer_returncode
        or target_runner.command != runtime.command
        or target_runner.completed_at != source.completed_at
        or target_runner.wrapper_source_digest
        != _sha256(
            (
                Path(__file__).resolve().parents[1]
                / "rl_phase3"
                / "run_verl_trainer_update.py"
            ).read_bytes()
        )
        or target_runner.target_source_digest
        != _sha256(
            expected_harness_sources["scripts/rl_phase3/target_verl_smoke_train.py"]
        )
        or target_runner.gate_source_digest != _sha256(Path(__file__).read_bytes())
        or target_runner.reload_harness_manifest_digest
        != source.artifacts.reload_harness.digest
    ):
        raise F8GRPOEvidenceGateError(
            "F8 target runner receipt is not callback/execution-derived"
        )
    if source.observed_sample_record_count != len(
        records
    ) or source.observed_optimizer_metric_record_count != len(steps):
        raise F8GRPOEvidenceGateError(
            "F8 source report counts are not raw-artifact-derived"
        )
    return (
        source,
        identities,
        source.input_hashes,
        config,
        tuple(episode_rows),
        learning,
        evidence_join,
        checkpoint_update,
        cleanup,
    )


def _read_input(path: str) -> tuple[F8GRPOEvidenceGateInput, str]:
    raw = Path(_absolute(path)).resolve(strict=True).read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F8GRPOEvidenceGateError("F8 gate input is not canonical JSON")
    return (
        F8GRPOEvidenceGateInput.model_validate_json(raw, strict=True),
        _sha256(raw),
    )


def _exclusive_write(path: Path, value: BaseModel) -> None:
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        if os.write(fd, raw) != len(raw):
            raise RuntimeError("short report write")
        os.fsync(fd)
    finally:
        os.close(fd)


def run_f8_grpo_evidence_gate(
    spec: F8GRPOEvidenceGateInput,
    input_digest: str,
    *,
    output_path: str,
    completed_at: str,
    runner_authority_key: bytes,
    runner_authority_key_path: str,
    expected_runner_authority_key_id: str,
    expected_runner_authority_key_digest: str,
) -> F8GRPOEvidenceGateReport:
    destination = Path(_absolute(output_path))
    destination.unlink(missing_ok=True)
    (
        source,
        identities,
        input_hashes,
        config,
        episodes,
        learning,
        evidence_join,
        checkpoint_update,
        cleanup,
    ) = _validate_authoritative_source(
        spec,
        runner_authority_key,
        runner_authority_key_path,
        expected_runner_authority_key_id,
        expected_runner_authority_key_digest,
    )
    report = F8GRPOEvidenceGateReport(
        schema_version="bb.rl.phase5-f8-grpo-evidence-gate-report.v3",
        component="f8_grpo_evidence_gate",
        report_id=f"{spec.gate_id}-report",
        passed=True,
        blocked_reason="",
        input_digest=input_digest,
        gate_id=spec.gate_id,
        target=spec.target,
        identities=identities,
        input_hashes=input_hashes,
        trainer_backend="verl_grpo",
        algorithm_adv_estimator="grpo",
        estimator_label="grpo",
        rollout_n=config.rollout_n,
        learning_evidence=learning,
        episode_joins=episodes,
        evidence_join=evidence_join,
        checkpoint_update=checkpoint_update,
        cleanup=cleanup,
        target_source_report=spec.target_source_report,
        completed_at=completed_at,
        claim_scope=("finite_step_optimizer_signal_not_convergence_or_benchmark_gain"),
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_authority=False,
        scorecard_update_allowed=False,
    )
    _exclusive_write(destination, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate source-closed real VeRL GRPO evidence"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--completed-at", required=True)
    parser.add_argument("--runner-authority-key-file", required=True)
    parser.add_argument("--expected-runner-authority-key-id", required=True)
    parser.add_argument("--expected-runner-authority-key-sha256", required=True)
    args = parser.parse_args()
    spec, digest = _read_input(args.input)
    runner_authority_key = Path(_absolute(args.runner_authority_key_file)).read_bytes()
    report = run_f8_grpo_evidence_gate(
        spec,
        digest,
        output_path=args.output,
        completed_at=args.completed_at,
        runner_authority_key=runner_authority_key,
        runner_authority_key_path=args.runner_authority_key_file,
        expected_runner_authority_key_id=(args.expected_runner_authority_key_id),
        expected_runner_authority_key_digest=(
            args.expected_runner_authority_key_sha256
        ),
    )
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    if Path(args.output).read_bytes() != raw:
        raise RuntimeError("persisted F8 gate report mismatch")
    os.write(1, b"PHASE3_COMPONENT_REPORT_JSON=" + raw + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "F8CarrierClaim",
    "F8CarrierDisposition",
    "F8CarrierManifest",
    "F8CheckpointReloadEvidence",
    "F8CheckpointUpdate",
    "F8CleanupEvidence",
    "F8ConfigArtifact",
    "F8ContainerObservationReceipt",
    "F8EvidenceJoin",
    "F8ExpectedEpisodeJoin",
    "F8FileEntry",
    "F8GRPOEvidenceGateError",
    "F8GRPOEvidenceGateInput",
    "F8GRPOEvidenceGateReport",
    "F8IdentityArtifactRefs",
    "F8ImageArtifact",
    "F8ImmutableBlobRef",
    "F8ImmutableJSONRef",
    "F8InputHashes",
    "F8LearningEvidence",
    "F8ObservedRuntimeManifest",
    "F8OptimizerStepReceipt",
    "F8OptimizerStepsManifest",
    "F8PayloadArtifact",
    "F8PreflightArtifact",
    "F8ReloadReceipt",
    "F8ReloadRequest",
    "F8RolloutCarrier",
    "F8RolloutEvidenceManifest",
    "F8RolloutSampleRecord",
    "F8TargetIdentity",
    "F8TargetSourceArtifacts",
    "F8TargetSourceReport",
    "F8TaskArtifact",
    "F8TaskRow",
    "F8TerminalLifecycleRecord",
    "F8TrainerMetricsManifest",
    "F8TrainerStepRecord",
    "F8TrainingIdentities",
    "F8TreeArtifact",
    "F8VerifierArtifact",
    "build_container_probe_command",
    "build_trainer_command",
    "run_f8_grpo_evidence_gate",
]
