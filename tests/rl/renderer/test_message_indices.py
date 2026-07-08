from __future__ import annotations

from breadboard.rl.renderer.records import validate_rendered_turn
from breadboard.rl.renderer.schema import RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_message_indices_map_every_token() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload())

    assert len(record.message_indices) == len(record.input_ids)
    assert validate_rendered_turn(record) == []


def test_message_indices_length_mismatch_fails() -> None:
    payload = cloned_payload()
    payload["message_indices"] = [0, 0]

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "message_indices length must equal input_ids length" in errors
