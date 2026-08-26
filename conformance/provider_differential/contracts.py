"""Contracts and check-only validators for provider differential evidence.

This module has no filesystem side effects. Public validators return the
unchanged mapping after validation so callers can safely chain them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

try:
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover
    jsonschema = None

ORACLE_REPOSITORY = "can1357/oh-my-pi"
ORACLE_TAG = "v18.0.1"
ORACLE_COMMIT = "6c1209842323bb4713f127ac303c97fd043d585c"
ORACLE_TREE = "67a19f0d45a71af8c3d9ae83562ffb1fcb579d61"
ORACLE_IDENTITY: dict[str, str] = {
    "repository": ORACLE_REPOSITORY,
    "tag": ORACLE_TAG,
    "commit": ORACLE_COMMIT,
    "tree": ORACLE_TREE,
}

PROVIDERS = ("codex", "openai", "anthropic", "openrouter")
PROVIDER_FAMILIES = (
    "catalog_model_route",
    "request_ir",
    "text_stream",
    "tool_stream",
    "usage_finish",
    "error_terminal",
    "cancel_terminal",
)
PROVIDER_ROW_IDS = tuple(
    f"{provider}.{family}" for provider in PROVIDERS for family in PROVIDER_FAMILIES
)
AUTH_ROW_IDS = (
    "auth.api_key_precedence",
    "auth.codex_oauth_precedence",
    "auth.explicit_account_binding",
    "auth.automatic_affinity_restart_rotation",
    "auth.classified_429_rotation",
    "auth.refresh_single_flight",
    "auth.refresh_transient_deferral",
    "auth.refresh_definitive_tombstone",
    "auth.revoke_during_refresh",
)
ROLE_ROW_IDS = (
    "role.public_alias_selection",
    "role.unknown_unavailable",
    "role.lock_secret_rotation_restart",
    "role.auth_policy_no_fallback",
    "role.cross_provider_default_forbidden",
)
ARTIFACT_ROW_IDS = (
    "artifact.wheel_provider_catalog_auth_role",
    "artifact.sdk_local_responses",
    "artifact.installed_end_to_end_trace",
)
ALL_ROW_IDS = PROVIDER_ROW_IDS + AUTH_ROW_IDS + ROLE_ROW_IDS + ARTIFACT_ROW_IDS
AUTH_ROLE_ROW_IDS = AUTH_ROW_IDS + ROLE_ROW_IDS
CLASSIFICATIONS = ("match", "intentional_divergence", "BreadBoard_defect", "unverified")
CLAIMABLE_CLASSIFICATIONS = frozenset({"match", "intentional_divergence"})
SEMANTIC_EVENT_KINDS = (
    "response_start",
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_end",
)
TERMINAL_STATES = ("done", "error", "cancelled")
_SCHEMA_DIR = Path(__file__).with_name("schemas")
_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


class ContractError(ValueError):
    """Raised when a contract or evidence gate is violated."""


class SchemaValidationError(ContractError):
    """Fallback schema-validation error when jsonschema is unavailable."""


def _json_compatible(
    value: Any, *, path: str = "$", _seen: set[int] | None = None
) -> Any:
    seen = _seen if _seen is not None else set()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{path}: non-finite number is not canonical JSON")
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise ContractError(f"{path}: cyclic mapping")
        seen.add(marker)
        try:
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ContractError(f"{path}: object keys must be strings")
                result[key] = _json_compatible(item, path=f"{path}.{key}", _seen=seen)
            return result
        finally:
            seen.remove(marker)
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in seen:
            raise ContractError(f"{path}: cyclic array")
        seen.add(marker)
        try:
            return [
                _json_compatible(item, path=f"{path}[{idx}]", _seen=seen)
                for idx, item in enumerate(value)
            ]
        finally:
            seen.remove(marker)
    raise ContractError(f"{path}: {type(value).__name__} is not JSON-compatible")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON with sorted keys and no insignificant whitespace."""
    return json.dumps(
        _json_compatible(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def verify_sha256(
    value: bytes | str | Path, expected: str, *, canonical: bool = False
) -> bool:
    if not isinstance(expected, str) or _HASH_RE.fullmatch(expected) is None:
        return False
    try:
        if isinstance(value, Path):
            actual = sha256_file(value)
        elif isinstance(value, bytes):
            actual = sha256_bytes(value)
        elif isinstance(value, str):
            raw = (
                canonical_json_bytes(json.loads(value))
                if canonical
                else value.encode("utf-8")
            )
            actual = sha256_bytes(raw)
        else:
            return False
    except (OSError, TypeError, ValueError, ContractError):
        return False
    return (
        actual.removeprefix("sha256:").lower()
        == expected.removeprefix("sha256:").lower()
    )


def _schema_path(schema: str | Path) -> Path:
    path = Path(schema)
    if not path.is_absolute() and len(path.parts) == 1:
        path = _SCHEMA_DIR / path
    if path.suffix != ".json":
        path = path.with_suffix(".schema.json")
    return path


def load_schema(schema: str | Path) -> dict[str, Any]:
    path = _schema_path(schema)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot load schema {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ContractError(f"schema {path} must be a JSON object")
    if jsonschema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(parsed)
        except Exception as exc:
            raise ContractError(f"invalid schema {path}: {exc}") from exc
    return parsed


def _resolve_ref(ref: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaValidationError(f"unsupported schema reference {ref!r}")
    value: Any = root
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"schema reference {ref!r} is not an object")
    return value


def _fallback_validate(
    value: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str = "$"
) -> None:
    if "$ref" in schema:
        _fallback_validate(value, _resolve_ref(str(schema["$ref"]), root), root, path)
        return
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")
    if "type" in schema:
        expected = (
            schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        )
        checks = {
            "null": value is None,
            "boolean": isinstance(value, bool),
            "object": isinstance(value, Mapping),
            "array": isinstance(value, (list, tuple)),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        }
        if not any(checks.get(kind, False) for kind in expected):
            raise SchemaValidationError(f"{path}: expected {expected}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{path}: string is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaValidationError(f"{path}: string does not match pattern")
    if (
        isinstance(value, (int, float))
        and "minimum" in schema
        and value < schema["minimum"]
    ):
        raise SchemaValidationError(f"{path}: number below minimum")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{path}: too few items")
        if schema.get("uniqueItems") and len(
            {canonical_json(item) for item in value}
        ) != len(value):
            raise SchemaValidationError(f"{path}: duplicate array items")
    if isinstance(value, Mapping):
        props = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                raise SchemaValidationError(
                    f"{path}: missing required property {key!r}"
                )
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(props)
            if unknown:
                raise SchemaValidationError(
                    f"{path}: unknown properties {sorted(unknown)!r}"
                )
        for key, item in value.items():
            if key in props:
                _fallback_validate(item, props[key], root, f"{path}.{key}")
            elif isinstance(schema.get("additionalProperties"), Mapping):
                _fallback_validate(
                    item, schema["additionalProperties"], root, f"{path}.{key}"
                )
    if isinstance(value, (list, tuple)) and isinstance(schema.get("items"), Mapping):
        for idx, item in enumerate(value):
            _fallback_validate(item, schema["items"], root, f"{path}[{idx}]")
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            successes = 0
            for alternative in schema[keyword]:
                try:
                    _fallback_validate(value, alternative, root, path)
                except Exception:
                    continue
                successes += 1
            if (keyword == "anyOf" and successes == 0) or (
                keyword == "oneOf" and successes != 1
            ):
                raise SchemaValidationError(
                    f"{path}: {keyword} alternatives did not match"
                )


def validate_json(value: Any, schema: Mapping[str, Any] | str | Path) -> Any:
    parsed = load_schema(schema) if isinstance(schema, (str, Path)) else dict(schema)
    _json_compatible(value)
    if jsonschema is not None:
        validator = jsonschema.Draft202012Validator(parsed)
        errors = sorted(
            validator.iter_errors(value), key=lambda error: list(error.absolute_path)
        )
        if errors:
            raise ContractError(errors[0].message)
    else:
        _fallback_validate(value, parsed, parsed)
    return value


def _require_row_id(row_id: Any) -> str:
    if not isinstance(row_id, str) or row_id not in ALL_ROW_IDS:
        raise ContractError(f"unknown provider differential row id: {row_id!r}")
    return row_id


def validate_row_set(
    row_ids: Sequence[Any], *, expected: Sequence[str] = ALL_ROW_IDS
) -> tuple[str, ...]:
    """Require an exact, ordered-or-not set of known unique row IDs."""
    if not isinstance(row_ids, Sequence) or isinstance(row_ids, (str, bytes)):
        raise ContractError("row IDs must be an array")
    normalized = tuple(_require_row_id(row_id) for row_id in row_ids)
    if len(normalized) != len(expected):
        raise ContractError(f"expected {len(expected)} rows, got {len(normalized)}")
    if len(set(normalized)) != len(normalized):
        raise ContractError("duplicate row ID")
    if set(normalized) != set(expected):
        missing = sorted(set(expected) - set(normalized))
        unknown = sorted(set(normalized) - set(expected))
        raise ContractError(
            f"row set mismatch (missing={missing!r}, unexpected={unknown!r})"
        )
    return normalized


def validate_row_ids(row_ids: Sequence[Any]) -> tuple[str, ...]:
    """Compatibility spelling for exact 45-row validation."""
    return validate_row_set(row_ids)


def validate_observation(
    value: Any, *, expected_row_id: str | None = None
) -> Mapping[str, Any]:
    """Validate exactly row_id, subject, claim, observed, evidence."""
    if not isinstance(value, Mapping) or set(value) != {
        "row_id",
        "subject",
        "claim",
        "observed",
        "evidence",
    }:
        raise ContractError(
            "observer result must have exactly row_id, subject, claim, observed, evidence"
        )
    row_id = _require_row_id(value["row_id"])
    if expected_row_id is not None and row_id != expected_row_id:
        raise ContractError(
            f"observer row id {row_id!r} does not match expected {expected_row_id!r}"
        )
    for field in ("subject", "claim"):
        if not isinstance(value[field], str) or not value[field]:
            raise ContractError(f"observer {field} must be a non-empty string")
    _json_compatible(value["observed"])
    # Observer evidence is intentionally semantic and provider-neutral.  The
    # manifest ledger, not an observer, owns path/hash records.
    _json_compatible(value["evidence"])
    return value


def _validate_hash(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ContractError(f"{label} must be a SHA-256 digest")


def _validate_evidence_record(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise ContractError("evidence records must have exactly path and sha256")
    if (
        not isinstance(value["path"], str)
        or not value["path"]
        or Path(value["path"]).is_absolute()
    ):
        raise ContractError("evidence path must be a non-empty relative path")
    _validate_hash(value["sha256"], label="evidence sha256")


def _validate_blob_records(value: Any, *, label: str) -> None:
    if not isinstance(value, (list, tuple)) or not value:
        raise ContractError(f"{label} must be a non-empty array")
    for blob in value:
        _validate_evidence_record(blob)


def _validate_manifest_file_hashes(
    manifest: Mapping[str, Any], root: Path | None
) -> None:
    if root is None:
        return
    root = Path(root).resolve()
    records: list[Mapping[str, Any]] = []
    for row in manifest["rows"]:
        records.extend(row["oracle_source_blobs"])
        records.extend(row["evidence"])
    for record in records:
        path = (root / record["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ContractError(
                f"evidence path escapes validation root: {record['path']!r}"
            ) from exc
        if not path.is_file():
            raise ContractError(f"evidence path does not exist: {record['path']!r}")
        actual = sha256_file(path).removeprefix("sha256:").lower()
        expected = record["sha256"].removeprefix("sha256:").lower()
        if actual != expected:
            raise ContractError(f"sha256 mismatch for {record['path']!r}")


def _validate_manifest_bindings(manifest: Mapping[str, Any]) -> None:
    if dict(manifest.get("oracle_identity", {})) != ORACLE_IDENTITY:
        raise ContractError("manifest oracle identity does not match F1")
    for field, label in (("breadboard_commit", "commit"), ("breadboard_tree", "tree")):
        value = manifest.get(field)
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None
        ):
            raise ContractError(f"manifest has invalid BreadBoard {label}")
    if manifest.get("row_count") != len(ALL_ROW_IDS):
        raise ContractError(f"manifest row_count must be {len(ALL_ROW_IDS)}")
    rows = manifest.get("rows")
    if not isinstance(rows, (list, tuple)) or len(rows) != len(ALL_ROW_IDS):
        raise ContractError(f"manifest must contain exactly {len(ALL_ROW_IDS)} rows")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContractError(f"manifest row {index} must be an object")
        row_id = _require_row_id(row.get("row_id"))
        if row_id in seen:
            raise ContractError(f"duplicate manifest row {row_id!r}")
        seen.add(row_id)
        for field in (
            "claim",
            "provider",
            "seam",
            "comparator",
            "oracle_runner",
            "breadboard_commit",
            "breadboard_tree",
            "verification_toolchain",
            "verified_at",
        ):
            if not isinstance(row.get(field), str) or not row[field]:
                raise ContractError(f"manifest row {row_id!r}: {field} is required")
        if (
            not row_id.startswith(("auth.", "role.", "artifact."))
            and row["provider"] not in PROVIDERS
        ):
            raise ContractError(f"manifest row {row_id!r}: invalid provider")
        for field, label in (
            ("breadboard_commit", "commit"),
            ("breadboard_tree", "tree"),
        ):
            if re.fullmatch(r"[0-9a-fA-F]{40}", row[field]) is None:
                raise ContractError(
                    f"manifest row {row_id!r}: invalid BreadBoard {label}"
                )
            if row[field] != manifest[field]:
                raise ContractError(
                    f"manifest row {row_id!r}: BreadBoard {label} differs from manifest"
                )
        identity = row.get("oracle_identity")
        if not isinstance(identity, Mapping) or dict(identity) != ORACLE_IDENTITY:
            raise ContractError(
                f"manifest row {row_id!r}: oracle identity does not match F1"
            )
        _validate_blob_records(
            row.get("oracle_source_blobs"), label="oracle_source_blobs"
        )
        for field in ("oracle_input_sha256", "oracle_output_sha256"):
            _validate_hash(row.get(field), label=field)
        evidence = row.get("evidence")
        if not isinstance(evidence, (list, tuple)) or not evidence:
            raise ContractError(f"manifest row {row_id!r}: evidence is required")
        for record in evidence:
            _validate_evidence_record(record)
        provenance = row.get("artifact_provenance")
        if row_id in ARTIFACT_ROW_IDS:
            if not isinstance(provenance, Mapping) or set(provenance) != {
                "artifact_id",
                "source",
                "sha256",
            }:
                raise ContractError(
                    f"manifest row {row_id!r}: invalid artifact provenance"
                )
            if provenance["artifact_id"] != row_id:
                raise ContractError(
                    f"manifest row {row_id!r}: artifact provenance id must match row"
                )
            if not isinstance(provenance["source"], str) or not provenance["source"]:
                raise ContractError(
                    f"manifest row {row_id!r}: artifact provenance source is required"
                )
            _validate_hash(provenance["sha256"], label="artifact provenance sha256")
        elif provenance is not None:
            raise ContractError(
                f"manifest row {row_id!r}: artifact provenance is forbidden"
            )
        classification = row.get("classification")
        if classification not in CLASSIFICATIONS:
            raise ContractError(f"manifest row {row_id!r}: unknown classification")
        if classification not in CLAIMABLE_CLASSIFICATIONS:
            raise ContractError(
                f"manifest row {row_id!r}: classification {classification!r} is not claimable"
            )
        divergence = row.get("divergence_ref")
        if classification == "intentional_divergence" and (
            not isinstance(divergence, str) or not divergence
        ):
            raise ContractError(
                f"manifest row {row_id!r}: intentional divergence needs divergence_ref"
            )
        if classification != "intentional_divergence" and divergence is not None:
            raise ContractError(
                f"manifest row {row_id!r}: divergence_ref is unreferenced"
            )
    missing = set(ALL_ROW_IDS) - seen
    if missing:
        raise ContractError(f"manifest is missing rows: {sorted(missing)!r}")


def validate_semantic_trace(value: Any) -> Mapping[str, Any]:
    """Validate ordered events, projections, lifecycles, and terminal state."""
    validate_json(value, "bb.provider_semantic_trace.v1.schema.json")
    if not isinstance(value, Mapping):
        raise ContractError("semantic trace must be an object")
    events = value["events"]
    sequences = [event["sequence"] for event in events]
    if sequences != list(range(len(sequences))):
        raise ContractError(
            "semantic trace events must have contiguous ordered sequence values"
        )
    kinds = [event["kind"] for event in events]
    if kinds[0] != "response_start" or kinds.count("response_start") != 1:
        raise ContractError("semantic trace must begin with exactly one response_start")
    text = thinking = False
    text_chunks: list[str] = []
    thinking_chunks: list[str] = []
    active_tools: set[str] = set()
    completed_tools: list[dict[str, Any]] = []
    for event in events:
        kind = event["kind"]
        if kind == "text_start":
            if text:
                raise ContractError("text_start without text_end")
            text = True
        elif kind == "text_delta":
            if not text or not event["delta"]:
                raise ContractError("text_delta lifecycle violation")
            text_chunks.append(event["delta"])
        elif kind == "text_end":
            if not text:
                raise ContractError("text_end without text_start")
            text = False
        elif kind == "thinking_start":
            if thinking:
                raise ContractError("thinking_start without thinking_end")
            thinking = True
        elif kind == "thinking_delta":
            if not thinking or not event["delta"]:
                raise ContractError("thinking_delta lifecycle violation")
            thinking_chunks.append(event["delta"])
        elif kind == "thinking_end":
            if not thinking:
                raise ContractError("thinking_end without thinking_start")
            thinking = False
        elif kind == "tool_call_start":
            call_id = event["call_id"]
            if call_id in active_tools:
                raise ContractError(f"duplicate tool call {call_id!r}")
            active_tools.add(call_id)
        elif kind == "tool_call_delta":
            if event["call_id"] not in active_tools:
                raise ContractError("tool_call_delta lifecycle violation")
        elif kind == "tool_call_end":
            call_id = event["call_id"]
            if call_id not in active_tools:
                raise ContractError("tool_call_end lifecycle violation")
            active_tools.remove(call_id)
            try:
                parsed_arguments = json.loads(event["arguments_json"])
            except json.JSONDecodeError as exc:
                raise ContractError("tool_call_end arguments_json is invalid") from exc
            if canonical_json(parsed_arguments) != canonical_json(event["arguments"]):
                raise ContractError("tool_call_end arguments projections disagree")
            completed_tools.append(
                {
                    "call_id": call_id,
                    "name": event["name"],
                    "arguments_json": event["arguments_json"],
                    "arguments": event["arguments"],
                }
            )
    terminal = value["terminal"]
    terminal_state = terminal["state"]
    if text or thinking or active_tools:
        if terminal_state != "cancelled":
            raise ContractError("semantic stream lifecycle is not balanced")
    if terminal_state not in TERMINAL_STATES:
        raise ContractError("semantic trace terminal state is invalid")
    result = value["result"]
    if result["assembled_text"] != "".join(text_chunks):
        raise ContractError("semantic text events and result disagree")
    if result["assembled_reasoning"] != "".join(thinking_chunks):
        raise ContractError("semantic reasoning events and result disagree")
    if canonical_json(result["tool_calls"]) != canonical_json(completed_tools):
        raise ContractError("semantic tool events and result disagree")
    output_emitted = any(
        kind
        in {
            "text_delta",
            "thinking_delta",
            "tool_call_start",
            "tool_call_delta",
            "tool_call_end",
        }
        for kind in kinds
    )
    if terminal["output_emitted"] is not output_emitted:
        raise ContractError("semantic events and terminal output_emitted disagree")
    return value


def validate_manifest(value: Any, *, root: Path | None = None) -> Mapping[str, Any]:
    """Validate an evidence ledger and return it unchanged; never writes.

    ``root`` optionally enables path confinement and SHA-256 verification for
    source/evidence blobs.
    """
    validate_json(value, "bb.provider_differential_manifest.v1.schema.json")
    if not isinstance(value, Mapping):
        raise ContractError("manifest must be an object")
    _validate_manifest_bindings(value)
    _validate_manifest_file_hashes(value, root)
    return value


__all__ = [
    "ALL_ROW_IDS",
    "ARTIFACT_ROW_IDS",
    "AUTH_ROLE_ROW_IDS",
    "AUTH_ROW_IDS",
    "CLASSIFICATIONS",
    "CLAIMABLE_CLASSIFICATIONS",
    "ContractError",
    "ORACLE_COMMIT",
    "ORACLE_IDENTITY",
    "ORACLE_REPOSITORY",
    "ORACLE_TAG",
    "ORACLE_TREE",
    "PROVIDERS",
    "PROVIDER_FAMILIES",
    "PROVIDER_ROW_IDS",
    "ROLE_ROW_IDS",
    "SEMANTIC_EVENT_KINDS",
    "TERMINAL_STATES",
    "canonical_json",
    "canonical_json_bytes",
    "load_schema",
    "sha256_bytes",
    "sha256_file",
    "sha256_json",
    "validate_json",
    "validate_manifest",
    "validate_observation",
    "validate_semantic_trace",
    "verify_sha256",
]
