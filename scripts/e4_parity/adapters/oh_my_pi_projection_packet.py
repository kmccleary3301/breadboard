from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.e4_parity.validators import hash_utils


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TypeError(f"{name} must be a list of non-empty strings")
    return list(value)


def _packet_constants(lane_def: Mapping[str, Any]) -> Mapping[str, Any]:
    normalize = _mapping(lane_def.get("normalize"), "lane_def.normalize")
    config = _mapping(normalize.get("config"), "lane_def.normalize.config")
    return _mapping(config.get("packet_constants"), "lane_def.normalize.config.packet_constants")


def _role_paths(lane_def: Mapping[str, Any]) -> dict[str, str]:
    normalize = _mapping(lane_def.get("normalize"), "lane_def.normalize")
    config = _mapping(normalize.get("config"), "lane_def.normalize.config")
    roles = _mapping(config.get("roles"), "lane_def.normalize.config.roles")
    return {str(key): str(value) for key, value in roles.items()}


def _record_map(finalized_records: Mapping[str, Any] | list[Mapping[str, Any]]) -> dict[str, Any]:
    if isinstance(finalized_records, Mapping):
        return dict(finalized_records)
    records: dict[str, Any] = {}
    for row in finalized_records:
        record_key = row.get("record_key") if isinstance(row, Mapping) else None
        if not isinstance(record_key, str) or not record_key:
            raise ValueError("finalized record rows must contain record_key")
        records[record_key] = row.get("value")
    return records


