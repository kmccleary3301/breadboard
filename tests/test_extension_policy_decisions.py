from __future__ import annotations

from agentic_coder_prototype.extensions.policy import (
    PolicyDecision,
    PolicyDecisionAction,
    merge_policy_decisions,
)


def test_policy_decision_merge_denies_win() -> None:
    decisions = [
        PolicyDecision(action="warn", reason_code="warn_a", plugin_id="b", priority=50),
        PolicyDecision(action="deny", reason_code="deny_a", plugin_id="a", priority=10),
        PolicyDecision(action="transform", reason_code="xform_a", plugin_id="c", priority=5),
    ]
    merged = merge_policy_decisions(decisions)
    assert merged.action == PolicyDecisionAction.DENY
    assert merged.deny is not None
    assert merged.deny.reason_code == "deny_a"
    assert [d.reason_code for d in merged.transforms] == ["xform_a"]


def test_policy_decision_merge_is_deterministic() -> None:
    decisions = [
        PolicyDecision(action="transform", reason_code="b", plugin_id="plugin_b", priority=10),
        PolicyDecision(action="transform", reason_code="a", plugin_id="plugin_a", priority=10),
        PolicyDecision(action="warn", reason_code="c", plugin_id="plugin_c", priority=5),
    ]
    merged = merge_policy_decisions(decisions)
    assert [d.reason_code for d in merged.ordered] == ["c", "a", "b"]
