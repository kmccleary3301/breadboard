from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from agentic_coder_prototype.compilation import helper_runtime_primitives as helper
from scripts.e4_parity.fixtures import p3_lane_fixtures

Projection = Callable[[Mapping[str, Any]], dict[str, Any]]
ProjectionResult = dict[str, Any]

HELPER_COMPILER_BY_PROJECTION: dict[str, str] = {
    "p3_2_context_resource_pack": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_context_resource_pack",
    "p3_3_capability_registry": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_capability_registry",
    "p3_4_extension_hook_execution": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_extension_hook_execution",
    "p3_5_resource_ref": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_resource_access_bundle",
    "p3_5_blob_ref": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_resource_access_bundle",
    "p3_5_resource_access": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_resource_access_bundle",
    "p3_5_write_resource_access": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_resource_access_bundle",
    "p3_5_truncated_redacted_access": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_resource_access_bundle",
    "p3_6_external_protocol_session": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_protocol_provider_policy_bundle",
    "p3_6_provider_route": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_protocol_provider_policy_bundle",
    "p3_6_effective_operation_policy": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_protocol_provider_policy_bundle",
    "p3_7_memory_compaction_plan": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_memory_work_bundle",
    "p3_7_work_item": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_memory_work_bundle",
    "p3_8_projection_event": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_projection_broker_bundle",
    "p3_8_side_effect_broker": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_projection_broker_bundle",
}


def _result(record_key: str, value: Mapping[str, Any], derived_facts: Mapping[str, Any] | None = None) -> ProjectionResult:
    return {
        "records": [{"record_key": record_key, "value": dict(value)}],
        "derived_facts": dict(derived_facts or {}),
    }


def _lane_id(context: Mapping[str, Any]) -> str:
    return str(context["lane_id"])


def _run_id(context: Mapping[str, Any]) -> str:
    return str(context["run_id"])


def _generated_at(context: Mapping[str, Any]) -> str:
    return str(context["generated_at_utc"])


def _root(context: Mapping[str, Any]) -> Path:
    return Path(str(context["root"]))


def _project_context_resource_pack(context: Mapping[str, Any]) -> ProjectionResult:
    record = helper.compile_context_resource_pack(
        pack_id=f"{_lane_id(context)}_pack",
        sources=p3_lane_fixtures.context_sources(workspace_root=str(_root(context))),
        render_profile=p3_lane_fixtures.RENDER_PROFILE,
    )
    return _result(
        "context_resource_pack",
        record,
        {
            "context_order_preserved": record["render_order"],
            "redacted_env_hidden_from_model": record["model_visibility"]["redacted_source_ids"],
        },
    )


def _project_capability_registry(context: Mapping[str, Any]) -> ProjectionResult:
    lane_id = _lane_id(context)
    record = helper.compile_capability_registry(
        registry_id=f"{lane_id}_registry",
        run_id=_run_id(context),
        environment_id=f"{lane_id}_env",
        declarations=p3_lane_fixtures.capability_declarations(),
        generated_at=_generated_at(context),
    )
    return _result(
        "capability_registry",
        record,
        {
            "first_wins_capability_count": [item["capability_id"] for item in record["capabilities"]],
            "shadow_and_disabled_in_mutation_log": [item["operation"] for item in record["mutation_log"]],
        },
    )


def _project_extension_hook_execution(context: Mapping[str, Any]) -> ProjectionResult:
    lane_id = _lane_id(context)
    execution_id = f"{lane_id}_hook_exec"
    record = helper.compile_extension_hook_execution(
        execution_id=execution_id,
        hook_id=f"{lane_id}_policy_hook",
        hook_type="pre_tool",
        event_id=f"{lane_id}_tool_request",
        event_type="tool.requested",
        effects=p3_lane_fixtures.hook_effects(execution_id=execution_id),
        status="completed",
        started_at=_generated_at(context),
        duration_ms=p3_lane_fixtures.HOOK_DURATION_MS,
        model_provider_visibility=p3_lane_fixtures.hook_visibility(execution_id=execution_id),
    )
    return _result("extension_hook_execution", record, {"hook_effect_types": [effect["effect_type"] for effect in record["effects"]]})


