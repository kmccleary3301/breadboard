from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


FIDELITY_CLASSES = {
    "F0": "message_only",
    "F1": "tokenized_posthoc",
    "F2": "token_native",
    "F3": "token_native_with_logprobs",
}


def _text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_list(value: Any, field_name: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [int(item) for item in value]


def _bool_list(value: Any, field_name: str) -> list[bool]:
    if value is None:
        return []
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


def _mapping_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    copied: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name}[{index}] must be a mapping")
        copied.append(dict(item))
    return copied


@dataclass(frozen=True)
class RendererIdentity:
    renderer_id: str
    renderer_version: str
    renderer_config_hash: str
    tokenizer_id: str
    tokenizer_hash: str
    chat_template_id: str
    chat_template_hash: str
    stop_token_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "renderer_id", _text(self.renderer_id, "renderer_id"))
        object.__setattr__(self, "renderer_version", _text(self.renderer_version, "renderer_version"))
        object.__setattr__(
            self,
            "renderer_config_hash",
            _text(self.renderer_config_hash, "renderer_config_hash"),
        )
        object.__setattr__(self, "tokenizer_id", _text(self.tokenizer_id, "tokenizer_id"))
        object.__setattr__(self, "tokenizer_hash", _text(self.tokenizer_hash, "tokenizer_hash"))
        object.__setattr__(self, "chat_template_id", _text(self.chat_template_id, "chat_template_id"))
        object.__setattr__(
            self,
            "chat_template_hash",
            _text(self.chat_template_hash, "chat_template_hash"),
        )
        object.__setattr__(self, "stop_token_ids", [int(item) for item in self.stop_token_ids])

    def to_dict(self) -> dict[str, Any]:
        return {
            "renderer_id": self.renderer_id,
            "renderer_version": self.renderer_version,
            "renderer_config_hash": self.renderer_config_hash,
            "tokenizer_id": self.tokenizer_id,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_id": self.chat_template_id,
            "chat_template_hash": self.chat_template_hash,
            "stop_token_ids": list(self.stop_token_ids),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RendererIdentity":
        return RendererIdentity(
            renderer_id=data.get("renderer_id") or "",
            renderer_version=data.get("renderer_version") or "",
            renderer_config_hash=data.get("renderer_config_hash") or "",
            tokenizer_id=data.get("tokenizer_id") or "",
            tokenizer_hash=data.get("tokenizer_hash") or "",
            chat_template_id=data.get("chat_template_id") or "",
            chat_template_hash=data.get("chat_template_hash") or "",
            stop_token_ids=_int_list(data.get("stop_token_ids"), "renderer.stop_token_ids"),
        )


@dataclass(frozen=True)
class BridgeToNextTurn:
    attempted: bool
    success: bool
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        failure_reason = _optional_text(self.failure_reason)
        object.__setattr__(self, "attempted", bool(self.attempted))
        object.__setattr__(self, "success", bool(self.success))
        object.__setattr__(self, "failure_reason", failure_reason)
        if not self.attempted and self.success:
            raise ValueError("bridge_to_next_turn cannot succeed when it was not attempted")
        if self.attempted and not self.success and not failure_reason:
            raise ValueError("bridge_to_next_turn failure requires failure_reason")

    def to_dict(self) -> dict[str, Any]:
        payload = {"attempted": self.attempted, "success": self.success}
        if self.failure_reason:
            payload["failure_reason"] = self.failure_reason
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BridgeToNextTurn":
        return BridgeToNextTurn(
            attempted=bool(data.get("attempted")),
            success=bool(data.get("success")),
            failure_reason=data.get("failure_reason"),
        )


@dataclass(frozen=True)
class ProviderFidelity:
    fidelity_class: str
    token_ids_source: str
    logprobs_source: str
    provider: str
    model_requested: str
    model_served: str
    sampling_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        fidelity_class = _text(self.fidelity_class, "fidelity_class").upper()
        if fidelity_class not in FIDELITY_CLASSES:
            raise ValueError(f"fidelity_class must be one of {sorted(FIDELITY_CLASSES)}")
        object.__setattr__(self, "fidelity_class", fidelity_class)
        object.__setattr__(self, "token_ids_source", _text(self.token_ids_source, "token_ids_source"))
        object.__setattr__(self, "logprobs_source", _text(self.logprobs_source, "logprobs_source"))
        object.__setattr__(self, "provider", _text(self.provider, "provider"))
        object.__setattr__(self, "model_requested", _text(self.model_requested, "model_requested"))
        object.__setattr__(self, "model_served", _text(self.model_served, "model_served"))
        object.__setattr__(self, "sampling_config", dict(self.sampling_config or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fidelity_class": self.fidelity_class,
            "fidelity_name": FIDELITY_CLASSES[self.fidelity_class],
            "token_ids_source": self.token_ids_source,
            "logprobs_source": self.logprobs_source,
            "provider": self.provider,
            "model_requested": self.model_requested,
            "model_served": self.model_served,
            "sampling_config": dict(self.sampling_config),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ProviderFidelity":
        return ProviderFidelity(
            fidelity_class=data.get("fidelity_class") or "",
            token_ids_source=data.get("token_ids_source") or "",
            logprobs_source=data.get("logprobs_source") or "",
            provider=data.get("provider") or "",
            model_requested=data.get("model_requested") or "",
            model_served=data.get("model_served") or "",
            sampling_config=_mapping(data.get("sampling_config"), "provider_fidelity.sampling_config"),
        )


@dataclass(frozen=True)
class RenderedTurnRecord:
    rollout_id: str
    trajectory_id: str
    task_id: str
    split_id: str
    env_package_hash: str
    turn_id: str
    renderer: RendererIdentity
    provider_fidelity: ProviderFidelity
    prompt_ids: list[int]
    completion_ids: list[int]
    input_ids: list[int]
    attention_mask: list[int]
    loss_mask: list[bool]
    assistant_mask: list[bool]
    tool_action_mask: list[bool]
    reward_mask: list[bool]
    sampled_mask: list[bool]
    message_indices: list[int]
    bridge_to_next_turn: BridgeToNextTurn
    tool_parse_status: str = "not_applicable"
    parsed_completion: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    completion_logprobs: list[float] | None = None
    finish_reason: str = "stop"
    is_truncated: bool = False
    overlong_prompt: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in [
            "rollout_id",
            "trajectory_id",
            "task_id",
            "split_id",
            "env_package_hash",
            "turn_id",
        ]:
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        renderer = self.renderer if isinstance(self.renderer, RendererIdentity) else RendererIdentity.from_dict(self.renderer)
        fidelity = (
            self.provider_fidelity
            if isinstance(self.provider_fidelity, ProviderFidelity)
            else ProviderFidelity.from_dict(self.provider_fidelity)
        )
        bridge = (
            self.bridge_to_next_turn
            if isinstance(self.bridge_to_next_turn, BridgeToNextTurn)
            else BridgeToNextTurn.from_dict(self.bridge_to_next_turn)
        )
        object.__setattr__(self, "renderer", renderer)
        object.__setattr__(self, "provider_fidelity", fidelity)
        object.__setattr__(self, "bridge_to_next_turn", bridge)
        for field_name in ["prompt_ids", "completion_ids", "input_ids", "attention_mask", "message_indices"]:
            object.__setattr__(self, field_name, [int(item) for item in getattr(self, field_name)])
        for field_name in ["loss_mask", "assistant_mask", "tool_action_mask", "reward_mask", "sampled_mask"]:
            object.__setattr__(self, field_name, [bool(item) for item in getattr(self, field_name)])
        if self.completion_logprobs is not None:
            object.__setattr__(self, "completion_logprobs", [float(item) for item in self.completion_logprobs])
        object.__setattr__(self, "tool_parse_status", _text(self.tool_parse_status, "tool_parse_status"))
        object.__setattr__(self, "parsed_completion", _optional_text(self.parsed_completion))
        object.__setattr__(self, "tool_calls", [dict(item) for item in self.tool_calls])
        object.__setattr__(self, "finish_reason", _text(self.finish_reason, "finish_reason"))
        object.__setattr__(self, "is_truncated", bool(self.is_truncated))
        object.__setattr__(self, "overlong_prompt", bool(self.overlong_prompt))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rollout_id": self.rollout_id,
            "trajectory_id": self.trajectory_id,
            "task_id": self.task_id,
            "split_id": self.split_id,
            "env_package_hash": self.env_package_hash,
            "turn_id": self.turn_id,
            "renderer": self.renderer.to_dict(),
            "provider_fidelity": self.provider_fidelity.to_dict(),
            "prompt_ids": list(self.prompt_ids),
            "completion_ids": list(self.completion_ids),
            "input_ids": list(self.input_ids),
            "attention_mask": list(self.attention_mask),
            "loss_mask": list(self.loss_mask),
            "assistant_mask": list(self.assistant_mask),
            "tool_action_mask": list(self.tool_action_mask),
            "reward_mask": list(self.reward_mask),
            "sampled_mask": list(self.sampled_mask),
            "message_indices": list(self.message_indices),
            "bridge_to_next_turn": self.bridge_to_next_turn.to_dict(),
            "tool_parse_status": self.tool_parse_status,
            "tool_calls": [dict(item) for item in self.tool_calls],
            "finish_reason": self.finish_reason,
            "is_truncated": self.is_truncated,
            "overlong_prompt": self.overlong_prompt,
            "metadata": dict(self.metadata),
        }
        if self.parsed_completion:
            payload["parsed_completion"] = self.parsed_completion
        if self.completion_logprobs is not None:
            payload["completion_logprobs"] = list(self.completion_logprobs)
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RenderedTurnRecord":
        return RenderedTurnRecord(
            rollout_id=data.get("rollout_id") or "",
            trajectory_id=data.get("trajectory_id") or "",
            task_id=data.get("task_id") or "",
            split_id=data.get("split_id") or "",
            env_package_hash=data.get("env_package_hash") or "",
            turn_id=data.get("turn_id") or "",
            renderer=RendererIdentity.from_dict(_mapping(data.get("renderer"), "renderer")),
            provider_fidelity=ProviderFidelity.from_dict(_mapping(data.get("provider_fidelity"), "provider_fidelity")),
            prompt_ids=_int_list(data.get("prompt_ids"), "prompt_ids"),
            completion_ids=_int_list(data.get("completion_ids"), "completion_ids"),
            input_ids=_int_list(data.get("input_ids"), "input_ids"),
            attention_mask=_int_list(data.get("attention_mask"), "attention_mask"),
            loss_mask=_bool_list(data.get("loss_mask"), "loss_mask"),
            assistant_mask=_bool_list(data.get("assistant_mask"), "assistant_mask"),
            tool_action_mask=_bool_list(data.get("tool_action_mask"), "tool_action_mask"),
            reward_mask=_bool_list(data.get("reward_mask"), "reward_mask"),
            sampled_mask=_bool_list(data.get("sampled_mask"), "sampled_mask"),
            message_indices=_int_list(data.get("message_indices"), "message_indices"),
            bridge_to_next_turn=BridgeToNextTurn.from_dict(
                _mapping(data.get("bridge_to_next_turn"), "bridge_to_next_turn")
            ),
            tool_parse_status=data.get("tool_parse_status") or "not_applicable",
            parsed_completion=data.get("parsed_completion"),
            tool_calls=_mapping_list(data.get("tool_calls"), "tool_calls"),
            completion_logprobs=_float_list(data.get("completion_logprobs"), "completion_logprobs"),
            finish_reason=data.get("finish_reason") or "stop",
            is_truncated=bool(data.get("is_truncated")),
            overlong_prompt=bool(data.get("overlong_prompt")),
            metadata=_mapping(data.get("metadata"), "metadata"),
        )