def _payload_from_template(template: Any) -> Any:
    # Templates are lane-data constants. Copy through JSON to detach callers and to keep
    # all packet assembly code independent of lane ids, profile modules, and accepted paths.
    return json.loads(json.dumps(template, ensure_ascii=False))


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    data = json.dumps({"row_id": row_id, "row": row}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hash_utils.sha256_bytes(data)

def _lookup(scope: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = scope
    for part in dotted_path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
            continue
        if isinstance(value, list) and part.isdigit():
            value = value[int(part)]
            continue
        raise KeyError(dotted_path)
    return value


def _set_path(payload: Any, path: list[Any], value: Any) -> None:
    if not path:
        raise ValueError("substitution path must not be empty")
    cursor = payload
    for part in path[:-1]:
        if isinstance(cursor, list) and isinstance(part, int):
            cursor = cursor[part]
        elif isinstance(cursor, dict) and isinstance(part, str):
            cursor = cursor[part]
        else:
            raise ValueError(f"invalid substitution path segment {part!r}")
    final = path[-1]
    if isinstance(cursor, list) and isinstance(final, int):
        cursor[final] = value
    elif isinstance(cursor, dict) and isinstance(final, str):
        cursor[final] = value
    else:
        raise ValueError(f"invalid substitution final segment {final!r}")


def _role_from_ref(value: str, path_to_role: Mapping[str, str]) -> str | None:
    path, separator, digest = value.partition("#")
    if separator and digest.startswith("sha256:"):
        return path_to_role.get(path)
    return None


def _referenced_roles(value: Any, path_to_role: Mapping[str, str]) -> set[str]:
    roles: set[str] = set()
    if isinstance(value, str):
        role = _role_from_ref(value, path_to_role)
        if role is not None:
            roles.add(role)
        return roles
    if isinstance(value, list):
        for item in value:
            roles.update(_referenced_roles(item, path_to_role))
        return roles
    if isinstance(value, Mapping):
        artifact_path = value.get("path")
        if isinstance(artifact_path, str) and artifact_path in path_to_role and any(
            key in value for key in ("bytes", "exists", "sha256")
        ):
            roles.add(path_to_role[artifact_path])
        for key, item in value.items():
            if isinstance(key, str) and key in path_to_role and isinstance(item, str) and item.startswith("sha256:"):
                roles.add(path_to_role[key])
            roles.update(_referenced_roles(item, path_to_role))
    return roles


def _bind_role_refs(
    value: Any,
    *,
    path_to_role: Mapping[str, str],
    role_hashes: Mapping[str, str],
    role_bytes: Mapping[str, int],
) -> Any:
    if isinstance(value, str):
        role = _role_from_ref(value, path_to_role)
        if role is None or role not in role_hashes:
            return value
        path = value.split("#", 1)[0]
        return f"{path}#{role_hashes[role]}"
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _bind_role_refs(
                item,
                path_to_role=path_to_role,
                role_hashes=role_hashes,
                role_bytes=role_bytes,
            )
        return value
    if isinstance(value, dict):
        artifact_path = value.get("path")
        if isinstance(artifact_path, str):
            role = path_to_role.get(artifact_path)
            if role is not None and role in role_hashes:
                if "sha256" in value:
                    value["sha256"] = role_hashes[role]
                if "bytes" in value and role in role_bytes:
                    value["bytes"] = role_bytes[role]
                if "exists" in value:
                    value["exists"] = True
        for key, item in list(value.items()):
            role = path_to_role.get(key) if isinstance(key, str) else None
            if role is not None and role in role_hashes and isinstance(item, str) and item.startswith("sha256:"):
                value[key] = role_hashes[role]
                continue
            value[key] = _bind_role_refs(
                item,
                path_to_role=path_to_role,
                role_hashes=role_hashes,
                role_bytes=role_bytes,
            )
    return value


def _apply_substitutions(
    payloads: dict[str, Any],
    substitutions: Mapping[str, Any],
    *,
    role_hashes: Mapping[str, Any],
    input_refs: Mapping[str, Any],
    derived_values: Mapping[str, Any],
    role_paths: Mapping[str, str],
    source_bytes: Mapping[str, Any],
    lane_values: Mapping[str, Any],
    source_values: Mapping[str, Any],
) -> None:
    scopes: dict[str, Mapping[str, Any]] = {
        "role_hashes": role_hashes,
        "input_refs": input_refs,
        "derived_values": derived_values,
        "role_paths": role_paths,
        "source_bytes": source_bytes,
        "lane_values": lane_values,
        "source_values": source_values,
    }
    for role, rows in substitutions.items():
        if role not in payloads:
            raise ValueError(f"substitution role {role!r} has no payload")
        if not isinstance(rows, list):
            raise TypeError(f"packet_constants.substitutions.{role} must be a list")
        for row in rows:
            mapping = _mapping(row, f"packet_constants.substitutions.{role}[]")
            path = mapping.get("path")
            source = mapping.get("source")
            key = mapping.get("key")
            if not isinstance(path, list) or not all(isinstance(part, (str, int)) for part in path):
                raise TypeError("substitution path must be a list of string/int segments")
            if not isinstance(source, str) or source not in scopes:
                raise ValueError(f"unknown substitution source {source!r}")
            if not isinstance(key, str) or not key:
                raise ValueError("substitution key must be a non-empty string")
            _set_path(payloads[role], path, _lookup(scopes[source], key))


def _render_role_bytes(
    payloads: dict[str, Any],
    substitutions: Mapping[str, Any],
    required_roles: list[str],
    *,
    role_hashes: Mapping[str, Any],
    input_refs: Mapping[str, Any],
    derived_values: Mapping[str, Any],
    role_paths: Mapping[str, str],
    source_bytes: Mapping[str, Any],
    lane_values: Mapping[str, Any],
    source_values: Mapping[str, Any],
    auto_bind_role_refs: bool,
    verbatim_roles: set[str] | None = None,
) -> dict[str, bytes]:
    resolved_hashes = dict(role_hashes)
    resolved_refs = dict(input_refs)
    resolved_bytes = {str(role): int(size) for role, size in source_bytes.items()}
    path_to_role: dict[str, str] = {}
    for role in [*required_roles, *role_paths]:
        path = role_paths.get(role)
        if path is not None:
            path_to_role.setdefault(path, role)
    for role, digest in resolved_hashes.items():
        path = role_paths.get(role)
        if path is not None:
            resolved_refs.setdefault(role, f"{path}#{digest}")
    pending = set(required_roles)
    rendered: dict[str, bytes] = {}
    while pending:
        progressed = False
        for role in required_roles:
            if role not in pending:
                continue
            verbatim = role in (verbatim_roles or set())
            rows = [] if verbatim else substitutions.get(role, [])
            if not isinstance(rows, list):
                raise TypeError(f"packet_constants.substitutions.{role} must be a list")
            dependencies = {
                (str(row.get("source")), str(row.get("key")))
                for row in rows
                if isinstance(row, Mapping) and row.get("source") in {"role_hashes", "input_refs"}
            }
            auto_dependencies = _referenced_roles(payloads[role], path_to_role) if auto_bind_role_refs and not verbatim else set()
            if any(dependency not in resolved_hashes for dependency in auto_dependencies):
                continue
            if any(
                key not in (resolved_hashes if source == "role_hashes" else resolved_refs)
                for source, key in dependencies
            ):
                continue
            if auto_bind_role_refs and not verbatim:
                _bind_role_refs(
                    payloads[role],
                    path_to_role=path_to_role,
                    role_hashes=resolved_hashes,
                    role_bytes=resolved_bytes,
                )
            _apply_substitutions(
                payloads,
                {role: rows},
                role_hashes=resolved_hashes,
                input_refs=resolved_refs,
                derived_values=derived_values,
                role_paths=role_paths,
                source_bytes=source_bytes,
                lane_values=lane_values,
                source_values=source_values,
            )
            data = canonical_json_bytes(payloads[role])
            digest = hash_utils.sha256_bytes(data)
            rendered[role] = data
            resolved_hashes[role] = digest
            resolved_refs[role] = f"{role_paths[role]}#{digest}"
            resolved_bytes[role] = len(data)
            pending.remove(role)
            progressed = True
        if not progressed:
            unresolved = {
                role: substitutions.get(role, [])
                for role in required_roles
                if role in pending
            }
            raise ValueError(f"unresolved packet role dependencies: {unresolved}")
    return rendered


def build_projection_packet(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any] | None,
    finalized_records: Mapping[str, Any] | list[Mapping[str, Any]],
    derived_facts: Mapping[str, Any] | None,
    logical_roles: Mapping[str, str] | None = None,
    physical_roles: Mapping[str, Path] | None = None,
    role_hashes: Mapping[str, Any] | None = None,
    input_refs: Mapping[str, Any] | None = None,
    source_payloads: Mapping[str, Any] | None = None,
    source_bytes: Mapping[str, Any] | None = None,
    source_values: Mapping[str, Any] | None = None,
) -> dict[str, bytes]:
    """Assemble Oh-My-Pi projection packet role bytes from lane data and records.

    The assembler intentionally dispatches only on declared role names, record keys,
    and packet constants from the lane definition. It imports no lane/profile modules,
    reads no accepted artifacts, and has no lane-id/family conditionals.
    """

    constants = _packet_constants(lane_def)
    role_paths = dict(logical_roles or _role_paths(lane_def))
    records = _record_map(finalized_records)
    derived = dict(derived_facts or {})
    expected_records = _strings(constants.get("required_records"), "packet_constants.required_records")
    missing_records = [record_key for record_key in expected_records if record_key not in records]
    if missing_records:
        raise ValueError("missing finalized records: " + ", ".join(missing_records))

    payloads: dict[str, Any] = {}
    for role, record_key in _mapping(constants.get("record_roles"), "packet_constants.record_roles").items():
        if role not in role_paths:
            raise ValueError(f"record role {role!r} is not declared in normalize.config.roles")
        if not isinstance(record_key, str) or record_key not in records:
            raise ValueError(f"record role {role!r} references unknown record {record_key!r}")
        payloads[role] = records[record_key]

    for role, envelope in _mapping(constants.get("record_envelopes", {}), "packet_constants.record_envelopes").items():
        if role not in role_paths:
            raise ValueError(f"record envelope role {role!r} is not declared in normalize.config.roles")
        record_keys = _strings(envelope.get("records"), f"packet_constants.record_envelopes.{role}.records")
        payload = {key: _payload_from_template(value) for key, value in _mapping(envelope.get("fields"), f"packet_constants.record_envelopes.{role}.fields").items()}
        payload[str(envelope.get("records_field", "records"))] = [records[record_key] for record_key in record_keys]
        payloads[role] = payload

    templates = _mapping(constants.get("payload_templates"), "packet_constants.payload_templates")
    for role, template in templates.items():
        if role not in role_paths:
            raise ValueError(f"template role {role!r} is not declared in normalize.config.roles")
        payloads[str(role)] = _payload_from_template(template)
    for role, payload in (source_payloads or {}).items():
        if role in payloads:
            payloads[role] = _payload_from_template(payload)
    for role, source_role in _mapping(constants.get("role_aliases", {}), "packet_constants.role_aliases").items():
        if role not in role_paths:
            raise ValueError(f"alias role {role!r} is not declared in normalize.config.roles")
        if not isinstance(source_role, str) or source_role not in payloads:
            raise ValueError(f"alias role {role!r} references unknown source role {source_role!r}")
        payloads[str(role)] = payloads[source_role]
    substitutions = _mapping(constants.get("substitutions", {}), "packet_constants.substitutions")
    required_roles = _strings(constants.get("required_roles"), "packet_constants.required_roles")
    missing_roles = [role for role in required_roles if role not in payloads]
    if missing_roles:
        raise ValueError("missing packet roles: " + ", ".join(missing_roles))
    run = _mapping(lane_def.get("run", {}), "lane_def.run")
    lane_values = {
        "config_id": lane_def.get("config_id"),
        "lane_id": lane_def.get("lane_id"),
        "provider_model": run.get("provider_model"),
        "run_id": run.get("run_id"),
        "sandbox_mode": run.get("sandbox_mode"),
        "target_family": lane_def.get("target_family"),
        "target_version": lane_def.get("target_version"),
    }
    observation_labels = _strings(constants.get("scope_observation_labels", []), "packet_constants.scope_observation_labels")
    lane_values["observed_provider_models"] = {
        label: lane_values["provider_model"] for label in observation_labels
    }
    lane_values["observed_run_ids"] = {
        label: lane_values["run_id"] for label in observation_labels
    }
    lane_values["true"] = True
    values = dict(source_values or {})
    catalog = values.get("artifact_catalog")
    if isinstance(catalog, Mapping):
        integrity = catalog.get("integrity")
        stable_hash = integrity.get("stable_entries_hash") if isinstance(integrity, Mapping) else None
        revision = catalog.get("revision")
        if isinstance(stable_hash, str):
            lane_values["catalog_hash"] = stable_hash
        if isinstance(revision, int):
            lane_values["catalog_revision"] = revision
    config_id = lane_values.get("config_id")
    freeze_manifest = values.get("freeze_manifest")
    if isinstance(config_id, str) and isinstance(freeze_manifest, Mapping):
        rows = freeze_manifest.get("e4_configs")
        row = rows.get(config_id) if isinstance(rows, Mapping) else None
        if isinstance(row, Mapping):
            digest = _row_hash(config_id, row)
            lane_values["freeze_row_hash"] = digest
            lane_values["freeze_ref"] = f"{role_paths['freeze_manifest']}#{config_id}#{digest}"
    inventory = dict(inventory_lane or {})
    feature_ids = inventory.get("ledger_feature_ids")
    ledger = values.get("atomic_feature_ledger")
    if isinstance(feature_ids, list) and len(feature_ids) == 1 and isinstance(feature_ids[0], str) and isinstance(ledger, Mapping):
        ledger_rows = ledger.get("rows")
        matches = [row for row in ledger_rows or [] if isinstance(row, Mapping) and row.get("feature_id") == feature_ids[0]]
        if len(matches) == 1:
            digest = _row_hash(feature_ids[0], matches[0])
            lane_values["ledger_row_ref"] = f"{role_paths['atomic_feature_ledger']}#{feature_ids[0]}#{digest}"
    support_claim = payloads.get("support_claim_ref")
    if isinstance(support_claim, Mapping) and isinstance(support_claim.get("schema_version"), str):
        lane_values["support_claim_schema_version"] = support_claim["schema_version"]
    byte_payloads = _render_role_bytes(
        payloads,
        substitutions,
        required_roles,
        role_hashes=dict(role_hashes or {}),
        input_refs=dict(input_refs or {}),
        derived_values=derived,
        role_paths=role_paths,
        source_bytes=dict(source_bytes or {}),
        lane_values=lane_values,
        source_values=values,
        auto_bind_role_refs=bool(constants.get("auto_bind_role_refs", False)),
        verbatim_roles={role for role in (source_payloads or {}) if role in payloads},
    )
    if physical_roles is not None:
        for role, data in byte_payloads.items():
            path = physical_roles.get(role)
            if path is None:
                raise ValueError(f"physical role path missing for {role!r}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    # Expose derived facts in the returned packet metadata without serializing them as roles.
    byte_payloads["__packet_metadata__"] = canonical_json_bytes(
        {
            "lane_id": lane_def.get("lane_id"),
            "config_id": lane_def.get("config_id"),
            "claim_id": (inventory_lane or {}).get("claim_id"),
            "record_count": len(records),
            "derived_fact_keys": sorted(derived),
            "role_count": len(required_roles),
        }
    )
    return byte_payloads


__all__ = ["build_projection_packet", "canonical_json_bytes"]
