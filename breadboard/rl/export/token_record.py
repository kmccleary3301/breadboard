from __future__ import annotations

from typing import Any, Mapping

from breadboard.rl.renderer.records import classify_rendered_turn_trainability, validate_rendered_turn
from breadboard.rl.renderer.schema import RenderedTurnRecord


TOKEN_RECORD_EXPORT_SCHEMA = "bb.token_record.v1alpha"


def build_token_record_export_payload(record: RenderedTurnRecord) -> dict[str, Any]:
    decision = classify_rendered_turn_trainability(record)
    return {
        "schema_version": TOKEN_RECORD_EXPORT_SCHEMA,
        "record": record.to_dict(),
        "trainability": decision.to_dict(),
        "projection_boundary": {
            "canonical_truth": "breadboard_graph_replay_runtime",
            "projection_kind": "token_record",
            "trainer_specific": False,
            "verl_support_claim": False,
        },
    }


def validate_token_record_export_payload(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != TOKEN_RECORD_EXPORT_SCHEMA:
        errors.append(f"schema_version must be {TOKEN_RECORD_EXPORT_SCHEMA!r}")
    record_payload = payload.get("record")
    if not isinstance(record_payload, Mapping):
        errors.append("record must be a mapping")
        return errors
    try:
        record = RenderedTurnRecord.from_dict(record_payload)
    except ValueError as exc:
        return [*errors, str(exc)]
    errors.extend(validate_rendered_turn(record))
    boundary = payload.get("projection_boundary")
    if not isinstance(boundary, Mapping):
        errors.append("projection_boundary must be a mapping")
    else:
        if boundary.get("canonical_truth") != "breadboard_graph_replay_runtime":
            errors.append("projection_boundary.canonical_truth must remain breadboard_graph_replay_runtime")
        if boundary.get("trainer_specific") is not False:
            errors.append("projection_boundary.trainer_specific must be false")
        if boundary.get("verl_support_claim") is not False:
            errors.append("projection_boundary.verl_support_claim must be false for M2")
    return errors
