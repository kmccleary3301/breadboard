from __future__ import annotations

import importlib.util
import json
import platform
from typing import Any, Mapping

from breadboard.rl.phase2.bridge import VERL_BATCH_CLAIM_BOUNDARY, VerlBatch


VERL_TRAINER_DRY_RUN_SCHEMA_VERSION = "bb.rl.phase2.verl_trainer_dry_run.v1alpha"
VERL_TRAINER_DRY_RUN_CLAIM_BOUNDARY = "phase2_trainer_dry_run_no_slurm_no_weight_update"
__all__ = [
    "VERL_TRAINER_DRY_RUN_CLAIM_BOUNDARY",
    "VERL_TRAINER_DRY_RUN_SCHEMA_VERSION",
    "build_trainer_dry_run_report",
    "report_to_json",
    "validate_trainer_dry_run_batch",
]

_ALLOWED_DRY_RUN_MODES = {"no_update", "one_step"}
_PROVENANCE_FIELDS = {
    "rollout_id",
    "trajectory_id",
    "episode_id",
    "task_id",
    "projection_manifest_id",
    "policy_snapshot_id",
}


def build_trainer_dry_run_report(
    batch: VerlBatch | Mapping[str, Any],
    *,
    target_run_id: str,
    mode: str = "no_update",
    device: str = "cpu",
) -> dict[str, Any]:
    payload = batch.to_dict() if isinstance(batch, VerlBatch) else dict(batch)
    target_run_id = _require_text(target_run_id, "target_run_id")
    mode = _require_text(mode, "mode")
    device = _require_text(device, "device")
    if mode not in _ALLOWED_DRY_RUN_MODES:
        raise ValueError("mode must be no_update or one_step")

    errors = validate_trainer_dry_run_batch(payload, target_run_id=target_run_id)
    if errors:
        raise ValueError("; ".join(errors))

    row_count = int(payload["row_count"])
    tensor_shape_metadata = payload["tensor_shape_metadata"]
    report = {
        "schema_version": VERL_TRAINER_DRY_RUN_SCHEMA_VERSION,
        "claim_boundary": VERL_TRAINER_DRY_RUN_CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "target_run_id": target_run_id,
        "batch_id": payload["batch_id"],
        "policy_snapshot_id": payload["policy_snapshot_id"],
        "mode": mode,
        "row_count": row_count,
        "dry_run_result": {
            "accepted": True,
            "optimizer_step_performed": False,
            "weight_update_performed": False,
            "slurm_execution_performed": False,
        },
        "device_evidence": _device_evidence(device),
        "batch_evidence": {
            "input_ids_shape": tensor_shape_metadata["input_ids"]["shape"],
            "completion_logprobs_shape": tensor_shape_metadata["completion_logprobs"]["shape"],
            "sequence_rewards_shape": tensor_shape_metadata["sequence_rewards"]["shape"],
            "has_reward_mask": "reward_mask" in payload["masks"],
            "has_completion_logprob_mask": "completion_logprob_mask" in payload["masks"],
        },
    }
    if mode == "one_step":
        report["dry_run_result"]["planned_step_count"] = 1
    else:
        report["dry_run_result"]["planned_step_count"] = 0
    return report


