from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from agentic_coder_prototype.compilation import helper_runtime_primitives as helper
from agentic_coder_prototype.compilation.primitive_records import PrimitiveCompileError
from scripts.e4_parity.fixtures import p3_lane_fixtures

GENERATED_AT = "2026-07-03T07:30:00Z"
RUN_ID = "unit_run"
LANE_ID = "unit_lane"


def _expect_compile_error(func: Callable[..., Any], /, **kwargs: Any) -> PrimitiveCompileError:
    with pytest.raises(PrimitiveCompileError) as exc_info:
        func(**kwargs)
    return exc_info.value


def _assert_error_mentions(error: PrimitiveCompileError, expected: str) -> None:
    rendered = str(error)
    rendered += "\n" + "\n".join(f"{pointer}: {message}" for pointer, message in error.errors)
    assert expected in rendered


def _valid_resource_kwargs() -> dict[str, Any]:
    return p3_lane_fixtures.resource_access_inputs(
        access_id=f"{LANE_ID}_read",
        uri="file:///workspace/src/main.py?rev=abc123#L10",
        content="print('hello')\n",
    )


def _valid_protocol_records() -> dict[str, dict[str, Any]]:
    return p3_lane_fixtures.protocol_provider_policy_records(
        run_id=RUN_ID,
        route_id=f"{LANE_ID}_route",
        policy_id=f"{LANE_ID}_policy",
        generated_at=GENERATED_AT,
    )


def _valid_memory_records() -> dict[str, dict[str, Any]]:
    records = p3_lane_fixtures.memory_work_records(
        run_id=RUN_ID,
        plan_id=f"{LANE_ID}_plan",
        work_item_id=f"{LANE_ID}_work_item",
        generated_at=GENERATED_AT,
    )
    records["memory_plan"]["schema_version"] = "bb.memory_compaction_plan.v1"
    records["work_item"]["schema_version"] = "bb.work_item.v1"
    return records


def _valid_projection_records() -> dict[str, dict[str, Any]]:
    return p3_lane_fixtures.projection_broker_records(
        run_id=RUN_ID,
        event_id=f"{LANE_ID}_projection_event",
        broker_id=f"{LANE_ID}_broker",
        generated_at=GENERATED_AT,
    )


def test_context_resource_pack_rejects_missing_source_id() -> None:
    sources = p3_lane_fixtures.context_sources(workspace_root="/workspace")
    del sources[0]["source_id"]

    error = _expect_compile_error(helper.compile_context_resource_pack, pack_id="pack", sources=sources)

    _assert_error_mentions(error, "/sources/0/source_id")


def test_context_resource_pack_rejects_invalid_source_kind() -> None:
    sources = p3_lane_fixtures.context_sources(workspace_root="/workspace")
    sources[0]["source_kind"] = "invalid-kind"

    error = _expect_compile_error(helper.compile_context_resource_pack, pack_id="pack", sources=sources)

    _assert_error_mentions(error, "/sources/0/source_kind")
    _assert_error_mentions(error, "expected one of")


def test_context_resource_pack_rejects_non_array_sources() -> None:
    error = _expect_compile_error(helper.compile_context_resource_pack, pack_id="pack", sources="not-array")

    _assert_error_mentions(error, "/sources")
    _assert_error_mentions(error, "must be an array")


def test_capability_registry_rejects_missing_name() -> None:
    declarations = p3_lane_fixtures.capability_declarations()
    del declarations[0]["name"]

    error = _expect_compile_error(
        helper.compile_capability_registry,
        registry_id="registry",
        run_id=RUN_ID,
        environment_id="env",
        declarations=declarations,
        generated_at=GENERATED_AT,
    )

    _assert_error_mentions(error, "/declarations/0/name")


def test_capability_registry_rejects_invalid_capability_type() -> None:
    declarations = p3_lane_fixtures.capability_declarations()
    declarations[0]["capability_type"] = "invalid_type"

    error = _expect_compile_error(
        helper.compile_capability_registry,
        registry_id="registry",
        run_id=RUN_ID,
        environment_id="env",
        declarations=declarations,
        generated_at=GENERATED_AT,
    )

    _assert_error_mentions(error, "/declarations/0/capability_type")
    _assert_error_mentions(error, "expected one of")


def test_capability_registry_rejects_non_array_declarations() -> None:
    error = _expect_compile_error(
        helper.compile_capability_registry,
        registry_id="registry",
        run_id=RUN_ID,
        environment_id="env",
        declarations="not-array",
        generated_at=GENERATED_AT,
    )

    _assert_error_mentions(error, "/declarations")
    _assert_error_mentions(error, "must be an array")


def test_extension_hook_rejects_missing_event_id() -> None:
    error = _expect_compile_error(
        helper.compile_extension_hook_execution,
        execution_id="exec",
        hook_id="hook",
        hook_type="pre_tool",
        event_id="",
        event_type="tool.requested",
        effects=p3_lane_fixtures.hook_effects(execution_id="exec"),
        status="completed",
        started_at=GENERATED_AT,
        duration_ms=p3_lane_fixtures.HOOK_DURATION_MS,
        model_provider_visibility=p3_lane_fixtures.hook_visibility(execution_id="exec"),
    )

    _assert_error_mentions(error, "/event_id")


def test_extension_hook_rejects_invalid_effect_status() -> None:
    effects = p3_lane_fixtures.hook_effects(execution_id="exec")
    effects[0]["status"] = "silently_applied"

    error = _expect_compile_error(
        helper.compile_extension_hook_execution,
        execution_id="exec",
        hook_id="hook",
        hook_type="pre_tool",
        event_id="event",
        event_type="tool.requested",
        effects=effects,
        status="completed",
        started_at=GENERATED_AT,
        duration_ms=p3_lane_fixtures.HOOK_DURATION_MS,
        model_provider_visibility=p3_lane_fixtures.hook_visibility(execution_id="exec"),
    )

    _assert_error_mentions(error, "/effects/0/status")
    _assert_error_mentions(error, "expected one of")


