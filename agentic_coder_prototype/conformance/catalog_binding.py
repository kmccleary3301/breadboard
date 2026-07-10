from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentic_coder_prototype.compilation.primitive_records import canonical_record_bytes, sha256_ref

CATALOG_PATH = "docs/conformance/e4_artifact_catalog.json"

CLAIM_LAYER_ROLE_SUFFIXES = frozenset(
    {
        "node_gate",
        "support_claim",
        "support_claim_ref",
        "validator_output",
    }
)

DERIVED_ROLE_SUFFIXES = frozenset(
    {
        "evidence_manifest",
        "evidence_manifest_ref",
    }
)

STABLE_ROLE_SUFFIXES = frozenset(
    {
        "agent_config",
        "atomic_feature_ledger",
        "capture",
        "capture_ref",
        "comparator",
        "comparator_ref",
        "compiled_records",
        "detached_subagent_target_capture",
        "effective_config_graph",
        "evidence_ledger",
        "freeze_manifest",
        "joined_subagent_target_capture",
        "memory_compaction_plan",
        "parity_results",
        "primitive_projection_manifest",
        "projection_events",
        "raw_source",
        "replay",
        "replay_ref",
        "schema_validation",
        "secret_scan_report",
        "session_transcript",
        "source_archive",
        "source_bb_replay_result",
        "source_comparator_report",
        "source_freeze",
        "source_raw_capture_manifest",
        "source_replay_session",
        "source_rollout",
        "target_config",
        "target_probe_output",
        "target_probe_script",
        "target_setup_report",
        "task_job_subagent_comparator",
        "transcript_continuation_patch",
        "work_item",
        "work_item_ref",
        "work_item_replay",
    }
)


DERIVED_STATIC_ROLE_IDS = frozenset(
    {
        "e4_static:report/bb_e4_atomic_feature_ledger_report_json",
        "e4_static:report/bb_e4_final_artifact_freshness_manifest_json",
        "e4_static:report/bb_e4_final_readiness_report_md",
        "e4_static:report/bb_e4_current_baseline_json",
        "e4_static:report/bb_e4_primitive_family_readiness_report_json",
        "e4_static:report/bb_e4_score_subledger_json",
        "e4_static:report/bb_e4_source_index_json",
        "e4_static:report/bb_e4_target_support_accepted_claim_hash_manifest_json",
        "e4_static:report/bb_e4_target_support_accepted_claim_report_json",
        "e4_static:report/bb_e4_target_support_accepted_claim_validation_report_json",
        "e4_static:report/bb_e4_primitive_parity_scorecard_json",
        "e4_static:report/bb_e4_remaining_prerequisites_after_l6_json",
        "e4_static:report/bb_e4_target_support_progress_json",
        "e4_static:report/bb_e4_target_support_final_hash_manifest_json",
        "e4_static:report/bb_e4_target_support_final_validation_report_json",
        "e4_static:report/bb_e4_target_support_hash_report_json",
        "e4_static:report/bb_e4_target_support_post_hash_validation_json",
        "e4_static:report/bb_e4_target_support_validation_report_json",
        "e4_static:report/conformance_matrix_sync_summary_v1_json",
        "e4_static:report/conformance_matrix_sync_summary_v1_md",
        "e4_static:report/conformance_test_matrix_v1_synced_csv",
        "e4_static:report/ct_scenarios_result_e4_1000_json",
        "e4_static:report/ct_scenarios_rows_e4_1000_json",
        "e4_static:report/tooling_manifest_json",
        "e4_static:script/build_artifact_catalog",
        "e4_static:script/build_e4_final_readiness_packet",
        "e4_static:script/accepted_artifact_materialize_adapter",
        "e4_static:script/oh_my_pi_compiler_capture_adapter",
        "e4_static:script/pi_p5_l1_capture_adapter",
        "e4_static:script/pi_p5_l2_capture_adapter",
        "e4_static:script/build_primitive_projection",
        "e4_static:script/build_source_index",
        "e4_static:script/refresh_e4_legacy_evidence_ledger_refs",
        "e4_static:script/regenerate_evidence",
        "e4_static:script/report_atomic_feature_ledger",
        "e4_static:script/scaffold_e4_target_lane",
        "e4_static:script/seed_atomic_feature_ledger",
        "e4_static:script/validate_atomic_feature_ledger",
        "e4_static:script/validate_e4_report_hash_freshness",
    }
)

CLAIM_LAYER_STATIC_ROLE_IDS = frozenset(
    role_id for role_id in DERIVED_STATIC_ROLE_IDS if role_id.startswith("e4_static:report/")
)

DERIVED_STATIC_ROLE_IDS = DERIVED_STATIC_ROLE_IDS - CLAIM_LAYER_STATIC_ROLE_IDS

STABLE_STATIC_ROLE_IDS = frozenset(
    {
        "e4_static:config/e4_lane_inventory",
        "e4_static:config/e4_report_roles",
        "e4_static:report/bb_e4_atomic_feature_ledger_seed_json",
        "e4_static:report/bb_e4_compatibility_migration_notes_md",
        "e4_static:report/bb_e4_evidence_governance_json",
        "e4_static:report/bb_e4_negative_primitive_ledger_json",
        "e4_static:report/bb_e4_oh_my_pi_p6_completion_playbook_md",
        "e4_static:report/bb_e4_primitive_decision_ledger_json",
        "e4_static:report/bb_e4_primitive_parity_master_plan_md",
        "e4_static:report/bb_e4_target_support_blocked_ready_report_json",
        "e4_static:report/bb_e4_target_support_blocked_ready_report_md",
        "e4_static:report/bb_e4_target_support_final_handoff_md",
    }
)


