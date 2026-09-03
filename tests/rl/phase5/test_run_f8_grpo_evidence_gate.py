from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import importlib.util
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from breadboard_engine.compilation.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from safetensors.numpy import save_file

from scripts.rl_phase3.run_verl_trainer_update import main as wrapper_main
from scripts.rl_phase3.target_verl_smoke_train import (
    F8TargetTrainingInput,
    _build_reload_command,
    _reload_harness_sources,
    _write_reload_harness,
    _effective_target_spec,
    _input_hashes,
    _load_authority,
    _materialize_datasets,
    _minimal_child_environment,
    _preauthorize_carriers,
    _read_f8_input,
    _reward_source,
    _run_f8_source_closed,
    _run_bounded_process,
    _terminate_process_group,
    _verified_rollout_refs,
)
from scripts.rl_phase5.run_f8_grpo_evidence_gate import (
    F8ConfigArtifact,
    F8GRPOEvidenceGateError,
    F8GRPOEvidenceGateInput,
    F8GRPOEvidenceGateReport,
    F8ImageArtifact,
    F8ImmutableJSONRef,
    F8PreflightArtifact,
    F8TaskArtifact,
    F8TargetIdentity,
    F8TerminalLifecycleRecord,
    F8TreeArtifact,
    F8VerifierArtifact,
    _parameter_digest,
    _parquet_projection,
    build_container_probe_command,
    build_trainer_command,
    run_f8_grpo_evidence_gate,
    _validate_image_and_runtime,
)


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_ref(path: Path, value: Any) -> F8ImmutableJSONRef:
    raw = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _blob_ref(path: Path) -> F8ImmutableJSONRef:
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(path.read_bytes()))


def _entries(root: Path, names: tuple[str, ...]) -> list[dict[str, Any]]:
    result = []
    for name in names:
        raw = (root / name).read_bytes()
        result.append({"relative_path": name, "size": len(raw), "digest": _sha(raw)})
    return result


def _tree(
    schema: str,
    root: Path,
    names: tuple[str, ...],
    *,
    exact: bool,
    parameter_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "root": str(root.resolve()),
        "entries": _entries(root, names),
        "exact_tree": exact,
        "format": "transformers",
        "parameter_files": parameter_files or {},
    }


