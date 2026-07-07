from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from breadboard.rl.phase2.bridge import build_verl_batch_from_projection_rows
from breadboard.rl.phase3.evidence import sha256_file, validate_phase3_command_log_manifest

PHASE3_DATAPROTO_SCHEMA = "bb.rl.phase3.verl_dataproto.v1"
PHASE3_TRAINER_UPDATE_SCHEMA = "bb.rl.phase3.verl_trainer_update.v1"
PHASE3_TRAINER_CLAIM_BOUNDARY = "phase3_verl_ppo_grpo_weight_update_named_target_scope"


@dataclass(frozen=True)
class Phase3TrainerRunSpec:
    target_run_id: str
    trainer_backend: Literal["verl_ppo", "verl_grpo"]
    model_ref: str
    projection_rows_path: Path
    output_dir: Path
    max_steps: int
    expected_device_count: int = 8
    require_weight_update: bool = True


def _as_payload(batch: Mapping[str, Any]) -> Mapping[str, Any]:
    if "tensors" in batch and "row_refs" in batch:
        return batch
    rows = batch.get("rows") if isinstance(batch, Mapping) else None
    if rows is not None:
        return build_verl_batch_from_projection_rows(rows, target_run_id=str(batch["target_run_id"])).to_dict()
    return batch


def _tensor(torch: Any, values: Any, *, device: str, dtype: Any | None = None) -> Any:
    try:
        return torch.tensor(values, device=device, dtype=dtype)
    except TypeError:
        return torch.tensor(values)


