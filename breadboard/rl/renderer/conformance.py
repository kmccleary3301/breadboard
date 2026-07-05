from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from breadboard.rl.renderer.records import classify_rendered_turn_trainability, validate_rendered_turn
from breadboard.rl.renderer.schema import RenderedTurnRecord


def build_renderer_conformance_report(records: Sequence[RenderedTurnRecord]) -> dict[str, Any]:
    """Summarize renderer-token record validity without promoting support claims."""

    fidelity_counts = Counter(record.provider_fidelity.fidelity_class for record in records)
    invalid_records: list[dict[str, Any]] = []
    trainability = []
    for record in records:
        errors = validate_rendered_turn(record)
        if errors:
            invalid_records.append({"turn_id": record.turn_id, "errors": errors})
        trainability.append(classify_rendered_turn_trainability(record).to_dict())
    return {
        "record_count": len(records),
        "valid_record_count": len(records) - len(invalid_records),
        "invalid_records": invalid_records,
        "fidelity_counts": dict(fidelity_counts),
        "sft_trainable_count": sum(1 for item in trainability if item["sft_trainable"]),
        "on_policy_trainable_count": sum(1 for item in trainability if item["on_policy_trainable"]),
        "claim_boundary": "renderer_conformance_only_not_verl_support",
    }