def _invoke_reward(adapter_path: str, metadata: dict[str, Any], truth: str) -> float:
    spec = importlib.util.spec_from_file_location(
        f"f8_reward_{os.getpid()}_{time.time_ns()}", adapter_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    count = len(metadata.get("f8_carrier_refs", ()))
    result = module.compute_score(
        ["math"] * count,
        [truth] * count,
        [truth] * count,
        [metadata] * count,
        num_workers=4,
    )
    return float(result[0]["score"])


@dataclass
class Fixture:
    spec: F8GRPOEvidenceGateInput
    input_digest: str
    input_path: Path
    source_path: Path
    output_path: Path
    target_input_path: Path
    target_spec: F8TargetTrainingInput
    runner_authority_key: bytes
    runner_authority_key_path: Path
    expected_runner_authority_key_digest: str
    refs: dict[str, F8ImmutableJSONRef]
    paths: dict[str, Path]
    carrier_refs: list[F8ImmutableJSONRef]
    carrier_values: list[dict[str, Any]]


def _fixture(
    root: Path,
    *,
    image_mismatch: bool = False,
    wrong_model_root: bool = False,
    synthetic_optimizer: bool = False,
    fake_reload: bool = False,
    fake_cleanup: bool = False,
    duplicate_carrier: bool = False,
    missing_disposition: bool = False,
    metric_declaration_mismatch: bool = False,
    nonfinite_optimizer: bool = False,
    fake_container_receipt: bool = False,
    omit_trainer_authority_bind: bool = False,
    omit_reload_authority_bind: bool = False,
    omit_rocm: bool = False,
    forged_runner_receipt: bool = False,
    reload_harness_drift: bool = False,
    producer_digest_drift: bool = False,
    unapproved_runner_key: bool = False,
    source_schema: str = "bb.rl.phase5-f8-target-source-report.v4",
) -> Fixture:
    authority = root / "authority"
    approved_runner_authority_key = b"f8-approved-external-test-runner-authority-key-v1"
    checkpoint_root = authority / "checkpoint-before"
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "config.json").write_bytes(
        b'{"architectures":["PinnedModel"]}\n'
    )
    (checkpoint_root / "tokenizer.json").write_bytes(
        b'{"model":{"type":"WordLevel"}}\n'
    )
    save_file(
        {"weight": np.asarray([1.0, 2.0], dtype=np.float32)},
        checkpoint_root / "model.safetensors",
    )
    output_root = root / "checkpoint-after"
    output_root.mkdir()
    (output_root / "config.json").write_bytes(
        (checkpoint_root / "config.json").read_bytes()
    )
    (output_root / "tokenizer.json").write_bytes(
        (checkpoint_root / "tokenizer.json").read_bytes()
    )
    save_file(
        {"weight": np.asarray([1.5, 2.5], dtype=np.float32)},
        output_root / "model.safetensors",
    )

    payload_root = root / "verl-payload"
    trainer_module_path = payload_root / "verl" / "trainer" / "main_ppo.py"
    reward_loader_path = payload_root / "verl" / "trainer" / "ppo" / "reward.py"
    reward_loader_path.parent.mkdir(parents=True)
    (payload_root / "verl" / "__init__.py").write_text("", encoding="utf-8")
    (payload_root / "verl" / "trainer" / "__init__.py").write_text("", encoding="utf-8")
    (payload_root / "verl" / "trainer" / "ppo" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    trainer_module_path.write_text("# pinned test VeRL module\n", encoding="utf-8")
    reward_loader_path.write_text(
        "def load(config):\n    return config.get('custom_reward_function')\n",
        encoding="utf-8",
    )
    payload_names = (
        "verl/__init__.py",
        "verl/trainer/__init__.py",
        "verl/trainer/main_ppo.py",
        "verl/trainer/ppo/__init__.py",
        "verl/trainer/ppo/reward.py",
    )
    payload_ref = _write_ref(
        authority / "verl-payload-artifact.json",
        {
            "schema_version": "bb.rl.phase5-f8-verl-payload-tree.v1",
            "root": str(payload_root.resolve()),
            "distribution": "verl",
            "distribution_version": "local-contract-fixture",
            "provenance": "focused-test-local-contract-only",
            "entries": _entries(payload_root, payload_names),
            "exact_tree": True,
            "entrypoint_relative_path": "verl/trainer/main_ppo.py",
            "reward_loader_relative_path": "verl/trainer/ppo/reward.py",
        },
    )
    config_value = {
        "schema_version": "bb.rl.phase5-f8-config.v2",
        "trainer_entrypoint": "verl.trainer.main_ppo",
        "rollout_name": "vllm",
        "rollout_mode": "sync",
        "rollout_n": 4,
        "train_batch_size": 8,
        "val_batch_size": 1,
        "total_training_steps": 3,
        "n_gpus_per_node": 1,
        "actor_learning_rate": 1e-6,
        "timeout_seconds": 30,
        "terminate_grace_seconds": 1,
        "kill_grace_seconds": 1,
        "reward_num_workers": 4,
        "max_prompt_length": 64,
        "max_response_length": 16,
        "hf_home": str((root / "hf-home").resolve()),
        "working_directory": str(payload_root.resolve()),
        "payload_manifest": payload_ref.model_dump(mode="json"),
        "trainer_module_relative_path": "verl/trainer/main_ppo.py",
        "trainer_module_digest": _sha(trainer_module_path.read_bytes()),
        "reward_loader_relative_path": "verl/trainer/ppo/reward.py",
        "reward_loader_digest": _sha(reward_loader_path.read_bytes()),
        "output_parameter_relative_path": "model.safetensors",
        "changed_parameter_name": "weight",
    }
    task_value = {
        "schema_version": "bb.rl.phase5-f8-task.v1",
        "train_rows": [
            {
                "row_id": f"train-{index:02d}",
                "data_source": "math",
                "prompt": [{"role": "user", "content": f"Return {index % 2}."}],
                "ground_truth": str(index % 2),
            }
            for index in range(24)
        ],
        "val_rows": [
            {
                "row_id": "val-00",
                "data_source": "math",
                "prompt": [{"role": "user", "content": "Return 1."}],
                "ground_truth": "1",
            }
        ],
    }
    verifier_source = authority / "verifier.py"
    verifier_source.write_text(
        "def compute_score(data_source, solution_str, ground_truth, extra_info=None):\n"
        "    reward = 1.0 if str(ground_truth).strip() in str(solution_str).strip() else 0.0\n"
        "    return {'score': reward, 'exact_match': bool(reward)}\n",
        encoding="utf-8",
    )
    verifier_source_ref = _blob_ref(verifier_source)
    image_path = authority / "pinned-f8.sif"
    image_path.write_bytes(b"pinned-apptainer-sif-image")
    image_digest = _sha(image_path.read_bytes())
    runtime_path = Path(sys.executable).resolve()
    runtime_digest = _sha(runtime_path.read_bytes())
    preflight_digest = (
        _sha(b"different-runtime-image") if image_mismatch else image_digest
    )
    model_root = (authority / "wrong-model") if wrong_model_root else checkpoint_root
    if wrong_model_root:
        model_root.mkdir()
        (model_root / "config.json").write_bytes(
            (checkpoint_root / "config.json").read_bytes()
        )
        (model_root / "model.safetensors").write_bytes(
            (checkpoint_root / "model.safetensors").read_bytes()
        )

    refs: dict[str, F8ImmutableJSONRef] = {}
    refs["config"] = _write_ref(authority / "config-artifact.json", config_value)
    refs["task"] = _write_ref(authority / "task-artifact.json", task_value)
    refs["model"] = _write_ref(
        authority / "model-artifact.json",
        _tree(
            "bb.rl.phase5-f8-model-tree.v1",
            model_root,
            ("config.json", "model.safetensors"),
            exact=False,
        ),
    )
    refs["tokenizer"] = _write_ref(
        authority / "tokenizer-artifact.json",
        _tree(
            "bb.rl.phase5-f8-tokenizer-tree.v1",
            checkpoint_root,
            ("tokenizer.json",),
            exact=False,
        ),
    )
    refs["input_checkpoint"] = _write_ref(
        authority / "input-checkpoint-artifact.json",
        _tree(
            "bb.rl.phase5-f8-checkpoint-tree.v1",
            checkpoint_root,
            ("config.json", "model.safetensors", "tokenizer.json"),
            exact=True,
            parameter_files={"weight": "model.safetensors"},
        ),
    )
    refs["verifier"] = _write_ref(
        authority / "verifier-artifact.json",
        {
            "schema_version": "bb.rl.phase5-f8-verifier.v1",
            "source": verifier_source_ref.model_dump(mode="json"),
            "function_name": "compute_score",
        },
    )
    refs["image"] = _write_ref(
        authority / "image-artifact.json",
        {
            "schema_version": "bb.rl.phase5-f8-image.v2",
            "immutable_image_digest": image_digest,
            "image_reference": str(image_path.resolve()),
            "container_runtime_executable": str(runtime_path),
            "container_runtime_digest": runtime_digest,
            "container_python_executable": "/usr/bin/python3",
        },
    )
    refs["preflight"] = _write_ref(
        authority / "preflight-artifact.json",
        {
            "schema_version": "bb.rl.phase5-f8-preflight.v2",
            "passed": True,
            "container_runtime_executable": str(runtime_path),
            "container_runtime_digest": runtime_digest,
            "container_python_executable": "/usr/bin/python3",
            "trainer_module": "verl.trainer.main_ppo",
            "accelerator_mode": "rocm",
            "payload_digest": payload_ref.digest,
            "image_reference": str(image_path.resolve()),
            "image_digest": preflight_digest,
            "observed_environment": {"SLURM_JOB_ID": "slurm-800"},
        },
    )
    target = F8TargetIdentity(
        target_run_id="f8-real-source-slurm-800",
        command_id="command-800",
        job_id="slurm-800",
    )
    target_input_value = {
        "schema_version": "bb.rl.phase5-f8-verl-grpo-target-input.v4",
        "execution_scope": "ibm_slurm_apptainer",
        "report_id": "f8-real-source-report",
        "target": target.model_dump(mode="json"),
        "slurm_job_id_source": "pinned",
        "identity_artifacts": {
            key: ref.model_dump(mode="json") for key, ref in refs.items()
        },
        "runner_authority_key_id": "f8-external-test-authority",
        "runner_authority_key_digest": _sha(approved_runner_authority_key),
        "run_root": str((root / "target-runs").resolve()),
    }
    target_input_ref = _write_ref(
        root / "canonical-f8-target-input.json", target_input_value
    )
    target_spec = F8TargetTrainingInput.model_validate(target_input_value, strict=True)
    config = F8ConfigArtifact.model_validate(config_value, strict=True)
    task = F8TaskArtifact.model_validate_json(
        canonical_json_bytes(task_value), strict=True
    )
    hashes = _input_hashes(target_spec)

    evidence = root / "source-evidence"
    evidence.mkdir()
    carrier_manifest_ref = _preauthorize_carriers(
        target_spec, config, task, hashes, evidence
    )
    carrier_manifest = canonical_json_loads(
        Path(carrier_manifest_ref.path).read_bytes()
    )
    carrier_refs = [
        F8ImmutableJSONRef.model_validate(value, strict=True)
        for value in carrier_manifest["carriers"]
    ]
    carrier_values = [
        canonical_json_loads(Path(ref.path).read_bytes()) for ref in carrier_refs
    ]
    dataset_carrier_manifest_ref = carrier_manifest_ref
    if duplicate_carrier:
        carrier_manifest["carriers"][-1] = carrier_manifest["carriers"][0]
        carrier_manifest_ref = _write_ref(
            evidence / "carrier-manifest-duplicate.json", carrier_manifest
        )

    train_path, val_path = evidence / "train.parquet", evidence / "val.parquet"
    _materialize_datasets(task, dataset_carrier_manifest_ref, train_path, val_path)

    claims_dir, records_dir, dispositions_dir = (
        evidence / "claims",
        evidence / "records",
        evidence / "dispositions",
    )
    for directory in (claims_dir, records_dir, dispositions_dir):
        directory.mkdir()
    verifier = F8VerifierArtifact.model_validate(
        canonical_json_loads(Path(refs["verifier"].path).read_bytes()), strict=True
    )
    reward_path = evidence / "reward-adapter.py"
    reward_path.write_text(
        _reward_source(
            refs["task"],
            carrier_manifest_ref,
            verifier,
            records_dir,
            claims_dir,
            dispositions_dir,
        ),
        encoding="utf-8",
    )

    record_refs: list[F8ImmutableJSONRef] = []
    disposition_refs: list[F8ImmutableJSONRef] = []
    for index, (carrier_ref, carrier) in enumerate(
        zip(carrier_refs, carrier_values, strict=True)
    ):
        claim_ref = _write_ref(
            claims_dir / f"{index:08d}.json",
            {
                "schema_version": "bb.rl.phase5-f8-carrier-claim.v2",
                "carrier_ref": carrier_ref.model_dump(mode="json"),
                "carrier_digest": carrier["carrier_digest"],
                "claimant_pid": 50_000,
                "claimant_thread_id": 60_000 + (index % 4),
            },
        )
        record_ref = _write_ref(
            records_dir / f"{index:08d}.json",
            {
                "schema_version": "bb.rl.phase5-f8-rollout-sample.v2",
                "sample_id": carrier["episode_id"],
                "target": target.model_dump(mode="json"),
                "input_hashes": hashes.model_dump(mode="json"),
                "rollout_carrier": carrier,
                "carrier_ref": carrier_ref.model_dump(mode="json"),
                "claim_ref": claim_ref.model_dump(mode="json"),
                "reward": float(index % 2),
                "solution_sha256": _sha(str(index % 2).encode()),
                "verifier_digest": refs["verifier"].digest,
            },
        )
        disposition_ref = _write_ref(
            dispositions_dir / f"{index:08d}.json",
            {
                "schema_version": "bb.rl.phase5-f8-carrier-disposition.v1",
                "carrier_ref": carrier_ref.model_dump(mode="json"),
                "claim_ref": claim_ref.model_dump(mode="json"),
                "state": "recorded",
                "record_ref": record_ref.model_dump(mode="json"),
            },
        )
        record_refs.append(record_ref)
        disposition_refs.append(disposition_ref)
    if missing_disposition:
        disposition_refs[-1] = disposition_refs[0]
    refs["rollout_manifest"] = _write_ref(
        evidence / "rollout-manifest.json",
        {
            "schema_version": "bb.rl.phase5-f8-rollout-evidence-manifest.v2",
            "target": target.model_dump(mode="json"),
            "input_hashes": hashes.model_dump(mode="json"),
            "carrier_manifest": carrier_manifest_ref.model_dump(mode="json"),
            "generated_sample_count": len(record_refs),
            "records": [ref.model_dump(mode="json") for ref in record_refs],
            "dispositions": [ref.model_dump(mode="json") for ref in disposition_refs],
        },
    )
    refs["carrier_manifest"] = carrier_manifest_ref

    stdout_lines = []
    metric_refs = []
    for step in range(1, 4):
        line = (
            f"step:{step} - training/global_step:{step}"
            " - actor/grad_norm:0.5 - actor/lr:0.000001"
            " - critic/advantages/min:-0.5 - critic/advantages/max:0.5"
            " - response/aborted_ratio:0"
            " - actor/ppo_kl:0.001 - actor/kl_loss:0.00001"
        )
        stdout_lines.append(line)
        metric_refs.append(
            _write_ref(
                evidence / "metrics" / f"{step:08d}.json",
                {
                    "schema_version": "bb.rl.phase5-f8-trainer-step.v3",
                    "target": target.model_dump(mode="json"),
                    "input_hashes": hashes.model_dump(mode="json"),
                    "optimizer_step": step,
                    "training_global_step": step,
                    "raw_line_sha256": _sha(line.encode()),
                    "raw_line": line,
                    "actor_gradient_norm": (
                        0.6 if metric_declaration_mismatch and step == 2 else 0.5
                    ),
                    "learning_rate": 1e-6,
                    "advantage_min": -0.5,
                    "advantage_max": 0.5,
                    "aborted_ratio": 0.0,
                    "actor_ppo_kl": 0.001,
                    "actor_k3_kl": 0.00001,
                },
            )
        )
    stdout_path = evidence / "trainer-stdout.log"
    stdout_path.write_text("\n".join(stdout_lines) + "\n", encoding="utf-8")
    stderr_path = evidence / "trainer-stderr.log"
    stderr_path.write_text("", encoding="utf-8")
    refs["metrics_manifest"] = _write_ref(
        evidence / "metrics-manifest.json",
        {
            "schema_version": "bb.rl.phase5-f8-trainer-metrics-manifest.v2",
            "target": target.model_dump(mode="json"),
            "input_hashes": hashes.model_dump(mode="json"),
            "rollout_mode": "sync",
            "stdout_ref": _blob_ref(stdout_path).model_dump(mode="json"),
            "records": [ref.model_dump(mode="json") for ref in metric_refs],
        },
    )

    output_manifest_ref = _write_ref(
        evidence / "checkpoint-after.json",
        _tree(
            "bb.rl.phase5-f8-checkpoint-tree.v1",
            output_root,
            ("config.json", "model.safetensors", "tokenizer.json"),
            exact=True,
            parameter_files={"weight": "model.safetensors"},
        ),
    )
    output_manifest = F8TreeArtifact.model_validate_json(
        Path(output_manifest_ref.path).read_bytes(), strict=True
    )
    optimizer_receipt_refs: list[F8ImmutableJSONRef] = []
    for step in range(1, 4):
        if step == 3:
            step_manifest_ref = output_manifest_ref
            step_manifest = output_manifest
        else:
            step_root = evidence / f"checkpoint-step-{step}"
            step_root.mkdir()
            (step_root / "config.json").write_bytes(
                (checkpoint_root / "config.json").read_bytes()
            )
            (step_root / "tokenizer.json").write_bytes(
                (checkpoint_root / "tokenizer.json").read_bytes()
            )
            value = 1.1 if step == 1 or synthetic_optimizer else 1.3
            parameter_values = np.asarray([value, value + 1.0], dtype=np.float32)
            if nonfinite_optimizer and step == 2:
                parameter_values[0] = np.nan
            save_file(
                {"weight": parameter_values},
                step_root / "model.safetensors",
            )
            step_manifest_ref = _write_ref(
                evidence / f"checkpoint-step-{step}.json",
                _tree(
                    "bb.rl.phase5-f8-checkpoint-tree.v1",
                    step_root,
                    ("config.json", "model.safetensors", "tokenizer.json"),
                    exact=True,
                    parameter_files={"weight": "model.safetensors"},
                ),
            )
            step_manifest = F8TreeArtifact.model_validate_json(
                Path(step_manifest_ref.path).read_bytes(), strict=True
            )
        optimizer_state_path = evidence / f"optimizer-state-{step}.bin"
        optimizer_state_path.write_bytes(f"optimizer-state-{step}".encode())
        optimizer_receipt_refs.append(
            _write_ref(
                evidence / f"optimizer-receipt-{step}.json",
                {
                    "schema_version": "bb.rl.phase5-f8-optimizer-step-receipt.v1",
                    "target": target.model_dump(mode="json"),
                    "input_hashes": hashes.model_dump(mode="json"),
                    "optimizer_step": step,
                    "checkpoint_ref": step_manifest_ref.model_dump(mode="json"),
                    "optimizer_state_refs": [
                        _blob_ref(optimizer_state_path).model_dump(mode="json")
                    ],
                    "parameter_name": "weight",
                    "parameter_digest": (
                        _sha(b"declared-finite")
                        if nonfinite_optimizer and step == 2
                        else _parameter_digest(step_manifest, "weight")
                    ),
                    "parameter_all_finite": True,
                },
            )
        )
    refs["optimizer_manifest"] = _write_ref(
        evidence / "optimizer-steps-manifest.json",
        {
            "schema_version": "bb.rl.phase5-f8-optimizer-steps-manifest.v1",
            "target": target.model_dump(mode="json"),
            "input_hashes": hashes.model_dump(mode="json"),
            "records": [ref.model_dump(mode="json") for ref in optimizer_receipt_refs],
        },
    )
    input_manifest = F8TreeArtifact.model_validate_json(
        Path(refs["input_checkpoint"].path).read_bytes(), strict=True
    )
    identities = {
        "config_digest": refs["config"].digest,
        "task_digest": refs["task"].digest,
        "model_digest": refs["model"].digest,
        "tokenizer_digest": refs["tokenizer"].digest,
        "input_checkpoint_digest": refs["input_checkpoint"].digest,
        "output_checkpoint_digest": output_manifest_ref.digest,
        "verifier_digest": refs["verifier"].digest,
        "image_digest": refs["image"].digest,
        "preflight_digest": refs["preflight"].digest,
    }
    reload_harness_ref, reload_target_script = _write_reload_harness(root)
    refs["reload_harness"] = reload_harness_ref
    reload_receipt_path = evidence / "reload-receipt.json"
    if reload_harness_drift:
        drift_path = (
            Path(
                canonical_json_loads(Path(reload_harness_ref.path).read_bytes())["root"]
            )
            / "breadboard_engine/compilation/contracts.py"
        )
        drift_path.chmod(0o600)
        drift_path.write_bytes(b"mutated import closure")
    reload_request_ref = _write_ref(
        evidence / "reload-request.json",
        {
            "schema_version": "bb.rl.phase5-f8-reload-request.v1",
            "output_path": str(reload_receipt_path.resolve()),
            "checkpoint_ref": output_manifest_ref.model_dump(mode="json"),
            "model_ref": refs["model"].model_dump(mode="json"),
            "tokenizer_ref": refs["tokenizer"].model_dump(mode="json"),
            "config_ref": refs["config"].model_dump(mode="json"),
            "verifier_ref": refs["verifier"].model_dump(mode="json"),
            "parameter_name": "weight",
            "prompt": "Return 1.",
            "ground_truth": "1",
        },
    )
    after_parameter = _parameter_digest(output_manifest, "weight")
    before_parameter = _parameter_digest(input_manifest, "weight")
    reload_receipt_ref = _write_ref(
        reload_receipt_path,
        {
            "schema_version": "bb.rl.phase5-f8-reload-receipt.v1",
            "request_ref": reload_request_ref.model_dump(mode="json"),
            "process_id": 50_002,
            "parent_process_id": os.getpid(),
            "checkpoint_ref": output_manifest_ref.model_dump(mode="json"),
            "model_ref": refs["model"].model_dump(mode="json"),
            "tokenizer_ref": refs["tokenizer"].model_dump(mode="json"),
            "config_ref": refs["config"].model_dump(mode="json"),
            "parameter_name": "weight",
            "parameter_digest": after_parameter,
            "deterministic": True,
            "inference_output_sha256": _sha(b"deterministic-output-1"),
            "verifier_reward": 1.0,
        },
    )
    reload_value: Any = {
        "schema_version": "bb.rl.phase5-f8-checkpoint-reload-evidence.v2",
        "target": target.model_dump(mode="json"),
        "input_hashes": hashes.model_dump(mode="json"),
        "checkpoint_before_ref": refs["input_checkpoint"].model_dump(mode="json"),
        "checkpoint_after_ref": output_manifest_ref.model_dump(mode="json"),
        "changed_parameter_name": "weight",
        "parameter_before_digest": before_parameter,
        "parameter_after_digest": after_parameter,
        "optimizer_update_digest": refs["optimizer_manifest"].digest,
        "reload_request_ref": reload_request_ref.model_dump(mode="json"),
        "reload_receipt_ref": reload_receipt_ref.model_dump(mode="json"),
        "reload_pid": 50_002,
        "trainer_pid": 50_001,
        "reload_returncode": 0,
        "reload_command": list(
            _build_reload_command(
                F8ImageArtifact.model_validate_json(
                    Path(refs["image"].path).read_bytes(), strict=True
                ),
                F8PreflightArtifact.model_validate_json(
                    Path(refs["preflight"].path).read_bytes(), strict=True
                ),
                config,
                target_script=reload_target_script,
                run_root=str(root.resolve()),
                config_ref_path=refs["config"].path,
                verifier_ref_path=refs["verifier"].path,
                verifier_source_path=verifier.source.path,
                reload_request_path=reload_request_ref.path,
            )
        ),
        "deterministic_inference_digest": _sha(b"deterministic-output-1"),
        "reload_harness_ref": reload_harness_ref.model_dump(mode="json"),
        "verifier_reward": 1.0,
    }
    if omit_reload_authority_bind:
        reload_command = reload_value["reload_command"]
        bind_value = f"{refs['config'].path}:{refs['config'].path}:ro"
        bind_index = reload_command.index(bind_value)
        del reload_command[bind_index - 1 : bind_index + 1]
    if fake_reload:
        reload_value = {
            "fresh_process_reload": True,
            "changed_parameter_reverified": True,
        }
    refs["reload"] = _write_ref(evidence / "checkpoint-reload.json", reload_value)

    terminal_value: Any = {
        "schema_version": "bb.rl.phase5-f8-terminal-lifecycle.v1",
        "target": target.model_dump(mode="json"),
        "terminal_state": "closed",
        "trainer_pid": 50_001,
        "trainer_pgid": 50_001,
        "trainer_returncode": 0,
        "timed_out": False,
        "termination_signals": [],
        "process_group_reaped": True,
        "stdout_reader_joined": True,
        "remaining_process_ids": [],
        "remaining_container_ids": [],
        "active_lease_ids": [],
        "cleanup_errors": [],
        "retained_checkpoint_ref": output_manifest_ref.model_dump(mode="json"),
        "quarantined_artifacts": [],
    }
    if fake_cleanup:
        terminal_value = {
            "terminal_state": "closed",
            "failed_outputs_quarantined": True,
        }
    refs["terminal"] = _write_ref(evidence / "terminal.json", terminal_value)

    preflight = F8PreflightArtifact.model_validate(
        canonical_json_loads(Path(refs["preflight"].path).read_bytes()), strict=True
    )
    image_artifact = F8ImageArtifact.model_validate(
        canonical_json_loads(Path(refs["image"].path).read_bytes()),
        strict=True,
    )
    refs["container_observation"] = _write_ref(
        evidence / "container-observation.json",
        {
            "schema_version": "bb.rl.phase5-f8-container-observation.v1",
            "target": target.model_dump(mode="json"),
            "input_hashes": hashes.model_dump(mode="json"),
            "probe_process_id": 49_999,
            "parent_process_id": os.getpid(),
            "command": list(
                build_container_probe_command(image_artifact, preflight, config)
            ),
            "container_runtime_executable": str(runtime_path),
            "container_runtime_digest": runtime_digest,
            "container_python_executable": preflight.container_python_executable,
            "observed_image_reference": str(image_path.resolve()),
            "observed_image_digest": (
                _sha(b"declared-container") if fake_container_receipt else image_digest
            ),
        },
    )
    child_env = dict(preflight.observed_environment)
    runtime_value = {
        "schema_version": "bb.rl.phase5-f8-observed-runtime.v2",
        "target": target.model_dump(mode="json"),
        "input_ref": target_input_ref.model_dump(mode="json"),
        "identity_artifacts": {
            key: refs[key].model_dump(mode="json")
            for key in (
                "config",
                "task",
                "model",
                "tokenizer",
                "input_checkpoint",
                "verifier",
                "image",
                "preflight",
            )
        },
        "container_runtime_executable": str(runtime_path),
        "container_runtime_digest": runtime_digest,
        "container_python_executable": preflight.container_python_executable,
        "command": list(
            build_trainer_command(
                F8ImageArtifact.model_validate(
                    canonical_json_loads(Path(refs["image"].path).read_bytes()),
                    strict=True,
                ),
                preflight,
                config,
                refs["task"].path,
                refs["verifier"].path,
                verifier.source.path,
                input_manifest.root,
                str(train_path.resolve()),
                str(val_path.resolve()),
                str(reward_path.resolve()),
                str(root.resolve()),
                target_spec.report_id,
            )
        ),
        "child_environment": child_env,
        "working_directory": config.working_directory,
        "payload_manifest": payload_ref.model_dump(mode="json"),
        "payload_digest": payload_ref.digest,
        "container_observation_ref": refs["container_observation"].model_dump(
            mode="json"
        ),
        "requested_image_reference": str(image_path.resolve()),
        "effective_image_reference": str(image_path.resolve()),
        "observed_image_reference": str(image_path.resolve()),
        "requested_image_digest": image_digest,
        "effective_image_digest": image_digest,
        "observed_image_digest": image_digest,
        "train_dataset": _blob_ref(train_path).model_dump(mode="json"),
        "val_dataset": _blob_ref(val_path).model_dump(mode="json"),
        "reward_adapter": _blob_ref(reward_path).model_dump(mode="json"),
        "claim_root": str(claims_dir.resolve()),
        "record_root": str(records_dir.resolve()),
        "disposition_root": str(dispositions_dir.resolve()),
        "train_projection_digest": _parquet_projection(str(train_path.resolve()))[1],
        "val_projection_digest": _parquet_projection(str(val_path.resolve()))[1],
        "output_checkpoint_root": str(root.resolve()),
        "controller_pid": os.getpid(),
        "trainer_pid": 50_001,
        "trainer_pgid": 50_001,
    }
    if omit_trainer_authority_bind:
        command = runtime_value["command"]
        bind_value = f"{refs['task'].path}:{refs['task'].path}:ro"
        bind_index = command.index(bind_value)
        del command[bind_index - 1 : bind_index + 1]
    if omit_rocm:
        runtime_value["command"].remove("--rocm")
    refs["runtime"] = _write_ref(evidence / "observed-runtime.json", runtime_value)

    source_path = root / "f8-target-source-report.json"
    source_value = {
        "schema_version": source_schema,
        "component": "f8_verl_grpo_target_source",
        "report_id": target_spec.report_id,
        "report_path": str(source_path.resolve()),
        "passed": True,
        "blocked_reason": "",
        "target": target.model_dump(mode="json"),
        "requested_target": target.model_dump(mode="json"),
        "input_ref": target_input_ref.model_dump(mode="json"),
        "identity_artifacts": {
            key: refs[key].model_dump(mode="json")
            for key in (
                "config",
                "task",
                "model",
                "tokenizer",
                "input_checkpoint",
                "verifier",
                "image",
                "preflight",
            )
        },
        "identities": identities,
        "input_hashes": hashes.model_dump(mode="json"),
        "expected_sample_count": len(record_refs),
        "observed_sample_record_count": len(record_refs),
        "observed_optimizer_metric_record_count": len(metric_refs),
        "artifacts": {
            "observed_runtime": refs["runtime"].model_dump(mode="json"),
            "container_observation": refs["container_observation"].model_dump(
                mode="json"
            ),
            "carrier_manifest": carrier_manifest_ref.model_dump(mode="json"),
            "reload_harness": reload_harness_ref.model_dump(mode="json"),
            "rollout_manifest": refs["rollout_manifest"].model_dump(mode="json"),
            "trainer_metrics_manifest": refs["metrics_manifest"].model_dump(
                mode="json"
            ),
            "optimizer_steps_manifest": refs["optimizer_manifest"].model_dump(
                mode="json"
            ),
            "checkpoint_reload": refs["reload"].model_dump(mode="json"),
            "terminal_lifecycle": refs["terminal"].model_dump(mode="json"),
            "stdout": _blob_ref(stdout_path).model_dump(mode="json"),
            "stderr": _blob_ref(stderr_path).model_dump(mode="json"),
        },
        "completed_at": "2026-07-14T01:00:00Z",
        "permanent_non_authority": True,
        "promotion_authority": False,
        "scorecard_update_allowed": False,
    }
    source_ref = _write_ref(source_path, source_value)
    runner_authority_key = (
        b"f8-unapproved-external-test-runner-authority-key-v1"
        if unapproved_runner_key
        else approved_runner_authority_key
    )
    runner_authority_key_path = (
        root.parent / "external-runner-keys" / f"{root.name}.key"
    )
    runner_authority_key_path.parent.mkdir(parents=True, exist_ok=True)
    runner_authority_key_path.write_bytes(runner_authority_key)
    runner_receipt_payload = {
        "schema_version": "bb.rl.phase5-f8-target-runner-receipt.v1",
        "component": "f8_canonical_target_runner",
        "execution_scope": "ibm_slurm_apptainer",
        "target": target.model_dump(mode="json"),
        "input_ref": target_input_ref.model_dump(mode="json"),
        "input_hashes": hashes.model_dump(mode="json"),
        "source_report_ref": source_ref.model_dump(mode="json"),
        "authority_key_id": "f8-external-test-authority",
        "authority_key_digest": _sha(runner_authority_key),
        "slurm_job_id": target.job_id,
        "wrapper_source_digest": (
            _sha(b"unreviewed-wrapper")
            if producer_digest_drift
            else _sha(
                (
                    Path(_reward_source.__code__.co_filename).resolve().parent
                    / "run_verl_trainer_update.py"
                ).read_bytes()
            )
        ),
        "target_source_digest": _sha(
            _reload_harness_sources()["scripts/rl_phase3/target_verl_smoke_train.py"]
        ),
        "gate_source_digest": _sha(
            Path(run_f8_grpo_evidence_gate.__code__.co_filename).read_bytes()
        ),
        "reload_harness_manifest_digest": reload_harness_ref.digest,
        "runtime_ref": refs["runtime"].model_dump(mode="json"),
        "container_observation_ref": refs["container_observation"].model_dump(
            mode="json"
        ),
        "callback_record_refs": [ref.model_dump(mode="json") for ref in record_refs],
        "callback_disposition_refs": [
            ref.model_dump(mode="json") for ref in disposition_refs
        ],
        "trainer_step_refs": [ref.model_dump(mode="json") for ref in metric_refs],
        "optimizer_step_refs": [
            ref.model_dump(mode="json") for ref in optimizer_receipt_refs
        ],
        "checkpoint_reload_ref": refs["reload"].model_dump(mode="json"),
        "terminal_lifecycle_ref": refs["terminal"].model_dump(mode="json"),
        "trainer_pid": 50_001,
        "trainer_pgid": 50_001,
        "reload_pid": 50_002,
        "trainer_returncode": 0,
        "command": runtime_value["command"],
        "completed_at": source_value["completed_at"],
    }
    runner_signature = (
        "sha256:"
        + hmac.new(
            runner_authority_key,
            canonical_json_bytes(runner_receipt_payload),
            hashlib.sha256,
        ).hexdigest()
    )
    if forged_runner_receipt:
        runner_signature = _sha(b"forged-external-runner-signature")
    runner_receipt_payload["authority_signature"] = runner_signature
    refs["target_runner_receipt"] = _write_ref(
        evidence / "external-target-runner-receipt.json",
        runner_receipt_payload,
    )
    gate_value = {
        "schema_version": "bb.rl.phase5-f8-grpo-evidence-gate-input.v3",
        "gate_id": "f8-source-closed-gate",
        "target": target.model_dump(mode="json"),
        "expected_episode_joins": [
            {
                "episode_id": carrier["episode_id"],
                "attempt_id": carrier["attempt_id"],
                "rollout_carrier_digest": carrier["carrier_digest"],
            }
            for carrier in carrier_values
        ],
        "target_source_report": source_ref.model_dump(mode="json"),
        "target_runner_receipt": refs["target_runner_receipt"].model_dump(mode="json"),
    }
    input_path = root / "f8-gate-input.json"
    input_raw = canonical_json_bytes(gate_value)
    input_path.write_bytes(input_raw)
    spec = F8GRPOEvidenceGateInput.model_validate_json(input_raw, strict=True)
    return Fixture(
        spec=spec,
        runner_authority_key_path=runner_authority_key_path,
        expected_runner_authority_key_digest=_sha(approved_runner_authority_key),
        input_digest=_sha(input_raw),
        input_path=input_path,
        source_path=source_path,
        output_path=root / "gate-report.json",
        target_input_path=Path(target_input_ref.path),
        target_spec=target_spec,
        runner_authority_key=runner_authority_key,
        refs=refs,
        paths={
            "checkpoint_root": checkpoint_root,
            "output_root": output_root,
            "verifier_source": verifier_source,
            "reward": reward_path,
            "stdout": stdout_path,
            "source": source_path,
        },
        carrier_refs=carrier_refs,
        carrier_values=carrier_values,
    )


def _gate(fixture: Fixture):
    return run_f8_grpo_evidence_gate(
        fixture.spec,
        fixture.input_digest,
        output_path=str(fixture.output_path.resolve()),
        completed_at="2026-07-14T02:00:00Z",
        runner_authority_key=fixture.runner_authority_key,
        runner_authority_key_path=str(fixture.runner_authority_key_path.resolve()),
        expected_runner_authority_key_id="f8-external-test-authority",
        expected_runner_authority_key_digest=(
            fixture.expected_runner_authority_key_digest
        ),
    )


def test_external_runner_signed_source_report_is_the_positive_gate(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    report = _gate(fixture)
    assert report.passed is True
    assert report.permanent_non_authority is True
    assert report.promotion_authority is False
    runtime = canonical_json_loads(Path(fixture.refs["runtime"].path).read_bytes())
    reload = canonical_json_loads(Path(fixture.refs["reload"].path).read_bytes())
    for command in (runtime["command"], reload["reload_command"]):
        assert "--rocm" in command
        assert "--nv" not in command
    trainer_bindings = set(runtime["command"])
    assert (
        f"{fixture.refs['task'].path}:{fixture.refs['task'].path}:ro"
        in trainer_bindings
    )
    assert (
        f"{fixture.refs['verifier'].path}:{fixture.refs['verifier'].path}:ro"
        in trainer_bindings
    )
    assert (
        f"{fixture.paths['verifier_source']}:{fixture.paths['verifier_source']}:ro"
        in trainer_bindings
    )
    reload_bindings = set(reload["reload_command"])
    assert (
        f"{fixture.refs['config'].path}:{fixture.refs['config'].path}:ro"
        in reload_bindings
    )
    assert (
        f"{fixture.refs['verifier'].path}:{fixture.refs['verifier'].path}:ro"
        in reload_bindings
    )
    assert (
        f"{fixture.paths['verifier_source']}:{fixture.paths['verifier_source']}:ro"
        in reload_bindings
    )
    reload_harness = canonical_json_loads(
        Path(fixture.refs["reload_harness"].path).read_bytes()
    )
    harness_root = reload_harness["root"]
    assert f"{harness_root}:{harness_root}:ro" in reload_bindings


def test_real_source_report_gate_cli_positive(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    gate_script = Path(run_f8_grpo_evidence_gate.__code__.co_filename).resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(gate_script),
            "--input",
            str(fixture.input_path.resolve()),
            "--output",
            str(fixture.output_path.resolve()),
            "--completed-at",
            "2026-07-14T02:00:00Z",
            "--runner-authority-key-file",
            str(fixture.runner_authority_key_path.resolve()),
            "--expected-runner-authority-key-id",
            "f8-external-test-authority",
            "--expected-runner-authority-key-sha256",
            fixture.expected_runner_authority_key_digest,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(gate_script.parents[2]),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = F8GRPOEvidenceGateReport.model_validate_json(
        fixture.output_path.read_bytes(), strict=True
    )
    assert report.passed is True
    assert report.target == fixture.spec.target


def test_forged_external_runner_receipt_cannot_pass(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, forged_runner_receipt=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="signature"):
        _gate(fixture)


def test_correctly_signed_unapproved_runner_key_cannot_pass(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, unapproved_runner_key=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="unapproved authority"):
        _gate(fixture)


@pytest.mark.parametrize(
    ("fixture_option", "error"),
    [
        ("reload_harness_drift", "reload harness"),
        ("producer_digest_drift", "callback/execution-derived"),
    ],
)
def test_reload_import_or_reviewed_producer_drift_cannot_pass(
    tmp_path: Path,
    fixture_option: str,
    error: str,
) -> None:
    fixture = _fixture(tmp_path, **{fixture_option: True})
    with pytest.raises(F8GRPOEvidenceGateError, match=error):
        _gate(fixture)


@pytest.mark.parametrize(
    ("fixture_option", "error"),
    [
        ("omit_trainer_authority_bind", "launched command"),
        ("omit_reload_authority_bind", "reload"),
        ("omit_rocm", "launched command"),
    ],
)
def test_omitted_containall_authority_binds_or_rocm_cannot_pass(
    tmp_path: Path,
    fixture_option: str,
    error: str,
) -> None:
    fixture = _fixture(tmp_path, **{fixture_option: True})
    with pytest.raises(F8GRPOEvidenceGateError, match=error):
        _gate(fixture)


def test_wrapper_requires_canonical_f8_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        wrapper_main([])
    assert raised.value.code == 2
    assert "--f8-input" in capsys.readouterr().err


def test_target_entrypoint_has_no_implicit_legacy_dispatch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.rl_phase3.target_verl_smoke_train import main

    with pytest.raises(SystemExit) as raised:
        main([])
    assert raised.value.code == 2
    assert "--f8-input" in capsys.readouterr().err


def test_hostile_environment_override_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config, _, _, _, _, _, _, preflight = _load_authority(fixture.target_spec)
    with pytest.raises(RuntimeError, match="authority environment"):
        _minimal_child_environment(config, preflight, {"PYTHONPATH": "/attacker"})
    with pytest.raises(RuntimeError, match="scheduler/GPU"):
        _minimal_child_environment(config, preflight, {"CUDA_VISIBLE_DEVICES": "7"})


def test_introspection_mutation_is_rejected_before_wrapper_launch(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    Path(fixture.refs["preflight"].path).write_bytes(b"{}")
    with pytest.raises(RuntimeError, match="digest mismatch"):
        _load_authority(fixture.target_spec)


@pytest.mark.parametrize(
    "artifact_file", ["config.json", "model.safetensors", "tokenizer.json"]
)
def test_wrong_model_tokenizer_or_checkpoint_bytes_are_rejected(
    tmp_path: Path, artifact_file: str
) -> None:
    fixture = _fixture(tmp_path)
    (fixture.paths["checkpoint_root"] / artifact_file).write_bytes(b"hostile-bytes")
    with pytest.raises(F8GRPOEvidenceGateError):
        _gate(fixture)
    assert not fixture.output_path.exists()


def test_wrong_model_path_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, wrong_model_root=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="paths"):
        _gate(fixture)


def test_wrong_observed_image_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, image_mismatch=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="image"):
        _gate(fixture)


def test_unused_checkpoint_sidecar_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.paths["checkpoint_root"] / "undeclared-sidecar.bin").write_bytes(
        b"not-used"
    )
    with pytest.raises(F8GRPOEvidenceGateError, match="undeclared"):
        _gate(fixture)


def test_exact_payload_rejects_fake_trainer_replacement(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = F8ConfigArtifact.model_validate_json(
        Path(fixture.refs["config"].path).read_bytes(), strict=True
    )
    trainer = Path(config.working_directory) / config.trainer_module_relative_path
    trainer.write_text("# arbitrary fake replacement\n", encoding="utf-8")
    with pytest.raises(F8GRPOEvidenceGateError, match="payload"):
        _gate(fixture)


def test_exact_payload_rejects_undeclared_import_closure_file(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = F8ConfigArtifact.model_validate_json(
        Path(fixture.refs["config"].path).read_bytes(), strict=True
    )
    (Path(config.working_directory) / "verl" / "undeclared.py").write_text(
        "raise RuntimeError\n", encoding="utf-8"
    )
    with pytest.raises(F8GRPOEvidenceGateError, match="undeclared"):
        _gate(fixture)


def test_declared_container_observation_cannot_replace_executable_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, fake_container_receipt=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="container observation"):
        _gate(fixture)


def test_shebang_container_runtime_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    image = tmp_path / "image.sif"
    image.write_bytes(b"image")
    artifact = F8ImageArtifact(
        schema_version="bb.rl.phase5-f8-image.v2",
        immutable_image_digest=_sha(image.read_bytes()),
        image_reference=str(image.resolve()),
        container_runtime_executable=str(runtime.resolve()),
        container_runtime_digest=_sha(runtime.read_bytes()),
        container_python_executable="/usr/bin/python3",
    )
    with pytest.raises(F8GRPOEvidenceGateError, match="native executable"):
        _validate_image_and_runtime(artifact)


def test_declared_metrics_must_reparse_from_raw_stdout(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, metric_declaration_mismatch=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="reparse"):
        _gate(fixture)


def test_declared_parameter_finiteness_cannot_mask_nan_tensor(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, nonfinite_optimizer=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="nonfinite"):
        _gate(fixture)


def test_reward_adapter_mutation_is_rejected_as_unused_verifier_seam(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.paths["reward"].write_text(
        "def compute_score(*args): return 1.0\n", encoding="utf-8"
    )
    with pytest.raises(F8GRPOEvidenceGateError):
        _gate(fixture)


def test_duplicate_carrier_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, duplicate_carrier=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="duplicated"):
        _gate(fixture)


def test_missing_or_raced_disposition_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, missing_disposition=True)
    with pytest.raises(F8GRPOEvidenceGateError):
        _gate(fixture)


def test_synthetic_optimizer_constants_cannot_pass(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, synthetic_optimizer=True)
    with pytest.raises(F8GRPOEvidenceGateError, match="optimizer step"):
        _gate(fixture)


def test_target_and_gate_schema_mismatch_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path, source_schema="bb.rl.phase5-f8-target-source-report.v1"
    )
    with pytest.raises(F8GRPOEvidenceGateError, match="source report"):
        _gate(fixture)


