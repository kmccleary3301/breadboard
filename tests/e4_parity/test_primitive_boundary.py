from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from agentic_coder_prototype.compilation.primitive_records import (
    PrimitiveCompileError,
    canonical_record_bytes,
    finalize_record,
    get_spec,
    sha256_ref,
)
from agentic_coder_prototype.compilation.visibility_adapters import (
    from_config_graph_class,
    from_registry_enum,
    from_resource_ref_enum,
)


def _minimal_effective_config_graph() -> dict[str, Any]:
    return {
        "graph_id": "graph-roundtrip",
        "source_layers": [
            {
                "layer_id": "layer-default",
                "source_kind": "default",
                "scope": "project",
                "precedence": 0,
                "source_ref": None,
                "layer_hash": "sha256:" + "1" * 64,
                "model_visible": True,
                "host_visible": True,
            }
        ],
        "effective_values": [
            {
                "path": "tools.echo.enabled",
                "value_kind": "boolean",
                "value": True,
                "source_layer_id": "layer-default",
                "visibility": "model-visible",
                "env_gate_ids": [],
            }
        ],
        "merge_policy": {
            "policy_id": "policy-default",
            "strategy": "replace",
            "conflict_resolution": "highest-precedence",
        },
        "visibility": {
            "model_visible_paths": ["tools.echo.enabled"],
            "host_only_paths": [],
            "redacted_paths": [],
        },
        "env_gates": [],
        "migrations": [],
    }


def _minimal_capability_registry() -> dict[str, Any]:
    return {
        "registry_id": "registry-roundtrip",
        "generated_at": "2026-07-03T00:00:00Z",
        "subject": {
            "environment_id": "env-test",
            "run_id": None,
        },
        "capabilities": [
            {
                "capability_id": "tool.echo",
                "capability_type": "tool",
                "name": "echo",
                "discovery": {
                    "state": "discovered",
                    "mode": "manual",
                    "evidence_ref": None,
                },
                "exposure": {
                    "state": "exposed",
                    "mode": "model_visible",
                    "model_visible": True,
                    "surface_refs": ["surface.echo"],
                },
                "visibility": "model_visible",
            }
        ],
    }

def _minimal_resource_access() -> dict[str, Any]:
    return {
        "access_id": "access-roundtrip",
        "resource": {
            "schema_version": "bb.resource_ref.v1",
            "uri": "local://artifact.json",
            "scheme": "local",
            "resolver_id": "local-resolver",
            "authority": None,
            "path": "/artifact.json",
            "query_hash": None,
            "fragment": None,
            "scope": {"scope_id": "workspace", "kind": "workspace", "boundary": None},
            "visibility": "host_only",
            "immutability": {"mode": "snapshot", "content_hash": "sha256:" + "2" * 64},
            "retention": {"policy": "project", "expires_at": None},
            "containment": {"root_uri": "local://", "parent_uri": None, "relationship": "descendant"},
        },
        "operation": "read",
        "status": "completed",
        "content_hash": "sha256:" + "3" * 64,
        "blob_refs": [
            {
                "schema_version": "bb.blob_ref.v1",
                "blob_id": "blob-roundtrip",
                "digest": {"algorithm": "sha256", "value": "4" * 64},
                "media_type": "application/json",
                "size_bytes": 2,
                "storage": {
                    "storage_class": "content_addressed",
                    "uri": "sha256:" + "4" * 64,
                    "resolver_id": "cas",
                    "encrypted_at_rest": False,
                },
                "retention": {"policy": "project", "expires_at": None, "legal_hold": False},
                "sidecars": [],
            }
        ],
        "truncation": {
            "truncated": False,
            "original_size_bytes": 2,
            "returned_size_bytes": 2,
            "strategy": None,
        },
        "redaction": {"redacted": False, "policy_ids": [], "redaction_refs": []},
        "approval": {"required": False, "status": "not_required", "approval_id": None},
        "model_provider_visibility": {"model_visible": False, "provider_visible": False},
    }



