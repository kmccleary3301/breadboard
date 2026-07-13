from __future__ import annotations

from breadboard.rl.renderer.records import classify_rendered_turn_trainability
from breadboard.rl.renderer.schema import RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_f3_non_truncated_record_is_on_policy_trainable() -> None:
    decision = classify_rendered_turn_trainability(RenderedTurnRecord.from_dict(cloned_payload()))

    assert decision.sft_trainable is True
    assert decision.on_policy_trainable is True
    assert decision.blocked_reasons == []


def test_finish_reason_length_blocks_trainability() -> None:
    payload = cloned_payload()
    payload["finish_reason"] = "length"

    decision = classify_rendered_turn_trainability(RenderedTurnRecord.from_dict(payload))
    assert "finish_reason=length" in decision.blocked_reasons
    assert decision.sft_trainable is False
    assert decision.on_policy_trainable is False


def test_overlong_prompt_blocks_trainability() -> None:
    payload = cloned_payload()
    payload["overlong_prompt"] = True

    decision = classify_rendered_turn_trainability(RenderedTurnRecord.from_dict(payload))
    assert "overlong_prompt" in decision.blocked_reasons
    assert decision.sft_trainable is False
