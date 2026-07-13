from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation.primitive_records import sha256_ref
from scripts.e4_parity import catalog_refs


CATALOG_SHA = "sha256:" + "a" * 64


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _row_sha(row_id: str, row: dict[str, Any]) -> str:
    payload = {"row_id": row_id, "row": row}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_ref(encoded)


def _catalog_fixture(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    row = {
        "feature_id": "feature_alpha",
        "name": "alpha",
        "nested": {"z": 1, "a": [3, 2, 1]},
    }
    other_row = {"feature_id": "feature_beta", "name": "beta"}
    ledger_path = tmp_path / "docs_tmp" / "phase_16" / "feature_ledger.json"
    _write_json(ledger_path, {"schema_version": "bb.e4.atomic_feature_ledger.v1", "rows": [other_row, row]})

    catalog = {
        "schema_version": "bb.e4.artifact_catalog.v1",
        "catalog_id": "catalog_refs_test",
        "generated_at_utc": "2026-07-03T00:00:00Z",
        "entries": [
            {
                "role_id": "ledger:features",
                "path": "docs_tmp/phase_16/feature_ledger.json",
                "sha256": CATALOG_SHA,
                "bytes": ledger_path.stat().st_size,
                "exists": True,
                "artifact_kind": "ledger",
                "lane_id": None,
                "media_type": "application/json",
                "derived_from": [],
                "generated_by": "test",
            }
        ],
        "integrity": {"entry_count": 1, "entries_hash": "sha256:" + "b" * 64},
    }
    catalog_path = tmp_path / "docs" / "conformance" / "e4_artifact_catalog.json"
    _write_json(catalog_path, catalog)
    return catalog_path, catalog, row


def test_load_catalog_entry_and_hash_ref_use_catalog_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path, expected_catalog, _row = _catalog_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catalog_refs, "ROOT", tmp_path, raising=False)

    catalog = catalog_refs.load_catalog(catalog_path)

    assert catalog == expected_catalog
    assert catalog_refs.entry(catalog, "ledger:features") == expected_catalog["entries"][0]
    assert catalog_refs.hash_ref(catalog, "ledger:features") == (
        "docs_tmp/phase_16/feature_ledger.json#" + CATALOG_SHA
    )


def test_entry_and_hash_ref_error_on_unknown_role(tmp_path: Path) -> None:
    _catalog_path, catalog, _row = _catalog_fixture(tmp_path)

    with pytest.raises(KeyError, match="missing:role"):
        catalog_refs.entry(catalog, "missing:role")
    with pytest.raises(KeyError, match="missing:role"):
        catalog_refs.hash_ref(catalog, "missing:role")


def test_row_ref_is_deterministic_and_uses_the_requested_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_path, catalog, row = _catalog_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catalog_refs, "ROOT", tmp_path, raising=False)

    first = catalog_refs.row_ref(catalog, "ledger:features", "feature_alpha")
    second = catalog_refs.row_ref(catalog, "ledger:features", "feature_alpha")

    assert first == second
    assert first == (
        "docs_tmp/phase_16/feature_ledger.json#feature_alpha#" + _row_sha("feature_alpha", row)
    )


def test_row_ref_errors_on_unknown_role_or_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_path, catalog, _row = _catalog_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catalog_refs, "ROOT", tmp_path, raising=False)

    with pytest.raises(KeyError, match="missing:role"):
        catalog_refs.row_ref(catalog, "missing:role", "feature_alpha")
    with pytest.raises(KeyError, match="missing_row"):
        catalog_refs.row_ref(catalog, "ledger:features", "missing_row")
