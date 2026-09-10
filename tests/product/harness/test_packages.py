from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard_engine.compilation.contracts import bytes_sha256, canonical_json_bytes
from breadboard.product.harness.resolution import compile_harness_source
from breadboard.product.harness.packages import (
    ModulePackageIntegrityError,
    ModulePackageValidationError,
    build_module_package,
    load_module_package,
)


_RUNTIME_REF = "sha256:" + "a" * 64


def _write_source(root: Path, *, code: bytes, source_digest: str | None = None) -> Path:
    code_path = root / "src" / "ranker.py"
    code_path.parent.mkdir(parents=True)
    code_path.write_bytes(code)
    digest = source_digest or bytes_sha256(code)
    manifest = {
        "schema_version": "bb.module_manifest.v1",
        "logical_package": "example.ranker",
        "package_version": "1.0.0",
        "entrypoint": "src/ranker.py:open_instance",
        "execution_tier": "trusted_native",
        "worker_protocol": "bb.worker.v2",
        "input_schema_ids": ["bb.example.input.v1"],
        "output_schema_ids": ["bb.example.output.v1"],
        "checkpoint_schema_id": "bb.example.checkpoint.v1",
        "accepted_checkpoint_schema_ids": ["bb.example.checkpoint.v1"],
        "dependency_contracts": {"scoring": "bb.example.scoring.v1"},
        "child_targets": [
            {
                "label": "review",
                "target": "reviewer",
                "contract_id": "bb.example.scoring.v1",
            }
        ],
        "contracts": [
            {
                "contract_id": "bb.example.scoring.v1",
                "input_schema_ids": ["bb.example.input.v1"],
                "output_schema_ids": ["bb.example.output.v1"],
            }
        ],
        "schema_members": {},
        "requested_authority": {
            "project": None,
            "network": None,
            "child": None,
            "provider_ids": [],
            "tool_ids": [],
            "credential_disclosures": [],
        },
        "resource_budget": {
            "max_children": 0,
            "max_message_bytes": 262144,
            "max_checkpoint_bytes": 1048576,
            "deadline_ms": 10000,
        },
        "source_members": [
            {"path": "src/ranker.py", "sha256": digest, "size_bytes": len(code)}
        ],
        "import_members": [
            {
                "module": "example.ranker",
                "path": "src/ranker.py",
                "sha256": digest,
                "size_bytes": len(code),
            }
        ],
        "runtime": {
            "kind": "native",
            "ref": _RUNTIME_REF,
            "platform": "darwin/arm64",
            "entrypoint": ["python3", "-I"],
        },
    }
    (root / "schemas").mkdir()
    for schema_id in (
        "bb.example.input.v1",
        "bb.example.output.v1",
        "bb.example.checkpoint.v1",
    ):
        member_path = f"schemas/{schema_id}.json"
        content = canonical_json_bytes(
            {"$id": schema_id, "type": "object", "additionalProperties": False}
        )
        (root / member_path).write_bytes(content)
        manifest["schema_members"][schema_id] = member_path
        manifest["source_members"].append(
            {
                "path": member_path,
                "sha256": bytes_sha256(content),
                "size_bytes": len(content),
            }
        )
    (root / "module.json").write_bytes(canonical_json_bytes(manifest))
    return root


def test_package_bytes_are_stable_and_survive_source_removal(tmp_path: Path) -> None:
    code = b"VALUE = 'ranker-a'\n"
    source = _write_source(tmp_path / "source", code=code)
    second_source = _write_source(tmp_path / "second-source", code=code)
    output = tmp_path / "ranker.bbpkg"
    second_output = tmp_path / "ranker-copy.bbpkg"
    cas = FilesystemCAS(tmp_path / "cas")
    built = build_module_package(source, output, cas=cas)
    build_module_package(second_source, second_output, cas=cas)
    assert output.read_bytes() == second_output.read_bytes()
    shutil.rmtree(source)
    loaded = load_module_package(output, built.package_digest, cas=cas)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("src/ranker.py") == code
    assert cas.get_bytes(loaded.artifact_ref) == output.read_bytes()