def test_finalize_record_round_trips_hashed_effective_config_graph() -> None:
    spec = get_spec("bb.effective_config_graph.v1")
    source = _minimal_effective_config_graph()
    original = copy.deepcopy(source)

    finalized = finalize_record(spec, source)

    assert source == original
    assert finalized["schema_version"] == "bb.effective_config_graph.v1"
    assert finalized["graph_id"] == source["graph_id"]
    assert finalized["source_layers"] == source["source_layers"]
    assert finalized["effective_values"] == source["effective_values"]
    preimage = {key: value for key, value in finalized.items() if key != "graph_hash"}
    assert finalized["graph_hash"] == sha256_ref(canonical_record_bytes(preimage))


def test_finalize_record_round_trips_unhashed_capability_registry() -> None:
    spec = get_spec("bb.capability_registry.v1")
    source = _minimal_capability_registry()
    original = copy.deepcopy(source)

    finalized = finalize_record(spec, source)

    assert source == original
    assert finalized == {"schema_version": "bb.capability_registry.v1", **source}
    assert "registry_hash" not in finalized

def test_finalize_record_resolves_registered_cross_schema_refs() -> None:
    spec = get_spec("bb.resource_access.v1")
    source = _minimal_resource_access()
    original = copy.deepcopy(source)

    finalized = finalize_record(spec, source)

    assert source == original
    assert finalized == {"schema_version": "bb.resource_access.v1", **source}



def test_invalid_record_raises_compile_error_with_record_id_and_sorted_json_pointers() -> None:
    spec = get_spec("bb.effective_config_graph.v1")
    invalid = _minimal_effective_config_graph()
    invalid["source_layers"][0]["precedence"] = -1
    invalid["effective_values"][0]["visibility"] = "member-visible"
    invalid["merge_policy"]["strategy"] = "merge-somehow"

    with pytest.raises(PrimitiveCompileError) as exc_info:
        finalize_record(spec, invalid)

    error = exc_info.value
    pointers = [pointer for pointer, _ in error.errors]
    assert error.schema_version == "bb.effective_config_graph.v1"
    assert error.record_id == "graph-roundtrip"
    assert pointers == sorted(pointers)
    assert pointers == [
        "/effective_values/0/visibility",
        "/merge_policy/strategy",
        "/source_layers/0/precedence",
    ]
    message = str(error)
    assert "bb.effective_config_graph.v1 record graph-roundtrip" in message
    for pointer in pointers:
        assert pointer in message


def test_repeated_finalize_record_output_hash_is_deterministic() -> None:
    spec = get_spec("bb.effective_config_graph.v1")
    source = _minimal_effective_config_graph()

    first = finalize_record(spec, source)
    second = finalize_record(spec, source)

    assert first == second
    assert first["graph_hash"] == second["graph_hash"]


def test_unknown_schema_version_raises_compile_error() -> None:
    with pytest.raises(PrimitiveCompileError) as exc_info:
        get_spec("bb.unknown_primitive.v1")

    error = exc_info.value
    assert error.schema_version == "bb.unknown_primitive.v1"
    assert error.record_id is None
    assert error.errors == [("", "unknown schema_version")]
    assert "use one of SPEC_REGISTRY.keys()" in str(error)


@pytest.mark.parametrize(
    ("adapter", "unknown_value", "schema_version", "dialect", "allowed"),
    [
        (
            from_config_graph_class,
            "member-visible",
            "bb.effective_config_graph.v1",
            "config graph visibility class",
            "host-only, model-visible, redacted",
        ),
        (
            from_registry_enum,
            "member_visible",
            "bb.capability_registry.v1",
            "capability registry visibility enum",
            "host_only, model_visible, policy_hidden, provider_visible",
        ),
        (
            from_resource_ref_enum,
            "member_visible",
            "bb.resource_ref.v1",
            "resource ref visibility enum",
            "host_only, model_visible, provider_visible, redacted, secret",
        ),
    ],
)
def test_visibility_adapters_reject_unknown_members(
    adapter: Callable[[str], dict[str, bool | str]],
    unknown_value: str,
    schema_version: str,
    dialect: str,
    allowed: str,
) -> None:
    with pytest.raises(PrimitiveCompileError) as exc_info:
        adapter(unknown_value)

    error = exc_info.value
    assert error.schema_version == schema_version
    assert error.record_id is None
    assert error.errors == [("", f"unknown {dialect}: {unknown_value!r}; expected one of: {allowed}")]
    assert f"unknown {dialect}: {unknown_value!r}" in str(error)
    assert f"expected one of: {allowed}" in str(error)
