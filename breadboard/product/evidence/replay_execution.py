from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .replay_plan import ReplayPlan, canonical_json

EXECUTION_SCHEMA_VERSION = "bb.replay_execution.v1"
MANIFEST_SCHEMA_VERSION = "bb.replay_artifact_manifest.v1"
_TERMINAL_STATUSES = frozenset(("completed", "provider_failed", "tool_failed", "policy_denied", "budget_exhausted", "timed_out", "cancelled", "host_failed", "invalidated"))
_EXECUTION_FIELDS = {
    "schema_version", "execution_id", "fresh_nonce", "mode", "plan_sha256", "lane_lock_sha256", "harness_lock_sha256",
    "started_at_utc", "completed_at_utc", "duration_ms", "terminal_status", "completion_reason", "kernel_event_stream",
    "provider_exchanges", "tool_outcomes", "workspace_before", "workspace_after", "workspace_diff", "artifact_manifest_id",
    "policy_decisions", "cleanup_ledger", "nondeterminism_disclosures", "redaction_report", "schema_validation_passed",
    "integrity_verified", "claimable", "reuse_attestation_id", "normalization_evidence_ids", "comparison_report_id", "problem",
}
_MANIFEST_FIELDS = {"schema_version", "manifest_id", "source_record_id", "publish_status", "created_at_utc", "entries", "integrity_verified"}
_ARTIFACT_ENTRY_FIELDS = {"artifact_id", "role", "location_kind", "location", "media_type", "schema_id", "size_bytes", "sha256", "producer", "sensitivity", "created_at_utc"}
_PROBLEM_FIELDS = {"schema_version", "error_code", "message", "record_refs", "failed_stage", "hint", "next_actions"}
_PROVIDER_EXCHANGE_FIELDS = {
    "schema_version", "exchange_id", "execution_id", "attempt_id", "request_id", "route_lock_sha256",
    "provider_family", "runtime_id", "runtime_version", "endpoint_class", "model_id", "model_revision",
    "started_at_utc", "completed_at_utc", "duration_ms", "status", "request_payload_sha256",
    "response_payload_sha256", "finish_reason", "usage", "evidence_refs", "fallback_used", "problem",
}
_REMOTE_OBJECT_REF = re.compile(r"(?![Ff][Ii][Ll][Ee]://)[A-Za-z][A-Za-z0-9+.-]*://[^/\s\\]+/[^\s\\]+")


class ReplayExecutionError(ValueError):
    pass


