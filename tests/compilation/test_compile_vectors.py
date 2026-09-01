from __future__ import annotations

from dataclasses import replace

import pytest

from breadboard_engine.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_member_map,
)
from breadboard_engine.compilation.contracts import (
    CompileOptions,
    ConfigBundleManifest,
    ConfigCompileError,
    DependencyClosureManifest,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
)
from breadboard_engine.compilation.server_compiler import (
    compile_config,
    verify_cached_manifest,
)
from breadboard.artifacts import InMemoryCAS
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "config_compiler" / "v1"
REPO_ROOT = Path(__file__).parents[2]
EXPECTED_CORPUS_DIGEST = "sha256:6a0c3564875ddecaa7bdb9d700313b7232cbf1328b81034332ce7821c3a881ea"
EXPECTED_MANIFEST_SHA256 = "c00c39d0c7aefedfc9af8cb87d942fb5d99f7aea05fcda4e5d053ffc039c34ff"


_CHILD_COMPILER = r"""
import base64
import json
import sys
from dataclasses import replace
from pathlib import Path
from breadboard_engine.compilation.bundle import ManifestReader, build_dependency_closure, ingest_member_map
from breadboard_engine.compilation.contracts import CompileOptions, ConfigBundleManifest, DependencyClosureManifest, canonical_json_loads
from breadboard_engine.compilation.server_compiler import compile_config
from breadboard.artifacts import InMemoryCAS

root = Path(sys.argv[1])
vector = json.loads((root / 'vector.json').read_bytes())
inputs = vector['input']
bundle = ConfigBundleManifest.from_json((root / inputs['bundle_manifest']).read_bytes())
closure = DependencyClosureManifest.from_json((root / inputs['closure_manifest']).read_bytes())
options = CompileOptions.from_dict(canonical_json_loads((root / inputs['compile_options']).read_bytes()))
members = {
    path.removeprefix('bundle/'): (root / path).read_bytes()
    for path in vector['bundle']['members']
}
cas = InMemoryCAS()
for entry in bundle.entries:
    cas.put_bytes(members[entry.logical_path], artifact_id=entry.artifact_id, media_type=entry.media_type)
reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
primary = compile_config(reader, closure, options).canonical_bytes()

repeat_reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
assert compile_config(repeat_reader, closure, options).canonical_bytes() == primary
result = {'primary': base64.b64encode(primary).decode('ascii')}
if vector['outcome'] == 'shadow':
    native_cas = InMemoryCAS()
    native_bundle = ingest_member_map(
        members,
        native_cas,
        entrypoints={'main': 'native.yaml'},
        source_label=f"shared-config-compiler-vector:{vector['id']}:native",
    )
    native_closure = build_dependency_closure(native_bundle, root_entrypoint='main')
    native_options = replace(options, source_contract='v2', v1_loss_policy='reject_all')
    native = compile_config(
        ManifestReader(cas=native_cas, bundle=native_bundle, closure=native_closure),
        native_closure,
        native_options,
    ).canonical_bytes()
    result['native'] = base64.b64encode(native).decode('ascii')
print(json.dumps(result, sort_keys=True, separators=(',', ':')))
"""


