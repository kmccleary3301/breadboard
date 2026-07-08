from __future__ import annotations

from breadboard.rl.renderer.records import validate_rendered_turn
from breadboard.rl.renderer.schema import RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_valid_token_mask_lengths_pass() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload())

    assert validate_rendered_turn(record) == []


def test_input_ids_must_match_prompt_plus_completion() -> None:
    payload = cloned_payload()
    payload["input_ids"] = [10, 11, 12, 99, 21]

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "input_ids must equal prompt_ids + completion_ids" in errors


def test_masks_must_align_to_input_ids_length() -> None:
    payload = cloned_payload()
    payload["loss_mask"] = [False, False]

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "loss_mask length must equal input_ids length" in errors


def test_completion_logprobs_must_align_to_completion_ids() -> None:
    payload = cloned_payload()
    payload["completion_logprobs"] = [-0.1]

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "completion_logprobs length must equal completion_ids length" in errors