def _dataproto_non_tensors(values: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return dict(values)
    return {key: np.asarray(value, dtype=object) for key, value in values.items()}


def _make_dataproto(DataProto: Any, payload: dict[str, Any]) -> Any:
    if hasattr(DataProto, "from_dict"):
        try:
            return DataProto.from_dict(
                tensors=dict(payload["batch"].items()),
                non_tensors=_dataproto_non_tensors(payload.get("non_tensor_batch", {})),
                meta_info=payload.get("meta_info", {}),
            )
        except (AttributeError, TypeError, ValueError, AssertionError, ImportError):
            try:
                return DataProto.from_dict(payload)
            except (AttributeError, TypeError, ValueError, AssertionError):
                pass
    if hasattr(DataProto, "from_single_dict"):
        try:
            return DataProto.from_single_dict(payload)
        except (AttributeError, TypeError, ValueError, AssertionError):
            pass
    try:
        return DataProto(batch=payload["batch"], non_tensor_batch=_dataproto_non_tensors(payload.get("non_tensor_batch", {})), meta_info=payload.get("meta_info", {}))
    except TypeError:
        instance = DataProto()
        for key, value in payload.items():
            setattr(instance, key, value)
        return instance


def build_phase3_dataproto(batch: Mapping[str, Any], *, device: str, require_grpo_uid: bool) -> Any:
    import torch  # type: ignore
    from tensordict import TensorDict  # type: ignore
    from verl.protocol import DataProto  # type: ignore

    payload = _as_payload(batch)
    row_refs = [dict(row) for row in payload.get("row_refs", [])]
    if not row_refs:
        raise ValueError("row_refs must be present")
    policy_snapshot_id = str(payload.get("policy_snapshot_id") or "")
    target_run_id = str(payload.get("target_run_id") or "")
    if not policy_snapshot_id:
        raise ValueError("policy_snapshot_id must be present")
    if not target_run_id:
        raise ValueError("target_run_id must be present")
    if payload.get("field_ledger", {}).get("provenance_loss_detected"):
        raise ValueError("projection provenance loss is not allowed")
    tensors = payload.get("tensors", {})
    masks = payload.get("masks", {})
    rewards = payload.get("rewards", {})
    logprobs = payload.get("logprobs", {})
    uid: list[str] = []
    for row_ref in row_refs:
        value = row_ref.get("group_id") if row_ref.get("group_id") else row_ref.get("task_id")
        if not value:
            raise ValueError("uid must be present for every row")
        uid.append(str(value))
    if require_grpo_uid:
        singles = sorted(key for key, count in Counter(uid).items() if count < 2)
        if singles:
            raise ValueError("GRPO uid groups must contain at least two rows: " + ",".join(singles))
    batch_tensors = TensorDict(
        {
            "input_ids": _tensor(torch, tensors.get("input_ids"), device=device, dtype=getattr(torch, "long", None)),
            "attention_mask": _tensor(torch, tensors.get("attention_mask"), device=device, dtype=getattr(torch, "long", None)),
            "responses": _tensor(torch, tensors.get("input_ids"), device=device, dtype=getattr(torch, "long", None)),
            "response_mask": _tensor(torch, masks.get("completion_logprob_mask") or masks.get("loss_mask"), device=device),
            "token_level_rewards": _tensor(torch, rewards.get("token_rewards"), device=device),
            "old_log_probs": _tensor(torch, logprobs.get("completion_logprobs"), device=device),
        },
        batch_size=[len(row_refs)],
    )
    return _make_dataproto(
        DataProto,
        {
            "batch": batch_tensors,
            "non_tensor_batch": {"uid": uid, "row_refs": row_refs},
            "meta_info": {
                "target_run_id": target_run_id,
                "policy_snapshot_id": policy_snapshot_id,
                "claim_boundary": PHASE3_TRAINER_CLAIM_BOUNDARY,
                "schema_version": PHASE3_DATAPROTO_SCHEMA,
            },
        },
    )


def _read_metrics(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_command_sha(manifest: Mapping[str, Any]) -> str:
    rows = manifest.get("commands", manifest.get("command_logs", []))
    if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
        return str(rows[0].get("raw_log_sha256") or "")
    return ""


def _derive_evidence_root(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    parts = resolved.parts
    for index in range(len(parts) - 2):
        if parts[index] == "ZYPHRA" and parts[index + 1] == "RL_PHASE_3" and parts[index + 2] == "runs":
            return Path(*parts[:index])
    return resolved


def build_phase3_trainer_update_report(
    spec: Phase3TrainerRunSpec, *, command_log_manifest: Mapping[str, Any], checkpoint_before: Path,
    checkpoint_after: Path, metrics_path: Path
) -> dict[str, Any]:
    metrics = _read_metrics(metrics_path)
    before_sha = sha256_file(checkpoint_before) if checkpoint_before.exists() else ""
    after_sha = sha256_file(checkpoint_after) if checkpoint_after.exists() else ""
    optimizer_steps = int(metrics.get("optimizer_step_count") or metrics.get("optimizer_steps") or 0)
    device_count = int(metrics.get("device_count") or spec.expected_device_count)
    manifest_errors = validate_phase3_command_log_manifest(
        command_log_manifest,
        target_run_id=spec.target_run_id,
        repo_root=Path.cwd(),
        evidence_root=_derive_evidence_root(spec.output_dir),
    )
    checkpoint_changed = bool(before_sha and after_sha and before_sha != after_sha)
    weight_update = optimizer_steps >= 1 and checkpoint_changed and bool(metrics.get("weight_update_performed", True))
    passed = not manifest_errors and optimizer_steps >= 1 and checkpoint_changed and weight_update and device_count == spec.expected_device_count
    return {
        "schema_version": PHASE3_TRAINER_UPDATE_SCHEMA,
        "report_id": f"phase3_{spec.trainer_backend}_trainer_update",
        "claim_boundary": PHASE3_TRAINER_CLAIM_BOUNDARY,
        "target_run_id": spec.target_run_id,
        "trainer_backend": spec.trainer_backend,
        "model_ref": spec.model_ref,
        "optimizer_step_count": optimizer_steps,
        "checkpoint_before_sha256": before_sha,
        "checkpoint_after_sha256": after_sha,
        "checkpoint_changed": checkpoint_changed,
        "weight_update_performed": weight_update,
        "loss_metrics": metrics.get("loss_metrics", {}),
        "device_count": device_count,
        "raw_command_log_sha256": _first_command_sha(command_log_manifest),
        "manifest_validation_errors": manifest_errors,
        "input_hashes": {"metrics": sha256_file(metrics_path) if metrics_path.exists() else ""},
        "artifact_paths": {"checkpoint_after": str(checkpoint_after), "metrics": str(metrics_path)},
        "scorecard_update_allowed": False,
        "passed": passed,
    }


def validate_phase3_trainer_update_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != PHASE3_TRAINER_UPDATE_SCHEMA:
        errors.append("schema_version must be trainer update v1")
    if report.get("claim_boundary") != PHASE3_TRAINER_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be trainer live boundary")
    if int(report.get("optimizer_step_count") or 0) < 1:
        errors.append("optimizer_step_count must be at least 1")
    if report.get("checkpoint_changed") is not True:
        errors.append("checkpoint_changed must be true")
    if report.get("checkpoint_before_sha256") == report.get("checkpoint_after_sha256"):
        errors.append("checkpoint hashes must differ")
    if report.get("weight_update_performed") is not True:
        errors.append("weight_update_performed must be true")
    if int(report.get("device_count") or 0) != 8:
        errors.append("device_count must be 8")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("passed") is not True:
        errors.append("passed must be true")
    return errors