def _jcs_ascii(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _fixture_tree_digest(root: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    index = json.loads((root / "manifest.json").read_bytes())
    payloads = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    return payloads, index


def _vector_records() -> list[tuple[dict[str, object], dict[str, object]]]:
    index = json.loads((FIXTURE_ROOT / "manifest.json").read_bytes())
    return [
        (entry, json.loads((FIXTURE_ROOT / entry["path"]).read_bytes()))
        for entry in index["vectors"]
    ]


def _vector_path(record: dict[str, object], relative: str) -> Path:
    return FIXTURE_ROOT / "vectors" / str(record["id"]) / relative


def _members(record: dict[str, object]) -> dict[str, bytes]:
    bundle = record["bundle"]
    assert isinstance(bundle, dict)
    member_paths = bundle["members"]
    assert isinstance(member_paths, list)
    result: dict[str, bytes] = {}
    for member_path in member_paths:
        assert isinstance(member_path, str) and member_path.startswith("bundle/")
        result[member_path.removeprefix("bundle/")] = _vector_path(
            record, member_path
        ).read_bytes()
    return result




def _options_for(record: dict[str, object]) -> CompileOptions:
    inputs = record["input"]
    assert isinstance(inputs, dict)
    raw = canonical_json_loads(
        _vector_path(record, str(inputs["compile_options"])).read_bytes()
    )
    return CompileOptions.from_dict(raw)


class _PoisonableCAS:
    def __init__(self) -> None:
        self.backing = InMemoryCAS()
        self.payload_override: bytes | None = None

    def put_bytes(self, data: bytes, **kwargs: object):
        return self.backing.put_bytes(data, **kwargs)

    def has(self, artifact_ref: object) -> bool:
        return self.backing.has(artifact_ref)

    def get_ref(self, artifact_id: str):
        return self.backing.get_ref(artifact_id)

    def get_bytes(self, artifact_ref: object, *, max_bytes: int | None = None) -> bytes:
        if self.payload_override is not None:
            return self.payload_override
        return self.backing.get_bytes(artifact_ref, max_bytes=max_bytes)


def _compile_error_record(vector_id: str, error: ConfigCompileError) -> dict[str, object]:
    return {
        "schema": "bb.config-compiler-error-result.v1",
        "vector_id": vector_id,
        "outcome": "error",
        "stage": error.stage.value,
        "code": error.code.value,
        "instance_path": error.instance_pointer,
        "logical_path": error.logical_path,
        "reference_kind": error.dependency_kind,
        "raw_reference": error.raw_reference,
        "related_logical_paths": list(error.related_logical_paths),
        "details": dict(error.details),
    }


def _runner_visible_projection(manifest: object) -> dict[str, object]:
    semantic = manifest.semantic.to_canonical_obj()  # type: ignore[attr-defined]
    prompts = semantic["prompts"]
    variants = [
        {
            key: value
            for key, value in variant.items()
            if key not in {"variant_id", "config_node_id"}
        }
        for variant in prompts["variants"]
    ]
    return {
        key: value
        for key, value in semantic.items()
        if key not in {"root_config_node_id", "config_nodes", "metadata", "prompts"}
    } | {"prompts": {**prompts, "variants": variants}}


def _scenario(record: dict[str, object]) -> str:
    tags = record["tags"]
    assert isinstance(tags, list)
    if "cas-integrity" in tags:
        return "cas_payload_poison"
    if "cache-poisoning" in tags:
        return "cache_manifest_poison"
    return "compile"


def _compile_vector(record: dict[str, object]):
    vector_id = str(record["id"])
    bundle_record = record["bundle"]
    assert isinstance(bundle_record, dict)
    scenario = _scenario(record)
    cas = _PoisonableCAS() if scenario == "cas_payload_poison" else InMemoryCAS()
    inputs = record["input"]
    assert isinstance(inputs, dict)
    bundle = ConfigBundleManifest.from_json(
        _vector_path(record, str(inputs["bundle_manifest"])).read_bytes()
    )
    closure = DependencyClosureManifest.from_json(
        _vector_path(record, str(inputs["closure_manifest"])).read_bytes()
    )
    members = _members(record)
    for entry in bundle.entries:
        payload = members[entry.logical_path]
        cas.put_bytes(
            payload,
            artifact_id=entry.artifact_id,
            media_type=entry.media_type,
        )
    reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
    if scenario == "cas_payload_poison":
        assert isinstance(cas, _PoisonableCAS)
        cas.payload_override = b"version: 2\n"
    return reader, closure, bundle, _options_for(record)


def _expected_record(record: dict[str, object]) -> dict[str, object]:
    expected = record["expected"]
    assert isinstance(expected, dict)
    return json.loads(_vector_path(record, str(expected["record"])).read_bytes())

def _success_artifact_ledger(record: dict[str, object]) -> dict[str, str]:
    inputs = record["input"]
    expected = record["expected"]
    assert isinstance(inputs, dict) and isinstance(expected, dict)
    paths = {
        "bundle_manifest": str(inputs["bundle_manifest"]),
        "closure_manifest": str(inputs["closure_manifest"]),
        "compile_options": str(inputs["compile_options"]),
        "compiled_manifest": str(expected["compiled_manifest"]),
        "semantic_payload": str(expected["semantic_payload"]),
    }
    return {
        name: "sha256:" + hashlib.sha256(_vector_path(record, path).read_bytes()).hexdigest()
        for name, path in paths.items()
    }


@pytest.mark.parametrize(
    ("index_entry", "record"),
    _vector_records(),
    ids=lambda value: value.get("id", value.get("path", "vector")),
)
def test_each_shared_vector_executes_against_the_public_compiler_seam(
    index_entry: dict[str, object],
    record: dict[str, object],
) -> None:
    assert record["id"] == index_entry["id"]
    assert record["outcome"] == index_entry["outcome"]
    assert record["tags"] == index_entry["tags"]
    reader, closure, _, options = _compile_vector(record)
    expected_record = _expected_record(record)
    scenario = _scenario(record)

    if record["outcome"] == "success":
        manifest = compile_config(reader, closure, options)
        expected = record["expected"]
        assert isinstance(expected, dict)
        assert manifest.semantic.canonical_bytes() == _vector_path(
            record, str(expected["semantic_payload"])
        ).read_bytes()
        assert manifest.canonical_bytes() == _vector_path(
            record, str(expected["compiled_manifest"])
        ).read_bytes()
        actual_record = {
            "schema": "bb.config-compiler-result.v1",
            "vector_id": record["id"],
            "outcome": "success",
            "canonicalizer_id": manifest.compiler.canonicalizer_id,
            "artifacts": _success_artifact_ledger(record),
            "compiler_input_digest": manifest.inputs.compiler_input_digest,
            "semantic_digest": manifest.semantic_digest,
            "compiled_manifest_digest": manifest.compiled_manifest_digest,
            "expected_reads": [
                {
                    "logical_path": dependency.logical_path,
                    "dependency_kind": dependency.dependency_kind,
                    "blob_digest": dependency.blob_digest,
                    "size_bytes": dependency.size_bytes,
                }
                for dependency in manifest.source_dependencies
            ],
        }
        assert canonical_json_bytes(actual_record) == canonical_json_bytes(expected_record)
        return

    if record["outcome"] == "shadow":
        legacy = compile_config(reader, closure, options)
        members = _members(record)
        native_cas = InMemoryCAS()
        native_bundle = ingest_member_map(
            members,
            native_cas,
            entrypoints={"main": "native.yaml"},
            source_label=f"shared-config-compiler-vector:{record['id']}:native",
        )
        native_closure = build_dependency_closure(
            native_bundle,
            root_entrypoint="main",
        )
        native = compile_config(
            ManifestReader(
                cas=native_cas,
                bundle=native_bundle,
                closure=native_closure,
            ),
            native_closure,
            replace(
                options,
                source_contract="v2",
                v1_loss_policy="reject_all",
            ),
        )
        legacy_projection = canonical_sha256(_runner_visible_projection(legacy))
        native_projection = canonical_sha256(_runner_visible_projection(native))
        actual_record = {
            "schema": "bb.config-compiler-shadow-result.v1",
            "vector_id": record["id"],
            "outcome": "shadow",
            "legacy_manifest_digest": legacy.compiled_manifest_digest,
            "native_manifest_digest": native.compiled_manifest_digest,
            "legacy_projection_digest": legacy_projection,
            "native_projection_digest": native_projection,
            "allowed_difference_pointers": expected_record[
                "allowed_difference_pointers"
            ],
            "runtime_fallback_allowed": False,
            "executions": 0,
        }
        if "mismatch" in record["tags"]:
            assert legacy_projection != native_projection
        else:
            assert legacy_projection == native_projection
        assert canonical_json_bytes(actual_record) == canonical_json_bytes(expected_record)
        return

    with pytest.raises(ConfigCompileError) as caught:
        if scenario == "cache_manifest_poison":
            manifest = compile_config(reader, closure, options)
            poisoned = bytearray(manifest.canonical_bytes())
            position = poisoned.index(b"test-model")
            poisoned[position] ^= 1
            verify_cached_manifest(
                bytes(poisoned),
                expected_compiler_input_digest=manifest.inputs.compiler_input_digest,
            )
        else:
            compile_config(reader, closure, options)
    actual_error = _compile_error_record(str(record["id"]), caught.value)
    assert canonical_json_bytes(actual_error) == canonical_json_bytes(expected_record)


def test_cross_vector_semantic_and_identity_relations() -> None:
    records = {str(record["id"]): record for _, record in _vector_records()}

    same_a_reader, same_a_closure, _, same_a_options = _compile_vector(
        records["same-semantics-a"]
    )
    same_b_reader, same_b_closure, _, same_b_options = _compile_vector(
        records["same-semantics-b"]
    )
    same_a = compile_config(same_a_reader, same_a_closure, same_a_options)
    same_b = compile_config(same_b_reader, same_b_closure, same_b_options)
    assert canonical_json_bytes(_runner_visible_projection(same_a)) == canonical_json_bytes(
        _runner_visible_projection(same_b)
    )
    assert same_a.semantic_digest == same_b.semantic_digest
    assert same_a.inputs.bundle_digest != same_b.inputs.bundle_digest
    assert same_a.inputs.compiler_input_digest != same_b.inputs.compiler_input_digest

    first_reader, first_closure, _, first_options = _compile_vector(
        records["ordered-loop-plan-build"]
    )
    second_reader, second_closure, _, second_options = _compile_vector(
        records["ordered-loop-build-plan"]
    )
    first = compile_config(first_reader, first_closure, first_options)
    second = compile_config(second_reader, second_closure, second_options)
    assert first.semantic.loop["sequence"] != second.semantic.loop["sequence"]
    assert first.semantic_digest != second.semantic_digest

    abi_reader, abi_closure, _, abi_options = _compile_vector(
        records["runtime-abi-v2"]
    )
    abi = compile_config(abi_reader, abi_closure, abi_options)
    assert abi.inputs.compiler_input_digest != same_a.inputs.compiler_input_digest
    assert abi.semantic.runtime["runtime_abi"] == "breadboard.conductor.v2"
    assert abi.semantic_digest != same_a.semantic_digest


def test_config_compiler_vector_corpus() -> None:
    manifest_bytes = (FIXTURE_ROOT / "manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == EXPECTED_MANIFEST_SHA256
    payloads, index = _fixture_tree_digest(FIXTURE_ROOT)
    assert index["schema"] == "bb.config-compiler-corpus.v1"
    assert index["canonicalizer_id"] == "rfc8785-jcs-v1"

    indexed = {record["path"]: record for record in index["files"]}
    indexed_paths = [record["path"] for record in index["files"]]
    assert indexed_paths == sorted(indexed_paths)
    assert len(indexed_paths) == len(set(indexed_paths))
    assert set(payloads) == set(indexed)
    for relative_path, payload in payloads.items():
        assert indexed[relative_path]["size_bytes"] == len(payload)
        assert indexed[relative_path]["sha256"] == hashlib.sha256(payload).hexdigest()

    preimage = {key: value for key, value in index.items() if key != "corpus_digest"}
    assert index["corpus_digest"] == (
        "sha256:" + hashlib.sha256(_jcs_ascii(preimage)).hexdigest()
    )
    assert index["corpus_digest"] == EXPECTED_CORPUS_DIGEST
    vector_ids = [record["id"] for record in index["vectors"]]
    assert vector_ids == sorted(vector_ids)
    assert len(vector_ids) == len(set(vector_ids))
    for record in index["vectors"]:
        assert set(record) == {"id", "path", "outcome", "tags"}
        assert record["path"] == f"vectors/{record['id']}/vector.json"
        assert record["outcome"] in {"success", "error", "shadow"}
        assert record["tags"]
        assert record["tags"] == sorted(set(record["tags"]))
    assert all((FIXTURE_ROOT / record["path"]).is_file() for record in index["vectors"])

    required_tags = {
        "inheritance", "null", "empty-map", "ordered-list", "closure",
        "duplicate-key", "tools", "plugins", "prompts", "templates",
        "guardrails", "team", "nested-config", "provider", "runtime", "task",
        "verifier", "evidence", "v1-shadow", "unknown-key", "cas-integrity",
        "cache-poisoning", "identity", "fresh-process", "unknown-display-name",
        "codex", "claude", "opencode", "oh-my-opencode", "pi", "terminal", "swe",
    }
    observed_tags = {tag for record in index["vectors"] for tag in record["tags"]}
    assert required_tags <= observed_tags


@pytest.mark.parametrize(
    "record",
    [
        record
        for _, record in _vector_records()
        if record["outcome"] in {"success", "shadow"}
    ],
    ids=lambda record: str(record["id"]),
)
def test_real_vectors_are_byte_identical_in_fresh_processes(
    record: dict[str, object],
    tmp_path: Path,
) -> None:
    vector_root = FIXTURE_ROOT / "vectors" / str(record["id"])
    outputs: list[dict[str, str]] = []
    for index, hash_seed in enumerate(("1", "987654321")):
        cwd = tmp_path / f"cwd-{index}"
        home = tmp_path / f"home-{index}"
        cwd.mkdir()
        home.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "PYTHONHASHSEED": hash_seed,
                "HOME": str(home),
                "AGENT_SCHEMA_V2_ENABLED": f"hostile-{index}",
                "BREADBOARD_PLUGIN_DIRS": str(tmp_path / f"plugins-{index}"),
                "XDG_CACHE_HOME": str(tmp_path / f"cache-{index}"),
                "TZ": "UTC" if index == 0 else "Pacific/Honolulu",
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_COMPILER, str(vector_root)],
            cwd=cwd,
            env=environment,
            check=True,
            capture_output=True,
        )
        assert result.stderr == b""
        outputs.append(json.loads(result.stdout))

    assert outputs[0] == outputs[1]
    expected = record["expected"]
    assert isinstance(expected, dict)
    assert base64.b64decode(outputs[0]["primary"]) == _vector_path(
        record, str(expected["compiled_manifest"])
    ).read_bytes()
    if record["outcome"] == "shadow":
        expected_record = _expected_record(record)
        native = json.loads(base64.b64decode(outputs[0]["native"]))
        assert native["compiled_manifest_digest"] == expected_record["native_manifest_digest"]


def test_shared_fixture_bytes_are_identical_to_wrapper_mirror() -> None:
    wrapper_root = (
        REPO_ROOT.parent
        / "verl_wrapper_breadboard_integration_20260709"
        / "tests"
        / "fixtures"
        / "config_compiler"
        / "v1"
    )
    if not wrapper_root.is_dir():
        pytest.skip("verl wrapper mirror is not provisioned in this checkout")
    authoritative, authoritative_index = _fixture_tree_digest(FIXTURE_ROOT)
    mirrored, mirrored_index = _fixture_tree_digest(wrapper_root)

    assert mirrored == authoritative
    assert mirrored_index == authoritative_index