CATALOG_ENTRY_CLASSIFICATIONS = frozenset({"stable", "derived", "claim_layer"})


def _role_id(entry: Mapping[str, Any]) -> str:
    value = entry.get("role_id")
    return value if isinstance(value, str) else ""


def classify_entry(entry: Mapping[str, Any]) -> str:
    role_id = _role_id(entry)
    if role_id.startswith("e4_static:"):
        if role_id in CLAIM_LAYER_STATIC_ROLE_IDS:
            return "claim_layer"
        if role_id in DERIVED_STATIC_ROLE_IDS:
            return "derived"
        if role_id in STABLE_STATIC_ROLE_IDS:
            return "stable"
        raise ValueError(f"unclassified static catalog role_id: {role_id}")

    if ":" not in role_id:
        raise ValueError(f"unclassified catalog role_id: {role_id}")

    suffix = role_id.rsplit(":", 1)[-1]
    if suffix in CLAIM_LAYER_ROLE_SUFFIXES:
        return "claim_layer"
    if suffix in DERIVED_ROLE_SUFFIXES:
        return "derived"
    if suffix in STABLE_ROLE_SUFFIXES:
        return "stable"
    raise ValueError(f"unclassified lane catalog role_id: {role_id}")


def is_claim_derived_entry(entry: Mapping[str, Any]) -> bool:
    return classify_entry(entry) in {"derived", "claim_layer"}


def validate_claim_derived_classification(entries: Sequence[Mapping[str, Any]]) -> None:
    for entry in entries:
        classify_entry(entry)


def stable_entries(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    validate_claim_derived_classification(entries)
    return [entry for entry in entries if classify_entry(entry) == "stable"]


def stable_entries_hash(entries: Sequence[Mapping[str, Any]]) -> str:
    return sha256_ref(canonical_record_bytes(stable_entries(entries)))


def _segment_id_for_entry(entry: Mapping[str, Any]) -> str:
    lane_id = entry.get("lane_id")
    if isinstance(lane_id, str) and lane_id:
        return lane_id
    return "shared"


def catalog_segments(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic v2 catalog segments partitioned by lane_id plus shared."""

    by_segment: dict[str, list[Mapping[str, Any]]] = {"shared": []}
    for entry in entries:
        by_segment.setdefault(_segment_id_for_entry(entry), []).append(entry)
    segments: list[dict[str, Any]] = []
    for segment_id in sorted(by_segment):
        segment_entries = sorted(by_segment[segment_id], key=lambda entry: str(entry.get("role_id", "")))
        segments.append(
            {
                "segment_id": segment_id,
                "entry_count": len(segment_entries),
                "entries_hash": sha256_ref(canonical_record_bytes(segment_entries)),
                "stable_entries_hash": stable_entries_hash(segment_entries),
            }
        )
    return segments


def catalog_segments_hash(entries: Sequence[Mapping[str, Any]]) -> str:
    return sha256_ref(canonical_record_bytes(catalog_segments(entries)))


def catalog_segment_hash(catalog: Mapping[str, Any], segment_id: str) -> str:
    segments = catalog.get("segments")
    if not isinstance(segments, list):
        raise ValueError("artifact catalog segments must be a list")
    matches = [segment for segment in segments if isinstance(segment, Mapping) and segment.get("segment_id") == segment_id]
    if len(matches) != 1:
        raise ValueError(f"artifact catalog segment {segment_id!r} must appear exactly once")
    stable_hash = matches[0].get("stable_entries_hash")
    if not isinstance(stable_hash, str) or not stable_hash.startswith("sha256:"):
        raise ValueError(f"artifact catalog segment {segment_id!r} stable_entries_hash missing")
    return stable_hash

def catalog_stable_entries_hash(catalog: Mapping[str, Any]) -> str:
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not all(isinstance(entry, Mapping) for entry in entries):
        raise ValueError("artifact catalog entries must be a list of objects")
    return stable_entries_hash(entries)


def reusable_catalog_revision(
    catalog: Mapping[str, Any],
    prior_binding: Mapping[str, Any] | None,
    binding_hashes: Mapping[str, str],
    *,
    minimum: int = 1,
) -> int:
    """Return a catalog revision for an informational claim binding.

    The revision is not a content-addressed pin. If the claim's catalog hashes are
    unchanged, reusing the prior revision keeps claim -> catalog -> claim
    regeneration from churning only because the live catalog revision advanced.
    """

    live_revision = catalog.get("revision")
    if isinstance(live_revision, bool) or not isinstance(live_revision, int) or live_revision < minimum:
        raise ValueError(f"artifact catalog revision must be an int >= {minimum}")
    if prior_binding is None:
        return live_revision
    prior_revision = prior_binding.get("catalog_revision")
    if (
        isinstance(prior_revision, int)
        and not isinstance(prior_revision, bool)
        and minimum <= prior_revision <= live_revision
        and all(prior_binding.get(key) == value for key, value in binding_hashes.items())
    ):
        return prior_revision
    return live_revision