def validate_trainer_dry_run_batch(payload: Mapping[str, Any], *, target_run_id: str) -> list[str]:
    errors: list[str] = []
    if payload.get("claim_boundary") != VERL_BATCH_CLAIM_BOUNDARY:
        errors.append("batch claim_boundary is not a phase2 VeRL dry-run batch")
    if payload.get("scorecard_update_allowed") is not False:
        errors.append("batch scorecard_update_allowed must be false")
    if payload.get("target_run_id") != target_run_id:
        errors.append("batch target_run_id must match trainer target_run_id")
    if not str(payload.get("policy_snapshot_id") or "").strip():
        errors.append("batch policy_snapshot_id must be present")

    field_ledger = payload.get("field_ledger")
    if not isinstance(field_ledger, Mapping):
        errors.append("batch field_ledger must be present")
    else:
        lost_fields = set(field_ledger.get("lost_fields") or [])
        critical_lost_fields = set(field_ledger.get("critical_lost_fields") or [])
        critical_lost_fields.update(lost_fields.intersection(_PROVENANCE_FIELDS))
        if critical_lost_fields or field_ledger.get("provenance_loss_detected") is True:
            errors.append("trainer dry-run rejects provenance loss")

    try:
        row_count = int(payload.get("row_count"))
    except (TypeError, ValueError):
        errors.append("batch row_count must be an integer")
        row_count = 0
    if row_count <= 0:
        errors.append("batch row_count must be positive")

    tensors = payload.get("tensors")
    masks = payload.get("masks")
    logprobs = payload.get("logprobs")
    rewards = payload.get("rewards")
    shapes = payload.get("tensor_shape_metadata")
    if not isinstance(tensors, Mapping):
        errors.append("batch tensors must be present")
        tensors = {}
    if not isinstance(masks, Mapping):
        errors.append("batch masks must be present")
        masks = {}
    if not isinstance(logprobs, Mapping):
        errors.append("batch logprobs must be present")
        logprobs = {}
    if not isinstance(rewards, Mapping):
        errors.append("batch rewards must be present")
        rewards = {}
    if not isinstance(shapes, Mapping):
        errors.append("batch tensor_shape_metadata must be present")
        shapes = {}

    _validate_matrix_shape(tensors.get("input_ids"), shapes.get("input_ids"), row_count, "input_ids", errors)
    _validate_matrix_shape(tensors.get("attention_mask"), shapes.get("attention_mask"), row_count, "attention_mask", errors)
    for mask_name in (
        "loss_mask",
        "assistant_mask",
        "tool_action_mask",
        "reward_mask",
        "completion_logprob_mask",
    ):
        _validate_matrix_shape(masks.get(mask_name), shapes.get(mask_name), row_count, mask_name, errors)
    _validate_matrix_shape(
        logprobs.get("completion_logprobs"),
        shapes.get("completion_logprobs"),
        row_count,
        "completion_logprobs",
        errors,
    )
    _validate_matrix_shape(
        rewards.get("token_rewards"),
        shapes.get("token_rewards"),
        row_count,
        "token_rewards",
        errors,
    )
    _validate_vector_shape(
        rewards.get("sequence_rewards"),
        shapes.get("sequence_rewards"),
        row_count,
        "sequence_rewards",
        errors,
    )
    return errors


def report_to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(dict(report), sort_keys=True, separators=(",", ":")) + "\n"


def _validate_matrix_shape(
    matrix: Any,
    shape_metadata: Any,
    row_count: int,
    field_name: str,
    errors: list[str],
) -> None:
    if not isinstance(matrix, list):
        errors.append(f"batch {field_name} must be a matrix")
        return
    if not isinstance(shape_metadata, Mapping):
        errors.append(f"batch {field_name} shape metadata must be present")
        return
    shape = shape_metadata.get("shape")
    if not isinstance(shape, list) or len(shape) != 2:
        errors.append(f"batch {field_name} shape must be rank 2")
        return
    if shape[0] != row_count or len(matrix) != row_count:
        errors.append(f"batch {field_name} row dimension must match row_count")
    width = shape[1]
    for row_index, row in enumerate(matrix, start=1):
        if not isinstance(row, list) or len(row) != width:
            errors.append(f"batch {field_name} row {row_index} width must match shape")
            return


def _validate_vector_shape(
    vector: Any,
    shape_metadata: Any,
    row_count: int,
    field_name: str,
    errors: list[str],
) -> None:
    if not isinstance(vector, list):
        errors.append(f"batch {field_name} must be a vector")
        return
    if not isinstance(shape_metadata, Mapping):
        errors.append(f"batch {field_name} shape metadata must be present")
        return
    shape = shape_metadata.get("shape")
    if shape != [row_count]:
        errors.append(f"batch {field_name} shape must match row_count")
    if len(vector) != row_count:
        errors.append(f"batch {field_name} length must match row_count")


def _device_evidence(device: str) -> dict[str, Any]:
    torch_spec = importlib.util.find_spec("torch")
    return {
        "requested_device": device,
        "resolved_device": "cpu" if device == "cpu" else device,
        "torch_importable": torch_spec is not None,
        "python_implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "execution_backend": "dry_run_only_no_trainer_or_slurm",
    }


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text