def test_extension_hook_rejects_wrong_duration_type() -> None:
    error = _expect_compile_error(
        helper.compile_extension_hook_execution,
        execution_id="exec",
        hook_id="hook",
        hook_type="pre_tool",
        event_id="event",
        event_type="tool.requested",
        effects=p3_lane_fixtures.hook_effects(execution_id="exec"),
        status="completed",
        started_at=GENERATED_AT,
        duration_ms="12",
        model_provider_visibility=p3_lane_fixtures.hook_visibility(execution_id="exec"),
    )

    _assert_error_mentions(error, "/duration_ms")


def test_resource_access_rejects_missing_uri() -> None:
    kwargs = _valid_resource_kwargs()
    kwargs["uri"] = ""

    error = _expect_compile_error(helper.compile_resource_access_bundle, **kwargs)

    _assert_error_mentions(error, "/uri")


def test_resource_access_rejects_invalid_operation() -> None:
    kwargs = _valid_resource_kwargs()
    kwargs["operation"] = "execute"

    error = _expect_compile_error(helper.compile_resource_access_bundle, **kwargs)

    _assert_error_mentions(error, "/operation")
    _assert_error_mentions(error, "expected one of")


def test_resource_access_rejects_non_array_sidecars() -> None:
    kwargs = _valid_resource_kwargs()
    kwargs["sidecars"] = "not-array"

    error = _expect_compile_error(helper.compile_resource_access_bundle, **kwargs)

    _assert_error_mentions(error, "/sidecars")


def test_protocol_provider_policy_bundle_rejects_missing_route_id() -> None:
    records = _valid_protocol_records()
    del records["provider_route"]["route_id"]

    error = _expect_compile_error(
        helper.compile_protocol_provider_policy_bundle,
        protocol_session=records["protocol_session"],
        provider_route=records["provider_route"],
        operation_policy=records["operation_policy"],
    )

    _assert_error_mentions(error, "route_id")


def test_protocol_provider_policy_bundle_rejects_invalid_route_role() -> None:
    records = _valid_protocol_records()
    records["provider_route"]["role"] = "preferred"

    error = _expect_compile_error(
        helper.compile_protocol_provider_policy_bundle,
        protocol_session=records["protocol_session"],
        provider_route=records["provider_route"],
        operation_policy=records["operation_policy"],
    )

    _assert_error_mentions(error, "/role")


def test_protocol_provider_policy_bundle_rejects_wrong_bindings_type() -> None:
    records = _valid_protocol_records()
    records["protocol_session"]["bindings"] = "not-array"

    error = _expect_compile_error(
        helper.compile_protocol_provider_policy_bundle,
        protocol_session=records["protocol_session"],
        provider_route=records["provider_route"],
        operation_policy=records["operation_policy"],
    )

    _assert_error_mentions(error, "/bindings")


def test_memory_work_evidence_is_validation_only() -> None:
    records = _valid_memory_records()

    validated = helper.validate_memory_work_evidence(
        memory_plan=records["memory_plan"],
        work_item=records["work_item"],
    )

    assert validated == {
        "memory_compaction_plan": records["memory_plan"],
        "work_item": records["work_item"],
    }


def test_memory_work_evidence_rejects_missing_plan_id() -> None:
    records = _valid_memory_records()
    del records["memory_plan"]["plan_id"]

    error = _expect_compile_error(helper.validate_memory_work_evidence, memory_plan=records["memory_plan"], work_item=records["work_item"])

    _assert_error_mentions(error, "plan_id")


def test_memory_work_evidence_rejects_invalid_work_item_status() -> None:
    records = _valid_memory_records()
    records["work_item"]["state"]["status"] = "done-ish"

    error = _expect_compile_error(helper.validate_memory_work_evidence, memory_plan=records["memory_plan"], work_item=records["work_item"])

    _assert_error_mentions(error, "/state/status")


def test_memory_work_evidence_rejects_wrong_owner_type() -> None:
    records = _valid_memory_records()
    records["work_item"]["owner"] = "main"

    error = _expect_compile_error(helper.validate_memory_work_evidence, memory_plan=records["memory_plan"], work_item=records["work_item"])

    _assert_error_mentions(error, "/owner")


def test_projection_broker_bundle_rejects_missing_projection_event_id() -> None:
    records = _valid_projection_records()
    del records["projection_event"]["projection_event_id"]

    error = _expect_compile_error(
        helper.compile_projection_broker_bundle,
        projection_event=records["projection_event"],
        side_effect_broker=records["side_effect_broker"],
    )

    _assert_error_mentions(error, "projection_event_id")


def test_projection_broker_bundle_rejects_invalid_status_frame() -> None:
    records = _valid_projection_records()
    records["projection_event"]["status_frames"][0]["status"] = "visible"

    error = _expect_compile_error(
        helper.compile_projection_broker_bundle,
        projection_event=records["projection_event"],
        side_effect_broker=records["side_effect_broker"],
    )

    _assert_error_mentions(error, "/status_frames/0/status")


def test_projection_broker_bundle_rejects_wrong_target_refs_type() -> None:
    records = _valid_projection_records()
    records["side_effect_broker"]["target_refs"] = "not-array"

    error = _expect_compile_error(
        helper.compile_projection_broker_bundle,
        projection_event=records["projection_event"],
        side_effect_broker=records["side_effect_broker"],
    )

    _assert_error_mentions(error, "/target_refs")
