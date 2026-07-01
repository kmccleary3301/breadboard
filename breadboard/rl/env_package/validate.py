from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from breadboard.rl.env_package.hash import canonical_env_package_hash
from breadboard.rl.env_package.schema import EnvPackage, SCHEMA_VERSION


PROTECTED_CONTAMINATION_SCOPES = {
    "dev_hidden",
    "promotion_hidden",
    "final_holdout",
    "external_board",
}
UNKNOWN_CONTAMINATION_SCOPES = {"unknown", ""}
SUPPORTED_EXPORT_LEVELS = {"experimental", "probe_backed", "supported"}
TRAINABILITY_STATUSES = {"not_trainable", "sft_candidate", "rl_candidate", "debug_only"}
SWE_SOURCE_KINDS = {"swe_gym", "swe_rebench_v2", "seta", "benchflow_swe"}
UNTRUSTED_ISOLATION_LEVELS = {
    "single_tenant_untrusted",
    "multi_tenant_untrusted",
    "verifier_high_integrity",
}


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def load_env_package(path: str | Path) -> EnvPackage:
    return EnvPackage.from_dict(load_yaml_mapping(path))


def _require_mapping(payload: Mapping[str, Any], field_name: str, errors: list[str]) -> dict[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be a mapping")
        return {}
    return dict(value)


def _require_list(payload: Mapping[str, Any], field_name: str, errors: list[str]) -> list[Any]:
    value = payload.get(field_name)
    if not isinstance(value, list) or not value:
        errors.append(f"{field_name} must be a non-empty list")
        return []
    return list(value)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _task_source_kinds(tasksets: list[Any]) -> set[str]:
    kinds: set[str] = set()
    for item in tasksets:
        if isinstance(item, Mapping):
            kinds.add(_text(item.get("source_kind")).lower())
    return kinds


def _requires_swe_hardening(payload: Mapping[str, Any], tasksets: list[Any]) -> bool:
    package_id = _text(payload.get("package_id")).lower()
    harness = payload.get("harness", {})
    interaction_mode = _text(harness.get("interaction_mode") if isinstance(harness, Mapping) else "").lower()
    source_kinds = _task_source_kinds(tasksets)
    return (
        "swe" in package_id
        or bool(source_kinds & SWE_SOURCE_KINDS)
        or interaction_mode in {"patch_submit", "swe_patch", "terminal_swe"}
    )


def validate_env_package_mapping(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    for field in [
        "package_id",
        "version",
        "package_hash",
        "provenance",
        "tasksets",
        "splits",
        "harness",
        "runtime",
        "verifier",
        "reward",
        "renderer",
        "replay",
        "exports",
    ]:
        if field not in payload:
            errors.append(f"missing required field: {field}")

    provenance = _require_mapping(payload, "provenance", errors)
    tasksets = _require_list(payload, "tasksets", errors)
    splits = _require_mapping(payload, "splits", errors)
    harness = _require_mapping(payload, "harness", errors)
    runtime = _require_mapping(payload, "runtime", errors)
    verifier = _require_mapping(payload, "verifier", errors)
    renderer = _require_mapping(payload, "renderer", errors)
    replay = _require_mapping(payload, "replay", errors)
    exports = _require_mapping(payload, "exports", errors)

    for field in ["created_at", "license", "source_usage_policy", "contamination_scope"]:
        if not _text(provenance.get(field)):
            errors.append(f"provenance.{field} must be non-empty")

    contamination_scope = _text(provenance.get("contamination_scope")).lower()
    if exports.get("trainable") is True:
        if contamination_scope in UNKNOWN_CONTAMINATION_SCOPES:
            errors.append("trainable packages must not use unknown contamination_scope")
        if not _text(provenance.get("license")):
            errors.append("trainable packages require provenance.license")
        if not _text(provenance.get("source_usage_policy")):
            errors.append("trainable packages require provenance.source_usage_policy")
        if replay.get("replay_required") is False:
            errors.append("trainable packages require replay.replay_required")
        if not _text(renderer.get("tokenizer_hash")):
            errors.append("trainable packages require renderer.tokenizer_hash")

    if contamination_scope in PROTECTED_CONTAMINATION_SCOPES and exports.get("trainable") is True:
        errors.append("protected contamination scopes cannot be marked trainable")

    for index, taskset in enumerate(tasksets):
        if not isinstance(taskset, Mapping):
            errors.append(f"tasksets[{index}] must be a mapping")
            continue
        for field in ["taskset_id", "source_kind", "source_hash", "task_id_field"]:
            if not _text(taskset.get(field)):
                errors.append(f"tasksets[{index}].{field} must be non-empty")
        if not isinstance(taskset.get("prompt_fields"), list) or not taskset.get("prompt_fields"):
            errors.append(f"tasksets[{index}].prompt_fields must be a non-empty list")
        if not isinstance(taskset.get("allowed_splits"), list) or not taskset.get("allowed_splits"):
            errors.append(f"tasksets[{index}].allowed_splits must be a non-empty list")

    allowed_splits_by_taskset: dict[str, set[str]] = {}
    for item in tasksets:
        if isinstance(item, Mapping) and _text(item.get("taskset_id")):
            allowed_splits_by_taskset[_text(item.get("taskset_id"))] = {
                _text(split_id) for split_id in item.get("allowed_splits", []) if _text(split_id)
            }
    for split_id, split in splits.items():
        if not isinstance(split, Mapping):
            errors.append(f"splits.{split_id} must be a mapping")
            continue
        split_id_text = _text(split.get("split_id") or split_id)
        taskset_id = _text(split.get("taskset_id"))
        if split.get("protected") is True:
            if split.get("trainer_visible") is True:
                errors.append(f"splits.{split_id} protected split cannot be trainer_visible")
            if split.get("optimizer_visible") is True:
                errors.append(f"splits.{split_id} protected split cannot be optimizer_visible")
        if taskset_id not in allowed_splits_by_taskset:
            errors.append(f"splits.{split_id} references unknown taskset_id")
        elif split_id_text not in allowed_splits_by_taskset[taskset_id]:
            errors.append(f"splits.{split_id} is not listed in taskset allowed_splits")
        if not _text(split.get("split_hash")):
            errors.append(f"splits.{split_id}.split_hash must be non-empty")

    for field in ["harness_id", "interaction_mode"]:
        if not _text(harness.get(field)):
            errors.append(f"harness.{field} must be non-empty")
    for field in ["backend", "isolation_level"]:
        if not _text(runtime.get(field)):
            errors.append(f"runtime.{field} must be non-empty")
    if runtime.get("network") == "full" and not _text(runtime.get("network_allowlist_reason")):
        errors.append("runtime.network=full requires network_allowlist_reason")

    hardening = payload.get("hardening")
    needs_hardening = _requires_swe_hardening(payload, tasksets)
    if needs_hardening and not isinstance(hardening, Mapping):
        errors.append("SWE packages require hardening policy")
    if isinstance(hardening, Mapping):
        if hardening.get("agent_non_root_required") is True:
            if _text(runtime.get("agent_user")).lower() == "root":
                errors.append("hardening.agent_non_root_required forbids runtime.agent_user=root")
        if hardening.get("verifier_isolated_required") is True and verifier.get("isolated_from_agent") is not True:
            errors.append("hardening.verifier_isolated_required requires verifier.isolated_from_agent=true")
    elif _text(runtime.get("isolation_level")) in UNTRUSTED_ISOLATION_LEVELS:
        errors.append("untrusted runtime packages require hardening policy")

    for field in ["verifier_id", "kind", "code_hash"]:
        if not _text(verifier.get(field)):
            errors.append(f"verifier.{field} must be non-empty")
    for field in ["renderer_id", "tokenizer_hash", "chat_template_hash"]:
        if not _text(renderer.get(field)):
            errors.append(f"renderer.{field} must be non-empty")

    support_level = _text(exports.get("support_level") or "experimental")
    if support_level not in SUPPORTED_EXPORT_LEVELS:
        errors.append(f"exports.support_level must be one of {sorted(SUPPORTED_EXPORT_LEVELS)}")
    if support_level == "supported" and not exports.get("support_evidence_refs"):
        errors.append("exports.support_level=supported requires support_evidence_refs")
    trainability_status = _text(exports.get("trainability_status") or "not_trainable")
    if trainability_status not in TRAINABILITY_STATUSES:
        errors.append(f"exports.trainability_status must be one of {sorted(TRAINABILITY_STATUSES)}")
    if exports.get("trainable") is False and trainability_status in {"sft_candidate", "rl_candidate"}:
        errors.append("non-trainable exports cannot use trainable trainability_status")
    if not isinstance(exports.get("allowed_formats"), list) or not exports.get("allowed_formats"):
        errors.append("exports.allowed_formats must be a non-empty list")

    declared_hash = _text(payload.get("package_hash"))
    if not declared_hash:
        errors.append("package_hash must be non-empty")
    else:
        actual_hash = canonical_env_package_hash(payload)
        if declared_hash != actual_hash:
            errors.append("package_hash does not match canonical EnvPackage hash")

    return errors