def test_source_and_expected_digest_mismatches_refuse(tmp_path: Path) -> None:
    source = _write_source(
        tmp_path / "source",
        code=b"VALUE = 'ranker-a'\n",
        source_digest="sha256:" + "b" * 64,
    )
    cas = FilesystemCAS(tmp_path / "cas")
    with pytest.raises(ModulePackageIntegrityError, match="manifest declaration"):
        build_module_package(source, tmp_path / "bad.bbpkg", cas=cas)

    valid_source = _write_source(tmp_path / "valid", code=b"VALUE = 'ranker-b'\n")
    package = build_module_package(valid_source, tmp_path / "valid.bbpkg", cas=cas)
    with pytest.raises(ModulePackageIntegrityError, match="digest mismatch"):
        load_module_package(tmp_path / "valid.bbpkg", "sha256:" + "c" * 64, cas=cas)


def test_discovery_does_not_import_code_and_import_canary_is_positive_control(
    tmp_path: Path,
) -> None:
    canary = tmp_path / "imported"
    code = (
        "from pathlib import Path\n"
        f"Path({str(canary)!r}).write_text('imported', encoding='utf-8')\n"
    ).encode()
    source = _write_source(tmp_path / "source", code=code)
    output = tmp_path / "ranker.bbpkg"
    cas = FilesystemCAS(tmp_path / "cas")

    package = build_module_package(source, output, cas=cas)
    load_module_package(output, package.package_digest, cas=cas)
    assert not canary.exists()

    spec = importlib.util.spec_from_file_location(
        "positive_import_control", source / "src" / "ranker.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert canary.read_text(encoding="utf-8") == "imported"


def test_manifest_unknown_fields_refuse_without_output(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", code=b"VALUE = 'ranker'\n")
    manifest = json.loads((source / "module.json").read_text(encoding="utf-8"))
    manifest["unexpected"] = True
    (source / "module.json").write_bytes(canonical_json_bytes(manifest))
    cas = FilesystemCAS(tmp_path / "cas")
    with pytest.raises(ModulePackageValidationError, match="unknown fields"):
        build_module_package(source, tmp_path / "bad.bbpkg", cas=cas)
    assert not (tmp_path / "bad.bbpkg").exists()


def test_package_refuses_unsupported_worker_protocol(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", code=b"VALUE = 'ranker'\n")
    manifest = json.loads((source / "module.json").read_text(encoding="utf-8"))
    manifest["worker_protocol"] = "breadboard.modules.worker.v2"
    (source / "module.json").write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(
        ModulePackageValidationError, match="worker_protocol must be bb.worker.v2"
    ):
        build_module_package(
            source, tmp_path / "bad.bbpkg", cas=FilesystemCAS(tmp_path / "cas")
        )
    assert not (tmp_path / "bad.bbpkg").exists()


def test_package_refuses_oci_worker_entrypoint_override(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", code=b"VALUE = 'ranker'\n")
    manifest = json.loads((source / "module.json").read_text(encoding="utf-8"))
    manifest["execution_tier"] = "enforced_isolated"
    manifest["runtime"] = {
        "kind": "oci",
        "ref": _RUNTIME_REF,
        "platform": "linux/arm64",
        "entrypoint": ["breadboard-worker"],
    }
    (source / "module.json").write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(
        ModulePackageValidationError,
        match=r"OCI bb\.worker\.v2 runtime entrypoint must be exactly",
    ):
        build_module_package(
            source, tmp_path / "bad.bbpkg", cas=FilesystemCAS(tmp_path / "cas")
        )
    assert not (tmp_path / "bad.bbpkg").exists()


def test_package_refuses_entrypoints_the_worker_cannot_load(tmp_path: Path) -> None:
    invalid = {
        "missing-symbol-separator": (
            "src/ranker.py",
            None,
            "entrypoint must be path:symbol",
        ),
        "unbound-path": (
            "missing.py:open_instance",
            None,
            "entrypoint path must have exactly one import member binding",
        ),
        "missing-import": (
            "src/ranker.py:open_instance",
            [],
            "entrypoint path must have exactly one import member binding",
        ),
    }
    for label, (entrypoint, import_members, message) in invalid.items():
        source = _write_source(tmp_path / label / "source", code=b"VALUE = 'ranker'\n")
        manifest = json.loads((source / "module.json").read_text(encoding="utf-8"))
        manifest["entrypoint"] = entrypoint
        if import_members is not None:
            manifest["import_members"] = import_members
        (source / "module.json").write_bytes(canonical_json_bytes(manifest))

        with pytest.raises(ModulePackageValidationError, match=message):
            build_module_package(
                source,
                tmp_path / label / "bad.bbpkg",
                cas=FilesystemCAS(tmp_path / label / "cas"),
            )
        assert not (tmp_path / label / "bad.bbpkg").exists()


def test_package_refuses_runtime_inoperable_authority_and_budget(
    tmp_path: Path,
) -> None:
    invalid = {
        "authority": (
            "requested_authority",
            {"network": "none", "project_write": False},
            "requested_authority has unknown fields",
        ),
        "budget": (
            "resource_budget",
            {"cpu": 1, "memory_bytes": 67108864, "processes": 1},
            "resource_budget has unknown fields",
        ),
    }
    for label, (field, value, message) in invalid.items():
        source = _write_source(tmp_path / label / "source", code=b"VALUE = 'ranker'\n")
        manifest = json.loads((source / "module.json").read_text(encoding="utf-8"))
        manifest[field] = value
        (source / "module.json").write_bytes(canonical_json_bytes(manifest))

        with pytest.raises(ModulePackageValidationError, match=message):
            build_module_package(
                source,
                tmp_path / label / "bad.bbpkg",
                cas=FilesystemCAS(tmp_path / label / "cas"),
            )
        assert not (tmp_path / label / "bad.bbpkg").exists()


def test_relative_schema_ids_resolve_from_their_declared_base(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", code=b"VALUE = 'ranker'\n")
    manifest_path = source / "module.json"
    manifest = json.loads(manifest_path.read_bytes())
    schemas = {
        "schemas/a.json": {"$id": "schemas/a.json", "$ref": "../other/b.json"},
        "other/b.json": {"$id": "other/b.json", "type": "object"},
    }
    expected = {}
    for schema_id, document in schemas.items():
        content = canonical_json_bytes(document)
        (source / schema_id).parent.mkdir(parents=True, exist_ok=True)
        (source / schema_id).write_bytes(content)
        manifest["schema_members"][schema_id] = schema_id
        manifest["source_members"].append(
            {
                "path": schema_id,
                "sha256": bytes_sha256(content),
                "size_bytes": len(content),
            }
        )
        expected[schema_id] = bytes_sha256(content)
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    cas = FilesystemCAS(tmp_path / "cas")
    try:
        package = build_module_package(source, tmp_path / "relative.bbpkg", cas=cas)
        assert dict(package.schema_closure(["schemas/a.json"])) == expected
    finally:
        cas.close()


def test_distinct_dependency_slots_can_share_a_contract(tmp_path: Path) -> None:
    contract_id = "bb.example.scoring.v1"
    cas = FilesystemCAS(tmp_path / "cas")
    try:
        packages = {}
        for name, slots in (
            ("root", {"left": contract_id, "right": contract_id}),
            ("leaf", {}),
        ):
            source = _write_source(tmp_path / name, code=b"VALUE = 'ranker'\n")
            manifest_path = source / "module.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["dependency_contracts"] = slots
            manifest["child_targets"] = []
            manifest_path.write_bytes(canonical_json_bytes(manifest))
            package = build_module_package(source, tmp_path / f"{name}.bbpkg", cas=cas)
            packages[name] = {
                "source": f"{name}.bbpkg",
                "digest": package.package_digest,
            }
        edges = {"left": "first", "right": "second"}
        definition = {
            "schema_version": "bb.harness_definition.v2",
            "version": 2,
            "modules": {
                "root": "root",
                "bindings": {
                    "root": {
                        "package": packages["root"],
                        "environment": "root",
                        "dependencies": edges,
                        "children": {},
                        "config": {},
                    },
                    "first": {
                        "package": packages["leaf"],
                        "environment": "leaf",
                        "dependencies": {},
                        "children": {},
                        "config": {"offset": 1},
                    },
                    "second": {
                        "package": packages["leaf"],
                        "environment": "leaf",
                        "dependencies": {},
                        "children": {},
                        "config": {"offset": 2},
                    },
                },
            },
        }
        source_path = tmp_path / "composition.json"
        source_path.write_bytes(canonical_json_bytes(definition))
        first = compile_harness_source(source_path, tmp_path, cas=cas)
        edges.update(left="second", right="first")
        source_path.write_bytes(canonical_json_bytes(definition))
        swapped = compile_harness_source(source_path, tmp_path, cas=cas)
        assert first.lock.generation_id != swapped.lock.generation_id
        assert (
            first.lock.configuration_graph_hash == swapped.lock.configuration_graph_hash
        )
    finally:
        cas.close()