@pytest.mark.parametrize("fake", ["reload", "cleanup"])
def test_boolean_only_reload_or_cleanup_cannot_pass(tmp_path: Path, fake: str) -> None:
    fixture = _fixture(
        tmp_path, fake_reload=fake == "reload", fake_cleanup=fake == "cleanup"
    )
    with pytest.raises(F8GRPOEvidenceGateError):
        _gate(fixture)


def test_failed_run_always_writes_terminal_quarantine_and_source_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path / "authority")
    value = fixture.target_spec.model_copy(
        update={"run_root": str((tmp_path / "failed-runs").resolve())}
    )
    input_path = tmp_path / "failed-target-input.json"
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    input_path.write_bytes(raw)
    monkeypatch.setenv("PYTHONPATH", "/hostile")
    result = _run_f8_source_closed(
        value,
        F8ImmutableJSONRef(path=str(input_path.resolve()), digest=_sha(raw)),
    )
    assert result == 2
    run_root = Path(value.run_root) / value.target.target_run_id / value.report_id
    source = canonical_json_loads(
        (run_root / "f8-target-source-report.json").read_bytes()
    )
    terminal = canonical_json_loads((run_root / "terminal-lifecycle.json").read_bytes())
    assert source["passed"] is False
    assert source["artifacts"]["terminal_lifecycle"]["path"] == str(
        (run_root / "terminal-lifecycle.json").resolve()
    )
    assert terminal["terminal_state"] == "failed_quarantined"
    assert terminal["quarantined_artifacts"]