def _resource_access_bundle(context: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = _lane_id(context)
    read_access = helper.compile_resource_access_bundle(
        **p3_lane_fixtures.resource_access_inputs(
            access_id=f"{lane_id}_read",
            uri="file:///workspace/src/main.py?rev=abc123#L10",
            content="print('hello')\n",
        )
    )
    write_access = helper.compile_resource_access_bundle(
        **p3_lane_fixtures.resource_access_inputs(
            access_id=f"{lane_id}_write",
            uri="file:///workspace/out/report.txt",
            content="approved output\n",
            operation="write",
            approval_required=True,
        )
    )
    truncated = helper.compile_resource_access_bundle(
        **p3_lane_fixtures.resource_access_inputs(
            access_id=f"{lane_id}_truncated_redacted",
            uri="file:///workspace/logs/secret.log",
            content="secret=redacted\n" * 20,
            returned_size_bytes=24,
            redacted=True,
        )
    )
    return {
        "resource_ref": read_access["resource_ref"],
        "blob_ref": read_access["blob_ref"],
        "resource_access": read_access["resource_access"],
        "write_resource_access": write_access["resource_access"],
        "truncated_redacted_access": truncated["resource_access"],
    }


def _select_resource_access_record(record_key: str, fact_keys: tuple[str, ...] = ()) -> Projection:
    def project(context: Mapping[str, Any]) -> ProjectionResult:
        records = _resource_access_bundle(context)
        facts = {
            "resource_access_variants": sorted(key for key in records if key.endswith("access")),
            "truncation_and_redaction_recorded": [records["truncated_redacted_access"]["truncation"]["truncated"], records["truncated_redacted_access"]["redaction"]["redacted"]],
            "write_requires_approval": records["write_resource_access"]["approval"]["required"],
        }
        return _result(record_key, records[record_key], {key: facts[key] for key in fact_keys})

    return project


def _protocol_provider_policy_bundle(context: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = _lane_id(context)
    records = p3_lane_fixtures.protocol_provider_policy_records(
        run_id=_run_id(context),
        route_id=f"{lane_id}_route",
        policy_id=f"{lane_id}_policy",
        generated_at=_generated_at(context),
    )
    return helper.compile_protocol_provider_policy_bundle(
        protocol_session=records["protocol_session"],
        provider_route=records["provider_route"],
        operation_policy=records["operation_policy"],
    )


def _select_protocol_provider_policy_record(record_key: str, fact_keys: tuple[str, ...] = ()) -> Projection:
    def project(context: Mapping[str, Any]) -> ProjectionResult:
        records = _protocol_provider_policy_bundle(context)
        facts = {
            "fallback_selected_index": records["provider_route"]["selected_fallback_index"],
            "policy_feeds_route": records["effective_operation_policy"]["applies_to"]["provider_route_ref"],
        }
        return _result(record_key, records[record_key], {key: facts[key] for key in fact_keys})

    return project


def _memory_work_bundle(context: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = _lane_id(context)
    records = p3_lane_fixtures.memory_work_records(
        run_id=_run_id(context),
        plan_id=f"{lane_id}_plan",
        work_item_id=f"{lane_id}_work_item",
        generated_at=_generated_at(context),
    )
    return helper.compile_memory_work_bundle(memory_plan=records["memory_plan"], work_item=records["work_item"])


def _select_memory_work_record(record_key: str, fact_keys: tuple[str, ...] = ()) -> Projection:
    def project(context: Mapping[str, Any]) -> ProjectionResult:
        records = _memory_work_bundle(context)
        facts = {
            "work_checkpoint_refs_memory_plan": records["work_item"]["state"]["checkpoint_ref"],
            "memory_plan_has_transcript_and_summary": [len(records["memory_compaction_plan"]["transcript_refs"]), len(records["memory_compaction_plan"]["generated_refs"])],
        }
        return _result(record_key, records[record_key], {key: facts[key] for key in fact_keys})

    return project


def _projection_broker_bundle(context: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = _lane_id(context)
    records = p3_lane_fixtures.projection_broker_records(
        run_id=_run_id(context),
        event_id=f"{lane_id}_projection_event",
        broker_id=f"{lane_id}_broker",
        generated_at=_generated_at(context),
    )
    return helper.compile_projection_broker_bundle(
        projection_event=records["projection_event"],
        side_effect_broker=records["side_effect_broker"],
    )


def _select_projection_broker_record(record_key: str, fact_keys: tuple[str, ...] = ()) -> Projection:
    def project(context: Mapping[str, Any]) -> ProjectionResult:
        records = _projection_broker_bundle(context)
        facts = {
            "projection_is_not_kernel_truth": records["projection_event"]["kernel_truth"],
            "broker_audits_before_after_refs": [len(records["side_effect_broker"]["before_refs"]), len(records["side_effect_broker"]["after_refs"])],
        }
        return _result(record_key, records[record_key], {key: facts[key] for key in fact_keys})

    return project


PROJECTIONS: dict[str, Projection] = {
    "p3_2_context_resource_pack": _project_context_resource_pack,
    "p3_3_capability_registry": _project_capability_registry,
    "p3_4_extension_hook_execution": _project_extension_hook_execution,
    "p3_5_resource_ref": _select_resource_access_record("resource_ref"),
    "p3_5_blob_ref": _select_resource_access_record("blob_ref"),
    "p3_5_resource_access": _select_resource_access_record("resource_access", ("resource_access_variants",)),
    "p3_5_write_resource_access": _select_resource_access_record("write_resource_access", ("write_requires_approval",)),
    "p3_5_truncated_redacted_access": _select_resource_access_record("truncated_redacted_access", ("truncation_and_redaction_recorded",)),
    "p3_6_external_protocol_session": _select_protocol_provider_policy_record("external_protocol_session"),
    "p3_6_provider_route": _select_protocol_provider_policy_record("provider_route", ("fallback_selected_index",)),
    "p3_6_effective_operation_policy": _select_protocol_provider_policy_record("effective_operation_policy", ("policy_feeds_route",)),
    "p3_7_memory_compaction_plan": _select_memory_work_record("memory_compaction_plan", ("memory_plan_has_transcript_and_summary",)),
    "p3_7_work_item": _select_memory_work_record("work_item", ("work_checkpoint_refs_memory_plan",)),
    "p3_8_projection_event": _select_projection_broker_record("projection_event", ("projection_is_not_kernel_truth",)),
    "p3_8_side_effect_broker": _select_projection_broker_record("side_effect_broker", ("broker_audits_before_after_refs",)),
}
