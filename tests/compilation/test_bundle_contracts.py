from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, replace

import pytest

from agentic_coder_prototype.compilation.bundle import (
    build_dependency_closure,
    ingest_member_map,
)
from agentic_coder_prototype.compilation.contracts import (
    MAX_SAFE_INTEGER,
    BundleIntegrityError,
    BundleLimits,
    BundleValidationError,
    CanonicalJSONError,
    ConfigBundleManifest,
    DependencyEdge,
    canonical_json_bytes,
    canonical_json_loads,
    normalize_logical_path,
)
from breadboard.rl.state import InMemoryCAS


class DuplicateKeyMapping(Mapping[str, int]):
    """Mapping-shaped input whose item stream preserves a duplicate key."""

    def __getitem__(self, key: str) -> int:
        if key == "duplicate":
            return 2
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield "duplicate"

    def __len__(self) -> int:
        return 1

    def items(self):  # type: ignore[override]
        return (("duplicate", 1), ("duplicate", 2))


def test_canonical_json_matches_jcs_number_string_and_utf16_key_order() -> None:
    payload = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27, -0.0],
        "string": "€$\u000f\nA'B\"\\\"/",
        "\ufffd": 1,
        "😀": 2,
    }

    assert canonical_json_bytes(payload) == (
        b'{"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27,0],'
        b'"string":"\xe2\x82\xac$\\u000f\\nA\'B\\"\\\\\\"/",'
        b'"\xf0\x9f\x98\x80":2,"\xef\xbf\xbd":1}'
    )


@pytest.mark.parametrize(
    "value",
    [
        {1: "non-string key"},
        float("nan"),
        float("inf"),
        float("-inf"),
        {"unsupported": {"set"}},
        "\ud800",
    ],
)
def test_canonical_json_rejects_values_without_a_cross_language_identity(value: object) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json_bytes(value)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"duplicate":1,"duplicate":2}',
        b'{"number":NaN}',
        b'{"number":Infinity}',
        b'not-json',
    ],
)
def test_canonical_json_loader_rejects_ambiguous_or_invalid_payloads(payload: bytes) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json_loads(payload)


def test_canonical_json_encoder_rejects_duplicate_mapping_item_stream() -> None:
    with pytest.raises(CanonicalJSONError, match="duplicate"):
        canonical_json_bytes(DuplicateKeyMapping())


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("configs/cafe\u0301.yaml", "configs/caf\u00e9.yaml"),
        ("prompts/日本語.txt", "prompts/日本語.txt"),
    ],
)
def test_logical_paths_are_normalized_to_unicode_nfc(raw: str, normalized: str) -> None:
    assert normalize_logical_path(raw) == normalized