def test_timeout_kills_descendant_holding_stdout_and_reaps_group(
    tmp_path: Path,
) -> None:
    script = tmp_path / "tree.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    signals, reaped = _terminate_process_group(process, 1, 1)
    assert reaped is True
    assert "SIGTERM" in signals
    assert process.poll() is not None


def test_bounded_reload_path_kills_descendant_and_cannot_leave_orphan(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "descendant.pid"
    script = tmp_path / "reload-tree.py"
    script.write_text(
        "import subprocess, sys, time\n"
        f"pid_path = {str(pid_path)!r}\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(pid_path, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="timed out"):
        _run_bounded_process(
            [sys.executable, str(script)],
            env={"PATH": os.environ["PATH"]},
            cwd=str(tmp_path),
            timeout=1,
            terminate_grace=1,
            kill_grace=1,
        )
    descendant_pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(descendant_pid, 0)


def test_partial_record_after_claim_has_failure_tombstone_and_is_unusable(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    records, claims, dispositions = (
        tmp_path / "partial-records",
        tmp_path / "partial-claims",
        tmp_path / "partial-dispositions",
    )
    for directory in (records, claims, dispositions):
        directory.mkdir()
    verifier = F8VerifierArtifact.model_validate(
        canonical_json_loads(Path(fixture.refs["verifier"].path).read_bytes()),
        strict=True,
    )
    adapter_path = tmp_path / "partial-reward.py"
    adapter_path.write_text(
        _reward_source(
            fixture.refs["task"],
            fixture.refs["carrier_manifest"],
            verifier,
            records,
            claims,
            dispositions,
        ),
        encoding="utf-8",
    )
    carrier = fixture.carrier_values[0]
    digest_hex = carrier["carrier_digest"].removeprefix("sha256:")
    (records / f"{digest_hex}.json").write_bytes(b"partial")
    group_refs = fixture.carrier_refs[:4]
    group_carriers = fixture.carrier_values[:4]
    metadata = {
        "f8_carrier_refs": [ref.model_dump(mode="json") for ref in group_refs],
        "f8_carrier_digests": [item["carrier_digest"] for item in group_carriers],
        "f8_optimizer_step": group_carriers[0]["optimizer_step"],
        "task_row_id": group_carriers[0]["task_row_id"],
        "split": "train",
    }
    with pytest.raises(FileExistsError):
        _invoke_reward(str(adapter_path), metadata, "0")
    failure = canonical_json_loads((dispositions / f"{digest_hex}.json").read_bytes())
    assert failure["state"] == "failed"
    with pytest.raises(Exception):
        _verified_rollout_refs(records, dispositions, fixture.refs["carrier_manifest"])


def test_reward_callback_rejects_mutated_ground_truth(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    records = tmp_path / "truth-records"
    claims = tmp_path / "truth-claims"
    dispositions = tmp_path / "truth-dispositions"
    for directory in (records, claims, dispositions):
        directory.mkdir()
    verifier = F8VerifierArtifact.model_validate_json(
        Path(fixture.refs["verifier"].path).read_bytes(), strict=True
    )
    adapter_path = tmp_path / "truth-reward.py"
    adapter_path.write_text(
        _reward_source(
            fixture.refs["task"],
            fixture.refs["carrier_manifest"],
            verifier,
            records,
            claims,
            dispositions,
        ),
        encoding="utf-8",
    )
    group_refs = fixture.carrier_refs[:4]
    group_carriers = fixture.carrier_values[:4]
    metadata = {
        "f8_carrier_refs": [ref.model_dump(mode="json") for ref in group_refs],
        "f8_carrier_digests": [item["carrier_digest"] for item in group_carriers],
        "f8_optimizer_step": group_carriers[0]["optimizer_step"],
        "task_row_id": group_carriers[0]["task_row_id"],
        "split": "train",
    }
    with pytest.raises(RuntimeError, match="pinned task/carrier"):
        _invoke_reward(str(adapter_path), metadata, "mutated-ground-truth")


def test_exact_carriers_are_claimed_directly_under_multiprocess_race(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    records, claims, dispositions = (
        tmp_path / "race-records",
        tmp_path / "race-claims",
        tmp_path / "race-dispositions",
    )
    for directory in (records, claims, dispositions):
        directory.mkdir()
    verifier = F8VerifierArtifact.model_validate(
        canonical_json_loads(Path(fixture.refs["verifier"].path).read_bytes()),
        strict=True,
    )
    adapter_path = tmp_path / "race-reward.py"
    adapter_path.write_text(
        _reward_source(
            fixture.refs["task"],
            fixture.refs["carrier_manifest"],
            verifier,
            records,
            claims,
            dispositions,
        ),
        encoding="utf-8",
    )
    task = canonical_json_loads(Path(fixture.refs["task"].path).read_bytes())
    ground_truth_by_row = {
        row["row_id"]: row["ground_truth"] for row in task["train_rows"]
    }
    jobs = []
    for start in range(0, 8, 4):
        group_refs = fixture.carrier_refs[start : start + 4]
        group_carriers = fixture.carrier_values[start : start + 4]
        metadata = {
            "f8_carrier_refs": [ref.model_dump(mode="json") for ref in group_refs],
            "f8_carrier_digests": [item["carrier_digest"] for item in group_carriers],
            "f8_optimizer_step": group_carriers[0]["optimizer_step"],
            "task_row_id": group_carriers[0]["task_row_id"],
            "split": "train",
        }
        jobs.append(
            (
                str(adapter_path),
                metadata,
                ground_truth_by_row[group_carriers[0]["task_row_id"]],
            )
        )
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                _invoke_reward, *(list(values) for values in zip(*jobs, strict=True))
            )
        )
    assert results == [1.0] * 2
    observed_workers = {
        (
            claim["claimant_pid"],
            claim["claimant_thread_id"],
        )
        for claim in (
            canonical_json_loads(path.read_bytes()) for path in claims.glob("*.json")
        )
    }
    assert len(observed_workers) >= 4
    duplicate = jobs[0]
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_invoke_reward, *duplicate) for _ in range(2)]
        errors = 0
        for future in futures:
            try:
                future.result()
            except FileExistsError:
                errors += 1
    assert errors == 2
    with pytest.raises(RuntimeError, match="aligned"):
        _invoke_reward(str(adapter_path), {}, "0")


def test_slurm_observation_only_finalizes_pending_target(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    pending = fixture.target_spec.model_copy(
        update={
            "target": F8TargetIdentity(
                target_run_id="f8-pending", command_id="command-800", job_id="pending"
            ),
            "slurm_job_id_source": "SLURM_JOB_ID",
        }
    )
    effective = _effective_target_spec(pending, {"SLURM_JOB_ID": "900"})
    assert effective.target.target_run_id == "f8-900"
    assert effective.target.job_id == "900"
    assert effective.identity_artifacts == pending.identity_artifacts


def test_failure_lifecycle_can_truthfully_record_unreaped_resources() -> None:
    quarantine = F8ImmutableJSONRef(path="/tmp/quarantine.json", digest=_sha(b"q"))
    record = F8TerminalLifecycleRecord(
        schema_version="bb.rl.phase5-f8-terminal-lifecycle.v1",
        target=F8TargetIdentity(
            target_run_id="run", command_id="command", job_id="job"
        ),
        terminal_state="failed_quarantined",
        trainer_pid=123,
        trainer_pgid=123,
        trainer_returncode=124,
        timed_out=True,
        termination_signals=("SIGTERM", "SIGKILL"),
        process_group_reaped=False,
        stdout_reader_joined=False,
        remaining_process_ids=(123,),
        remaining_container_ids=(),
        active_lease_ids=(),
        cleanup_errors=("bounded_reap_expired",),
        retained_checkpoint_ref=None,
        quarantined_artifacts=(quarantine,),
    )
    assert record.process_group_reaped is False


def test_canonical_input_rejects_legacy_control_fields(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    value = canonical_json_loads(fixture.target_input_path.read_bytes())
    value["model_ref"] = "/mutable/model"
    path = tmp_path / "hostile-input.json"
    path.write_bytes(canonical_json_bytes(value))
    with pytest.raises(Exception):
        _read_f8_input(str(path))
