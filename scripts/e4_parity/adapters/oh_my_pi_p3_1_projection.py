from __future__ import annotations

from typing import Any, Callable, Mapping

from scripts.e4_parity import build_primitive_projection as primitive_projection

ProjectionContext = Mapping[str, Any]
ProjectionResult = dict[str, Any]
Projection = Callable[[ProjectionContext], ProjectionResult]

L1_PROBE_SOURCE = "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json"
L1_MANIFEST_SOURCE = "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/raw_capture_manifest.json"
L2_PROBE_SOURCE = "docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/target_probe_output.json"
L2_MANIFEST_SOURCE = "docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/raw_capture_manifest.json"

P3_1_INPUT_SOURCES = (
    L1_PROBE_SOURCE,
    L1_MANIFEST_SOURCE,
    L2_PROBE_SOURCE,
    L2_MANIFEST_SOURCE,
)


def _required_mapping(context: ProjectionContext, key: str) -> Mapping[str, Any]:
    value = context.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"P3.1 projection context requires mapping {key!r}")
    return value


def _inputs(context: ProjectionContext) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    l1_probe = _required_mapping(context, "l1_probe")
    l2_probe = _required_mapping(context, "l2_probe")
    l1_manifest = _required_mapping(context, "l1_manifest")
    l2_manifest = _required_mapping(context, "l2_manifest")
    if l1_probe["builtinToolNames"] and l2_probe["builtin_tool_count"] != len(l1_probe["builtinToolNames"]):
        raise ValueError("L1 builtin tool names and L2 builtin tool count disagree")
    if l1_probe["selectedSettings"] != l2_probe["selectedSettings"]:
        raise ValueError("L1 and L2 selected settings disagree")
    return l1_probe, l2_probe, l1_manifest, l2_manifest


def _single_record(record_key: str, schema_version: str, value: Mapping[str, Any]) -> ProjectionResult:
    return {
        "records": [
            {
                "record_key": record_key,
                "schema_version": schema_version,
                "value": dict(value),
            }
        ]
    }


def project_p3_1_capability_registry(context: ProjectionContext) -> ProjectionResult:
    l1_probe, l2_probe, l1_manifest, l2_manifest = _inputs(context)
    return _single_record(
        "capability_registry",
        "bb.capability_registry.v1",
        primitive_projection._build_capability_registry(l1_probe, l2_probe, l1_manifest, l2_manifest),
    )


def project_p3_1_effective_config_graph(context: ProjectionContext) -> ProjectionResult:
    l1_probe, l2_probe, l1_manifest, l2_manifest = _inputs(context)
    return _single_record(
        "effective_config_graph",
        "bb.effective_config_graph.v1",
        primitive_projection._build_effective_config_graph(l1_probe, l2_probe, l1_manifest, l2_manifest),
    )


def project_p3_1_effective_tool_surface(context: ProjectionContext) -> ProjectionResult:
    l1_probe, _l2_probe, _l1_manifest, _l2_manifest = _inputs(context)
    return _single_record(
        "effective_tool_surface",
        "bb.effective_tool_surface.v1",
        primitive_projection._build_effective_tool_surface(l1_probe),
    )


PROJECTIONS: dict[str, Projection] = {
    "p3_1_capability_registry": project_p3_1_capability_registry,
    "p3_1_effective_config_graph": project_p3_1_effective_config_graph,
    "p3_1_effective_tool_surface": project_p3_1_effective_tool_surface,
}


__all__ = [
    "P3_1_INPUT_SOURCES",
    "PROJECTIONS",
    "project_p3_1_capability_registry",
    "project_p3_1_effective_config_graph",
    "project_p3_1_effective_tool_surface",
]
