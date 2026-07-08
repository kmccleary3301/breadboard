from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


VERL_BATCH_SCHEMA_VERSION = "bb.rl.phase2.verl_batch.v1alpha"
VERL_BATCH_CLAIM_BOUNDARY = "phase2_verl_batch_dry_run_only_not_training_evidence"
__all__ = [
    "TensorShape",
    "VERL_BATCH_CLAIM_BOUNDARY",
    "VERL_BATCH_SCHEMA_VERSION",
    "VerlBatch",
    "build_verl_batch_from_projection_rows",
    "build_verl_dataproto_like_payload",
    "detect_verl_dataproto_api",
]


_MASK_FIELDS = (
    "attention_mask",
    "loss_mask",
    "assistant_mask",
    "tool_action_mask",
    "reward_mask",
)
_REQUIRED_PROVENANCE_FIELDS = (
    "rollout_id",
    "trajectory_id",
    "episode_id",
    "task_id",
    "projection_manifest_id",
    "policy_snapshot_id",
)
_PRESERVED_FIELDS = tuple(
    sorted(
        {
            *_REQUIRED_PROVENANCE_FIELDS,
            "admission",
            "completion_ids",
            "completion_logprob_status",
            "completion_logprobs",
            "env_package_hash",
            "env_package_id",
            "group_id",
            "input_ids",
            "metadata",
            "policy",
            "prompt_ids",
            "renderer",
            "reward",
            "runtime",
            "split_id",
            "trainable_candidate",
            *_MASK_FIELDS,
        }
    )
)


@dataclass(frozen=True)
class TensorShape:
    name: str
    dtype: str
    shape: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"dtype": self.dtype, "shape": list(self.shape)}


