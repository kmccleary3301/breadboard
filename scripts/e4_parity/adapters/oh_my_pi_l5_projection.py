from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from scripts.e4_parity.validators import hash_utils


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hash_utils.sha256_bytes(data)


def _required_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _lane_value(context: Mapping[str, Any], key: str) -> str:
    lane = _required_mapping(context.get("lane"), "lane")
    value = lane.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"lane.{key} must be a non-empty string")
    return value


def _constants(context: Mapping[str, Any]) -> Mapping[str, Any]:
    return _required_mapping(context.get("constants"), "constants")


def _path(constants: Mapping[str, Any], key: str) -> str:
    paths = _required_mapping(constants.get("paths"), "constants.paths")
    value = paths.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"constants.paths.{key} must be a non-empty string")
    return value


def _target_probe(context: Mapping[str, Any]) -> Mapping[str, Any]:
    source = _required_mapping(context.get("source"), "source")
    value = source.get("value")
    return _required_mapping(value, "source.value")


def _target_probe_hash(context: Mapping[str, Any]) -> str:
    source = _required_mapping(context.get("source"), "source")
    digest = source.get("sha256")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("source.sha256 must be a prefixed sha256 digest")
    return digest


def _plan_hash(plan: Mapping[str, Any]) -> str:
    clone = copy.deepcopy(dict(plan))
    hashes = dict(clone.get("hashes", {}))
    hashes["plan_hash"] = "sha256:" + "0" * 64
    clone["hashes"] = hashes
    return _sha256_bytes(_canonical_json(clone))


def _build_patch(context: Mapping[str, Any]) -> dict[str, Any]:
    constants = _constants(context)
    target_probe = _target_probe(context)
    target_probe_path = _path(constants, "target_probe_output")
    transcript_fixture_path = _path(constants, "transcript_fixture")

    lane_id = _lane_value(context, "lane_id")
    transcript_fixture = _required_mapping(target_probe.get("transcript_fixture"), "target_probe.transcript_fixture")
    transcript_hash = _sha256_bytes(_canonical_json(transcript_fixture))
    compaction_runtime = _required_mapping(target_probe.get("compaction_runtime"), "target_probe.compaction_runtime")
    prep = _required_mapping(compaction_runtime.get("preparation"), "compaction_runtime.preparation")
    snap = _required_mapping(compaction_runtime.get("snapcompactResult"), "compaction_runtime.snapcompactResult")
    archive = _required_mapping(snap.get("archive"), "snapcompactResult.archive")
    memory_visibility = _required_mapping(target_probe.get("memory_backend_visibility"), "target_probe.memory_backend_visibility")
    target_probe_ref = f"{target_probe_path}#{_target_probe_hash(context)}"

    return {
        "schema_version": "bb.transcript_continuation_patch.v1",
        "patch_id": f"{lane_id}_snapcompact_patch",
        "pre_state_ref": f"{transcript_fixture_path}#{transcript_hash}",
        "appended_messages": [
            {
                "role": "compactionSummary",
                "summary": snap["summary"],
                "shortSummary": snap.get("shortSummary"),
                "firstKeptEntryId": snap["firstKeptEntryId"],
                "tokensBefore": snap["tokensBefore"],
                "modelVisible": True,
                "providerVisible": True,
            }
        ],
        "appended_tool_events": [],
        "lineage_updates": [
            {"kind": "snapcompact_archive", "source_ref": target_probe_ref, "archive": archive},
            {
                "kind": "memory_backend_visibility",
                "source_ref": target_probe_ref,
                "none_backend_tools_absent": memory_visibility["noneBackendToolsAbsent"],
                "configured_backends_blocked_without_initialized_state": memory_visibility[
                    "configuredBackendsBlockedWithoutInitializedState"
                ],
            },
        ],
        "compaction_markers": [
            {
                "strategy": "snapcompact",
                "firstKeptEntryId": snap["firstKeptEntryId"],
                "messagesToSummarizeCount": prep["messagesToSummarizeCount"],
                "recentMessagesCount": prep["recentMessagesCount"],
                "tokensBefore": snap["tokensBefore"],
                "archiveFrameCount": archive["frameCount"],
                "archiveTextHeadChars": archive["textHeadChars"],
                "memoryBackendVisibility": constants.get(
                    "memory_backend_visibility_label",
                    "none absent; mnemopi/hindsight tools blocked without initialized state",
                ),
            }
        ],
        "post_state_digest": _sha256_bytes(
            _canonical_json({"summary": snap["summary"], "firstKeptEntryId": snap["firstKeptEntryId"]})
        ),
        "lossiness_flags": list(constants.get("lossiness_flags", [])),
    }


