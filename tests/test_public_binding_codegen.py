from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

import scripts.quality.generate_public_bindings as generator
from scripts.quality.build_surface_inventory import _docs_bindings
from scripts.quality.validate_public_contracts import ContractValidationError

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "contracts/public/operations.v2.json"


def _staged_catalog(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "repo"
    path = root / "contracts/public/operations.v2.json"
    path.parent.mkdir(parents=True)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return root, catalog


def _materialize_documents(root: Path) -> None:
    for path, content in generator.build_outputs(root).items():
        if path.suffix == ".md":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def test_generated_rows_are_sorted_by_operation_id(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    namespace: dict[str, object] = {}
    exec(
        generator.build_outputs(root)[
            root / "breadboard_sdk/generated/public_bindings.py"
        ],
        namespace,
    )
    bindings = namespace["PUBLIC_OPERATION_BINDINGS"]
    ids = [row["operation_id"] for row in catalog["operations"]]
    assert [binding.operation_id for binding in bindings] == sorted(ids)


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
            assert payload["audience"] == "public"
        elif path.suffix == ".md":
            metadata = generator.parse_generated_document_metadata(content)
            assert metadata["generator"] == generator.GENERATOR_PATH
            assert metadata["generator-version"] == generator.GENERATOR_VERSION
            assert metadata["catalog-id"] == "bb.public_operation_catalog.v2"
            catalog_hashes.add(metadata["catalog-sha256"])
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


def test_generated_python_bindings_carry_immutable_catalog_policy(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    outputs = generator.build_outputs(root)
    namespace: dict[str, object] = {}
    exec(
        outputs[root / "breadboard/product/operations/generated_bindings.py"],
        namespace,
    )
    bindings = namespace["PUBLIC_BINDINGS_BY_OPERATION_ID"]
    assert isinstance(bindings, Mapping)
    operation = catalog["operations"][0]
    binding = bindings[operation["operation_id"]]
    required_capabilities = getattr(binding, "required_capabilities")

    assert getattr(binding, "lifecycle") == operation["lifecycle"]
    assert getattr(binding, "idempotency_mode") == operation["idempotency"]["mode"]
    assert getattr(binding, "auth_mode") == operation["auth_policy"]["mode"]
    assert required_capabilities == tuple(sorted(operation["required_capabilities"]))
    assert isinstance(required_capabilities, tuple)



def test_capability_reordering_does_not_change_outputs(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    operation = next(
        row
        for row in catalog["operations"]
        if row["auth_policy"]["mode"] == "capability_gated"
    )
    operation["required_capabilities"] = ["public.zeta", "public.alpha"]
    catalog_path = root / "contracts/public/operations.v2.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    original = generator.build_outputs(root)

    operation["required_capabilities"].reverse()
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    assert generator.build_outputs(root) == original


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("none-with-capability", "requires no capabilities"),
        ("gated-without-capability", "requires at least one capability"),
        ("duplicate-capability", "must be unique"),
    ],
)
def test_invalid_capability_policy_is_rejected(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    root, catalog = _staged_catalog(tmp_path)
    if case == "none-with-capability":
        operation = next(
            row for row in catalog["operations"] if row["auth_policy"]["mode"] == "none"
        )
        operation["required_capabilities"] = ["public.invalid"]
    else:
        operation = next(
            row
            for row in catalog["operations"]
            if row["auth_policy"]["mode"] == "capability_gated"
        )
        operation["required_capabilities"] = (
            [] if case == "gated-without-capability" else ["public.same", "public.same"]
        )
    (root / "contracts/public/operations.v2.json").write_text(
        json.dumps(catalog),
        encoding="utf-8",
    )

    with pytest.raises(generator.CatalogError, match=message):
        generator.build_outputs(root)


@pytest.mark.parametrize(
    ("binding", "field", "value"),
    [
        ("operation", "operation_id", "artifact.fetch"),
        ("openapi", "method", "PUT"),
        ("openapi", "path", "/v1/artifacts/{artifact_id}/content"),
        ("bbh", "command", "breadboard artifact fetch"),
        ("typescript_sdk", "method", "fetchArtifact"),
        ("tui", "action_id", "public.artifact.fetch"),
        ("tui", "kind", "action"),
    ],
)
def test_catalog_identity_mutations_drift_relevant_bindings_deterministically(
    tmp_path: Path,
    binding: str,
    field: str,
    value: str,
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


def test_generation_writes_stable_readable_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _staged_catalog(tmp_path)
    monkeypatch.setattr(generator, "ROOT", root)

    assert generator.main([]) == 0
    outputs = generator.build_outputs(root)
    assert all(
        path.stat().st_mode & 0o777 == generator.GENERATED_FILE_MODE for path in outputs
    )
    assert generator.main(["--check"]) == 0


def test_generation_uses_portable_path_permission_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _staged_catalog(tmp_path)
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.delattr(generator.os, "fchmod", raising=False)

    assert generator.main([]) == 0


def test_codegen_builds_exact_operation_docs_and_index(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    outputs = generator.build_outputs(root)
    pages = {
        path: content
        for path, content in outputs.items()
        if path.suffix == ".md" and path.name != "index.md"
    }
    assert len(pages) == len(catalog["operations"])
    for path, content in pages.items():
        metadata = generator.parse_generated_document_metadata(content)
        assert metadata["operation-id"] and metadata["slug"]
        assert metadata["catalog-id"] == "bb.public_operation_catalog.v2"
    index = outputs[root / "docs/reference/public/index.md"].decode()
    assert len(re.findall(r"\]\(operations/.+\.md\)", index)) == len(
        catalog["operations"]
    )


@pytest.mark.parametrize(
    "slug",
    [
        "../escape",
        "/absolute",
        "artifact/get",
        "reference/artifact/get",
        "operations/artifact",
        "operations//artifact/get",
        "operations/artifact/get/",
    ],
)
def test_noncanonical_document_slug_is_rejected(tmp_path: Path, slug: str) -> None:
    root, catalog = _staged_catalog(tmp_path)
    catalog["operations"][0]["bindings"]["docs"]["slug"] = slug
    (root / "contracts/public/operations.v2.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )
    with pytest.raises(generator.CatalogError, match="docs.slug"):
        generator.build_outputs(root)


def test_document_slug_collision_is_rejected(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    catalog["operations"][1]["bindings"]["docs"]["slug"] = catalog["operations"][0][
        "bindings"
    ]["docs"]["slug"]
    (root / "contracts/public/operations.v2.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )
    with pytest.raises(generator.CatalogError, match="duplicate docs_slug"):
        generator.build_outputs(root)


def test_write_mode_preserves_human_document_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _staged_catalog(tmp_path)
    human_path = root / "docs/reference/public/operations/artifact/get.md"
    human_path.parent.mkdir(parents=True)
    human_path.write_text("# Human-owned page\n", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", root)
    assert generator.main([]) == 2
    assert human_path.read_text(encoding="utf-8") == "# Human-owned page\n"
    assert not (root / "breadboard_sdk/generated/public_bindings.py").exists()


def test_write_mode_rejects_symlinked_document_parent_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _staged_catalog(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    operations = root / "docs/reference/public/operations"
    operations.parent.mkdir(parents=True)
    operations.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(generator, "ROOT", root)

    assert generator.main([]) == 2
    assert not list(outside.iterdir())
    assert not (root / "breadboard_sdk/generated/public_bindings.py").exists()


def test_generated_document_links_resolve_without_linking_logical_ids() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    outputs = generator.build_outputs()
    expected_pages: set[Path] = set()
    for operation in catalog["operations"]:
        path = (
            ROOT
            / "docs/reference/public"
            / (operation["bindings"]["docs"]["slug"] + ".md")
        )
        expected_pages.add(path)
        text = outputs[path].decode()
        links = re.findall(r"\]\(([^)]+)\)", text)
        assert len(links) == 1 + int(operation["event_schema"] is not None)
        assert all((path.parent / link).resolve().is_file() for link in links)

    index_path = ROOT / "docs/reference/public/index.md"
    index_links = re.findall(
        r"\]\((operations/[^)]+\.md)\)", outputs[index_path].decode()
    )
    assert len(index_links) == len(expected_pages)
    assert {(index_path.parent / link).resolve() for link in index_links} == {
        path.resolve() for path in expected_pages
    }


def test_document_inventory_reports_wholly_missing_docs_as_gaps(
    tmp_path: Path,
) -> None:
    root, catalog = _staged_catalog(tmp_path)
    assert _docs_bindings(root, catalog) == {
        operation["operation_id"]: False for operation in catalog["operations"]
    }


def test_document_inventory_accepts_exact_pages_and_unowned_narrative(
    tmp_path: Path,
) -> None:
    root, catalog = _staged_catalog(tmp_path)
    _materialize_documents(root)
    narrative = root / "docs/reference/public/overview.md"
    narrative.write_text("# Authored overview\n", encoding="utf-8")

    assert _docs_bindings(root, catalog) == {
        operation["operation_id"]: True for operation in catalog["operations"]
    }


@pytest.mark.parametrize("mode", ["operation-id", "slug", "catalog-sha256", "body"])
def test_document_inventory_rejects_tampered_page(tmp_path: Path, mode: str) -> None:
    root, catalog = _staged_catalog(tmp_path)
    _materialize_documents(root)
    path = root / "docs/reference/public/operations/artifact/get.md"
    content = path.read_text(encoding="utf-8")
    if mode == "operation-id":
        content = content.replace(
            "<!-- operation-id: artifact.get -->",
            "<!-- operation-id: artifact.list -->",
        )
    elif mode == "slug":
        content = content.replace(
            "<!-- slug: operations/artifact/get -->",
            "<!-- slug: operations/artifact/list -->",
        )
    elif mode == "catalog-sha256":
        content = re.sub(
            r"<!-- catalog-sha256: sha256:[0-9a-f]{64} -->",
            f"<!-- catalog-sha256: sha256:{'0' * 64} -->",
            content,
            count=1,
        )
    else:
        content += "\ntampered\n"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ContractValidationError, match="invalid|stale|tampered"):
        _docs_bindings(root, catalog)


@pytest.mark.parametrize("mode", ["missing-index", "tampered-index", "moved-page"])
def test_document_inventory_rejects_incomplete_or_moved_output(
    tmp_path: Path, mode: str
) -> None:
    root, catalog = _staged_catalog(tmp_path)
    _materialize_documents(root)
    index = root / "docs/reference/public/index.md"
    if mode == "missing-index":
        index.unlink()
    elif mode == "tampered-index":
        index.write_bytes(index.read_bytes() + b"\ntampered\n")
    else:
        source = root / "docs/reference/public/operations/artifact/get.md"
        moved = root / "docs/reference/public/operations/artifact/copied.md"
        moved.write_bytes(source.read_bytes())

    with pytest.raises(ContractValidationError, match="index|unexpected|moved"):
        _docs_bindings(root, catalog)


def test_document_inventory_rejects_symlinked_catalog_page(tmp_path: Path) -> None:
    root, catalog = _staged_catalog(tmp_path)
    _materialize_documents(root)
    page = root / "docs/reference/public/operations/artifact/get.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(page.read_bytes())
    page.unlink()
    page.symlink_to(outside)

    with pytest.raises(ContractValidationError, match="symlink"):
        _docs_bindings(root, catalog)