@dataclass(frozen=True)
class VerlBatch:
    batch_id: str
    target_run_id: str
    policy_snapshot_id: str
    row_refs: tuple[dict[str, Any], ...]
    tensors: dict[str, list[list[int]]]
    masks: dict[str, list[list[bool]]]
    logprobs: dict[str, list[list[float]]]
    rewards: dict[str, Any]
    tensor_shape_metadata: dict[str, TensorShape]
    field_ledger: dict[str, Any]
    verl_dataproto_api: dict[str, Any]
    schema_version: str = VERL_BATCH_SCHEMA_VERSION
    claim_boundary: str = VERL_BATCH_CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False

    @property
    def row_count(self) -> int:
        return len(self.row_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "batch_id": self.batch_id,
            "target_run_id": self.target_run_id,
            "policy_snapshot_id": self.policy_snapshot_id,
            "row_count": self.row_count,
            "row_refs": [dict(row_ref) for row_ref in self.row_refs],
            "tensors": self.tensors,
            "masks": self.masks,
            "logprobs": self.logprobs,
            "rewards": self.rewards,
            "tensor_shape_metadata": {
                name: self.tensor_shape_metadata[name].to_dict()
                for name in sorted(self.tensor_shape_metadata)
            },
            "field_ledger": self.field_ledger,
            "verl_dataproto_api": self.verl_dataproto_api,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def build_verl_dataproto_like_payload(batch: VerlBatch | Mapping[str, Any]) -> dict[str, Any]:
    """Return a DataProto-shaped payload while keeping real VeRL optional."""

    payload = batch.to_dict() if isinstance(batch, VerlBatch) else dict(batch)
    return {
        "batch": {
            "input_ids": payload["tensors"]["input_ids"],
            "attention_mask": payload["tensors"]["attention_mask"],
            "loss_mask": payload["masks"]["loss_mask"],
            "assistant_mask": payload["masks"]["assistant_mask"],
            "tool_action_mask": payload["masks"]["tool_action_mask"],
            "reward_mask": payload["masks"]["reward_mask"],
            "old_log_probs": payload["logprobs"]["completion_logprobs"],
            "old_log_probs_mask": payload["masks"]["completion_logprob_mask"],
            "token_rewards": payload["rewards"]["token_rewards"],
            "sequence_rewards": payload["rewards"]["sequence_rewards"],
        },
        "non_tensor_batch": {
            "row_refs": payload["row_refs"],
            "policy_snapshot_id": payload["policy_snapshot_id"],
            "target_run_id": payload["target_run_id"],
        },
        "meta_info": {
            "schema_version": payload["schema_version"],
            "claim_boundary": payload["claim_boundary"],
            "scorecard_update_allowed": payload["scorecard_update_allowed"],
            "tensor_shape_metadata": payload["tensor_shape_metadata"],
            "field_ledger": payload["field_ledger"],
            "verl_dataproto_api": payload["verl_dataproto_api"],
        },
    }


def detect_verl_dataproto_api(
    importer: Callable[[str], Any] = importlib.import_module,
) -> dict[str, Any]:
    """Detect the optional real VeRL DataProto API without making it a dependency."""

    attempts: list[dict[str, str]] = []
    for module_name in ("verl.protocol", "verl"):
        try:
            module = importer(module_name)
        except Exception as exc:
            attempts.append({"module": module_name, "error": exc.__class__.__name__})
            continue
        if hasattr(module, "DataProto"):
            return {
                "available": True,
                "required": False,
                "module": module_name,
                "symbol": "DataProto",
                "attempts": attempts,
            }
        attempts.append({"module": module_name, "error": "DataProto missing"})
    return {
        "available": False,
        "required": False,
        "module": None,
        "symbol": "DataProto",
        "attempts": attempts,
    }


def build_verl_batch_from_projection_rows(
    rows: Iterable[Any],
    *,
    target_run_id: str,
    batch_id: str | None = None,
) -> VerlBatch:
    materialized_rows = [_row_to_mapping(row) for row in rows]
    if not materialized_rows:
        raise ValueError("rows must contain at least one projection row")
    target_run_id = _require_text(target_run_id, "target_run_id")
    batch_id = _require_text(batch_id or f"{target_run_id}.verl_batch", "batch_id")

    errors: list[str] = []
    normalized_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(materialized_rows, start=1):
        normalized, row_errors = _normalize_row(row, row_index)
        normalized_rows.append(normalized)
        errors.extend(row_errors)

    policy_snapshot_ids = sorted({row["policy_snapshot_id"] for row in normalized_rows if row.get("policy_snapshot_id")})
    if len(policy_snapshot_ids) > 1:
        errors.append("all rows in a VeRL batch must share one policy_snapshot_id")
    if errors:
        raise ValueError("; ".join(errors))

    max_sequence_length = max(len(row["input_ids"]) for row in normalized_rows)
    max_completion_length = max(len(row["completion_ids"]) for row in normalized_rows)
    row_count = len(normalized_rows)

    tensors = {
        "input_ids": [_pad_ints(row["input_ids"], max_sequence_length) for row in normalized_rows],
        "attention_mask": [_pad_ints(row["attention_mask"], max_sequence_length) for row in normalized_rows],
    }
    masks = {
        field_name: [_pad_bools(row[field_name], max_sequence_length) for row in normalized_rows]
        for field_name in _MASK_FIELDS
        if field_name != "attention_mask"
    }
    masks["completion_logprob_mask"] = [
        [index < len(row["completion_logprobs"]) for index in range(max_completion_length)]
        for row in normalized_rows
    ]
    logprobs = {
        "completion_logprobs": [
            _pad_floats(row["completion_logprobs"], max_completion_length) for row in normalized_rows
        ]
    }

    sequence_rewards = [row["reward_scalar"] for row in normalized_rows]
    token_rewards = []
    for row in normalized_rows:
        reward_scalar = row["reward_scalar"]
        token_rewards.append(
            [reward_scalar if mask_value else 0.0 for mask_value in _pad_bools(row["reward_mask"], max_sequence_length)]
        )
    rewards = {"sequence_rewards": sequence_rewards, "token_rewards": token_rewards}

    tensor_shape_metadata = {
        "input_ids": TensorShape("input_ids", "int64", (row_count, max_sequence_length)),
        "attention_mask": TensorShape("attention_mask", "int64", (row_count, max_sequence_length)),
        "loss_mask": TensorShape("loss_mask", "bool", (row_count, max_sequence_length)),
        "assistant_mask": TensorShape("assistant_mask", "bool", (row_count, max_sequence_length)),
        "tool_action_mask": TensorShape("tool_action_mask", "bool", (row_count, max_sequence_length)),
        "reward_mask": TensorShape("reward_mask", "bool", (row_count, max_sequence_length)),
        "completion_logprobs": TensorShape("completion_logprobs", "float32", (row_count, max_completion_length)),
        "completion_logprob_mask": TensorShape("completion_logprob_mask", "bool", (row_count, max_completion_length)),
        "sequence_rewards": TensorShape("sequence_rewards", "float32", (row_count,)),
        "token_rewards": TensorShape("token_rewards", "float32", (row_count, max_sequence_length)),
    }

    return VerlBatch(
        batch_id=batch_id,
        target_run_id=target_run_id,
        policy_snapshot_id=policy_snapshot_ids[0],
        row_refs=tuple(_row_ref(row) for row in normalized_rows),
        tensors=tensors,
        masks=masks,
        logprobs=logprobs,
        rewards=rewards,
        tensor_shape_metadata=tensor_shape_metadata,
        field_ledger=_field_ledger(normalized_rows),
        verl_dataproto_api=detect_verl_dataproto_api(),
    )


def _row_to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    to_dict = getattr(row, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return payload
    raise TypeError("projection rows must be mappings or expose to_dict()")


def _normalize_row(row: Mapping[str, Any], row_index: int) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    normalized: dict[str, Any] = {"_source_fields": tuple(sorted(str(key) for key in row.keys()))}

    for field_name in ("input_ids", "prompt_ids", "completion_ids"):
        normalized[field_name] = _int_list(row.get(field_name), field_name, errors)
    normalized["attention_mask"] = _int_list(row.get("attention_mask"), "attention_mask", errors)
    for field_name in ("loss_mask", "assistant_mask", "tool_action_mask", "reward_mask"):
        normalized[field_name] = _bool_list(row.get(field_name), field_name, errors)

    completion_logprobs = row.get("completion_logprobs")
    normalized["completion_logprobs"] = _float_list(completion_logprobs, "completion_logprobs", errors)
    normalized["completion_logprob_status"] = str(row.get("completion_logprob_status") or "")

    policy_snapshot_id = _policy_snapshot_id(row)
    if not policy_snapshot_id:
        errors.append(f"row {row_index} policy_snapshot_id must be present")
    normalized["policy_snapshot_id"] = policy_snapshot_id

    admission = _mapping(row.get("admission"), "admission", errors)
    normalized["admission"] = admission
    if admission.get("quarantine_status") == "quarantined":
        errors.append(f"row {row_index} is quarantined and cannot enter a VeRL batch")
    elif admission.get("quarantine_status") != "clear":
        errors.append(f"row {row_index} quarantine_status must be clear")
    if admission.get("row_status") != "accepted":
        errors.append(f"row {row_index} row_status must be accepted")
    if admission.get("trainable") is not True or row.get("trainable_candidate") is False:
        errors.append(f"row {row_index} must be trainable")

    input_length = len(normalized["input_ids"])
    for field_name in _MASK_FIELDS:
        if len(normalized[field_name]) != input_length:
            errors.append(f"row {row_index} {field_name} length must equal input_ids length")
    if normalized["input_ids"] != [*normalized["prompt_ids"], *normalized["completion_ids"]]:
        errors.append(f"row {row_index} input_ids must equal prompt_ids + completion_ids")
    if len(normalized["completion_logprobs"]) != len(normalized["completion_ids"]):
        errors.append(f"row {row_index} completion_logprobs length must equal completion_ids length")
    if normalized["completion_logprob_status"] != "native_available":
        errors.append(f"row {row_index} completion_logprob_status must be native_available")

    reward = _mapping(row.get("reward"), "reward", errors)
    normalized["reward"] = reward
    normalized["reward_scalar"] = _reward_scalar(reward, row_index, errors)

    for field_name in _REQUIRED_PROVENANCE_FIELDS:
        if field_name == "policy_snapshot_id":
            continue
        value = row.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"row {row_index} {field_name} must be present")
        normalized[field_name] = value

    for field_name in (
        "env_package_id",
        "env_package_hash",
        "group_id",
        "split_id",
        "policy",
        "renderer",
        "runtime",
        "metadata",
        "trainable_candidate",
    ):
        if field_name in row:
            normalized[field_name] = row[field_name]
    return normalized, errors


def _policy_snapshot_id(row: Mapping[str, Any]) -> str:
    direct_value = row.get("policy_snapshot_id")
    if direct_value is not None and str(direct_value).strip():
        return str(direct_value).strip()
    policy = row.get("policy")
    if isinstance(policy, Mapping):
        for field_name in ("policy_snapshot_id", "snapshot_id", "checkpoint_ref"):
            value = policy.get(field_name)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _mapping(value: Any, field_name: str, errors: list[str]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    errors.append(f"{field_name} must be a mapping")
    return {}


def _int_list(value: Any, field_name: str, errors: list[str]) -> list[int]:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list")
        return []
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        errors.append(f"{field_name} must contain integers")
        return []


def _bool_list(value: Any, field_name: str, errors: list[str]) -> list[bool]:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list")
        return []
    return [bool(item) for item in value]


def _float_list(value: Any, field_name: str, errors: list[str]) -> list[float]:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list")
        return []
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        errors.append(f"{field_name} must contain floats")
        return []


def _reward_scalar(reward: Mapping[str, Any], row_index: int, errors: list[str]) -> float:
    try:
        return float(reward["scalar"])
    except KeyError:
        errors.append(f"row {row_index} reward.scalar must be present")
    except (TypeError, ValueError):
        errors.append(f"row {row_index} reward.scalar must be numeric")
    return 0.0


def _pad_ints(values: list[int], width: int) -> list[int]:
    return values + [0] * (width - len(values))


def _pad_bools(values: list[bool], width: int) -> list[bool]:
    return values + [False] * (width - len(values))


def _pad_floats(values: list[float], width: int) -> list[float]:
    return values + [0.0] * (width - len(values))


def _row_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rollout_id": row["rollout_id"],
        "trajectory_id": row["trajectory_id"],
        "episode_id": row["episode_id"],
        "task_id": row["task_id"],
        "projection_manifest_id": row["projection_manifest_id"],
        "policy_snapshot_id": row["policy_snapshot_id"],
        "reward_scalar": row["reward_scalar"],
    }


def _field_ledger(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    row_ledgers: list[dict[str, Any]] = []
    all_source_fields: set[str] = set()
    all_preserved_fields: set[str] = set()
    all_lost_fields: set[str] = set()
    for row in rows:
        source_fields = set(row["_source_fields"])
        preserved_fields = source_fields.intersection(_PRESERVED_FIELDS)
        if row.get("policy_snapshot_id"):
            preserved_fields.add("policy_snapshot_id")
        lost_fields = source_fields.difference(_PRESERVED_FIELDS)
        all_source_fields.update(source_fields)
        all_preserved_fields.update(preserved_fields)
        all_lost_fields.update(lost_fields)
        row_ledgers.append(
            {
                "task_id": row["task_id"],
                "preserved_fields": sorted(preserved_fields),
                "lost_fields": sorted(lost_fields),
            }
        )
    critical_lost_fields = sorted(all_lost_fields.intersection(_REQUIRED_PROVENANCE_FIELDS))
    return {
        "source_fields": sorted(all_source_fields),
        "preserved_fields": sorted(all_preserved_fields),
        "lost_fields": sorted(all_lost_fields),
        "critical_lost_fields": critical_lost_fields,
        "provenance_loss_detected": bool(critical_lost_fields),
        "row_ledgers": row_ledgers,
    }