def _portable_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or not value[0].isalnum() or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in value):
        raise ReplayExecutionError(f"{name} must be a portable identifier")
    return value


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:") or any(char not in "0123456789abcdef" for char in value[7:]):
        raise ReplayExecutionError(f"{name} must be an exact lowercase sha256 hash")
    return value


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise ReplayExecutionError(f"{name} must be an RFC 3339 date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayExecutionError(f"{name} must be an RFC 3339 date-time") from exc
    if parsed.tzinfo is None:
        raise ReplayExecutionError(f"{name} must include an offset")
    return value


def _ref(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"ref", "sha256"} or not isinstance(value.get("ref"), str) or not value["ref"]:
        raise ReplayExecutionError(f"{name} must be an artifact reference")
    return {"ref": value["ref"], "sha256": _sha256(value.get("sha256"), f"{name}.sha256")}


def _problem(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PROBLEM_FIELDS or value.get("schema_version") != "bb.problem.v1":
        raise ReplayExecutionError("problem must match bb.problem.v1")
    if not isinstance(value.get("error_code"), str) or not value["error_code"] or not isinstance(value.get("message"), str) or not value["message"]:
        raise ReplayExecutionError("problem identity and message must be populated")
    if any(not isinstance(value.get(name), list) or any(not isinstance(item, str) or not item for item in value[name]) for name in ("record_refs", "next_actions")):
        raise ReplayExecutionError("problem record_refs and next_actions must be string arrays")
    if any(value.get(name) is not None and (not isinstance(value[name], str) or not value[name]) for name in ("failed_stage", "hint")):
        raise ReplayExecutionError("problem optional fields must be null or populated strings")
    return dict(value)


def _unique_strings(value: Any, name: str, *, populated: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value) or len(value) != len(set(value)) or populated and not value:
        raise ReplayExecutionError(f"{name} must be a unique string array")
    return list(value)


def _validate_usage(value: Any) -> dict[str, Any]:
    names = {"input_tokens", "output_tokens", "total_tokens", "cost_amount", "cost_currency"}
    if not isinstance(value, Mapping) or set(value) != names:
        raise ReplayExecutionError("normalized usage fields are invalid")
    if any(type(value[name]) is not int or value[name] < 0 for name in ("input_tokens", "output_tokens", "total_tokens")) or isinstance(value["cost_amount"], bool) or not isinstance(value["cost_amount"], (int, float)) or value["cost_amount"] < 0:
        raise ReplayExecutionError("normalized usage values must be finite and nonnegative")
    try:
        finite_cost = math.isfinite(float(value["cost_amount"]))
    except OverflowError as exc:
        raise ReplayExecutionError("normalized usage cost must be representable as a finite number") from exc
    if not finite_cost:
        raise ReplayExecutionError("normalized usage values must be finite and nonnegative")
    if value["total_tokens"] < value["input_tokens"] + value["output_tokens"]:
        raise ReplayExecutionError("total_tokens cannot understate component token counts")
    if not isinstance(value["cost_currency"], str) or len(value["cost_currency"]) != 3 or any(character < "A" or character > "Z" for character in value["cost_currency"]):
        raise ReplayExecutionError("cost_currency must be an uppercase ISO-like code")
    return dict(value)

def validate_provider_exchange(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayExecutionError("provider exchange must be an object")
    try:
        record = json.loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ReplayExecutionError(str(exc)) from exc
    if set(record) != _PROVIDER_EXCHANGE_FIELDS or record.get("schema_version") != "bb.provider_exchange.v2":
        raise ReplayExecutionError("record fields do not match bb.provider_exchange.v2")
    for name in ("exchange_id", "execution_id", "attempt_id", "request_id"):
        _portable_id(record.get(name), name)
    for name in ("route_lock_sha256", "request_payload_sha256"):
        _sha256(record.get(name), name)
    for name in ("provider_family", "runtime_id", "runtime_version", "endpoint_class", "model_id"):
        if not isinstance(record.get(name), str) or not record[name]:
            raise ReplayExecutionError(f"{name} must be populated")
    if record.get("model_revision") is not None and (not isinstance(record["model_revision"], str) or not record["model_revision"]):
        raise ReplayExecutionError("model_revision must be null or populated")
    _timestamp(record.get("started_at_utc"), "started_at_utc"); _timestamp(record.get("completed_at_utc"), "completed_at_utc")
    if isinstance(record.get("duration_ms"), bool) or not isinstance(record.get("duration_ms"), (int, float)) or not math.isfinite(record["duration_ms"]) or record["duration_ms"] < 0:
        raise ReplayExecutionError("duration_ms must be finite and nonnegative")
    status = record.get("status")
    if status not in ("completed", "provider_error", "invalid_response", "timed_out", "cancelled"):
        raise ReplayExecutionError("provider exchange status is invalid")
    usage = _validate_usage(record.get("usage"))
    evidence_refs = _unique_strings(record.get("evidence_refs"), "evidence_refs", populated=True)
    if record.get("fallback_used") is not False:
        raise ReplayExecutionError("fallback_used must be false")
    if status == "completed":
        _sha256(record.get("response_payload_sha256"), "response_payload_sha256")
        if not isinstance(record.get("finish_reason"), str) or not record["finish_reason"] or record.get("problem") is not None:
            raise ReplayExecutionError("completed provider exchange terminal fields are invalid")
    else:
        if record.get("response_payload_sha256") is not None or record.get("finish_reason") is not None:
            raise ReplayExecutionError("failed provider exchange cannot expose completed response fields")
        _problem(record.get("problem"))
    record["usage"] = usage
    record["evidence_refs"] = evidence_refs
    return record


def _validate_execution(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayExecutionError("replay execution must be an object")
    try:
        record = json.loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ReplayExecutionError(str(exc)) from exc
    if set(record) != _EXECUTION_FIELDS or record.get("schema_version") != EXECUTION_SCHEMA_VERSION:
        raise ReplayExecutionError("record fields do not match bb.replay_execution.v1")
    _portable_id(record.get("execution_id"), "execution_id")
    if record.get("mode") not in ("execute", "reuse"):
        raise ReplayExecutionError("mode must be execute or reuse")
    for name in ("plan_sha256", "lane_lock_sha256", "harness_lock_sha256"):
        _sha256(record.get(name), name)
    _timestamp(record.get("started_at_utc"), "started_at_utc"); _timestamp(record.get("completed_at_utc"), "completed_at_utc")
    if type(record.get("duration_ms")) is not int or record["duration_ms"] < 0 or record.get("terminal_status") not in _TERMINAL_STATUSES or not isinstance(record.get("completion_reason"), str) or not record["completion_reason"]:
        raise ReplayExecutionError("execution terminal fields are invalid")
    for name in ("kernel_event_stream", "workspace_before", "workspace_after", "workspace_diff", "redaction_report"):
        _ref(record.get(name), name)
    if not isinstance(record.get("provider_exchanges"), list):
        raise ReplayExecutionError("provider_exchanges must be an array")
    for index, row in enumerate(record["provider_exchanges"]):
        names = {"ref", "sha256", "request_id", "attempt_id", "normalized_usage", "status"}
        if not isinstance(row, Mapping) or set(row) != names or row.get("status") not in ("completed", "provider_error", "invalid_response", "timed_out", "cancelled"):
            raise ReplayExecutionError(f"provider_exchanges[{index}] is invalid")
        _ref({"ref": row.get("ref"), "sha256": row.get("sha256")}, f"provider_exchanges[{index}]")
        _portable_id(row.get("request_id"), "request_id"); _portable_id(row.get("attempt_id"), "attempt_id"); _validate_usage(row.get("normalized_usage"))
    if not isinstance(record.get("tool_outcomes"), list):
        raise ReplayExecutionError("tool_outcomes must be an array")
    for index, row in enumerate(record["tool_outcomes"]):
        _ref(row, f"tool_outcomes[{index}]")
    _portable_id(record.get("artifact_manifest_id"), "artifact_manifest_id")
    decisions = record.get("policy_decisions")
    if not isinstance(decisions, list):
        raise ReplayExecutionError("policy_decisions must be an array")
    seen_kinds: set[str] = set()
    allowed_by_kind = {"policy": {"allowed", "denied"}, "capability": {"allowed", "denied"}, "approval": {"approved", "rejected", "not_required"}}
    for index, row in enumerate(decisions):
        names = {"kind", "decision", "ref", "sha256"}
        if not isinstance(row, Mapping) or set(row) != names or row.get("kind") not in allowed_by_kind or row.get("decision") not in allowed_by_kind[row["kind"]]:
            raise ReplayExecutionError(f"policy_decisions[{index}] is invalid")
        _ref({"ref": row.get("ref"), "sha256": row.get("sha256")}, f"policy_decisions[{index}]")
        if row["kind"] in seen_kinds:
            raise ReplayExecutionError("policy_decisions must contain each decision kind exactly once")
        seen_kinds.add(row["kind"])
    if seen_kinds != set(allowed_by_kind):
        raise ReplayExecutionError("policy_decisions must contain each decision kind exactly once")
    _unique_strings(record.get("cleanup_ledger"), "cleanup_ledger"); _unique_strings(record.get("nondeterminism_disclosures"), "nondeterminism_disclosures")
    _unique_strings(record.get("normalization_evidence_ids"), "normalization_evidence_ids")
    for name in ("fresh_nonce", "reuse_attestation_id", "comparison_report_id"):
        if record.get(name) is not None and (not isinstance(record[name], str) or not record[name]):
            raise ReplayExecutionError(f"{name} must be null or a populated string")
    if any(type(record.get(name)) is not bool for name in ("schema_validation_passed", "integrity_verified", "claimable")):
        raise ReplayExecutionError("execution verification flags must be booleans")
    status, mode = record["terminal_status"], record["mode"]
    if mode == "execute" and (not isinstance(record.get("fresh_nonce"), str) or not record["fresh_nonce"] or record.get("reuse_attestation_id") is not None):
        raise ReplayExecutionError("execute records require a fresh_nonce and prohibit reuse_attestation_id")
    if mode == "reuse" and (record.get("fresh_nonce") is not None or not isinstance(record.get("reuse_attestation_id"), str) or not record["reuse_attestation_id"] or record["claimable"] or record.get("comparison_report_id") is not None or record["normalization_evidence_ids"]):
        raise ReplayExecutionError("reuse records are non-claimable and require an attestation")
    if status == "completed":
        if record.get("problem") is not None:
            raise ReplayExecutionError("completed execution must not contain a problem")
        if mode == "execute" and (not record["provider_exchanges"] or not any(row["status"] == "completed" for row in record["provider_exchanges"])):
            raise ReplayExecutionError("completed execute records require a completed provider exchange")
    else:
        _problem(record.get("problem"))
        if record["claimable"] or record.get("comparison_report_id") is not None or record["normalization_evidence_ids"]:
            raise ReplayExecutionError("non-completed execution cannot be compared or claimed")
    if record["claimable"] or record["normalization_evidence_ids"] or record.get("comparison_report_id") is not None:
        raise ReplayExecutionError("candidate replay executions cannot assert comparison evidence or claimability")
    return record


def _validate_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplayExecutionError("replay artifact manifest must be an object")
    record = json.loads(canonical_json(value))
    if set(record) != _MANIFEST_FIELDS or record.get("schema_version") != MANIFEST_SCHEMA_VERSION or record.get("publish_status") not in ("complete", "quarantined") or type(record.get("integrity_verified")) is not bool:
        raise ReplayExecutionError("record fields do not match bb.replay_artifact_manifest.v1")
    _portable_id(record.get("manifest_id"), "manifest_id"); _portable_id(record.get("source_record_id"), "source_record_id"); _timestamp(record.get("created_at_utc"), "created_at_utc")
    if record["publish_status"] == "complete" and not record["integrity_verified"] or record["publish_status"] == "quarantined" and record["integrity_verified"]:
        raise ReplayExecutionError("manifest publish status conflicts with integrity_verified")
    if not isinstance(record.get("entries"), list) or not record["entries"]:
        raise ReplayExecutionError("artifact manifest entries must be populated")
    ids: set[str] = set()
    for index, row in enumerate(record["entries"]):
        if not isinstance(row, Mapping) or set(row) != _ARTIFACT_ENTRY_FIELDS:
            raise ReplayExecutionError(f"entries[{index}] fields are invalid")
        artifact_id = _portable_id(row.get("artifact_id"), "artifact_id")
        if artifact_id in ids:
            raise ReplayExecutionError("artifact manifest contains duplicate artifact_id")
        ids.add(artifact_id); _sha256(row.get("sha256"), "sha256"); _timestamp(row.get("created_at_utc"), "created_at_utc")
        if row.get("location_kind") not in ("workspace_relative_path", "object_ref") or not isinstance(row.get("location"), str) or not row["location"] or not isinstance(row.get("media_type"), str) or not row["media_type"] or row.get("sensitivity") not in ("public", "internal", "secret_redacted") or type(row.get("size_bytes")) is not int or row["size_bytes"] < 0:
            raise ReplayExecutionError(f"entries[{index}] metadata is invalid")
        if not isinstance(row.get("producer"), str) or not row["producer"] or not isinstance(row.get("role"), str) or not row["role"] or row.get("schema_id") is not None and (not isinstance(row["schema_id"], str) or not row["schema_id"]):
            raise ReplayExecutionError(f"entries[{index}] ownership is invalid")
        if row["location_kind"] == "object_ref":
            location = row["location"]
            if location != row["sha256"] and _REMOTE_OBJECT_REF.fullmatch(location) is None:
                raise ReplayExecutionError("object_ref locations must be a content digest or remote object URI")
        if row["location_kind"] == "workspace_relative_path":
            location = row["location"]
            portable = PurePosixPath(location)
            if location == "." or portable.is_absolute() or portable.as_posix() != location or "\\" in location or (len(location) >= 2 and location[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" and location[1] == ":") or any(part in ("", ".", "..") for part in portable.parts):
                raise ReplayExecutionError("workspace_relative_path locations must be canonical and stay within the workspace")
    unsigned = dict(record)
    actual_manifest_id = unsigned.pop("manifest_id")
    expected_manifest_id = "replay_manifest:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()
    if actual_manifest_id != expected_manifest_id:
        raise ReplayExecutionError("manifest_id does not match the canonical manifest content")
    return record


@dataclass(frozen=True, slots=True)
class ReplayExecution:
    _canonical: bytes

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayExecution":
        return cls(canonical_json(_validate_execution(value)))

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical)

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical).hexdigest()

    @property
    def terminal_status(self) -> str:
        return self.as_dict()["terminal_status"]

    @property
    def claimable(self) -> bool:
        return self.as_dict()["claimable"]

    def verify_plan(self, plan: ReplayPlan) -> None:
        record, plan_record = self.as_dict(), plan.as_dict()
        if record["plan_sha256"] != plan.sha256:
            raise ReplayExecutionError("execution was produced from a different replay plan")
        if record["mode"] != plan_record["mode"]:
            raise ReplayExecutionError("execution mode does not match its replay plan")
        if record["lane_lock_sha256"] != plan_record["lane_lock_sha256"] or record["harness_lock_sha256"] != plan_record["harness_lock_sha256"]:
            raise ReplayExecutionError("execution lock hashes do not match its replay plan")

    def require_comparable(self) -> None:
        record = self.as_dict()
        if record["mode"] != "execute" or record["terminal_status"] != "completed" or not record["schema_validation_passed"] or not record["integrity_verified"]:
            raise ReplayExecutionError("only completed, schema-valid, integrity-verified execute-mode replays may be compared")


@dataclass(frozen=True, slots=True)
class ReplayArtifactManifest:
    _canonical: bytes

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayArtifactManifest":
        return cls(canonical_json(_validate_manifest(value)))

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical)

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self._canonical).hexdigest()


def problem(error_code: str, message: str, *, failed_stage: str = "replay", record_refs: Sequence[str] = ()) -> dict[str, Any]:
    value = {
        "schema_version": "bb.problem.v1",
        "error_code": error_code,
        "message": message,
        "record_refs": list(record_refs),
        "failed_stage": failed_stage,
        "hint": None,
        "next_actions": [],
    }
    return _problem(value)
