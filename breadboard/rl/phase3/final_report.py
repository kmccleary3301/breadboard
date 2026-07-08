from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from breadboard.rl.phase3.evidence import PHASE3_COMPONENT_REPORT_SCHEMA, sha256_file, validate_phase3_artifact_hashes, validate_phase3_command_log_manifest, validate_phase3_component_report
from breadboard.rl.phase3.observability_live import LOCAL_OBJECT_STORE_BACKENDS, endpoint_is_local, validate_scheduler_metrics_readiness
from breadboard.rl.phase3.parity import PHASE3_PARITY_REPORT_ID, validate_phase3_parity_report

PHASE3_FINAL_SCHEMA = "bb.rl.phase3.final_report.v1"
PHASE3_FINAL_REPORT_ID = "bb_zyphra_rl_phase3_final_report_v1"
PHASE3_FINAL_CLAIM_BOUNDARY = "phase3_final_report_existing_artifact_audit_scope"
PHASE3_RETIRED_MILESTONES: tuple[str, ...] = ()
PHASE3_MILESTONES = tuple(f"P3-M{index}" for index in range(13))
PHASE3_ACTIVE_MILESTONES = tuple(milestone for milestone in PHASE3_MILESTONES if milestone not in PHASE3_RETIRED_MILESTONES)
PHASE3_MILESTONE_POINTS = {
    "P3-M0": 40,
    "P3-M1": 80,
    "P3-M2": 120,
    "P3-M3": 120,
    "P3-M4": 100,
    "P3-M5": 90,
    "P3-M6": 80,
    "P3-M7": 80,
    "P3-M8": 70,
    "P3-M9": 60,
    "P3-M10": 60,
    "P3-M11": 80,
    "P3-M12": 20,
}
PHASE3_ORIGINAL_TOTAL_POINTS = 1000
PHASE3_ACTIVE_SCOPE_SCHEMA = "bb.rl.phase3.active_scope.v1"
PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY = "phase3_active_scope_all_milestones_target_evidence_scope"
PHASE3_ACTIVE_SCOPE_READY_MEANING = (
    "Existing artifacts satisfy the promoted exact-scope Phase 3 boundary; broader or successor claims require separate canonical promotion."
)
PHASE3_CORE_READINESS_SCHEMA = PHASE3_ACTIVE_SCOPE_SCHEMA
PHASE3_CORE_CLAIM_BOUNDARY = PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY
PHASE3_DEFERRED_MILESTONES: tuple[str, ...] = PHASE3_RETIRED_MILESTONES
PHASE3_CORE_MILESTONES = PHASE3_ACTIVE_MILESTONES
PHASE3_CORE_RAW_POINTS_TOTAL = sum(PHASE3_MILESTONE_POINTS[milestone] for milestone in PHASE3_CORE_MILESTONES)
PHASE3_MILESTONE_CLAIM_BOUNDARIES = {
    "P3-M0": "phase3_strict_evidence_gates_named_scope",
    "P3-M1": "phase3_target_verl_api_introspection_named_scope",
    "P3-M2": "phase3_ppo_weight_update_8gpu_named_target_scope",
    "P3-M3": "phase3_grpo_weight_update_8gpu_named_target_scope",
    "P3-M4": "phase3_closed_loop_projection_to_real_verl_checkpoint_8gpu_scope",
    "P3-M5": "phase3_api_sqlite_persistence_local_validation_scope",
    "P3-M6": "phase3_containerized_slurm_workspace_hardening_scope",
    "P3-M7": "phase3_named_benchmark_campaign_scope",
    "P3-M8": "phase3_retired_provider_milestone_accepted_scope",
    "P3-M9": "phase3_harbor_nemo_gym_named_endpoint_scope",
    "P3-M11": "phase3_live_observability_object_store_scheduler_scope",
    "P3-M10": "phase3_second_environment_family_lean_console_isolated_elan_scope",
    "P3-M12": "phase3_final_report_audit_existing_artifacts_scope",
}


PHASE3_MILESTONE_BLOCKED_CLAIM_BOUNDARIES = {
    "P3-M8": "phase3_retired_provider_milestone_pending_rubric_change_scope",
    "P3-M9": "phase3_harbor_nemo_gym_blocked_scope",
    "P3-M11": "phase3_observability_scheduler_store_blocked_scope",
}


