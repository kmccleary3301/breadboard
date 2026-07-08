from __future__ import annotations

from breadboard.rl.export.token_record import (
    TOKEN_RECORD_EXPORT_SCHEMA,
    build_token_record_export_payload,
    validate_token_record_export_payload,
)
from breadboard.rl.renderer.schema import RenderedTurnRecord
from tests.rl.renderer.helpers import cloned_payload


def test_token_record_export_payload_validates() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload())
    payload = build_token_record_export_payload(record)

    assert payload["schema_version"] == TOKEN_RECORD_EXPORT_SCHEMA
    assert payload["projection_boundary"]["canonical_truth"] == "breadboard_graph_replay_runtime"
    assert payload["projection_boundary"]["trainer_specific"] is False
    assert payload["projection_boundary"]["verl_support_claim"] is False
    assert validate_token_record_export_payload(payload) == []


def test_token_record_export_rejects_trainer_specific_boundary() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload())
    payload = build_token_record_export_payload(record)
    payload["projection_boundary"]["trainer_specific"] = True

    errors = validate_token_record_export_payload(payload)
    assert "projection_boundary.trainer_specific must be false" in errors


def test_token_record_export_rejects_m2_verl_support_claim() -> None:
    record = RenderedTurnRecord.from_dict(cloned_payload())
    payload = build_token_record_export_payload(record)
    payload["projection_boundary"]["verl_support_claim"] = True

    errors = validate_token_record_export_payload(payload)
    assert "projection_boundary.verl_support_claim must be false for M2" in errors
