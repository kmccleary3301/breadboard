from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "docs" / "contracts" / "darwin" / "schemas"


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def _issues_from_validator(validator: Draft202012Validator, payload: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for error in validator.iter_errors(payload):
        path = ".".join(str(part) for part in error.absolute_path) if error.absolute_path else "$"
        issues.append(ValidationIssue(path=path, message=error.message))
    return issues


@lru_cache(maxsize=None)
def load_schema(schema_name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / schema_name
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator_for(schema_name: str) -> Draft202012Validator:
    schema = load_schema(schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate(schema_name: str, payload: dict[str, Any]) -> list[ValidationIssue]:
    return _issues_from_validator(_validator_for(schema_name), payload)


def validate_campaign_spec(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("campaign_spec_v0.schema.json", payload)


def validate_policy_bundle(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("policy_bundle_v0.schema.json", payload)


def validate_candidate_artifact(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("candidate_artifact_v0.schema.json", payload)


def validate_evaluation_record(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("evaluation_record_v0.schema.json", payload)


def validate_evidence_bundle(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("evidence_bundle_v0.schema.json", payload)


def validate_claim_record(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("claim_record_v0.schema.json", payload)


def validate_lane_registry(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("lane_registry_v0.schema.json", payload)


def validate_policy_registry(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("policy_registry_v0.schema.json", payload)


def validate_weekly_evidence_packet(payload: dict[str, Any]) -> list[ValidationIssue]:
    return _validate("weekly_evidence_packet_v0.schema.json", payload)