def _expected_claim_boundary(milestone_id: str, report: Mapping[str, Any]) -> str:
    if report.get("passed") is True:
        return PHASE3_MILESTONE_CLAIM_BOUNDARIES[milestone_id]
    return PHASE3_MILESTONE_BLOCKED_CLAIM_BOUNDARIES.get(
        milestone_id,
        PHASE3_MILESTONE_CLAIM_BOUNDARIES[milestone_id],
    )


def _validate_blocked_component_report(
    report: Mapping[str, Any], *, expected_schema: str, expected_claim_boundary: str, target_run_id: str,
    required_artifact_keys: tuple[str, ...], evidence_root: Path
) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != expected_schema:
        errors.append("schema_version must match expected component schema")
    if not str(report.get("component") or ""):
        errors.append("generic Phase 3 component reports must include component")
    if report.get("claim_boundary") != expected_claim_boundary:
        errors.append("claim_boundary must match exact expected claim boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("passed") is True:
        errors.append("blocked component reports must not pass")
    if not _blocked_reason(report):
        errors.append("blocked component reports must include blocked_reason")
    if not str(report.get("report_id") or ""):
        errors.append("report_id must be present")
    if report.get("target_run_id") != target_run_id:
        errors.append("target_run_id must match expected target run")
    if not isinstance(report.get("input_hashes"), Mapping) or not report.get("input_hashes"):
        errors.append("input_hashes must be a non-empty mapping")
    artifact_paths = report.get("artifact_paths")
    artifact_paths = artifact_paths if isinstance(artifact_paths, Mapping) else {}
    if not artifact_paths:
        errors.append("artifact_paths must be present")
    errors.extend(validate_phase3_artifact_hashes(report, required_artifact_keys=required_artifact_keys, evidence_root=evidence_root))
    return errors


def _mapping_copy(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}



def _blocked_reason(report: Mapping[str, Any]) -> str:
    reason = report.get("blocked_reason")
    return reason if isinstance(reason, str) else ""



def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _artifact_paths(report: Mapping[str, Any]) -> Mapping[str, Any]:
    paths = report.get("artifact_paths")
    return paths if isinstance(paths, Mapping) else {}


def _input_hashes(report: Mapping[str, Any]) -> Mapping[str, Any]:
    hashes = report.get("input_hashes")
    return hashes if isinstance(hashes, Mapping) else {}




def _validate_p3m8_retirement_acceptance(component: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if component.get("passed") is not True:
        return errors
    if component.get("provider_kind") != "none":
        errors.append("P3-M8 accepted retirement must not claim a live provider (provider_kind must be none)")
    if component.get("retirement_accepted") is not True:
        errors.append("P3-M8 accepted retirement must set retirement_accepted true")
    if not str(component.get("rubric_decision") or ""):
        errors.append("P3-M8 accepted retirement must record a rubric_decision")
    if component.get("scorecard_update_allowed") is not False:
        errors.append("P3-M8 accepted retirement scorecard_update_allowed must be false")
    provider_report = component.get("provider_report")
    provider_report = provider_report if isinstance(provider_report, Mapping) else {}
    if provider_report.get("provider_kind") != "none":
        errors.append("P3-M8 accepted retirement provider_report.provider_kind must be none")
    if provider_report.get("retirement_accepted") is not True:
        errors.append("P3-M8 accepted retirement provider_report.retirement_accepted must be true")
    if provider_report.get("passed") is not True:
        errors.append("P3-M8 accepted retirement provider_report.passed must be true")
    if provider_report.get("scorecard_update_allowed") is not False:
        errors.append("P3-M8 accepted retirement provider_report.scorecard_update_allowed must be false")
    return errors


def _validate_p3m9_provider_classification(component: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if component.get("passed") is not True:
        return errors
    provider_report = component.get("provider_report")
    provider_report = provider_report if isinstance(provider_report, Mapping) else {}
    component_kind = component.get("provider_kind")
    report_kind = provider_report.get("attestation_backend")
    if component_kind != "harbor_facade":
        errors.append("P3-M9 provider_kind must be harbor_facade for the active Harbor/NeMo Gym path")
    if report_kind != "harbor_facade":
        errors.append("P3-M9 provider_report.attestation_backend must be harbor_facade for the active Harbor/NeMo Gym path")
    if component_kind == "native_benchflow" or report_kind == "native_benchflow":
        errors.append("P3-M9 native BenchFlow evidence is contract-only and cannot satisfy the active Harbor/NeMo Gym milestone")
    if provider_report.get("self_hosted") is True:
        errors.append("P3-M9 harbor provider_report.self_hosted must not be true for canonical target evidence")
    endpoint_identity = str(provider_report.get("endpoint_identity") or "")
    if endpoint_is_local(endpoint_identity):
        errors.append("P3-M9 harbor provider_report.endpoint_identity must not be local for canonical target evidence")
    required_strings = (
        "endpoint_identity",
        "env_package_sha256",
        "task_name",
        "trial_id_sha256",
    )
    for key in required_strings:
        if not str(provider_report.get(key) or ""):
            errors.append(f"P3-M9 harbor provider_report.{key} must be present")
    if provider_report.get("passed") is not True:
        errors.append("P3-M9 harbor provider_report must pass")
    if provider_report.get("scorecard_update_allowed") is not False:
        errors.append("P3-M9 harbor provider_report.scorecard_update_allowed must be false")
    routes = provider_report.get("harbor_routes")
    routes = [str(route) for route in routes] if isinstance(routes, list) else []
    required_routes = [
        "GET /health",
        "GET /metrics.json",
        "GET /list_tasks",
        "POST /score",
        "POST /trial/create",
        "POST /trial/{trial_id}/exec",
        "GET /trial/{trial_id}",
        "POST /trial/{trial_id}/finalize",
    ]
    if routes != required_routes:
        errors.append("P3-M9 harbor provider_report.harbor_routes must match the Harbor service proof route sequence")
    calls = provider_report.get("harbor_calls")
    calls = calls if isinstance(calls, list) else []
    call_routes = []
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            errors.append(f"P3-M9 harbor provider_report.harbor_calls[{index}] must be an object")
            continue
        call_routes.append(str(call.get("route_template") or ""))
        for key in ("method", "route_template", "status_code", "request_sha256", "response_sha256", "latency_seconds", "passed"):
            if key not in call:
                errors.append(f"P3-M9 harbor provider_report.harbor_calls[{index}].{key} must be present")
        if call.get("passed") is not True:
            errors.append(f"P3-M9 harbor provider_report.harbor_calls[{index}] must pass")
    if call_routes != required_routes:
        errors.append("P3-M9 harbor provider_report.harbor_calls must match the Harbor service proof route sequence")
    return errors


def _reject_local_metric_urls(metric_sections: Mapping[str, Any]) -> list[str]:
    url_fields = (
        ("verifier_metrics", "endpoint", "P3-M11 verifier_metrics.endpoint must not be local"),
        ("object_store_metrics", "endpoint", "P3-M11 object_store_metrics.endpoint must not be local"),
        ("object_store_metrics", "put_endpoint", "P3-M11 object_store_metrics.put_endpoint must not be local"),
        ("object_store_metrics", "get_endpoint", "P3-M11 object_store_metrics.get_endpoint must not be local"),
        ("object_store_metrics", "delete_endpoint", "P3-M11 object_store_metrics.delete_endpoint must not be local"),
    )
    errors: list[str] = []
    for section_name, field_name, error in url_fields:
        section = metric_sections.get(section_name)
        if isinstance(section, Mapping) and endpoint_is_local(str(section.get(field_name) or "")):
            errors.append(error)
    scheduler = metric_sections.get("scheduler_metrics")
    scheduler_control = scheduler.get("scheduler_control") if isinstance(scheduler, Mapping) else None
    if isinstance(scheduler_control, Mapping) and endpoint_is_local(str(scheduler_control.get("endpoint") or "")):
        errors.append("P3-M11 scheduler_metrics.scheduler_control.endpoint must not be local")
    return errors

def _presence_flag(section: Mapping[str, Any], key: str) -> bool | None:
    if key not in section:
        return None
    value = section[key]
    if isinstance(value, Mapping):
        if "present" in value:
            return value["present"] is True
        return None
    if isinstance(value, bool):
        return value
    return None


def _env_presence_flag(section: Mapping[str, Any], env_name: str) -> bool | None:
    env_presence = section.get("env_presence")
    if not isinstance(env_presence, Mapping):
        return None
    return _presence_flag(env_presence, env_name)


def _any_presence(section: Mapping[str, Any], keys: tuple[str, ...], env_names: tuple[str, ...] = ()) -> bool:
    if any(_presence_flag(section, key) is True for key in keys):
        return True
    return any(_env_presence_flag(section, env_name) is True for env_name in env_names)


def _status_is_success(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return 200 <= value < 300
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"ok", "passed", "success", "succeeded", "verified"}:
            return True
        try:
            return 200 <= int(normalized) < 300
        except ValueError:
            return False
    return False


def _operation_status_ok(section: Mapping[str, Any], operation: str) -> bool:
    candidates = (
        f"{operation}_status",
        f"{operation}_status_code",
        f"{operation}_http_status",
        f"{operation}_passed",
        f"{operation}_verified",
    )
    for key in candidates:
        if key in section and _status_is_success(section[key]):
            return True
    operation_payload = section.get(operation)
    if isinstance(operation_payload, Mapping):
        for key in ("status", "status_code", "http_status", "passed", "verified"):
            if key in operation_payload and _status_is_success(operation_payload[key]):
                return True
    return False


def _validate_readback_durability(object_store: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if object_store.get("write_read_verified") is not True:
        errors.append("P3-M11 object_store_metrics.write_read_verified must be true")
    boolean_signals = (
        "readback_verified",
        "read_after_write_verified",
        "readback_matches",
        "durability_verified",
        "readback_durable",
    )
    for key in boolean_signals:
        if key in object_store and object_store.get(key) is not True:
            errors.append(f"P3-M11 object_store_metrics.{key} must be true when present")
    expected_hash = object_store.get("written_sha256") or object_store.get("put_sha256")
    readback_hash = object_store.get("readback_sha256") or object_store.get("get_sha256")
    if not isinstance(expected_hash, str) or not expected_hash:
        errors.append("P3-M11 object_store_metrics written_sha256/put_sha256 must be present")
    if not isinstance(readback_hash, str) or not readback_hash:
        errors.append("P3-M11 object_store_metrics readback_sha256/get_sha256 must be present")
    if isinstance(expected_hash, str) and expected_hash and isinstance(readback_hash, str) and readback_hash and expected_hash != readback_hash:
        errors.append("P3-M11 object_store_metrics readback hash must match written hash")
    return errors


def _validate_p3m11_observability_promotion(component: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if component.get("passed") is not True:
        return errors
    observability = component.get("observability_evidence")
    observability = observability if isinstance(observability, Mapping) else {}
    if observability.get("passed") is not True:
        errors.append("P3-M11 observability_evidence.passed must be true")
    evidence_errors = observability.get("errors")
    if evidence_errors:
        errors.append("P3-M11 observability_evidence.errors must be empty")
    metric_sections = observability.get("metric_sections")
    metric_sections = metric_sections if isinstance(metric_sections, Mapping) else {}
    object_store = metric_sections.get("object_store_metrics")
    if not isinstance(object_store, Mapping):
        errors.append("P3-M11 observability_evidence.metric_sections.object_store_metrics must be present")
        object_store = {}
    object_store_backend = str(object_store.get("object_store") or "")
    if not object_store_backend:
        errors.append("P3-M11 object_store_metrics.object_store must be present")
    elif object_store_backend in LOCAL_OBJECT_STORE_BACKENDS:
        errors.append("P3-M11 object_store_metrics.object_store must be a production object-store backend")
    errors.extend(_reject_local_metric_urls(metric_sections))
    verifier = metric_sections.get("verifier_metrics")
    if not isinstance(verifier, Mapping):
        errors.append("P3-M11 observability_evidence.metric_sections.verifier_metrics must be present")
        verifier = {}
    verifier_endpoint = str(verifier.get("endpoint") or "")
    if not verifier_endpoint:
        errors.append("P3-M11 verifier_metrics.endpoint must be present")
    elif endpoint_is_local(verifier_endpoint):
        errors.append("P3-M11 verifier_metrics.endpoint must not be local")
    if not _any_presence(verifier, ("token_present", "verifier_token_present"), ("BREADBOARD_VERIFIER_TOKEN",)):
        errors.append("P3-M11 verifier_metrics token evidence must be present")
    if not _any_presence(
        object_store,
        ("token_present", "object_store_token_present", "credential_present", "credentials_present", "access_key_present"),
        ("BREADBOARD_OBJECT_STORE_TOKEN", "BREADBOARD_OBJECT_STORE_ACCESS_KEY"),
    ):
        errors.append("P3-M11 object_store_metrics token evidence must be present")
    for endpoint_field in ("put_endpoint", "get_endpoint", "delete_endpoint"):
        endpoint = str(object_store.get(endpoint_field) or "")
        if not endpoint:
            errors.append(f"P3-M11 object_store_metrics.{endpoint_field} must be present")
        elif endpoint_is_local(endpoint):
            errors.append(f"P3-M11 object_store_metrics.{endpoint_field} must not be local")
    for operation in ("put", "get", "delete"):
        if not _operation_status_ok(object_store, operation):
            errors.append(f"P3-M11 object_store_metrics.{operation} status evidence must show success")
    errors.extend(_validate_readback_durability(object_store))
    scheduler = metric_sections.get("scheduler_metrics")
    if not isinstance(scheduler, Mapping):
        errors.append("P3-M11 observability_evidence.metric_sections.scheduler_metrics must be present")
        scheduler = {}
    for scheduler_error in validate_scheduler_metrics_readiness(scheduler):
        errors.append(f"P3-M11 observability_evidence.metric_sections.scheduler_metrics.{scheduler_error}")
    if observability.get("verifier_latency") is None:
        errors.append("P3-M11 observability_evidence.verifier_latency must be present")
    return errors




def _fixture_benchmark_without_external_inputs(component: Mapping[str, Any]) -> bool:
    benchmark = component.get("benchmark_report")
    benchmark = benchmark if isinstance(benchmark, Mapping) else {}
    slice_report = benchmark.get("slice_report")
    slice_report = slice_report if isinstance(slice_report, Mapping) else {}
    metadata = slice_report.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    source_pin = benchmark.get("source_pin")
    source_pin = source_pin if isinstance(source_pin, Mapping) else {}
    if metadata.get("fixture_scope") == "hash_pinned_benchmark_slice":
        return True
    return source_pin.get("benchmark_version") == "fixture-v2"

def _benchmark_uses_non_model_candidate(component: Mapping[str, Any]) -> bool:
    benchmark = component.get("benchmark_report")
    benchmark = benchmark if isinstance(benchmark, Mapping) else {}
    prompt_scan = benchmark.get("prompt_solution_leakage_scan")
    if not isinstance(prompt_scan, Mapping):
        return False
    candidate_source = str(prompt_scan.get("candidate_source") or "").lower()
    non_model_markers = ("hand_written", "hand-written", "target_payload", "probe_only", "synthetic_baseline")
    return any(marker in candidate_source for marker in non_model_markers)


def _validate_p3m7_benchmark_candidate_source(component: Mapping[str, Any]) -> list[str]:
    if component.get("passed") is not True:
        return []
    if _benchmark_uses_non_model_candidate(component):
        return ["P3-M7 benchmark candidate source must come from the Phase 3 model pipeline"]
    return []


def _core_milestone_blocker(milestone_id: str, component: Mapping[str, Any]) -> str:
    if milestone_id == "P3-M7" and _fixture_benchmark_without_external_inputs(component):
        return "fixture_benchmark_not_external_core_credit"
    if milestone_id == "P3-M7" and _benchmark_uses_non_model_candidate(component):
        return "benchmark_candidate_not_phase3_model_pipeline"
    blocked_reason = _blocked_reason(component)
    if blocked_reason:
        return blocked_reason
    if component.get("passed") is not True:
        return "milestone_report_not_passed"
    return ""


def build_phase3_core_readiness(milestone_reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    report_sources = milestone_reports if isinstance(milestone_reports, Mapping) else {}
    milestone_statuses = []
    blocked_milestones = []
    for milestone_id in PHASE3_ACTIVE_MILESTONES:
        component = report_sources.get(milestone_id, {})
        component = component if isinstance(component, Mapping) else {}
        blocker = _core_milestone_blocker(milestone_id, component)
        active_complete = blocker == ""
        if not active_complete:
            blocked_milestones.append(milestone_id)
        point_value = PHASE3_MILESTONE_POINTS[milestone_id]
        milestone_statuses.append({
            "milestone_id": milestone_id,
            "point_value": point_value,
            "active_complete": active_complete,
            "blocker": blocker,
            "report_id": component.get("report_id"),
            "passed": component.get("passed") is True,
        })
    ready = not blocked_milestones
    label = "active-artifact-audit-clean" if ready else "active-artifact-audit-blocked"
    ready_meaning = PHASE3_ACTIVE_SCOPE_READY_MEANING
    core_raw_points_verified = sum(
        PHASE3_MILESTONE_POINTS[status["milestone_id"]]
        for status in milestone_statuses
        if status["active_complete"]
    )
    return {
        "schema_version": PHASE3_ACTIVE_SCOPE_SCHEMA,
        "claim_boundary": PHASE3_ACTIVE_SCOPE_CLAIM_BOUNDARY,
        "ready": ready,
        "artifact_audit_clean": ready,
        "ready_meaning": ready_meaning,
        "scorecard_update_allowed": False,
        "active_milestones": list(PHASE3_ACTIVE_MILESTONES),
        "retired_milestones": list(PHASE3_RETIRED_MILESTONES),
        "blocked_active_milestones": blocked_milestones,
        "core_milestones": list(PHASE3_ACTIVE_MILESTONES),
        "deferred_milestones": list(PHASE3_RETIRED_MILESTONES),
        "blocked_core_milestones": blocked_milestones,
        "core_raw_points_total": PHASE3_CORE_RAW_POINTS_TOTAL,
        "core_raw_points_verified": core_raw_points_verified,
        "original_scorecard_total_points": PHASE3_ORIGINAL_TOTAL_POINTS,
        "report_label": label,
        "milestone_statuses": milestone_statuses,
    }


def build_phase3_active_scope(milestone_reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return build_phase3_core_readiness(milestone_reports)

def _summary_by_milestone(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    summaries = report.get("milestone_summaries")
    if not isinstance(summaries, list):
        return {}
    mapped: dict[str, Mapping[str, Any]] = {}
    for summary in summaries:
        if not isinstance(summary, Mapping):
            continue
        milestone_id = summary.get("milestone_id")
        if isinstance(milestone_id, str):
            mapped[milestone_id] = summary
    return mapped

def _validate_milestone_summary_list(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    summaries = report.get("milestone_summaries")
    if not isinstance(summaries, list):
        return ["milestone_summaries must be a list"]
    milestone_ids: list[str] = []
    for index, summary in enumerate(summaries):
        if not isinstance(summary, Mapping):
            errors.append(f"milestone_summaries[{index}] must be an object")
            continue
        milestone_id = summary.get("milestone_id")
        if not isinstance(milestone_id, str):
            errors.append(f"milestone_summaries[{index}].milestone_id must be a string")
            continue
        milestone_ids.append(milestone_id)
    if milestone_ids != list(PHASE3_MILESTONES):
        errors.append("milestone_summaries must appear exactly once in Phase 3 milestone order")
    return errors




def _validate_milestone_summary(milestone_id: str, component: Mapping[str, Any], summary: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    blocked_reason = _blocked_reason(component)
    passed = component.get("passed") is True
    claim_ready = passed and not blocked_reason
    expected = {
        "report_id": component.get("report_id"),
        "schema_version": component.get("schema_version"),
        "claim_boundary": component.get("claim_boundary"),
        "passed": passed,
        "blocked_reason": blocked_reason,
        "claim_ready": claim_ready,
        "scorecard_update_allowed": component.get("scorecard_update_allowed"),
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            errors.append(f"{milestone_id} summary.{key} must match milestone report")
    return errors



def build_phase3_final_report(
    *, target_run_id: str, milestone_reports: Mapping[str, Mapping[str, Any]], command_log_manifest: Mapping[str, Any],
    scorecard: Mapping[str, Any], claim_ledger_text: str
) -> dict[str, Any]:
    report_sources = milestone_reports if isinstance(milestone_reports, Mapping) else {}
    summaries = []
    for milestone_id in PHASE3_MILESTONES:
        report = report_sources.get(milestone_id, {})
        report = report if isinstance(report, Mapping) else {}
        blocked_reason = _blocked_reason(report)
        claim_ready = report.get("passed") is True and not blocked_reason
        summaries.append({
            "milestone_id": milestone_id,
            "report_id": report.get("report_id"),
            "schema_version": report.get("schema_version"),
            "claim_boundary": report.get("claim_boundary"),
            "passed": report.get("passed") is True,
            "blocked_reason": blocked_reason,
            "claim_ready": claim_ready,
            "scorecard_update_allowed": report.get("scorecard_update_allowed"),
        })
    return {
        "schema_version": PHASE3_FINAL_SCHEMA,
        "report_id": PHASE3_FINAL_REPORT_ID,
        "claim_boundary": PHASE3_FINAL_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "milestone_summaries": summaries,
        "milestone_reports": {key: _mapping_copy(value) for key, value in report_sources.items()},
        "active_scope": build_phase3_active_scope(report_sources),
        "core_readiness": build_phase3_active_scope(report_sources),
        "command_log_manifest": _mapping_copy(command_log_manifest),
        "scorecard": {},
        "claim_ledger_text": claim_ledger_text if isinstance(claim_ledger_text, str) else "",
        "scorecard_update_allowed": False,
    }


def validate_phase3_final_report(report: Mapping[str, Any], *, repo_root: Path, evidence_root: Path) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != PHASE3_FINAL_SCHEMA:
        errors.append("schema_version must be Phase 3 final report schema")
    if report.get("report_id") != PHASE3_FINAL_REPORT_ID:
        errors.append("report_id must be Phase 3 final report id")
    if report.get("claim_boundary") != PHASE3_FINAL_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be Phase 3 final boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("final report cannot update scorecard")
    target_run_id = str(report.get("target_run_id") or "")
    manifest = report.get("command_log_manifest") if isinstance(report.get("command_log_manifest"), Mapping) else {}
    errors.extend(validate_phase3_command_log_manifest(manifest, target_run_id=target_run_id, repo_root=repo_root, evidence_root=evidence_root))
    errors.extend(_validate_milestone_summary_list(report))
    milestone_summaries = _summary_by_milestone(report)
    for milestone_id in PHASE3_MILESTONES:
        if milestone_id not in milestone_summaries:
            errors.append(f"{milestone_id} summary must be present")
    for milestone_id in milestone_summaries:
        if milestone_id not in PHASE3_MILESTONES:
            errors.append(f"{milestone_id} summary is not a Phase 3 milestone")
    milestone_reports = report.get("milestone_reports") if isinstance(report.get("milestone_reports"), Mapping) else {}
    for milestone_id in milestone_reports:
        if milestone_id not in PHASE3_MILESTONES:
            errors.append(f"{milestone_id} report is not a Phase 3 milestone")
    parity_refs: dict[str, str] = {}
    parity_hashes: dict[str, str] = {}
    for milestone_id in PHASE3_MILESTONES:
        component = milestone_reports.get(milestone_id)
        if not isinstance(component, Mapping):
            errors.append(f"{milestone_id} report must be present")
            continue
        if component.get("milestone_id") != milestone_id:
            errors.append(f"{milestone_id} report milestone_id must match outer milestone key")
        schema_version = str(component.get("schema_version") or "")
        summary = milestone_summaries.get(milestone_id, {})
        if isinstance(summary, Mapping):
            errors.extend(_validate_milestone_summary(milestone_id, component, summary))
        claim_boundary = str(component.get("claim_boundary") or "")
        expected_claim_boundary = _expected_claim_boundary(milestone_id, component)
        if not schema_version:
            errors.append(f"{milestone_id} schema_version must be present")
            continue
        if not claim_boundary:
            errors.append(f"{milestone_id} claim_boundary must be present")
            continue
        if claim_boundary != expected_claim_boundary:
            errors.append(f"{milestone_id} claim_boundary must be {expected_claim_boundary}")
        blocked_reason = _blocked_reason(component)
        required_artifact_keys = tuple(component.get("required_artifact_keys", ()))
        if blocked_reason:
            component_errors = _validate_blocked_component_report(
                component,
                expected_schema=PHASE3_COMPONENT_REPORT_SCHEMA,
                expected_claim_boundary=expected_claim_boundary,
                target_run_id=target_run_id,
                required_artifact_keys=required_artifact_keys,
                evidence_root=evidence_root,
            )
        else:
            component_errors = validate_phase3_component_report(
                component,
                expected_schema=PHASE3_COMPONENT_REPORT_SCHEMA,
                expected_claim_boundary=expected_claim_boundary,
                target_run_id=target_run_id,
                required_artifact_keys=required_artifact_keys,
                evidence_root=evidence_root,
            )
        if milestone_id in {"P3-M2", "P3-M3", "P3-M4"}:
            component_paths = _artifact_paths(component)
            component_hashes = _input_hashes(component)
            parity_report_path = component_paths.get("parity_report")
            parity_report_hash = component_hashes.get("parity_report")
            if not isinstance(parity_report_path, str) or not parity_report_path:
                errors.append(f"{milestone_id}: parity_report artifact path must be present")
            else:
                parity_refs[milestone_id] = parity_report_path
            if not isinstance(parity_report_hash, str) or not parity_report_hash:
                errors.append(f"{milestone_id}: parity_report input hash must be present")
            else:
                parity_hashes[milestone_id] = parity_report_hash
            if component.get("parity_report_id") != PHASE3_PARITY_REPORT_ID:
                errors.append(f"{milestone_id}: parity_report_id must be {PHASE3_PARITY_REPORT_ID}")
        if milestone_id == "P3-M7" and _int_or_zero(component.get("points")) > 0 and _fixture_benchmark_without_external_inputs(component):
            if component.get("scorecard_update_allowed") is not False:
                errors.append("P3-M7 fixture benchmark evidence cannot update scorecard")
        if milestone_id == "P3-M8":
            errors.extend(_validate_p3m8_retirement_acceptance(component))
        if milestone_id == "P3-M7":
            errors.extend(_validate_p3m7_benchmark_candidate_source(component))
        if milestone_id == "P3-M9":
            errors.extend(_validate_p3m9_provider_classification(component))
        if milestone_id == "P3-M11":
            errors.extend(_validate_p3m11_observability_promotion(component))
        errors.extend(f"{milestone_id}: {error}" for error in component_errors)
    if parity_refs:
        if len(set(parity_refs.values())) != 1:
            errors.append("P3-M2/P3-M3/P3-M4 parity_report artifact paths must match")
        if len(set(parity_hashes.values())) != 1:
            errors.append("P3-M2/P3-M3/P3-M4 parity_report input hashes must match")
        parity_rel = next(iter(parity_refs.values()))
        parity_path = (evidence_root / parity_rel).resolve()
        try:
            parity_path.relative_to(evidence_root.resolve())
        except ValueError:
            errors.append("parity_report artifact must stay under evidence_root")
        else:
            parity_report = _load_json_mapping(parity_path)
            if not parity_report:
                errors.append("parity_report artifact must be readable JSON")
            else:
                errors.extend(f"parity_report: {error}" for error in validate_phase3_parity_report(parity_report, target_run_id=target_run_id, evidence_root=evidence_root))
                parity_sha = sha256_file(parity_path)
                expected_sha = next(iter(parity_hashes.values()), "")
                if expected_sha and parity_sha != expected_sha:
                    errors.append("parity_report input hash must match artifact content")
    active_scope = report.get("active_scope")
    if not isinstance(active_scope, Mapping):
        active_scope = report.get("core_readiness")
    if not isinstance(active_scope, Mapping):
        errors.append("active_scope must be present")
    else:
        expected_active_scope = build_phase3_active_scope(milestone_reports)
        for key, value in expected_active_scope.items():
            if active_scope.get(key) != value:
                errors.append(f"active_scope.{key} must match milestone reports")
    ledger_raw = report.get("claim_ledger_text")
    ledger = ledger_raw if isinstance(ledger_raw, str) else ""
    if target_run_id not in ledger or PHASE3_FINAL_REPORT_ID not in ledger or PHASE3_FINAL_CLAIM_BOUNDARY not in ledger:
        errors.append("claim ledger must contain target_run_id, final report id, and final claim boundary")
    return errors
