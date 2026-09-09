from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard_engine.compilation.contracts import bytes_sha256, canonical_json_bytes
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
        "execution_tier": "enforced_isolated",
        "worker_protocol": "bb.worker.v2",
        "input_schema_ids": ["bb.example.input.v1"],
        "output_schema_ids": ["bb.example.output.v1"],
        "checkpoint_schema_id": "bb.example.checkpoint.v1",
        "accepted_checkpoint_schema_ids": ["bb.example.checkpoint.v1"],
        "dependency_contract_ids": ["bb.example.scoring.v1"],
        "child_targets": [{"label": "review", "target": "reviewer"}],
        "requested_authority": {"network": "none", "project_write": False},
        "resource_budget": {"cpu": 1, "memory_bytes": 67108864, "processes": 1},
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
    (root / "module.json").write_bytes(canonical_json_bytes(manifest))
    return root


def test_build_and_load_are_deterministic_and_lock_ready(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", code=b"VALUE = 'ranker-a'\n")
    output = tmp_path / "ranker.bbpkg"
    cas = FilesystemCAS(tmp_path / "cas")

    built = build_module_package(source, output, cas=cas)
    loaded = load_module_package(output, built.package_digest, cas=cas)

    assert loaded.package_digest == built.package_digest
    assert loaded.artifact_ref == built.artifact_ref
    assert loaded.manifest == built.manifest
    assert loaded.import_members == (("example.ranker", "src/ranker.py"),)
    assert set(built.lock_record()) == {
        "artifact_ref",
        "import_members",
        "manifest",
        "package_digest",
        "runtime_key",
    }
    assert cas.get_bytes(built.artifact_ref) == output.read_bytes()


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
    assert package.package_digest != "sha256:" + "c" * 64


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

    spec = importlib.util.spec_from_file_location("positive_import_control", source / "src" / "ranker.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert canary.read_text(encoding="utf-8") == "imported"


def test_manifest_values_are_frozen_and_unknown_fields_fail(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source", code=b"VALUE = 'ranker'\n")
    manifest = json.loads((source / "module.json").read_text(encoding="utf-8"))
    manifest["unexpected"] = True
    (source / "module.json").write_bytes(canonical_json_bytes(manifest))
    cas = FilesystemCAS(tmp_path / "cas")
    with pytest.raises(ModulePackageValidationError, match="unknown fields"):
        build_module_package(source, tmp_path / "bad.bbpkg", cas=cas)
