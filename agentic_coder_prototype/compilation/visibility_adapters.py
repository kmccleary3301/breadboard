from __future__ import annotations

from .primitive_records import PrimitiveCompileError

CanonicalVisibility = dict[str, bool | str]


_CONFIG_GRAPH_VISIBILITY: dict[str, CanonicalVisibility] = {
    "model-visible": {
        "model_visible": True,
        "provider_visible": True,
        "host_visible": True,
        "redaction_state": "none",
    },
    "host-only": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": True,
        "redaction_state": "none",
    },
    "redacted": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": True,
        "redaction_state": "redacted",
    },
}

_REGISTRY_VISIBILITY: dict[str, CanonicalVisibility] = {
    "model_visible": {
        "model_visible": True,
        "provider_visible": True,
        "host_visible": True,
        "redaction_state": "none",
    },
    "provider_visible": {
        "model_visible": False,
        "provider_visible": True,
        "host_visible": True,
        "redaction_state": "none",
    },
    "host_only": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": True,
        "redaction_state": "none",
    },
    "policy_hidden": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": False,
        "redaction_state": "elided",
    },
}

_RESOURCE_REF_VISIBILITY: dict[str, CanonicalVisibility] = {
    "model_visible": {
        "model_visible": True,
        "provider_visible": True,
        "host_visible": True,
        "redaction_state": "none",
    },
    "provider_visible": {
        "model_visible": False,
        "provider_visible": True,
        "host_visible": True,
        "redaction_state": "none",
    },
    "host_only": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": True,
        "redaction_state": "none",
    },
    "secret": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": False,
        "redaction_state": "redacted",
    },
    "redacted": {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": True,
        "redaction_state": "redacted",
    },
}


def from_config_graph_class(value: str) -> dict[str, bool | str]:
    """Map effective-config-graph visibility classes to canonical visibility."""

    return _from_table(
        value,
        table=_CONFIG_GRAPH_VISIBILITY,
        schema_version="bb.effective_config_graph.v1",
        dialect="config graph visibility class",
    )


def from_registry_enum(value: str) -> dict[str, bool | str]:
    """Map capability-registry visibility enums to canonical visibility."""

    return _from_table(
        value,
        table=_REGISTRY_VISIBILITY,
        schema_version="bb.capability_registry.v1",
        dialect="capability registry visibility enum",
    )


def from_resource_ref_enum(value: str) -> dict[str, bool | str]:
    """Map resource-ref visibility enums to canonical visibility."""

    return _from_table(
        value,
        table=_RESOURCE_REF_VISIBILITY,
        schema_version="bb.resource_ref.v1",
        dialect="resource ref visibility enum",
    )


def _from_table(
    value: str,
    *,
    table: dict[str, CanonicalVisibility],
    schema_version: str,
    dialect: str,
) -> dict[str, bool | str]:
    try:
        mapped = table[value]
    except KeyError as exc:
        allowed = ", ".join(sorted(table))
        raise PrimitiveCompileError(
            schema_version=schema_version,
            record_id=None,
            errors=[("", f"unknown {dialect}: {value!r}; expected one of: {allowed}")],
        ) from exc
    return dict(mapped)


__all__ = [
    "from_config_graph_class",
    "from_registry_enum",
    "from_resource_ref_enum",
]
