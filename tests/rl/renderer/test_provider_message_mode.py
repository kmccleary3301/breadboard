from __future__ import annotations

from breadboard.rl.renderer.records import classify_rendered_turn_trainability, validate_rendered_turn
from breadboard.rl.renderer.schema import RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_message_only_f0_is_debug_only_not_trainable() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload(fidelity_class="F0"))

    assert validate_rendered_turn(record) == []
    decision = classify_rendered_turn_trainability(record)
    assert decision.sft_trainable is False
    assert decision.on_policy_trainable is False
    assert "message_only_provider_fidelity" in decision.blocked_reasons


def test_posthoc_f1_can_be_sft_but_not_on_policy() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload(fidelity_class="F1"))

    decision = classify_rendered_turn_trainability(record)
    assert decision.sft_trainable is True
    assert decision.on_policy_trainable is False
    assert "on_policy_requires_f3_token_native_logprobs" in decision.blocked_reasons


def test_f2_token_native_without_logprobs_is_not_on_policy() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload(fidelity_class="F2"))

    decision = classify_rendered_turn_trainability(record)
    assert decision.sft_trainable is True
    assert decision.on_policy_trainable is False
    assert "on_policy_requires_f3_token_native_logprobs" in decision.blocked_reasons


def test_f3_requires_completion_logprobs() -> None:
    payload = cloned_payload(fidelity_class="F3")
    payload.pop("completion_logprobs")

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "F3 provider fidelity requires completion_logprobs" in errors
