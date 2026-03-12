from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import (
    GateResult,
    PromotionDecision,
    PromotionRecord,
    build_codex_dossier_promotion_examples,
    build_codex_dossier_promotion_examples_payload,
    create_promotion_record,
)


def test_promotion_examples_round_trip() -> None:
    payload = build_codex_dossier_promotion_examples_payload()
    promotable_record = PromotionRecord.from_dict(payload["promotable"]["record"])
    promotable_decision = PromotionDecision.from_dict(payload["promotable"]["decision"])
    frontier_record = PromotionRecord.from_dict(payload["frontier_blocked"]["record"])
    support_fail_record = PromotionRecord.from_dict(payload["support_fail"]["record"])

    assert promotable_record.state == "promotable"
    assert promotable_decision.next_state == "promotable"
    assert frontier_record.state == "frontier"
    assert support_fail_record.state == "rejected"


def test_promotion_examples_cover_frontier_rejected_and_promotable() -> None:
    example = build_codex_dossier_promotion_examples()

    promotable = example["promotable"]
    frontier_blocked = example["frontier_blocked"]
    support_fail = example["support_fail"]

    assert promotable["record"].state == "promotable"
    assert frontier_blocked["record"].state == "frontier"
    assert "replay" in frontier_blocked["record"].blocked_by_gate_kinds
    assert "conformance" in frontier_blocked["record"].blocked_by_gate_kinds
    assert support_fail["record"].state == "rejected"
    assert "support_envelope" in support_fail["decision"].blocked_by_gate_kinds


def test_promotion_record_valid_transitions_preserve_lineage() -> None:
    record = create_promotion_record(
        record_id="promotion.test.001",
        target_id="target.codex_dossier.tool_render",
        candidate_id="cand.codex_dossier.001",
        created_at="2026-03-12T14:00:00.000Z",
    )
    evaluated = record.mark_evaluated(
        transitioned_at="2026-03-12T14:00:01.000Z",
        reason="evaluation attached",
        evidence=record.evidence,
    )
    frontier = evaluated.move_to_frontier(
        transitioned_at="2026-03-12T14:00:02.000Z",
        reason="retained on frontier",
    )
    gated = frontier.move_to_gated(
        transitioned_at="2026-03-12T14:00:03.000Z",
        reason="gates executed",
        gate_results=[
            GateResult(
                gate_id="gate.test.001",
                gate_kind="replay",
                status="pass",
                target_id=frontier.target_id,
                candidate_id=frontier.candidate_id,
                reason="ok",
            )
        ],
    )
    promotable = gated.move_to_promotable(
        transitioned_at="2026-03-12T14:00:04.000Z",
        reason="all gates passed",
        gate_results=gated.gate_results,
    )

    assert promotable.target_id == record.target_id
    assert promotable.candidate_id == record.candidate_id
    assert promotable.state_history == ["draft", "evaluated", "frontier", "gated", "promotable"]


def test_promotion_record_rejects_illegal_transition() -> None:
    record = create_promotion_record(
        record_id="promotion.test.002",
        target_id="target.codex_dossier.tool_render",
        candidate_id="cand.codex_dossier.002",
        created_at="2026-03-12T14:10:00.000Z",
    )

    with pytest.raises(ValueError, match="illegal promotion transition"):
        record.promote(
            transitioned_at="2026-03-12T14:10:01.000Z",
            reason="cannot skip straight to promoted",
        )


def test_promotion_terminal_state_behavior_is_explicit() -> None:
    example = build_codex_dossier_promotion_examples()
    rejected = example["support_fail"]["record"]

    archived = rejected.archive(
        transitioned_at="2026-03-12T14:20:00.000Z",
        reason="preserve rejected candidate lineage without leaving it active",
    )

    assert archived.state == "archived"
    assert archived.state_history[-2:] == ["rejected", "archived"]
