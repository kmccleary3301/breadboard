from __future__ import annotations

from breadboard.rl.renderer.records import validate_rendered_turn
from breadboard.rl.renderer.schema import RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_tool_call_requires_tool_action_mask() -> None:
    payload = cloned_payload()
    payload["tool_parse_status"] = "ok"
    payload["tool_calls"] = [{"name": "python", "arguments": {"code": "1+1"}}]

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "tool_calls require at least one true tool_action_mask entry" in errors


def test_tool_call_with_action_mask_passes() -> None:
    payload = cloned_payload()
    payload["tool_parse_status"] = "ok"
    payload["tool_calls"] = [{"name": "python", "arguments": {"code": "1+1"}}]
    payload["tool_action_mask"] = [False, False, False, True, True]

    assert validate_rendered_turn(RenderedTurnRecord.from_dict(payload)) == []


def test_tool_calls_require_parse_status_ok() -> None:
    payload = cloned_payload()
    payload["tool_parse_status"] = "failed"
    payload["tool_calls"] = [{"name": "python", "arguments": {}}]
    payload["tool_action_mask"] = [False, False, False, True, True]

    errors = validate_rendered_turn(RenderedTurnRecord.from_dict(payload))
    assert "tool_calls require tool_parse_status=ok" in errors