def _build_plan(context: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    constants = _constants(context)
    target_probe = _target_probe(context)
    target_probe_path = _path(constants, "target_probe_output")
    transcript_fixture_path = _path(constants, "transcript_fixture")
    transcript_patch_path = _path(constants, "transcript_continuation_patch")

    lane_id = _lane_value(context, "lane_id")
    transcript_fixture = _required_mapping(target_probe.get("transcript_fixture"), "target_probe.transcript_fixture")
    entries = transcript_fixture.get("entries")
    if not isinstance(entries, list):
        raise ValueError("target_probe.transcript_fixture.entries must be a list")
    transcript_hash = _sha256_bytes(_canonical_json(transcript_fixture))
    patch_hash = _sha256_bytes(_canonical_json(patch))
    target_probe_hash = _target_probe_hash(context)
    compaction_runtime = _required_mapping(target_probe.get("compaction_runtime"), "target_probe.compaction_runtime")
    prep = _required_mapping(compaction_runtime.get("preparation"), "compaction_runtime.preparation")
    snap = _required_mapping(compaction_runtime.get("snapcompactResult"), "compaction_runtime.snapcompactResult")
    archive = _required_mapping(snap.get("archive"), "snapcompactResult.archive")
    thresholds = _required_mapping(compaction_runtime.get("thresholds"), "compaction_runtime.thresholds")
    settings = _required_mapping(compaction_runtime.get("settings"), "compaction_runtime.settings")

    visibility_model = dict(constants.get("visibility_model", {"model_visible": True, "provider_visible": True, "host_visible": True}))
    visibility_host = dict(constants.get("visibility_host", {"model_visible": False, "provider_visible": False, "host_visible": True}))

    plan: dict[str, Any] = {
        "schema_version": "bb.memory_compaction_plan.v1",
        "plan_id": f"{lane_id}_snapcompact_plan",
        "transcript_refs": [
            {
                "transcript_id": transcript_fixture["transcript_id"],
                "ref": transcript_fixture_path,
                "start_seq": 0,
                "end_seq": len(entries) - 1,
                "hash": transcript_hash,
                "visibility": visibility_model,
            }
        ],
        "trigger": {
            "kind": "manual",
            "source_ref": f"{target_probe_path}#{target_probe_hash}",
            "reason": constants.get("trigger_reason", "P6.0-L5 deterministic no-provider snapcompact runtime probe"),
            "observed_tokens": int(snap["tokensBefore"]),
            "threshold_tokens": int(thresholds["defaultThreshold"]),
        },
        "token_budget": {
            "model_context_window": int(constants.get("model_context_window", 128000)),
            "max_input_tokens": int(constants.get("max_input_tokens", 128000)),
            "reserved_output_tokens": int(settings["reserveTokens"]),
            "before_tokens": int(snap["tokensBefore"]),
            "target_after_tokens": max(1, int(thresholds["defaultThreshold"])),
        },
        "preserved_refs": [
            {
                "ref_id": "recent_live_suffix",
                "ref_kind": "transcript_segment",
                "ref": f"{transcript_fixture_path}#firstKeptEntryId={snap['firstKeptEntryId']}",
                "hash": transcript_hash,
                "reason": "target prepareCompaction kept the live suffix beginning at firstKeptEntryId",
                "visibility": visibility_model,
            }
        ],
        "elided_refs": [
            {
                "ref_id": "snapcompact_archived_prefix",
                "ref": f"{transcript_fixture_path}#entries=0..{max(0, prep['messagesToSummarizeCount'] - 1)}",
                "reason": "target snapcompact archived older history into a compaction summary/archive and kept the live suffix",
                "token_estimate": int(snap["tokensBefore"]),
                "replacement_ref": f"{transcript_patch_path}#{patch_hash}",
                "visibility": visibility_model,
            }
        ],
        "generated_refs": [
            {
                "ref_id": "snapcompact_summary_patch",
                "ref_kind": "model_visible_insertion",
                "artifact_ref": transcript_patch_path,
                "hash": patch_hash,
                "produced_by": constants.get("summary_producer", "@oh-my-pi/snapcompact.compact + createCompactionSummaryMessage"),
            },
            {
                "ref_id": "memory_backend_visibility_probe",
                "ref_kind": "backend_note",
                "artifact_ref": target_probe_path,
                "hash": target_probe_hash,
                "produced_by": constants.get("memory_visibility_producer", "Oh-My-Pi retain/recall/reflect createIf/execute runtime probe"),
            },
        ],
        "model_visible_insertions": [
            {
                "insertion_id": "compaction_summary_message",
                "position": "between_turns",
                "content_ref": transcript_patch_path,
                "content_hash": patch_hash,
                "token_estimate": max(1, int(archive["textHeadChars"]) // 4),
            }
        ],
        "backend_contributions": [
            {
                "contribution_id": "snapcompact_local_archive",
                "contributor_kind": "compactor",
                "contributor_id": constants.get("compactor_id", "@oh-my-pi/snapcompact.compact"),
                "input_refs": [transcript_fixture_path],
                "output_refs": [transcript_patch_path, target_probe_path],
                "visibility": visibility_model,
                "hash": target_probe_hash,
            },
            {
                "contribution_id": "memory_backend_tool_visibility",
                "contributor_kind": "tool",
                "contributor_id": constants.get("memory_tool_contributor_id", "retain/recall/reflect memory.backend none|mnemopi|hindsight"),
                "input_refs": [target_probe_path],
                "output_refs": [target_probe_path],
                "visibility": visibility_host,
                "hash": target_probe_hash,
            },
        ],
        "hashes": {
            "algorithm": "sha256",
            "source_hash": transcript_hash,
            "plan_hash": "sha256:" + "0" * 64,
            "compacted_hash": patch_hash,
        },
        "status": "applied",
    }
    plan["hashes"]["plan_hash"] = _plan_hash(plan)
    return plan


def project_memory_compaction(context: Mapping[str, Any]) -> dict[str, Any]:
    """Project immutable L5 target-probe data into memory plan and transcript patch records."""
    patch = _build_patch(context)
    plan = _build_plan(context, patch)
    return {
        "records": [
            {"record_key": "memory_compaction_plan", "value": plan},
            {"record_key": "transcript_continuation_patch", "value": patch},
        ],
        "derived_facts": {
            "plan_hash": plan["hashes"]["plan_hash"],
            "patch_hash": _sha256_bytes(_canonical_json(patch)),
            "transcript_hash": plan["hashes"]["source_hash"],
        },
        "derived_values": {
            "plan_hash": plan["hashes"]["plan_hash"],
            "patch_hash": _sha256_bytes(_canonical_json(patch)),
            "transcript_hash": plan["hashes"]["source_hash"],
        },
    }


def project_memory_compaction_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    result = project_memory_compaction(context)
    return {"records": [result["records"][0]], "derived_facts": result["derived_facts"]}


def project_transcript_continuation_patch(context: Mapping[str, Any]) -> dict[str, Any]:
    result = project_memory_compaction(context)
    return {"records": [result["records"][1]], "derived_facts": result["derived_facts"]}


PROJECTIONS = {
    "p6_l5_memory_compaction": project_memory_compaction,
    "p6_l5_memory_compaction_plan": project_memory_compaction_plan,
    "p6_l5_transcript_continuation_patch": project_transcript_continuation_patch,
}
