from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from jsonschema import Draft202012Validator


EVOLAKE_CAMPAIGN_API_VERSION = "evolake.campaign.v1"
EVOLAKE_CAMPAIGN_PAYLOAD_SCHEMA_ID = "breadboard.evolake.campaign_payload.v1"
EVOLAKE_CAMPAIGN_RECORD_SCHEMA_ID = "breadboard.evolake.campaign_record.v1"
EVOLAKE_CHECKPOINT_SCHEMA_ID = "breadboard.evolake.campaign_checkpoint.v1"
EVOLAKE_REPLAY_MANIFEST_SCHEMA_ID = "breadboard.evolake.replay_manifest.v1"


EVOLAKE_CAMPAIGN_PAYLOAD_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "EvoLakeCampaignPayload",
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "campaign_id": {"type": "string", "minLength": 1},
        "tenant_id": {"type": "string", "minLength": 1},
        "rounds": {"type": "integer", "minimum": 1},
        "seed": {"type": "integer"},
        "metadata": {"type": "object", "additionalProperties": True},
        "tasks": {"type": "array", "items": {"type": "string"}},
        "resume_from": {"type": "integer", "minimum": 0},
        "checkpoint_id": {"type": "string", "minLength": 1},
        "budget": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "iterations": {"type": "integer", "minimum": 1},
                "wall_time_s": {"type": "number", "exclusiveMinimum": 0},
                "candidate_limit": {"type": "integer", "minimum": 1},
                "max_rounds": {"type": "integer", "minimum": 1},
            },
        },
        "atp": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "rounds": {"type": "integer", "minimum": 1},
                "requests": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/ATPRequestOrPassThrough"},
                },
            },
        },
        "atp_requests": {
            "type": "array",
            "items": {"$ref": "#/$defs/ATPRequestOrPassThrough"},
        },
    },
    "$defs": {
        "ATPRequest": {
            "type": "object",
            "additionalProperties": True,
            "required": ["commands"],
            "properties": {
                "api_version": {"type": "string"},
                "commands": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "state_ref": {"type": ["string", "null"]},
                "tenant_id": {"type": ["string", "null"]},
                "timeout_s": {"type": "number", "exclusiveMinimum": 0},
                "want_state": {"type": "boolean"},
            },
        },
        "ATPRequestOrPassThrough": {
            "oneOf": [
                {"$ref": "#/$defs/ATPRequest"},
                {"not": {"type": "object"}},
            ]
        },
    },
}


EVOLAKE_CAMPAIGN_RECORD_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "EvoLakeCampaignRecord",
    "type": "object",
    "required": ["schema", "api_version", "campaign_id", "submitted_at", "payload", "tenant_id", "rounds"],
    "additionalProperties": True,
    "properties": {
        "schema": {"const": EVOLAKE_CAMPAIGN_RECORD_SCHEMA_ID},
        "api_version": {"const": EVOLAKE_CAMPAIGN_API_VERSION},
        "campaign_id": {"type": "string"},
        "submitted_at": {"type": "number"},
        "payload": {"type": "object", "additionalProperties": True},
        "tenant_id": {"type": "string"},
        "rounds": {"type": "integer", "minimum": 1},
    },
}


EVOLAKE_CHECKPOINT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "EvoLakeCampaignCheckpoint",
    "type": "object",
    "required": [
        "schema",
        "checkpoint_id",
        "campaign_id",
        "tenant_id",
        "round",
        "status",
        "created_at",
        "api_version",
    ],
    "additionalProperties": True,
    "properties": {
        "schema": {"const": EVOLAKE_CHECKPOINT_SCHEMA_ID},
        "checkpoint_id": {"type": "string"},
        "campaign_id": {"type": "string"},
        "tenant_id": {"type": "string"},
        "round": {"type": "integer", "minimum": 0},
        "status": {"enum": ["ok", "error"]},
        "created_at": {"type": "number"},
        "api_version": {"const": EVOLAKE_CAMPAIGN_API_VERSION},
    },
}


EVOLAKE_REPLAY_MANIFEST_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "EvoLakeReplayManifest",
    "type": "object",
    "required": ["schema", "campaign_id", "tenant_id", "row_count", "api_version"],
    "additionalProperties": True,
    "properties": {
        "schema": {"const": EVOLAKE_REPLAY_MANIFEST_SCHEMA_ID},
        "campaign_id": {"type": "string"},
        "tenant_id": {"type": "string"},
        "row_count": {"type": "integer", "minimum": 0},
        "api_version": {"const": EVOLAKE_CAMPAIGN_API_VERSION},
    },
}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def _issues_from_validator(validator: Draft202012Validator, payload: Dict[str, Any]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for err in validator.iter_errors(payload):
        path = ".".join(str(p) for p in err.absolute_path) if err.absolute_path else "$"
        issues.append(ValidationIssue(path=path, message=err.message))
    return issues


def validate_campaign_payload(payload: Dict[str, Any]) -> List[ValidationIssue]:
    validator = Draft202012Validator(EVOLAKE_CAMPAIGN_PAYLOAD_SCHEMA)
    return _issues_from_validator(validator, payload)


def validate_campaign_record(record: Dict[str, Any]) -> List[ValidationIssue]:
    validator = Draft202012Validator(EVOLAKE_CAMPAIGN_RECORD_SCHEMA)
    return _issues_from_validator(validator, record)


def validate_checkpoint(record: Dict[str, Any]) -> List[ValidationIssue]:
    validator = Draft202012Validator(EVOLAKE_CHECKPOINT_SCHEMA)
    return _issues_from_validator(validator, record)


def validate_replay_manifest(payload: Dict[str, Any]) -> List[ValidationIssue]:
    validator = Draft202012Validator(EVOLAKE_REPLAY_MANIFEST_SCHEMA)
    return _issues_from_validator(validator, payload)