def test_bundle_and_closure_manifests_are_closed_immutable_and_round_trip() -> None:
    manifest = ingest_member_map(
        {"config.yaml": b"version: 2\n", "prompts/system.txt": b"Be exact.\n"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        source_label="fixture-A",
    )
    closure = build_dependency_closure(
        manifest,
        root_entrypoint="main",
        edges=(
            DependencyEdge(
                from_path="config.yaml",
                kind="prompt",
                raw_ref="prompts/system.txt",
                logical_path="prompts/system.txt",
            ),
        ),
    )

    assert ConfigBundleManifest.from_json(manifest.canonical_bytes()) == manifest
    assert type(closure).from_json(closure.canonical_bytes()) == closure
    assert manifest.bundle_digest.startswith("sha256:") and len(manifest.bundle_digest) == 71
    assert closure.closure_digest.startswith("sha256:") and len(closure.closure_digest) == 71
    with pytest.raises(FrozenInstanceError):
        manifest.bundle_digest = "sha256:" + "0" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        closure.root_entrypoint = "other.yaml"  # type: ignore[misc]

    manifest_payload = manifest.to_dict()
    manifest_payload["unknown"] = True
    with pytest.raises(BundleValidationError, match="unknown"):
        ConfigBundleManifest.from_dict(manifest_payload)
    closure_payload = closure.to_dict()
    closure_payload["unknown"] = True
    with pytest.raises(BundleValidationError, match="unknown"):
        type(closure).from_dict(closure_payload)


def test_member_map_manifest_is_deterministic_across_input_order_and_fresh_stores() -> None:
    members_a = {
        "z-last.txt": b"last",
        "config.yaml": b"version: 2\n",
        "tools/a.yaml": b"name: a\n",
    }
    members_b = dict(reversed(tuple(members_a.items())))

    entrypoints_a = {"secondary": "tools/a.yaml", "main": "config.yaml"}
    entrypoints_b = dict(reversed(tuple(entrypoints_a.items())))
    first = ingest_member_map(
        members_a,
        InMemoryCAS(),
        entrypoints=entrypoints_a,
        source_label="same-source",
    )
    second = ingest_member_map(
        members_b,
        InMemoryCAS(),
        entrypoints=entrypoints_b,
        source_label="same-source",
    )
    edges = (
        DependencyEdge("config.yaml", "member", "tools/a.yaml", "tools/a.yaml", 0),
        DependencyEdge("config.yaml", "member", "z-last.txt", "z-last.txt", 1),
    )
    first_closure = build_dependency_closure(
        first, root_entrypoint="main", edges=edges
    )
    second_closure = build_dependency_closure(
        second, root_entrypoint="main", edges=tuple(reversed(edges))
    )

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.bundle_digest == second.bundle_digest
    assert first_closure.canonical_bytes() == second_closure.canonical_bytes()
    assert first_closure.closure_digest == second_closure.closure_digest
    assert tuple(entry.logical_path for entry in first.entries) == tuple(sorted(members_a))


def test_source_bundle_and_closure_digests_bind_their_exact_inputs() -> None:
    original = ingest_member_map(
        {"config.yaml": b"value: one\n", "dep.txt": b"same\n"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        source_label="source-A",
    )
    byte_changed = ingest_member_map(
        {"config.yaml": b"value: two\n", "dep.txt": b"same\n"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        source_label="source-A",
    )
    provenance_changed = ingest_member_map(
        {"config.yaml": b"value: one\n", "dep.txt": b"same\n"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        source_label="source-B",
    )
    first_edge = DependencyEdge(
        from_path="config.yaml",
        kind="extends",
        raw_ref="./dep.txt",
        logical_path="dep.txt",
    )
    second_edge = replace(first_edge, raw_ref="dep.txt")
    original_closure = build_dependency_closure(
        original, root_entrypoint="main", edges=(first_edge,)
    )
    changed_edge_closure = build_dependency_closure(
        original, root_entrypoint="main", edges=(second_edge,)
    )
    changed_bundle_closure = build_dependency_closure(
        byte_changed, root_entrypoint="main", edges=(first_edge,)
    )

    assert original.provenance.raw_source_digest != byte_changed.provenance.raw_source_digest
    assert original.bundle_digest != byte_changed.bundle_digest
    assert original.bundle_digest == provenance_changed.bundle_digest
    assert original_closure.closure_digest != changed_edge_closure.closure_digest
    assert original_closure.closure_digest != changed_bundle_closure.closure_digest
    assert original.bundle_digest == changed_edge_closure.bundle_digest


def test_manifest_rejects_digest_and_total_tampering() -> None:
    manifest = ingest_member_map(
        {"config.yaml": b"version: 2\n"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
    )
    wrong_digest = manifest.to_dict()
    wrong_digest["bundle_digest"] = "sha256:" + "0" * 64
    with pytest.raises(BundleIntegrityError, match="digest"):
        ConfigBundleManifest.from_dict(wrong_digest)

    wrong_total = manifest.to_dict()
    wrong_total["total_bytes"] += 1
    with pytest.raises(BundleIntegrityError, match="totals"):
        ConfigBundleManifest.from_dict(wrong_total)


def test_limit_values_accept_safe_boundary_and_reject_overflow_or_bool() -> None:
    assert BundleLimits(max_members=MAX_SAFE_INTEGER).max_members == MAX_SAFE_INTEGER
    with pytest.raises(BundleValidationError, match="safe integer"):
        BundleLimits(max_members=MAX_SAFE_INTEGER + 1)
    with pytest.raises(BundleValidationError, match="positive integer"):
        BundleLimits(max_members=True)  # type: ignore[arg-type]
