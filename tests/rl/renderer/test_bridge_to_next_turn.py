from __future__ import annotations

import pytest

from breadboard.rl.renderer.records import classify_rendered_turn_trainability
from breadboard.rl.renderer.schema import BridgeToNextTurn, RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_bridge_success_is_recorded() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload())

    assert record.bridge_to_next_turn.attempted is True
    assert record.bridge_to_next_turn.success is True


def test_bridge_failure_requires_reason() -> None:
    with pytest.raises(ValueError, match="failure requires failure_reason"):
        BridgeToNextTurn(attempted=True, success=False)


def test_bridge_failure_blocks_trainability() -> None:
    payload = cloned_payload()
    payload["bridge_to_next_turn"] = {
        "attempted": True,
        "success": False,
        "failure_reason": "parser_state_lost",
    }

    decision = classify_rendered_turn_trainability(RenderedTurnRecord.from_dict(payload))
    assert decision.sft_trainable is False
    assert decision.on_policy_trainable is False
    assert "bridge_to_next_turn_failed" in decision.blocked_reasons
