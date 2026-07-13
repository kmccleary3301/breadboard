from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.renderer.schema import RenderedTurnRecord


TOKEN_ALIGNED_FIELDS = [
    "attention_mask",
    "loss_mask",
    "assistant_mask",
    "tool_action_mask",
    "reward_mask",
    "sampled_mask",
    "message_indices",
]
VALID_TOOL_PARSE_STATUSES = {"not_applicable", "ok", "failed"}


@dataclass(frozen=True)
class TrainabilityDecision:
    sft_trainable: bool
    on_policy_trainable: bool
    fidelity_class: str
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sft_trainable": self.sft_trainable,
            "on_policy_trainable": self.on_policy_trainable,
            "fidelity_class": self.fidelity_class,
            "blocked_reasons": list(self.blocked_reasons),
        }


def validate_rendered_turn(record: RenderedTurnRecord) -> list[str]:
    errors: list[str] = []
    input_len = len(record.input_ids)

    if record.input_ids != [*record.prompt_ids, *record.completion_ids]:
        errors.append("input_ids must equal prompt_ids + completion_ids")
    if not record.input_ids:
        errors.append("input_ids must be non-empty")
    if not record.completion_ids:
        errors.append("completion_ids must be non-empty")

    for field_name in TOKEN_ALIGNED_FIELDS:
        if len(getattr(record, field_name)) != input_len:
            errors.append(f"{field_name} length must equal input_ids length")

    if len(record.attention_mask) == input_len and any(item not in {0, 1} for item in record.attention_mask):
        errors.append("attention_mask values must be 0 or 1")

    if record.completion_logprobs is not None and len(record.completion_logprobs) != len(record.completion_ids):
        errors.append("completion_logprobs length must equal completion_ids length")

    if record.tool_parse_status not in VALID_TOOL_PARSE_STATUSES:
        errors.append(f"tool_parse_status must be one of {sorted(VALID_TOOL_PARSE_STATUSES)}")
    if record.tool_calls and record.tool_parse_status != "ok":
        errors.append("tool_calls require tool_parse_status=ok")
    if record.tool_calls and not any(record.tool_action_mask):
        errors.append("tool_calls require at least one true tool_action_mask entry")

    completion_start = len(record.prompt_ids)
    if completion_start < input_len:
        completion_assistant_mask = record.assistant_mask[completion_start:]
        if not any(completion_assistant_mask):
            errors.append("completion tokens require at least one assistant_mask entry")
        if not any(record.sampled_mask[completion_start:]):
            errors.append("completion tokens require sampled_mask entries")

    if record.provider_fidelity.fidelity_class == "F0":
        if record.provider_fidelity.token_ids_source != "unavailable":
            errors.append("F0 provider fidelity requires token_ids_source=unavailable")
        if record.provider_fidelity.logprobs_source != "unavailable":
            errors.append("F0 provider fidelity requires logprobs_source=unavailable")
    if record.provider_fidelity.fidelity_class == "F1":
        if record.provider_fidelity.token_ids_source != "posthoc_tokenizer":
            errors.append("F1 provider fidelity requires token_ids_source=posthoc_tokenizer")
        if record.provider_fidelity.logprobs_source not in {"unavailable", "posthoc_actor"}:
            errors.append("F1 provider fidelity requires logprobs_source unavailable or posthoc_actor")
    if record.provider_fidelity.fidelity_class == "F2":
        if record.provider_fidelity.token_ids_source != "provider_native":
            errors.append("F2 provider fidelity requires token_ids_source=provider_native")
        if record.provider_fidelity.logprobs_source != "unavailable":
            errors.append("F2 provider fidelity requires logprobs_source=unavailable")
    if record.provider_fidelity.fidelity_class == "F3":
        if record.provider_fidelity.token_ids_source != "provider_native":
            errors.append("F3 provider fidelity requires token_ids_source=provider_native")
        if record.provider_fidelity.logprobs_source != "provider_native":
            errors.append("F3 provider fidelity requires logprobs_source=provider_native")
        if record.completion_logprobs is None:
            errors.append("F3 provider fidelity requires completion_logprobs")

    return errors


def classify_rendered_turn_trainability(record: RenderedTurnRecord) -> TrainabilityDecision:
    blocked_reasons = validate_rendered_turn(record)

    if record.finish_reason == "length":
        blocked_reasons.append("finish_reason=length")
    if record.is_truncated:
        blocked_reasons.append("is_truncated")
    if record.overlong_prompt:
        blocked_reasons.append("overlong_prompt")
    if record.bridge_to_next_turn.attempted and not record.bridge_to_next_turn.success:
        blocked_reasons.append("bridge_to_next_turn_failed")

    fidelity_class = record.provider_fidelity.fidelity_class
    if fidelity_class == "F0":
        blocked_reasons.append("message_only_provider_fidelity")

    sft_trainable = not blocked_reasons and fidelity_class in {"F1", "F2", "F3"}
    on_policy_trainable = not blocked_reasons and fidelity_class == "F3"
    if fidelity_class in {"F1", "F2"}:
        blocked_reasons.append("on_policy_requires_f3_token_native_logprobs")
    if fidelity_class == "F0":
        sft_trainable = False
        on_policy_trainable = False

    return TrainabilityDecision(
        sft_trainable=sft_trainable,
        on_policy_trainable=on_policy_trainable,
        fidelity_class=fidelity_class,
        blocked_reasons=list(dict.fromkeys(blocked_reasons)),
    )
