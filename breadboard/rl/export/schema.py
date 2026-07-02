from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


VERL_PROBE_SCHEMA = "bb.verl_probe_row.v1alpha"


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _int_list(value: Any, field_name: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [int(item) for item in value]


def _bool_list(value: Any, field_name: str) -> list[bool]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [bool(item) for item in value]


def _float_list(value: Any, field_name: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [float(item) for item in value]


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


@dataclass(frozen=True)
class VerlProbeRow:
    rollout_id: str
    trajectory_id: str
    episode_id: str
    task_id: str
    split_id: str
    env_package_id: str
    env_package_hash: str
    group_id: str
    policy: dict[str, Any]
    prompt_ids: list[int]
    completion_ids: list[int]
    input_ids: list[int]
    attention_mask: list[int]
    loss_mask: list[bool]
    assistant_mask: list[bool]
    tool_action_mask: list[bool]
    reward_mask: list[bool]
    completion_logprobs: list[float] | None
    completion_logprob_status: str
    renderer: dict[str, Any]
    reward: dict[str, Any]
    runtime: dict[str, Any]
    admission: dict[str, Any]
    projection_manifest_id: str
    trainable_candidate: bool = False
    schema_version: str = VERL_PROBE_SCHEMA
    claim_boundary: str = "verl_shaped_probe_not_trainer_ready"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "rollout_id": self.rollout_id,
            "trajectory_id": self.trajectory_id,
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "split_id": self.split_id,
            "env_package_id": self.env_package_id,
            "env_package_hash": self.env_package_hash,
            "group_id": self.group_id,
            "policy": dict(self.policy),
            "prompt_ids": list(self.prompt_ids),
            "completion_ids": list(self.completion_ids),
            "input_ids": list(self.input_ids),
            "attention_mask": list(self.attention_mask),
            "loss_mask": list(self.loss_mask),
            "assistant_mask": list(self.assistant_mask),
            "tool_action_mask": list(self.tool_action_mask),
            "reward_mask": list(self.reward_mask),
            "completion_logprob_status": self.completion_logprob_status,
            "renderer": dict(self.renderer),
            "reward": dict(self.reward),
            "runtime": dict(self.runtime),
            "admission": dict(self.admission),
            "projection_manifest_id": self.projection_manifest_id,
            "trainable_candidate": self.trainable_candidate,
            "claim_boundary": self.claim_boundary,
            "metadata": dict(self.metadata),
        }
        if self.completion_logprobs is not None:
            payload["completion_logprobs"] = list(self.completion_logprobs)
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "VerlProbeRow":
        return VerlProbeRow(
            schema_version=data.get("schema_version") or VERL_PROBE_SCHEMA,
            rollout_id=_require_text(data.get("rollout_id"), "rollout_id"),
            trajectory_id=_require_text(data.get("trajectory_id"), "trajectory_id"),
            episode_id=_require_text(data.get("episode_id"), "episode_id"),
            task_id=_require_text(data.get("task_id"), "task_id"),
            split_id=_require_text(data.get("split_id"), "split_id"),
            env_package_id=_require_text(data.get("env_package_id"), "env_package_id"),
            env_package_hash=_require_text(data.get("env_package_hash"), "env_package_hash"),
            group_id=_require_text(data.get("group_id"), "group_id"),
            policy=_mapping(data.get("policy"), "policy"),
            prompt_ids=_int_list(data.get("prompt_ids"), "prompt_ids"),
            completion_ids=_int_list(data.get("completion_ids"), "completion_ids"),
            input_ids=_int_list(data.get("input_ids"), "input_ids"),
            attention_mask=_int_list(data.get("attention_mask"), "attention_mask"),
            loss_mask=_bool_list(data.get("loss_mask"), "loss_mask"),
            assistant_mask=_bool_list(data.get("assistant_mask"), "assistant_mask"),
            tool_action_mask=_bool_list(data.get("tool_action_mask"), "tool_action_mask"),
            reward_mask=_bool_list(data.get("reward_mask"), "reward_mask"),
            completion_logprobs=_float_list(data.get("completion_logprobs"), "completion_logprobs"),
            completion_logprob_status=str(data.get("completion_logprob_status") or ""),
            renderer=_mapping(data.get("renderer"), "renderer"),
            reward=_mapping(data.get("reward"), "reward"),
            runtime=_mapping(data.get("runtime"), "runtime"),
            admission=_mapping(data.get("admission"), "admission"),
            projection_manifest_id=_require_text(data.get("projection_manifest_id"), "projection_manifest_id"),
            trainable_candidate=bool(data.get("trainable_candidate")),
            claim_boundary=str(data.get("claim_boundary") or "verl_shaped_probe_not_trainer_ready"),
            metadata=_mapping(data.get("metadata"), "metadata"),
        )
