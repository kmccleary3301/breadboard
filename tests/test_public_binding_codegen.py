from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.quality.generate_public_bindings as generator

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "contracts/public/operations.v2.json"


def _staged_catalog(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    path = root / "contracts/public/operations.v2.json"
    path.parent.mkdir(parents=True)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return root, catalog


def test_generated_rows_have_all_catalog_fields_and_are_sorted(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    outputs = generator.build_outputs(root)
    module = outputs[root / "breadboard_sdk/generated/public_bindings.py"].decode()
    assert module.count("PublicOperationBinding(") == 26
    ids = [
        row["operation_id"]
        for row in sorted(catalog["operations"], key=lambda row: row["operation_id"])
    ]
    assert all(identifier in module for identifier in ids)
    assert "catalog-sha256: sha256:" in module


def test_every_generated_output_carries_catalog_provenance(tmp_path: Path) -> None:
    root, _ = _staged_catalog(tmp_path)
    catalog_hashes: set[str] = set()
    for path, content in generator.build_outputs(root).items():
        if path.suffix == ".json":
            payload = json.loads(content)
            assert payload["generated_by"] == generator.GENERATOR_PATH
            assert payload["generator_version"] == generator.GENERATOR_VERSION
            assert payload["catalog_id"] == "bb.public_operation_catalog.v2"
            catalog_hashes.add(payload["catalog_sha256"])
        else:
            header = content.decode().splitlines()[:5]
            assert f"generator: {generator.GENERATOR_PATH}" in header[1]
            assert f"generator-version: {generator.GENERATOR_VERSION}" in header[2]
            assert "catalog-id: bb.public_operation_catalog.v2" in header[3]
            catalog_hashes.add(header[4].split("catalog-sha256: ", 1)[1])
    assert len(catalog_hashes) == 1
    catalog_hash = catalog_hashes.pop()
    assert catalog_hash.startswith("sha256:") and len(catalog_hash) == 71


def test_catalog_reordering_does_not_change_outputs(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    original = generator.build_outputs(root)
    catalog["operations"].reverse()
    (root / "contracts/public/operations.v2.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )
    assert generator.build_outputs(root) == original


@pytest.mark.parametrize(
    ("binding", "field", "value", "output"),
    [
        (
            "operation",
            "operation_id",
            "artifact.fetch",
            "breadboard/product/operations/generated_bindings.py",
        ),
        ("openapi", "method", "PUT", "sdk/ts/src/generated/public-bindings.ts"),
        (
            "openapi",
            "path",
            "/v1/artifacts/{artifact_id}/content",
            "sdk/ts/src/generated/public-bindings.ts",
        ),
        (
            "bbh",
            "command",
            "bbh artifact fetch",
            "breadboard/product/operations/generated_bindings.py",
        ),
        (
            "python_sdk",
            "method",
            "fetch_artifact",
            "breadboard_sdk/generated/public_surface_manifest.v1.json",
        ),
        (
            "typescript_sdk",
            "method",
            "fetchArtifact",
            "sdk/ts/src/generated/public_surface_manifest.v1.json",
        ),
        (
            "tui",
            "action_id",
            "public.artifact.fetch",
            "tui_skeleton/src/generated/public_surface_manifest.v1.json",
        ),
        (
            "tui",
            "kind",
            "action",
            "tui_skeleton/src/generated/public_surface_manifest.v1.json",
        ),
    ],
)
def test_catalog_identity_mutations_drift_relevant_bindings_deterministically(
    tmp_path: Path,
    binding: str,
    field: str,
    value: str,
    output: str,
) -> None:
    root, catalog = _staged_catalog(tmp_path)
    original = generator.build_outputs(root)
    operation = catalog["operations"][0]
    if binding == "operation":
        operation[field] = value
        operation["bindings"]["openapi"]["operation_id"] = value
    else:
        operation["bindings"][binding][field] = value
    (root / "contracts/public/operations.v2.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )

    changed = generator.build_outputs(root)
    assert changed != original
    assert generator.build_outputs(root) == changed
    assert value.encode() in changed[root / output]


def test_duplicate_operation_id_is_rejected(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    catalog["operations"][1]["operation_id"] = catalog["operations"][0]["operation_id"]
    (root / "contracts/public/operations.v2.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )
    with pytest.raises(generator.CatalogError, match="duplicate operation_id"):
        generator.build_outputs(root)


def test_check_reports_sorted_stale_paths_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = _staged_catalog(tmp_path)
    monkeypatch.setattr(generator, "ROOT", root)
    assert generator.main(["--check"]) == 1
    output = capsys.readouterr().out.splitlines()
    paths = [line.split(": ", 1)[1] for line in output]
    assert paths == sorted(paths)
    assert not list(root.rglob("*.py"))
